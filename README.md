# Gemini2API (`gemini2api`)

**Gemini2API** là OpenAI-compatible API Gateway dành cho **Google AI Studio (`aistudio.google.com`)** và **Gemini 4 Argon (`gemini-4-argon`)**, được thiết kế dựa trên kiến trúc chuẩn của [`muse2api`](https://github.com/www222fff/muse2api).

Hỗ trợ đầy đủ:
- **Chat & Streaming (`/v1/chat/completions`, `/v1/responses`)** với **Gemini 4 Argon** (`gemini-4-argon`, `gemini-4-argon-high`, `gemini-4-argon-medium`, `gemini-4-argon-low`, `gemini-4-argon-nothink`).
- **Thinking / Reasoning Stream**: trả về `reasoning_content` chuẩn OpenAI / DeepSeek R1 (hoặc bọc trong `<think>...</think>` khi bật `GEMINI2API_THINK_TAGS=true`), điều chỉnh mức độ suy luận qua `reasoning_effort` (`high` / `medium` / `low` / `none`) hoặc `thinking`.
- **Multimodal Vision Input**: gửi ảnh qua `image_url` (URL hoặc `data:image/...;base64,...`) hoặc tạo/chỉnh sửa ảnh (`/v1/images/generations`, `/v1/images/edits`) và video (`/v1/videos`).
- **Account Pool đa tài khoản**: xoay vòng `lru`, `round_robin`, `affinity`, tự động failover khi hết quota, giữ phiên sống (`keepalive`), giới hạn luồng đồng thời trên mỗi tài khoản.
- **3 chế độ Driver**:
  - `browser` (mặc định): điều khiển Chromium qua CDP kết nối trực tiếp tới `https://aistudio.google.com/prompts/new_chat`, tự động chọn model **Gemini 4 Argon** và cấu hình Thinking Level.
  - `http`: gọi trực tiếp SSE qua `generativelanguage.googleapis.com` nếu tài khoản có `api_key` (AI Studio API Key).
  - `mock`: kiểm thử offline tốc độ cao không cần tài khoản.
- **Web Dashboard (`/dashboard`) & Chrome Extension (`/admin/extension.zip`)**: quản lý tài khoản, tạo/thu hồi Client API Key (`g2a-...`), theo dõi thống kê request/latency theo thời gian thực và nhập cookie Google AI Studio chỉ với 1 cú nhấp chuột.

---

## 1. Cài đặt & Khởi chạy nhanh

### Cách 1: Nhấp đúp chạy `start.bat` (Windows - Nhanh nhất)

Chỉ cần chạy file [`start.bat`](start.bat):
- Tự động tạo `.env` từ `.env.example` (nếu chưa có).
- Tự động kiểm tra/khởi tạo môi trường `.venv`.
- Tự động in **Admin/API Key (`g2a-...`)** ra màn hình console.
- Tự động khởi chạy server ở cổng `18610` và mở trình duyệt tới **`http://127.0.0.1:18610/dashboard`**.

### Cách 2: Chạy trực tiếp bằng dòng lệnh PowerShell

```powershell
# 1. Tạo môi trường ảo và cài đặt
uv venv .venv
uv pip install -e ".[dev]"

# 2. Tạo file cấu hình .env
Copy-Item .env.example .env

# 3. Khởi chạy server (mặc định cổng 18610)
.\.venv\Scripts\gemini2api
```

Mở trình duyệt tại: **`http://127.0.0.1:18610/dashboard`** và đăng nhập bằng `GEMINI2API_ADMIN_KEY` (hoặc khóa tự sinh trong `data/api_key`).

### Chạy bằng Docker Compose

```bash
cp .env.example .env
docker compose up -d --build
```

---

## 2. Nhập tài khoản Google AI Studio

Bạn có 3 cách thêm tài khoản vào Account Pool:

1. **Chrome Extension 1-Click (Khuyên dùng)**:
   - Trên `/dashboard`, bấm **Download Extension (.zip)** (hoặc tải từ `GET /admin/extension.zip`).
   - Giải nén, mở `chrome://extensions`, bật **Developer mode** → **Load unpacked**.
   - Đăng nhập `https://aistudio.google.com` trên trình duyệt, mở popup tiện ích **gemini2api Cookie Helper** và bấm **Import current account**.
2. **Dán Cookie trên Dashboard**:
   - Mở DevTools trên `https://aistudio.google.com`, copy chuỗi `Cookie` header (chứa `SID`, `__Secure-1PSID`, `SAPISID`, ...) hoặc xuất JSON từ Cookie-Editor rồi dán vào mục **Add Account** trên `/dashboard`.
3. **Dùng AI Studio API Key (cho `GEMINI2API_DRIVER=http`)**:
   - Thêm tài khoản với JSON: `{"api_key": "AIzaSy..."}`.

---

## 3. Sử dụng Gemini 4 Argon với OpenAI SDK / Cursor / Cline / Roo Code

### Danh sách Model hỗ trợ (`/v1/models`)

| Model ID | Upstream Model | Thinking Level | Mô tả |
|---|---|---|---|
| `gemini-4-argon` | `models/gemini-4-argon` | `high` (mặc định) | Gemini 4 Argon trên Google AI Studio (mặc định cho mọi chat alias) |
| `gemini-4-argon-high` | `models/gemini-4-argon` | `high` | Gemini 4 Argon với mức suy luận sâu nhất (High Thinking) |
| `gemini-4-argon-medium` | `models/gemini-4-argon` | `medium` | Gemini 4 Argon với mức suy luận cân bằng (Medium Thinking) |
| `gemini-4-argon-low` | `models/gemini-4-argon` | `low` | Gemini 4 Argon phản hồi nhanh, suy luận ngắn gọn (Low Thinking) |
| `gemini-4-argon-nothink` | `models/gemini-4-argon` | `none` | Gemini 4 Argon tắt hoàn toàn bước suy luận |
| `gemini-2.5-pro` | `models/gemini-2.5-pro` | `high` | Gemini 2.5 Pro trên AI Studio |
| `gemini-2.5-flash` | `models/gemini-2.5-flash` | `medium` | Gemini 2.5 Flash trên AI Studio |
| `gemini-image` | `models/imagen-3.0-generate-002` | - | Tạo & chỉnh sửa ảnh (`/v1/images/*`) |
| `gemini-video` | `models/veo-2.0-generate-001` | - | Tạo video bất đồng bộ (`/v1/videos`) |

### Ví dụ gọi bằng Python `openai` SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18610/v1",
    api_key="g2a-...",  # Hoặc GEMINI2API_API_KEY trong .env / data/api_key
)

stream = client.chat.completions.create(
    model="gemini-4-argon",
    reasoning_effort="high",  # "high" | "medium" | "low" | "none"
    messages=[
        {"role": "system", "content": "Bạn là chuyên gia kiến trúc phần mềm."},
        {"role": "user", "content": "Phân tích ưu nhược điểm của Event Sourcing."},
    ],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta
    reasoning = getattr(delta, "reasoning_content", None)
    if reasoning:
        print(reasoning, end="", flush=True)
    if delta.content:
        print(delta.content, end="", flush=True)
```

---

## 4. Kiểm thử (Testing)

```powershell
pytest -v
```

Chi tiết kiến trúc và bảng ánh xạ từ `muse2api` sang `gemini2api` được trình bày trong [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
