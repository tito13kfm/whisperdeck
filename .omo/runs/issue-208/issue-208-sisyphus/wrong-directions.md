# Wrong directions: Issue #208

## Issue #208 body — pre-req check was accurate

Issue says "Do not start until A's functions exist on master." They do (merged PR #211). No issue.

## SQLAlchemy default overrides None

Transcript.provider has `default="groq"` in the model. Passing `provider=None` to the constructor gets replaced with "groq" by SQLAlchemy's default mechanism. The test_batch_stt_costs_no_provider_returns_zero test originally used `provider=None` and got 0.006 (groq rate) instead of 0.0. Fixed by using `provider=""` (empty string) which `_batch_stt_costs` correctly treats as falsy.

Fix: None of the documentation needed updating — this is expected SQLAlchemy behavior.

## Client fixture user mismatch

The `client` fixture in conftest.py registers "testuser", but my test helpers created a different user "costapitest". Since all endpoints filter by `current_user.id`, the endpoints returned 404 for transcripts owned by "costapitest". Fixed by changing `_make_user` to look up the existing "testuser" from the db_session instead of creating a new user.

Fix: No docs change — this is test-writing convention, not a code issue.

## datetime.utcnow() deprecated

Python 3.12 deprecated `datetime.utcnow()`. The `/api/costs` endpoint used it. Fixed by using `datetime.datetime.now(datetime.UTC).replace(tzinfo=None)` — same result, no deprecation warning.

Fix: This is a code style fix, already applied.
