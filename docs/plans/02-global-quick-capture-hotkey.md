# Global quick-capture hotkey + text-selection toolbar

> One-line status: Draft plan. Idea inspired by Blinko (github.com/blinkospace/blinko), concept only, no code copied.

## Motivation

Today, starting a recording or asking the LLM a question both require opening the WhisperDeck tab, navigating to the right page, and clicking through a modal. That's fine for planned meetings but kills the "I just thought of something, capture it now" use case that voice notes are meant for, and it means the transcript detail view has no fast path from "this sentence is interesting" to "summarize it" or "ask about it" without leaving the page to the Assistant tab and retyping context by hand.

Two independent conveniences:

- **Quick capture**: one keystroke, from wherever you are, starts recording and files the result as a voice note.
- **Selection toolbar**: select text inside a transcript, get inline actions (ask AI, summarize, copy corrected) without a page hop.

## What Blinko does (attribution)

Blinko (github.com/blinkospace/blinko) is a self-hosted note app with two relevant features we're borrowing the *idea* of, not the code:

- **Quick Note / Quick AI**: a system-wide hotkey opens a small capture window from any application, so a thought can be jotted (or processed by an AI action) without switching to the main app.
- **Text-selection toolbar**: selecting text anywhere in the note view surfaces a small floating toolbar with actions like translate, ask AI, or bookmark.

We're adapting both concepts to WhisperDeck's shape: capture is audio-first (voice notes), not text-first, and the selection toolbar operates on transcript segments instead of freeform notes.

## Proposed approach

### Critical constraint: WhisperDeck has no native layer today

WhisperDeck is a locally-served browser app: `app.py` starts a FastAPI server via `uvicorn.run(app, host="0.0.0.0", port=9781)` (invoked by `run.bat` or `python app.py`), and everything else, recording, playback, transcript editing, is a single-page app in `static/index.html` + `static/rack.js` running in an ordinary browser tab. The "portable build" (`scripts/build_release.ps1`) just zips an embedded Python runtime and a static ffmpeg around the same server; it does not wrap the app in Electron, add a tray icon, or install any native shell. There is no `pynput`/`keyboard`/`global-hotkey` dependency anywhere in `requirements*.txt`.

That matters because a browser tab cannot register a true OS-global hotkey. Page-level `keydown` listeners (the pattern already used in `rack.js`, e.g. the `document.addEventListener('keydown', ...)` Escape-closes-modal handler installed in the `DOMContentLoaded` init block) only fire while the tab has focus. There is no web platform API that lets an ordinary page intercept a key combo system-wide, regardless of which window is focused.

Four ways to actually get an OS-global hotkey, in order of how well they fit this project:

| Option | How it works | Works when browser is closed? | Engineering lift | Fit for WhisperDeck |
|---|---|---|---|---|
| **A. Small native tray helper** | A separate long-running process (Python + `pynput`/`keyboard` for the hotkey, `pystray` for a tray icon) registers a true OS hotkey (Win32 `RegisterHotKey` under the hood on Windows; X11/`evdev` hooks on Linux; `Carbon`/`Quartz` on macOS) and calls the local server's API when triggered. | Yes (as long as the helper is running) | Medium, new process, per-OS packaging, autostart, and an auth story for calling the API from outside the browser session | **Best fit.** Matches WhisperDeck's Python-first, no-Electron ethos, and there's already a precedent for shipping a bundled Python runtime (the portable build). |
| **B. OS-level hotkey daemon (DIY)** | Point a third-party tool the user already has or installs themselves (AutoHotkey on Windows, `xbindkeys`/systemd user unit on Linux, `skhd` or macOS Shortcuts) at a documented local endpoint (e.g. `curl -X POST localhost:9781/api/...`). | Yes | Low for us, but pushes setup onto the user | Reasonable fallback to document for power users, not a good default (per-OS tool sprawl, nothing "just works") |
| **C. Browser extension** | A Chrome/Firefox extension registers a shortcut via the `commands` API and messages the app or opens a "quick capture" route. | Only while the browser process is running | Medium-high, new build target, store listing or unpacked-extension friction, Manifest V3 vs Firefox differences | Weaker fit: WhisperDeck has zero extension infrastructure today, and the result is still browser-scoped, not truly system-wide |
| **D. In-page hotkey only** | A `keydown` listener scoped to the WhisperDeck tab, using the same pattern already in `rack.js`. | No, requires the tab focused | Very low | Good as an immediate, zero-risk Phase 1; not what "quick-capture from anywhere" promises |

**Recommendation:** ship D immediately as a cheap, real improvement, then treat A (native tray helper) as the real answer to "global hotkey," gated as an optional, separately-launched companion process, not bundled into the always-on web server. Document B as a DIY recipe for anyone who doesn't want a second process running. Skip C: it adds a whole new packaging surface (extension store + browser-specific APIs) for a capability (works only while the browser runs) that option A already covers plus more.

The one problem option A introduces that doesn't exist today: the helper is a separate OS process, so it can't ride along on the browser's session cookie. `get_current_user` in `app.py` only resolves a session (`_resolve_session_user`, cookie-based via `SessionMiddleware`), there's no bearer-token path for programmatic callers. The helper needs its own credential; see Data model section.

### Quick capture flow (once a trigger, hotkey or otherwise, fires)

Reuse the existing voice-note pipeline rather than inventing a new one. `Transcript.kind` already has a `voice_note` value (alongside `meeting` / `dictation`) in `database/__init__.py`, which drives a lighter diarization/summary path and triggers the `LlmJob(kind="voice_note")` chain (see `VoiceNote` model and `rerun_voice_note_chain` in `app.py`). Quick capture should produce a transcript with `kind="voice_note"` so it lands in the same place existing voice notes do, gets the same lightweight processing, and shows up wherever voice notes are already listed (`GET /api/voice-notes`).

Two sub-cases depending on which hotkey layer fired:

- **In-page hotkey (Phase 1):** call `startLiveCapture()` in `rack.js` directly (same function the "Start recording" modal button already calls), but tag the resulting tape as a voice note instead of a general meeting recording before it's handed to `loadTape()`.
- **Native helper (Phase 2):** the helper either (a) does its own short mic recording and uploads it to a new "quick capture" endpoint, or (b) just brings the WhisperDeck window/tab to focus and simulates the in-page hotkey. (b) is far less code and reuses 100% of the browser-side recording path; (a) is more "truly background" (doesn't need a browser tab open at all) but duplicates audio-capture logic outside the browser. Recommend starting with (b), revisiting (a) only if "capture without a browser window open at all" turns out to matter to users.

### Text-selection toolbar

Pure frontend, no native layer needed. On `mouseup` inside a transcript's rendered body (the segment rows built in `renderDetail/renderDetailBody`, and the corrected-text rows built in `correctedHtml`), check `window.getSelection()` for a non-collapsed range confined to the transcript container, and if so position a small floating toolbar near the selection (via `Range.getBoundingClientRect()`). Toolbar actions:

- **Ask AI about selection**, reuse the existing assistant plumbing: `POST /api/assistant` already enqueues an `LlmJob(kind="assistant")` polled via `GET /api/assistant/result/{job_id}` (`assistant_request`/`assistant_result` in `app.py`, `submitAssistantRequest`/`pollAssistantJob` in `rack.js`). Seed the request text with the selection plus a small amount of surrounding context instead of a typed question.
- **Summarize selection**, same idea, but this is closer to a scoped one-off than the existing full-transcript `summary` job kind; simplest implementation is to route it through the assistant job with a "summarize the following" framing rather than adding a new `LlmJob` kind.
- **Copy corrected**, no LLM call at all; if the selection falls inside the corrected-text view already, this is just `navigator.clipboard.writeText(selection.toString())` (a much simpler version of the existing `copyToClipboard`/export helpers already used elsewhere in `rack.js`).

No new job kinds or schema needed for the toolbar; it's a thin UI layer over endpoints that already exist.

## Code touchpoints (files + symbols, no line numbers)

- `static/rack.js`
  - `startLiveCapture()`, the `CAP` capture-state object, `loadTape()`, existing recording path; Phase 1 hotkey and Phase 2 "bring tab to focus + trigger capture" both hang off this.
  - `document.addEventListener('keydown', ...)` inside the `DOMContentLoaded` init block, pattern to extend for the in-page quick-capture shortcut (with a guard so it doesn't fire while typing in an `input`/`textarea`, same care already taken by the per-input `keydown` listeners elsewhere in the file).
  - `renderDetail()`, `renderDetailBody()`, `correctedHtml()`, where transcript/segment text is rendered; the selection toolbar listens for `mouseup` within these containers.
  - `loadAssistant()`, `renderAssistant()`, `submitAssistantRequest()`, `pollAssistantJob()`, reused by the "ask AI about selection" toolbar action.
  - `copyToClipboard()`, reused by "copy corrected".
- `app.py`
  - `uvicorn.run(...)` entry point, `get_current_user()` / `_resolve_session_user()`, where a new token-based auth path would plug in alongside the existing cookie path for the native helper.
  - `assistant_request()` / `assistant_result()`, no change needed, just a new caller.
  - Voice-note endpoints (`get_transcript_voice_note`, `list_voice_notes`, `rerun_voice_note_chain`), quick-capture transcripts should show up through these unchanged.
  - A new endpoint (name TBD, e.g. `quick_capture_upload`) only if Phase 2 goes with helper-does-its-own-recording (option (a) above) instead of helper-triggers-the-tab (option (b)).
- `database/__init__.py`
  - `Transcript.kind`, reuse the existing `voice_note` value; no new enum value expected.
  - `VoiceNote` model, unchanged, quick-capture transcripts flow into it the same way manual voice notes do.
  - New: a small token table (or a column on `User`) if Phase 2's native helper needs its own credential, see Data model section.
- `services/settings.py`
  - `DEFAULT_SETTINGS`, candidate home for a `quick_capture_enabled` / default-kind-for-quick-capture flag if we want it configurable per user, though the hotkey binding itself is client-side and doesn't belong here.
- `services/llm_jobs.py`
  - `VALID_KINDS`, `AUTO_RETRY_KINDS`, `IO_KINDS`, confirm no changes needed since the toolbar reuses the existing `assistant` kind rather than adding one.
- `scripts/`, home for a new, separate helper script/package if Phase 2 is built (e.g. `scripts/quick_capture_helper.py` using `pystray` + `pynput`), kept independent from `app.py` so the always-on web server doesn't gain a new dependency just for an optional feature.

## Data model / schema changes

- **Phase 1 (in-page hotkey + selection toolbar): none.** Both reuse existing tables, columns, and job kinds.
- **Phase 2 (native helper), if pursued:**
  - A local API credential so the helper process can authenticate without a browser session cookie. Options, roughly in order of simplicity:
    - A single per-user long-lived token stored as a new column (e.g. `User.local_api_token`, hashed at rest like passwords already are) generated/regenerated from a Settings UI action, sent by the helper as a custom header and checked by a small addition to `get_current_user`'s resolution path.
    - Or a dedicated small table (`id`, `user_id`, `token_hash`, `label`, `created_at`) if we want multiple named tokens (e.g. "tray helper" vs. "future mobile client") rather than one token per user. Slightly more schema, more future-proof; probably overkill for a single helper process.
  - No changes to `Transcript`/`VoiceNote`/`LlmJob` schemas, quick capture is just another way of producing a `kind="voice_note"` transcript.

## Research notes

- **Selection API**: `window.getSelection()` + `Selection.getRangeAt(0)` + `Range.getBoundingClientRect()` is standard, broadly supported, and sufficient for positioning a floating toolbar; no library needed. Must guard against selections that span outside the transcript container (e.g. user drags across the whole page) and against firing on an empty/collapsed selection.
- **True OS-global hotkeys are an OS-level primitive, not a web one.** Windows: `RegisterHotKey` (wrapped by Python's `keyboard`/`pynput` or, for a lower-level option, a small Rust binary using the `global-hotkey` crate). Linux has no single mechanism (X11 vs. Wayland compositors differ, several of which restrict global key grabs entirely for security reasons, this is a real limitation to flag, not just an implementation detail). macOS requires Accessibility/Input-Monitoring permission grants for any global key hook, which adds an onboarding step users have to click through.
- **Browser `commands` API** (Chrome/Firefox extensions) can declare `"global": true` shortcuts on some platforms, but support and OS coverage is inconsistent, and it only works while the browser process is alive, same ceiling as option D but with the packaging cost of option C.
- **Server bind address**: `app.py` binds `uvicorn.run(..., host="0.0.0.0", ...)`, not `127.0.0.1`, so the server is reachable from other machines on the LAN, not just localhost. This raises the stakes on Phase 2's auth story: a bearer token isn't optional hardening, it's required, since "just trust localhost" isn't actually true of the current bind.

## Open questions

- Is a second always-running background process (the tray helper) an acceptable addition to WhisperDeck's "local-first, run `python app.py`" simplicity, or does that undercut the project's low-friction pitch enough that Phase 2 should stay an optional, clearly-separate download rather than something `run.bat`/`build_release.ps1` sets up automatically?
- Should quick-capture transcripts get their own `kind` value (e.g. `quick_capture`) distinct from manually-created `voice_note`s, in case we later want different defaults (e.g. even lighter diarization, a different summary prompt), or is reusing `voice_note` as-is the right call for now?
- For Phase 2's helper-triggers-the-tab approach: what happens if no WhisperDeck tab is currently open? Does the helper launch a new browser window pointed at the app, and if so, does that count as "instant capture" or does the extra window-open latency defeat the purpose?
- Single per-user token vs. a full token table: do we expect more than one external caller (helper today, maybe a future mobile companion) soon enough to justify the extra schema now, per the "don't front-load" plan-hygiene rule?
- Cross-platform priority: build the native helper for Windows only first (matches the existing Windows-first portable-build precedent), or block Phase 2 on having a Linux/macOS story too?

## Rough phasing / checklist

### Phase 1: Text-selection toolbar (low risk, ships first)
- [ ] Add `mouseup` listener scoped to transcript containers (segment rows in `renderDetailBody`, corrected-text rows in `correctedHtml`)
- [ ] Compute selection bounds via `Range.getBoundingClientRect()`, render a small floating toolbar, dismiss on new selection/click-away/Escape (reuse the existing Escape-closes-modal pattern)
- [ ] Wire "Ask AI about selection" to the existing `/api/assistant` + poll flow, seeded with selected text
- [ ] Wire "Copy corrected"/"Copy selection" to `navigator.clipboard.writeText`
- [ ] Manual pass: selection spanning multiple segments, selection dragged outside the container, empty selection, very long selection

### Phase 2: In-page quick-capture hotkey (low risk)
- [ ] Add a document-level `keydown` listener (guarded against firing while an `input`/`textarea` has focus) bound to a configurable key combo
- [ ] Trigger `startLiveCapture()` and tag the resulting tape/transcript as `kind="voice_note"`
- [ ] Surface the bound key combo somewhere discoverable (Settings or a tooltip) so it isn't a hidden Easter egg
- [ ] Manual pass: hotkey while a modal is open, while an input has focus, while already recording

### Phase 3: Native tray helper for a true OS-global hotkey (bigger lift, do only after Phase 1/2 land and the appetite for a second process is confirmed)
- [ ] Design the local-API credential (single token column vs. token table) and add the corresponding auth path alongside `get_current_user`
- [ ] Build a minimal Windows tray helper (`pystray` + `pynput` or equivalent) that registers the global hotkey and calls the local server
- [ ] Decide helper-triggers-tab vs. helper-records-itself (recommend starting with triggers-tab)
- [ ] Document manual install/autostart for Windows; explicitly scope out (or separately plan) Linux/macOS support
- [ ] Document the AutoHotkey/`xbindkeys`/Shortcuts DIY recipe (option B) as a no-extra-process alternative for power users

## Testing considerations

- Selection toolbar and in-page hotkey are pure frontend changes with a real runtime surface (DOM events, clipboard, live recording), per the project's testing-tiers guidance, verify by actually driving the flow in a browser rather than relying on unit tests alone: select text across segment boundaries, trigger the hotkey while typing in a field (must not fire), trigger it mid-recording (must not double-start).
- If Phase 3's token auth path is built, it changes `get_current_user`'s resolution logic on a security-sensitive path, needs explicit tests for "valid token, no cookie," "invalid/expired token," and "neither token nor cookie," not just the happy path.
- Any new visible control (toolbar buttons, a hotkey hint in Settings) is a UI change; grep `tests/`/e2e specs for existing selectors before renaming or restructuring anything nearby.
