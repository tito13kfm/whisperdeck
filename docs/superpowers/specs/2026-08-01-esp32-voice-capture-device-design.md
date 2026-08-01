# ESP32-S3 voice-capture device: design

## Summary

A Waveshare ESP32-S3-Touch-LCD-1.83 board becomes a standalone voice-note/brain-dump capture device. It records audio on a button press, either uploads it directly to WhisperDeck (LIVE mode) or buffers it to its SD card for a manual sync later (BUFFERED mode). This spec covers two independent deliverables: new device firmware (separate project, not yet a git repo, at `c:\claude\esp32-s3touch`), and a small server-side addition to WhisperDeck (`app.py` / `models.py`) that gives the device a way to authenticate without a browser session cookie.

## Preconditions (out of scope for this spec)

- **WhisperDeck reachable from the internet**, for LIVE mode to have anywhere to upload to. However the user sets this up (reverse proxy, Tailscale, port-forward) is their own infra decision; this spec assumes a stable base URL exists and designs against it.
- **Issue #268** (in-flight auto-classification pipeline) landing `kind="auto"` support on `/api/transcribe`. Until it merges, the device falls back to `kind="voice_note"` — one config value, not a blocker to shipping the rest of this feature.

## Non-goals (v1)

- VAD-based auto-stop (button start/stop only for now; VAD hybrid is a documented future phase).
- SSID-gated sync (any known network can be used; the gate is a manual sync action, not the network itself).
- Designing WhisperDeck's internet exposure mechanism.
- A full on-device menu system (history browsing, per-note actions). The screen is a status glance, not an app.

## Architecture

Two independent pieces, one feature:

- **Device firmware**: ESP-IDF, built on the official `waveshare/esp32_s3_touch_lcd_1_83` BSP component + LVGL v9. Owns recording, SD buffering, mode/sync state, and the captive-portal provisioning flow.
- **WhisperDeck server addition**: a device-token auth path added to `get_current_user`'s resolution chain, scoped narrowly to the upload endpoint. Ships as its own PR, independent of whatever issue #268 branch is in flight.

## Firmware base: decision

KB8NH2/esp32-s3-voice-input (the repo that inspired this) targets a different, display-less "ESP32-S3 Audio Board" — confirmed via its README, which describes mic/VAD/PTT controls with no screen or touch mentioned. Its board-init and pin-config code will not transfer to the Touch-LCD-1.83's pinout. Its *application logic* — VAD threshold approach, WAV header framing, PTT debounce pattern, WAV-streaming-to-a-server shape — is useful as a reference, but this device's firmware is written fresh against the official Waveshare BSP, not forked from KB8NH2.

Rejected alternatives:
- Porting KB8NH2 onto this board's pinout: little saved over starting clean, loses the maintained BSP's display/touch/mic component in exchange for reverse-engineering someone else's board-init layer.
- Arduino framework instead of ESP-IDF: faster to prototype, but weaker fit for a background sync/buffer system (no real task control) and the captive portal is more DIY (`WiFi.softAP` plus a hand-rolled web server, versus ESP-IDF's `esp_http_server`/`protocomm` primitives built for exactly this).

## Device components

- **Recording**: physical button, short-press toggles start/stop. Audio captured via `bsp_extra_i2s_read`, WAV-framed, written directly to SD as it records (not buffered in RAM first — 8MB PSRAM is available but not required for reasonable note lengths).
- **SD buffer**: a flat directory of timestamp-named WAV files (`YYYYMMDD_HHMMSS.wav`). The directory listing is the sync queue; no separate index file.
- **Mode toggle**: LIVE or BUFFERED, set by tapping the mode label on the status screen. Persisted across reboots (NVS).
- **Networking**: NVS stores a list of known `{ssid, password}` pairs, the WhisperDeck base URL, and the bearer token. The device is not restricted to a single "home" SSID — it opportunistically connects to any known network. Connectivity is not gated; *sync* is.
- **LIVE mode behavior**: on stop-recording, if a known network and the server are already reachable, upload immediately. If not reachable at that moment, the file simply drops into the buffer like any BUFFERED-mode file — no special-casing, no automatic retry. It waits for the next manual sync like everything else.
- **Manual sync** (long-press, same button used for recording): connect to whichever known network is in range, walk the buffer directory oldest-first, `POST` each file to `/api/transcribe`, delete the local file on a 2xx response, and stop the batch on the first failure rather than hammering a flaky connection. This is the *only* path that uploads buffered files — never automatic, per explicit requirement.
- **Provisioning**: captive portal (`esp_http_server` + softAP) on first boot. A web form collects SSIDs/passwords, the server base URL, and the bearer token, written to NVS. Re-enterable later via a factory-reset button combo (long-press both buttons at boot) for re-provisioning without reflashing.
- **Status screen** (LVGL, always visible, no menu navigation): mode label (tap target), a recording indicator (dot + elapsed time while capturing), buffered-file count, last sync result and timestamp, battery icon.

## Data flow

1. **Record**: press → capture starts → press again → WAV finalized to SD, file now sits in the buffer directory.
2. **LIVE mode**: best-effort immediate upload attempt right after step 1. On failure, the file is left in the buffer exactly as if BUFFERED mode were active — no distinct failure state to track.
3. **Sync** (long-press): connect to an in-range known network → iterate the buffer oldest-first → upload → delete on success → stop and show the result on first failure.
4. **Provisioning**: softAP + captive portal form → write config to NVS → reboot into normal operation.

## WhisperDeck server addition

- New column, `User.local_device_token`, hashed at rest using the project's existing `hashlib.pbkdf2_hmac("sha256", ...)` + per-user random salt pattern (matching `docs/superpowers/specs/2026-06-30-per-user-auth-design.md`'s password-hashing approach — no new hashing dependency).
- `get_current_user`'s resolution chain (`app.py`) gets a fallback path: if no session cookie resolves a user, check for an `Authorization: Bearer <token>` header, hash it, and look up the matching user. This fallback is only consulted on routes that opt in — `/api/transcribe` and a lightweight `/api/health` reachability check for the device's own connectivity probing — not a blanket bypass available to every authenticated route. This mirrors the exact recommendation already scoped in `docs/plans/02-global-quick-capture-hotkey.brainstorm.md`'s auth section, for the same underlying problem (a headless caller with no cookie jar).
- Settings UI gets a "generate device token" action. Regenerating invalidates whatever token was previously issued (single token per user, matching the "one caller" reality today — if the quick-capture helper from that other brainstorm doc is ever built too, it becomes a second caller needing its own token column or a small token table at that point, not before).
- Rate limiting: reuse whatever rate-limiter WhisperDeck already applies elsewhere (`rate_limiter` import already present in `app.py`) on the token-authenticated path, so a leaked token can't be used to spam uploads.
- `kind` sent by the device is `"auto"`, contingent on issue #268 landing that sentinel's support on `/api/transcribe`. Until then, the device (or a server-side default) substitutes `"voice_note"`.

## Error handling

| Condition | Behavior |
|---|---|
| SD card full | Refuse to start a new recording, show "SD full" on screen. Never write a partial/corrupt file. |
| No reachable network at sync time | Skip, leave the buffer untouched, show "no network." No retry loop — sync is only ever user-triggered. |
| Upload fails mid-batch (timeout, 500, 401) | Keep the local file, stop the batch (don't hammer a flaky link). Distinguish a 401 ("check token") from a generic network failure on screen, since the fix differs. |
| Battery critical while recording | Finalize whatever's captured so far, stop, skip any sync attempt to conserve power. Resumes normally next boot. |

## Testing

- **Firmware**: no automated test harness applies to ESP-IDF firmware here; a manual smoke checklist substitutes: record/stop happy path, SD-full refusal, buffered-sync happy path, sync failure leaves the file intact and stops the batch, mode toggle persists across reboot, first-boot captive portal flow, factory-reset re-provisioning.
- **Server**: standard pytest coverage for the new auth path — valid token with no cookie, invalid/expired token, neither token nor cookie present, and a test confirming the token is rejected on a route it isn't scoped to. This matches the exact test matrix the prior brainstorm doc called for on this class of change.

## Open items deferred, not resolved here

- VAD auto-stop (documented non-goal for v1, revisit once button-only usage is validated).
- Whether the device ever needs a second local API token (only relevant if the quick-capture helper feature also ships).
- Whether `kind="voice_note"` fallback (if used temporarily, pre-#268) needs its own follow-up to flip to `"auto"` once available, or if that's simple enough to not need tracking as a separate task.
