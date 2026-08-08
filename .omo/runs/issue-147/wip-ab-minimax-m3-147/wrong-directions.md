# Wrong directions - issue #147 run, variant `minimax-m3`

## Issue body claims that didn't match current code

### 1. "For the tape library listing 100 transcripts, that's 500-800
individual DB queries per request"

The tape library endpoint is `GET /api/transcripts`, which routes through
`_build_recent_transcripts` (app.py:498) -> `_serialize_transcript_summary`
(app.py:456). The summary function does **not** call `latest_job()` at
all, so the 5-8 LlmJob queries per row the issue describes don't fire
on the list endpoint today. The N+1 was already resolved (intentionally
or not) when the summary/full split was introduced - see
`1f09967 perf(api): make list/dashboard transcript payloads lightweight`.

`_serialize_transcript` itself is only called from single-transcript
endpoints (7 call sites, see investigation.md). The fix is still
worthwhile: 3-7 per-row queries collapse to 2 SQL statements, and the
function is now safe to use from any future list endpoint without
re-introducing an N+1. But the issue's "Impact" framing - that the tape
library is slow right now - is wrong.

**Recommended issue-template fix:** issues in this tracker should require
the author to re-verify the N+1 against current code (e.g. `grep -n
"latest_job|compute_queue_status" app.py`) before submission. The
stale "for the tape library" line and the stale line number
(`app.py:229`, current is 298) are both the same root cause: the issue
text was written against an older revision and never re-checked.

### 2. "List endpoint uses batch job fetching" (acceptance criterion)

This is a non-applicable criterion given #1 above. The list endpoint
doesn't use `_serialize_transcript` and so has no N+1 to fix. The
criterion reads as if it were written assuming the list endpoint called
the full serializer, which it never did.

I left the batch-query fix in place (it speeds up the 7 single-transcript
endpoints and future-proofs the function), but I cannot tick the
"list endpoint uses batch job fetching" box - the list endpoint doesn't
make the per-row LlmJob calls the criterion assumes it does.

## Stale code references found while investigating (not fixed, just noted)

- `_dictation_job_fields` docstring (was app.py:320, now app.py:351) used
  to say "Matters because _serialize_transcript runs per-row in
  list_transcripts (up to 50 rows)". That was true before the
  summary/full split landed in #158 but is no longer true. The current
  version of the function I shipped has a shorter, accurate docstring.
  If there are other stale docstrings in app.py with the same
  assumption, they should be re-grepped.

## AGENTS.md / doc accuracy check

Per the prompt, I checked `~/.config/opencode/oh-my-openagent.json` and
the project-level override before deciding which agents are local vs
cloud. I did not need to launch any local explore agent for this issue
(the investigation fit comfortably in a direct read of app.py,
services/llm_jobs.py, services/queue.py, and the LlmJob model), so the
local-vs-cloud distinction didn't gate any agent choice. No
AGENTS.md-vs-actual-config discrepancy hit my run.

## Hook feedback worth remembering

The "agent memo comment" hook fired twice during this run - once on
my first version of the helper (which had several "what changed" notes),
once on a test that documented `enqueue_llm_job`'s dedup behavior. The
memo comments were removed on the first pass; the test comment was
genuinely necessary (without it, the `older.status = "completed"`
line is unmotivated) and I kept it. Future runs in this project should
expect the hook and pre-strip "what changed / replaced / now uses"
wording from any new comments.
