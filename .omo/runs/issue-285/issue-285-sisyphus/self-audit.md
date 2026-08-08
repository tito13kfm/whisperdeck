# Self-audit: Issue #285 — Voice dump API endpoints + serialization

[x] add VoiceDumpItem to app.py imports — delivered, confirmed at app.py:30
[x] add enqueue_auto_voice_dump and latest_job to llm_jobs imports — delivered, confirmed at app.py:53
[x] add voice_dump to _SERIALIZED_JOB_KINDS — delivered, confirmed at app.py:322
[x] add voice_dump branch to _dictation_job_fields — delivered, confirmed at app.py:449
[x] enqueue_auto_voice_dump on inline transcription finalize — delivered, confirmed at app.py:1423
[x] add voice_dump to transcript_runs kind whitelist — delivered, confirmed at app.py:2770
[x] _serialize_voice_dump_item function — delivered, confirmed at app.py:2809
[x] POST voice-dump/rerun route — delivered, confirmed at app.py:2931
[x] POST voice-dump/save-draft route with full-column JSON reassign — delivered, confirmed at app.py:2962
[x] POST voice-dump/finalize route filtering discarded items — delivered, confirmed at app.py:2987
[x] GET voice-dump-items per-transcript route — delivered, confirmed at app.py:3033
[x] GET voice-dump-items board listing route — delivered, confirmed at app.py:3055
[x] enqueue_auto_voice_dump function in llm_jobs — delivered, confirmed at services/llm_jobs.py:227
[x] voice_dump in AUTO_RETRY_KINDS — delivered, confirmed at services/llm_jobs.py:35
[x] retroactive classify_pipeline triggers voice_dump — delivered, confirmed at services/llm_jobs.py:512
[x] queue.py chunked-finalize enqueues voice_dump — delivered, confirmed at services/queue.py:616
[x] 22 voice-dump route tests plus contract tests all passing — delivered, confirmed at tests/test_voice_dump_route.py:1
[x] no regression — voice_note route (20), serialize contract (9) all pass — delivered, confirmed at tests/test_voice_note_route.py:1
