# Gemini2API (`gemini2api`)

<p align="center">
  <b>OpenAI-Compatible API Gateway for Google Gemini Web Chat (<code>gemini.google.com</code> — Primary) &amp; Google AI Studio (<code>aistudio.google.com</code> — Secondary)</b><br>
  <i>Use Google Gemini directly in Cursor, Cline, Roo Code, LobeChat, OpenWebUI, NextChat &amp; OpenAI SDKs — No Paid API Key Required!</i>
</p>

<p align="center">
  🌍 <b>Languages / Ngôn ngữ / 语言 / 言語 / 언어 / Idiomas / Langues / Языки:</b><br>
  <a href="#-english">🇬🇧 English</a> ·
  <a href="#-tiếng-việt">🇻🇳 Tiếng Việt</a> ·
  <a href="#-简体中文">🇨🇳 简体中文</a> ·
  <a href="#-日本語">🇯🇵 日本語</a> ·
  <a href="#-한국어">🇰🇷 한국어</a> ·
  <a href="#-español">🇪🇸 Español</a> ·
  <a href="#-français">🇫🇷 Français</a> ·
  <a href="#-русский">🇷🇺 Русский</a> ·
  <a href="#-português">🇧🇷 Português</a> ·
  <a href="#-deutsch">🇩🇪 Deutsch</a>
</p>

---

## 🇬🇧 English

### Overview
**Gemini2API** is a local & self-hosted **OpenAI-compatible API Gateway** designed primarily for **Google Gemini Web Chat (`https://gemini.google.com/app`)**, with **Google AI Studio (`https://aistudio.google.com`)** supported as a secondary upstream.

- **Primary Upstream (`gemini`)**: **Chat Gemini (`https://gemini.google.com/app`)** — connects directly to your logged-in Google Gemini web session.
- **Secondary Upstream (`aistudio`)**: **Google AI Studio (`https://aistudio.google.com/prompts/new_chat`)** — easily switchable from the Chrome Extension popup or `.env` (`GEMINI2API_UPSTREAM_MODE=aistudio`).
- **Full OpenAI Compatibility**:
  - `POST /v1/chat/completions` & `POST /v1/responses` (streaming & non-streaming).
  - **Thinking / Reasoning Stream**: emits `reasoning_content` (OpenAI / DeepSeek R1 format) or `<think>...</think>` tags (`GEMINI2API_THINK_TAGS=true`), controlled via `reasoning_effort` (`high` | `medium` | `low` | `none`).
  - **Smart Session Lifecycle**: automatically opens a new chat for new conversations, sends only delta messages for ongoing turns, and navigates back to saved threads (`https://gemini.google.com/app/<id>`) when reopening old chats.
  - **Multimodal & Media**: Vision input (`image_url`), Image generation/edits (`/v1/images/generations`, `/v1/images/edits`), and Video generation (`/v1/videos`).
  - **Multi-Account Pool & Web Dashboard (`/dashboard`)**: `affinity` / `lru` / `round_robin` routing, automatic quota failover, cookie keepalive, and real-time analytics.

### 1. Quick Start

#### Option A: 1-Click Start on Windows (`start.bat`)
Double-click [`start.bat`](start.bat):
- Automatically creates `.env` from `.env.example`.
- Automatically sets up the `.venv` Python environment and installs dependencies.
- Starts the gateway on `http://127.0.0.1:18610` and opens the Web Dashboard at **`http://127.0.0.1:18610/dashboard`**.

#### Option B: Command Line (Windows / macOS / Linux)
```bash
# 1. Create virtual environment & install
uv venv .venv
uv pip install -e ".[dev]"

# 2. Copy default configuration
cp .env.example .env

# 3. Start server (default port: 18610)
gemini2api
```

#### Option C: Docker Compose
```bash
cp .env.example .env
docker compose up -d --build
```

### 2. Connect Chrome Extension (30 Seconds — Recommended)
1. Open Chrome / Edge / Brave and go to `chrome://extensions`.
2. Enable **Developer mode** (top-right corner).
3. Click **Load unpacked** and select the [`chrome-extension`](chrome-extension) folder (or download `.zip` from `/dashboard` → **Accounts** → **Download extension**).
4. Open **Chat Gemini (Primary)**: **`https://gemini.google.com/app`** (or **Google AI Studio (Secondary)**: `https://aistudio.google.com/prompts/new_chat`) and sign in with your Google account.
5. Click the **Gemini2API Bridge** extension icon → choose **💬 Chat Gemini (Primary)** or **🧪 Google AI Studio (Secondary)** → click **"⚡ Connect & Sync"**. Badge will show green **`ON`**.

### 3. Supported Models (`/v1/models`)

| Model ID | Target Upstream | Thinking Effort | Description |
|---|---|---|---|
| `gemini-chat` | **Chat Gemini (`gemini.google.com`) [Primary]** | `high` | Default Google Gemini Web Chat model |
| `gemini-pro` | **Chat Gemini (`gemini.google.com`) [Primary]** | `high` | Google Gemini Pro / Thinking mode |
| `gemini-flash` | **Chat Gemini (`gemini.google.com`) [Primary]** | `low` | Google Gemini Fast / Flash mode |
| `gemini-2.5-pro` | Chat Gemini (Primary) / AI Studio (Secondary) | `high` | Gemini 2.5 Pro |
| `gemini-2.5-flash` | Chat Gemini (Primary) / AI Studio (Secondary) | `low` | Gemini 2.5 Flash |
| `gemini-4-argon` | Chat Gemini / AI Studio (`aistudio.google.com`) | `high` | Gemini 4 Argon (`-high`, `-medium`, `-low`, `-nothink` variants) |
| `gemini-image` | Imagen 3 | - | Image generation & editing (`/v1/images/*`) |
| `gemini-video` | Veo 2 | - | Async video generation (`/v1/videos`) |

> **Client Aliases**: Standard OpenAI/Anthropic/DeepSeek model names (`gpt-4o`, `gpt-4.1`, `gpt-5`, `o1`, `o3`, `claude-sonnet-4`, `deepseek-chat`, `deepseek-reasoner`) are automatically routed to Gemini!

### 4. Example Usage (Python OpenAI SDK & Cursor / Cline / Roo Code)

- **Base URL**: `http://127.0.0.1:18610/v1`
- **API Key**: `local` (not required when `GEMINI2API_OPEN_ACCESS=true`, or issue a `g2a-...` key in `/dashboard`)
- **Model ID**: `gemini-chat` (or `gemini-pro`, `gemini-4-argon`)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:18610/v1",
    api_key="local",
)

stream = client.chat.completions.create(
    model="gemini-chat",
    reasoning_effort="high",
    messages=[{"role": "user", "content": "Explain Event Sourcing with a concise example."}],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta
    if getattr(delta, "reasoning_content", None):
        print(delta.reasoning_content, end="", flush=True)
    if delta.content:
        print(delta.content, end="", flush=True)
```

---

## 🇻🇳 Tiếng Việt

### Giới thiệu
**Gemini2API** là cổng chuyển đổi **OpenAI-compatible API Gateway** lấy **Google Gemini Web Chat (`https://gemini.google.com/app`) làm nguồn CHÍNH** (đúng với tên gọi **Gemini2API**), đồng thời hỗ trợ **Google AI Studio (`https://aistudio.google.com`) làm nguồn PHỤ**.

- **Nguồn Chính (`gemini` — Mặc định)**: **Chat Gemini (`https://gemini.google.com/app`)** — kết nối trực tiếp với trang chat Gemini chính thức qua Chrome Extension hoặc trình duyệt CDP mà không cần API Key.
- **Nguồn Phụ (`aistudio` — Tùy chọn)**: **Google AI Studio (`https://aistudio.google.com/prompts/new_chat`)** — có thể chuyển đổi bất kỳ lúc nào ngay trong popup Chrome Extension hoặc cấu hình `GEMINI2API_UPSTREAM_MODE=aistudio` trong `.env`.
- **Tính năng nổi bật**:
  - Tương thích hoàn toàn chuẩn OpenAI (`/v1/chat/completions`, `/v1/responses`, `/v1/models`, `/v1/images/*`, `/v1/videos`).
  - **Streaming Thinking / Suy luận**: tách riêng luồng suy nghĩ `reasoning_content` chuẩn OpenAI / DeepSeek R1 (hoặc bọc trong `<think>...</think>` khi bật `GEMINI2API_THINK_TAGS=true`).
  - **Quản lý phiên thông minh**: tự động mở cuộc trò chuyện mới trên `gemini.google.com/app` khi tạo chat mới, gửi tiếp tin nhắn trong cùng tab khi đang hội thoại, và tự động mở lại đúng đường dẫn chat cũ khi bạn chọn lại lịch sử chat.
  - **Web Dashboard (`/dashboard`)**: giao diện quản lý tài khoản, khóa API, theo dõi lưu lượng và khung **Chat thử trực tiếp** trên trình duyệt.

### 1. Cài đặt & Khởi chạy nhanh

1. **Cách nhanh nhất (Windows)**: Nhấp đúp chạy file [`start.bat`](start.bat). Hệ thống sẽ tự động cài đặt môi trường `.venv`, tạo file `.env`, khởi chạy server ở cổng `18610` và mở trình duyệt tới **`http://127.0.0.1:18610/dashboard`**.
2. **Cài đặt Chrome Extension (30 giây)**:
   - Mở trình duyệt Chrome / Edge / Brave / Cốc Cốc, truy cập: `chrome://extensions`
   - Bật **Chế độ dành cho nhà phát triển (Developer mode)** ở góc trên bên phải.
   - Bấm **Tải tiện ích đã giải nén (Load unpacked)** → chọn thư mục [`chrome-extension`](chrome-extension).
   - Mở một tab **Chat Gemini (Chính)**: **`https://gemini.google.com/app`** (hoặc **AI Studio (Phụ)**: `https://aistudio.google.com/prompts/new_chat`) và đăng nhập tài khoản Google.
   - Bấm vào biểu tượng tiện ích **Gemini2API Bridge** → chọn nguồn **💬 Chat Gemini (Chính)** (mặc định) hoặc **🧪 Google AI Studio (Phụ)** → bấm **"⚡ Kết nối & Đồng bộ ngay"**. Biểu tượng hiện chữ **`ON`** màu xanh lá là hoàn tất!

### 2. Cấu hình trong Cursor / Cline / Roo Code / ChatBox / LobeChat
- **API Provider**: `OpenAI Compatible`
- **Base URL**: `http://127.0.0.1:18610/v1`
- **API Key**: `local` (hoặc tạo mã `g2a-...` trong tab **API Keys** trên Dashboard)
- **Model ID**: `gemini-chat`, `gemini-pro`, `gemini-flash`, hoặc `gemini-4-argon`

---

## 🇨🇳 简体中文

### 项目简介
**Gemini2API** 是一个本地自托管的 **OpenAI 兼容 API 网关**，以 **Google Gemini 网页版 (`https://gemini.google.com/app`) 作为主要上游 (Primary)**，同时保留 **Google AI Studio (`https://aistudio.google.com`) 作为辅助上游 (Secondary)**，无需付费 API Key 即可在任何支持 OpenAI 的工具中使用 Gemini。

- **主数据源 (`gemini` - 默认)**：**Chat Gemini (`https://gemini.google.com/app`)** —— 通过 Chrome 扩展实时桥接 (`/ws/extension`) 或 CDP 浏览器直接驱动您已登录的 Gemini 网页。
- **辅助数据源 (`aistudio` - 可选)**：**Google AI Studio (`https://aistudio.google.com/prompts/new_chat`)** —— 可随时在 Chrome 扩展弹窗或 `.env` (`GEMINI2API_UPSTREAM_MODE=aistudio`) 中切换。
- **核心特性**：
  - 完整支持 `/v1/chat/completions`、`/v1/responses`、`/v1/models`、`/v1/images/*`、`/v1/videos`。
  - **思考链 (Thinking / Reasoning) 流式输出**：原生输出 `reasoning_content` 字段（兼容 DeepSeek R1 / OpenAI o1/o3 格式），也可通过 `GEMINI2API_THINK_TAGS=true` 输出 `<think>...</think>` 标签。
  - **多会话自动接续**：新建对话自动在 Gemini 网页开启新会话；多轮对话仅发送增量消息；重新打开历史会话时自动跳转回对应的历史链接继续对话。

### 快速开始
1. **启动服务**：
   - **Windows 一键启动**：双击运行 [`start.bat`](start.bat)，自动配置 `.venv` 与 `.env`，并在 `http://127.0.0.1:18610/dashboard` 打开管理面板。
   - **命令行启动**：执行 `uv venv .venv && uv pip install -e . && gemini2api`。
2. **安装 Chrome 扩展（推荐）**：
   - 打开浏览器访问 `chrome://extensions`，开启右上角 **开发者模式 (Developer mode)**。
   - 点击左上角 **加载已解压的扩展程序 (Load unpacked)**，选择项目下的 [`chrome-extension`](chrome-extension) 文件夹。
   - 打开 **`https://gemini.google.com/app`**（主）或 `https://aistudio.google.com`（辅）并登录 Google 账号。
   - 点击扩展图标，选择 **Chat Gemini（主）** 或 **AI Studio（辅）**，点击连接按钮，图标显示绿色 **`ON`** 即可开始调用！
3. **客户端接入 (Cursor / Cline / Roo Code / ChatBox / LobeChat)**：
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`、`gemini-pro`、`gemini-flash` 或 `gemini-4-argon`

---

## 🇯🇵 日本語

### 概要
**Gemini2API** は、**Google Gemini ウェブチャット (`https://gemini.google.com/app`) をメイン (Primary)** とし、**Google AI Studio (`https://aistudio.google.com`) をサブ (Secondary)** としてサポートする **OpenAI 互換 API ゲートウェイ**です。有料の API キーなしで、Cursor、Cline、Roo Code、LobeChat、OpenWebUI、Python/Node.js SDK から直接 Gemini を利用できます。

### クイックスタート
1. **サーバーの起動**:
   - **Windows**: [`start.bat`](start.bat) をダブルクリックするだけで `.venv` と `.env` が自動作成され、`http://127.0.0.1:18610/dashboard` が開きます。
   - **CLI (macOS / Linux / Windows)**: `uv venv .venv && uv pip install -e . && gemini2api`
2. **Chrome 拡張機能の導入 (30秒)**:
   - Chrome で `chrome://extensions` を開き、右上の **デベロッパー モード** をオンにします。
   - **パッケージ化されていない拡張機能を読み込む (Load unpacked)** をクリックし、[`chrome-extension`](chrome-extension) フォルダを選択します。
   - **`https://gemini.google.com/app`**（メイン）または `https://aistudio.google.com`（サブ）を開いて Google アカウントにログインします。
   - 拡張機能アイコンをクリックして同期ボタンを押すと、緑色の **`ON`** バッジが表示されます。
3. **OpenAI クライアント設定**:
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`、`gemini-pro`、`gemini-flash`、`gemini-4-argon`

---

## 🇰🇷 한국어

### 소개
**Gemini2API**는 **Google Gemini 웹 채팅 (`https://gemini.google.com/app`)을 기본(Primary)**으로 사용하고 **Google AI Studio (`https://aistudio.google.com`)를 보조(Secondary)**로 지원하는 **OpenAI 호환 API 게이트웨이**입니다. 별도의 유료 API 키 없이도 Cursor, Cline, Roo Code, LobeChat, OpenAI SDK에서 Gemini를 사용할 수 있습니다.

### 빠른 시작
1. **서버 실행**:
   - **Windows**: [`start.bat`](start.bat) 파일을 더블 클릭하면 가상 환경(`.venv`) 및 설정 파일(`.env`)이 자동 생성되며 `http://127.0.0.1:18610/dashboard` 대시보드가 열립니다.
   - **명령줄 실행**: `uv venv .venv && uv pip install -e . && gemini2api`
2. **Chrome 확장 프로그램 설치**:
   - Chrome 주소창에 `chrome://extensions` 입력 후 우측 상단 **개발자 모드(Developer mode)** 활성화.
   - **압축해제된 확장 프로그램을 로드합니다(Load unpacked)** 클릭 후 [`chrome-extension`](chrome-extension) 폴더 선택.
   - **`https://gemini.google.com/app`**(기본) 또는 `https://aistudio.google.com`(보조)에 접속하여 Google 계정 로그인.
   - 확장 프로그램 아이콘을 클릭해 연결 버튼을 누르면 녹색 **`ON`** 배지가 표시됩니다.
3. **연동 설정**:
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`, `gemini-pro`, `gemini-flash`, `gemini-4-argon`

---

## 🇪🇸 Español

### Descripción General
**Gemini2API** es una pasarela **API compatible con OpenAI** que utiliza **Google Gemini Web Chat (`https://gemini.google.com/app`) como fuente PRINCIPAL (Primary)** y **Google AI Studio (`https://aistudio.google.com`) como fuente SECUNDARIA (Secondary)**, sin necesidad de una clave API de pago.

### Inicio Rápido
1. **Iniciar el servidor**:
   - **Windows**: Haz doble clic en [`start.bat`](start.bat). Configurará automáticamente `.venv` y `.env` y abrirá el panel en `http://127.0.0.1:18610/dashboard`.
   - **Terminal (macOS / Linux / Windows)**: `uv venv .venv && uv pip install -e . && gemini2api`
2. **Instalar la extensión de Chrome**:
   - Abre `chrome://extensions`, activa el **Modo de desarrollador (Developer mode)**.
   - Haz clic en **Cargar descomprimida (Load unpacked)** y selecciona la carpeta [`chrome-extension`](chrome-extension).
   - Abre **`https://gemini.google.com/app`** (Principal) o `https://aistudio.google.com` (Secundario) e inicia sesión en Google.
   - Haz clic en el icono de la extensión **Gemini2API Bridge** y pulsa el botón de conexión (el icono mostrará **`ON`** en verde).
3. **Configuración del cliente (Cursor / Cline / Roo Code / SDK de OpenAI)**:
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`, `gemini-pro`, `gemini-flash`, `gemini-4-argon`

---

## 🇫🇷 Français

### Présentation
**Gemini2API** est une passerelle **API compatible OpenAI** utilisant **Google Gemini Web Chat (`https://gemini.google.com/app`) comme source PRINCIPALE (Primary)** et **Google AI Studio (`https://aistudio.google.com`) comme source SECONDAIRE (Secondary)**, sans nécessiter de clé API payante.

### Démarrage Rapide
1. **Lancer le serveur** :
   - **Windows** : Double-cliquez sur [`start.bat`](start.bat). Le tableau de bord s'ouvrira automatiquement sur `http://127.0.0.1:18610/dashboard`.
   - **Terminal** : `uv venv .venv && uv pip install -e . && gemini2api`
2. **Installer l'extension Chrome** :
   - Ouvrez `chrome://extensions` et activez le **Mode développeur (Developer mode)**.
   - Cliquez sur **Charger l'extension non empaquetée (Load unpacked)** et sélectionnez le dossier [`chrome-extension`](chrome-extension).
   - Connectez-vous sur **`https://gemini.google.com/app`** (Principal) ou `https://aistudio.google.com` (Secondaire), puis cliquez sur l'extension pour activer le pont (**`ON`**).
3. **Configuration OpenAI** :
   - **Base URL** : `http://127.0.0.1:18610/v1`
   - **API Key** : `local`
   - **Model** : `gemini-chat`, `gemini-pro`, `gemini-flash`, `gemini-4-argon`

---

## 🇷🇺 Русский

### Описание
**Gemini2API** — это локальный **OpenAI-совместимый API-шлюз**, который использует **веб-чат Google Gemini (`https://gemini.google.com/app`) в качестве ОСНОВНОГО источника (Primary)** и **Google AI Studio (`https://aistudio.google.com`) в качестве ДОПОЛНИТЕЛЬНОГО (Secondary)** без необходимости платного API-ключа.

### Быстрый старт
1. **Запуск сервера**:
   - **Windows**: Дважды нажмите на [`start.bat`](start.bat). Скрипт автоматически создаст окружение `.venv`, файл `.env` и откроет панель управления `http://127.0.0.1:18610/dashboard`.
   - **Терминал**: `uv venv .venv && uv pip install -e . && gemini2api`
2. **Установка расширения Chrome**:
   - Откройте `chrome://extensions` и включите **Режим разработчика (Developer mode)**.
   - Нажмите **Загрузить распакованное расширение (Load unpacked)** и выберите папку [`chrome-extension`](chrome-extension).
   - Откройте **`https://gemini.google.com/app`** (Основной) или `https://aistudio.google.com` (Дополнительный), войдите в аккаунт Google и нажмите кнопку синхронизации в расширении (загорится зелёный значок **`ON`**).
3. **Подключение в Cursor / Cline / Roo Code / OpenAI SDK**:
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`, `gemini-pro`, `gemini-flash`, `gemini-4-argon`

---

## 🇧🇷 Português

### Visão Geral
**Gemini2API** é um gateway de **API compatível com OpenAI** que utiliza o **Google Gemini Web Chat (`https://gemini.google.com/app`) como fonte PRINCIPAL (Primary)** e o **Google AI Studio (`https://aistudio.google.com`) como fonte SECUNDÁRIA (Secondary)**, sem precisar de chave de API paga.

### Início Rápido
1. **Iniciar o servidor**:
   - **Windows**: Execute [`start.bat`](start.bat) com dois cliques. O painel abrirá automaticamente em `http://127.0.0.1:18610/dashboard`.
   - **Terminal**: `uv venv .venv && uv pip install -e . && gemini2api`
2. **Instalar a Extensão do Chrome**:
   - Acesse `chrome://extensions` e ative o **Modo do desenvolvedor (Developer mode)**.
   - Clique em **Carregar sem compactação (Load unpacked)** e selecione a pasta [`chrome-extension`](chrome-extension).
   - Abra **`https://gemini.google.com/app`** (Principal) ou `https://aistudio.google.com` (Secundário), faça login na sua conta Google e clique no botão de conexão da extensão (o ícone ficará verde **`ON`**).
3. **Configuração OpenAI**:
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`, `gemini-pro`, `gemini-flash`, `gemini-4-argon`

---

## 🇩🇪 Deutsch

### Überblick
**Gemini2API** ist ein **OpenAI-kompatibles API-Gateway**, das **Google Gemini Web Chat (`https://gemini.google.com/app`) als PRIMÄRE Quelle (Primary)** und **Google AI Studio (`https://aistudio.google.com`) als SEKUNDÄRE Quelle (Secondary)** nutzt — ganz ohne kostenpflichtigen API-Schlüssel.

### Schnellstart
1. **Server starten**:
   - **Windows**: Doppelklicken Sie auf [`start.bat`](start.bat). Das Dashboard öffnet sich automatisch unter `http://127.0.0.1:18610/dashboard`.
   - **Terminal**: `uv venv .venv && uv pip install -e . && gemini2api`
2. **Chrome-Erweiterung installieren**:
   - Öffnen Sie `chrome://extensions` und aktivieren Sie den **Entwicklermodus (Developer mode)**.
   - Klicken Sie auf **Entpackte Erweiterung laden (Load unpacked)** und wählen Sie den Ordner [`chrome-extension`](chrome-extension).
   - Öffnen Sie **`https://gemini.google.com/app`** (Primär) oder `https://aistudio.google.com` (Sekundär), melden Sie sich bei Google an und klicken Sie in der Erweiterung auf Verbinden (**`ON`**).
3. **Client-Konfiguration**:
   - **Base URL**: `http://127.0.0.1:18610/v1`
   - **API Key**: `local`
   - **Model**: `gemini-chat`, `gemini-pro`, `gemini-flash`, `gemini-4-argon`

---

## 🧪 Testing / Kiểm thử

```bash
pytest -v
```

Architecture details are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
