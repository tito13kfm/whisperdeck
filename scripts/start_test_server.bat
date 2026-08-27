@echo off
set PORT=9786
set WHISPERDESK_DATA_DIR=%TEMP%\whisperdeck-diarize-594599271
if "%HUGGINGFACE_TOKEN%"=="" (
    echo Set HUGGINGFACE_TOKEN in your environment before running this script.
    exit /b 1
)
cd /d C:\Claude\whisperdesk\whisperdesk
python app.py