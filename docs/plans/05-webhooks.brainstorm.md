# Outbound Webhooks: Brainstorm Layer

> Companion to `docs/plans/05-webhooks.md` (the draft plan). This document exists to pressure-test that plan's choices before anyone commits to them. Nothing here is decided. Where a recommendation is stated, read it as "the option I'd pick if forced today," not as settled.

## User-intent framing

The person who wants this feature is not asking "notify me that a job finished." They already see that on the Queue screen. What they want is: the moment a transcript or voice note is done, the *content* shows up somewhere else without them touching WhisperDeck again, an Obsidian vault, a Notion database, a Slack channel, or an n8n flow that fans out further. That framing matters for two of the decisions below: it argues for inlining content in the payload (a link-only payload defeats the "zero-click" point), and it argues the primary destination really is a local or LAN process, which is exactly what makes the SSRF stance non-standard here.

It also means the audience is a single user configuring their own machine, not a multi-tenant service accepting webhook URLs from strangers. Every tradeoff below should be read against that scale: one user, one install, receivers the user themselves stood up.

## Decision 1: Delivery reliability

**The question:** does a completion event get a durable, retried delivery (a `WebhookDelivery` table plus worker loop, the plan's choice), or a plain background task that fires once and logs on failure?

| Approach | Description | Pros | Cons |
|---|---|---|---|
| A. Fire-and-forget background task | `asyncio.create_task(httpx_post(...))` right at the completion site, no table, no retry | Zero schema change, zero new worker loop, matches Blinko's own scope exactly | A receiver that's asleep (laptop running n8n, not currently on) loses the event permanently, no visibility that it happened, no way to see delivery history in the UI |
| B. Durable queue with retries (plan's choice) | `WebhookDelivery` row inserted at completion, `webhook_worker_loop` claims and POSTs, retries on failure with backoff | Survives a receiver being briefly down or asleep; gives a delivery-history UI for free (the table already has everything needed); matches the existing `LlmJob`/`TranscriptionJob` idiom this codebase already uses everywhere else | A third worker loop, a new table, more surface area to test and to get wrong (see Vacuous-test note in the existing plan) |
| C. Fire-and-forget, but log to a lightweight append-only table (no retry) | Background task fires once; success/failure and response code logged to a simple table for visibility, no retry logic | Gives the "did it work" visibility of B without the backoff/retry machinery | Half a solution: you built a table and still lose the event if the receiver was briefly down, which is the single most likely real-world failure (an n8n box that wasn't awake yet) |

**Reliability actually warranted here:** this is a single-user, local-first tool, not a payments webhook. Nobody's business process breaks if one voice-note push to Slack is silently dropped. But the single most common real failure mode for a LAN receiver (Home Assistant, n8n, a personal server) is exactly "it wasn't running yet" or "it was mid-restart", both of which resolve within seconds to minutes. That's a good match for a *small*, *short* retry window, not for treating this like a payments-grade at-least-once pipeline with days of backoff.

**At-least-once vs at-most-once:** any retry design is at-least-once by construction (a delivery that succeeded on the receiver's end but the response was lost to a timeout on our side will retry and double-fire). The plan already proposes `X-WhisperDeck-Delivery` as a dedupe key precisely because of this. Worth being explicit: this is a deliberate choice, not an oversight, dedupe-by-header pushes the correctness burden onto the receiver, which is standard practice (Stripe, GitHub do the same) and reasonable given how cheap it is for a receiver to check one header.

**Recommendation:** keep the plan's durable-table approach (B), but shrink the retry envelope from what's proposed. Three attempts over roughly a minute, not five over longer backoff windows, this is chasing "receiver is booting" not "receiver has been down for hours." A receiver down longer than that is better served by the user noticing in the delivery-history UI and manually retrying (the plan doesn't currently mention a manual-redelivery action; worth adding as cheap phase-4 scope since the row already has everything needed to replay it).

## Decision 2: Event model

**Which events fire, and when:**

- `transcript.completed`: fires from the mirrored pair (`services/queue.py`'s `_finalize_if_done` and `app.py`'s `_run_transcription_pipeline`), only on `status == "completed"`, per the plan.
- `voice_note.completed`: fires from `services/llm_jobs.py`'s `run_llm_job`, `"voice_note"` branch, after the `VoiceNote` row is written.
- `correction.completed`: fires from the same file's `"correction"` branch, on the `result == "ok"` path.

**Should `transcript.completed` also fire on `partial`?** The plan defers this as an open question and that's the right call to leave open, but here's the sharper framing: a `partial` transcript (some chunks failed) piped silently into an Obsidian note or a Slack message reads to the receiving system as a finished, trustworthy transcript. There's no signal in a plain Slack message that says "this is missing the middle third." Two honest options:

| Option | Behavior | Tradeoff |
|---|---|---|
| Only fire on full `completed` | `partial` never triggers a webhook | Silent hole: a long meeting that partially failed just never shows up downstream, and unless the user is watching the Queue screen they won't know to go look |
| Fire on `partial` too, with a distinguishing field | Payload carries `"status": "partial"` and maybe a `"failed_chunk_count"` | Puts the burden on every receiver to branch on that field; a naive Slack webhook or Notion automation almost certainly won't, so the practical effect is the same silent hole, just with an ignored flag |

Neither option is clearly better without knowing what the received automations actually check. Leaning toward: fire only on full `completed` for v1 (matches the plan), but say so explicitly in whatever developer docs ship, and treat "should partial fire a distinct event type entirely" (`transcript.partial`, a receiver can subscribe or not) as a natural phase-2 addition rather than overloading `transcript.completed`.

**Ordering / race between `correction.completed` and `transcript.completed`:** the plan's Open Questions section already calls this out accurately: `transcript.completed` fires when transcription itself lands, `correction.completed` fires later (a separate async `LlmJob`), if it fires at all (auto-correct off, no LLM key configured). A receiver stitching these into "the final version" has to handle: zero, one, or two events for a given transcript, in either firing order relative to other transcripts' events (there's no per-transcript sequencing across event types, each is dispatched by its own worker loop on its own timer). This isn't fixable by making the code smarter, it's an artifact of correction genuinely being optional and asynchronous, so documenting the contract explicitly (as the plan already proposes) is the right level of fix, not a design change.

**Payload shape: inline text vs id-only:**

| Option | Description | Tradeoff |
|---|---|---|
| Inline full text always | `full_text`/`corrected_text`/voice-note `body` embedded directly in the JSON body | Zero-click for the receiver, matches the user-intent framing above; breaks on receivers with hard payload caps (Slack incoming webhooks reject oversized bodies outright) |
| ID + link only | Payload carries `transcript_id`, receiver calls back into WhisperDeck's REST API for content | Never blows a size limit; forces every receiver automation to make a second authenticated call back to a `localhost`-bound API, which for something like a plain Slack incoming webhook (no code, just a URL) is simply impossible, defeating the feature for that receiver class entirely |
| Inline with truncation past a threshold (plan's choice) | Full text up to N bytes, `truncated: true` + id past that | Best of both for the common case (most dictations and voice notes are well under any reasonable threshold); adds one more field every payload consumer has to be aware of, and picking the right threshold is itself a real decision (Slack's own limit is roughly 40KB, but a Notion or n8n receiver has a much bigger ceiling) |

**Recommendation:** truncation-with-flag (the plan's choice) is right, it's the only option that doesn't quietly break the feature for one whole class of receiver (plain incoming-webhook URLs with no code behind them, which is likely the *most* common WhisperDeck receiver, not an edge case). The open question that matters is the threshold number itself, and whether it should be one global constant or a per-endpoint configurable value (ties into Decision 4 below).

## Decision 3: Security

### HMAC signing

Not much to brainstorm here, the plan's `X-WhisperDeck-Signature: sha256=<hex-digest>` over the raw body, plus `X-WhisperDeck-Event`/`X-WhisperDeck-Delivery`/`X-WhisperDeck-Timestamp`, is the industry-standard shape (Stripe, GitHub) and there's no real alternative worth considering other than "sign nothing" (what Blinko does), which just pushes forgery-detection entirely onto the receiver with zero support from WhisperDeck. Keep it.

### SSRF stance: pressure-testing the plan's "no default blocking" position

The plan's argument is: WhisperDeck is single-tenant and local-first, the person configuring the webhook URL is the same person who owns the machine and LAN, so blocking RFC1918/loopback by default breaks the primary use case (n8n/Home Assistant/Obsidian Local REST API on `localhost` or another LAN box). That argument holds for the *machine owner*. It gets weaker once you look at how the app actually runs.

`app.py`'s `uvicorn.run(app, host="0.0.0.0", ...)` binds every interface, not just loopback. That means anyone else on the same LAN, or the same Wi-Fi network, can reach WhisperDeck's HTTP API, not just the machine's owner sitting at the keyboard. Whether that matters hinges entirely on what auth gate sits in front of `PUT /api/settings` and the webhook-config fields specifically:

- If a session cookie / login is required to reach `PUT /api/settings` and sessions aren't trivially guessable or fixable, the "single trusted operator" framing mostly holds, the LAN-adjacency of `0.0.0.0` binding doesn't by itself let a stranger reconfigure the webhook target.
- If there's any path that reaches settings without full auth (a CSRF gap, a session-fixation issue, or simply a shared household/office LAN where multiple people already have valid logins to the same WhisperDeck instance), then `0.0.0.0` binding is exactly the wrinkle that turns "the user points their own tool at their own LAN" into "anyone on the LAN can point WhisperDeck at an arbitrary internal host and have it push transcript content there." That's a materially different threat than the plan's framing assumes, WhisperDeck becomes a request-forwarding proxy for whoever can reach its API, not just for its owner.

This doesn't mean the plan's core position is wrong, blanket-blocking private ranges genuinely would break the primary use case, and cloud-metadata-address blocking plus no-redirect-following plus scheme restriction are all cheap, uncontroversial wins worth keeping regardless of which way this resolves. But "no default private-IP blocking" deserves one more layer than the plan currently gives it:

| Option | Description | Tradeoff |
|---|---|---|
| A. Plan's baseline as-is | Scheme restriction + no redirects + metadata-IP block only, no private-range blocking | Simplest, matches primary use case exactly; assumes the auth gate in front of settings is solid, which hasn't been verified as part of this brainstorm |
| B. Same baseline, plus require confirmation for a private-IP target other than `localhost`/`127.0.0.1` | Settings UI shows an explicit "this points at another device on your network, confirm" warning when the URL host isn't loopback | Keeps the LAN use case fully working (Home Assistant, another box running n8n) while making a not-the-machine-owner's-intent misconfiguration visible instead of silent; small UI cost |
| C. Full standard SSRF blocking (RFC1918 + loopback blocked by default, opt-in override) | Matches generic SSRF guidance | Breaks the single most common real use case (local automation receivers) by default; wrong fit for this product, listed only for completeness |

**Recommendation:** keep the plan's baseline (A) as the floor, it's right for the primary use case. But treat "confirm the actual auth surface in front of `PUT /api/settings` before shipping this" as a prerequisite check, not a nice-to-have, specifically because of the `0.0.0.0` bind. That's a five-minute check against existing code (does `get_current_user` gate that route, is there a CSRF check on state-changing settings writes) rather than new design work, and it determines whether option B's extra confirmation step is worth the UI cost or genuinely unnecessary.

### Secret storage

The plan proposes encrypting `webhook_secret` the same way `ProviderConfig.api_key` is encrypted today (`services/security.py`'s `encrypt_api_key`/`decrypt_api_key`, keyed off `SESSION_SECRET`). Worth naming explicitly what this actually requires, because it's more than the plan's phrasing ("special-case the same way provider-config does") suggests: today, `app.py`'s `PUT /api/settings` is a bare passthrough straight into `update_user_settings` with zero field-specific handling, and `GET /api/settings` returns the stored dict as-is, including `hf_token` in plaintext. There's no existing "encrypt on write, mask on read" hook anywhere in the settings blob path, the only place that pattern exists today is the separate `ProviderConfig` table's own PUT handler. Building it for `webhook_secret` means adding the *first* such special-case to a route that currently has none.

| Option | Description | Tradeoff |
|---|---|---|
| Plaintext in the settings JSON blob, same as `hf_token` today | No new code path | Consistent with existing precedent (`hf_token` already sits there in plaintext) but treats an HMAC signing secret, whose entire purpose is proving requests came from this install, as less sensitive than an API key, which is backwards |
| Encrypt via `services/security.py`, special-case in `PUT`/`GET /api/settings` (plan's choice) | Mirrors `ProviderConfig.api_key`'s existing encryption | Correct relative sensitivity; is real new code (the special-case hook doesn't exist yet) and needs its own test, not an extension of an existing one |
| Move webhook config to its own table (like `ProviderConfig`) instead of the settings JSON blob | Sidesteps the settings-blob special-casing problem entirely, reuses the exact PUT-handler pattern that already encrypts `ProviderConfig.api_key` | Only makes sense if Decision 4 lands on "multiple endpoints" anyway (a growing per-item collection, exactly what `services/settings.py`'s own docstring says belongs in a real table, not the scalar-only JSON blob); wasted structure if v1 stays single-URL |

**Recommendation:** encrypt it (plan's choice), the "an HMAC secret is more sensitive than a bearer API key" argument is straightforwardly correct. But note the "own table" option isn't just a security tradeoff, it's coupled to Decision 4, if multiple endpoints ship even in phase 2, building the encrypted-field special-case on the JSON blob now is throwaway work.

## Decision 4: Config surface

| Option | Description | Tradeoff |
|---|---|---|
| A. Single URL, event checklist (plan's v1 scope) | One `webhook_url`, one `webhook_secret`, `webhook_events` list, all in the settings JSON blob | Minimal surface, fastest to ship, matches Blinko's own scope; can't route different events to different places (Slack for meetings, Obsidian for voice notes) without the user picking one destination for everything |
| B. Multiple endpoints, each with its own URL/secret/event filter | A real table (`WebhookEndpoint`: id, url, secret, enabled_events, user_id) | Matches the actual stated use case in the feature description ("push into Obsidian/Notion/Slack/n8n", plural) more literally; real schema + CRUD UI + per-endpoint delivery-history filtering, meaningfully more surface than A |
| C. Single URL, but let the *receiver* fan out | Ship A, tell users that a proper fan-out belongs in n8n/Zapier on the receiving end (WhisperDeck fires once into an automation tool that's built for branching) | No extra WhisperDeck complexity at all; requires the user to already run or set up a fan-out tool, which not everyone will want just to get "Slack for meetings, Obsidian for notes" |

**Recommendation:** ship A for v1 as the plan proposes, and treat C as the honest answer for "how do I get multiple destinations" in the v1-era docs (a single n8n webhook fanning out to Slack/Notion/Obsidian is a five-minute n8n flow for anyone already reaching for this feature). Revisit B only if real usage shows people hand-rolling the same "one endpoint per destination" request repeatedly, at which point the earlier secret-storage decision should be revisited alongside it (see Decision 3).

## Complement Rule: the mirrored transcript-completion sites

`transcript.completed` needs to fire from both `services/queue.py`'s `_finalize_if_done` (the chunked-upload path) and `app.py`'s `_run_transcription_pipeline` (the inline path). This is exactly the shape the Complement Rule exists for: both sites already carry a comment acknowledging they're a mirrored pair ("keep this site in lockstep with `services/queue.py:_finalize_if_done`"), because `enqueue_auto_correction`/`enqueue_auto_classify`/`enqueue_auto_tagging`/`enqueue_auto_voice_note` are all called from both places today. The webhook enqueue call is a fifth sibling in that same list, added to a location that's proven, twice already (issue #171's tagging addition, the earlier auto-correct/classify addition), to be easy to patch in only one spot when a change lands under time pressure or via a narrower diff than intended.

Concretely: any PR that adds `enqueue_webhook_delivery(...)` to `_finalize_if_done` without the matching call in `_run_transcription_pipeline` (or vice versa) produces a webhook that silently fires for, say, every long chunked meeting but never for a short inline dictation, or the reverse, with no error, no log line, nothing that surfaces the gap short of a user noticing their automation didn't trigger. The plan's own testing section already proposes a paired-file test structure (mirroring `test_correction_chunked_finalize.py`/`test_correction_inline_and_manual.py`) specifically to make this an enforced invariant rather than a comment-and-hope convention, that's the right mitigation and should not be trimmed if implementation time gets tight.

`voice_note.completed` and `correction.completed` don't have this problem, both run through the single `run_llm_job` dispatch in `services/llm_jobs.py` regardless of which upload path the parent transcript took, so there's exactly one call site each. Worth stating plainly so nobody over-applies the Complement Rule where it doesn't apply, only `transcript.completed` has a sibling-entry-point risk.

## Risks, failure modes, and how they'd surface

| Failure mode | How it happens | Detection |
|---|---|---|
| Silent delivery failure | Receiver is down, wrong URL typo'd, secret mismatch on the receiving end (nothing to detect on our side for that last one, since we don't get receiver-side validation errors back, just whatever HTTP status they choose to return) | Delivery-history UI (already in the plan's phase 4) is the only real mitigation; without it, a broken webhook is invisible until the user notices their Obsidian vault stopped getting notes, could be days |
| Retry storm | A receiver that's up but returns 500 for every request (misconfigured on their end) gets retried repeatedly by every future completion, each retrying independently | `MAX_ATTEMPTS` (proposed 3, see Decision 1) caps this per-delivery; worth also considering a simple circuit breaker (if the last N consecutive deliveries to this URL all failed, stop retrying new ones and surface a banner) so a persistently broken receiver doesn't quietly accumulate a growing backlog of `failed` rows the worker keeps revisiting every tick |
| Leaking transcript content to the wrong URL | Typo'd URL that happens to resolve to something real, or (per the SSRF discussion above) a settings write that wasn't actually the machine owner | Nothing catches a typo've-resolved-to-something-real URL after the fact, this is why a "send test event" button (already in the plan) matters more than it looks, it's the only pre-flight check before real transcript content goes out; the SSRF-adjacent case is mitigated by resolving the auth-surface question in Decision 3, not by anything in the webhook code itself |
| Payload size surprise | A long meeting transcript blows a receiver's body-size limit, receiver silently drops it or 413s | Truncation-with-flag (Decision 2) avoids the silent-drop case for size limits WhisperDeck knows about; a receiver with a limit smaller than WhisperDeck's own truncation threshold still sees a failure, which lands in delivery history as a non-2xx `failed` row, at least visible, not silent |
| Correction arriving asymmetrically | Receiver assumes every `transcript.completed` is followed by a `correction.completed`, breaks when auto-correct is off | Documentation only (per Decision 2), no code fix changes the underlying async-and-optional nature of correction |

## Recommended MVP slice

Single URL, one event type (`transcript.completed` only, skip `voice_note.completed`/`correction.completed` for the first cut), HMAC signing, background fire-and-forget (Decision 1's option A, not the durable queue) with a small in-memory or lightweight logged attempt, no retry.

This is a deliberately smaller slice than the plan's own phase 1-3, and worth stating why: the riskiest, highest-value thing to learn before building the durable queue, multi-event dispatch, and settings UI is whether the core shape (an HTTP POST with a signed body reaching a real local receiver) actually works end to end against a real Obsidian Local REST API plugin or a real n8n webhook trigger. That's learnable with the smallest possible slice. The durable-queue machinery, the three-event dispatch, and the full settings panel are all real value but none of them de-risk anything, they're additive scope once the core shape is proven.

If the fire-and-forget MVP shows the "receiver was briefly asleep" failure mode happening in practice (which is plausible, the primary receivers are exactly the kind of local automation tools that aren't always running), that's the signal to build the durable queue for real, informed by an actual observed failure rate rather than a hypothetical one.

## Later phases (beyond MVP)

- Durable `WebhookDelivery` table + `webhook_worker_loop` with retry/backoff, once the MVP either shows real transient-failure need or the team decides not to wait for that signal.
- `voice_note.completed` and `correction.completed` event types (mechanically simple once the delivery plumbing exists, both are single-call-site additions in `run_llm_job`).
- Delivery-history UI (last N deliveries: event, status, HTTP code, timestamp) and a "send test event" button.
- Manual redelivery action on a `failed` row (cheap once the table exists, addresses the "receiver was down for hours, not seconds" case the retry window won't cover).
- Multiple endpoints with per-endpoint event filters (Decision 4, option B), only if real usage shows demand.
- `transcript.partial` as its own distinct event type rather than overloading `transcript.completed` (Decision 2).

## Decisions needed from the human

- Fire-and-forget MVP vs building the durable queue immediately (Decision 1).
- Does `transcript.completed` fire on `partial` status, and if so, same event type with a flag or a separate `transcript.partial` event (Decision 2).
- Payload truncation threshold, and whether it's a single global constant or configurable (Decision 2).
- Confirm the actual auth gate in front of `PUT /api/settings` (session-only? CSRF-protected?) before finalizing the SSRF stance, this determines whether Decision 3's option B (confirmation prompt for non-loopback LAN targets) is warranted given the `0.0.0.0` bind.
- Plaintext vs encrypted `webhook_secret` in the settings blob, and whether building that special-case now is worth it if Decision 4 might move config to its own table later anyway (Decision 3).
- Single URL vs multiple endpoints for v1 (Decision 4).
- `MAX_ATTEMPTS` and backoff window for webhook retries, if the durable queue ships (proposed 3 attempts over roughly a minute above, versus the plan's original higher-attempt proposal).
