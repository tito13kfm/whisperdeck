# Hotword Glossary + Post-Hoc Correction Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-maintained hotword glossary and a non-fatal, rerunnable LLM correction pass that cleans up finished transcripts using that glossary.

**Architecture:** A new `hotword_entries` table backs a small CRUD service and API. A new `services/correction.py` module does two things with an LLM chat-completion call (same pattern as the existing `summarize()`): extract candidate terms from an optional user-pasted context doc into the glossary, and correct a transcript's `full_text` using the glossary. The correction pass runs automatically after both the inline and chunked transcription paths complete, and can be rerun manually against a different provider/model via a new endpoint — never touching the raw `full_text`/`segments`.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), httpx (existing LLM call pattern), pytest + pytest-asyncio (new — bootstrapped in Task 1).

## Global Constraints

- Whisper's transcription-time `prompt` param is NOT touched by this work (see spec background).
- `full_text` and `segments` are never overwritten by correction — always additive to new columns.
- All new failure paths (extraction, correction) are non-fatal: caught internally, logged, never raised past the caller.
- Follow existing per-user scoping: every new query/table filters by `user_id`, matching `ProviderConfig`/`HotwordEntry` pattern already in the codebase.
- Follow existing DB migration pattern: new tables via `Base.metadata.create_all`, new columns on existing tables via `ensure_columns(engine, table, {col: "SQL_TYPE"})` in `database/__init__.py: init_db()`.

---

### Task 1: Bootstrap pytest test infrastructure

**Files:**
- Modify: `requirements.txt`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Produces: `tests/conftest.py` fixtures `db_session` (SQLAlchemy `Session` bound to a fresh temp-file sqlite DB per test) and `client` (`fastapi.testclient.TestClient` with a registered+logged-in user, cookies attached) — every later task's tests use these.

- [ ] **Step 1: Add test dependencies**

Append to `requirements.txt`:

```
pytest==8.3.3
pytest-asyncio==0.24.0
httpx==0.27.2
```

(`httpx` is likely already present as a transitive dependency of the app itself — check `requirements.txt` first; if a line already starts with `httpx`, leave it and don't add a duplicate.)

Run: `pip install -r requirements.txt`
Expected: pytest, pytest-asyncio install cleanly (or confirm already present).

- [ ] **Step 2: Create `tests/__init__.py`**

Empty file — marks `tests/` as a package so imports resolve consistently.

```python
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
"""Shared pytest fixtures: an isolated per-test database and an
authenticated TestClient against the real FastAPI app."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
from database import init_db


@pytest.fixture()
def db_session(tmp_path):
    """A SQLAlchemy session bound to a fresh sqlite file per test, built
    through the same init_db() path the real app uses (so schema/migration
    logic is exercised identically to production)."""
    db_path = tmp_path / "test.db"
    engine, SessionLocal, _ = init_db(str(db_path))
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """TestClient wired to db_session via a get_db override, with a
    logged-in test user (cookies carried automatically by TestClient).

    Does NOT use `with TestClient(app) as client:` — that would trigger
    the app's lifespan and start queue_worker_loop against the real
    production database, which we don't want touched by tests."""
    def _override_get_db():
        yield db_session

    app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
    test_client = TestClient(app_module.app)
    test_client.post(
        "/api/register",
        json={"username": "testuser", "password": "testpass123"},
    )
    yield test_client
    app_module.app.dependency_overrides.clear()
```

- [ ] **Step 4: Write `tests/test_smoke.py`**

```python
def test_health_check_unauthenticated(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_client_fixture_is_authenticated(client):
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json()["username"] == "testuser"
```

- [ ] **Step 5: Run the smoke tests**

Run: `pytest tests/test_smoke.py -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add requirements.txt tests/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "test: bootstrap pytest infrastructure with isolated db+client fixtures"
```

---

### Task 2: HotwordEntry model, Transcript columns, settings default

**Files:**
- Modify: `database/__init__.py`
- Modify: `services/settings.py`
- Create: `tests/test_database_hotwords.py`

**Interfaces:**
- Produces: `database.HotwordEntry` (columns: `id`, `user_id`, `term`, `source`, `created_at`), `Transcript.corrected_text`, `Transcript.correction_error`, `Transcript.correction_model`, `DEFAULT_SETTINGS["auto_correct"] = True`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_database_hotwords.py`:

```python
from database import HotwordEntry, Transcript, User
from services.settings import DEFAULT_SETTINGS, get_user_settings


def test_hotword_entry_table_exists_and_scopes_to_user(db_session):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    entry = HotwordEntry(user_id=user.id, term="Groq", source="manual")
    db_session.add(entry)
    db_session.commit()

    fetched = db_session.query(HotwordEntry).filter(HotwordEntry.user_id == user.id).all()
    assert len(fetched) == 1
    assert fetched[0].term == "Groq"
    assert fetched[0].source == "manual"
    assert fetched[0].created_at is not None


def test_transcript_has_correction_columns(db_session):
    user = User(username="bob", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    t = Transcript(user_id=user.id, title="t", filename="f.mp3")
    db_session.add(t)
    db_session.commit()

    assert t.corrected_text is None
    assert t.correction_error is None
    assert t.correction_model is None

    t.corrected_text = "cleaned up text"
    t.correction_model = "groq/llama-3.3-70b-versatile"
    db_session.commit()
    db_session.refresh(t)
    assert t.corrected_text == "cleaned up text"
    assert t.correction_model == "groq/llama-3.3-70b-versatile"


def test_auto_correct_defaults_to_true(db_session):
    assert DEFAULT_SETTINGS["auto_correct"] is True

    user = User(username="carol", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    settings = get_user_settings(db_session, user.id)
    assert settings["auto_correct"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_database_hotwords.py -v`
Expected: FAIL — `ImportError: cannot import name 'HotwordEntry' from 'database'`

- [ ] **Step 3: Add `HotwordEntry` model and `Transcript` columns**

In `database/__init__.py`, add the new model directly after the `TranscriptionJob` class (before `class Summary(Base):`):

```python
class HotwordEntry(Base):
    __tablename__ = "hotword_entries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    term = Column(String(255), nullable=False)
    source = Column(String(16), default="manual")  # "manual" | "extracted"
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
```

In the `Transcript` class, add after the existing `processed_size_bytes` column (line 42):

```python
    corrected_text = Column(Text, nullable=True)
    correction_error = Column(Text, nullable=True)
    correction_model = Column(String(128), nullable=True)  # e.g. "groq/llama-3.3-70b-versatile"
```

In `init_db()`, extend the existing `ensure_columns` call for `transcripts` (around line 199) to include the three new columns:

```python
    ensure_columns(engine, "transcripts", {"audio_path": "TEXT", "diarize_requested": "BOOLEAN", "num_speakers": "INTEGER", "processed_size_bytes": "INTEGER", "corrected_text": "TEXT", "correction_error": "TEXT", "correction_model": "TEXT"})
```

Update `__all__` at the bottom of the file to include `HotwordEntry`:

```python
__all__ = [
    "Base", "User", "Transcript", "Summary", "VoiceProfile", "ProviderConfig", "TranscriptionJob", "HotwordEntry",
    "init_db", "migrate_schema", "backfill_user_id", "ensure_columns",
]
```

- [ ] **Step 4: Add `auto_correct` to `DEFAULT_SETTINGS`**

In `services/settings.py`, add to the `DEFAULT_SETTINGS` dict:

```python
DEFAULT_SETTINGS = {
    "bitrate_kbps": 128,
    "chunk_threshold_mb": 20,
    "max_concurrent_chunks": 4,
    "hf_token": "",
    "auto_correct": True,
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_database_hotwords.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add database/__init__.py services/settings.py tests/test_database_hotwords.py
git commit -m "feat: add HotwordEntry table, Transcript correction columns, auto_correct setting"
```

---

### Task 3: Hotword CRUD service

**Files:**
- Create: `services/hotwords.py`
- Create: `tests/test_hotwords_service.py`

**Interfaces:**
- Consumes: `database.HotwordEntry` (Task 2).
- Produces: `list_hotwords(db, user_id) -> list[HotwordEntry]`, `add_hotword(db, user_id, term, source="manual") -> HotwordEntry` (returns existing entry if a case-insensitive duplicate exists, does not create a second row), `delete_hotword(db, user_id, hotword_id) -> bool` (returns `False` if not found or not owned by `user_id`).

- [ ] **Step 1: Write the failing test**

Create `tests/test_hotwords_service.py`:

```python
from database import User
from services.hotwords import add_hotword, delete_hotword, list_hotwords


def _make_user(db_session, username="alice"):
    user = db_session.query(User).filter(User.username == username).first()
    if user:
        return user
    user = User(username=username, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def test_add_and_list_hotwords(db_session):
    user = _make_user(db_session)
    add_hotword(db_session, user.id, "Groq")
    add_hotword(db_session, user.id, "Moonshine", source="extracted")

    entries = list_hotwords(db_session, user.id)
    assert {(e.term, e.source) for e in entries} == {("Groq", "manual"), ("Moonshine", "extracted")}


def test_add_hotword_dedups_case_insensitively(db_session):
    user = _make_user(db_session)
    first = add_hotword(db_session, user.id, "Groq")
    second = add_hotword(db_session, user.id, "groq", source="extracted")

    assert second.id == first.id
    assert len(list_hotwords(db_session, user.id)) == 1
    # original source is preserved, not overwritten by the dup attempt
    assert list_hotwords(db_session, user.id)[0].source == "manual"


def test_add_hotword_scopes_dedup_per_user(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    add_hotword(db_session, alice.id, "Groq")
    add_hotword(db_session, bob.id, "Groq")

    assert len(list_hotwords(db_session, alice.id)) == 1
    assert len(list_hotwords(db_session, bob.id)) == 1


def test_delete_hotword_removes_owned_entry(db_session):
    user = _make_user(db_session)
    entry = add_hotword(db_session, user.id, "Groq")

    assert delete_hotword(db_session, user.id, entry.id) is True
    assert list_hotwords(db_session, user.id) == []


def test_delete_hotword_refuses_other_users_entry(db_session):
    alice = _make_user(db_session, "alice")
    bob = _make_user(db_session, "bob")
    entry = add_hotword(db_session, alice.id, "Groq")

    assert delete_hotword(db_session, bob.id, entry.id) is False
    assert len(list_hotwords(db_session, alice.id)) == 1


def test_delete_hotword_returns_false_for_missing_id(db_session):
    user = _make_user(db_session)
    assert delete_hotword(db_session, user.id, 9999) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hotwords_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.hotwords'`

- [ ] **Step 3: Write `services/hotwords.py`**

```python
"""Persistent per-user hotword glossary — manual entries plus terms
auto-extracted from pasted meeting-context docs (see services/correction.py).
Feeds the post-hoc correction pass, never the transcription-time prompt."""
from database import HotwordEntry


def list_hotwords(db, user_id: int) -> list[HotwordEntry]:
    return db.query(HotwordEntry).filter(HotwordEntry.user_id == user_id).all()


def add_hotword(db, user_id: int, term: str, source: str = "manual") -> HotwordEntry:
    """Insert a new glossary term, or return the existing entry if this
    user already has the same term (case-insensitive). The existing
    entry's source is never overwritten by a later dup attempt."""
    term = term.strip()
    existing = (
        db.query(HotwordEntry)
        .filter(HotwordEntry.user_id == user_id)
        .filter(HotwordEntry.term.ilike(term))
        .first()
    )
    if existing:
        return existing

    entry = HotwordEntry(user_id=user_id, term=term, source=source)
    db.add(entry)
    db.commit()
    return entry


def delete_hotword(db, user_id: int, hotword_id: int) -> bool:
    entry = (
        db.query(HotwordEntry)
        .filter(HotwordEntry.id == hotword_id, HotwordEntry.user_id == user_id)
        .first()
    )
    if not entry:
        return False
    db.delete(entry)
    db.commit()
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hotwords_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/hotwords.py tests/test_hotwords_service.py
git commit -m "feat: add hotword glossary CRUD service with case-insensitive dedup"
```

---

### Task 4: Hotword API routes

**Files:**
- Modify: `app.py`
- Create: `tests/test_hotwords_api.py`

**Interfaces:**
- Consumes: `services.hotwords.{list_hotwords, add_hotword, delete_hotword}` (Task 3).
- Produces: `GET /api/hotwords`, `POST /api/hotwords`, `DELETE /api/hotwords/{id}` routes.

- [ ] **Step 1: Write the failing test**

Create `tests/test_hotwords_api.py`:

```python
def test_list_hotwords_empty_initially(client):
    response = client.get("/api/hotwords")
    assert response.status_code == 200
    assert response.json() == []


def test_add_hotword_via_api(client):
    response = client.post("/api/hotwords", json={"term": "Groq"})
    assert response.status_code == 200
    body = response.json()
    assert body["term"] == "Groq"
    assert body["source"] == "manual"

    listed = client.get("/api/hotwords").json()
    assert len(listed) == 1
    assert listed[0]["term"] == "Groq"


def test_add_hotword_requires_term(client):
    response = client.post("/api/hotwords", json={"term": ""})
    assert response.status_code == 400


def test_delete_hotword_via_api(client):
    created = client.post("/api/hotwords", json={"term": "Groq"}).json()
    response = client.delete(f"/api/hotwords/{created['id']}")
    assert response.status_code == 200
    assert client.get("/api/hotwords").json() == []


def test_delete_missing_hotword_returns_404(client):
    response = client.delete("/api/hotwords/99999")
    assert response.status_code == 404


def test_hotwords_require_login(client):
    client.post("/api/logout")
    response = client.get("/api/hotwords")
    assert response.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hotwords_api.py -v`
Expected: FAIL — 404 on `/api/hotwords` (route doesn't exist yet)

- [ ] **Step 3: Add routes to `app.py`**

Add the import at the top with the other `services.*` imports (near line 32):

```python
from services.hotwords import list_hotwords, add_hotword, delete_hotword
```

Add a new section after the existing `PUT /api/settings` route (after line 218, before the `# ── API Routes ──` comment):

```python
# ── Hotword Glossary ─────────────────────────────────────────────────────

def _serialize_hotword(h) -> dict:
    return {
        "id": h.id,
        "term": h.term,
        "source": h.source,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }


@app.get("/api/hotwords")
async def get_hotwords(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [_serialize_hotword(h) for h in list_hotwords(db, current_user.id)]


@app.post("/api/hotwords")
async def create_hotword(data: dict = Body(...), db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    term = (data.get("term") or "").strip()
    if not term:
        raise HTTPException(status_code=400, detail="term is required")
    entry = add_hotword(db, current_user.id, term)
    return _serialize_hotword(entry)


@app.delete("/api/hotwords/{hotword_id}")
async def remove_hotword(hotword_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    deleted = delete_hotword(db, current_user.id, hotword_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Hotword not found")
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hotwords_api.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_hotwords_api.py
git commit -m "feat: add /api/hotwords CRUD routes"
```

---

### Task 5: Correction service — extraction and correction LLM calls

**Files:**
- Create: `services/correction.py`
- Create: `tests/test_correction_service.py`

**Interfaces:**
- Consumes: `services.hotwords.{list_hotwords, add_hotword}` (Task 3), `database.Transcript` (existing + Task 2 columns).
- Produces: `async def extract_hotwords_from_doc(db, user_id, doc_text, api_key, provider_name="groq", model="llama-3.3-70b-versatile") -> list[str]` (returns extracted terms, also persists them via `add_hotword(..., source="extracted")`; returns `[]` and never raises on failure), `async def correct_transcript(db, transcript, api_key, provider_name="groq", model="llama-3.3-70b-versatile") -> None` (mutates `transcript.corrected_text`/`correction_error`/`correction_model` in place, commits, never raises).

- [ ] **Step 1: Write the failing test**

Create `tests/test_correction_service.py`:

```python
import json
from unittest.mock import AsyncMock, patch

from database import Transcript, User
from services.correction import correct_transcript, extract_hotwords_from_doc
from services.hotwords import list_hotwords


def _make_user_and_transcript(db_session, full_text="we discussed the api rate limiting"):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="completed", full_text=full_text)
    db_session.add(t)
    db_session.commit()
    return user, t


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _chat_completion_response(content: str):
    return _FakeResponse(200, {"choices": [{"message": {"content": content}}]})


def test_correct_transcript_sets_corrected_text_on_success(db_session):
    user, transcript = _make_user_and_transcript(db_session)

    fake_post = AsyncMock(return_value=_chat_completion_response("We discussed the API rate limiting."))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    db_session.refresh(transcript)
    assert transcript.corrected_text == "We discussed the API rate limiting."
    assert transcript.correction_model == "groq/llama-3.3-70b-versatile"
    assert transcript.correction_error is None


def test_correct_transcript_sets_error_on_failure_without_raising(db_session):
    user, transcript = _make_user_and_transcript(db_session)

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    db_session.refresh(transcript)
    assert transcript.corrected_text is None
    assert "500" in transcript.correction_error


def test_correct_transcript_includes_glossary_in_prompt(db_session):
    from services.hotwords import add_hotword

    user, transcript = _make_user_and_transcript(db_session)
    add_hotword(db_session, user.id, "Groqonomicon")

    fake_post = AsyncMock(return_value=_chat_completion_response("corrected"))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        asyncio.run(correct_transcript(db_session, transcript, api_key="fake-key"))

    sent_json = fake_post.call_args.kwargs["json"]
    prompt_text = json.dumps(sent_json)
    assert "Groqonomicon" in prompt_text


def test_extract_hotwords_from_doc_persists_terms(db_session):
    user = User(username="bob", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    fake_post = AsyncMock(
        return_value=_chat_completion_response(json.dumps({"terms": ["Acme Corp", "Q3 rollout"]}))
    )
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        terms = asyncio.run(
            extract_hotwords_from_doc(db_session, user.id, "Agenda: Acme Corp Q3 rollout status", api_key="fake-key")
        )

    assert set(terms) == {"Acme Corp", "Q3 rollout"}
    stored = {h.term for h in list_hotwords(db_session, user.id)}
    assert stored == {"Acme Corp", "Q3 rollout"}
    assert all(h.source == "extracted" for h in list_hotwords(db_session, user.id))


def test_extract_hotwords_from_doc_returns_empty_on_failure(db_session):
    user = User(username="carol", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse(500, {"error": "boom"}))
    with patch("httpx.AsyncClient.post", fake_post):
        import asyncio
        terms = asyncio.run(
            extract_hotwords_from_doc(db_session, user.id, "some doc text", api_key="fake-key")
        )

    assert terms == []
    assert list_hotwords(db_session, user.id) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_correction_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.correction'`

- [ ] **Step 3: Write `services/correction.py`**

```python
"""Post-hoc transcript correction using a user-maintained hotword glossary.

Two LLM-backed operations, both non-fatal (never raise): pulling candidate
vocabulary out of a pasted meeting-context doc into the glossary, and using
the full glossary to clean up a finished transcript's full_text. Whisper's
transcription-time `prompt` param is never touched by either — see
docs/superpowers/specs/2026-07-02-hotword-glossary-and-correction-pass-design.md
for why a same-audio pre-pass was rejected in favor of this approach.
"""
import json

import httpx

from services.hotwords import list_hotwords, add_hotword

_API_BASES = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}
_DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _api_base(provider_name: str, provider_config: dict | None = None) -> str:
    if provider_name == "local":
        return (provider_config or {}).get("api_url", "http://localhost:11434/v1")
    return _API_BASES.get(provider_name, _API_BASES["groq"])


async def _chat_completion(prompt: str, api_key: str, provider_name: str, model: str, json_mode: bool) -> str:
    """Raises on any failure — callers catch and set their own error field."""
    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You output only what is requested, no commentary."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    if json_mode and provider_name in ("groq", "openai"):
        request_body["response_format"] = {"type": "json_object"}

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{_api_base(provider_name)}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=request_body,
        )

    if response.status_code != 200:
        raise RuntimeError(f"LLM API error ({response.status_code}): {response.text}")

    return response.json()["choices"][0]["message"]["content"]


async def correct_transcript(
    db, transcript, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
) -> None:
    """Non-fatal: sets transcript.corrected_text + correction_model on
    success, or transcript.correction_error on failure. Never raises.
    full_text and segments are never modified."""
    glossary = [h.term for h in list_hotwords(db, transcript.user_id)]
    glossary_block = (
        f"Known names/jargon that may appear (spell these correctly if you "
        f"see a close phonetic match): {', '.join(glossary)}\n\n"
        if glossary else ""
    )
    prompt = (
        "Below is a raw speech-to-text transcript that may contain misheard "
        "words, awkward grammar, or missing punctuation. Rewrite it to fix "
        "likely transcription errors and improve readability, WITHOUT "
        "changing its meaning or adding any new content. Return only the "
        "corrected transcript text, nothing else.\n\n"
        f"{glossary_block}"
        f"TRANSCRIPT:\n{transcript.full_text}"
    )

    try:
        corrected = await _chat_completion(prompt, api_key, provider_name, model, json_mode=False)
        transcript.corrected_text = corrected.strip()
        transcript.correction_model = f"{provider_name}/{model}"
        transcript.correction_error = None
    except Exception as e:
        transcript.correction_error = str(e)

    db.commit()


async def extract_hotwords_from_doc(
    db, user_id: int, doc_text: str, api_key: str, provider_name: str = "groq", model: str = _DEFAULT_MODEL,
) -> list[str]:
    """Non-fatal: returns the list of newly-seen extracted terms (also
    persisted via add_hotword with source='extracted'), or [] on any
    failure. Never raises."""
    prompt = (
        "Extract a short list of proper nouns, names, and domain-specific "
        "jargon from the following document that might appear in a related "
        "meeting recording. Respond with JSON: {\"terms\": [\"...\", ...]}. "
        "Keep the list short (under 20 items) and skip common words.\n\n"
        f"DOCUMENT:\n{doc_text}"
    )

    try:
        content = await _chat_completion(prompt, api_key, provider_name, model, json_mode=True)
        terms = json.loads(content).get("terms", [])
    except Exception:
        return []

    for term in terms:
        add_hotword(db, user_id, term, source="extracted")
    return terms
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_correction_service.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add services/correction.py tests/test_correction_service.py
git commit -m "feat: add correction service (glossary extraction + transcript correction)"
```

---

### Task 6: Wire context_doc extraction into /api/transcribe

**Files:**
- Modify: `app.py`
- Create: `tests/test_transcribe_context_doc.py`

**Interfaces:**
- Consumes: `services.correction.extract_hotwords_from_doc` (Task 5).
- Produces: `POST /api/transcribe` accepts optional `context_doc: str` form field.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcribe_context_doc.py`:

```python
import io
from unittest.mock import AsyncMock, patch


def test_transcribe_with_context_doc_extracts_hotwords(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})

    fake_extract = AsyncMock(return_value=["Acme Corp"])
    with patch("app.extract_hotwords_from_doc", fake_extract), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": "groq", "context_doc": "Agenda: Acme Corp kickoff"},
        )

    assert response.status_code == 200
    fake_extract.assert_awaited_once()
    call_kwargs = fake_extract.await_args.kwargs
    assert call_kwargs.get("doc_text") == "Agenda: Acme Corp kickoff" or fake_extract.await_args.args[2] == "Agenda: Acme Corp kickoff"


def test_transcribe_without_context_doc_skips_extraction(client):
    fake_extract = AsyncMock()
    with patch("app.extract_hotwords_from_doc", fake_extract), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        response = client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": "groq"},
        )

    assert response.status_code == 200
    fake_extract.assert_not_awaited()


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t
```

Note: this test patches `app.transcription_service.transcribe` to avoid a real provider call — the inline `/api/transcribe` path also runs audio transcode/chunk-threshold logic that isn't relevant here; a real audio file isn't needed since the provider call itself is stubbed out before it would inspect the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_transcribe_context_doc.py -v`
Expected: FAIL — `context_doc` unsupported / `AttributeError: module 'app' has no attribute 'extract_hotwords_from_doc'`

- [ ] **Step 3: Wire it into `app.py`**

Add the import near the other new import from Task 4/5:

```python
from services.correction import extract_hotwords_from_doc, correct_transcript
```

In the `transcribe_audio` route signature (around line 350), add the new form field after `num_speakers`:

```python
    num_speakers: Optional[int] = Form(None),
    context_doc: Optional[str] = Form(None),
```

Immediately after `user_settings = get_user_settings(db, current_user.id)` (around line 372), add the best-effort extraction call. It needs an API key — reuse the `prov_cfg` lookup that already happens a few lines later for `provider_config`, so move that lookup earlier. Replace this existing block:

```python
    # Get provider config
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
    provider_config = {}
    if prov_cfg:
        provider_config = {
            "api_key": prov_cfg.api_key,
            "api_url": prov_cfg.api_url,
            "default_model": prov_cfg.default_model or "",
        }
```

with (moved earlier, right after `user_settings = ...`, plus the extraction call):

```python
    # Get provider config
    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
    provider_config = {}
    if prov_cfg:
        provider_config = {
            "api_key": prov_cfg.api_key,
            "api_url": prov_cfg.api_url,
            "default_model": prov_cfg.default_model or "",
        }

    if context_doc and context_doc.strip():
        groq_cfg = db.query(ProviderConfig).filter(
            ProviderConfig.user_id == current_user.id,
            ProviderConfig.name == "groq",
        ).first()
        if groq_cfg and groq_cfg.api_key:
            try:
                await extract_hotwords_from_doc(db, current_user.id, context_doc, api_key=groq_cfg.api_key)
            except Exception as e:
                # Non-fatal: glossary-building side effect, never blocks transcription.
                print(f"[correction] non-fatal hotword extraction failure: {e}")
```

(then delete the now-duplicate original block further down where `prov_cfg`/`provider_config` used to be computed — there should be exactly one copy left after this edit).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_transcribe_context_doc.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: all tests from Tasks 1-6 pass

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_transcribe_context_doc.py
git commit -m "feat: extract hotwords from optional context_doc on /api/transcribe"
```

---

### Task 7: Wire automatic + manual correction pass into the inline transcribe path

**Files:**
- Modify: `app.py`
- Create: `tests/test_correction_inline_and_manual.py`

**Interfaces:**
- Consumes: `services.correction.correct_transcript` (Task 5), `services.settings.get_user_settings` (existing).
- Produces: automatic correction after inline transcription completes (gated by `auto_correct` setting); `POST /api/transcripts/{transcript_id}/correct` manual re-run endpoint; `_serialize_transcript` includes `corrected_text`, `correction_error`, `correction_model`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_correction_inline_and_manual.py`:

```python
import io
from unittest.mock import AsyncMock, patch


async def _stub_transcribe(db, user_id, **kwargs):
    from database import Transcript
    t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello world")
    db.add(t)
    db.commit()
    return t


def _upload(client, provider="groq"):
    with patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("meeting.mp3", io.BytesIO(b"fake audio bytes"), "audio/mpeg")},
            data={"provider": provider},
        )


def test_auto_correct_runs_after_inline_transcription(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    fake_correct = AsyncMock()

    async def _fake_correct(db, transcript, **kwargs):
        transcript.corrected_text = "Hello world."
        transcript.correction_model = "groq/llama-3.3-70b-versatile"
        db.commit()

    with patch("app.correct_transcript", side_effect=_fake_correct):
        response = _upload(client)

    assert response.status_code == 200
    body = response.json()
    assert body["corrected_text"] == "Hello world."
    assert body["correction_model"] == "groq/llama-3.3-70b-versatile"


def test_auto_correct_skipped_when_setting_disabled(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})
    fake_correct = AsyncMock()

    with patch("app.correct_transcript", fake_correct):
        response = _upload(client)

    assert response.status_code == 200
    fake_correct.assert_not_awaited()


def test_manual_correct_endpoint_reruns_with_different_model(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})

    async def _fake_correct(db, transcript, api_key, provider_name="groq", model="llama-3.3-70b-versatile"):
        transcript.corrected_text = f"corrected by {model}"
        transcript.correction_model = f"{provider_name}/{model}"
        db.commit()

    with patch("app.correct_transcript", side_effect=_fake_correct):
        upload_response = _upload(client)
        transcript_id = upload_response.json()["id"]

        rerun_response = client.post(
            f"/api/transcripts/{transcript_id}/correct",
            data={"provider": "groq", "model": "llama-3.1-8b-instant"},
        )

    assert rerun_response.status_code == 200
    body = rerun_response.json()
    assert body["corrected_text"] == "corrected by llama-3.1-8b-instant"
    assert body["correction_model"] == "groq/llama-3.1-8b-instant"


def test_manual_correct_requires_completed_transcript(client):
    from database import Transcript

    response = client.post("/api/transcripts/99999/correct", data={"provider": "groq"})
    assert response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_correction_inline_and_manual.py -v`
Expected: FAIL — `corrected_text` missing from response / 404 route not found

- [ ] **Step 3: Wire correction into the inline path and add the manual endpoint**

In `app.py`, inside `transcribe_audio`, find the diarization block that runs after the inline `transcription_service.transcribe(...)` call (the `if diarize and transcript.segments:` block, ending around line 472 with `return _serialize_transcript(db, transcript)`). Add the automatic correction call right after that block, before the `return`:

```python
        # Post-hoc correction pass — best-effort, mirrors diarization's
        # non-fatal handling. Uses groq by default, same as summarize().
        if user_settings.get("auto_correct", True):
            correction_cfg = db.query(ProviderConfig).filter(
                ProviderConfig.user_id == current_user.id,
                ProviderConfig.name == "groq",
            ).first()
            if correction_cfg and correction_cfg.api_key:
                try:
                    await correct_transcript(db, transcript, api_key=correction_cfg.api_key)
                except Exception as e:
                    print(f"[correction] non-fatal failure for transcript {transcript.id}: {e}")

        return _serialize_transcript(db, transcript)
```

Update `_serialize_transcript` (around line 135) to include the new fields:

```python
        "error": t.error,
        "corrected_text": t.corrected_text,
        "correction_error": t.correction_error,
        "correction_model": t.correction_model,
```

Add the manual re-run endpoint near the existing `summarize_transcript` route (after line 643):

```python
@app.post("/api/transcripts/{transcript_id}/correct")
async def correct_transcript_route(
    transcript_id: int,
    provider: str = Form("groq"),
    model: str = Form("llama-3.3-70b-versatile"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Manually (re)run the correction pass, e.g. to try a different
    provider/model against the same raw full_text."""
    transcript = db.query(Transcript).filter(
        Transcript.id == transcript_id, Transcript.user_id == current_user.id
    ).first()
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    if transcript.status not in ("completed", "partial"):
        raise HTTPException(status_code=400, detail=f"Transcript {transcript_id} is not completed")

    prov_cfg = db.query(ProviderConfig).filter(
        ProviderConfig.user_id == current_user.id,
        ProviderConfig.name == provider,
    ).first()
    api_key = prov_cfg.api_key if prov_cfg else ""

    await correct_transcript(db, transcript, api_key=api_key, provider_name=provider, model=model)
    return _serialize_transcript(db, transcript)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_correction_inline_and_manual.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: all tests from Tasks 1-7 pass

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_correction_inline_and_manual.py
git commit -m "feat: run correction pass after inline transcription, add manual rerun endpoint"
```

---

### Task 8: Wire automatic correction pass into the chunked finalize path

**Files:**
- Modify: `services/queue.py`
- Create: `tests/test_correction_chunked_finalize.py`

**Interfaces:**
- Consumes: `services.correction.correct_transcript` (Task 5), `services.settings.get_user_settings` (existing, already imported locally in `_finalize_if_done`).
- Produces: `_finalize_if_done` runs the correction pass exactly once per finalize, after the transcript is committed to `completed`/`partial`, gated by `auto_correct`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_correction_chunked_finalize.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from database import ProviderConfig, Transcript, TranscriptionJob, User
from services.diarization import DiarizationService
from services.queue import _finalize_if_done


def _setup_completed_chunks(db_session, auto_correct=True, with_groq_key=True):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    if not auto_correct:
        user.settings = {"auto_correct": False}
        db_session.commit()

    if with_groq_key:
        db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake-groq-key"))
        db_session.commit()

    transcript = Transcript(user_id=user.id, title="t", filename="f.mp3", status="processing")
    db_session.add(transcript)
    db_session.commit()

    db_session.add(TranscriptionJob(
        transcript_id=transcript.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="completed",
        result_json={
            "segments": [{"start": 0, "end": 5, "text": "hello world", "speaker": None, "confidence": None}],
            "language": "en", "model": "whisper-large-v3",
        },
    ))
    db_session.commit()
    return transcript


@pytest.mark.asyncio
async def test_finalize_runs_correction_when_enabled(db_session):
    transcript = _setup_completed_chunks(db_session)

    async def _fake_correct(db, t, api_key, provider_name="groq", model="llama-3.3-70b-versatile"):
        t.corrected_text = "Hello world."
        t.correction_model = f"{provider_name}/{model}"
        db.commit()

    with patch("services.queue.correct_transcript", side_effect=_fake_correct):
        await _finalize_if_done(db_session, transcript.id, DiarizationService())

    db_session.refresh(transcript)
    assert transcript.status == "completed"
    assert transcript.corrected_text == "Hello world."


@pytest.mark.asyncio
async def test_finalize_skips_correction_when_setting_disabled(db_session):
    transcript = _setup_completed_chunks(db_session, auto_correct=False)
    fake_correct = AsyncMock()

    with patch("services.queue.correct_transcript", fake_correct):
        await _finalize_if_done(db_session, transcript.id, DiarizationService())

    fake_correct.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_skips_correction_without_groq_key(db_session):
    transcript = _setup_completed_chunks(db_session, with_groq_key=False)
    fake_correct = AsyncMock()

    with patch("services.queue.correct_transcript", fake_correct):
        await _finalize_if_done(db_session, transcript.id, DiarizationService())

    fake_correct.assert_not_awaited()
```

Add `pytest-asyncio` mode config so `@pytest.mark.asyncio` tests run — create `pytest.ini` at the repo root:

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_correction_chunked_finalize.py -v`
Expected: FAIL — `corrected_text` stays `None` (correction never wired in) / `AttributeError: module 'services.queue' has no attribute 'correct_transcript'`

- [ ] **Step 3: Wire it into `_finalize_if_done`**

In `services/queue.py`, add the import near the top of the file with the other imports:

```python
from services.correction import correct_transcript
```

At the end of `_finalize_if_done`, after the existing final block:

```python
    transcript.segments = segments
    transcript.full_text = full_text
    transcript.duration_seconds = duration_seconds
    transcript.status = new_status
    if speaker_count is not None:
        transcript.speaker_count = speaker_count
    transcript.updated_at = datetime.datetime.utcnow()
    db.commit()
```

add:

```python
    if new_status in ("completed", "partial"):
        from services.settings import get_user_settings  # local import avoids a module-load cycle with app.py
        user_settings = get_user_settings(db, transcript.user_id)
        if user_settings.get("auto_correct", True):
            from database import ProviderConfig
            groq_cfg = db.query(ProviderConfig).filter(
                ProviderConfig.user_id == transcript.user_id,
                ProviderConfig.name == "groq",
            ).first()
            if groq_cfg and groq_cfg.api_key:
                try:
                    await correct_transcript(db, transcript, api_key=groq_cfg.api_key)
                except Exception as e:
                    print(f"[queue] non-fatal correction failure for transcript {transcript_id}: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_correction_chunked_finalize.py -v`
Expected: 3 passed

- [ ] **Step 5: Run the full test suite to check for regressions**

Run: `pytest -v`
Expected: all tests from Tasks 1-8 pass

- [ ] **Step 6: Commit**

```bash
git add services/queue.py tests/test_correction_chunked_finalize.py pytest.ini
git commit -m "feat: run correction pass once after chunked-path finalize"
```

---

## Spec coverage check (self-review)

- Persistent hotword glossary + CRUD → Tasks 2, 3, 4.
- Per-transcript context doc → auto-extraction → Task 6.
- Correction pass (automatic, non-fatal, full_text only) → Tasks 5, 7, 8.
- `corrected_text`/`correction_error` storage, raw text untouched → Task 2 (columns), enforced by Task 5's implementation (only ever sets new columns).
- `auto_correct` setting → Task 2 (default), Tasks 7/8 (gating).
- Manual re-run with `correction_model` tracking (added to spec mid-session) → Task 2 (column), Task 7 (endpoint).
- Frontend UI (glossary management, context-doc textarea, raw/corrected toggle, model picker) → explicitly out of scope for this backend-focused plan per the spec's "Frontend" section; not a task here. Flag to the user before considering this plan "done" end-to-end.
