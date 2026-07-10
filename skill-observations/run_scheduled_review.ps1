# Scheduled MWF skill review runner (invoked by Windows Task Scheduler).
$ErrorActionPreference = "Stop"
Set-Location C:\Claude\whisperdesk
$prompt = Get-Content .\skill-observations\scheduled-task-draft.md -Raw
& claude -p $prompt --permission-mode acceptEdits *> .\skill-observations\last-scheduled-review-output.txt
