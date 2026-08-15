# Release plan — 0.9 (soon) and 1.0 (full vision)

Date: 2026-08-15
Status: Planning only, no code changed. Approved split: 0.9 = stable foundation, 1.0 = your bigger goal.

## Where we are

- Main app on `master` is clean. The big "golden path" fixes (search corruption, speaker labels, long-meeting garbling, retry loops) are all shipped and merged (#309, #192, #122, #115, #328, etc.).
- 70 open issues remain. Most are intentional Future ideas, not 0.9 blockers. Worktrees cleaned up (5 merged branches removed Aug 15).
- `worklist.md` is out of date. Use this doc instead.

## What 0.9 and 1.0 mean

**0.9 — very soon. No big new features.**
Record or upload, get a good transcript with speakers, corrections, and keyword search, and it doesn't leak or leave junk behind. Just small fixes so you can tag it and tell someone "try it" without being embarrassed.

**1.0 — the app you actually want.**
Two new systems on top of 0.9:
1. "I ramble into the mic and it breaks it down and classifies it for me"
2. Search by meaning, not just exact words ("ask your meetings")

Both were greenfield before the audit. The search bug that would have blocked them is now fixed, so they are safe to build.

---

## Quick plain-English explanations

**Embeddings (for semantic search #218)**
Today search only finds exact words. If you search "grocery run" it only finds "grocery run."
Embeddings turn every sentence into numbers that capture meaning. Then "grocery run" finds "buy milk" because they mean the same thing, even though no words match.
To do that we need: a small AI model that makes those numbers (runs locally or via API), and a place to save them (a new table in the database). That's all #218 is.

**Ramble classifier (for #245 and friends)**
You record one long ramble. We split it into smaller pieces, and each piece gets its own label (task, idea, note, etc.). You asked for two things here — both are yes:
- One ramble can have many different topics. Not one label per recording.
- The list of labels can grow later without rebuilding the system.

**Real queue (for #103)**
Today the app can accidentally send two jobs at the same time and go over the rate limit.
You said you want a real queue you can cancel, pause, skip, or reorder. We will build that in 1.0. Doesn't have to have every button on day one, but the design should allow it.

---

## 0.9 checklist — do these in this order

Only 7 small fixes. Nothing here blocks anything else, so you can do them one at a time (calmest) or in parallel (fastest).

| Order | Issue | Plain English | Can run at same time as... | Depends on |
|---|---|---|---|---|
| 1 | #123 | Logging in keeps the old session (security) | 2 | nothing |
| 2 | #303 | Transcribe skips a security check when a header is present | 1 | nothing |
| 3 | #128 | Re-transcribing leaves old audio chunks on disk | 4,5,6,7 | nothing |
| 4 | #300 | Deleting a transcript leaves database rows behind | 3,5,6,7 | nothing |
| 5 | #118 | Speaker count 0 handled two different ways | 3,4,6,7 | nothing |
| 6 | #114 | Hotwords pasted raw into the LLM prompt (no escaping) | 3,4,5,7 | nothing |
| 7 | #116 | Empty transcript still calls the LLM (wastes money, can hallucinate) | 3,4,5,6 | nothing |

**How to run them:**
- Calmest: 1 then 2 (both auth, same files), then 3, 4, 5, 6, 7 one by one.
- Fastest: 1+2 together in one branch, and 3, 4, 5, 6, 7 in separate branches at the same time.
- After these 7, tag 0.9. Don't add anything else to 0.9.

What we leave out of 0.9 on purpose: UI polish (#133-137), performance tweak (#113), and everything marked Future.

---

## 1.0 — the two tracks

They can run side by side. Neither blocks the other.

### Track A — Ramble classifier (your "talk and it sorts it" goal)

This is the entity/voice-dump work: #245 -> then #241, #247, #248, #249, #251, #253.
Plus the half-built voice dump pieces #296, #312, #315, #318 should be finished first.

Your decisions captured here:
- One long ramble gets split into smaller chunks. Each chunk gets its own label.
- Labels are expandable. Start small (maybe task / idea / note / reminder / question) and add more later without a rebuild.

Dependency: Everything in Track A waits on #245. You can't build the UI (#247) or topic grouping (#248) until #245's database tables and background job exist.

### Track B — Search by meaning

#218 semantic search, then #242 ask-your-meetings Q&A on top of it.
Old blockers (#309, #192) are already fixed, so no waiting.

Dependency: #242 needs #218 done first. Nothing else.

### Track C — Real queue

#103 (concurrent dispatch) plus the smaller queue pieces.
Your decision: build a real queue with cancel/pause/skip/reorder ability, not just a simple lock.

Dependency: independent from Tracks A and B.

### Small cleanup (whenever)

#404, #403, #406, #417, #405 — duplicated code found by audit. No dependencies. Do when someone has a free hour.

### Explicitly not 1.0

About 20 issues marked `Future:` (#195-203, #219-224, #227, #236-239, #263, plus the container #196, #322). Audit said defer. Keep them as 2.0.

---

## Big decisions made (so the next session doesn't stall)

1. **#218 embeddings:** Need a local model vs API call decision, but both work. Default idea is local (private, no internet needed) unless you say otherwise. Confirm with one sentence before building.
2. **#245 ramble style:** One ramble = many labeled chunks, not one label per recording. Label list is expandable.
3. **#103 queue:** Real queue with cancel/pause/skip/reorder, not a simple lock.

---

## Handoff notes for next session

- Repo is on `master` at 31d9fc2, clean. Worktrees: none (5 merged branches cleaned up Aug 15 — #263, #304, #308, #369, #391).
- 70 open issues (see `.omo/runs/open-70.txt` dump from this session).
- `worklist.md` is stale — do not use. `studio-framing.md` and `server-client-mobile-expansion.md` are marked exploratory, not scheduled.
- No code was changed this session. Next step is to pick the first 0.9 issue (#123) and start a branch for it, or jump to Track A/B design for 1.0 if you want to design first.

## Order to tackle everything (one list)

If you want one straight line:

1. 0.9 fixes #123 + #303 together
2. 0.9 fixes #128, #300, #118, #114, #116 (any order, can be parallel)
3. Tag 0.9
4. Then start 1.0 Track A (#245) and Track B (#218) in parallel
5. Track C (#103 queue) in parallel with those
6. Finish Track A follow-ups (#241, #247, etc.) and Track B follow-up (#242)
7. Cleanup #404, #403, #406, #417, #405 whenever
