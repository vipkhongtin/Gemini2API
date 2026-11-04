@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title Gemini2API - Chat Gemini (Chinh) ^& AI Studio (Phu) Bridge

cd /d "%~dp0"

echo ====================================================================
echo   GEMINI2API - Bien Chat Gemini (gemini.google.com) thanh OpenAI API
echo   * Nguon Chinh (Primary)   : https://gemini.google.com/app
echo   * Nguon Phu   (Secondary) : https://aistudio.google.com
echo ====================================================================
echo.

:: 1. Khoi tao file .env neu chua co (mac dinh bat GEMINI2API_OPEN_ACCESS=true)
if not exist ".env" (
    if exist ".env.example" (
        echo [INFO] Dang tao file cau hinh .env mac dinh...
        copy /Y ".env.example" ".env" >nul
    )
)

:: 2. Kiem tra moi truong ao .venv
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Dang khoi tao moi truong ao .venv va cai dat thu vien...
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        uv venv .venv
        uv pip install -e .
    ) else (
        python -m venv .venv
        ".venv\Scripts\pip.exe" install -e .
    )
    if not exist ".venv\Scripts\python.exe" (
        echo [ERROR] Khong the tao moi truong ao .venv. Vui long kiem tra Python/uv.
        pause
        exit /b 1
    )
)

:: 3. Doc cong (PORT) tu .env (mac dinh 18610)
set "PORT=18610"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "KEY=%%A"
        set "VAL=%%B"
        if /i "!KEY!"=="GEMINI2API_PORT" (
            if not "!VAL!"=="" set "PORT=!VAL!"
        )
    )
)

set "DASHBOARD_URL=http://127.0.0.1:!PORT!/dashboard"
set "API_BASE_URL=http://127.0.0.1:!PORT!/v1"

echo   1. Thu muc Chrome Extension : %~dp0chrome-extension
echo      (Mo chrome://extensions -^> Developer mode -^> Load unpacked)
echo   2. Nguon Chinh (Primary)    : https://gemini.google.com/app
echo   3. Nguon Phu   (Secondary)  : https://aistudio.google.com/prompts/new_chat
echo   4. Web Dashboard + Chat thu : !DASHBOARD_URL!
echo   5. OpenAI Endpoint          : !API_BASE_URL!  (Khong can API Key)
echo ====================================================================
echo [INFO] Dang mo trinh duyet tai !DASHBOARD_URL! sau 2 giay...
echo.

:: 4. Tu dong mo trinh duyet sau 2 giay (chay ngam)
start "" /b cmd /c "timeout /t 2 /nobreak >nul & start "" "!DASHBOARD_URL!""

:: 5. Khoi chay server Gemini2API
set "PYTHONPATH=%~dp0src"
".venv\Scripts\python.exe" -m gemini2api

echo.
echo [WARN] Server da dung.
pause
