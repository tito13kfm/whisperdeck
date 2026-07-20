@echo off
REM WhisperDeck — Launcher for Windows
cd /d "%~dp0"

echo ==============================================
echo          WhisperDeck v0.7
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