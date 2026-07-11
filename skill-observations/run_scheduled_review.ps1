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

# Snapshot the global CLAUDE.md before the run: the review updates it
# autonomously, and a bad update should be one copy away from recovery.
Copy-Item $claudeMd "$obsDir\claudemd-backup.md" -Force

# The agent stages CLAUDE.md updates to this file instead of editing the live
# one (the harness sensitive-file gate blocks headless writes to CLAUDE.md
# even with acceptEdits + --add-dir); the wrapper applies the staged file
# below. A staged file surviving from an earlier run means that run crashed
# after staging but before applying: its observation may already read
# ACTIONED while CLAUDE.md never got the rule. Never re-apply it (CLAUDE.md
# may have been edited since), but keep it as evidence instead of deleting.
$staged = "$obsDir\claudemd-staged.md"
if (Test-Path $staged) {
    Move-Item $staged "$obsDir\claudemd-staged-orphaned-$(Get-Date -Format yyyyMMdd-HHmmss).md" -Force
    Add-Content $statusFile "ORPHANED-STAGED $(Get-Date -Format o) earlier run staged but never applied; review the orphan file"
}

# cmd-level redirect keeps claude's stderr as plain text in the log file.
# claude -p with no prompt argument reads the prompt from stdin, which avoids
# quoting the multi-line prompt on the command line.
cmd /c "claude -p --permission-mode acceptEdits --add-dir C:\Users\tito1\.claude < $obsDir\scheduled-task-draft.md > $outFile 2>&1"

if ($LASTEXITCODE -ne 0) {
    Add-Content $statusFile "FAILED $(Get-Date -Format o) exit=$LASTEXITCODE"
    exit 1
}
Add-Content $statusFile "OK $(Get-Date -Format o)"

# Apply staged CLAUDE.md updates. Absent staged file means the run applied
# nothing (no open observations, or everything escalated). A clean claude
# exit doesn't guarantee sane staged content, so sanity-check the file
# before letting it overwrite the live CLAUDE.md: right header, and no
# large shrink against the pre-run backup (the review only adds rules).
if (Test-Path $staged) {
    $firstLine = Get-Content $staged -TotalCount 1
    $minSize = [long]((Get-Item "$obsDir\claudemd-backup.md").Length * 0.8)
    if ($firstLine -ne "# User-level Claude Instructions" -or (Get-Item $staged).Length -lt $minSize) {
        Add-Content $statusFile "FAILED-APPLY $(Get-Date -Format o) staged file failed sanity check (header or size)"
        exit 1
    }
    try {
        Copy-Item $staged $claudeMd -Force
        Add-Content $statusFile "APPLIED-CLAUDEMD $(Get-Date -Format o)"
    } catch {
        Add-Content $statusFile "FAILED-APPLY $(Get-Date -Format o) $($_.Exception.Message)"
        exit 1
    }
}

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
