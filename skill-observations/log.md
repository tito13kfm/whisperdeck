# Skill Observation Log

Observations captured during task-oriented work. Each entry identifies a
potential skill improvement or new skill opportunity.

**Status key:** OPEN = not yet actioned | ACTIONED = skill updated/created |
DECLINED = user decided not to pursue | ESCALATED = needs a user decision;
stays in the active log (never archived) until resolved to ACTIONED or DECLINED

---

## 2026-07-10

### Observation 10: Compound UI action — failure in step B suppressed feedback for already-committed step A
**Status:** ACTIONED — duplicate re-log of archived Observation 10 (2026-07-10 archive); rule already in CLAUDE.md "Verifying against a live app" as (wd#10), no new edit needed (scheduled review 2026-07-22)

**Date:** 2026-07-04
**Session context:** WhisperDeck speaker-naming feature; rename-then-enroll flow in the transcript detail screen
**Skill:** verify (also frontend-design / any UI-flow skill)
**Type:** open-source
**Phase/Area:** Verification of multi-step UI flows

**Issue:** The rename-speaker flow chained two server calls in one try block: rename (committed server-side) then voice enrollment (failed on this machine's broken embedding backend). The catch swallowed the post-action view refresh, so the UI kept showing the OLD speaker labels even though the rename had landed. Unit tests passed (each endpoint correct in isolation); only driving the compound flow live with step B forced to fail exposed it.

**Suggested improvement:** Add to the verify skill's checklist: for any UI action that chains multiple mutations, exercise the flow with each later step forced to fail and assert the UI still reflects every earlier step that committed. Structure code so refresh/feedback for step A does not live inside step B's failure path.

**Principle:** In a compound action, each committed step's user feedback must be independent of later steps' outcomes — one shared try/catch around sequential mutations silently converts a partial success into an apparent no-op.

### Observation 11: Orphan classification only excluded "active" job states, missing revivable ones

**Status:** ACTIONED — Applied to CLAUDE.md "Verifying against a live app" as (wd#15); tag wd#11 was already taken by the archived 2026-07-10 Observation 11 (scheduled review 2026-07-22)
**Date:** 2026-07-20
**Session context:** Review of draft PRs #39/#40 (video playback + file inventory/cleanup)
**Skill:** review (also relevant to writing-plans for any cleanup/GC feature)
**Type:** open-source
**Phase/Area:** correctness analysis of delete/cleanup endpoints

**Issue:** A file-cleanup endpoint classified upload-dir files as "orphaned" (deletable) unless referenced by a job with status pending/running. But the job system also has "failed" (auto-retried after a backoff window, plus a manual retry button) and "cancelled" (resumable) states — files for those jobs are still needed, yet classified orphaned and deletable, silently breaking retry/resume.

**Suggested improvement:** When reviewing any garbage-collection / cleanup / orphan-detection feature, enumerate the FULL state machine of the resource's consumers and ask, for each state, "can this state ever transition back to needing the resource?" Protect everything not strictly terminal, not just currently-active states.

**Principle:** "In use" means "reachable by any future transition," not "active right now." Cleanup logic keyed to active-state lists rots the moment a retry/resume/backoff path exists; audit against the state machine, not the status quo.

### Observation 12: New API field fed to an existing frontend formatter with a different serialization convention

**Status:** ACTIONED — Applied to CLAUDE.md "Verifying against a live app" as (wd#16); tag wd#12 was already taken by the archived 2026-07-13 Observation 12 (scheduled review 2026-07-22)
**Date:** 2026-07-20
**Session context:** Browser verification pass on PR #40 (Files page)
**Skill:** verify (also review; instance of the CLAUDE.md mirror-paths rule)
**Type:** open-source
**Phase/Area:** frontend rendering of new backend fields

**Issue:** A new endpoint emitted a timestamp as tz-aware isoformat ("+00:00" suffix) while every existing timestamp in the app is naive-UTC isoformat. The frontend's shared timeAgo() helper appends "Z" itself, so the new field rendered "NaNd ago" on every row. All backend tests passed — only the real-browser pass surfaced it, since no test asserted the serialization convention and the formatter lives client-side.

**Suggested improvement:** When a change introduces a new backend field consumed by an existing frontend helper (formatter, parser, sorter), check the helper's expected input convention against the new field's actual serialization, and add a backend test pinning the convention. Real-browser passes catch this class; HTTP-only tests don't.

**Principle:** A shared client-side helper encodes an implicit serialization contract; every new producer feeding it must be checked against that contract, not just against "returns a valid value."

### Observation 15: Driving a DOM-diffed SPA via browser MCP produces false findings unless probes are page/state-scoped

**Status:** OPEN
**Date:** 2026-07-22
**Session context:** Deep UX audit of WhisperDeck (new e2e-ux-audit-deep skill); repeatedly measured wrong before correcting.
**Skill:** e2e-ux-audit, e2e-ux-audit-deep (also any browser-MCP-driven audit)
**Type:** open-source
**Phase/Area:** how to read SPA state via Playwright/UI-TARS browser_evaluate

**Issue:** Four separate false findings arose from naive DOM probes against WhisperDeck's single-page app: (1) every nav "page" stays mounted in the DOM, so `document.querySelector('h1')` and bare `[data-tid]`/`[data-seg-*]` selectors matched hidden pages and reported "landed on Monitor" / inflated row counts; (2) the modal container `#modal-box` is always present (emptied when closed), so checking for a `[role="dialog"]` element gave false "modal still open" readings — the real signal is `#modal-overlay.classList.contains('open')`; (3) toasts auto-remove after 4.2s, so snapshotting between tool calls missed them, producing false "no feedback" findings — must poll `#toast-wrap .toast` at click time inside one browser_evaluate; (4) marking/renaming re-renders the segment list, staling cached element refs.

**Suggested improvement:** Add a "Reading SPA state (pitfalls)" section to the browser-audit skills: scope every probe to `[id^="page-"].active`; scope row queries to their container; detect modals by the overlay's open-class not element presence; instrument toasts at action time by polling; re-query re-rendered lists rather than caching refs. These prevent the audit from generating retractions.

**Principle:** In a client-rendered app, "present in the DOM" ≠ "visible/active," and transient UI (toasts, re-rendered lists) can't be observed by after-the-fact snapshots. Audit probes must assert on the app's own visibility/state signals, captured synchronously with the action, or they measure the framework rather than the UX.

### Observation 16: Test harness can silently corrupt the system under test via a per-call-rotating token endpoint

**Status:** OPEN
**Date:** 2026-07-22
**Session context:** Deep UX audit; out-of-band GET /api/csrf-token bricked all in-browser mutations for ~15 min before diagnosis.
**Skill:** e2e-ux-audit-deep, e2e-regression-http (also any skill that configures the app via direct HTTP alongside a browser session)
**Type:** open-source
**Phase/Area:** setup / provider+settings configuration

**Issue:** WhisperDeck's `GET /api/csrf-token` overwrites the session's CSRF token on every call. The skill's setup configured providers via out-of-band HTTP that first fetched a token; this rotated the server token out from under the browser's boot-cached one, so every subsequent UI mutation failed 403 with only a 4.2s jargon toast. Time lost distinguishing "product bug" from "harness self-inflicted." (It is also a real product bug — a second tab bricks the first — filed as F1.)

**Suggested improvement:** In setup, perform mutations through the browser page's own `api()` helper (reusing its cached token) rather than a separate HTTP client, OR fetch the token once and reuse it for the whole config sequence, never re-calling the token endpoint mid-session. Document token-rotation endpoints as harness hazards in the skill's Pitfalls.

**Principle:** Before configuring an app out-of-band while also driving it in a browser, check whether any auth/CSRF/session endpoint mutates shared session state on read; if so, share one token source or drive everything through the same client, else the harness and the UI fight over session state and failures look like product bugs.

### Observation 17: A dependency the audit needs may be absent on the test machine and must be stubbed hermetically

**Status:** OPEN
**Date:** 2026-07-22
**Session context:** Deep UX audit; no local LLM (Lemonade) and zero configured API keys on the isolated instance, blocking all correction/summary/context journeys.
**Skill:** e2e-ux-audit-deep, e2e-ux-audit
**Type:** open-source
**Phase/Area:** setup / backend availability

**Issue:** The audit machine had no local LLM and the fresh isolated instance started with `has_key:false` on every provider, so LLM-dependent journeys (correction, summary, context, wrap-up flow) could not run at all as written. A tiny committed OpenAI-compatible stub (deterministic, offline, slow-by-design) unblocked every LLM path and additionally made progress-UI and cancel-race testing observable. One subtlety: the stub had to return the app's expected summary JSON schema, else the summary pipeline failed on a parse error and masked the actual UX being tested.

**Suggested improvement:** Ship a hermetic provider stub with the skill (committed to a tracked dir, not a gitignored fixtures dir), point the app's provider at it in setup, and teardown must kill it + free its port. Make the stub schema-aware for any endpoint that parses structured model output. Offer the operator the choice of real key vs stub, defaulting to the stub for repeatability.

**Principle:** An audit that depends on an external/heavy backend must own a hermetic stand-in so it runs anywhere and deterministically; the stub must satisfy not just the transport contract but every response *schema* the app parses, or it silently converts the feature-under-test into an error path.

## 2026-07-22

### Observation 18: Observation numbering collided after archival; duplicate re-log surfaced as OPEN

**Status:** ACTIONED — resolved in place: numbers 15 and 16 are reserved as CLAUDE.md rule tags (see below), numbering resumes at 19 (scheduled review 2026-07-22; merge-conflict renumber bumped this entry from 17 to 18, and origin's real Observation 17 above is unrelated and kept as-is)
**Date:** 2026-07-22
**Session context:** Scheduled comprehensive review (autonomous)
**Skill:** task-observer
**Type:** internal
**Phase/Area:** Observation numbering / archival-on-write

**Issue:** The active log held Observation 10 as an exact duplicate of the archived, already-ACTIONED Observation 10 (rule live as wd#10), and Observations 11 and 12 (dated 2026-07-20) reused numbers already claimed by archived observations (docs-rewrite rule wd#11, scheduler-cadence rule wd#12). A prior session evidently numbered from log.md alone after archival had emptied it, despite the skill's scan-archives-too rule. Separately, Observation 14 was archived and removed on 2026-07-20, then reappeared in the active log (a later session apparently rewrote log.md from stale content, which also explains the duplicate Observation 10 re-log) and was archived again by copy on 2026-07-21 without removal; this run removes it from the active log a second time (archive/log-2026-07-22.md).

**Suggested improvement:** No new rule needed; the task-observer skill already mandates scanning archive/*.md in the pre-logging max-number step and moving (not copying) entries on archival. This entry records the collision resolution: CLAUDE.md tags wd#15 and wd#16 belong to the duplicate-numbered 2026-07-20 Observations 11 and 12 respectively; observation numbers 15 and 16 are therefore retired. (Post-merge correction: this entry itself collided with origin's real Observation 15/16/17 from a concurrent session and was renumbered 17→18; the next observation number is 19.)

**Principle:** An append-only ID scheme survives archival only if the max-scan covers every location IDs can live; any copy-without-remove archival turns "move" into silent duplication.

### Observation 19: Complement Rule needs the inverse direction: state readers must be invalidated by every state rewriter

**Status:** OPEN
**Date:** 2026-07-22
**Session context:** Multi-agent review of PR #72 (issue #67 diarization, Phases 1-4)
**Skill:** review (also writing-plans; relates to AGENTS.md Complement Rule)
**Type:** internal
**Phase/Area:** cross-file tracing / plan design

**Issue:** The PR carefully applied the Complement Rule in the forward direction: all three relabel writers (rename, retag, voice-match) record RelabelHistory. But the review found the inverse miss: RelabelHistory entries are index-based snapshots of transcript.segments, and the sibling set of *segments rewriters* (rediarize job, queue finalize, inline diarize) never invalidates that history. Undo after rediarize stamps stale labels onto a new segmentation. Same shape: relabel_history rows are never cascaded on transcript delete (SQLite FK cascade inert, no ORM relationship), so SQLite id reuse can resurrect a dead transcript's undo onto a new one.

**Suggested improvement:** When a plan adds derived/snapshot state (history, cache, undo patch, denormalized field) keyed to mutable parent data, enumerate BOTH sibling sets: (a) writers that must produce the derived state, and (b) rewriters/deleters of the parent data that must invalidate it. Review finders should explicitly ask "who rewrites the data this snapshot indexes into, and do they invalidate it?"

**Principle:** Index-or-snapshot-based derived state has two complement sets, producers and invalidators; covering only producers yields silent corruption when any parent rewriter fires.
