# Scheduled MWF skill review runner (invoked by Windows Task Scheduler).
# Host-agnostic: must behave identically under Windows PowerShell 5.1 (the
# registered task host) and pwsh 7. Native stderr is therefore never redirected
# at the PowerShell layer; 5.1 wraps redirected native stderr in ErrorRecords,
# which $ErrorActionPreference = "Stop" promotes to a terminating error.
$ErrorActionPreference = "Stop"
Set-Location C:\Claude\whisperdesk

$obsDir = "skill-observations"
$outFile = "$obsDir\last-scheduled-review-output.txt"
$statusFile = "$obsDir\last-scheduled-review-status.txt"
$claudeMd = "C:\Users\tito1\.claude\CLAUDE.md"

# Snapshot the global CLAUDE.md before the run: the review edits it
# autonomously, and a bad edit should be one copy away from recovery.
Copy-Item $claudeMd "$obsDir\claudemd-backup.md" -Force

# cmd-level redirect keeps claude's stderr as plain text in the log file.
# claude -p with no prompt argument reads the prompt from stdin, which avoids
# quoting the multi-line prompt on the command line.
cmd /c "claude -p --permission-mode acceptEdits --add-dir C:\Users\tito1\.claude < $obsDir\scheduled-task-draft.md > $outFile 2>&1"

if ($LASTEXITCODE -ne 0) {
    Add-Content $statusFile "FAILED $(Get-Date -Format o) exit=$LASTEXITCODE"
    exit 1
}
Add-Content $statusFile "OK $(Get-Date -Format o)"

# Commit the durable review artifacts. The prompt forbids the LLM from running
# git; the commit is script-owned so it is deterministic. Pathspec-scoped add
# and commit leave any user staging area alone. Only commit on master so a
# review that fires while a feature branch is checked out never pollutes a PR.
$reviewPaths = "$obsDir/log.md", "$obsDir/last-review-date.txt", "$obsDir/archive"
$branch = git rev-parse --abbrev-ref HEAD
if ($branch -ne "master") {
    Add-Content $statusFile "SKIPPED-COMMIT $(Get-Date -Format o) branch=$branch"
    exit 0
}
$dirty = git status --porcelain -- $reviewPaths
if ($dirty) {
    cmd /c "git add -- $obsDir/log.md $obsDir/last-review-date.txt $obsDir/archive >> $outFile 2>&1"
    cmd /c "git commit -m ""chore(skill-observations): scheduled review $(Get-Date -Format yyyy-MM-dd)"" -- $obsDir/log.md $obsDir/last-review-date.txt $obsDir/archive >> $outFile 2>&1"
    if ($LASTEXITCODE -ne 0) {
        Add-Content $statusFile "FAILED-COMMIT $(Get-Date -Format o) exit=$LASTEXITCODE"
        exit 1
    }
}
