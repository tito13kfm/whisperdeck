"""Capture missing user manual screenshots via Playwright.

Starts an isolated WhisperDeck server, registers a test user, then
captures screenshots of pages the existing set doesn't cover:

  1. Login page
  2. File inventory
  3. Enroll speaker modal (Voice Roster)

Saves to the worktree screenshots/ directory.
"""
import http.cookiejar
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import urllib.error

import uvicorn

# Add main repo to path for app imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# Isolated data dir — unique per run
import tempfile
DATA_DIR = tempfile.mkdtemp(prefix="whisperdeck-screenshots-")
os.environ["WHISPERDECK_DATA_DIR"] = DATA_DIR

OUT_DIR = os.path.join(REPO_ROOT, "screenshots")
os.makedirs(OUT_DIR, exist_ok=True)

# Find free port
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
PORT = sock.getsockname()[1]
sock.close()
BASE_URL = f"http://127.0.0.1:{PORT}"

# Start server
import app as app_module
config = uvicorn.Config(app_module.app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on")
server = uvicorn.Server(config)
t = threading.Thread(target=server.run, daemon=True)
t.start()

deadline = time.time() + 10
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

# Register + login test user via API
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def api(method, path, body=None):
    token = json.loads(opener.open(BASE_URL + "/api/csrf-token", timeout=5).read())["token"]
    headers = {"Content-Type": "application/json", "X-CSRF-Token": token}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
    try:
        resp = opener.open(req, timeout=10)
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
print("Logged in via API")

# Playwright capture
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

    # 2. Log in via browser for subsequent pages
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx2.new_page()
    pg.goto(BASE_URL + "/", wait_until="networkidle")
    pg.wait_for_timeout(500)
    pg.fill("input[type='text'], input[name='username']", "docs")
    pg.fill("input[type='password']", "docs_pass_123")
    pg.click("button[type='submit']")
    pg.wait_for_selector("#app-shell", state="visible", timeout=10000)
    pg.wait_for_timeout(1500)

    # 2. File inventory
    pg.evaluate("navigate('files')")
    pg.wait_for_timeout(2500)
    pg.screenshot(path=os.path.join(OUT_DIR, "12-file-inventory.png"))
    print("Captured: 12-file-inventory.png")

    # 3. Voice Roster + Enroll modal
    pg.evaluate("navigate('voices')")
    pg.wait_for_timeout(2000)
    enroll_btn = pg.locator("button:has-text('Enroll')")
    if enroll_btn.count() > 0:
        enroll_btn.first.click()
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT_DIR, "13-enroll-speaker.png"))
        print("Captured: 13-enroll-speaker.png")
    else:
        print("No enroll button found")

    ctx2.close()
    browser.close()

server.should_exit = True
t.join(timeout=5)
print("Done.")
