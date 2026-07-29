# Outbound Webhooks on Transcript / Voice-Note Completion

> One-line status: Draft plan. Idea inspired by Blinko (github.com/blinkospace/blinko), concept only, no code copied.

## Motivation

WhisperDeck has no way to tell the outside world "a transcript just finished" short of a human opening the app and checking the Queue screen. For a local-first tool whose whole value is capturing a thought or a meeting the moment it happens, that's a gap: the natural next step for a lot of users is pushing the result somewhere else immediately, into an Obsidian vault, a Notion database, a Slack channel, or an n8n/Zapier-style automation that fans out from there. Today the only way to get a transcript out is to open the app and copy/export it by hand. A configurable outbound webhook on completion turns WhisperDeck from an endpoint into a node in whatever workflow the user already has.

## What Blinko does (attribution)

Blinko (github.com/blinkospace/blinko) fires an outbound webhook whenever a note or comment event happens (`server/lib/helper.ts`'s `SendWebhook` helper, called from `server/routerTrpc/note.ts` on note create/update/delete and from `server/lib/commentWebhook.ts` on comment events). Its design is intentionally minimal: one global webhook URL per install (`globalConfig.webhookEndpoint`, configured in the settings UI), one `POST` per event with a `{data, webhookType, activityType}` JSON body, fired with a bare `axios.post` wrapped in a try/catch that only logs on failure, no signing, no retry, no backoff, no timeout override. We borrow the idea (fire a webhook when content-producing work finishes, so a self-hosted note tool becomes a workflow trigger) but not the fire-and-forget implementation; WhisperDeck already has a durable job-queue pattern (`LlmJob`/`TranscriptionJob`) that a one-shot `axios.post`-style call would not fit, and a completion event is exactly the kind of thing worth not silently losing to one transient DNS hiccup.

## Proposed approach

Treat a webhook delivery as its own small background job, dispatched the same way an `LlmJob` is: the completion path only **enqueues** a delivery row and commits, it never makes the HTTP call itself, so a slow or dead receiving endpoint can never add latency to transcription or an LLM pass. A new `webhook_worker_loop` (started from `app.py`'s `lifespan` next to `queue_worker_loop` and `llm_worker_loop`) polls for pending deliveries and does the actual `POST`, with the same claim-then-commit-before-await discipline already used everywhere else in this codebase.

**Event types (v1):**
- `transcript.completed`, a meeting/dictation transcript reaches `status == "completed"` (not `partial`/`failed`, see Open Questions).
- `voice_note.completed`, the voice-note LLM chain (`LlmJob(kind="voice_note")`) finishes and the `VoiceNote` row is written.
- `correction.completed`, the auto-correction `LlmJob(kind="correction")` finishes successfully. Marked "maybe" in the task brief; kept in v1 because it's a one-line addition once the delivery plumbing exists, but see Open Questions on sequencing versus `transcript.completed`.

**Where this hooks in, mirrored pair, per the Complement Rule.** A transcript reaches "completed" from two independent code paths that already duplicate the same auto-correct/classify/tagging enqueue block, and both need the webhook call added in lockstep:
- `services/queue.py`'s `_finalize_if_done`, the chunked-upload path, right where `new_status` is computed and `enqueue_auto_correction`/`enqueue_auto_classify`/`enqueue_auto_tagging`/`enqueue_auto_voice_note` are called.
- `app.py`'s `_run_transcription_pipeline`, the inline (non-chunked) path, at the same enqueue block after `transcription_service.transcribe()` succeeds.

Both sites already carry a comment ("keep this site in lockstep with services/queue.py:_finalize_if_done") acknowledging they're a mirrored pair; add the webhook enqueue call to both in the same change, not just the one a test happens to exercise.

`voice_note.completed` and `correction.completed` don't have this mirroring problem, both kinds always execute inside `services/llm_jobs.py`'s `run_llm_job`, regardless of whether the parent transcript came from the inline or chunked path, so there's exactly one call site each: the `"voice_note"` branch (after the `VoiceNote` row is written) and the `"correction"` branch (at the `result == "ok"` branch, right before `_finish(db, job, "completed")`).

**Delivery worker.** `services/webhooks.py` (new):
- `enqueue_webhook_delivery(db, user_id, event_type, transcript_id, payload)`, no-ops if the user has no `webhook_url` configured or hasn't enabled `event_type`; otherwise inserts one `WebhookDelivery` row (`status="pending"`) and commits. Mirrors `enqueue_llm_job`'s shape.
- `webhook_worker_tick(SessionLocal)` / `webhook_worker_loop(SessionLocal, interval_seconds=3.0)`, mirrors `llm_worker_tick`/`llm_worker_loop`: claim a small batch of `pending` rows (plus any `failed` rows whose backoff window has elapsed, reusing the same `_retry_eligible`-style check already in `services/queue.py`), mark them `running`, commit, then `POST` each with `httpx.AsyncClient` (already a project dependency, see `services/llm_client.py`'s use of `httpx.AsyncClient(timeout=120)`), and land each on `delivered` or `failed` afterward.
- Concurrency cap similar to `_MAX_CONCURRENT_IO_JOBS` (2), webhook receivers are just as likely to be slow as an LLM provider, no reason to let a stuck delivery stall every other one, but no reason to fire 50 at once either.

**Non-blocking guarantee.** This is the same guarantee `LlmJob` already gives correction/summary/tagging: the completion path's only obligation is one `INSERT` + `commit`; the network call happens on the next `webhook_worker_tick`, off the critical path entirely. No new blocking behavior is introduced anywhere a transcript or job currently completes.

## Code touchpoints (files + symbols, no line numbers)

- **services/webhooks.py** (new), `enqueue_webhook_delivery`, `webhook_worker_tick`, `webhook_worker_loop`, `build_signature` (HMAC helper), `serialize_webhook_payload` per event type.
- **services/queue.py**, `_finalize_if_done`: add `enqueue_webhook_delivery(db, transcript.user_id, "transcript.completed", transcript.id, ...)` alongside the existing `enqueue_auto_*` calls, gated the same way (`new_status in ("completed", "partial")` block, event fired only for `"completed"`).
- **app.py**, `_run_transcription_pipeline`: mirror the same call in the inline branch, same gating; import and start `webhook_worker_loop` in `lifespan` next to `worker_task`/`llm_worker_task`, cancelled the same way on shutdown.
- **services/llm_jobs.py**, `run_llm_job`'s `"voice_note"` branch (after the `VoiceNote` row is created/updated) and `"correction"` branch (`result == "ok"` path) each get one `enqueue_webhook_delivery` call.
- **services/settings.py**, extend `DEFAULT_SETTINGS` with `webhook_url` (`""` = disabled, same convention as `export_directory`), `webhook_events` (list of enabled event-type strings, default `[]`), `webhook_secret` (see Data model note on encryption).
- **app.py**, `PUT /api/settings`: special-case `webhook_secret` the same way `POST` on the provider-config endpoint special-cases `api_key` (encrypt via `encrypt_api_key`/`SESSION_SECRET` before `update_user_settings`, mirroring `services/security.py`'s existing `encrypt_api_key`/`decrypt_api_key` pair); `GET /api/settings` must not echo the decrypted secret back to the client.
- **database/__init__.py**, new `WebhookDelivery` model (picked up automatically by `create_all` on both fresh and existing databases, no `ensure_columns` migration needed since this is a new table, not a new column on an existing one).
- **static/**, settings-panel addition: webhook URL field, per-event checkboxes, secret field (write-only, masked on redisplay), a "send test event" button (mirrors Blinko's own `testWebhook` endpoint idea, a cheap, user-visible way to confirm the URL/secret work before relying on it), and a small delivery-history readout (last N deliveries: event, status, HTTP response code, timestamp).

## Data model / schema changes

- New table `WebhookDelivery`: `id`, `user_id` (FK, indexed), `transcript_id` (nullable FK to `transcripts`, `ondelete="CASCADE"`, mirroring `LlmJob.transcript_id`), `event_type` (string), `url` (string, snapshotted at enqueue time so a later settings change doesn't retroactively alter an in-flight delivery), `payload_json` (JSON), `status` (`pending`/`running`/`delivered`/`failed`), `attempts` (int, default 0), `response_status` (int, nullable), `error` (text, nullable), `created_at`, `updated_at`. Same status vocabulary and attempts/backoff shape as `LlmJob`, for consistency with the rest of the job-observability idiom in this codebase.
- `services/settings.py`'s `DEFAULT_SETTINGS`: three new keys as above. No new table needed for config itself, a single URL plus an event list is exactly the "small fixed set of scalar values" this file's own docstring says the settings blob is for; only the delivery log needs a real table, since that's the growing-per-item collection.
- No changes to `Transcript`, `LlmJob`, or `VoiceNote` themselves, the webhook payload reads from them, it doesn't need to write anything back onto them.

## Research notes

**Signing.** HMAC-SHA256 over the raw JSON request body (byte-for-byte, before any re-serialization on the receiving end) is the de facto standard, it's what Stripe, GitHub, and most webhook providers do. Send it as `X-WhisperDeck-Signature: sha256=<hex-digest>`, alongside `X-WhisperDeck-Event: <event_type>` and `X-WhisperDeck-Delivery: <delivery id>` (an idempotency key, so a receiver that got the same delivery twice, e.g. after a retry that actually succeeded but timed out on our side, can dedupe by ID, the same convention GitHub uses with `X-GitHub-Delivery`). Also send `X-WhisperDeck-Timestamp` so a receiver can reject old/replayed deliveries if it wants to. WhisperDeck itself is the sender only, so there's no inbound verification to build, these headers are purely for the receiver's benefit, and go beyond what Blinko's own implementation does (which signs nothing).

**Retry/backoff.** Stripe retries webhook deliveries with exponential backoff over roughly three days; GitHub does not auto-retry at all (manual redelivery only) but does record every delivery for inspection. WhisperDeck already has its own backoff formula in `services/queue.py`'s `_retry_eligible` (`min(60, 5 * 2 ** attempts)`, capped at 60s, `MAX_ATTEMPTS = 3`), reuse that exact shape for consistency rather than inventing a second one, though a self-hosted receiver (a laptop running n8n that's asleep) plausibly deserves a higher `MAX_ATTEMPTS` than a paid LLM API call does; propose `MAX_ATTEMPTS = 5` for webhook deliveries specifically (see Open Questions).

**Timeouts.** Keep them short, 5s connect / 10s total is a reasonable default (shorter than `llm_client.py`'s 120s, since this is our own outbound side-call, not a multi-second LLM completion the user is waiting on). A stuck receiving endpoint should time out and retry, not tie up a worker slot.

**SSRF.** This is the one place the task brief specifically flagged, and it's a genuinely different risk shape here than in a multi-tenant SaaS webhook feature. Classic SSRF advice (block loopback/link-local/private CIDR ranges) is written for a server that fires requests on behalf of untrusted third parties into a network the operator doesn't control. WhisperDeck is local-first and single-tenant: the person typing in the webhook URL is the same person who owns the machine and the LAN it's on, and the single most common real use case (n8n/Home Assistant/Obsidian Local REST API running on `localhost` or another box on the same LAN) *is* a private-network URL. Blanket-blocking RFC1918/loopback by default would break the feature's primary use case. Recommended baseline instead:
  - Restrict scheme to `http`/`https` only (reject `file://`, `gopher://`, etc., cheap, no legitimate use here).
  - Disable automatic redirect-following (`httpx.AsyncClient(follow_redirects=False)`), the classic SSRF-via-open-redirect bypass is a URL that looks fine at validation time but 302s somewhere else; not following redirects at all removes that class outright, and a receiver that needs a redirect can just be configured with the final URL.
  - Block the cloud metadata address (`169.254.169.254`) specifically, even though WhisperDeck isn't typically deployed on a cloud VM today, cheap defense-in-depth against a future deployment shape, and it's not a URL any legitimate local webhook receiver would ever need.
  - Do not block other private/loopback ranges by default; this is a deliberate departure from generic SSRF guidance, justified by the single-tenant local-first threat model, and should be called out as such in the settings UI copy so it's a documented decision, not an oversight.

## Open questions

- **`transcript.completed` on `partial`?** A chunked transcript where some chunks failed lands on `status == "partial"`. Proposal above only fires the webhook on full `"completed"`. Confirm before building, a `"partial"` result piped silently into Obsidian with missing sections could be worse than no automation at all.
- **Payload size vs. inline content.** Should `transcript.completed` embed the full `full_text`/`corrected_text` inline, or just transcript metadata plus an ID and let the receiver call back into WhisperDeck's existing REST API for the body? Inline content is the whole point for a zero-click Slack/Notion push, but some receivers have hard payload limits (Slack incoming webhooks cap around 40KB) that a long meeting transcript can blow past. Proposed default: include the text fields but truncate past a size threshold with a `truncated: true` flag and the transcript ID for follow-up, rather than omitting content by default, confirm the threshold.
- **Sequencing of `correction.completed` vs `transcript.completed`.** For a meeting/dictation transcript, `transcript.completed` fires first (transcription itself is done); `correction.completed` fires later, asynchronously, and may never fire at all if auto-correct is off or the configured LLM has no saved key. A receiver that wants "the final, corrected text" needs to know it might get one event or two, in either order relative to other events for unrelated transcripts. Worth documenting explicitly in whatever developer-facing webhook docs ship with this, rather than solving with more code.
- **Secret storage.** Recommended approach (encrypt via `services/security.py`'s existing `encrypt_api_key`, same as `ProviderConfig.api_key`) requires special-casing one field in the settings PUT/GET path, since `services/settings.py`'s generic `update_user_settings`/`get_user_settings` round-trip has no encrypt-on-write/mask-on-read hook today. The cheaper alternative, store it as a plain string in the settings JSON blob like `hf_token` already is, is inconsistent with treating an HMAC signing secret as more sensitive than an API key users already trust the app with. Recommend building the special-case; flagging because it's more code than the rest of the settings additions.
- **Single URL vs. multiple.** v1 mirrors Blinko's own scope: one global webhook URL per user, gated by an event checklist. Multiple simultaneous destinations (e.g. Slack for meeting transcripts, Obsidian for voice notes) is a natural phase-2 ask but adds real config-surface complexity (per-destination event filters, per-destination secrets); not in v1 scope.

## Rough phasing / checklist

**Phase 1, schema and settings**
- [ ] Add `WebhookDelivery` model to `database/__init__.py` (picked up by `create_all`, no migration helper needed)
- [ ] Extend `services/settings.py`'s `DEFAULT_SETTINGS` with `webhook_url`, `webhook_events`, `webhook_secret`
- [ ] Special-case `webhook_secret` encryption in `app.py`'s settings PUT handler; mask it on GET
- [ ] Resolve the "partial" and "payload size" open questions above before Phase 2 starts

**Phase 2, delivery worker**
- [ ] `services/webhooks.py`: `enqueue_webhook_delivery`, payload serializers per event type, HMAC signing helper
- [ ] `webhook_worker_tick`/`webhook_worker_loop`: claim, POST via `httpx.AsyncClient` (no redirects, short timeout, scheme + metadata-IP guard), land on `delivered`/`failed`, backoff-eligible retry reusing `_retry_eligible`'s formula
- [ ] Start `webhook_worker_loop` from `app.py`'s `lifespan`, cancelled on shutdown like the other two loops

**Phase 3, wire up the three event types**
- [ ] `transcript.completed`: add the enqueue call to both `services/queue.py`'s `_finalize_if_done` and `app.py`'s `_run_transcription_pipeline` in the same change
- [ ] `voice_note.completed`: add the enqueue call to `run_llm_job`'s `"voice_note"` branch
- [ ] `correction.completed`: add the enqueue call to `run_llm_job`'s `"correction"` branch

**Phase 4, surface and hardening**
- [ ] Settings panel: URL field, event checkboxes, secret field (write-only), "send test event" action, recent-deliveries readout
- [ ] Confirm SSRF guard behavior (scheme restriction, no redirects, metadata-IP block) is actually exercised, not just described

## Testing considerations

- Unit-test `enqueue_webhook_delivery` against each of the three completion call sites the same way `test_correction_chunked_finalize.py`/`test_correction_inline_and_manual.py` already test the mirrored chunked/inline correction paths, a webhook test suite should follow the same paired-file shape so the mirror is enforced by the tests, not just by convention.
- Mock the outbound HTTP call (no real network in unit tests) and assert: the JSON body matches the expected payload shape per event type, the `X-WhisperDeck-Signature` header verifies against a known secret + body, and a non-2xx response lands the delivery on `failed` with `attempts` incremented rather than `delivered`.
- Vacuous-test guard: a retry-backoff test must actually assert a `pending`/eligible-`failed` row is picked up only after its backoff window elapses (fabricate a `failed` row with `updated_at` set at varying offsets), not just call the tick function and check it didn't crash.
- SSRF guard test: assert a `file://` URL and a `169.254.169.254` URL are both rejected before any request is attempted; assert an ordinary `http://localhost:5678/...` URL (the primary local-automation use case) is NOT rejected, the point of the guard is what it blocks, not blocking everything.
- This is backend-only apart from the settings-panel addition; per `AGENTS.md`'s testing tiers, a scoped run of the new webhook tests plus the existing correction/voice-note/queue suites is the right tier, not a full browser e2e pass, reserve that for a pre-release checkpoint. If the settings panel adds new labeled controls, grep the e2e directory for anything that already asserts on the settings panel's structure and update selectors in the same change.
