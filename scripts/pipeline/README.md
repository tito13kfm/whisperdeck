# Issue pipeline runner

A deterministic outer loop that drives the existing opencode commands over a
queue of GitHub issues, plus a localhost dashboard for watching it work.

```
/issue <N>  ->  wait for PR  ->  wait for CI  ->  /audit-pr <PR>
                                      |               |
                                      |          APPROVE -> stop, human merges
                                      +--- fail ---> fix cycle ---+
                                                   BLOCK ---------+
```

The loop holds no intelligence. Each box is one `opencode run` against the real
command; this script only sequences them, watches GitHub, reads the verdict file,
and decides whether to iterate or stop.

**It never merges.** An APPROVE ends the issue and prints the PR URL. Merging is
the human's call, which is also the standing rule in `.omo/issue-runner-prompt.md`.

## Files

| File | What it is |
|---|---|
| `run-pipeline.ps1` | The loop. PowerShell 7. |
| `dashboard_server.py` | Read-only JSON API + static server. Stdlib only. |
| `dashboard.html` | Single-file viewer. Vanilla JS, polls the API. |

Runtime artifacts go to `<repo>/.omo/pipeline/`, which is gitignored
(`.omo/*` is ignored except `.omo/runs/`), so a run leaves no repo noise:

```
.omo/pipeline/state.json      current state, rewritten atomically per transition
.omo/pipeline/events.jsonl    append-only timeline
.omo/pipeline/logs/*.log      raw stdout of every opencode run
.omo/pipeline/dryrun/         simulator artifacts (fake PRs, fake verdicts)
```

## Prerequisites

1. **opencode CLI.** `npm install -g opencode-ai`. Verified against 1.18.16.
2. **gh CLI**, authenticated (`gh auth status`).
3. **Python 3** on PATH, for the dashboard only.
4. **`/audit-pr`** present at `.opencode/command/audit-pr.md` in the checkout.
   If it is not there yet, run with `-SkipAudit` — the loop then stops at green
   CI and hands the PR to you.
5. Main checkout on `master`. The runner creates its own worktrees; the pipeline
   invokes opencode with the main checkout as cwd and never checks anything out
   itself.

## Usage

```powershell
# Full loop against the simulator. No tokens, no GitHub writes.
./run-pipeline.ps1 -DryRun -Dashboard

# Simulator, stall path (fix cycle runs, pushes nothing, loop gives up).
./run-pipeline.ps1 -DryRun -DryRunScenario stall -Dashboard

# One real issue, one pass.
./run-pipeline.ps1 -Issues 305 -Once -Dashboard

# A tracking issue: its open children become the queue.
./run-pipeline.ps1 -Tracking 300 -MaxIssues 2 -Dashboard

# Just the viewer, for sessions you started by hand.
./run-pipeline.ps1 -DashboardOnly
```

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `-Issues <n[,n]>` | — | Explicit queue, in order. |
| `-Tracking <n>` | — | Queue = open issues referenced by that tracking issue. |
| `-DashboardOnly` | — | Start the viewer and nothing else. |
| `-MaxIssues` | 3 | Cap on issues per invocation. |
| `-StallLimit` | 3 | Cap on fix cycles per issue. |
| `-Once` | off | Stop after the first issue. |
| `-SkipAudit` | off | Skip `/audit-pr`; stop at green CI. |
| `-DryRun` | off | Replace every opencode run with a simulator. |
| `-DryRunScenario` | `block-then-approve` | `approve`, `block-then-approve`, `stall`. |
| `-Auto` | off | Pass `--auto` to `opencode run`. See the warning below. |
| `-Dashboard` | off | Start the dashboard alongside the loop. |
| `-ServerPort` | 4747 | opencode server port. Reused if already listening. |
| `-DashboardPort` | 4748 | Dashboard port. |
| `-CiPollSeconds` | 60 | CI poll interval. |
| `-CiTimeoutMinutes` | 45 | Give up waiting on pending checks. |
| `-RunTimeoutMinutes` | 180 | Kill a single opencode run that overruns. |
| `-RepoRoot` | `C:\Claude\WhisperDeck` | Main checkout. |

### About `-Auto`

`opencode run --auto` auto-approves every permission prompt for the whole run.
That is what makes a genuinely unattended loop possible, and it also means the
agent can run any tool, including destructive shell commands, without asking.
It is off by default; without it, a run that hits a permission prompt will sit
there until you answer it in the TUI or the web UI. The dashboard flags a session
with a pending permission so you can see when that is what is happening.

## Watching it

Three views, all live at once:

- **Terminal.** Every opencode run's stdout is streamed to the console as it is
  written, and also tee'd to `.omo/pipeline/logs/`.
- **Dashboard** (`-Dashboard`, `http://127.0.0.1:4748`). Pipeline state per
  issue (phase, cycle, stall counter, PR, CI, verdict), the event timeline, the
  session tree with parent/child subagents, a per-session feed of tool calls with
  their inputs and outputs, and a log tail.
- **Native opencode.** The loop starts one `opencode serve` and points every
  run at it with `--attach`, so the same sessions are visible in
  `opencode attach http://127.0.0.1:4747` and in the server's own web UI. That
  is the richest view of a live session; the dashboard's job is the pipeline
  layer around it.

## Capability notes this design rests on

Probed on the work machine against opencode 1.18.16. Recorded here because the
design depends on them and they are the first things to re-check if it breaks.

- **The CLI shim is not an exe.** `(Get-Command opencode).Source` resolves to
  `…\AppData\Roaming\npm\opencode.ps1`, which `Start-Process` cannot launch
  ("cannot find all the information required"). The script launches
  `…\npm\opencode.cmd`, falling back to
  `…\npm\node_modules\opencode-ai\bin\opencode.exe`. The `.cmd` shim spawns the
  real server as a **child** process, so killing the shim's PID alone leaves the
  server listening — teardown kills the child first, then verifies the port is
  free.
- **Session endpoints require a `directory` query parameter.** Without it
  `GET /session` hangs rather than erroring (observed: timeouts at both 5s and
  30s). With `?directory=<urlencoded repo path>` it answers instantly.
- **Live session data comes from the REST API, not from disk.** The CLI's
  storage root is `~\.local\share\opencode` (`storage/`, `tool-output/`,
  `opencode.db`), not `~\.opencode` as first assumed, and it is SQLite-backed.
  Reading the API instead avoids parsing it:
  - `GET /session?directory=…` — id, `parentID` (subagent to parent, which is
    what gives the session tree), title, agent, model, cost, tokens, timestamps,
    and `permission[]` when a prompt is waiting.
  - `GET /session/{id}/message?directory=…` — messages with `parts[]`. A tool
    part is `{type:"tool", tool:"…", callID, state:{status, input, output}}`,
    which is exactly the feed.
  - `/event`, `/global/event`, `/api/session/{id}/event` exist for a push-based
    feed. The dashboard polls instead, because polling survives a dropped
    connection with no reconnect logic.
- **The runner prompt's ban on `opencode session list`/`export`/`stats` during a
  run still holds** — that is about shelling the CLI into a live session's
  directory. Read-only HTTP GETs against our own server are a different thing
  and are what both the loop and the dashboard use.
- **`opencode run` flags in use:** `--attach`, `--agent`, `--session`, `--auto`.
  The session id is not taken from run output; the loop diffs the server's
  session list before and during the run and takes the new one, which does not
  depend on any output format staying stable.

## Known gaps

- `opencode.jsonc` is missing at the repo root on this machine, so the git-bash
  `"shell"` setting `AGENTS.md` documents is not in effect here. Real runs may
  behave differently from the machine where that config exists.
- Tracking mode reads `#<n>` references out of the tracking issue's body to
  decide what to watch and in what order. The runner's own Phase 0 remains
  authoritative about which issue it actually works on, so the two can disagree;
  the state file records what the pipeline queued, not what Phase 0 chose.
- Progress between fix cycles is measured as new commits on the PR. An amended
  or force-pushed commit that does not raise the count reads as a stall.
