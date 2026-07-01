# Per-user auth & API key ownership — design

## Context

WhisperDeck is currently single-tenant: one `ProviderConfig` row per provider (groq, openai, etc.), storing one shared API key used by anyone who reaches the running server. There are no user accounts. This was fine while the app was used by one person on one machine, but the user wants to move toward user-provided API keys (not admin-stored, shared ones) and a way to track "who is this," starting as just themselves but possibly expanding to a few other people later. The explicit ask was "whatever is easy and expandable" — the auth-strength decision was deferred to the implementer.

Discovered mid-brainstorm: the user also wants a provider-queue/fallback system aware of rate limits and cost, so a rate-limited free-tier Groq call can fall back to another provider. This is a genuinely separate subsystem — it depends on per-user identity existing first (usage/cost has to be tracked *per user*), but it is not part of this design. See "Future work" below.

## Decisions

Resolved through structured questions during brainstorming, each with the recommended option selected:

1. **Key storage**: server-side, per-user (not browser-only/never-persisted). Users enter their key once; it's saved and reused on future visits.
2. **Identity method**: real username + password login, not an anonymous browser-cookie identity. Works across devices/browsers, and supports "invite a coworker" later.
3. **Data scope**: transcripts and voice profiles are private per-user, not a shared library. Matches how the API keys work.
4. **Sign-up**: open self-serve registration, no admin/invite gate. Appropriate since this runs on a home machine/network, not the open internet.

## Architecture

Session-cookie auth via Starlette's built-in `SessionMiddleware` — a signed cookie, no server-side session table to manage, no new heavy dependency (Starlette already ships this; `itsdangerous`, its only requirement, is a small pure-Python package with no compiled-extension install risk).

A FastAPI dependency, `get_current_user(request: Request, db: Session = Depends(get_db))`, reads the user id out of the session cookie, loads the `User` row via the per-request `db` session, and raises `HTTPException(401)` if there's no valid session. Every route that touches provider keys, transcripts, or voice profiles depends on it.

Password hashing uses Python's standard-library `hashlib.pbkdf2_hmac("sha256", password, salt, iterations=...)` with a random per-user salt (`secrets.token_hex`), rather than bcrypt or passlib. This project already hit real friction with a compiled-extension dependency (numpy/soundfile wheels missing for Python 3.14 during initial setup) — pbkdf2 via stdlib avoids repeating that class of problem, and is adequate for a personal app's threat model (not a public multi-tenant SaaS).

## Data model changes

New table:
```
User
  id            INTEGER PRIMARY KEY
  username      TEXT UNIQUE NOT NULL
  password_hash TEXT NOT NULL
  password_salt TEXT NOT NULL
  created_at    DATETIME
```

Existing tables gain a `user_id` foreign key:
- `ProviderConfig` — uniqueness changes from "one row per `name`" to "one row per (`user_id`, `name`)". Each user has their own Groq/OpenAI/etc. key, independent of everyone else's.
- `Transcript` — gains `user_id`. Every list/get/update/delete query filters on it.
- `VoiceProfile` — gains `user_id`; uniqueness changes from "one row per `name`" to "one row per (`user_id`, `name`)" — two users can each have a profile named "Sarah" without colliding.

`Summary` is unaffected directly — it's scoped implicitly through its `transcript_id` FK to an already-owned `Transcript`.

## Migration (protecting existing data)

The app initializes its schema with `Base.metadata.create_all()`, which creates missing tables but does **not** add columns to tables that already exist on disk. A fresh `data/whisperdesk.db` gets the new schema for free; an existing one needs a one-time upgrade path.

On startup, before `create_all()` runs (or immediately after, checking column existence via `PRAGMA table_info`):
1. If `ProviderConfig`, `Transcript`, or `VoiceProfile` are missing a `user_id` column, run `ALTER TABLE ... ADD COLUMN user_id INTEGER` on each (SQLite supports adding nullable columns to existing tables).
2. Create a fallback account (e.g. username derived from the OS user, or a fixed placeholder like `local`) if no users exist yet.
3. Backfill: any row in those three tables with `user_id IS NULL` gets assigned to that fallback account.

This guarantees nothing already created (transcripts, enrolled voices, saved provider keys) becomes orphaned or silently inaccessible after the upgrade.

## API changes

New endpoints:
- `POST /api/register` — `{username, password}` → creates a `User`, hashes the password, logs them in (sets the session cookie).
- `POST /api/login` — `{username, password}` → verifies password hash, sets the session cookie.
- `POST /api/logout` — clears the session cookie.
- `GET /api/me` — returns the current user's username if logged in, or 401.

Every existing route that queries `ProviderConfig`, `Transcript`, or `VoiceProfile` gains:
- `current_user: User = Depends(get_current_user)` as a parameter.
- A `.filter(Model.user_id == current_user.id)` added to its query (or `user_id=current_user.id` set when creating a new row).

## Frontend changes

`static/index.html` is a single-file vanilla-JS SPA with no build step; this stays true. On load, it calls `GET /api/me`. If 401, it shows a simple login/register view (new markup + a couple of functions, same file) instead of the dashboard. On successful login/register, it re-runs the existing `loadDashboard()` init path. A logout action (e.g. in Settings) calls `POST /api/logout` and reloads to the login view.

## Explicitly out of scope

- Password reset / email verification — no email-sending infrastructure exists, and adding one is a separate, unrequested project.
- Admin roles or permissions beyond "is a registered user."
- Login rate-limiting / brute-force protection.
- HTTPS enforcement — deployment (reverse proxy, TLS) is left to the user; this app runs on a home machine/LAN today.

These are reasonable gaps for a personal app used by one person and a small number of trusted others, not exposed to the open internet. Revisit if the deployment context changes.

## Future work (not designed yet)

Once per-user accounts exist, a follow-on project can add:
- **Per-user/provider usage tracking**: call counts, minutes transcribed, estimated cost, keyed by `user_id` + provider.
- **Rate-limit awareness and fallback routing**: when a provider call fails with a rate-limit error (e.g. Groq's free tier), either automatically retry against a configured fallback provider, or prompt the user to confirm switching to a non-free option.

This is a genuinely separate subsystem from auth — it needs its own brainstorm and design once there's a real `user_id` to attach usage records to. It's noted here only so this auth design's data model doesn't accidentally make that harder later (it doesn't: usage records would simply be a new table keyed by `user_id` and provider name, no changes needed to what's designed above).

## Verification plan

No test suite exists for this app; verification will be manual, covering:
1. Fresh install: register a new user, confirm login/logout round-trips via the session cookie.
2. Existing-DB upgrade: run against the current `data/whisperdesk.db`, confirm the fallback account is created and the existing transcript/upload row is still visible after logging in as that fallback account.
3. Two-user isolation: register a second user, confirm they see an empty transcript list and cannot see or delete the first user's transcripts, voice profiles, or provider keys via direct API calls (not just hidden in the UI).
4. Provider key isolation: two users each save a different Groq key; confirm a transcription request from each user uses their own saved key (verifiable by testing with a temporarily different/invalid key value showing back through `GET /api/providers/groq`, or a live call rejected for the wrong account).
