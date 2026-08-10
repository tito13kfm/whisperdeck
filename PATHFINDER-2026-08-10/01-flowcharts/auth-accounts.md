# Feature: auth-accounts

## Sources consulted
- `app.py` lines 1-60, 180-279, 280-315, 489-552, 800-931
- `services/auth.py` (full, 243 lines)
- `services/security.py` (full, 114 lines)
- `database/__init__.py` lines 1-31 (User model), 643-835 (init_db incl. first-user-auto-admin migration)

## Concrete findings
- Corrected line numbers vs Phase 0 guess: register app.py:499-526, login 529-541, /api/me 550-552, reset-password 836-858, admin routes 864-897, device-token routes 912-931. `/api/forgot-password` (admin-generates-reset-token) at 816-833, gated by `current_user.is_admin` inline, not a route dependency.
- CSRF enforced by global Starlette middleware `enforce_csrf` (app.py:193-215), executes after SessionMiddleware at request time; exempts bearer-token calls to `/api/transcribe`.
- `create_user` (auth.py:82-94) auto-admins first user in empty table; `init_db` (database/__init__.py:820-833) separately self-heals by promoting earliest user to admin if none exists — same `is_admin` field, two independent code paths writing it.
- `authenticate_user` (auth.py:97-103): single query + verify_password, no timing defense beyond that.
- Reset-password token checked twice: `get_user_by_reset_token` pre-check in route (app.py:848, prioritizes "bad token" error over "weak password" error) then again inside `reset_password` (auth.py:158-173) which mutates. Intentional double-check, not divergent logic.
- Rate limiting via shared in-process singleton `rate_limiter` (security.py:77) keyed `f"{route}:{client_ip}"`: register 5/300s, login 10/60s, forgot-username 5/300s, forgot-password 10/60s, reset-password 5/300s. No limit on /api/me or /api/admin/*.
- Device-token auth (`get_current_user_or_device`, app.py:300-314) is a sibling path used only by the upload route, not part of this feature's happy path — shares the User row/columns only.

## Mermaid flowchart

```mermaid
flowchart TD
    A["POST /api/register<br/>app.py:499-526"] --> A1["rate_limiter.check register:ip<br/>services/security.py:61-73"]
    A1 -->|blocked| A1E["HTTPException 429<br/>app.py:505"]
    A1 -->|allowed| A2["validate username/password present<br/>app.py:506-509"]
    A2 -->|missing| A2E["HTTPException 400<br/>app.py:509"]
    A2 --> A3["query User by username<br/>app.py:510"]
    A3 -->|exists| A3E["HTTPException 400 Username taken<br/>app.py:511"]
    A3 --> A4["validate_password<br/>services/auth.py:223-241"]
    A4 -->|fails policy| A4E["HTTPException 400<br/>app.py:513-514"]
    A4 --> A5["create_user<br/>services/auth.py:82-94"]
    A5 --> A6["generate_salt + hash_password (PBKDF2-SHA256, 200k iter)<br/>services/auth.py:30-37,85-89"]
    A6 --> A7["INSERT User row (is_admin=True if first user)<br/>services/auth.py:86-93"]
    A7 -->|IntegrityError race| A7E["db.rollback + HTTPException 400<br/>app.py:517-523"]
    A7 --> A8["request.session[user_id] = user.id<br/>app.py:524"]
    A8 --> A9["rotate_csrf_token<br/>services/security.py:29-37, app.py:525"]
    A9 --> A10["200 {ok, username}<br/>app.py:526"]

    B["POST /api/login<br/>app.py:529-541"] --> B1["rate_limiter.check login:ip<br/>services/security.py:61-73"]
    B1 -->|blocked| B1E["HTTPException 429<br/>app.py:533"]
    B1 -->|allowed| B2["authenticate_user<br/>services/auth.py:97-103"]
    B2 --> B2a["query User by username<br/>services/auth.py:98"]
    B2a -->|not found| B2E["return None -> HTTPException 401<br/>app.py:537-538"]
    B2a --> B2b["verify_password (PBKDF2 recompute + compare_digest)<br/>services/auth.py:34-41,101"]
    B2b -->|mismatch| B2E
    B2b -->|match| B3["request.session[user_id] = user.id<br/>app.py:539"]
    B3 --> B4["rotate_csrf_token<br/>services/security.py:29-37, app.py:540"]
    B4 --> B5["200 {ok, username}<br/>app.py:541"]

    C["GET /api/me<br/>app.py:550-552"] --> C1["get_current_user dependency<br/>app.py:281-285"]
    C1 --> C2["_resolve_session_user<br/>app.py:268-278"]
    C2 --> C3["read request.session[user_id]<br/>app.py:271"]
    C3 -->|absent| C3E["return None -> HTTPException 401<br/>app.py:283-284"]
    C3 --> C4["query User by id<br/>app.py:274"]
    C4 -->|stale id, no row| C4E["session.clear + return None -> 401<br/>app.py:275-277,283-284"]
    C4 -->|found| C5["200 {username, is_admin}<br/>app.py:552"]

    D["POST /api/forgot-password (admin generates token)<br/>app.py:816-833"] --> D1["get_current_user dependency<br/>app.py:281-285"]
    D1 --> D2{"current_user.is_admin?<br/>app.py:821"}
    D2 -->|no| D2E["HTTPException 403<br/>app.py:822"]
    D2 -->|yes| D3["rate_limiter.check forgot-password:ip<br/>services/security.py:61-73"]
    D3 -->|blocked| D3E["HTTPException 429<br/>app.py:825"]
    D3 --> D4["generate_reset_token<br/>services/auth.py:127-142"]
    D4 --> D4a["lookup target User by username<br/>services/auth.py:135"]
    D4a -->|not found| D4E["return None -> HTTPException 404<br/>app.py:830-831"]
    D4a --> D5["token=secrets.token_hex(32); store SHA-256 hash + TTL 1h<br/>services/auth.py:138-141"]
    D5 --> D6["200 {reset_token plaintext, expires_at}<br/>app.py:829-833"]

    E["POST /api/reset-password<br/>app.py:836-858"] --> E1["rate_limiter.check reset-password:ip<br/>services/security.py:61-73"]
    E1 -->|blocked| E1E["HTTPException 429<br/>app.py:841"]
    E1 --> E2["validate token/new_password present<br/>app.py:844-845"]
    E2 -->|missing| E2E["HTTPException 400<br/>app.py:845"]
    E2 --> E3["get_user_by_reset_token (pre-check)<br/>services/auth.py:145-155, app.py:848"]
    E3 -->|invalid/expired| E3E["HTTPException 400<br/>app.py:849"]
    E3 --> E4["validate_password(new_password)<br/>services/auth.py:223-241"]
    E4 -->|fails policy| E4E["HTTPException 400<br/>app.py:851-852"]
    E4 --> E5["reset_password (re-validates token, mutates)<br/>services/auth.py:158-173"]
    E5 --> E5a["new salt + hash_password; clear reset_token/expiry<br/>services/auth.py:167-171"]
    E5a -->|race: token consumed between E3 and E5| E5E["return None -> HTTPException 400<br/>app.py:854-855"]
    E5a --> E6["request.session[user_id] = user.id (auto-login)<br/>app.py:856"]
    E6 --> E7["rotate_csrf_token<br/>app.py:857"]
    E7 --> E8["200 {ok, username}<br/>app.py:858"]

    F["POST /api/admin/promote or /demote<br/>app.py:872-897"] --> F1["get_current_user dependency<br/>app.py:281-285"]
    F1 --> F2{"current_user.is_admin?<br/>app.py:875,889"}
    F2 -->|no| F2E["HTTPException 403<br/>app.py:876,890"]
    F2 -->|yes| F3["set_admin_status<br/>services/auth.py:179-192"]
    F3 --> F3a{"demote + target.id == admin.id?<br/>services/auth.py:188-189"}
    F3a -->|yes| F3E["return None -> HTTPException 400<br/>app.py:896"]
    F3a -->|no| F4["target.is_admin = value; db.commit<br/>services/auth.py:190-191"]
    F4 --> F5["200 {ok, username, is_admin}<br/>app.py:883,897"]

    G["Every /api/* mutating request<br/>enforce_csrf middleware, app.py:193-215"] --> G1{"method in GET/HEAD/OPTIONS?<br/>app.py:190,194"}
    G1 -->|yes| G_ok["skip check, call_next<br/>app.py:215"]
    G1 -->|no| G2{"bearer auth on /api/transcribe?<br/>app.py:205-208"}
    G2 -->|yes| G_ok
    G2 -->|no| G3["validate_csrf_token(session, X-CSRF-Token)<br/>services/security.py:40-45, app.py:210-211"]
    G3 -->|invalid| G3E["403 Invalid or missing CSRF token<br/>app.py:214"]
    G3 -->|valid| G_ok2["call_next -> route handler<br/>app.py:215"]
    G_ok2 -.gates.-> A
    G_ok2 -.gates.-> B
    G_ok2 -.gates.-> E
    G_ok2 -.gates.-> F
```

## External dependencies
- `database/__init__.py` `User` model (lines 17-30): sole persistence target for every mutation above.
- `database/__init__.py init_db` (lines 820-833): startup self-heal promoting earliest user to admin if none exists — feeds same `is_admin` field.
- `starlette.middleware.sessions.SessionMiddleware` (app.py:246) backs `request.session`, no external service call.
- No LlmJob/transcription queue interaction — auth-accounts is upstream (everything gates on `get_current_user`), never enqueues or reads a job.
- Device-token auth (`get_current_user_or_device`, app.py:300-314, backed by `set_device_token`/`get_user_by_device_token` in auth.py) shares User row/columns, separate entry path used only by the upload route.

## Confidence and gaps
High confidence, all line numbers verified from source (several Phase 0 guesses were off by a few lines). Did not expand `/api/csrf-token` (489-496), `/api/logout` (544-547), or `/api/admin/users` (GET list, 864-869) as separate flows — single-hop utility routes with no interesting branches. `encrypt_api_key`/`decrypt_api_key` in security.py confirmed unused by this feature (provider-config only).
