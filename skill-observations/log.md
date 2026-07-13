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

**Principle:** The index is writable independently of the working tree. Any "commit only part of a co-modified file" problem decomposes into building the desired blob out-of-tree and pointing the index at it, which is deterministic and reviewable, unlike interactive hunk selection, and never puts the other party's work at risk the way stash or checkout does.
