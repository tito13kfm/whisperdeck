# E2E Test Skill for WhisperDeck

## Goal

A repeatable, agent-driven end-to-end test covering every user-facing feature
of WhisperDeck, runnable by a weak model (Haiku) via Playwright MCP browser
tools. Doubles as the source material for README.md's feature list.

## Non-goals

- Not a coded/CI-gated Playwright test suite (see "Determinism model" below
  for why hybrid, not pure-agent, was chosen instead).
- Not testing cloud providers (Groq/OpenAI/etc.) — local/keyless backends
  only, so the test costs nothing and doesn't flake on rate limits.
- Not exhaustively fuzzing edge cases — happy-path coverage of every
  feature, not adversarial testing.

## Architecture

### Process isolation

`app.py` reads `WHISPERDECK_DATA_DIR` (falls back to `data/`) and `PORT`
(falls back to 9781) from env vars. The skill exploits this instead of
reusing the user's running instance:

1. Skill spawns `python app.py` itself with `WHISPERDECK_DATA_DIR=<fresh
   temp dir>` and `PORT=9782`.
2. Polls `GET /api/health` until the server is up.
3. Runs the full scenario checklist against `localhost:9782`.
4. Teardown: kill the spawned process, delete the temp data dir.

This means test runs never touch the user's real `data/` directory,
database, or uploaded files — repeatable runs, no accumulating cruft.

### Browser setup

Chromium launched via Playwright MCP with:
```
--use-fake-device-for-media-stream
--use-file-for-fake-audio-capture=<fixture-audio-path>
```
so the live-capture (mic) flow can be driven headlessly, not just file
upload.

### Backend configuration (local/keyless only)

- **STT**: Moonshine (default, zero-config, already in requirements.txt).
- **Diarization**: whatever's actually installed — skill checks
  `/api/providers` / `/api/health` at setup and records whether pyannote
  is present or it'll fall back to the heuristic alternator. Either way
  the diarization *flow* (UI, speaker rename, retag) is exercised; only
  the *quality* of speaker separation depends on what's installed.
- **LLM (summarize / correct / context)**: local Lemonade server at
  `http://localhost:13305/v1`, model `gpt-oss-20b-mxfp4-GGUF`, configured
  as WhisperDeck's `local` provider (`services/correction.py:32` reads
  `provider_config.api_url`, default `http://localhost:11434/v1` —
  override to Lemonade's port in the test's provider config step).
  Skill verifies `GET http://localhost:13305/v1/models` is reachable
  before running LLM-dependent steps; if not, those steps are marked
  SKIPPED rather than failing the whole run.

### Fixture audio

User-provided multi-speaker file (~2-3 min, 2-3 distinct voices) at
`tests/fixtures/e2e_multispeaker.<ext>`. Existing `test.mp4` (3 sec) stays
as-is for the pytest smoke suite; it's too short for diarization/speaker
features. Voice-bank enrollment reuses per-speaker *segments* of this same
recording (via the transcript's `enroll-speaker` endpoint) rather than
requiring separate clip files.

## Determinism model (hybrid, not pure agent-driven)

Per advisor review: a design that leans on Haiku to manage a long async
session (LLM jobs run for minutes) and *judge* fuzzy outcomes live is
fragile. Split responsibilities:

- **Scripted/deterministic**: navigation, uploads, clicks, "poll until job
  status == complete/failed" loops — the skill gives Haiku exact Playwright
  MCP tool-call sequences and exact wait/poll conditions to execute
  verbatim. No judgment required.
- **Agent judgment reserved for**: reading a final screen and confirming
  expected UI elements are present (e.g. "does the transcript panel show
  N segments with speaker labels" — checked via DOM text/selector
  presence, not visual vibes).
- **Fuzzy quality claims downgraded to hard checks.** Steps like
  "correction pass" or "context refinement" do NOT ask the agent to judge
  whether output *improved*. They assert:
  - job status reaches `complete` (not `failed`/`error`)
  - output field is non-empty
  - output text differs from the pre-correction input
  Actual quality judgment is left for a human reading the final report,
  not baked into pass/fail.

## Scenario coverage

Each scenario in the skill file has: numbered steps, exact expected
outcome (selector/text/status to check), and behavior on failure (log and
continue, don't abort the run). Scenarios, mapped to routes:

1. Auth — register / login / logout / session persists across reload
2. Settings — view, update, verify persisted after reload
3. Hotwords — add, list, delete, dedup behavior
4. Providers panel — switch provider, save config (incl. Lemonade local
   LLM config), verify model list loads
5. Upload transcribe (file) — poll job, verify transcript + segments render
6. Live capture transcribe (fake mic device) — same verification
7. Transcript list / detail / rename / delete
8. Cancel + resume a running job; retry failed chunks
9. Retranscribe with a different provider
10. Diarization — standalone `/api/diarize` trigger, verify speaker labels
    appear; rediarize
11. Speaker rename + segment retag, verify propagation into transcript view
12. Voice bank — enroll speaker from segment, list voices, identify on a
    new transcript, delete voice/clip
13. Summarize (Lemonade) — job completes, summary panel populates
14. Correction pass (Lemonade) — job completes, output differs from input
15. Context refinement — add context doc, re-run, job completes
16. Jobs panel — list, cancel, rerun

## Failure handling & reporting

Each step emits a structured line: `PASS | FAIL | SKIPPED(reason)`. The
skill continues past individual step failures rather than aborting, and
ends with a summary table. This report is also the intended seed for
README.md's feature list — each scenario's one-line description becomes a
feature bullet.

## Deliverable

`.claude/skills/e2e-test-app/SKILL.md` — invocable via `/e2e-test-app` or
the Skill tool, self-contained (setup, teardown, all 16 scenarios,
reporting format).

## Open dependency

User to provide `tests/fixtures/e2e_multispeaker.<ext>` before the skill
can be fully exercised; skill can be written now and scenarios 10-12
marked blocked until the fixture lands.
