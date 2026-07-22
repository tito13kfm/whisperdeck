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

## 2026-07-22

### Observation 17: Observation numbering collided after archival; duplicate re-log surfaced as OPEN

**Status:** ACTIONED — resolved in place: numbers 15 and 16 are reserved as CLAUDE.md rule tags (see below), numbering resumes at 18 (scheduled review 2026-07-22)
**Date:** 2026-07-22
**Session context:** Scheduled comprehensive review (autonomous)
**Skill:** task-observer
**Type:** internal
**Phase/Area:** Observation numbering / archival-on-write

**Issue:** The active log held Observation 10 as an exact duplicate of the archived, already-ACTIONED Observation 10 (rule live as wd#10), and Observations 11 and 12 (dated 2026-07-20) reused numbers already claimed by archived observations (docs-rewrite rule wd#11, scheduler-cadence rule wd#12). A prior session evidently numbered from log.md alone after archival had emptied it, despite the skill's scan-archives-too rule. Separately, Observation 14 was archived and removed on 2026-07-20, then reappeared in the active log (a later session apparently rewrote log.md from stale content, which also explains the duplicate Observation 10 re-log) and was archived again by copy on 2026-07-21 without removal; this run removes it from the active log a second time (archive/log-2026-07-22.md).

**Suggested improvement:** No new rule needed; the task-observer skill already mandates scanning archive/*.md in the pre-logging max-number step and moving (not copying) entries on archival. This entry records the collision resolution: CLAUDE.md tags wd#15 and wd#16 belong to the duplicate-numbered 2026-07-20 Observations 11 and 12 respectively; observation numbers 15 and 16 are therefore retired, and the next observation number is 18.

**Principle:** An append-only ID scheme survives archival only if the max-scan covers every location IDs can live; any copy-without-remove archival turns "move" into silent duplication.
