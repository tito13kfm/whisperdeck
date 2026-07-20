# Skill Observation Log

Observations captured during task-oriented work. Each entry identifies a
potential skill improvement or new skill opportunity.

**Status key:** OPEN = not yet actioned | ACTIONED = skill updated/created |
DECLINED = user decided not to pursue | ESCALATED = needs a user decision;
stays in the active log (never archived) until resolved to ACTIONED or DECLINED

---

## 2026-07-10

### Observation 14: HEAD-based blob staging cleanly splits your hunks from foreign uncommitted work in the same file

**Status:** ACTIONED — Applied to CLAUDE.md "Verification gates that temporarily modify a file" section as (wd#14) (scheduled review 2026-07-13)
**Date:** 2026-07-10
**Session context:** PR 3 of the code-review fixes needed two README edits while the working tree README carried 31 uncommitted lines of someone else's in-progress screenshot work. Interactive `git add -p` is unavailable in the harness, and stash was ruled out (prior incident leaked uncommitted content).
**Skill:** verification-before-completion, superpowers:finishing-a-development-branch
**Type:** open-source
**Phase/Area:** Committing when the working tree contains unrelated uncommitted changes to the same file

**Issue:** Needed to commit only my hunks of a co-modified file without touching the other work. Solution that worked cleanly: (1) apply my edits to the working-tree file so both change sets coexist there, (2) extract the HEAD version to a temp file (byte-exact via bash redirect), (3) apply the same edits to the temp copy, (4) `git hash-object -w --path=<file>` (the --path applies clean filters, keeping line-ending conversion correct) then `git update-index --cacheinfo 100644,<hash>,<file>`, (5) plain `git commit` of the staged index. Result verified: committed diff contained exactly my hunks; post-merge worktree diff was exactly the foreign 31 lines.

**Suggested improvement:** Candidate rule for the CLAUDE.md git-workflow area or the finishing-a-development-branch skill: when a file you must change carries foreign uncommitted modifications, never stash, never commit the whole file; edit both the worktree AND a HEAD-derived temp copy, stage the temp copy via hash-object --path + update-index --cacheinfo, and verify both directions after commit (staged diff = only your hunks; worktree diff = only the foreign hunks).

**Principle:** Search output is a lossy view of a file; only a byte-faithful Read is evidence. Verify surprising content at the source before treating it as a defect.

### Observation 10: Compound UI action — failure in step B suppressed feedback for already-committed step A
**Status:** OPEN

**Date:** 2026-07-04
**Session context:** WhisperDeck speaker-naming feature; rename-then-enroll flow in the transcript detail screen
**Skill:** verify (also frontend-design / any UI-flow skill)
**Type:** open-source
**Phase/Area:** Verification of multi-step UI flows

**Issue:** The rename-speaker flow chained two server calls in one try block: rename (committed server-side) then voice enrollment (failed on this machine's broken embedding backend). The catch swallowed the post-action view refresh, so the UI kept showing the OLD speaker labels even though the rename had landed. Unit tests passed (each endpoint correct in isolation); only driving the compound flow live with step B forced to fail exposed it.

**Suggested improvement:** Add to the verify skill's checklist: for any UI action that chains multiple mutations, exercise the flow with each later step forced to fail and assert the UI still reflects every earlier step that committed. Structure code so refresh/feedback for step A does not live inside step B's failure path.

**Principle:** In a compound action, each committed step's user feedback must be independent of later steps' outcomes — one shared try/catch around sequential mutations silently converts a partial success into an apparent no-op.

### Observation 11: Orphan classification only excluded "active" job states, missing revivable ones

**Status:** OPEN
**Date:** 2026-07-20
**Session context:** Review of draft PRs #39/#40 (video playback + file inventory/cleanup)
**Skill:** review (also relevant to writing-plans for any cleanup/GC feature)
**Type:** open-source
**Phase/Area:** correctness analysis of delete/cleanup endpoints

**Issue:** A file-cleanup endpoint classified upload-dir files as "orphaned" (deletable) unless referenced by a job with status pending/running. But the job system also has "failed" (auto-retried after a backoff window, plus a manual retry button) and "cancelled" (resumable) states — files for those jobs are still needed, yet classified orphaned and deletable, silently breaking retry/resume.

**Suggested improvement:** When reviewing any garbage-collection / cleanup / orphan-detection feature, enumerate the FULL state machine of the resource's consumers and ask, for each state, "can this state ever transition back to needing the resource?" Protect everything not strictly terminal, not just currently-active states.

**Principle:** "In use" means "reachable by any future transition," not "active right now." Cleanup logic keyed to active-state lists rots the moment a retry/resume/backoff path exists; audit against the state machine, not the status quo.

### Observation 12: New API field fed to an existing frontend formatter with a different serialization convention

**Status:** OPEN
**Date:** 2026-07-20
**Session context:** Browser verification pass on PR #40 (Files page)
**Skill:** verify (also review; instance of the CLAUDE.md mirror-paths rule)
**Type:** open-source
**Phase/Area:** frontend rendering of new backend fields

**Issue:** A new endpoint emitted a timestamp as tz-aware isoformat ("+00:00" suffix) while every existing timestamp in the app is naive-UTC isoformat. The frontend's shared timeAgo() helper appends "Z" itself, so the new field rendered "NaNd ago" on every row. All backend tests passed — only the real-browser pass surfaced it, since no test asserted the serialization convention and the formatter lives client-side.

**Suggested improvement:** When a change introduces a new backend field consumed by an existing frontend helper (formatter, parser, sorter), check the helper's expected input convention against the new field's actual serialization, and add a backend test pinning the convention. Real-browser passes catch this class; HTTP-only tests don't.

**Principle:** A shared client-side helper encodes an implicit serialization contract; every new producer feeding it must be checked against that contract, not just against "returns a valid value."
