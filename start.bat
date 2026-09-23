@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Gemini Gateway

echo ========================================
echo   Gemini Gateway - Antigravity Local API
echo ========================================
echo.

set "PY="
py -3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :found_py
python -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :found_py
python3 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=python3"
if defined PY goto :found_py
echo [ERROR] Python 3.11+ not found in PATH
pause
exit /b 1

:found_py
%PY% --version
%PY% -c "import sys; raise SystemExit(0 if sys.version_info>=(3,11) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Need Python 3.11 or newer
    pause
    exit /b 1
)

%PY% -c "import fastapi,uvicorn,httpx,pydantic,anyio" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing dependencies...
    %PY% -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] pip install failed
        pause
        exit /b 1
    )
)

if not exist "config.json" (
    if exist "config.example.json" (
        copy /y "config.example.json" "config.json" >nul
        echo [INFO] Created config.json from example
    )
)

set "HOST=127.0.0.1"
set "PORT=8789"
set "PORTFILE=%TEMP%\gg-port-%RANDOM%.txt"
%PY% -c "import json;s=json.load(open('config.json',encoding='utf-8')).get('server',{});print(str(s.get('host','127.0.0.1'))+'|'+str(s.get('port',8789)))" > "%PORTFILE%" 2>nul
if exist "%PORTFILE%" (
    for /f "usebackq tokens=1,2 delims=|" %%A in ("%PORTFILE%") do (
        set "HOST=%%A"
        set "PORT=%%B"
    )
    del /q "%PORTFILE%" >nul 2>&1
)
if "%HOST%"=="" set "HOST=127.0.0.1"
if "%PORT%"=="" set "PORT=8789"

echo.
echo [INFO] Gateway API: http://127.0.0.1:%PORT%/v1
echo [INFO] Web Panel:   http://127.0.0.1:%PORT%/
echo [INFO] Health:      http://127.0.0.1:%PORT%/health
echo ========================================
echo.

start "" "http://127.0.0.1:%PORT%/"

%PY% -m uvicorn app.main:app --host %HOST% --port %PORT%
if errorlevel 1 (
    echo.
    echo [ERROR] Server exited with error.
    pause
)
endlocal
