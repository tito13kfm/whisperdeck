# Token usage - issue #147 run, variant `minimax-m3`

(Reminder: per the A/B test override, the run's real cost numbers live
in OpenCode's own usage panel, not anything this file can read. This
file tracks which actions cost tokens, not how many cents they cost.)

## Where tokens were spent

### 1. Reading app.py and the surrounding helpers

- 5 direct `read` calls against `app.py` (offset/limit windows covering
  the serializer, the dictation helper, the list endpoint, the
  call sites at 1050, 1105, 1179-1191, 1435, 1620, 1665, 1705, 1750)
- 1 direct `read` against `services/llm_jobs.py` (full file, 499 lines)
- 1 direct `read` against `services/queue.py` (window 140-189, the
  `compute_queue_status` body)
- 1 direct `read` against `database/__init__.py` (LlmJob model)

These reads were unavoidable - the prompt required verifying the
issue's claims against current code, and the code is small enough that
direct reads are cheaper than an agent dispatch. The local-agent cap
(2 simultaneous) made it sensible to NOT spend one of my 2 agent
slots on a "read app.py and report X" task I could answer in a single
Read.

### 2. Greps for call sites

- 1 grep for `_serialize_transcript` (found 68 matches in 13 files,
  most of which were in `docs/superpowers/...` historical plans -
  useful to confirm the historical "list endpoint used full
  serializer" assumption was real and dated to before #158)
- 1 grep for the 4 helper definitions (`latest_job`,
  `compute_queue_status`, etc.)
- 1 grep for `latest_job\(` to confirm no remaining callers
- 1 grep for `_serialize_transcript\(` after the edit, to verify
  all 7 sites were updated

These were cheap (each was 1 call returning <20 lines after filtering)
and each saved a wider search later.

## What I'd cut on a re-run

### a. The "list endpoint claim" verification

The biggest avoidable cost was disproving the issue's "tape library is
slow" claim. That required reading `_serialize_transcript_summary` AND
`list_transcripts` AND the dictation helper's docstring AND the
git history (1f09967) to confirm that the split happened in a known
PR. If the issue template required authors to grep for the function
the issue claims is the N+1 source before submitting, this whole
verification could be skipped. Worth proposing as a template change.

### b. The unused-import round-trip

I added `func` to the sqlalchemy import, then later noticed
`latest_job` was no longer used in app.py and removed it. Each of
those edits was a single-line Edit call, but the round-trip
(write the helper, realize latest_job is now dead, remove the import)
could have been collapsed if I had grepped for `latest_job\(` at the
top of the change instead of in the middle. Cheap to fix next time.

## What went right

- The static contract test (`test_serialize_transcript_contract.py`)
  caught the "expected key set" hand-trace once for the username
  collision in the "meeting vs dictation" test. Cheap fix, no agent
  round-trip needed.
- Running the full unit suite once after each non-trivial edit
  (4 runs total) caught nothing the contract test would have caught
  but gave a clear "no regression" signal. Cost: ~40s per run, but
  the per-run budget is way under the static-check budget the prompt
  called out.
- No `codegraph_explore` calls. The codebase is small enough that
  direct reads + greps answered everything. Codegraph is a better
  tool when the search would fan out across 8+ files; here the
  blast radius was 1 file.
