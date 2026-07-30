# Brainstorm: global quick-capture hotkey + selection toolbar

> Companion to `docs/plans/02-global-quick-capture-hotkey.md`. That plan already picked a direction (in-page hotkey now, native tray helper later). This document is the brainstorm layer underneath it: alternatives considered, honest tradeoffs, and open decisions. It does not resolve anything the draft plan left open, and it does not replace the draft plan's checklist.

## User-intent framing

Why does "from anywhere" matter for a dictation tool specifically, more than for a generic note app.

- The core value of voice capture over typed notes is that it removes the friction of stopping what you're doing to write something down. If the user still has to stop, find the WhisperDeck tab, and click a button before they can start talking, most of that friction is already back. The whole pitch of a hotkey is: the thought arrives while doing something else (reading, coding, driving thought experiments in the shower with a phone nearby, whatever), and capture has to compete with the cost of losing the thought.
- A browser-tab-scoped hotkey (`docs/plans/02-global-quick-capture-hotkey.md` option D) still requires the tab to exist and be the active window. That is a real improvement over "open a modal and click Record," but it is not the "walked away from my desk, thought of something, hit a key, done" use case the feature name promises. Worth being explicit about this gap rather than letting Phase 1 quietly stand in for the whole feature.

Silent assumptions worth surfacing before committing engineering time:

- **Assumes a desktop session, not a phone.** WhisperDeck is a local server the user reaches over LAN/localhost. A true global hotkey helper is a desktop-OS concept (Win32 `RegisterHotKey`, macOS Carbon/Quartz, X11/evdev). If a meaningful fraction of "away from the desk" capture actually happens from a phone, none of the native-helper work here addresses that at all, a phone home-screen shortcut or PWA install would be a completely different feature.
- **Assumes the user is willing to run a second background process.** This is exactly the open question the draft plan already flags. Worth treating as load-bearing: if the answer turns out to be "no," Phase 3 (native helper) is not a smaller version of the feature, it is a different feature that doesn't ship.
- **Assumes silent/fast beats accurate.** A hotkey capture flow implies the user wants zero decisions between "have a thought" and "it's being recorded." Every confirm dialog, permission prompt, or "which app to file this under" choice erodes that. This tension surfaces again in the "what quick-capture does with the result" section below.
- **Assumes transcription latency is acceptable after the fact.** The hotkey solves *starting* capture, not the wait for STT/diarization to produce a transcript. If the underlying assumption is "I want to see readable text moments later," local model transcription time needs to actually support that; if it's "I just want the audio saved and I'll deal with it later," the UI can be much less eager to surface a live transcript. The draft plan doesn't say which the user wants; worth resolving before designing the confirm/preview affordance.

## Native-layer choice for a true global hotkey

The draft plan's four-option table is solid; this expands two dimensions it under-weights: cross-platform reality and what "small" actually costs.

### Cross-platform reality, Windows-first

| Platform | Global hotkey mechanism | Practical friction |
|---|---|---|
| Windows | `RegisterHotKey` (Win32), wrapped by `keyboard` or `pynput` in Python | Works reliably, no elevation needed for most key combos, matches WhisperDeck's existing Windows-first portable-build precedent |
| macOS | Requires Accessibility or Input Monitoring permission grant (System Settings, not just an in-app prompt) | Non-trivial onboarding: user has to leave the app, find the settings pane, toggle a permission, sometimes restart the helper. Easy to get flagged as "sketchy background app wants to watch your keystrokes" |
| Linux (X11) | `xbindkeys`, `evdev`, or library-level hooks | Works, but several distros restrict raw input device access without `udev` rules or running as root |
| Linux (Wayland) | No standard global-grab API; compositor-specific (GNOME, KDE, sway all differ) or none at all | Real ceiling, not a papering-over-it problem. Some compositors flatly do not allow a background process to grab a global hotkey for security reasons |

Given the repo's actual footprint (no Electron, no packaging for macOS/Linux GUIs referenced anywhere the grounding search touched, portable-build script is Windows-focused), a cross-platform native helper is a bigger commitment than "small tray app" suggests. Windows-only Phase 3 is the realistic scope; macOS is a second, separately-scoped project with its own onboarding UX; Linux/Wayland may not be fully solvable at all and should be documented as "use the DIY daemon recipe" rather than promised.

### Reconsidering the four options with that lens

| Option | Added consideration | Verdict |
|---|---|---|
| A. Native tray helper | Cost is mostly Windows-only if scoped honestly; macOS/Linux support is a distinct, optional follow-up, not "the same feature, more platforms" | Still the right answer for "true global hotkey," but scope the initial build to Windows explicitly and say so in the UI/docs rather than implying parity |
| B. DIY daemon (AutoHotkey/xbindkeys/skhd) | Zero code for WhisperDeck, but the user has to wire it to a documented endpoint themselves, and every user request for help becomes "which of the three tools are you using" | Good permanent fallback for power users regardless of whether A ships; keep documenting it even after A exists |
| C. Browser extension | `commands` API's `"global": true` shortcut support is inconsistent (varies by OS and by Chrome vs Firefox), and it still requires the browser process alive, same ceiling as in-page-only, but with an extension-store or unpacked-extension packaging tax on top | Skip, as the draft plan concludes; the added packaging cost buys nothing over option D that persists once the browser closes |
| D. In-page only | No native layer, real limitation is explicit rather than hidden | Correct as immediate ship, wrong if marketed as solving "from anywhere" |

**Recommendation (unchanged from the draft plan, restated with the added cross-platform caveat):** ship D now, treat A as Windows-only for its first cut, document B always, skip C. The new piece here: put the Windows-only scope in the user-facing description of Phase 3 from day one, not as a retroactive caveat once someone on macOS files a bug.

## Auth/security for a native helper calling the local server

`app.py`'s `get_current_user` resolves only through `_resolve_session_user`, which reads `request.session.get("user_id")`, populated by `SessionMiddleware` off a cookie. There is no bearer-token or API-key code path anywhere in `app.py` today (confirmed by grep, no `Authorization`/`X-API-Key` handling exists). A helper process is not a browser tab and has no cookie jar tied to a logged-in session, so it needs its own credential path added, not reused.

This matters more than it would for a typical "add an API token" task because `uvicorn.run(app, host="0.0.0.0", ...)` in `app.py` binds all interfaces, not just localhost. Anyone on the same LAN segment can already reach the port; a "start recording" endpoint with no auth at all would let any device on the network trigger recording on the user's machine. A bearer token for the helper is not a nice-to-have hardening pass, it is the minimum bar for shipping this endpoint at all.

### Alternatives for the credential itself

| Approach | Shape | Tradeoff |
|---|---|---|
| Single per-user token column | `User.local_api_token`, hashed at rest like passwords, one value, regenerate via a Settings button | Simplest; fine if there's only ever one caller (the helper). Regenerating invalidates every existing caller at once, acceptable for a single-helper story |
| Dedicated token table | `id, user_id, token_hash, label, created_at`, supports multiple named tokens | More schema and UI (list/revoke per token) for a benefit that doesn't exist yet (no second caller is planned). Matches the "don't front-load" plan-hygiene habit: build this when a second caller (mobile companion, second helper) is real, not preemptively |
| Reuse session cookie via helper-launches-browser | Helper never calls the API directly; it always brings a real browser tab to focus and simulates the in-page hotkey (already the draft plan's recommended Phase 2 sub-option "(b)") | No new auth surface at all for the "helper triggers tab" path. Only the "helper records audio itself and uploads it" sub-option (a) actually needs a token. This is worth restating clearly: **the auth question only exists if the helper does its own recording**, so deferring auth work is possible if (b) ships first |

### Scoping the exposed endpoint if a token path is built

If the helper does call the server directly (sub-option (a), or any future "start recording remotely" endpoint), the endpoint surface should be as narrow as the credential allows:

- A single-purpose endpoint (e.g., "start a voice-note recording, here's the audio") rather than a general-purpose token that unlocks every authenticated route the cookie session would. Scoping the token to specific routes (or specific job kinds) limits blast radius if the token leaks (e.g., synced to a dotfile repo by accident, or read off disk by other local software).
- Rate limiting or a minimum-interval guard on a "start recording" trigger, so a leaked or replayed token can't be used to spam recording starts.
- Explicit test coverage for "valid token, no cookie," "invalid or expired token," and "neither token nor cookie" on whatever code path gets added to `get_current_user`'s resolution chain, per the project's own testing-tiers rule that a security-sensitive path needs more than the happy path.

**Recommendation:** don't build the token path in the same phase as the hotkey UI. Ship helper-triggers-tab first (sub-option (b), zero new auth surface), and only build the token + endpoint if "capture without any browser window open" turns out to be a real, requested capability rather than a nice-to-have. This mirrors the draft plan's own phasing but makes the auth cost explicit as the reason to defer, not just "bigger lift."

## What quick-capture does with the result

Three shapes, in order of how much friction they add back:

| Shape | Behavior | Tradeoff |
|---|---|---|
| Silent auto-file | Hotkey starts recording, stopping (second press, silence timeout, or fixed max length) auto-transcribes and files as a `kind="voice_note"` transcript with zero further interaction | Matches the "capture now, deal with it later" intent described above. Risk: a misfire (accidental hotkey press, wrong app focused) silently creates a low-value transcript with no chance to cancel before it's filed |
| Confirm before recording starts | Hotkey opens a small "recording..." indicator with a cancel affordance, but still auto-files on stop | Small friction cost (has to notice/dismiss the indicator), catches misfires before audio is captured, but adds a UI element that has to render fast and reliably even if focus is elsewhere |
| Preview before filing | After transcription, show the result and require a save/discard/edit action | Defeats a lot of the "instant capture" motivation. Reasonable only if the user profile leans toward "protect me from noise in my voice-note list" over "just get out of my way" |

The draft plan's recommendation (reuse `kind="voice_note"`, same lightweight pipeline as `VoiceNote`/`rerun_voice_note_chain` in `app.py`) already answers "where does it go." It does not answer "does the user see anything before it's committed." Given the tension between the silent-and-fast assumption above and the very real annoyance of misfire noise, this is a genuine open call, not a foregone one: a lightweight "recording... (press again to cancel)" indicator is probably the sweet spot (catches accidental starts, costs almost nothing), but auto-transcribe-and-file after that with no further gate matches the feature's whole premise.

One more shape worth flagging separately from the three above: should quick-capture transcripts get their own `Transcript.kind` value (e.g., `quick_capture`) instead of reusing `voice_note`, specifically so that a future "silent misfire" cleanup pass (e.g., "auto-delete quick captures under 2 seconds with no speech detected") has something to filter on that manually created voice notes don't share. The draft plan already raises this as an open question; worth keeping open here too rather than pre-committing.

## Selection toolbar action set

The draft plan's three actions (ask AI about selection, summarize selection, copy corrected) are a reasonable starting set. Widening the option space:

| Action | Reuses existing pipeline? | Notes |
|---|---|---|
| Ask AI about selection | Yes, `/api/assistant` → `assistant_request`/`assistant_result` in `app.py`, `submitAssistantRequest`/`pollAssistantJob` in `rack.js` | Seed the request text with selection + light surrounding context. No new job kind |
| Summarize selection | Yes, same assistant job, framed as "summarize the following" rather than a full-transcript `summary` job | Cheaper than adding a scoped-summary job kind; the tradeoff is the assistant job's prompt has to disambiguate "summarize this selection" from "answer this question" reliably, worth a quick prompt-quality check before assuming it "just works" |
| Copy corrected / copy selection | No LLM call, `navigator.clipboard.writeText` | Simplest action, no backend involvement at all |
| Translate selection | Could reuse assistant job with a "translate to X" framing, or could be a genuinely new lightweight job kind if quality suffers from being crammed into the general assistant prompt | Not in the draft plan's three, but Blinko's version of this feature includes it. Worth asking whether translate is in scope for this pass or a later addition, since a dedicated job kind is a bigger lift than the other three |
| Save selection as a standalone voice-note-style note (text, not audio) | Would need a new "text note" concept, WhisperDeck has none today (`VoiceNote` wraps a transcript, not freeform text) | Out of scope, flagged only because Blinko's Quick Note is text-first, and it would be easy to over-borrow that shape into a project whose entire model assumes audio-first capture |

**Reuse-vs-new-endpoint framing:** every action that touches an LLM should go through the existing `/api/assistant` + `LlmJob(kind="assistant")` pipeline rather than adding new job kinds, matching the draft plan's stated preference and avoiding a parallel enum WhisperDeck's own Complement Rule would then require threading through `VALID_KINDS`/`AUTO_RETRY_KINDS`/`IO_KINDS` in `services/llm_jobs.py`, the batch-fetch filter that already special-cases `rediarize`, and any UI switch that renders per-kind labels. Only add a new kind if a specific action's quality genuinely suffers from being expressed as an assistant-job prompt, translate is the most likely candidate for that, if it's in scope at all.

## Cross-platform global-hotkey libraries and footprint

For the native helper (Phase 3), if and when it's built:

| Library / approach | Platform coverage | Footprint / permissions |
|---|---|---|
| `keyboard` (Python) | Windows well-supported; Linux needs root or input-group membership for raw device access; macOS limited | Simple API, but the Linux root requirement is a real deployment wrinkle, not just a doc footnote |
| `pynput` | Windows, macOS, Linux (X11) | macOS still needs Accessibility/Input Monitoring permission granted through System Settings, same friction as any macOS global-hook approach |
| `pystray` (tray icon, paired with either of the above for the hotkey itself) | Windows, macOS, Linux (varies by desktop environment's tray/notification-area support) | Tray icon presence itself is usually not permission-gated, the hotkey hook is what triggers OS prompts |
| A small native binary using Rust's `global-hotkey` crate, invoked from or alongside the Python helper | Same OS coverage as the above, different implementation | Adds a second language/toolchain to the helper for marginal benefit unless Python-side libraries prove unreliable in testing; not worth it unless `keyboard`/`pynput` demonstrably misbehave on the target OS |

**Recommendation:** start with `pynput` + `pystray` for a Windows-only Phase 3 helper, matching the draft plan's own suggestion; there's no reason to reach for a second toolchain (Rust) before the Python-first approach is shown to be insufficient. Revisit only if cross-platform coverage becomes a hard requirement.

## Risks and failure modes (with detection)

| Risk | How it would show up | Detection / mitigation |
|---|---|---|
| Hotkey fires while typing in an unrelated app (Phase 3) or in a WhisperDeck input field (Phase 1/2) | Recording starts unexpectedly, interrupting typing or capturing keystrokes as audio context noise | Phase 1/2: guard the `keydown` listener against focus being in an `input`/`textarea`, same pattern the draft plan already calls out. Phase 3: harder, since the helper can't see focus state in other apps; likely needs a distinctive, unlikely-to-collide key combo as the main defense, plus a visible "recording started" indicator so a misfire is caught immediately rather than silently |
| Hotkey double-fires (key repeat, or pressed while already recording) | Two overlapping recordings, or a recording that starts then immediately stops | Guard on `S.capturing`/`S.running` state, same check already present at the top of `startLiveCapture()` |
| Native helper silently stops running (crashed, not auto-started after reboot) | User believes the global hotkey works, presses it, nothing happens, no feedback anywhere since the browser tab isn't involved | Needs its own liveness signal, a tray icon state, a periodic "helper unreachable" check surfaced somewhere the user will actually see it, not just a swallowed error |
| Leaked or intercepted local API token (Phase 3, sub-option a only) | Any device that obtains the token can trigger recording or read whatever the token is scoped to, given the LAN-reachable bind address | Scope the token narrowly (see auth section), don't log it, store hashed at rest, provide a one-click regenerate |
| Selection toolbar fires on unintended selections (e.g., drag across the whole page, selection spanning outside the transcript container) | Toolbar appears in the wrong place, or offers "ask AI" on garbage text | Draft plan already flags this: confine the `mouseup` handler's selection check to the transcript container and guard collapsed/empty selections |
| Assistant-job framing for "summarize selection" produces a worse result than a dedicated prompt would | Users notice summaries feel generic or miss the point of the selection versus the full transcript | Worth a manual quality spot-check before assuming the shared assistant-job prompt handles both "answer a question" and "summarize this passage" equally well; if it doesn't, that's the strongest argument for a dedicated job kind down the line |
| Cross-platform expectation mismatch | User on macOS/Linux installs WhisperDeck, expects the same tray-helper hotkey experience described for Windows, files a bug when Accessibility permissions or Wayland restrictions block it | Document Phase 3 as Windows-only explicitly, from the first release note, not discovered after a support request |

## Recommended MVP slice

The in-page hotkey (Phase 1 in the current numbering: selection toolbar; Phase 2: in-page hotkey) is the cheap, safe half of this feature, matching the draft plan's own phasing:

- No schema changes.
- No new auth surface.
- No new job kinds, both the toolbar's LLM actions and the hotkey's recording path reuse existing endpoints (`/api/assistant`, `startLiveCapture()`/`loadTape()`).
- Real, immediately testable UI surface: selection detection, floating toolbar, keydown guard logic.
- Ships value ("capture without opening a modal," "ask AI about a highlighted passage without leaving the page") even though it doesn't fulfill the full "from anywhere, browser closed" promise.

This MVP slice is worth shipping and evaluating on its own before any Phase 3 native-helper work starts, both because it's independently useful and because it's the cheapest way to learn whether users actually reach for a quick-capture hotkey at all before investing in the native-process, cross-platform, token-auth complexity that Phase 3 requires.

## Later phases (not resolved here)

- **Phase 3, Windows-only native tray helper**: token/auth design, helper-triggers-tab as the default sub-option, helper-records-itself only if "capture without a browser window open" proves to matter.
- **macOS support**: a distinct follow-on, not a checkbox on the same ticket; different permission model, different onboarding UX, should get its own scoping pass once Windows Phase 3 has real usage data.
- **Linux/Wayland**: likely partial or DIY-only (option B), document the ceiling rather than promising parity.
- **Translate selection**: possible fourth toolbar action, likely needs its own job-kind evaluation if assistant-job framing underperforms.
- **Quick-capture's own `Transcript.kind`**: revisit if a "was this a hotkey misfire" filter or different default processing (lighter diarization, different summary prompt) becomes desirable.

## Decisions needed from the human

These are left open on purpose, brainstorm layer doesn't resolve them:

1. Silent auto-file vs. a lightweight "recording... cancel?" indicator vs. full preview-before-save, for what quick-capture does with its result.
2. Reuse `kind="voice_note"` for quick captures, or introduce a distinct `kind="quick_capture"` value now, to leave room for different defaults or misfire cleanup later.
3. Whether translate belongs in the selection toolbar's initial action set, or is a later addition once the initial three actions are validated.
4. Single per-user token column vs. a full token table for Phase 3's local API credential, given there's currently exactly one anticipated caller (the helper).
5. Helper-triggers-tab vs. helper-records-itself as Phase 3's starting sub-option (the auth/token work is only required for the latter).
6. Whether Phase 3 is pursued at all, given it means shipping and maintaining a second always-running background process, versus staying with Phase 1/2's in-app-only scope and documenting the DIY daemon recipe (option B) as the answer for anyone who wants a true global hotkey.
7. If Phase 3 is pursued, whether it is scoped to Windows only for the foreseeable future, or macOS/Linux support is a stated (even if later) commitment.
