# WhisperDeck Worklist — Top 10 Open Issues

Difficulty-weighted, independently implementable. Items marked `[DECIDE]` need a one-sentence design call before starting.

## Easy wins (validate pipeline)

- [ ] **#126** — chunk file cleanup: Add chunk file deletion in `_finalize_if_done` and `delete_transcript`
- [X] **#193** — populate_fts() N+1: Replace per-row loop with anti-join + batch INSERT in `database/__init__.py`
- [ ] **#120** — diarization failure feedback: Set `diarization_method='failed'` on exception in `_finalize_if_done`

## #101 — API key lifecycle (4 sub-bugs)

- [ ] **B1** — deduplicate key resolution: Replace `queue.py:622-639` inline decrypt with `resolve_provider_key()` call
- [ ] **B2** — silent decrypt failure: Log warning instead of returning ciphertext as plaintext in `_decrypt_key_if_needed`
- [ ] **B3** — encrypt/decrypt tests: Add unit test for roundtrip + different-secrets in `test_security.py`
- [ ] **B4** — cache session_secret: Read `.session_secret` once at module level in `settings.py`, not per call

## Medium (design decisions needed)

- [ ] **#127** — silent audio pre-check: Run ffmpeg volumedetect on each chunk, skip silent chunks before dispatch
- [ ] **[DECIDE]** **#115** — correction boundary misalignment: Pick option A/B/C, then fix context window / dedup logic in `correction.py`
- [ ] **[DECIDE]** **#102** — audio double-count: Pick option A/B/C, then fix `compute_audio_seconds_used` + add regression test

## Hard

- [ ] **[DECIDE]** **#103** — concurrent dispatch race: Pick option A/B/C, then add per-user lock or pre-commit reservation + test
- [ ] **#194** — E2E search tests: Write `test_search_ui.py` with 6 scenarios using existing Playwright fixtures
- [ ] **#100 Phase 1** — bulk import backend: Add `batch_id` column + `POST /api/bulk-transcribe` endpoint + tests

---

### Design decisions (resolve before implementing)

| Issue | Options |
|-------|---------|
| #115 | A: Increase context lines to 5-8 / B: Full prior batch + dedup / C: Overlap raw input + dedup |
| #102 | A: Subtract job-seconds / B: Transcript-only tracking / C: Exclude by updated_at window |
| #103 | A: per-user asyncio.Lock / B: Pre-commit reservations / C: Post-dispatch rollback |

### Running tally

Done: 0/13
