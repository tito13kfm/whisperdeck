# token-usage.md

Live log of where the run spent tokens. I update as I go, not
backfill at the end.

## Sub-agents and cloud dispatches

This run is the orchestrator (Sisyphus/MiniMax-M3 in the A/B
abbreviation). The run dispatched:

- **0 cloud agents** (no `deep`, `ultrabrain`, `oracle`, or
  `librarian` calls). Every investigation was a direct tool call
  (codegraph + grep + read), every test was a local pytest, every
  edit was applied by this main thread.

- **1 local (Lemonade) background agent** — the `explore` agent
  doing the sibling-sweep. Dispatched to the model
  `lemonade/Qwen3.5-4B-MTP-GGUF` (the `explore` key in
  `~/.config/opencode/oh-my-openagent.json`). Local GPU cost, no
  cloud spend. The dispatch count was 1 (under the 2-cap).

  The agent spent 7m46s on a broad cross-file scan, struggled with
  Windows path quoting on broad `*.py` greps (60s timeouts), and
  produced a partial report. Cost-benefit: it surfaced the
  `/api/transcripts/{id}/runs/{kind}` hardcoded kind allowlist
  (app.py:2128) that my own sibling-sweep had not enumerated. That
  one catch paid back the dispatch.

  For future issue runs of similar surface area, budget ~8m for
  this kind of local-agent sweep and expect ~50% of the result
  surface to be useful.

## Codegraph calls

Three `codegraph_explore` calls against the main repo path
(`C:\Claude\whisperdesk`):

1. **LlmJob / Transcript / classify_intent / reformatting** — 12
   files. Returned the full schema + the `classify_intent` body +
   the LlmJob model. Replaced 4-5 grep+read cycles.

2. **enqueue_llm_job / run_llm_job / worker pipeline** — 15
   files. Returned the VALID_KINDS/AUTO_RETRY_KINDS/IO_KINDS
   constants and the full if/elif dispatch. Replaced 3-4 grep+read
   cycles.

3. **dictation / single-speaker / transcription** — 10 files. The
   query was looser ("dictation kind upload recording UI") and
   codegraph returned formatDur / transcriptMeta JS helpers as
   tangential hits. The body of the result was useful for the
   upload-route context but I would have been more efficient with
   a tighter question.

Total: 3 codegraph calls replaced ~10-15 grep+read cycles I would
otherwise have needed. Net positive.

## Direct file reads

After codegraph had the architecture, the rest of the work was
direct file reads of:

- `database/__init__.py` (1 read, full)
- `services/llm_jobs.py` (1 read, full, before editing)
- `services/reformatting.py` (1 read, partial — for the pattern
  reference)
- `services/transcription.py` (1 read, partial — for the summarize
  function context)
- `app.py` (3 reads, partial — for upload, PATCH, format,
  rediarize, voice-match gates)
- `static/rack.js` (5 reads, partial — for mode toggle, tabs,
  upload form, kind display)
- `tests/test_reformatting.py`, `tests/test_llm_jobs.py`,
  `tests/test_serialize_transcript_contract.py` (1 read each,
  for the test patterns and the contract-pinning test)

All reads were targeted at specific lines, not the full file
unnecessarily. Codegraph returned verbatim line-numbered source for
the first two reads' worth of investigation, so those didn't
happen.

## Edits

10 files modified or created. Edit count by file:
- `database/__init__.py` — 4 edits (column comment, relationship,
  model class, `__all__`)
- `services/llm_jobs.py` — 4 edits (VALID_KINDS / AUTO_RETRY_KINDS
  / IO_KINDS, enqueue helper, dispatch branch, import line)
- `services/voice_notes.py` — 1 write (new file)
- `app.py` — 8 edits (kind allowlist at upload, PATCH, runs
  endpoint; import; pipeline force-off; post-pipeline enqueue
  branch; format route 400; rediarize + voice-match gates;
  serializer; voice-note routes; summarize branch)
- `static/rack.js` — 6 edits (mode toggle, syncTranscribe render,
  upload form, format tab guard, Notes tab body, kind label,
  detail-tab guard)
- `tests/test_serialize_transcript_contract.py` — 1 edit
  (EXPECTED_KEYS)
- `tests/test_voice_note_chain.py` — 1 write (new file)
- `tests/test_voice_note_route.py` — 1 write (new file)
- `README.md` — 1 edit (API table)

Edit iteration count includes intermediate failures that I rolled
back or rewrote — notably the diarize-force-off test (had to drop
the diarize_requested override from the stub) and the
unsupported-provider test (had to change the assertion from
"raises" to "returns DEFAULT_NOTE_TYPE" once I realized the
never-raise contract). 2 of 8 app.py edits needed rework after
the first run failed.

## Tests run

- 2 new test files (15 + 16 = 31 tests, all passing)
- 1 contract test updated (4 tests, all passing)
- Full suite: 419 tests passing, 0 failures

The contract test failure caught me by surprise — adding the 3
new serializer fields (`voice_note`, `voice_note_job`,
`has_voice_note`) is exactly the kind of change the contract test
is supposed to flag. Good sign that the test was load-bearing
here.

## What would cut it next time

1. **Don't write the `diarize_requested` override in the upload
   stub.** The stub should not pre-set fields that the pipeline
   is responsible for. Cost: 1 failed test, 1 edit-undo cycle.

2. **Don't assert `pytest.raises(ProviderError)` on
   `classify_voice_note`.** It's a never-raise function (by
   design, matching `classify_intent`). The matching
   test_format_function_raises_on_unsupported_provider in
   test_reformatting.py is for `format_as_markdown`, which DOES
   raise. The two-step voice-note classifier is intentionally
   different. Cost: 1 failed test, 1 edit-undo cycle.

3. **The `voice_note` worktree path is not codegraph-indexed.**
   I should have used the main repo path (`C:\Claude\whisperdesk`)
   for the first codegraph call instead of
   `C:\Claude\whisperdesk-minimax-m3-169`. The first call returned
   "no .codegraph/ directory" and wasted 1 round-trip. Already
   noted in wrong-directions.md.

4. **The local explore agent took 7m46s on a 60s-timeout-bound
   `*.py` grep.** A narrower prompt ("find every hardcoded
   LlmJob.kind allowlist in app.py" instead of "scan everything")
   would have finished in <2m. The broad prompt's value was
   catching the /runs/{kind} site; a targeted prompt would have
   missed that. Net wash, but worth knowing.

## Total time / cost

- This run: ~45m wall-clock from Phase 0 to push, plus 7m46s
  for the background explore agent (which ran in parallel with
  Phase 1's manual sweep, so it didn't add wall-clock time but it
  did burn local GPU).
- 0 cloud-token spend (all dispatches were direct tool calls or
  local-only agents). All code generation was done by this
  orchestrator thread.
- This run's cost lives in the OpenCode usage panel under the
  MiniMax-M3 model ID, not in any local spend panel. The
  reminder in the canonical prompt applies.
