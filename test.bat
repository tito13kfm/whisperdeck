@echo off
REM WhisperDeck — Test runner for Windows
cd /d "%~dp0"

REM Check for virtual environment
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

REM Run pytest with any passed arguments
%PYTHON% -m pytest %*
