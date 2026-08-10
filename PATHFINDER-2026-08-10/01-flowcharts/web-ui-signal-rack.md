# Feature: web-ui-signal-rack

## Sources consulted
- `static/rack.js`: full-file grep for top-level function/const/class/let definitions (unbounded, no 150-match cap this time), plus targeted reads at 1-142, 243-330, 425-474, 858-967, 896-943, 1561-1630, 2271-2358, 3374-3423, 3610-3622, 3784-3812, 6515-6574
- `static/index.html` full file (157 lines)
- `static/sw.js` full file (80 lines)
- `app.py`: grep of all @app.get/post decorators (full list), reads at 3613-3690

## Phase 0 blind spot corrected: definitions past line 5767
Phase 0's grep capped at 150 matches, missing 5767-6574 entirely. That range contains 4 sections never seen:
- **Voice-roster modals** (~5766-5930): openEnrollModal(5767), openAddClipModal(5820), openIdentifyModal(5859), renderThresholds(5888), runIdentify(5901)
- **Files page** (5931-6026): fmtBytes(5931), renderFilesPage(5938), deleteSelectedFiles(6007)
- **Settings/"Service panel" page** (6027-6526, largest missed block ~500 lines): JACK_DEFS(6027), jackRow(6037), setJackLed(6056), CLEANUP_FIELDS(6080), cleanupFieldRow(6101), loadSettingsPage(6122, biggest function in file), syncFaceplate(6481), renderHotwordRows(6493), addHotword(6515)
- **Boot/init block** (6528-6574): DOMContentLoaded wiring rail nav, auth form, modal/Escape handling, file-input change, video-dock buttons, service-worker registration, checkAuth() call, plus a window test-hook block (6572-6574) exposing navigate/S/syncTranscribe/renderDetail/curProv/logout/api/loadCostsPage/_jobFingerprint/startJob for Playwright.

Phase 0's inventory was missing three whole product surfaces (Files page, Settings/service-panel incl. patch-bay jack rows and hotword list, voice-roster enroll/add-clip/identify modals) plus the entire bootstrap sequence.

## Page navigation happy path
`index.html:155` loads `<script defer src="/static/rack.min.js">`. `app.py:3613 GET /` serves index.html. DOMContentLoaded (rack.js:6529) wires rail buttons, calls `checkAuth()` (951) -> raw fetch('/api/bootstrap') (app.py:757), caches as bootData, sets csrfToken, calls `showApp()` (858) -> `navigate('dashboard')`.

`navigate(page,data)` (446) validates against PAGES (425), sets S.page, toggles .page-*/.rail-btn visibility, dispatches to a per-page loader from the `loaders` map (457-471). Each loader does its own api() fetch(es), re-renders its `#page-<name>` container's innerHTML. No URL hash/routing — pure JS state, not History API.

## Job/queue polling sub-flow
- `startBackgroundJobPoll()` (932), called once from showApp(), runs forever while logged in: clears existing timer, resets bgJobStatusSeen, calls `pollBackgroundJobs()` (900) -> `getJobs()` (874), self-schedules via setTimeout(...,8000) regardless of success/failure (fetch failures silently swallowed, "retry next tick").
- `getJobs(opts)` (874): shared 15s TTL cache (_JOB_CACHE_TTL=15000) with in-flight dedup, wraps `api('/api/jobs?limit=50')`. Every consumer (background poll, loadQueue, refreshQueueBadge) reads this one cache; `{force:true}` bypasses it for a one-shot fresh read.
- `jobStatusView(j)` (3380) maps job status to LED-bargraph color/lit-count/nixie text; `jobActions(j)` (3395) renders action buttons gated by j.kind/j.status.
- `updateQueueBadge(active)` (3610) sets nav rail badge text; `refreshQueueBadge(force)` (3614) best-effort wrapper, swallows errors.
- Terminal-failure toasting: `pollBackgroundJobs` tracks bgJobStatusSeen (job id -> last-seen status) across ticks. A job toasts only when status==='failed' && will_retry===false AND not already marked 'terminalFailed' on a prior tick, AND not the very first tick after login (bgJobPollFirstTick guard so pre-existing failures don't all toast at once).

**Detail-page poll and fingerprint** (3784-3811): `scheduleDetailPoll()` only arms if at least one LLM job on the open transcript is "active" per `llmJobActive()` (correction/summary/voice_match/format_markdown/format_email/format_coding_prompt/classify_intent/tagging/voice_dump). Snapshots `_jobFingerprint(t)` — maps each of 9 job slots to `status:done-count`, joined with `|` — before a 2500ms setTimeout. On fire, re-fetches /api/transcripts/:id, calls `updateDetailJobStatus()` re-render **only if the fresh fingerprint differs from pre-fetch one**. Anti-flicker mechanism: re-renders only when a tracked job's status/progress actually changed; stops re-arming once no job active. Guards (S.page!=='detail', detailData.id!==id) prevent stale timers clobbering state after navigation.

## Side effects
**fetch() to app.py** grouped by page: Boot/auth (/api/bootstrap, /api/csrf-token, /api/login, /api/register, /api/logout, /api/me, /api/forgot-username, /api/forgot-password, /api/reset-password); Dashboard (/api/status, /api/costs partial); Transcribe (/api/providers, /api/providers/{name}/models, POST /api/transcribe, /api/costs/estimate); Transcript library/detail (/api/transcripts, /.../{id}, /api/search, /.../audio, /.../video, /.../speakers/rename, /.../segments/retag, /.../relabel-undo, /.../enroll-speaker, /.../summarize, /.../format/{target}, /.../export-markdown, /.../correct, /.../rediarize, /.../voice-match, /.../context, /.../versions, /.../retry-failed-chunks, /.../cancel, /.../resume, /.../retranscribe); Bulk (/api/bulk-transcribe, /api/batches, /.../{id}, /.../{id}/cancel); Voice notes/dump; Jobs/queue; Assistant; Voice roster; Settings/files/admin; Misc (/api/health, GET /, GET /sw.js).

**localStorage**: rack-theme, rack-motion, rack-phosphor — read once in loadPrefs() (127) on boot, written by applyTheme/applyMotion/applyPhosphor (104-125) on Settings change.

**sessionStorage**: wd_assistant_history (486 read, 699 write) — Assistant page conversation history, tab lifetime only.

**Service worker** (sw.js, served at app.py:3660 with a content-hash CACHE_VERSION fingerprint forcing reinstall on bundle change): a **caching layer, not offline-queue**. Fetch handler (sw.js:60-80): /api/* -> network-first with cache fallback (no queueing, no background sync); everything else -> cache-first falling back to network. Precache: /, rack.min.js, rack.min.css, fonts.

## Error/fallback branches
- `api()` (243): 401 -> forces showLogin(), throws. 403 on mutating request -> one-shot CSRF-token refresh + retry, then re-checks 401. Other non-ok -> throws Error(detail) from JSON body or 'HTTP '+status.
- **No special 429 or 409 client-side handling exists.** Grep for `409|429|res\.status` found only the 401/403 branches above — 429 rate-limit responses and app.py:2177's 409 ("Cannot change mode while transcription is running") surface as generic thrown Error, caught and toasted. No client-side retry-on-lock/backoff loop. app.py has several 429 limiters but UI treats identically to any other error.
- The "atomic terminal job state" work (PR #389, per session history) is backend/job-state-machine only — no corresponding client-side 409-retry code found in rack.js. Flagged as a gap between what session history implied and what's actually in source (may be backend-only so far).
- pollBackgroundJobs/refreshQueueBadge swallow fetch failures silently, retry/skip, explicitly "best-effort."
- scheduleDetailPoll's catch swallows transient failures, "next action revives it," no auto-retry-with-backoff.

## Mermaid flowchart

```mermaid
flowchart TD
    subgraph ENTRY["Entry / Boot"]
        A1["GET /<br/>app.py:3613"] --> A2["index.html<br/>static/index.html:155"]
        A2 --> A3["DOMContentLoaded<br/>rack.js:6529"]
        A3 --> A4["checkAuth<br/>rack.js:951"]
        A4 --> A5["fetch /api/bootstrap<br/>app.py:757"]
        A5 -->|user present| A6["showApp<br/>rack.js:858"]
        A5 -->|no user| A7["showLogin<br/>rack.js:849"]
        A6 --> A8["navigate('dashboard')<br/>rack.js:446"]
        A6 --> A9["startBackgroundJobPoll<br/>rack.js:932"]
        A3 --> A10["serviceWorker.register('/sw.js')<br/>rack.js:6560"]
        A10 --> A11["GET /sw.js<br/>app.py:3660"]
    end

    subgraph NAV["Page navigation"]
        B1["PAGES array<br/>rack.js:425"] --> B2["navigate(page,data)<br/>rack.js:446"]
        B2 --> B3["toggle .page-* / .rail-btn active<br/>rack.js:452"]
        B2 --> B4["refreshRailChrome<br/>rack.js:433"]
        B4 --> B5["fetch /api/status<br/>app.py:3690"]
        B2 --> B6{"loaders[page]<br/>rack.js:457"}
    end

    subgraph TX["Representative page: Transcribe"]
        C1["renderTranscribe<br/>rack.js:1561"] --> C2["ensureProviders<br/>rack.js:1488"]
        C2 --> C3["fetch /api/providers<br/>app.py:982"]
        C1 --> C4["fetchModelsFor<br/>rack.js:1515"]
        C4 --> C5["fetch /api/providers/{name}/models<br/>app.py:1059"]
        C1 --> C6["root.innerHTML = deck markup<br/>rack.js:1567"]
        C6 --> C7["wireTranscribe / wireMfd<br/>rack.js:1699 / 1862"]
        C7 --> C8["loadTape<br/>rack.js:2234"]
        C8 --> C9["startJob<br/>rack.js:2271"]
        C9 --> C10["POST /api/transcribe (FormData)<br/>app.py:1482"]
        C10 --> C11["pollTranscript(id)<br/>rack.js:2360"]
        C11 -->|running/queued| C11
        C11 -->|done| C12["syncTranscribe<br/>rack.js:2034"]
        C12 --> C13["toast + update deck B state<br/>rack.js:2339"]
        C9 -->|catch| C14["toast('Transcription failed')<br/>rack.js:2355"]
    end

    subgraph JOBPOLL["Job / queue polling subsystem"]
        D1["startBackgroundJobPoll<br/>rack.js:932"] --> D2["pollBackgroundJobs<br/>rack.js:900"]
        D2 --> D3["getJobs (15s TTL cache)<br/>rack.js:874"]
        D3 --> D4["fetch /api/jobs?limit=50<br/>app.py:3322"]
        D2 --> D5{"terminalFailure &&<br/>!wasTerminalFailure?"}
        D5 -->|yes| D6["toast(humanizeJobError)<br/>rack.js:923"]
        D5 -->|no| D7["update bgJobStatusSeen<br/>rack.js:925"]
        D2 --> D8["setTimeout 8000ms, loop<br/>rack.js:929"]

        E1["loadQueue<br/>rack.js:3425"] --> D3
        E1 --> E2["jobStatusView<br/>rack.js:3380"]
        E1 --> E3["jobActions<br/>rack.js:3395"]
        E3 --> E4["updateQueueBadge<br/>rack.js:3610"]
        E5["refreshQueueBadge<br/>rack.js:3614"] --> D3
        E5 --> E4

        F1["loadTranscriptDetail<br/>rack.js:3755"] --> F2["scheduleDetailPoll<br/>rack.js:3793"]
        F2 --> F3["_jobFingerprint(t)<br/>rack.js:3784<br/>hash of 9 job slots"]
        F2 --> F4["setTimeout 2500ms"]
        F4 --> F5["fetch /api/transcripts/:id<br/>app.py:1882"]
        F5 --> F6{"fingerprint changed?"}
        F6 -->|yes| F7["updateDetailJobStatus + re-render<br/>rack.js:4264"]
        F6 -->|no| F8["skip render, re-arm poll"]
        F7 --> F2
        F8 --> F2
    end

    B6 -->|transcribe| C1
    B6 -->|detail| F1
    B6 -->|queue| E1
```

## External dependencies (grouped API routes)
Auth/session; Admin; Settings/config; Providers/models; Transcription; Transcripts CRUD/detail; Speaker/diarization; LLM jobs on a transcript; Voice notes/dump; Jobs/queue; Assistant; Voice roster; Files; Costs/status; Misc — full grouped list in the source agent report (70+ routes grepped exhaustively from app.py's @app.get/post decorators).

## Confidence and gaps
High confidence on: full definitions list (unbounded grep, cross-checked against direct read of 6515-6574), PAGES/navigate control flow, job-poll/fingerprint mechanism, service worker caching-only behavior (all read verbatim/in full). Gaps: did not read all of loadSettingsPage (6122-6480, ~360 lines) or rack.css in full — confirmed boundaries/role only. No client-side 409-retry-on-lock code found despite PR #389 session-history reference — flagged as possible backend-only or not-yet-landed work, not asserted either way. Route list exhaustive for @app.get/post decorators but not every one verified as actually called from rack.js (a few, e.g. /api/diarize, may be legacy/unused frontend-side).
