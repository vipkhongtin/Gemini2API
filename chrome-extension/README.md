# Tiện ích Chrome: Gemini2API - Google AI Studio Bridge (Gemini 4 Argon)

Tiện ích Chrome này giúp bạn sử dụng **Gemini 4 Argon (`gemini-4-argon`)** trên **Google AI Studio (`https://aistudio.google.com`)** thông qua trình duyệt Chrome thật của bạn **mà không cần Google API Key**.

## Cách cài đặt vào Chrome (Chỉ mất 30 giây)

1. Mở trình duyệt Chrome (hoặc Edge / Brave / Cốc Cốc), nhập vào thanh địa chỉ:
   ```
   chrome://extensions
   ```
2. Bật công tắc **Chế độ dành cho nhà phát triển (Developer mode)** ở góc trên bên phải.
3. Nhấn nút **Tải tiện ích đã giải nén (Load unpacked)** ở góc trên bên trái.
4. Chọn thư mục:
   ```
   d:\AI KIEN\GEMINI2API\chrome-extension
   ```
5. Mở một tab **`https://aistudio.google.com/prompts/new_chat`** và đăng nhập tài khoản Google của bạn.
6. Nhấp vào biểu tượng tiện ích **Gemini2API Bridge** trên thanh công cụ Chrome → bấm **"⚡ Kết nối & Đồng bộ AI Studio ngay"**. Biểu tượng tiện ích sẽ hiện chữ **`ON`** màu xanh lá!

## Cơ chế hoạt động kép

1. **Live Browser Bridge (`/ws/extension`)**:
   - Tiện ích giữ kết nối WebSocket trực tiếp với `Gemini2API` (`ws://127.0.0.1:18610/ws/extension`).
   - Khi có yêu cầu chat tới `http://127.0.0.1:18610/v1/chat/completions`, tiện ích tự động chọn model **Gemini 4 Argon** trên tab `aistudio.google.com` đang mở trong Chrome của bạn, gửi câu hỏi và truyền trực tiếp luồng **Thinking (`reasoning_content`)** + **Câu trả lời** về cho bạn.
2. **Đồng bộ Cookie 1-Click (`/admin/accounts`)**:
   - Tiện ích tự động đồng bộ phiên cookie của bạn vào Account Pool trên Server để bạn có thể quản lý nhiều tài khoản trên `http://127.0.0.1:18610/dashboard`.
