"""Tests for STT pricing catalog and cost computation functions.

Mutation-check standard: each test must fail if the function under test
returned a constant — i.e. a paid provider test that asserts cost > 0.0
would break if the function always returned 0.0.
"""
from datetime import datetime, timedelta, timezone

from database import Transcript, LlmJob, User
from services.pricing import get_stt_rate, get_provider_stt_rate, STT_RATES, LOCAL_STT_PROVIDERS
from services.cost import transcript_cost, provider_cost, estimate_cost


def _make_user(db_session):
    user = User(username="costtest", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def _make_transcript(db_session, user, provider="groq", model="whisper-large-v3-flash",
                     duration=60.0, status="completed"):
    t = Transcript(
        user_id=user.id, title="tc", filename="tc.mp3",
        provider=provider, model=model,
        duration_seconds=duration, status=status,
    )
    db_session.add(t)
    db_session.commit()
    return t


def _make_llm_job(db_session, user, transcript_id, kind, provider="local", model="llama3",
                  status="completed"):
    j = LlmJob(
        user_id=user.id, transcript_id=transcript_id,
        kind=kind, status=status,
        provider=provider, model=model,
    )
    db_session.add(j)
    db_session.commit()
    return j


# ── pricing.py: get_stt_rate ───────────────────────────────────────────────

def test_get_stt_rate_groq_flash():
    """Paid provider returns correct non-zero rate."""
    rate = get_stt_rate("groq", "whisper-large-v3-flash")
    assert rate["rate_per_minute"] == 0.004
    assert "Groq" in rate["rate_source"]


def test_get_stt_rate_local_builtin_free():
    """Local (builtin) provider returns cost 0.0, marks as free."""
    rate = get_stt_rate("builtin", "any")
    assert rate["rate_per_minute"] == 0.0
    assert "free" in rate["rate_source"].lower()


def test_get_stt_rate_unknown_no_raise():
    """Unknown provider never raises, returns sentinel with cost 0.0."""
    rate = get_stt_rate("madeup", "fakemodel")
    assert rate["rate_per_minute"] == 0.0
    assert "unknown" in rate["rate_source"]


def test_get_provider_stt_rate_matches_first():
    """get_provider_stt_rate finds first model for a provider with multiple entries."""
    rate = get_provider_stt_rate("groq")
    # groq has two models; first in dict iteration order is flash at 0.004
    assert rate["rate_per_minute"] in (0.004, 0.006)
    assert "Groq" in rate["rate_source"]


def test_get_provider_stt_rate_unknown():
    """get_provider_stt_rate for unknown provider returns free sentinel."""
    rate = get_provider_stt_rate("nonexistent")
    assert rate["rate_per_minute"] == 0.0
    assert "unknown" in rate["rate_source"]


# ── cost.py: transcript_cost ───────────────────────────────────────────────

def test_transcript_cost_paid_stt(db_session):
    """Paid STT (groq flash, 120s) computes cost = duration/60 * rate."""
    user = _make_user(db_session)
    transcript = _make_transcript(db_session, user, provider="groq",
                                   model="whisper-large-v3-flash",
                                   duration=120.0, status="completed")
    result = transcript_cost(db_session, transcript)
    # 120 / 60 * 0.004 = 0.008
    assert result["stt"]["cost"] == 0.008
    assert result["stt"]["rate_per_minute"] == 0.004
    assert result["stt"]["duration_seconds"] == 120.0
    assert result["total"] == 0.008
    assert result["correction"]["cost"] == 0.0
    assert result["summary"]["cost"] == 0.0


def test_transcript_cost_free_stt(db_session):
    """Local (builtin) STT always costs 0.0 regardless of duration."""
    user = _make_user(db_session)
    transcript = _make_transcript(db_session, user, provider="builtin",
                                   model="tiny", duration=120.0, status="completed")
    result = transcript_cost(db_session, transcript)
    assert result["stt"]["cost"] == 0.0
    assert result["stt"]["rate_per_minute"] == 0.0
    assert result["total"] == 0.0


def test_transcript_cost_local_llm_jobs(db_session):
    """LlmJobs with local provider report cost 0.0 and 'Local LLM (free)'."""
    user = _make_user(db_session)
    transcript = _make_transcript(db_session, user, provider="groq",
                                   model="whisper-large-v3-flash",
                                   duration=60.0, status="completed")
    _make_llm_job(db_session, user, transcript.id, kind="correction",
                  provider="local", model="llama3", status="completed")
    _make_llm_job(db_session, user, transcript.id, kind="summary",
                  provider="local", model="llama3", status="completed")
    result = transcript_cost(db_session, transcript)
    # STT: 60/60 * 0.004 = 0.004
    assert result["stt"]["cost"] == 0.004
    assert result["correction"]["cost"] == 0.0
    assert result["correction"]["rate_source"] == "Local LLM (free)"
    assert result["summary"]["cost"] == 0.0
    assert result["summary"]["rate_source"] == "Local LLM (free)"
    assert result["total"] == 0.004


# ── cost.py: estimate_cost ─────────────────────────────────────────────────

def test_estimate_cost():
    """estimate_cost computes cost = duration/60 * rate for given provider+model."""
    result = estimate_cost("groq", "whisper-large-v3-flash", 300.0)
    # 300 / 60 * 0.004 = 0.02
    assert result["cost"] == 0.02
    assert result["rate_per_minute"] == 0.004
    assert "Groq" in result["rate_source"]


# ── cost.py: provider_cost ─────────────────────────────────────────────────

def test_provider_cost_sums_correctly(db_session):
    """provider_cost aggregates duration across multiple transcripts for
    the same user+provider and multiplies by the provider's STT rate."""
    user = _make_user(db_session)
    _make_transcript(db_session, user, provider="groq", model="whisper-large-v3-flash",
                     duration=120.0, status="completed")
    _make_transcript(db_session, user, provider="groq", model="whisper-large-v3-turbo",
                     duration=180.0, status="completed")
    _make_transcript(db_session, user, provider="groq", model="whisper-large-v3-flash",
                     duration=60.0, status="failed")  # should NOT be counted

    since_loc = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    result = provider_cost(db_session, user.id, "groq", since_loc)
    assert result["total_seconds"] == 300.0
    # 300 / 60 * 0.004 = 0.02 (first match for groq is flash at 0.004)
    assert result["total_cost"] == 0.02
    assert result["rate_per_minute"] in (0.004, 0.006)
    assert "Groq" in result["rate_source"]
