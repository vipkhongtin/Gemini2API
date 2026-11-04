# Phân tích Kiến trúc `muse2api` → `gemini2api` (Chat Gemini [Chính] & AI Studio [Phụ])

Tài liệu này mô tả chi tiết phân tích từ kho mã nguồn tham chiếu [`muse2api`](https://github.com/www222fff/muse2api) và cách chuyển đổi, mở rộng sang **`gemini2api`** để phục vụ **Google Gemini Web Chat (`https://gemini.google.com/app` — Chính)** và **Google AI Studio (`https://aistudio.google.com` — Phụ)**.

---

## 1. Tổng quan Kiến trúc 5 Tầng

```
Client (OpenAI SDK / Cursor / Cline / LobeChat / NextChat)
   │  POST /v1/chat/completions | /v1/responses | /v1/images/* | /v1/videos/*
   ▼
[Tầng 1: FastAPI Routes & Middleware] (src/gemini2api/api/)
   ├── Xác thực Bearer API Key (`g2a-...`, legacy key, admin key)
   ├── Chuẩn hóa prompt đa lượt (`core/prompt.py`), SSRF guard cho media (`core/media.py`)
   ├── Phân giải model & mức độ suy luận (`core/models.py`: `resolve_model`, `resolve_thinking_level`)
   └── Ghi nhận nhật ký & thống kê SQLite (`services/request_log.py`)
   │
   ▼
[Tầng 2: Gateway & TaskRunner] (src/gemini2api/services/)
   ├── `Gateway`: điều phối retry / failover qua các tài khoản khi gặp lỗi Upstream / Quota
   └── `TaskRunner`: quản lý tác vụ bất đồng bộ cho tạo ảnh/video
   │
   ▼
[Tầng 3: Account Pool & Keepalive] (src/gemini2api/accounts/)
   ├── `AccountPool`: chọn tài khoản theo `lru`, `round_robin`, hoặc `affinity`
   ├── Kiểm soát concurrency per-account, cooldown khi rate-limit (429), khoá tài khoản khi hết hạn cookie
   └── `keepalive_loop`: định kỳ làm mới phiên Google AI Studio và xoay vòng cookie (`__Secure-1PSIDTS`)
   │
   ▼
[Tầng 4: Drivers] (src/gemini2api/drivers/)
   ├── `BrowserDriver` (CDP): điều khiển Chromium trực tiếp trên `aistudio.google.com`
   ├── `HttpDriver` (SSE): kết nối `generativelanguage.googleapis.com` với `thinkingConfig`
   └── `MockDriver`: mô phỏng luồng suy luận `ChatThought` + câu trả lời để kiểm thử
   │
   ▼
[Tầng 5: Upstream Google AI Studio] (src/gemini2api/upstream/aistudio.py)
   └── Xác thực Cookie (`.google.com`: `SID`, `__Secure-1PSID`, `SAPISID`) + `SAPISIDHASH`
```

---

## 2. Bảng Ánh xạ Chuyển đổi `muse2api` → `gemini2api`

| Thành phần | `muse2api` (Gốc) | `gemini2api` (Mới - Gemini 4 Argon) |
|---|---|---|
| **Upstream Domain (Chính)** | `https://muse.ai` (`/thread/new`) | `https://gemini.google.com` (`/app`) — **Google Gemini Web Chat (Chính)** |
| **Upstream Domain (Phụ)** | - | `https://aistudio.google.com` (`/prompts/new_chat`) — **Google AI Studio (Phụ)** |
| **Session Cookies** | `hatch_sess`, `hatch_native_auth_device`, `hatch_gw`, `hatch_vml` | `SID`, `__Secure-1PSID`, `HSID`, `SSID`, `APISID`, `SAPISID`, `__Secure-1PSIDTS` |
| **Header uỷ quyền phụ** | Không có | `Authorization: SAPISIDHASH <ts>_<sha1>` (`sapisidhash_header`) |
| **Model mặc định** | `muse-chat` | `gemini-4-argon` (`models/gemini-4-argon`) |
| **Biến thể Model** | `muse-chat`, `muse-image`, `muse-video` | `gemini-4-argon`, `gemini-4-argon-high`, `gemini-4-argon-medium`, `gemini-4-argon-low`, `gemini-4-argon-nothink`, `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-image`, `gemini-video` |
| **Hỗ trợ Thinking / Reasoning** | Không tách luồng suy luận riêng | `ChatThought(str)` tách bạch luồng suy luận (`ms-thought-chunk` trên DOM hoặc `thought: true` trên HTTP SSE) → xuất ra `reasoning_content` và `<think>...</think>` |
| **DOM Selectors (`dom.py`)** | `[data-testid^="hatch-"]`, `div[contenteditable="true"]` | `ms-autosize-textarea textarea`, `ms-run-button button`, `ms-chat-turn`, `ms-thought-chunk`, `ms-model-selector` |
| **Cấu hình Model & Thinking trên UI** | Không có | `dom.configure_run_settings(model, thinking_level)` tự động chọn `Gemini 4 Argon` và bật/chỉnh mức Thinking trên bảng Run Settings của AI Studio |
| **Tiền tố Client API Key** | `m2a-` | `g2a-` |
| **Biến môi trường** | `MUSE2API_*` | `GEMINI2API_*` |
| **Tương thích Hệ điều hành** | Ưu tiên Linux (`os.replace`, đường dẫn Chromium Linux/macOS) | Hỗ trợ đầy đủ cả **Windows**, Linux và macOS (tự động dò Chrome/Edge trên Windows, atomic file write chịu lỗi khoá file Windows) |

---

## 3. Cơ chế Streaming Thinking / Reasoning cho Gemini 4 Argon

1. **Phân giải mức suy luận (`resolve_thinking_level`)**:
   - Nhận cấu hình từ tên model (`gemini-4-argon-high` → `high`, `gemini-4-argon-low` → `low`, `gemini-4-argon-nothink` → `none`) hoặc từ body request (`reasoning_effort`, `thinking.type`, `thinking.budget_tokens`).
2. **Trích xuất Thought Chunk trên Browser CDP (`drivers/browser/dom.py` & `driver.py`)**:
   - Trên Google AI Studio, khối suy luận của Gemini 4 Argon nằm trong phần tử `<ms-thought-chunk>` bên trong `.model-prompt-container`.
   - Script `dom.CHAT_STATE` tách riêng:
     - `lastThought`: toàn bộ nội dung văn bản bên trong `ms-thought-chunk`.
     - `lastText`: nội dung câu trả lời chính thức sau khi đã loại bỏ các node `ms-thought-chunk` khỏi bản sao DOM.
   - `BrowserDriver.chat_stream` theo dõi độ dài `thought_sent` và `sent` độc lập, phát ra `ChatThought(delta)` khi mô hình đang suy nghĩ và `str(delta)` khi mô hình trả lời.
3. **Định dạng đầu ra chuẩn OpenAI / DeepSeek (`api/routes/chat.py`)**:
   - Ở chế độ **Streaming (`stream: true`)**: mỗi `ChatThought` được phát ra trong `choices[0].delta.reasoning_content`. Nếu bật `GEMINI2API_THINK_TAGS=true`, luồng cũng tự động chèn `<think>\n...\n</think>\n\n` vào `delta.content`.
   - Ở chế độ **Non-streaming (`stream: false`)**: trả về `choices[0].message.reasoning_content` kèm thống kê `usage.completion_tokens_details.reasoning_tokens`.
