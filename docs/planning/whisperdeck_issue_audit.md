# WhisperDeck open-issue audit: path to a stable capture → transcribe → diarize → correct → finalize → RAG-index → search flow

Audited: 88 open issues, via 8 code-verified investigations (not title-reading). Goal: separate what actually blocks a normal single-meeting run from what's polish or future scope. Report only — nothing in GitHub was touched.

## The one fact that reframes everything: RAG search doesn't exist yet

Semantic/vector indexing (#218) is **completely greenfield** — zero embedding/vector-store substrate for text anywhere in the codebase (the only `embedding` columns are speaker-voice embeddings for voice-ID, unrelated). What exists today is plain SQLite FTS5 keyword search, and it works for a fresh database.

But #218 and the "ask your meetings" Q&A feature (#242) are both explicitly designed to sit *on top of* that FTS5 layer. And the FTS5 layer has a real corruption bug:

**#309 is the most important fix in this entire backlog.** Delete a transcript and create a new one (an entirely ordinary action — re-record a meeting, remove a test upload) and SQLite reuses the freed rowid, because there's no delete trigger cleaning up the FTS index and no autoincrement on the primary key. Verified live: searching for the deleted transcript's term returned the *new* transcript's content, and a follow-up integrity check threw `database disk image is malformed` — actual index corruption, not a near-miss.

Building semantic search or "cite this line" grounding on top of that means the new feature tests clean on a fresh dev database and then silently misbehaves the first time anyone deletes anything in production. **Fix #309 (and #192, below) before starting #218 — this is a prerequisite, not parallel cleanup.**

## P0 — blocks the stated goal today, on the golden path (no unusual input, no concurrency, no opt-in feature required)

| # | What | Why it's P0 |
|---|---|---|
| **#309** | No AFTER-DELETE FTS trigger; rowid reuse | Confirmed FTS corruption + wrong-transcript search results after ordinary delete-then-recreate. Blocking prerequisite for #218/#242. |
| **#192** | FTS5 porter-stemmer search vs. literal-substring segment matching disagree | A normal plural/inflected query ("meetings") finds the transcript via FTS, then jump-to-segment shows "no segments match" on a transcript that genuinely matched. Also breaks #242's grounded-Q&A context assembly. |
| **#122** | Diarization heuristic (the default engine — no pyannote installed) only advances speaker index on a >1.5s gap | Any snappy conversation, back-and-forth Q&A, or overlapping speech collapses to "Speaker 1" for the whole meeting, silently, with no error. This is the default engine's everyday failure mode, not an edge case. |
| **#115** | Correction-stitch has zero boundary reconciliation between LLM correction batches (~6000 chars/batch, ~7 min of speech) | Correction runs unconditionally on every first meeting (local LLM, keyless, `auto_correct=True` by default). Any recording past ~7 minutes risks garbled/duplicated/dropped text written into `corrected_text` with `correction_error` cleared — no error surfaced. |
| **#328** | Automatic chunk retry (no user action, built-in backoff) re-triggers a full finalize pass | Wipes relabel history and fires duplicate paid LLM jobs on any transient chunk failure that resolves itself. |
| **#126** | Chunk files never cleaned up | Local chunking kicks in at 5 minutes; hosted chunking at 20MB. Any real meeting-length recording leaks chunk files forever — unbounded disk growth on a self-hosted, presumably-long-running instance. |
| **#127** | No pre-check for silent/empty chunks | Trailing dead air or a pause landing on a chunk boundary fails that chunk with a misleading provider error and silently degrades the whole transcript's status to "partial," with nothing telling the user *content*, not the provider, was the cause. |
| **#316** | Browser e2e suite is excluded from CI entirely (`pytest.ini` marks it `not e2e` by default) and fails outright when run manually (rate-limit bucket exhaustion across test files, empirically reproduced: 6 passed, 16 errors) | You're about to make eight changes to exactly the surfaces this suite covers (detail-view polling, voice-dump review, session/queue cleanup). Right now there is no working safety net protecting that work — green CI proves nothing about it. |

Not P0 by the same golden-path bar, but a cheap ride-along while you're in the same code: **#191** (dead `segment_text` column) only bites on segment-only search matches after correction/rediarization drift — real and user-visible when it happens, but it needs that drift first, so it doesn't meet the "no unusual input required" bar above. Fix it while you're already in the FTS code for #309/#192.

**Also cheap and directly addresses the maintainer's stated fear of "silent wrong speaker attribution":**

| # | What | Why it matters |
|---|---|---|
| **#311** | Voice-match computes per-speaker similarity, then throws it away | The one signal that would catch an over-matching embedding backend producing confident-looking wrong output is computed and discarded on every run. Surfacing an already-computed value — cheap. |
| **#321** | Undo-after-relabel has never been driven in a browser | #55 was closed specifically on the strength of "you can always undo." That claim is untested. Combined with #106/#107's known small undo bugs, the entire safety net for voice-match's known failure mode is unverified, not verified. One e2e test — cheap. |
| **#305** | Manual retag doesn't clear the stale `speaker_confidence` marker | Hits every meeting transcript (not voice_dump-specific): a user-corrected line still shows the "uncertain" badge, eroding trust in corrections that are actually right. |
| **#119 + #121** | Missing pyannote HF token → cryptic 401, and no heuristic fallback if pyannote fails | Compounding pair: the moment someone opts into pyannote for better quality but hasn't set a token yet (the single easiest first-setup mistake), diarization hard-fails with a confusing error instead of degrading gracefully. Only affects the pyannote-opt-in population, but for that population it's the very first thing they'd hit. |

## Security cluster (ranked third, per your instruction — after core-pipeline correctness and the e2e safety-net gap, ahead of voice-match trust work and everything else)

- **#124** (login rate limiter is IP-only) — conditional. Becomes a real defect (and a self-DoS: one user's failed logins lock out everyone) the moment this instance sits behind a reverse proxy without forwarded-header trust configured — which is the normal way to invite someone in remotely. Worth a five-minute check of your actual deployment.
- **Unfiled gap, bigger than #301:** there is no invite/registration gate at all. `/api/register` is open to anyone who can reach the port. #301's admin-race is a real but vanishingly small window; the actual risk on "day one of inviting someone in" is that a stranger doesn't need to win a race, they can just register first if the port is reachable before you're ready. Worth filing as its own issue.
- **#302** (bootstrap `local`/`changeme` bypasses password policy) — conditional. Only matters if this instance was ever migrated from a pre-multi-user version. One query answers it (check if a `local` user row exists with the default password).

## Free wins — already fixed, just needs closing

- **#130** (speaker-count input validation) — superseded. PR #228 replaced the old text input with a wheel selector restricted to Auto-detect or 1–12; nothing invalid is reachable anymore.
- **#297** and **#329** (verify_self_audit.py build-check bugs) — both fixed by PR #332, same day as filing.

## P1 — real, but off the golden path (concurrency, explicit secondary action, opt-in feature, or narrow config)

Transcription/jobs: #101 (key rotation only), #103 (needs 2 simultaneous same-provider dispatches), #114 (needs adversarial glossary/doc content), #116 (needs already-broken upstream input), #117 (cosmetic), #300 (explicit delete only), #314 (narrow), #244 (pure enhancement, not a bug).

Diarization: #118 (unreachable via shipped UI), #276 (doesn't touch the meeting flow today).

Voice-match/relabel: #106/#107 (undo-only edge cases), #113 (real perf tax on long meetings — worth doing, not correctness-blocking), #320 (design gap, forward path still works).

Capture: #128 (retranscribe-only, but confirmed worse than filed — can overwrite an old completed run's chunk files in place), #307 (opt-in cleanup filters off by default, or repeat same-basename upload), #310 (opt-in context-doc field only), #257 (only past 50/100 items — a scale issue, not correctness).

UI/a11y: #133 (cosmetic loading states), #134 (real a11y blocker for keyboard/screen-reader users on modals), #135 (a11y — color-only stage LEDs), #136 (rename control genuinely keyboard-dead; play/seek buttons already fine), #137 (rarely-touched settings panel), #229 (cosmetic VU-meter loss), #313 (cosmetic, but hits every run — tagging progress never repaints).

Auth/security (ranked third overall, as requested): #304 (same-tab sequential logins only), #308 (cosmetic drift risk), #319 (missing feature, not a vulnerability), #123 (already mitigated by existing CSRF rotation — stateless cookie sessions make the classic exploit inapplicable), #303 (self-assessed non-exploitable, confirmed).

## P2 — defer, genuinely separate scope

**Voice_dump/voice_note — confirmed a separate feature track, independently deferrable from the meeting pipeline:** #296, #299, #312, #315, #318, #295 (real bug, but confined to its own feature).

**Entity-extraction / knowledge-layer epic — all confirmed unbuilt, correctly scoped as blocked on #245, defer as one unit:** #241, #245, #247, #248, #249, #251, #253.

**Search/RAG scope beyond the P0 fixes:** #250 (blocked on #245, also inherits the FTS issues above), #242 (real, but its cited failure mode is currently only edge-case-triggered by #192 — becomes normal-run once built, so sequence it after #192 lands, not before), #198 (wishlist tracker), #194 (test-coverage gap, not a runtime bug).

**Self-declared Future:/Epic: issues — title alone is sufficient, no hidden overlaps found when the container issues were opened:** #195, #197, #199, #202, #203, #219, #220, #221, #222, #223, #224, #227, #236, #237, #238, #239, #263.

**Container issues, resolved:** #322's 12 items are all confirmed genuine polish/edge-case, nothing core-path hiding inside. #196 turned out to already be a well-maintained index into #197–#204 with no hidden duplicates — good hygiene, no action needed.

## Suggested order of attack

1. **#309, #192** (+ #191 as a cheap ride-along in the same code) — fix the search substrate before building anything on top of it.
2. **#122** — default diarization engine's everyday failure mode.
3. **#115, #328** — correction and finalize idempotency; both silently corrupt or duplicate on a normal run.
4. **#126, #127** — chunk lifecycle; disk leak and misleading partial-status failures.
5. **#316** — get the e2e suite actually passing and decide whether to wire it into CI, before you land steps 1–4 with no working regression coverage over the exact surfaces they touch.
6. **Security, third as requested:** check your actual deployment against **#124** and **#302**; file the missing invite/registration gate as its own issue.
7. **#311, #321, #305** — cheap fixes that make the existing voice-match trust story actually true instead of assumed.
8. **#119, #121** — graceful pyannote fallback, for the subset of users who opt into it.
9. Only then: start **#218** (semantic RAG), now that its foundation is verified sound.
10. Close **#130, #297, #329** whenever convenient — no work required, just bookkeeping.

Everything else in the 88 is real (the investigation found very little that was simply wrong), but none of it stands between you and a working, trustworthy end-to-end run today.

**A note on confidence:** four findings were empirically reproduced, not just read — #309 (live repro: delete/recreate returned the wrong transcript's content, then an integrity check failed), #316 (actually ran the e2e suite: 6 passed, 16 errors), #328 and #115 (traced the actual reachable code path/absence of a guard, not merely inferred from description). Everything else in this report is a careful, code-verified static reading — high confidence, but not exercised at runtime. Worth a quick smoke-test on anything past the top few before sinking real time into a fix.
