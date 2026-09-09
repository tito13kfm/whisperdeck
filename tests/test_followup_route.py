"""Follow-up session routes (issue #253), slice 2.

POST /api/transcripts/{id}/followup            start the generate phase
POST /api/transcripts/{id}/followup/save-draft persist result_json["draft"]
POST /api/transcripts/{id}/followup/apply      enqueue the apply phase
POST /api/transcripts/{id}/followup/finalize   write rows + the finalized_at marker
GET  /api/transcripts/{id}/followup-items      list the finalized rows

Templated on tests/test_voice_dump_route.py, but three of that template's
behaviours are deliberately NOT mirrored and are pinned as such here:
row existence is not the "finalized" marker, finalize takes the lock even
when every item is discarded, and sequence_index restarts at 0 per apply job.

See docs/plans/14-followup-session.md.
"""
from unittest.mock import patch

import pytest
from database import Transcript, Summary, User, LlmJob, FollowupItem

from services.followups import MAX_ITEMS


def _testuser(db_session):
    return db_session.query(User).filter(User.username == "testuser").first()


def _make_meeting(db_session, *, key_points=None, action_items=None, decisions=None,
                  status="completed", kind="meeting", with_summary=True):
    """A completed meeting transcript, with a Summary row unless asked otherwise."""
    user = _testuser(db_session)
    t = Transcript(
        user_id=user.id, title="standup", filename="m.mp3", status=status,
        full_text="dana will look at the login bug", segments=[], kind=kind,
    )
    db_session.add(t)
    db_session.commit()
    summary = None
    if with_summary:
        summary = Summary(
            transcript_id=t.id,
            short_summary="A short standup.",
            key_points=key_points if key_points is not None else ["Login is flaky"],
            action_items=action_items if action_items is not None else ["Dana fixes login"],
            decisions=decisions if decisions is not None else ["Ship on Friday"],
            model="m", provider="local_llm",
        )
        db_session.add(summary)
        db_session.commit()
    db_session.refresh(t)
    return user, t, summary


def _seed_job(db_session, t, phase, status, *, provider="local_llm", model="m", **result):
    """Seed a followup LlmJob directly. The test client never runs the app
    lifespan, so no worker claims these — the status written here sticks."""
    user = _testuser(db_session)
    job = LlmJob(
        user_id=user.id, transcript_id=t.id, kind="followup",
        provider=provider, model=model, status=status,
        result_json={"phase": phase, **result},
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def _generate_item(key="a0", bucket="action_items", index=0, text="Dana fixes login",
                   item_type="action_item", **extra):
    return {
        "key": key, "source_bucket": bucket, "source_index": index,
        "source_text": text, "type": item_type, "owner": "", "due": None,
        "private": False, "needs_clarification": True,
        "reason": "no owner", "questions": ["Who owns this?"], "confidence": 0.4,
        **extra,
    }


def _input_item(key="a0", bucket="action_items", index=0, text="Dana fixes login",
                item_type="action_item", **extra):
    return {
        "key": key, "source_bucket": bucket, "source_index": index,
        "source_text": text, "type": item_type, "owner": "", "due": None,
        "private": False, "answers": [{"question": "Who owns this?", "answer": "Dana"}],
        **extra,
    }


def _proposal(key="a0", bucket="action_items", index=0, text="Dana fixes the login bug by Friday.",
              item_type="action_item", **extra):
    return {
        "key": key, "source_bucket": bucket, "source_index": index, "text": text,
        "type": item_type, "owner": "Dana", "due": None, "confidence": 0.9,
        "changed": True, "note": "", **extra,
    }


def _apply_job(db_session, t, *, status="completed", inputs=None, proposals=None, **extra):
    return _seed_job(
        db_session, t, "apply", status,
        generate_job_id=1,
        summary_snapshot={"short_summary": "", "key_points": [], "action_items": [],
                          "decisions": [], "created_at": "2026-01-01T00:00:00"},
        input=inputs if inputs is not None else [_input_item()],
        proposals=proposals if proposals is not None else [_proposal()],
        **extra,
    )


# ── POST /followup (start, generate phase) ──────────────────────────────


def test_start_followup_enqueues_generate_job_with_snapshot_and_seeds(client, db_session):
    user, t, summary = _make_meeting(db_session)
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm", "model": "m"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job"]["id"]
    job = db_session.get(LlmJob, job_id)
    assert job.kind == "followup"
    rj = job.result_json
    # The input has to land in the INSERT commit, not a second one — a worker
    # can claim the job between two commits and see no input.
    assert rj["phase"] == "generate"
    assert rj["truncated"] is False
    assert rj["summary_snapshot"]["created_at"] == summary.created_at.isoformat()
    assert rj["summary_snapshot"]["action_items"] == ["Dana fixes login"]
    keys = [it["key"] for it in rj["items"]]
    assert keys == ["a0", "d0", "k0"]
    assert [it["source_bucket"] for it in rj["items"]] == ["action_items", "decisions", "key_points"]


def test_start_followup_snapshot_is_frozen_against_a_later_summary_rerun(client, db_session):
    user, t, summary = _make_meeting(db_session)
    client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    job = db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id).first()
    frozen = dict(job.result_json["summary_snapshot"])

    # Simulate the destructive in-place Summary upsert.
    summary.action_items = ["Someone else fixes login"]
    summary.created_at = None
    db_session.commit()
    db_session.refresh(job)
    assert job.result_json["summary_snapshot"] == frozen
    assert job.result_json["summary_snapshot"]["action_items"] == ["Dana fixes login"]


def test_start_followup_404_for_other_users_transcript(client, db_session):
    other = User(username="other_fu", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(user_id=other.id, title="x", filename="x.mp3", status="completed",
                   full_text="hi", segments=[], kind="meeting")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 404


@pytest.mark.parametrize("kind", ["voice_note", "voice_dump"])
def test_start_followup_400_for_single_speaker_kinds(client, db_session, kind):
    user, t, _ = _make_meeting(db_session, kind=kind)
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 400
    assert "meeting" in r.json()["detail"]


def test_start_followup_400_when_transcript_not_completed(client, db_session):
    user, t, _ = _make_meeting(db_session, status="processing")
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 400
    assert "not completed" in r.json()["detail"]


def test_start_followup_400_when_no_summary(client, db_session):
    user, t, _ = _make_meeting(db_session, with_summary=False)
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 400
    assert "Summarize" in r.json()["detail"]


def test_start_followup_400_when_summary_has_no_items(client, db_session):
    """An all-empty Summary row is normal (the upsert writes .get(bucket, []))
    and would otherwise produce a completed job with an empty draft and no
    explanation at all."""
    user, t, _ = _make_meeting(db_session, key_points=[], action_items=[], decisions=[])
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 400
    assert r.json()["detail"] == "This summary has no items to follow up on"
    assert db_session.query(LlmJob).filter(LlmJob.kind == "followup").count() == 0


def test_start_followup_400_when_summary_holds_only_blank_bullets(client, db_session):
    user, t, _ = _make_meeting(db_session, key_points=["  "], action_items=[""], decisions=[])
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 400
    assert r.json()["detail"] == "This summary has no items to follow up on"


def test_start_followup_409_while_a_summary_job_is_running(client, db_session):
    user, t, _ = _make_meeting(db_session)
    db_session.add(LlmJob(user_id=user.id, transcript_id=t.id, kind="summary",
                          provider="local_llm", model="m", status="running"))
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 409
    assert "summary" in r.json()["detail"].lower()


def test_start_followup_400_when_keyed_provider_has_no_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "groq"})
    assert r.status_code == 400
    assert "groq API key" in r.json()["detail"]


def test_start_followup_returns_the_running_generate_job(client, db_session):
    user, t, _ = _make_meeting(db_session)
    existing = _seed_job(db_session, t, "generate", "running", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 200
    assert r.json()["job"]["id"] == existing.id
    assert db_session.query(LlmJob).filter(LlmJob.kind == "followup").count() == 1


def test_start_followup_409_while_an_apply_job_is_running(client, db_session):
    """generate and apply share one kind, so get_active_job matches an
    in-flight apply too. Without the phase check the route would hand the
    apply job back with 200 and the UI would render `applying`."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t, status="running")
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 409
    assert "being applied" in r.json()["detail"]


def test_start_followup_records_truncation_flag(client, db_session):
    user, t, _ = _make_meeting(
        db_session,
        action_items=[f"item {i}" for i in range(MAX_ITEMS)],
        decisions=["dropped by the cap"], key_points=[],
    )
    r = client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    assert r.status_code == 200
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.result_json["truncated"] is True
    assert len(job.result_json["items"]) == MAX_ITEMS


# ── POST /followup/save-draft ───────────────────────────────────────────


def test_save_draft_writes_only_the_draft_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    job = _seed_job(db_session, t, "generate", "completed", items=[_generate_item()],
                    summary_snapshot={"created_at": "2026-01-01T00:00:00"})
    before = dict(job.result_json)
    draft = [{"key": "a0", "type": "decision", "owner": "Dana", "due": "2026-02-01",
              "private": True, "answers": [{"question": "Who owns this?", "answer": "Dana"}]}]
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={"items": draft})
    assert r.status_code == 200
    db_session.refresh(job)
    assert job.result_json["draft"] == draft
    for key, value in before.items():
        assert job.result_json[key] == value, f"save-draft must not touch result_json[{key!r}]"


def test_save_draft_404_when_no_followup_job(client, db_session):
    user, t, _ = _make_meeting(db_session)
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={"items": []})
    assert r.status_code == 404
    assert r.json()["detail"] == "No follow-up job found for this transcript"


def test_save_draft_400_on_a_bare_array_body(client, db_session):
    """The envelope is {"items": [...]}, NOT the bare array voice-dump's
    save-draft takes. The JS module and the route must agree."""
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json=[{"key": "a0"}])
    assert r.status_code == 400


def test_save_draft_409_while_the_generate_job_is_still_running(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "running", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={"items": []})
    assert r.status_code == 409


def test_save_draft_allowed_on_a_failed_apply_job_against_its_input(client, db_session):
    """apply_failed is a draft-editable state; the editable draft is
    result_json["input"], so keys validate against `input`, not `items`."""
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t, status="failed")
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0", "type": "reference"}]})
    assert r.status_code == 200
    db_session.refresh(job)
    assert [d["key"] for d in job.result_json["draft"]] == ["a0"]
    assert job.result_json["draft"][0]["type"] == "reference"


def test_save_draft_on_an_apply_job_stores_the_full_input_shape(client, db_session):
    """rerun_llm_job promotes an apply job's saved draft straight into the
    next job's input[] (`old.get("draft") or old.get("input")`). Storing the
    sparse posted shape verbatim would hand the next apply run entries with no
    source_bucket / source_index / source_text — the prompt would rewrite from
    nothing and finalize would then violate the two NOT NULL provenance
    columns. So the apply-phase draft is normalized here."""
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t, status="failed", inputs=[
        _input_item(key="a0"),
        _input_item(key="d0", bucket="decisions", index=2, text="Ship on Friday",
                    item_type="decision"),
    ])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0", "type": "reference", "owner": "Ada",
                                     "answers": [{"question": "Who owns this?", "answer": "Ada"}]}]})
    assert r.status_code == 200
    db_session.refresh(job)
    draft = job.result_json["draft"]
    # Every source item is carried, not only the posted one.
    assert [d["key"] for d in draft] == ["a0", "d0"]
    a0 = draft[0]
    assert (a0["source_bucket"], a0["source_index"]) == ("action_items", 0)
    assert a0["source_text"] == "Dana fixes login"
    assert a0["type"] == "reference"
    assert a0["owner"] == "Ada"
    assert a0["answers"] == [{"question": "Who owns this?", "answer": "Ada"}]
    # The omitted item keeps its own provenance and type.
    assert (draft[1]["source_bucket"], draft[1]["source_index"]) == ("decisions", 2)
    assert draft[1]["type"] == "decision"


def test_save_draft_accepts_answers_as_bare_strings(client, db_session):
    """static/followup.js's draft cards carry `answers` as bare strings
    aligned to the generate item's question order. They are folded into
    {question, answer} pairs against those questions."""
    user, t, _ = _make_meeting(db_session)
    job = _seed_job(db_session, t, "generate", "completed", items=[
        _generate_item(questions=["Who owns this?", "By when?"]),
    ])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0", "answers": ["Dana", ""]}]})
    assert r.status_code == 200
    db_session.refresh(job)
    # The generate-phase draft is stored verbatim (the frontend overlays it).
    assert job.result_json["draft"][0]["answers"] == ["Dana", ""]
    # ...and apply folds the same shape into pairs, dropping the unanswered one.
    ap = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "provider": "local_llm",
        "items": [{"key": "a0", "answers": ["Dana", ""]}],
    })
    assert ap.status_code == 200, ap.text
    job2 = db_session.get(LlmJob, ap.json()["job"]["id"])
    assert job2.result_json["input"][0]["answers"] == [
        {"question": "Who owns this?", "answer": "Dana"},
    ]


def test_save_draft_409_once_finalized(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t, finalized_at="2026-01-02T00:00:00", finalized_count=1)
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0"}]})
    assert r.status_code == 409
    assert r.json()["detail"] == "This follow-up is already finalized"


def test_save_draft_400_on_unknown_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "zz9"}]})
    assert r.status_code == 400
    assert "Unknown follow-up item key" in r.json()["detail"]


def test_save_draft_400_on_bad_type(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0", "type": "epic"}]})
    assert r.status_code == 400
    assert "Unknown follow-up item type" in r.json()["detail"]


def test_save_draft_400_on_cap_violation(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0", "owner": "x" * 256}]})
    assert r.status_code == 400
    assert "owner" in r.json()["detail"]


def test_save_draft_400_on_over_long_answer(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={"items": [
        {"key": "a0", "answers": [{"question": "q", "answer": "y" * 1001}]},
    ]})
    assert r.status_code == 400
    assert "answer" in r.json()["detail"]


def test_save_draft_400_on_duplicate_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft",
                    json={"items": [{"key": "a0"}, {"key": "a0"}]})
    assert r.status_code == 400
    assert "Duplicate" in r.json()["detail"]


def test_save_draft_takes_no_provider_key(client, db_session):
    """save-draft and finalize make no LLM call, so they must not run
    require_provider_key — no key is saved for any provider in this fixture."""
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", provider="groq",
              items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={"items": []})
    assert r.status_code == 200


# ── POST /followup/apply ────────────────────────────────────────────────


def test_apply_enqueues_apply_job_carrying_provenance_and_answers(client, db_session):
    user, t, _ = _make_meeting(db_session)
    gen = _seed_job(db_session, t, "generate", "completed",
                    summary_snapshot={"created_at": "2026-01-01T00:00:00"},
                    items=[_generate_item(), _generate_item(key="d0", bucket="decisions",
                                                            text="Ship on Friday",
                                                            item_type="decision")])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "provider": "local_llm", "model": "m",
        "items": [
            {"key": "a0", "type": "action_item", "owner": "Dana", "due": "2026-02-01",
             "private": False, "answers": [{"question": "Who owns this?", "answer": "Dana"}]},
            {"key": "d0", "type": "decision", "private": True, "answers": []},
        ],
    })
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    rj = job.result_json
    assert rj["phase"] == "apply"
    assert rj["generate_job_id"] == gen.id
    assert rj["summary_snapshot"]["created_at"] == "2026-01-01T00:00:00"
    a0, d0 = rj["input"]
    assert (a0["source_bucket"], a0["source_index"]) == ("action_items", 0)
    assert a0["owner"] == "Dana" and a0["due"] == "2026-02-01"
    assert a0["answers"] == [{"question": "Who owns this?", "answer": "Dana"}]
    # input[] holds EVERY item, private ones included — _prompt_items is the
    # single filter that keeps them out of the prompt.
    assert d0["private"] is True
    assert (d0["source_bucket"], d0["source_index"]) == ("decisions", 0)


def test_apply_keeps_the_original_snapshot_so_the_stale_notice_survives(client, db_session):
    """The stale notice compares summary.created_at against the SNAPSHOT's
    created_at. Re-snapshotting here (or comparing job timestamps) would make
    a summary rerun between generate and apply silently disappear."""
    user, t, summary = _make_meeting(db_session)
    client.post(f"/api/transcripts/{t.id}/followup", data={"provider": "local_llm"})
    gen = db_session.query(LlmJob).filter(LlmJob.kind == "followup").first()
    original_created_at = gen.result_json["summary_snapshot"]["created_at"]
    gen.status = "completed"
    db_session.commit()

    # The user reruns Summarize; the upsert rewrites the buckets and bumps
    # created_at by hand.
    import datetime
    summary.action_items = ["Someone else fixes login"]
    summary.created_at = datetime.datetime(2030, 1, 1, 0, 0, 0)
    db_session.commit()

    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": [{"key": "a0"}]})
    assert r.status_code == 200
    apply_job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert apply_job.result_json["summary_snapshot"]["created_at"] == original_created_at
    assert apply_job.result_json["summary_snapshot"]["created_at"] != summary.created_at.isoformat()


def test_apply_404_when_no_followup_job(client, db_session):
    user, t, _ = _make_meeting(db_session)
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": []})
    assert r.status_code == 404
    assert r.json()["detail"] == "No follow-up job found for this transcript"


def test_apply_409_on_any_active_followup_job_not_just_an_apply(client, db_session):
    """The guard is get_active_job over the whole kind, not "is the latest job
    active": here the latest job is a draft-editable failed apply, so every
    other guard passes, and only the active-job check stands between the user
    and a second concurrent follow-up run.

    The distinct wording is what discriminates — enqueue_llm_job's own refusal
    to drop a result_json onto an active job would also 409, with the other
    message."""
    user, t, _ = _make_meeting(db_session)
    running = _seed_job(db_session, t, "generate", "running", items=[_generate_item()])
    latest = _apply_job(db_session, t, status="failed")
    assert latest.id > running.id
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": [{"key": "a0"}]})
    assert r.status_code == 409
    assert r.json()["detail"] == "A follow-up job is already running — wait for it to finish"


def test_apply_409_while_the_generate_job_is_still_running(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "running", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": []})
    assert r.status_code == 409


def test_apply_409_once_finalized(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t, finalized_at="2026-01-02T00:00:00", finalized_count=1)
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": [{"key": "a0"}]})
    assert r.status_code == 409
    assert r.json()["detail"] == "This follow-up is already finalized"


def test_apply_400_on_unknown_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": [{"key": "k9"}]})
    assert r.status_code == 400
    assert "Unknown follow-up item key" in r.json()["detail"]


def test_apply_400_on_missing_items_envelope(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={"provider": "local_llm"})
    assert r.status_code == 400


def test_apply_400_when_keyed_provider_has_no_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "groq", "items": [{"key": "a0"}]})
    assert r.status_code == 400
    assert "groq API key" in r.json()["detail"]
    assert db_session.query(LlmJob).filter(LlmJob.kind == "followup").count() == 1


def test_apply_from_a_failed_apply_job_carries_the_generate_job_id(client, db_session):
    user, t, _ = _make_meeting(db_session)
    failed = _apply_job(db_session, t, status="failed")
    failed.result_json = {**failed.result_json, "generate_job_id": 4242}
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": [{"key": "a0"}]})
    assert r.status_code == 200
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.result_json["generate_job_id"] == 4242


def test_apply_carries_omitted_source_items_forward(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[
        _generate_item(), _generate_item(key="d0", bucket="decisions", index=0,
                                         text="Ship on Friday", item_type="decision"),
    ])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply",
                    json={"provider": "local_llm", "items": [{"key": "a0"}]})
    assert r.status_code == 200
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert [it["key"] for it in job.result_json["input"]] == ["a0", "d0"]
    assert job.result_json["input"][1]["type"] == "decision"
    assert job.result_json["input"][1]["answers"] == []


# ── POST /followup/finalize ─────────────────────────────────────────────


def test_finalize_writes_rows_and_the_marker(client, db_session):
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "Dana fixes the login bug by Friday.",
         "owner": "Dana", "due": "2026-02-01", "private": False},
    ]})
    assert r.status_code == 200, r.text
    out = r.json()["items"]
    assert len(out) == 1
    assert out[0]["sequence_index"] == 0
    assert out[0]["item_type"] == "action_item"
    assert out[0]["owner"] == "Dana"
    assert out[0]["due"] == "2026-02-01"
    assert out[0]["clarifications"] == [{"question": "Who owns this?", "answer": "Dana"}]
    assert out[0]["source_job_id"] == job.id
    db_session.refresh(job)
    assert job.result_json["finalized_at"]
    assert job.result_json["finalized_count"] == 1
    # Merge, never overwrite: the phase, the snapshot, the input and the
    # proposals all have to survive the marker write, or every later guard
    # (and the read-only finalized list) reads None.
    assert job.result_json["phase"] == "apply"
    assert job.result_json["summary_snapshot"]["created_at"] == "2026-01-01T00:00:00"
    assert [it["key"] for it in job.result_json["input"]] == ["a0"]
    assert [p["key"] for p in job.result_json["proposals"]] == ["a0"]
    # The marker is written in the same transaction as the inserts, and the
    # job's own status is untouched.
    assert job.status == "completed"


def test_finalize_provenance_comes_from_the_input_entry_not_the_key(client, db_session):
    """source_bucket / source_index are read off the matched input[] entry and
    NEVER reverse-parsed out of `key`. Here the two disagree on purpose: a
    parser would derive ("action_items", 3) from the key "a3"."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(
        db_session, t,
        inputs=[_input_item(key="a3", bucket="decisions", index=7, text="Ship on Friday")],
        proposals=[_proposal(key="a3", bucket="action_items", index=3)],
    )
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a3", "type": "decision", "text": "We ship on Friday."},
    ]})
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item["source_bucket"] == "decisions"
    assert item["source_index"] == 7
    assert item["source_text"] == "Ship on Friday"


def test_finalize_all_discarded_still_writes_the_marker(client, db_session):
    """Do NOT copy the voice-dump early return: an all-discarded finalize
    inserts zero rows but must still take the lock and stamp the marker, or
    the review card stays live forever and finalize stays repeatable."""
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t)
    payload = {"items": [{"key": "a0", "type": "action_item", "text": "whatever",
                          "discarded": True}]}
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json=payload)
    assert r.status_code == 200
    assert r.json()["items"] == []
    db_session.refresh(job)
    assert job.result_json["finalized_at"]
    assert job.result_json["finalized_count"] == 0
    assert db_session.query(FollowupItem).filter(FollowupItem.transcript_id == t.id).count() == 0
    # ...and the second call is refused by the marker, not by row existence.
    again = client.post(f"/api/transcripts/{t.id}/followup/finalize", json=payload)
    assert again.status_code == 409
    assert again.json()["detail"] == "This follow-up is already finalized"


def test_finalize_409_on_a_second_call(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    payload = {"items": [{"key": "a0", "type": "action_item", "text": "Dana fixes login."}]}
    assert client.post(f"/api/transcripts/{t.id}/followup/finalize", json=payload).status_code == 200
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json=payload)
    assert r.status_code == 409
    assert db_session.query(FollowupItem).filter(FollowupItem.transcript_id == t.id).count() == 1


def test_finalize_unique_constraint_catches_a_double_insert(client, db_session):
    """The last-resort integrity net behind the marker. It only works because
    sequence_index restarts at 0 per apply job — a transcript-wide max+1
    offset (the voice-dump formula) would never collide and this path would
    be dead code."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    payload = {"items": [{"key": "a0", "type": "action_item", "text": "Dana fixes login."}]}
    assert client.post(f"/api/transcripts/{t.id}/followup/finalize", json=payload).status_code == 200
    with patch("app._followup_finalized", return_value=False):
        r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json=payload)
    assert r.status_code == 409
    assert db_session.query(FollowupItem).filter(FollowupItem.transcript_id == t.id).count() == 1


def test_finalize_409_when_the_latest_job_is_a_generate_job(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": []})
    assert r.status_code == 409
    assert "apply job" in r.json()["detail"]


def test_finalize_409_when_the_apply_job_has_not_completed(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t, status="failed")
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": []})
    assert r.status_code == 409


def test_finalize_400_on_a_key_outside_proposals(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "d0", "type": "decision", "text": "Ship on Friday."},
    ]})
    assert r.status_code == 400
    assert "Unknown follow-up item key" in r.json()["detail"]
    assert db_session.query(FollowupItem).count() == 0


def test_finalize_400_on_a_bad_type(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "epic", "text": "Dana fixes login."},
    ]})
    assert r.status_code == 400
    assert "Unknown follow-up item type" in r.json()["detail"]


def test_finalize_400_on_empty_text(client, db_session):
    """Strict here, no silent fallback to source_text — these become rows."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "   "},
    ]})
    assert r.status_code == 400
    assert "text cannot be empty" in r.json()["detail"]


def test_finalize_400_on_over_cap_text(client, db_session):
    """Strict, like save-draft: an over-cap field is refused, never silently
    truncated into a durable row."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "x" * 601},
    ]})
    assert r.status_code == 400
    assert "600 characters" in r.json()["detail"]


def test_finalize_400_on_a_non_string_owner(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "Dana fixes login.", "owner": 7},
    ]})
    assert r.status_code == 400
    assert "owner" in r.json()["detail"]


def test_finalize_400_on_over_cap_due(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "Dana fixes login.", "due": "d" * 65},
    ]})
    assert r.status_code == 400
    assert "due" in r.json()["detail"]


def test_finalize_400_when_over_max_items(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "x"} for _ in range(MAX_ITEMS + 1)
    ]})
    assert r.status_code == 400
    assert "Too many" in r.json()["detail"]


def test_finalize_numbers_sequence_index_from_zero_per_job(client, db_session):
    """Not a transcript-wide max+1: rows from different jobs stay
    distinguishable by source_job_id, and restarting at 0 is what arms the
    unique constraint."""
    user, t, _ = _make_meeting(db_session)
    first = _apply_job(db_session, t)
    client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "Round one."},
    ]})
    second = _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "Round two."},
    ]})
    assert r.status_code == 200
    assert r.json()["items"][0]["sequence_index"] == 0
    assert r.json()["items"][0]["source_job_id"] == second.id
    assert first.id != second.id


def test_finalize_takes_no_provider_key(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t, provider="groq")
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": [
        {"key": "a0", "type": "action_item", "text": "Dana fixes login."},
    ]})
    assert r.status_code == 200


def test_finalize_404_for_other_users_transcript(client, db_session):
    other = User(username="other_fu_fin", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(user_id=other.id, title="x", filename="x.mp3", status="completed",
                   full_text="hi", segments=[], kind="meeting")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={"items": []})
    assert r.status_code == 404


# ── GET /followup-items ─────────────────────────────────────────────────


def test_get_followup_items_empty(client, db_session):
    user, t, _ = _make_meeting(db_session)
    r = client.get(f"/api/transcripts/{t.id}/followup-items")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_get_followup_items_returns_rows_in_order_including_private(client, db_session):
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t)
    for idx, (key, private) in enumerate([("a0", False), ("a0", True)]):
        db_session.add(FollowupItem(
            user_id=user.id, transcript_id=t.id, source_job_id=job.id,
            sequence_index=idx, item_type="action_item", text=f"item {idx}",
            owner="Dana", due=None, private=private, confidence=0.5,
            source_bucket="action_items", source_index=idx, source_text="src",
            clarifications=[], model="m", provider="local_llm",
        ))
    db_session.commit()
    r = client.get(f"/api/transcripts/{t.id}/followup-items")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [it["sequence_index"] for it in items] == [0, 1]
    # `private` has no consumer today — the list is returned unfiltered.
    assert [it["private"] for it in items] == [False, True]


def test_get_followup_items_404_for_other_users_transcript(client, db_session):
    other = User(username="other_fu_get", password_hash="x", password_salt="y")
    db_session.add(other)
    db_session.commit()
    t = Transcript(user_id=other.id, title="x", filename="x.mp3", status="completed",
                   full_text="hi", segments=[], kind="meeting")
    db_session.add(t)
    db_session.commit()
    r = client.get(f"/api/transcripts/{t.id}/followup-items")
    assert r.status_code == 404


# ── registry sweep: /runs/{kind} allowlist and the transcript slot ──────


def test_runs_followup_is_allowed(client, db_session):
    """GET /runs/followup is the ONLY path to result_json — the transcript
    slot deliberately carries no result."""
    user, t, _ = _make_meeting(db_session)
    job = _seed_job(db_session, t, "generate", "completed", items=[_generate_item()])
    r = client.get(f"/api/transcripts/{t.id}/runs/followup")
    assert r.status_code == 200
    runs = r.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["id"] == job.id
    assert runs[0]["result"]["phase"] == "generate"


def test_transcript_detail_exposes_followup_job_without_its_result(client, db_session):
    """_serialize_transcript runs for every transcript on the list endpoint,
    and a follow-up job's result_json holds every item's text, the user's
    typed answers and the items they marked private. include_result stays
    off; do not copy the voice_match_job opt-in."""
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t)
    detail = client.get(f"/api/transcripts/{t.id}").json()
    assert detail["followup_job"] is not None
    assert detail["followup_job"]["id"] == job.id
    assert detail["followup_job"]["kind"] == "followup"
    assert "result" not in detail["followup_job"]


def test_transcript_detail_followup_job_is_null_without_a_job(client, db_session):
    user, t, _ = _make_meeting(db_session)
    detail = client.get(f"/api/transcripts/{t.id}").json()
    assert "followup_job" in detail
    assert detail["followup_job"] is None


# ── settings defaults ───────────────────────────────────────────────────


def test_followup_settings_defaults(client):
    settings = client.get("/api/settings").json()
    assert settings["followup_provider"] == "local_llm"
    assert settings["followup_model"] == "gpt-oss-20b-mxfp4-GGUF"


# ── Provider resolution: an off-machine send is strictly opt-in ───────────
# A request that omits provider/model falls back to the user's stored
# followup_provider / followup_model, never to a literal baked into the
# route. DEFAULT_SETTINGS ships local_llm, so a route with a baked-in cloud
# provider (or a baked-in "local_llm" that later drifts from the settings
# default) would decide an off-machine send on the user's behalf. Issue #456
# tracks the same fix owed to summarize / correct / the two rerun routes,
# which still carry Form("groq").

def test_start_followup_without_provider_uses_the_stored_setting(client, db_session):
    user, t, _ = _make_meeting(db_session)
    from services.settings import update_user_settings, DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["followup_provider"] == "local_llm"
    update_user_settings(db_session, user.id, {"followup_model": "stored-model"})
    r = client.post(f"/api/transcripts/{t.id}/followup", data={})
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.provider == "local_llm"
    assert job.model == "stored-model"


def test_start_followup_honours_an_explicitly_chosen_provider(client, db_session):
    """Opt-in still works: an explicit choice beats the stored default."""
    user, t, _ = _make_meeting(db_session)
    with patch("services.settings.require_provider_key", return_value=("k", {})):
        r = client.post(f"/api/transcripts/{t.id}/followup",
                        data={"provider": "groq", "model": "llama-3.3-70b-versatile"})
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.provider == "groq"


def test_apply_without_provider_uses_the_stored_setting(client, db_session):
    user, t, _ = _make_meeting(db_session)
    from services.settings import update_user_settings

    update_user_settings(db_session, user.id, {"followup_model": "stored-model"})
    _seed_job(db_session, t, "generate", "completed",
              summary_snapshot={"created_at": "2026-01-01T00:00:00"},
              items=[_generate_item()])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "items": [{"key": "a0", "type": "action_item", "owner": "", "due": "",
                   "private": False, "answers": []}],
    })
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.provider == "local_llm"
    assert job.model == "stored-model"


# ── The apply_failed retry must not lose the user's answers ───────────────
# input[] entries carry pair-form answers and no `questions` key. Reading only
# `questions` (on either side of the wire) left the answers empty and dropped
# everything the user typed on the way back to Apply, which is exactly the
# loss keeping followup out of AUTO_RETRY_KINDS exists to prevent.

def _failed_apply_job(db_session, t, **extra):
    return _seed_job(
        db_session, t, "apply", "failed",
        summary_snapshot={"created_at": "2026-01-01T00:00:00"},
        generate_job_id=1,
        input=[_input_item(answers=[{"question": "By when?", "answer": "Friday"}])],
        **extra,
    )


def test_save_draft_on_a_failed_apply_keeps_the_question_text(client, db_session):
    """A bare-string answer posted against an input[] entry has no questions
    list to match against; the question has to come back out of the stored
    pairs, or it is silently replaced by an empty string."""
    user, t, _ = _make_meeting(db_session)
    job = _failed_apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={
        "items": [{"key": "a0", "type": "action_item", "owner": "", "due": "",
                   "private": False, "answers": ["Monday"]}],
    })
    assert r.status_code == 200, r.text
    db_session.refresh(job)
    assert job.result_json["draft"][0]["answers"] == [
        {"question": "By when?", "answer": "Monday"}
    ]


def test_apply_from_a_failed_apply_carries_the_stored_answers(client, db_session):
    """Re-applying without re-posting the answers keeps them."""
    user, t, _ = _make_meeting(db_session)
    _failed_apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "items": [{"key": "a0", "type": "action_item", "owner": "", "due": "",
                   "private": False}],
    })
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.result_json["input"][0]["answers"] == [
        {"question": "By when?", "answer": "Friday"}
    ]


def test_apply_can_still_clear_an_answer(client, db_session):
    """An explicitly posted empty list clears, so carrying forward on an
    omitted key does not make an answer impossible to delete."""
    user, t, _ = _make_meeting(db_session)
    _failed_apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "items": [{"key": "a0", "type": "action_item", "owner": "", "due": "",
                   "private": False, "answers": []}],
    })
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.result_json["input"][0]["answers"] == []


def test_draft_source_prefers_a_saved_draft_over_input(client, db_session):
    """Mirrors rerun_llm_job's promotion. If the two disagree, a draft edit
    survives the Queue screen's Retry but is reverted by the Summary tab."""
    user, t, _ = _make_meeting(db_session)
    _failed_apply_job(db_session, t, draft=[_input_item(
        owner="Dana", answers=[{"question": "By when?", "answer": "Monday"}])])
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "items": [{"key": "a0", "type": "action_item", "owner": "", "due": "",
                   "private": False}],
    })
    assert r.status_code == 200, r.text
    job = db_session.get(LlmJob, r.json()["job"]["id"])
    assert job.result_json["input"][0]["answers"] == [
        {"question": "By when?", "answer": "Monday"}
    ]


def test_finalize_rejects_an_item_that_lost_its_provenance(client, db_session):
    """A missing source_bucket/source_index would violate the two NOT NULL
    columns and surface as a false "already finalized" 409."""
    user, t, _ = _make_meeting(db_session)
    proposal = _proposal()
    proposal.pop("source_bucket")
    job = _apply_job(db_session, t, inputs=[], proposals=[proposal])
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={
        "items": [{"key": "a0", "type": "action_item", "text": "Ship it.",
                   "owner": "", "due": "", "private": False, "discarded": False}],
    })
    assert r.status_code == 400, r.text
    assert "source bucket" in r.json()["detail"]
    db_session.refresh(job)
    assert "finalized_at" not in job.result_json


def test_finalize_rejects_a_non_boolean_private_flag(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/finalize", json={
        "items": [{"key": "a0", "type": "action_item", "text": "Ship it.",
                   "owner": "", "due": "", "private": "yes", "discarded": False}],
    })
    assert r.status_code == 400
    assert r.json()["detail"] == "private must be true or false"


# ── Review-screen edits are savable (user decision, 2026-09-09) ───────────
# The review cards let the user rewrite the proposed text, retype an owner and
# tick Discard. Those edits persist under result_json["review_draft"], a key of
# its own: rerun_llm_job promotes `draft` into the next job's input[], and a
# review overlay carries `text` rather than source_text/answers, so sharing the
# key would feed the wrong shape into a Queue-screen Retry.

def test_save_draft_on_a_completed_apply_stores_a_review_draft(client, db_session):
    user, t, _ = _make_meeting(db_session)
    job = _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={
        "items": [{"key": "a0", "type": "action_item", "text": "Dana fixes login by Monday.",
                   "owner": "Dana", "due": "2026-02-02", "private": False, "discarded": False}],
    })
    assert r.status_code == 200, r.text
    db_session.refresh(job)
    rj = job.result_json
    entry = rj["review_draft"][0]
    assert entry["text"] == "Dana fixes login by Monday."
    assert entry["owner"] == "Dana"
    assert entry["discarded"] is False
    # The proposals the overlay sits on top of are untouched, and the draft
    # key used by the failed-apply path is not written here.
    assert rj["proposals"][0]["text"] == "Dana fixes the login bug by Friday."
    assert "draft" not in rj


def test_review_draft_survives_a_reload(client, db_session):
    """The whole point of the decision: edit, reload, still there."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={
        "items": [{"key": "a0", "type": "action_item", "text": "Edited.",
                   "owner": "", "due": None, "private": False, "discarded": True}],
    })
    r = client.get(f"/api/transcripts/{t.id}/runs/followup")
    assert r.status_code == 200, r.text
    entry = r.json()["runs"][0]["result"]["review_draft"][0]
    assert entry["text"] == "Edited."
    assert entry["discarded"] is True


def test_save_draft_is_refused_once_finalized(client, db_session):
    """Widening save-draft to the review screen must not reopen a finalized
    follow-up."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t, finalized_at="2026-01-02T00:00:00", finalized_count=1)
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={
        "items": [{"key": "a0", "type": "action_item", "text": "Too late.",
                   "owner": "", "due": None, "private": False, "discarded": False}],
    })
    assert r.status_code == 409
    assert r.json()["detail"] == "This follow-up is already finalized"


def test_apply_is_still_refused_from_the_review_screen(client, db_session):
    """save-draft got its own predicate on purpose. Apply must NOT become
    available on a completed apply job, or the review screen would silently
    enqueue a second apply."""
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/apply", json={
        "items": [{"key": "a0", "type": "action_item", "owner": "", "due": "",
                   "private": False, "answers": []}],
    })
    assert r.status_code == 409
    assert r.json()["detail"] == "This follow-up draft can't be edited right now"


def test_review_draft_keys_are_validated_against_proposals(client, db_session):
    user, t, _ = _make_meeting(db_session)
    _apply_job(db_session, t)
    r = client.post(f"/api/transcripts/{t.id}/followup/save-draft", json={
        "items": [{"key": "nope", "type": "action_item", "text": "x",
                   "owner": "", "due": None, "private": False, "discarded": False}],
    })
    assert r.status_code == 400
