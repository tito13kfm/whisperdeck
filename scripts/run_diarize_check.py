"""Standalone check: diarization + Qwen3.5 LLM jobs.

Not a pytest module (renamed from run_diarize_test.py): everything here runs
at import, including starting a server and firing HTTP requests, so the
*_test.py filename let pytest collection hang on it. Run directly:

    python scripts/run_diarize_check.py
"""
import os, sys, json, time, warnings, subprocess, tempfile, http.client, http.cookiejar, urllib.request, random, string
warnings.filterwarnings('ignore')

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(REPO_ROOT)

# ── Start server ──────────────────────────────────────────────────────
data_dir = tempfile.mkdtemp(prefix="whisperdeck-diartest-")
port = "9790"
env = os.environ.copy()
env.update({
    "PORT": port,
    "WHISPERDESK_DATA_DIR": data_dir,
    "HUGGINGFACE_TOKEN": os.environ.get("HUGGINGFACE_TOKEN", ""),
})

print(f"Starting server on port {port}, data dir: {data_dir}")
proc = subprocess.Popen(
    [sys.executable, "app.py"],
    env=env, cwd=REPO_ROOT,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE
)

# ── Wait for health ───────────────────────────────────────────────────
def health_check():
    for i in range(60):
        try:
            conn = http.client.HTTPConnection("localhost", int(port), timeout=3)
            conn.request("GET", "/api/health")
            resp = conn.getresponse()
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                print(f"  Server healthy (attempt {i+1}): diarization_backend={data.get('diarization_backend')}")
                conn.close()
                return data
            conn.close()
        except Exception:
            pass
        time.sleep(1)
    return None

print("Waiting for server...")
health = health_check()
if not health:
    stdout, stderr = proc.communicate(timeout=5)
    print("STDOUT:", stdout.decode()[:2000])
    print("STDERR:", stderr.decode()[:2000])
    sys.exit(1)

assert health.get("diarization_backend"), "Diarization backend should report True now!"
print("  ✓ diarization_backend = True")

# ── HTTP helper with cookie jar ───────────────────────────────────────
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def api_json(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=data, method=method,
        headers={"Content-Type": "application/json"} if body else {}
    )
    resp = opener.open(req)
    return json.loads(resp.read().decode())

def api_upload(path, filepath, extra_fields=None):
    boundary = "----TB" + str(time.time()).replace(".", "")
    body_bytes = b""
    if extra_fields:
        for name, value in extra_fields.items():
            body_bytes += f"--{boundary}\r\n".encode()
            body_bytes += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            body_bytes += (value.encode() if isinstance(value, str) else value) + b"\r\n"
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        file_content = f.read()
    body_bytes += f"--{boundary}\r\n".encode()
    body_bytes += f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    body_bytes += b"Content-Type: audio/mpeg\r\n\r\n" + file_content + b"\r\n"
    body_bytes += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://localhost:{port}{path}", data=body_bytes, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    resp = opener.open(req)
    return json.loads(resp.read().decode())

def api_form(method, path, fields):
    boundary = "----TB" + str(time.time()).replace(".", "")
    body_bytes = b""
    for name, value in fields.items():
        body_bytes += f"--{boundary}\r\n".encode()
        body_bytes += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body_bytes += (value.encode() if isinstance(value, str) else value) + b"\r\n"
    body_bytes += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://localhost:{port}{path}", data=body_bytes, method=method,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    resp = opener.open(req)
    return json.loads(resp.read().decode())

# ── Register & login ──────────────────────────────────────────────────
print("\nRegistering user...")
try:
    api_json("POST", "/api/register", {"username": "diartest", "password": "diartest123"})
    print("  Registered.")
except urllib.error.HTTPError as e:
    body = e.read().decode()
    print(f"  Register: {e.code} - {body}")
    print("  Trying login instead...")
    api_json("POST", "/api/login", {"username": "diartest", "password": "diartest123"})

# ── Configure local provider for Lemonade ─────────────────────────────
print("Configuring local provider...")
api_json("PUT", "/api/providers/local", {
    "api_url": "http://localhost:13305/v1",
    "api_key": "not-needed",
    "default_model": "Whisper-Tiny",
})

# ── Set correction/summary to use gpt-oss-20b-mxfp4-GGUF (fastest local LLM) ─
print("Setting LLM models to gpt-oss-20b-mxfp4-GGUF...")
api_json("PUT", "/api/settings", {
    "correction_provider": "local",
    "correction_model": "gpt-oss-20b-mxfp4-GGUF",
    "summary_provider": "local",
    "summary_model": "gpt-oss-20b-mxfp4-GGUF",
})

# ── Upload + transcribe with diarization ──────────────────────────────
print("\nUploading e2e_multispeaker.mp3 with diarization...")
fixture = os.path.join(REPO_ROOT, "tests", "fixtures", "e2e_multispeaker.mp3")
result = api_upload("/api/transcribe", fixture, {
    "diarize": "true",
    "provider": "local",
    "model": "Whisper-Tiny",
})

transcript_id = result.get("id")
print(f"  Transcript ID: {transcript_id}")
print(f"  Title: {result.get('title')}")
print(f"  Speaker count: {result.get('speaker_count')}")
print(f"  Status: {result.get('status')}")

if not transcript_id:
    print("  ERROR: No transcript ID returned!")
    print(f"  Full response: {json.dumps(result, indent=2)[:500]}")
    # Cleanup
    proc.terminate(); proc.wait(); import shutil; shutil.rmtree(data_dir, ignore_errors=True)
    sys.exit(1)

# ── Wait for transcription to complete ────────────────────────────────
print("\nWaiting for transcription to complete...")
for i in range(120):
    t = api_json("GET", f"/api/transcripts/{transcript_id}")
    if t.get("status") == "completed":
        print(f"  Completed! Speaker count: {t.get('speaker_count')}")
        segs = t.get("segments", [])
        print(f"  Segments with speaker labels: {sum(1 for s in segs if s.get('speaker'))} / {len(segs)}")
        speakers = set(s.get("speaker") for s in segs if s.get("speaker"))
        print(f"  Unique speakers: {sorted(speakers)}")
        break
    elif t.get("status") == "failed":
        print(f"  FAILED: {t.get('error')}")
        break
    elif t.get("status") == "pending":
        jp = t.get("job_progress", {})
        print(f"  Pending: {jp.get('completed', 0)}/{jp.get('total', '?')} chunks")
    time.sleep(2)

succeeded = t.get("status") == "completed"

# ── Run correction with Bonsai-8B ─────────────────────────────────────
if succeeded:
    print("\nRunning correction (gpt-oss-20b-mxfp4-GGUF)...")
    api_form("POST", f"/api/transcripts/{transcript_id}/correct", {
        "provider": "local", "model": "gpt-oss-20b-mxfp4-GGUF",
    })
    print("Waiting for correction...")
    for i in range(120):
        t = api_json("GET", f"/api/transcripts/{transcript_id}")
        if t.get("corrected_text"):
            print(f"  Correction completed!")
            print(f"  Corrected text (first 400 chars): {t['corrected_text'][:400]}")
            break
        elif t.get("correction_error"):
            print(f"  Correction failed: {t['correction_error']}")
            break
        cj = t.get("correction_job")
        if cj:
            print(f"  Correction job: {cj.get('status')} ({cj.get('progress', '')})")
        time.sleep(3)

    # ── Run summary with Bonsai-8B ────────────────────────────────────────
    print("\nRunning summary (gpt-oss-20b-mxfp4-GGUF)...")
    api_form("POST", f"/api/transcripts/{transcript_id}/summarize", {
        "provider": "local", "model": "gpt-oss-20b-mxfp4-GGUF",
    })
    print("Waiting for summary...")
    for i in range(120):
        t = api_json("GET", f"/api/transcripts/{transcript_id}")
        if t.get("has_summary"):
            print(f"  Summary completed!")
            sr = api_json("GET", f"/api/transcripts/{transcript_id}/runs/summary")
            print(f"  Short summary: {sr.get('short_summary', '')[:200]}")
            print(f"  Key points: {len(sr.get('key_points', []))}")
            print(f"  Action items: {len(sr.get('action_items', []))}")
            print(f"  Decisions: {len(sr.get('decisions', []))}")
            break
        cj = t.get("summary_job")
        if cj:
            print(f"  Summary job: {cj.get('status')} ({cj.get('progress', '')})")
        time.sleep(3)

    # ── Add context and re-run correction ─────────────────────────────────
    print("\nAdding context document...")
    api_json("POST", f"/api/transcripts/{transcript_id}/context", {
        "document": "The product is called ERPNext and the client is ACME Corp."
    })
    print("Re-running correction with context (gpt-oss-20b-mxfp4-GGUF)...")
    api_form("POST", f"/api/transcripts/{transcript_id}/correct", {
        "provider": "local", "model": "gpt-oss-20b-mxfp4-GGUF",
    })
    for i in range(120):
        t = api_json("GET", f"/api/transcripts/{transcript_id}")
        if t.get("corrected_text"):
            print(f"  Re-correction completed!")
            print(f"  First 400 chars: {t['corrected_text'][:400]}")
            break
        elif t.get("correction_error"):
            print(f"  Correction failed: {t['correction_error']}")
            break
        time.sleep(3)

# ── Cleanup ───────────────────────────────────────────────────────────
print("\n--- TESTS COMPLETE ---")
proc.terminate()
try: proc.wait(timeout=10)
except subprocess.TimeoutExpired: proc.kill()
import shutil
shutil.rmtree(data_dir, ignore_errors=True)
print("Cleaned up.")