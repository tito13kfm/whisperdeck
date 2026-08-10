<#
.SYNOPSIS
  Deterministic outer loop that drives the opencode `/issue` and `/audit-pr`
  commands over one or more GitHub issues, with live state for the dashboard.

.DESCRIPTION
  The loop itself contains no intelligence. Each phase is one `opencode run`
  invocation against the real commands; this script only sequences them, watches
  GitHub for the PR and its checks, reads the audit verdict file, and decides
  whether to iterate, stop on stall, or hand off to the human.

  Merging is never automated. An APPROVE verdict ends the issue and notifies;
  the human merges.

  See README.md in this directory for setup, flags, and the capability notes the
  design depends on.

.EXAMPLE
  ./run-pipeline.ps1 -DryRun -Dashboard
  Full loop against a simulator. No tokens spent, no GitHub writes.

.EXAMPLE
  ./run-pipeline.ps1 -Issues 305 -Once -Dashboard
  One real issue, one pass, dashboard on.

.EXAMPLE
  ./run-pipeline.ps1 -DashboardOnly
  Just the viewer, for watching sessions you started by hand.
#>
[CmdletBinding(DefaultParameterSetName = 'Issues')]
param(
    # Tracking issue: the runner's own Phase 0 picks the next open child each cycle.
    [Parameter(ParameterSetName = 'Tracking', Mandatory)]
    [int]$Tracking,

    # Explicit issue numbers, processed in order.
    [Parameter(ParameterSetName = 'Issues', Mandatory)]
    [int[]]$Issues,

    # Start only the dashboard, against whatever state and sessions already exist.
    [Parameter(ParameterSetName = 'DashboardOnly', Mandatory)]
    [switch]$DashboardOnly,

    # Cap on issues handled per invocation.
    [int]$MaxIssues = 3,

    # Cap on fix cycles per issue before declaring a stall.
    [int]$StallLimit = 3,

    # Stop after the first issue instead of draining the queue.
    [switch]$Once,

    # Skip the audit stage (use until /audit-pr is present in this checkout).
    [switch]$SkipAudit,

    # Replace every opencode run with a scripted simulator.
    [switch]$DryRun,

    [ValidateSet('approve', 'block-then-approve', 'stall')]
    [string]$DryRunScenario = 'block-then-approve',

    # Pass --auto to opencode run: auto-approves every permission prompt.
    # Required for a genuinely unattended loop, and it means the agent can run
    # any tool without asking. Off by default on purpose.
    [switch]$Auto,

    [switch]$Dashboard,

    [int]$ServerPort = 4747,
    [int]$DashboardPort = 4748,

    [int]$CiPollSeconds = 60,
    [int]$CiTimeoutMinutes = 45,

    # Minutes to let a single opencode run go before killing it.
    [int]$RunTimeoutMinutes = 180,

    [string]$RepoRoot = 'C:\Claude\WhisperDeck'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- paths, state

$RepoRoot     = (Resolve-Path $RepoRoot).Path
$PipelineDir  = Join-Path $RepoRoot '.omo\pipeline'
$LogDir       = Join-Path $PipelineDir 'logs'
$DryRunDir    = Join-Path $PipelineDir 'dryrun'
$StatePath    = Join-Path $PipelineDir 'state.json'
$EventsPath   = Join-Path $PipelineDir 'events.jsonl'
$ScriptDir    = Split-Path -Parent $PSCommandPath

foreach ($d in @($PipelineDir, $LogDir, $DryRunDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}

# Captured here because $PSCmdlet only exists in this (advanced) script scope,
# not inside the plain functions below.
$script:ParamSet      = $PSCmdlet.ParameterSetName

$script:ServerProc    = $null
$script:DashboardProc = $null
$script:OpencodeExe   = $null
$script:RepoSlug      = $null

$script:State = [ordered]@{
    startedAt    = (Get-Date).ToString('o')
    updatedAt    = (Get-Date).ToString('o')
    pid          = $PID
    mode         = if ($DryRun) { "dry-run ($DryRunScenario)" } else { 'live' }
    skipAudit    = [bool]$SkipAudit
    auto         = [bool]$Auto
    repoRoot     = $RepoRoot
    serverUrl    = $null
    dashboardUrl = $null
    queue        = @()
    currentIssue = $null
    status       = 'starting'
    note         = $null
    issues       = [ordered]@{}
}

function Save-State {
    $script:State.updatedAt = (Get-Date).ToString('o')
    $tmp = "$StatePath.tmp"
    $script:State | ConvertTo-Json -Depth 8 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Path $tmp -Destination $StatePath -Force
}

function Write-Event {
    param(
        [Parameter(Mandatory)][string]$Type,
        [Parameter(Mandatory)][string]$Message,
        [int]$Issue,
        [hashtable]$Data
    )
    $rec = [ordered]@{
        at      = (Get-Date).ToString('o')
        type    = $Type
        issue   = if ($PSBoundParameters.ContainsKey('Issue')) { $Issue } else { $null }
        message = $Message
        data    = $Data
    }
    ($rec | ConvertTo-Json -Depth 6 -Compress) | Add-Content -Path $EventsPath -Encoding UTF8

    $color = switch ($Type) {
        'error'  { 'Red' }
        'stall'  { 'Yellow' }
        'block'  { 'Yellow' }
        'done'   { 'Green' }
        'phase'  { 'Cyan' }
        default  { 'Gray' }
    }
    $tag = if ($rec.issue) { "#$($rec.issue)" } else { '--' }
    Write-Host ("[{0}] {1,-4} {2}" -f (Get-Date -Format 'HH:mm:ss'), $tag, $Message) -ForegroundColor $color
}

function Get-IssueState {
    param([Parameter(Mandatory)][int]$Issue)
    $k = "$Issue"
    if (-not $script:State.issues.Contains($k)) {
        $script:State.issues[$k] = [ordered]@{
            issue      = $Issue
            status     = 'queued'
            phase      = $null
            cycle      = 0
            stallCount = 0
            pr         = $null
            prUrl      = $null
            branch     = $null
            ci         = $null
            verdict    = $null
            commits    = $null
            sessionId  = $null
            sessions   = @()
            logs       = @()
            startedAt  = $null
            endedAt    = $null
            note       = $null
        }
    }
    $script:State.issues[$k]
}

function Set-Phase {
    param([Parameter(Mandatory)][int]$Issue, [Parameter(Mandatory)][string]$Phase, [string]$Note)
    $st = Get-IssueState $Issue
    $st.phase = $Phase
    if ($PSBoundParameters.ContainsKey('Note')) { $st.note = $Note }
    Save-State
    Write-Event -Type 'phase' -Issue $Issue -Message "phase: $Phase$(if ($Note) { " ($Note)" })"
}

# ------------------------------------------------------------------- preflight

function Resolve-OpencodeExe {
    # `opencode` on PATH resolves to a PowerShell shim (.ps1) on this machine,
    # which Start-Process cannot launch. Prefer the .cmd shim, then the real exe.
    $candidates = @(
        (Join-Path $env:APPDATA 'npm\opencode.cmd'),
        (Join-Path $env:APPDATA 'npm\node_modules\opencode-ai\bin\opencode.exe')
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command opencode -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike '*.ps1') { return $cmd.Source }
    throw 'opencode CLI not found. Install with: npm install -g opencode-ai'
}

function Invoke-Preflight {
    Write-Event -Type 'info' -Message "preflight: repo root $RepoRoot"

    if (-not $DryRun) {
        $script:OpencodeExe = Resolve-OpencodeExe
        Write-Event -Type 'info' -Message "opencode: $script:OpencodeExe"
    }

    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw 'gh CLI not found on PATH.'
    }
    if (-not $DryRun) {
        & gh auth status *>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'gh is not authenticated. Run: gh auth login' }

        $script:RepoSlug = (& gh repo view --json nameWithOwner -q .nameWithOwner 2>$null)
        if ($LASTEXITCODE -ne 0 -or -not $script:RepoSlug) { throw 'gh repo view failed in this directory.' }
        Write-Event -Type 'info' -Message "repo: $script:RepoSlug"
    } else {
        $script:RepoSlug = 'dryrun/whisperdeck'
    }

    $branch = (& git -C $RepoRoot rev-parse --abbrev-ref HEAD).Trim()
    if ($branch -ne 'master') {
        throw "Main checkout is on '$branch', not master. The runner needs it on master; check with another session before switching it back."
    }
    if (-not $DryRun) {
        & git -C $RepoRoot fetch origin --quiet
    }

    if (-not $SkipAudit -and -not $DryRun) {
        $auditCmd = Join-Path $RepoRoot '.opencode\command\audit-pr.md'
        if (-not (Test-Path $auditCmd)) {
            throw "/audit-pr not found at $auditCmd. Sync it from the other machine, or pass -SkipAudit."
        }
    }
}

# ------------------------------------------------- opencode server + dashboard

function Start-OpencodeServer {
    $url = "http://127.0.0.1:$ServerPort"
    try {
        $null = Invoke-RestMethod -Uri "$url/session?directory=$([uri]::EscapeDataString($RepoRoot))" -TimeoutSec 10
        Write-Event -Type 'info' -Message "reusing opencode server already on port $ServerPort"
        $script:State.serverUrl = $url
        Save-State
        return
    } catch { }

    $log = Join-Path $LogDir 'opencode-serve.log'
    $script:ServerProc = Start-Process -FilePath $script:OpencodeExe `
        -ArgumentList @('serve', '--port', "$ServerPort") `
        -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"

    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-RestMethod -Uri "$url/session?directory=$([uri]::EscapeDataString($RepoRoot))" -TimeoutSec 10
            $script:State.serverUrl = $url
            Save-State
            Write-Event -Type 'info' -Message "opencode server up at $url (watch live: opencode attach $url, or open $url in a browser)"
            return
        } catch { Start-Sleep -Seconds 2 }
    }
    throw "opencode server did not answer on $url within 45s. See $log.err"
}

function Stop-OpencodeServer {
    if (-not $script:ServerProc) { return }
    # The .cmd shim spawns the real server as a child; kill the tree.
    try {
        $kids = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($script:ServerProc.Id)" -ErrorAction SilentlyContinue
        foreach ($k in $kids) { Stop-Process -Id $k.ProcessId -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $script:ServerProc.Id -Force -ErrorAction SilentlyContinue
    } catch { }
    Start-Sleep -Seconds 1
    $still = Get-NetTCPConnection -LocalPort $ServerPort -State Listen -ErrorAction SilentlyContinue
    if ($still) {
        Write-Event -Type 'error' -Message "port $ServerPort still listening after teardown; check for stray opencode processes"
    } else {
        Write-Event -Type 'info' -Message "opencode server down, port $ServerPort free"
    }
    $script:ServerProc = $null
}

function Start-Dashboard {
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) { Write-Event -Type 'error' -Message 'python not found; dashboard skipped'; return }
    $server = Join-Path $ScriptDir 'dashboard_server.py'
    if (-not (Test-Path $server)) { Write-Event -Type 'error' -Message "dashboard_server.py missing at $server"; return }

    $dashArgs = @($server, '--port', "$DashboardPort", '--repo-root', $RepoRoot)
    if ($script:State.serverUrl) { $dashArgs += @('--opencode-url', $script:State.serverUrl) }

    $log = Join-Path $LogDir 'dashboard.log'
    $script:DashboardProc = Start-Process -FilePath $py.Source -ArgumentList $dashArgs `
        -WorkingDirectory $ScriptDir -NoNewWindow -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err"

    $script:State.dashboardUrl = "http://127.0.0.1:$DashboardPort"
    Save-State
    Write-Event -Type 'info' -Message "dashboard: http://127.0.0.1:$DashboardPort"
}

function Stop-Dashboard {
    if (-not $script:DashboardProc) { return }
    Stop-Process -Id $script:DashboardProc.Id -Force -ErrorAction SilentlyContinue
    $script:DashboardProc = $null
    Write-Event -Type 'info' -Message 'dashboard stopped'
}

# --------------------------------------------------------------- opencode runs

function Get-SessionIds {
    if (-not $script:State.serverUrl) { return @() }
    try {
        $u = "$($script:State.serverUrl)/session?directory=$([uri]::EscapeDataString($RepoRoot))"
        $sessions = Invoke-RestMethod -Uri $u -TimeoutSec 15
        return @($sessions | ForEach-Object { $_.id })
    } catch { return @() }
}

function Invoke-OpencodeRun {
    <#
      Runs one opencode prompt to completion. Streams its stdout to the console
      and to $LogPath at the same time, and while it runs, polls the server's
      session list so the dashboard can point at the live session.

      Read-only HTTP GETs against our own server are used for that. The runner
      prompt's ban on `opencode session list`/`export`/`stats` is about shelling
      the CLI into a live session's directory; it does not apply here.
    #>
    param(
        [Parameter(Mandatory)][string]$Prompt,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][int]$Issue,
        [string]$Agent,
        [string]$ContinueSession
    )

    $st = Get-IssueState $Issue
    $st.logs = @($st.logs + (Split-Path -Leaf $LogPath) | Select-Object -Unique)
    Save-State

    if ($DryRun) { return Invoke-SimRun -Prompt $Prompt -LogPath $LogPath -Issue $Issue }

    $before = Get-SessionIds

    $runArgs = @('run')
    if ($script:State.serverUrl) { $runArgs += @('--attach', $script:State.serverUrl) }
    if ($Agent)           { $runArgs += @('--agent', $Agent) }
    if ($ContinueSession) { $runArgs += @('--session', $ContinueSession) }
    if ($Auto)            { $runArgs += '--auto' }
    $runArgs += $Prompt

    $errPath = "$LogPath.err"
    Set-Content -Path $LogPath -Value '' -Encoding UTF8
    $proc = Start-Process -FilePath $script:OpencodeExe -ArgumentList $runArgs `
        -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
        -RedirectStandardOutput $LogPath -RedirectStandardError $errPath

    $offset = 0
    $deadline = (Get-Date).AddMinutes($RunTimeoutMinutes)
    $nextSessionPoll = (Get-Date)

    while (-not $proc.HasExited) {
        $offset = Write-LogTail -Path $LogPath -Offset $offset

        if ((Get-Date) -ge $nextSessionPoll -and -not $st.sessionId) {
            $new = @(Get-SessionIds | Where-Object { $before -notcontains $_ })
            if ($new.Count -gt 0) {
                $st.sessionId = $new[0]
                $st.sessions = @($st.sessions + $new | Select-Object -Unique)
                Save-State
                Write-Event -Type 'info' -Issue $Issue -Message "session $($st.sessionId)"
            }
            $nextSessionPoll = (Get-Date).AddSeconds(5)
        }

        if ((Get-Date) -gt $deadline) {
            Write-Event -Type 'error' -Issue $Issue -Message "run exceeded $RunTimeoutMinutes min; killing"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            break
        }
        Start-Sleep -Milliseconds 700
    }
    $null = Write-LogTail -Path $LogPath -Offset $offset

    $out = if (Test-Path $LogPath) { Get-Content -Path $LogPath -Raw } else { '' }
    if ((Test-Path $errPath) -and (Get-Item $errPath).Length -gt 0) {
        $out += "`n" + (Get-Content -Path $errPath -Raw)
    }
    [pscustomobject]@{ ExitCode = $proc.ExitCode; Output = $out }
}

function Write-LogTail {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][int]$Offset)
    if (-not (Test-Path $Path)) { return $Offset }
    $lines = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    if ($lines.Count -gt $Offset) {
        $lines[$Offset..($lines.Count - 1)] | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        return $lines.Count
    }
    $Offset
}

# ------------------------------------------------------------------- PR and CI

function Get-PrForIssue {
    param([Parameter(Mandatory)][int]$Issue, [string]$RunOutput)

    if ($DryRun) { return Get-SimPr -Issue $Issue }

    if ($RunOutput) {
        $m = [regex]::Matches($RunOutput, 'https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)')
        if ($m.Count -gt 0) {
            $num = [int]$m[$m.Count - 1].Groups[1].Value
            return Get-PrDetail -Number $num
        }
    }

    $json = & gh pr list --state open --limit 50 --json number,url,headRefName,body,title 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    $prs = $json | ConvertFrom-Json
    $hit = $prs | Where-Object {
        ($_.body -and $_.body -match "(?i)(closes|fixes|resolves)\s+#$Issue\b") -or
        ($_.title -and $_.title -match "#$Issue\b") -or
        ($_.headRefName -match "(^|[^0-9])$Issue([^0-9]|$)")
    } | Select-Object -First 1
    if (-not $hit) { return $null }
    Get-PrDetail -Number $hit.number
}

function Get-PrDetail {
    param([Parameter(Mandatory)][int]$Number)
    if ($DryRun) { return Get-SimPr -Number $Number }
    $json = & gh pr view $Number --json number,url,headRefName,state,commits 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    $pr = $json | ConvertFrom-Json
    [pscustomobject]@{
        Number  = $pr.number
        Url     = $pr.url
        Branch  = $pr.headRefName
        State   = $pr.state
        Commits = @($pr.commits).Count
    }
}

function Get-CiStatus {
    param([Parameter(Mandatory)][int]$Number, [int]$Cycle = 1)
    if ($DryRun) { return Get-SimCi -Cycle $Cycle }

    $json = & gh pr view $Number --json statusCheckRollup 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $json) {
        return [pscustomobject]@{ State = 'unknown'; Failing = @() }
    }
    $checks = @(($json | ConvertFrom-Json).statusCheckRollup)
    if ($checks.Count -eq 0) { return [pscustomobject]@{ State = 'none'; Failing = @() } }

    $failing = @()
    $pending = $false
    foreach ($c in $checks) {
        $status     = if ($c.PSObject.Properties['status'])     { $c.status }     else { $null }
        $conclusion = if ($c.PSObject.Properties['conclusion']) { $c.conclusion } else { $null }
        $legacy     = if ($c.PSObject.Properties['state'])      { $c.state }      else { $null }
        $name       = if ($c.PSObject.Properties['name'] -and $c.name) { $c.name } else { $c.context }

        if ($status -and $status -ne 'COMPLETED') { $pending = $true; continue }
        if ($legacy -eq 'PENDING' -or $legacy -eq 'EXPECTED') { $pending = $true; continue }
        if ($conclusion -in @('FAILURE', 'TIMED_OUT', 'CANCELLED', 'ACTION_REQUIRED', 'STARTUP_FAILURE') -or
            $legacy -in @('FAILURE', 'ERROR')) {
            $failing += $name
        }
    }
    $state = if ($pending) { 'pending' } elseif ($failing.Count -gt 0) { 'fail' } else { 'pass' }
    [pscustomobject]@{ State = $state; Failing = $failing }
}

function Wait-CiChecks {
    param([Parameter(Mandatory)][int]$Issue, [Parameter(Mandatory)][int]$Number, [int]$Cycle = 1)
    $st = Get-IssueState $Issue
    $deadline = (Get-Date).AddMinutes($CiTimeoutMinutes)
    while ($true) {
        $ci = Get-CiStatus -Number $Number -Cycle $Cycle
        $st.ci = "$($ci.State)$(if ($ci.Failing.Count) { ': ' + ($ci.Failing -join ', ') })"
        Save-State
        if ($ci.State -ne 'pending') {
            Write-Event -Type 'info' -Issue $Issue -Message "CI $($st.ci)"
            return $ci
        }
        if ((Get-Date) -gt $deadline) {
            Write-Event -Type 'error' -Issue $Issue -Message "CI still pending after $CiTimeoutMinutes min"
            return [pscustomobject]@{ State = 'timeout'; Failing = @() }
        }
        Write-Event -Type 'info' -Issue $Issue -Message "CI pending, re-checking in ${CiPollSeconds}s"
        Start-Sleep -Seconds $CiPollSeconds
    }
}

# ---------------------------------------------------------------------- verdict

function Get-AuditVerdict {
    <#
      /audit-pr appends one block per re-audit to
      .omo/runs/issue-<N>/<branch>/audit-pr-verdict-<model-slug>.md, so the LAST
      `VERDICT:` line in the file is the current one. Multiple reviewer models
      can each have a file; any BLOCK blocks.
    #>
    param([Parameter(Mandatory)][int]$Issue, [datetime]$Since)

    $root = Join-Path $RepoRoot ".omo\runs\issue-$Issue"
    if ($DryRun) { $root = Join-Path $DryRunDir "runs\issue-$Issue" }
    if (-not (Test-Path $root)) {
        return [pscustomobject]@{ Verdict = 'missing'; Files = @(); Detail = "no run dir at $root" }
    }

    $files = @(Get-ChildItem -Path $root -Recurse -Filter 'audit-pr-verdict-*.md' -ErrorAction SilentlyContinue)
    if ($PSBoundParameters.ContainsKey('Since')) {
        $files = @($files | Where-Object { $_.LastWriteTime -ge $Since.AddMinutes(-1) })
    }
    if ($files.Count -eq 0) {
        return [pscustomobject]@{ Verdict = 'missing'; Files = @(); Detail = 'no verdict file written' }
    }

    $verdicts = @()
    foreach ($f in $files) {
        $last = @(Select-String -Path $f.FullName -Pattern '^\s*VERDICT:\s*(APPROVE|BLOCK)' ) | Select-Object -Last 1
        if ($last) {
            $verdicts += [pscustomobject]@{
                File    = $f.FullName
                Verdict = ($last.Matches[0].Groups[1].Value).ToUpper()
            }
        }
    }
    if ($verdicts.Count -eq 0) {
        return [pscustomobject]@{ Verdict = 'unparsed'; Files = @($files.FullName); Detail = 'no VERDICT: line found' }
    }
    $overall = if ($verdicts.Verdict -contains 'BLOCK') { 'BLOCK' } else { 'APPROVE' }
    [pscustomobject]@{
        Verdict = $overall
        Files   = @($verdicts.File)
        Detail  = ($verdicts | ForEach-Object { "$(Split-Path -Leaf $_.File)=$($_.Verdict)" }) -join ', '
    }
}

# ------------------------------------------------------------- dry-run simulator

function Invoke-SimRun {
    param([string]$Prompt, [string]$LogPath, [int]$Issue)
    $st = Get-IssueState $Issue
    $isAudit = $Prompt -like '*/audit-pr*'
    $isFix   = $Prompt -notlike '/*'

    $lines = if ($isAudit) {
        @("audit-pr: checking out PR fixture", "audit-pr: reading diff (12 files)",
          "audit-pr: tool: read scripts/pipeline/run-pipeline.ps1",
          "audit-pr: tool: bash python -m pytest -q", "audit-pr: writing verdict file")
    } elseif ($isFix) {
        @("sisyphus: re-reading blocking findings", "sisyphus: tool: edit static/rack.js",
          "sisyphus: tool: bash npx vitest run", "sisyphus: commit + push")
    } else {
        @("sisyphus: Phase 0 resolve target", "sisyphus: Phase 1 investigate",
          "sisyphus: tool: grep speaker_confidence", "sisyphus: Phase 2 fix",
          "sisyphus: Phase 3 test", "sisyphus: Phase 3.5 self-audit",
          "sisyphus: Phase 4 opening PR")
    }

    Set-Content -Path $LogPath -Value '' -Encoding UTF8
    foreach ($l in $lines) {
        Add-Content -Path $LogPath -Value $l -Encoding UTF8
        Write-Host "    $l" -ForegroundColor DarkGray
        Start-Sleep -Milliseconds 900
    }

    if (-not $st.sessionId) {
        $st.sessionId = "ses_dryrun_$Issue"
        $st.sessions = @($st.sessionId)
        Save-State
    }

    $pr = Get-SimPr -Issue $Issue -Create
    if (-not $isAudit -and -not $isFix) {
        Add-Content -Path $LogPath -Value "PR opened: $($pr.Url)" -Encoding UTF8
    }
    if ($isFix) { Set-SimPrCommits -Issue $Issue }
    if ($isAudit) { Write-SimVerdict -Issue $Issue -Cycle $st.cycle }

    [pscustomobject]@{ ExitCode = 0; Output = (Get-Content -Path $LogPath -Raw) }
}

function Get-SimPrStore {
    $p = Join-Path $DryRunDir 'prs.json'
    if (Test-Path $p) { return (Get-Content $p -Raw | ConvertFrom-Json) }
    [pscustomobject]@{}
}

function Set-SimPrStore {
    param($Store)
    $Store | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $DryRunDir 'prs.json') -Encoding UTF8
}

function Get-SimPr {
    param([int]$Issue, [int]$Number, [switch]$Create)
    $store = Get-SimPrStore
    if ($Number) {
        foreach ($p in $store.PSObject.Properties) {
            if ($p.Value.Number -eq $Number) { return $p.Value }
        }
        return $null
    }
    $key = "issue-$Issue"
    if ($store.PSObject.Properties[$key]) { return $store.$key }
    if (-not $Create) { return $null }
    $num = 9000 + $Issue
    $pr = [pscustomobject]@{
        Number  = $num
        Url     = "https://github.com/dryrun/whisperdeck/pull/$num"
        Branch  = "issue-$Issue-sim"
        State   = 'OPEN'
        Commits = 3
    }
    $store | Add-Member -NotePropertyName $key -NotePropertyValue $pr -Force
    Set-SimPrStore $store
    $pr
}

function Set-SimPrCommits {
    param([int]$Issue)
    $store = Get-SimPrStore
    $key = "issue-$Issue"
    if (-not $store.PSObject.Properties[$key]) { return }
    # The stall scenario is exactly "fix cycle ran, no new commits landed".
    if ($DryRunScenario -ne 'stall') { $store.$key.Commits = $store.$key.Commits + 2 }
    Set-SimPrStore $store
}

function Get-SimCi {
    param([int]$Cycle)
    [pscustomobject]@{ State = 'pass'; Failing = @() }
}

function Write-SimVerdict {
    param([int]$Issue, [int]$Cycle)
    $dir = Join-Path $DryRunDir "runs\issue-$Issue\issue-$Issue-sim"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $file = Join-Path $dir 'audit-pr-verdict-sim-model.md'

    $verdict = switch ($DryRunScenario) {
        'approve' { 'APPROVE' }
        'stall'   { 'BLOCK' }
        default   { if ($Cycle -ge 2) { 'APPROVE' } else { 'BLOCK' } }
    }
    $block = @"

---
Re-audit timestamp: $(Get-Date -Format 'o') (cycle $Cycle)

## PR Audit: dry run (reviewer: sim-model)

Reviewer slug: sim-model

### Blocking
$(if ($verdict -eq 'BLOCK') { "- Simulated blocking finding for cycle $Cycle." } else { "- none" })

### Summary
Simulated audit output for pipeline testing.

VERDICT: $verdict
"@
    Add-Content -Path $file -Value $block -Encoding UTF8
}

# ------------------------------------------------------------- the issue cycle

function Invoke-IssueCycle {
    param([Parameter(Mandatory)][int]$Issue)

    $st = Get-IssueState $Issue
    $st.status = 'running'
    $st.startedAt = (Get-Date).ToString('o')
    $script:State.currentIssue = $Issue
    Save-State
    Write-Event -Type 'start' -Issue $Issue -Message "starting issue #$Issue"

    # --- cycle 1: the runner itself -------------------------------------------
    $st.cycle = 1
    Set-Phase -Issue $Issue -Phase 'issue-run' -Note '/issue'
    $log = Join-Path $LogDir "issue-$Issue-cycle1.log"
    $run = Invoke-OpencodeRun -Prompt "/issue $Issue" -LogPath $log -Issue $Issue

    if ($run.ExitCode -ne 0) {
        Write-Event -Type 'error' -Issue $Issue -Message "/issue exited $($run.ExitCode); see $log"
    }

    $pr = Get-PrForIssue -Issue $Issue -RunOutput $run.Output
    if (-not $pr) {
        $st.status = 'failed'
        $st.phase = 'no-pr'
        $st.note = 'runner finished without an open PR for this issue'
        $st.endedAt = (Get-Date).ToString('o')
        Save-State
        Write-Event -Type 'error' -Issue $Issue -Message 'no PR found after /issue; stopping this issue'
        return
    }
    Set-PrOnState -Issue $Issue -Pr $pr
    Write-Event -Type 'info' -Issue $Issue -Message "PR #$($pr.Number) $($pr.Url) [$($pr.Branch)]"

    # --- fix loop -------------------------------------------------------------
    while ($true) {
        Set-Phase -Issue $Issue -Phase 'ci-wait'
        $ci = Wait-CiChecks -Issue $Issue -Number $pr.Number -Cycle $st.cycle

        $needsFix = $false
        $reason = $null

        if ($ci.State -in @('fail', 'timeout')) {
            $needsFix = $true
            $reason = "CI is $($ci.State)$(if ($ci.Failing.Count) { ': failing checks: ' + ($ci.Failing -join ', ') })"
        } elseif ($SkipAudit) {
            Set-Phase -Issue $Issue -Phase 'audit-skipped'
            $st.verdict = 'skipped'
            Save-State
            Write-Event -Type 'done' -Issue $Issue -Message "CI green, audit skipped. Review and merge PR #$($pr.Number) yourself."
            Complete-Issue -Issue $Issue -Status 'awaiting-human' -Note 'CI green, audit skipped'
            return
        } else {
            Set-Phase -Issue $Issue -Phase 'audit' -Note "/audit-pr $($pr.Number)"
            $auditStart = Get-Date
            $alog = Join-Path $LogDir "issue-$Issue-audit$($st.cycle).log"
            $arun = Invoke-OpencodeRun -Prompt "/audit-pr $($pr.Number)" -LogPath $alog -Issue $Issue
            if ($arun.ExitCode -ne 0) {
                Write-Event -Type 'error' -Issue $Issue -Message "/audit-pr exited $($arun.ExitCode); see $alog"
            }

            $v = Get-AuditVerdict -Issue $Issue -Since $auditStart
            $st.verdict = "$($v.Verdict)$(if ($v.Detail) { " ($($v.Detail))" })"
            Save-State

            if ($v.Verdict -eq 'APPROVE') {
                Write-Event -Type 'done' -Issue $Issue -Message "APPROVE. PR #$($pr.Number) is yours to merge: $($pr.Url)"
                Complete-Issue -Issue $Issue -Status 'approved' -Note "audit APPROVE ($($v.Detail))"
                return
            }
            if ($v.Verdict -in @('missing', 'unparsed')) {
                Write-Event -Type 'error' -Issue $Issue -Message "audit produced no usable verdict ($($v.Detail)); stopping this issue"
                Complete-Issue -Issue $Issue -Status 'failed' -Note "audit verdict $($v.Verdict): $($v.Detail)"
                return
            }
            $needsFix = $true
            $reason = "audit BLOCK. Blocking findings are in: $($v.Files -join '; ')"
            Write-Event -Type 'block' -Issue $Issue -Message 'audit BLOCK'
        }

        # --- stall guards -----------------------------------------------------
        if ($st.stallCount -ge $StallLimit) {
            Write-Event -Type 'stall' -Issue $Issue -Message "stall limit $StallLimit reached; stopping this issue"
            Complete-Issue -Issue $Issue -Status 'stalled' -Note "hit stall limit after $($st.stallCount) fix cycles; last state: $reason"
            return
        }

        $commitsBefore = $pr.Commits
        $st.stallCount = $st.stallCount + 1
        $st.cycle = $st.cycle + 1
        Save-State

        Set-Phase -Issue $Issue -Phase 'fix' -Note "cycle $($st.cycle)"
        $flog = Join-Path $LogDir "issue-$Issue-fix$($st.cycle).log"
        $prompt = @"
You are continuing work on issue #$Issue. The PR is #$($pr.Number) ($($pr.Url)), branch ``$($pr.Branch)``.

Status: $reason

Fix it on that branch, in the existing worktree for this issue. Re-run the tests
you touched, update the run artifacts under .omo/runs/issue-$Issue/ (self-audit.md
in particular), then commit and push to the same PR. Do not open a new PR and do
not merge.
"@
        $frun = Invoke-OpencodeRun -Prompt $prompt -LogPath $flog -Issue $Issue `
            -Agent 'sisyphus' -ContinueSession $st.sessionId
        if ($frun.ExitCode -ne 0) {
            Write-Event -Type 'error' -Issue $Issue -Message "fix cycle exited $($frun.ExitCode); see $flog"
        }

        # progress = new commits on the PR
        $pr = Get-PrDetail -Number $pr.Number
        if (-not $pr) {
            Write-Event -Type 'error' -Issue $Issue -Message 'PR disappeared after fix cycle'
            Complete-Issue -Issue $Issue -Status 'failed' -Note 'PR no longer readable'
            return
        }
        Set-PrOnState -Issue $Issue -Pr $pr

        if ($pr.Commits -le $commitsBefore) {
            Write-Event -Type 'stall' -Issue $Issue -Message "fix cycle added no commits ($($pr.Commits) total); stopping this issue"
            Complete-Issue -Issue $Issue -Status 'stalled' -Note "no new commits in fix cycle $($st.cycle); last state: $reason"
            return
        }
        Write-Event -Type 'info' -Issue $Issue -Message "fix cycle pushed $($pr.Commits - $commitsBefore) commit(s)"
    }
}

function Set-PrOnState {
    param([Parameter(Mandatory)][int]$Issue, [Parameter(Mandatory)]$Pr)
    $st = Get-IssueState $Issue
    $st.pr = $Pr.Number
    $st.prUrl = $Pr.Url
    $st.branch = $Pr.Branch
    $st.commits = $Pr.Commits
    Save-State
}

function Complete-Issue {
    param([Parameter(Mandatory)][int]$Issue, [Parameter(Mandatory)][string]$Status, [string]$Note)
    $st = Get-IssueState $Issue
    $st.status = $Status
    $st.note = $Note
    $st.endedAt = (Get-Date).ToString('o')
    $script:State.currentIssue = $null
    Save-State
}

# ------------------------------------------------------------------------ main

function Resolve-Queue {
    if ($script:ParamSet -eq 'Issues') { return @($Issues) }

    # Tracking mode: list the issues this tracking issue references, keep the
    # open ones. This is only to know what to watch and in what order. The
    # runner's own Phase 0 stays authoritative about which one it works on.
    if ($DryRun) { return @($Tracking) }

    $body = & gh issue view $Tracking --json body -q .body 2>$null
    if ($LASTEXITCODE -ne 0) { throw "gh issue view $Tracking failed." }
    $nums = [regex]::Matches($body, '#(\d+)') | ForEach-Object { [int]$_.Groups[1].Value } | Select-Object -Unique
    $open = @()
    foreach ($n in $nums) {
        if ($n -eq $Tracking) { continue }
        $state = & gh issue view $n --json state -q .state 2>$null
        if ($LASTEXITCODE -eq 0 -and $state -eq 'OPEN') { $open += $n }
    }
    Write-Event -Type 'info' -Message "tracking #$Tracking -> open children: $($open -join ', ')"
    @($open)
}

try {
    if ($DashboardOnly) {
        Invoke-Preflight
        if (-not $DryRun) { Start-OpencodeServer }
        Start-Dashboard
        $script:State.status = 'dashboard-only'
        Save-State
        Write-Host ''
        Write-Host "Dashboard on http://127.0.0.1:$DashboardPort. Ctrl+C to stop." -ForegroundColor Green
        while ($true) { Start-Sleep -Seconds 5 }
    }

    if ($Auto) {
        Write-Host ''
        Write-Host 'WARNING: -Auto passes --auto to opencode run. Every permission prompt is auto-approved for the whole loop, which lets the agent run any tool (including destructive shell commands) without asking. Ctrl+C now if that is not what you want.' -ForegroundColor Yellow
        Write-Host ''
        Start-Sleep -Seconds 5
    }

    Invoke-Preflight
    if (-not $DryRun) { Start-OpencodeServer }
    if ($Dashboard) { Start-Dashboard }

    $queue = @(Resolve-Queue)
    if ($queue.Count -eq 0) { Write-Event -Type 'info' -Message 'nothing to do'; return }
    if ($Once) { $queue = @($queue[0]) }
    elseif ($queue.Count -gt $MaxIssues) { $queue = @($queue[0..($MaxIssues - 1)]) }

    $script:State.queue = $queue
    $script:State.status = 'running'
    Save-State
    Write-Event -Type 'info' -Message "queue: $($queue -join ', ')"

    foreach ($n in $queue) {
        Invoke-IssueCycle -Issue $n
        if ($Once) { break }
    }

    $script:State.status = 'finished'
    Save-State

    Write-Host ''
    Write-Host 'Summary' -ForegroundColor Cyan
    foreach ($k in $script:State.issues.Keys) {
        $st = $script:State.issues[$k]
        $line = "  #{0,-5} {1,-15} PR {2,-6} {3}" -f $st.issue, $st.status, ($st.pr ?? '-'), ($st.note ?? '')
        Write-Host $line
    }
    $approved = @($script:State.issues.Values | Where-Object { $_.status -eq 'approved' })
    if ($approved.Count -gt 0) {
        Write-Host ''
        Write-Host 'Ready for you to merge:' -ForegroundColor Green
        foreach ($a in $approved) { Write-Host "  #$($a.issue) -> $($a.prUrl)" -ForegroundColor Green }
    }
}
catch {
    $script:State.status = 'error'
    $script:State.note = $_.Exception.Message
    Save-State
    Write-Event -Type 'error' -Message $_.Exception.Message
    throw
}
finally {
    Stop-Dashboard
    Stop-OpencodeServer
    if ($script:State.status -eq 'running') { $script:State.status = 'interrupted' }
    Save-State
}
