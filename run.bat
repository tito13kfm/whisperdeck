@echo off
REM WhisperDeck — Launcher for Windows
cd /d "%~dp0"

REM Read the app version from app.py (the FastAPI version= line) so the
REM banner can't drift from the code again.
set VERSION=
for /f "tokens=2 delims==," %%v in ('findstr /r /c:"^ *version=" app.py') do set VERSION=%%v
if not defined VERSION (set VERSION=unknown) else set VERSION=%VERSION:"=%

echo ==============================================
echo          WhisperDeck v%VERSION%
echo   Transcribe - Diarize - Summarize - Identify
echo ==============================================
echo.

REM Check for virtual environment
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
    echo [*] Using virtual environment
) else (
    set PYTHON=python
    echo [*] Using system Python
)

echo [*] Installing/checking dependencies...
%PYTHON% -m pip install -q -r requirements.txt 2>nul

echo [*] Starting server on http://localhost:9781
echo [*] Open http://localhost:9781 in your browser
echo.
%PYTHON% app.py