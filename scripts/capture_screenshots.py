"""Capture/regenerate docs/USER-MANUAL.md screenshots via Playwright.

Starts an isolated WhisperDeck server, registers a test user, drives the real
transcribe -> diarize -> correct -> summarize pipeline, and captures every
screenshot the manual references:

  01 monitor / 03 tape library / 04 transcript detail / 07 voice roster /
  10 theme x4 are NOT regenerated here - they already look correct.

  Regenerated (were broken or mismatched):
    02-transcribe.png    - was a blank capture
    05-corrected.png     - was showing "CORRECTION FAILED"
    06-queue.png         - was showing failed jobs
    08-service-panel.png - was a blank capture
    09-hotwords.png      - recropped to just the Term Glossary widget
                           (there's no separate hotwords page/route)
    12-file-inventory.png - reseeded with one linked + one orphaned file

  New (fill manual gaps):
    15-version-compare.png - the version-compare modal (was a TODO in the manual)
    14-video-panel.png     - the floating video panel

  Unchanged (kept as-is, existing behavior):
    11-login.png, 13-enroll-speaker.png

The main upload is titled "test_meeting", uses the moonshine "base" model,
and hints 3 speakers - matching 04-transcript-detail.png's kept figure
(same audio, same speaker count) so the two don't visibly disagree.

Correction/summary need a real model, not a canned string - if the output
text is the visible subject of a figure (05, 15, the transcript behind 14),
placeholder text would look like the product is broken. So this script
prefers a real local LLM: it points the "local_llm" provider at the
project's own Lemonade server (http://localhost:13305/v1) when reachable.
Only if Lemonade is NOT running does it fall back to the committed hermetic
stub (scripts/llm_stub.py) - and if that fallback triggers, the
correction/summary text visible in 05/06/14/15 will be stub placeholder
text, not real model output. That's fine for exercising the pipeline
end-to-end, but re-run this script with Lemonade up before trusting those
four images for the manual.

Uses tests/fixtures/test_meeting_demo.mp3 (the Sarah/John/Emma dialogue from
scripts/generate_test_audio.py's DEFAULT_DIALOGUE - the same content
04-transcript-detail.png shows) and tests/fixtures/e2e_video_demo.mp4
(synthetic: SMPTE bars + that same TTS audio, synthesized on demand if
missing) - no real recordings. Both live under tests/fixtures/, which is
gitignored (local-only) - generate the audio fixture with
`python scripts/generate_test_audio.py --output tests/fixtures/test_meeting_demo.mp3`
if it's missing (needs Lemonade up locally).
"""
import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

import uvicorn

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import tempfile
DATA_DIR = tempfile.mkdtemp(prefix="whisperdeck-screenshots-")
os.environ["WHISPERDECK_DATA_DIR"] = DATA_DIR

OUT_DIR = os.path.join(REPO_ROOT, "screenshots")
os.makedirs(OUT_DIR, exist_ok=True)

# The Sarah/John/Emma "test_meeting" dialogue - same content as
# 04-transcript-detail.png's kept figure. Generate it with:
#   python scripts/generate_test_audio.py --output tests/fixtures/test_meeting_demo.mp3
# (needs Lemonade up locally for its kokoro-v1 TTS model). Deliberately NOT
# tests/fixtures/e2e_multispeaker.mp3 - that's a different (banking-themed)
# fixture used by the UX-audit skills, and its content doesn't match 04.
FIXTURE_AUDIO = os.path.join(REPO_ROOT, "tests", "fixtures", "test_meeting_demo.mp3")
FIXTURE_VIDEO = os.path.join(REPO_ROOT, "tests", "fixtures", "e2e_video_demo.mp4")

REAL_LEMONADE_URL = "http://127.0.0.1:13305/v1"
REAL_LEMONADE_MODEL = "gpt-oss-20b-mxfp4-GGUF"
HOTWORDS = ["WhisperDeck", "Moonshine", "pyannote"]  # matches the kept 08-service-panel figure


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


PORT = free_port()
BASE_URL = f"http://127.0.0.1:{PORT}"

if not os.path.exists(FIXTURE_AUDIO):
    print(f"FAIL: missing {FIXTURE_AUDIO} (tests/fixtures/ is gitignored - local-only). Generate it with:\n"
          f"  python scripts/generate_test_audio.py --output {FIXTURE_AUDIO}\n"
          "(needs Lemonade up locally for its kokoro-v1 TTS model)")
    sys.exit(1)

ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"

# Already ~47s at the source - just stage a copy named to match the
# transcript title used below, no trimming needed.
SHORT_AUDIO = os.path.join(DATA_DIR, "test_meeting.mp3")
shutil.copy(FIXTURE_AUDIO, SHORT_AUDIO)

# --- synthesize the video fixture on demand (also gitignored/local-only) -
# SMPTE bars + the same TTS meeting audio, never a real recording ---
if not os.path.exists(FIXTURE_VIDEO):
    print(f"{FIXTURE_VIDEO} not found - synthesizing it from the audio fixture")
    subprocess.run(
        [ffmpeg_bin, "-y", "-v", "error",
         "-f", "lavfi", "-i", "smptebars=s=960x540:d=15",
         "-ss", "0", "-t", "15", "-i", FIXTURE_AUDIO,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         FIXTURE_VIDEO],
        check=True,
    )

# --- start the isolated app server ---
import app as app_module
config = uvicorn.Config(app_module.app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on")
server = uvicorn.Server(config)
t = threading.Thread(target=server.run, daemon=True)
t.start()

deadline = time.time() + 20
while time.time() < deadline:
    try:
        with urllib.request.urlopen(BASE_URL + "/", timeout=1) as r:
            if r.status == 200:
                break
    except Exception:
        time.sleep(0.2)
else:
    print("FAIL: server did not start")
    sys.exit(1)

print(f"Server running on {BASE_URL}")

# --- prefer the real Lemonade server; only fall back to the hermetic stub
# (scripts/llm_stub.py) if it's not reachable - see module docstring ---
stub_proc = None
try:
    with urllib.request.urlopen(REAL_LEMONADE_URL + "/models", timeout=2) as r:
        real_llm_up = r.status == 200
except Exception:
    real_llm_up = False

if real_llm_up:
    LLM_URL = REAL_LEMONADE_URL
    LLM_MODEL = REAL_LEMONADE_MODEL
    print(f"Using real Lemonade server at {LLM_URL} for correction/summary")
else:
    print("WARNING: Lemonade not reachable at " + REAL_LEMONADE_URL
          + " - falling back to the hermetic stub. Correction/summary text in"
          " 05/06/14/15 will be stub placeholder text, not real model output."
          " Re-run with Lemonade up before trusting those images for the manual.")
    STUB_PORT = free_port()
    LLM_URL = f"http://127.0.0.1:{STUB_PORT}/v1"
    LLM_MODEL = "gpt-oss-20b-mxfp4-GGUF"
    stub_env = dict(os.environ)
    stub_env["STUB_PORT"] = str(STUB_PORT)
    stub_env["STUB_MODEL"] = LLM_MODEL
    stub_env["STUB_DELAY"] = "3"
    stub_proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO_ROOT, "scripts", "llm_stub.py")],
        env=stub_env,
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(LLM_URL + "/models", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        print("FAIL: llm stub did not start")
        stub_proc.terminate()
        sys.exit(1)
    print(f"LLM stub running on {LLM_URL}")

# --- register + login test user via API (HTTP session, used for polling) ---
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def api(method, path, body=None):
    token = json.loads(opener.open(BASE_URL + "/api/csrf-token", timeout=5).read())["token"]
    headers = {"Content-Type": "application/json", "X-CSRF-Token": token}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        resp = opener.open(req, timeout=15)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise RuntimeError(f"{method} {path} -> {e.code}: {body_text}")


try:
    api("POST", "/api/register", {"username": "docs", "password": "docs_pass_123"})
    print("Registered user 'docs'")
except RuntimeError as e:
    err = str(e)
    if "400" in err or "409" in err or "already taken" in err.lower():
        print("User 'docs' already exists")
    else:
        raise

api("POST", "/api/login", {"username": "docs", "password": "docs_pass_123"})
print("Logged in via API (polling session)")

api("PUT", "/api/providers/local", {"api_key": "not-needed", "api_url": LLM_URL, "default_model": LLM_MODEL})
api("PUT", "/api/providers/local_llm", {"api_key": "not-needed", "api_url": LLM_URL, "default_model": LLM_MODEL})
api("PUT", "/api/settings", {
    "correction_provider": "local_llm", "correction_model": LLM_MODEL,
    "summary_provider": "local_llm", "summary_model": LLM_MODEL,
    "auto_correct": True,
})
print("Configured providers/settings")

for term in HOTWORDS:
    try:
        api("POST", "/api/hotwords", {"term": term})
    except RuntimeError:
        pass  # already exists
print(f"Seeded hotwords: {', '.join(HOTWORDS)}")


def poll_transcript(tid, want_statuses, timeout=150):
    """Poll GET /api/transcripts/{id} until status is in want_statuses (or timeout)."""
    deadline_ = time.time() + timeout
    last = None
    while time.time() < deadline_:
        last = api("GET", f"/api/transcripts/{tid}")
        if last.get("status") in want_statuses:
            return last
        time.sleep(1)
    return last


def poll_correction_done(tid, timeout=90):
    deadline_ = time.time() + timeout
    last = None
    while time.time() < deadline_:
        last = api("GET", f"/api/transcripts/{tid}")
        job = last.get("correction_job")
        if job and job.get("status") in ("done", "failed"):
            return last
        time.sleep(1)
    return last


# --- seed one orphaned file for the File Inventory screenshot (before the
# real upload below, so it sorts oldest/first and reads clearly as unrelated) ---
uploads_dir = os.path.join(DATA_DIR, "uploads")
os.makedirs(uploads_dir, exist_ok=True)
orphan_path = os.path.join(uploads_dir, "orphaned_leftover_demo.mp3")
shutil.copy(SHORT_AUDIO, orphan_path)
print("Seeded one orphaned upload for the File Inventory screenshot")

# --- Playwright: drive the real UI ---
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    # 1. Login page (no cookies)
    ctx1 = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx1.new_page()
    pg.goto(BASE_URL + "/", wait_until="networkidle")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=os.path.join(OUT_DIR, "11-login.png"))
    print("Captured: 11-login.png")
    ctx1.close()

    # 2. Log in via browser for everything else (own session/CSRF token)
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx2.new_page()
    pg.goto(BASE_URL + "/", wait_until="networkidle")
    pg.wait_for_timeout(500)
    pg.fill("input[type='text'], input[name='username']", "docs")
    pg.fill("input[type='password']", "docs_pass_123")
    pg.click("button[type='submit']")
    pg.wait_for_selector("#app-shell", state="visible", timeout=10000)
    pg.wait_for_timeout(1000)

    # 3. Transcribe: load fixture, title it "test_meeting", pick the "base"
    #    model, hint 3 speakers, enable diarize + auto-correct, start job -
    #    matching 04-transcript-detail.png's kept figure so the two agree.
    pg.evaluate("navigate('transcribe')")
    pg.wait_for_selector("#ctl-diarize", state="visible", timeout=10000)
    pg.wait_for_timeout(500)
    # Set state directly (not .click()) - clicking raced renderTranscribe()'s
    # async re-render on first navigation and silently lost the toggle.
    pg.evaluate("""() => {
        const i = curProv().models.indexOf('base');
        if (i >= 0) S.modelIdx = i;
        S.diarize = true;
        S.autoCorrect = true;
        syncTranscribe();
    }""")
    pg.locator("summary:has-text('Fine adjust')").click()
    pg.wait_for_timeout(300)
    pg.fill("#tx-title", "test_meeting")
    pg.fill("#tx-speakers", "3")
    pg.locator("summary:has-text('Fine adjust')").click()  # collapse - keep 02-transcribe.png uncluttered
    pg.wait_for_timeout(300)
    pg.set_input_files("#file-input", SHORT_AUDIO)
    pg.wait_for_timeout(800)
    # Re-assert right before starting in case loading the file reset anything.
    pg.evaluate("() => { S.diarize = true; S.autoCorrect = true; syncTranscribe(); }")
    pg.locator("#key-play-a").click()
    pg.wait_for_timeout(2500)  # mid-job: deck animation + elapsed readout visible
    pg.screenshot(path=os.path.join(OUT_DIR, "02-transcribe.png"))
    print("Captured: 02-transcribe.png")

    # 4. Wait for transcription to finish, then open the transcript detail view
    initial_status = api("GET", "/api/transcripts")
    tid = initial_status[0]["id"]
    poll_transcript(tid, {"done", "failed"}, timeout=150)
    pg.wait_for_selector("#key-open-done:not([disabled])", timeout=150000)
    pg.locator("#key-open-done").click()
    pg.wait_for_selector('[id^="page-"].active', timeout=10000)
    pg.wait_for_timeout(1000)

    # Queue a summary pass alongside the auto-triggered correction pass, so
    # the Queue screen below shows more than one job kind/state at once.
    summarize_btn = pg.locator('[data-dact="summarize"]')
    if summarize_btn.count() > 0 and summarize_btn.first.is_enabled():
        summarize_btn.first.click()
        pg.wait_for_timeout(300)

    # 5. Queue: catch the correction + summary jobs while still running/queued -
    #    non-error state either way, since the backend configured above always succeeds.
    pg.evaluate("navigate('queue')")
    pg.wait_for_timeout(1200)
    pg.screenshot(path=os.path.join(OUT_DIR, "06-queue.png"))
    print("Captured: 06-queue.png")

    # 6. Back to detail, wait for the correction job to finish, open Corrected tab
    pg.evaluate(f"navigate('detail', {tid})")
    pg.wait_for_timeout(500)
    poll_correction_done(tid, timeout=90)
    pg.evaluate("renderDetail && renderDetail()")
    pg.wait_for_timeout(500)
    pg.locator('[data-tab="corrected"]').click()
    pg.wait_for_timeout(800)
    pg.screenshot(path=os.path.join(OUT_DIR, "05-corrected.png"))
    print("Captured: 05-corrected.png")

    # 7. Re-run correction to get a second version, then open the compare modal
    rerun_btn = pg.locator('[data-dact="rerun"]')
    if rerun_btn.count() > 0 and rerun_btn.first.is_enabled():
        rerun_btn.first.click()
        poll_correction_done(tid, timeout=90)
        pg.wait_for_timeout(500)
    pg.locator('[data-dact="correction-history"]').click()
    pg.wait_for_selector("#modal-overlay.open", timeout=10000)
    pg.wait_for_timeout(500)
    pg.locator("#modal-box").screenshot(path=os.path.join(OUT_DIR, "15-version-compare.png"))
    print("Captured: 15-version-compare.png")
    pg.keyboard.press("Escape")
    pg.wait_for_timeout(300)

    # 8. Settings / Service panel: wait for the async page to actually render
    #    (root-caused blank capture: loadSettingsPage() is async and the old
    #    script screenshotted before its fetches resolved)
    pg.evaluate("navigate('settings')")
    pg.wait_for_selector("#page-settings .unit--svc", state="visible", timeout=10000)
    pg.wait_for_timeout(500)
    pg.screenshot(path=os.path.join(OUT_DIR, "08-service-panel.png"), full_page=True)
    print("Captured: 08-service-panel.png (full page, includes Faceplate/Phosphor theme controls below the fold)")

    # 9. Hotwords: there's no separate route - crop to just the Term Glossary
    #    widget instead of the full Settings page.
    pg.locator("#term-glossary-panel").screenshot(path=os.path.join(OUT_DIR, "09-hotwords.png"))
    print("Captured: 09-hotwords.png (cropped to Term Glossary widget)")

    # 10. File inventory: one linked (the meeting upload) + one orphaned file
    pg.evaluate("navigate('files')")
    pg.wait_for_timeout(2000)
    pg.screenshot(path=os.path.join(OUT_DIR, "12-file-inventory.png"))
    print("Captured: 12-file-inventory.png")

    # 11. Voice Roster + Enroll modal (unchanged from before)
    pg.evaluate("navigate('voices')")
    pg.wait_for_timeout(2000)
    enroll_btn = pg.locator("#voice-enroll-btn")
    if enroll_btn.count() > 0:
        enroll_btn.first.click()
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT_DIR, "13-enroll-speaker.png"))
        print("Captured: 13-enroll-speaker.png")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)

    # 12. Floating video panel: upload the synthetic video fixture, detach it
    pg.evaluate("navigate('transcribe')")
    pg.wait_for_selector("#ctl-diarize", state="visible", timeout=10000)
    pg.wait_for_timeout(500)
    pg.evaluate("() => { S.diarize = false; syncTranscribe(); }")  # not needed for this job
    pg.set_input_files("#file-input", FIXTURE_VIDEO)
    pg.wait_for_timeout(800)
    pg.locator("#key-play-a").click()
    all_transcripts = api("GET", "/api/transcripts")
    video_tid = max((row["id"] for row in all_transcripts), default=tid)
    poll_transcript(video_tid, {"done", "failed"}, timeout=90)
    pg.wait_for_selector("#key-open-done:not([disabled])", timeout=90000)
    pg.locator("#key-open-done").click()
    pg.wait_for_timeout(1200)
    detach_btn = pg.locator("#video-detach-btn")
    if detach_btn.count() > 0:
        detach_btn.click()
        pg.wait_for_timeout(600)
        pg.screenshot(path=os.path.join(OUT_DIR, "14-video-panel.png"))
        print("Captured: 14-video-panel.png")
    else:
        print("WARNING: no #video-detach-btn found - has_video may not have been detected")

    ctx2.close()
    browser.close()

server.should_exit = True
t.join(timeout=5)
if stub_proc is not None:
    stub_proc.terminate()
    try:
        stub_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        stub_proc.kill()
print("Done.")
