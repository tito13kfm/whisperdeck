# Portable Build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce `dist/WhisperDeck-portable-vX.Y.zip` — extract anywhere,
double-click `WhisperDeck.bat`, the app runs with zero Python/ffmpeg
installed on the machine — plus a `scripts/build_release.ps1` that rebuilds
that zip on request.

**Architecture:** `services/audio_prep.py` gains an `FFMPEG_DIR`-aware
lookup so it can find a bundled `ffmpeg.exe`/`ffprobe.exe` instead of
requiring PATH. A new `scripts/build_release.ps1` assembles a portable
tree (embeddable CPython + bundled ffmpeg + app source), and a
`WhisperDeck.bat` template is the double-click entry point that installs
deps into the bundled Python on first run, then launches the app and
opens a browser tab.

**Tech Stack:** PowerShell (build script), Windows batch (launcher),
existing FastAPI app unchanged except the ffmpeg lookup.

## Global Constraints

- No code-signing — zip distribution, not an installer .exe (per spec).
- `data/` lives inside the extracted folder itself, next to `app/` (per
  spec's package layout) — this already falls out of `app.py`'s existing
  `BASE_DIR`-relative `DATA_DIR` (`app.py:45-49`), no change needed there.
- Build script must be re-runnable on request with no manual steps
  ("rebuild the installer" = run one script).
- `dist/` and `scripts/.build-cache/` are build output / downloaded
  binaries — gitignored, not committed.
- Python embeddable version pinned to 3.13.1 in the script (a named
  variable, not hardcoded inline) — matches INSTALL.md's "use 3.11–3.13,
  avoid 3.14" guidance.
- ffmpeg source: BtbN's rolling "latest" static Windows build
  (`https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip`)
  — cached locally after first download so a broken upstream release
  doesn't break repeat builds.

---

### Task 1: `FFMPEG_DIR`-aware ffmpeg/ffprobe lookup in `services/audio_prep.py`

**Files:**
- Modify: `services/audio_prep.py`
- Test: `tests/test_audio_prep_ffmpeg_lookup.py` (new)

**Interfaces:**
- Produces: `_ffmpeg_bin() -> str`, `_ffprobe_bin() -> str` — both read
  the `FFMPEG_DIR` env var; if set, return
  `os.path.join(FFMPEG_DIR, "ffmpeg.exe")` /
  `os.path.join(FFMPEG_DIR, "ffprobe.exe")`; if unset, return the bare
  `"ffmpeg"` / `"ffprobe"` (current PATH-lookup behavior, unchanged for
  every existing dev/test environment).
- `ffmpeg_available()` keeps its existing signature (`() -> bool`) but
  checks `_ffmpeg_bin()` instead of the literal `"ffmpeg"`.

Six call sites in this file currently hardcode `"ffmpeg"` (lines 26, 55,
98, 158, 175, 237) and one hardcodes `"ffprobe"` (line 77) — confirmed by
reading the file directly, not assumed. All seven get replaced.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_audio_prep_ffmpeg_lookup.py`:

```python
import os
import pytest
from services import audio_prep


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    yield


def test_ffmpeg_bin_defaults_to_path_lookup():
    assert audio_prep._ffmpeg_bin() == "ffmpeg"


def test_ffprobe_bin_defaults_to_path_lookup():
    assert audio_prep._ffprobe_bin() == "ffprobe"


def test_ffmpeg_bin_uses_ffmpeg_dir_when_set(monkeypatch):
    monkeypatch.setenv("FFMPEG_DIR", r"C:\WhisperDeck\ffmpeg")
    assert audio_prep._ffmpeg_bin() == os.path.join(r"C:\WhisperDeck\ffmpeg", "ffmpeg.exe")


def test_ffprobe_bin_uses_ffmpeg_dir_when_set(monkeypatch):
    monkeypatch.setenv("FFMPEG_DIR", r"C:\WhisperDeck\ffmpeg")
    assert audio_prep._ffprobe_bin() == os.path.join(r"C:\WhisperDeck\ffmpeg", "ffprobe.exe")


def test_ffmpeg_available_checks_bundled_path_when_ffmpeg_dir_set(monkeypatch, tmp_path):
    bundled_dir = tmp_path / "ffmpeg"
    bundled_dir.mkdir()
    (bundled_dir / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setenv("FFMPEG_DIR", str(bundled_dir))
    assert audio_prep.ffmpeg_available() is True


def test_ffmpeg_available_false_when_ffmpeg_dir_set_but_binary_missing(monkeypatch, tmp_path):
    bundled_dir = tmp_path / "ffmpeg"
    bundled_dir.mkdir()
    monkeypatch.setenv("FFMPEG_DIR", str(bundled_dir))
    assert audio_prep.ffmpeg_available() is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_audio_prep_ffmpeg_lookup.py -v`
Expected: FAIL — `AttributeError: module 'services.audio_prep' has no attribute '_ffmpeg_bin'`

- [ ] **Step 3: Implement the lookup functions and wire them into every call site**

In `services/audio_prep.py`, replace the `ffmpeg_available` function and
add the two new helpers immediately above it:

```python
def _ffmpeg_bin() -> str:
    """Resolve the ffmpeg binary: bundled copy (portable build) if
    FFMPEG_DIR is set, otherwise PATH lookup (normal dev/installed use)."""
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    return os.path.join(ffmpeg_dir, "ffmpeg.exe") if ffmpeg_dir else "ffmpeg"


def _ffprobe_bin() -> str:
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    return os.path.join(ffmpeg_dir, "ffprobe.exe") if ffmpeg_dir else "ffprobe"


def ffmpeg_available() -> bool:
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    if ffmpeg_dir:
        return os.path.isfile(_ffmpeg_bin())
    return shutil.which("ffmpeg") is not None
```

Then update every remaining call site in the same file:

- Line ~55 (`transcode_for_upload`'s `cmd` list): change
  `"ffmpeg", "-y",` to `_ffmpeg_bin(), "-y",`
- Line ~77 (`get_audio_duration`'s subprocess list): change
  `["ffprobe", "-v", ...]` to `[_ffprobe_bin(), "-v", ...]`
- Line ~98 (`detect_silence_midpoints`): change `"ffmpeg", "-i", audio_path,`
  to `_ffmpeg_bin(), "-i", audio_path,`
- Line ~158 (`extract_clips_concat`'s per-part loop): change
  `"ffmpeg", "-y", "-i", audio_path,` to `_ffmpeg_bin(), "-y", "-i", audio_path,`
- Line ~175 (`extract_clips_concat`'s concat step): change
  `["ffmpeg", "-y", "-f", "concat", ...]` to
  `[_ffmpeg_bin(), "-y", "-f", "concat", ...]`
- Line ~237 (`chunk_audio`'s `_cut_one`): change `"ffmpeg", "-y",` to
  `_ffmpeg_bin(), "-y",`

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_audio_prep_ffmpeg_lookup.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests pass (113 previously + 6 new = 119)

- [ ] **Step 6: Commit**

```bash
git add services/audio_prep.py tests/test_audio_prep_ffmpeg_lookup.py
git commit -m "feat: audio_prep resolves ffmpeg/ffprobe via FFMPEG_DIR for portable builds"
```

---

### Task 2: Portable launcher and README templates

**Files:**
- Create: `scripts/portable-template/WhisperDeck.bat.template`
- Create: `scripts/portable-template/README.txt`

**Interfaces:**
- Consumes: nothing (static template files).
- Produces: `WhisperDeck.bat.template` contains the literal placeholder
  `__PORT__` (Task 3's build script replaces it with `9781` when writing
  the final `WhisperDeck.bat` into the assembled tree) — this is the only
  substitution the build script performs on this file.

This task has no automated test — it produces static template content
that Task 3 copies verbatim (aside from the one substitution) and Task 4
verifies end-to-end. Correctness is checked by manual execution in Task 4.

- [ ] **Step 1: Write `scripts/portable-template/WhisperDeck.bat.template`**

```bat
@echo off
setlocal
cd /d "%~dp0"

set PYTHON=python\python.exe
set FFMPEG_DIR=%~dp0ffmpeg
set DEPS_MARKER=data\.deps_installed
set PORT=__PORT__

echo ==============================================
echo          WhisperDeck (portable)
echo   Transcribe - Diarize - Summarize - Identify
echo ==============================================
echo.

if not exist "data" mkdir "data"

if not exist "%DEPS_MARKER%" (
    echo [*] First launch - installing dependencies, this takes a minute...
    "%PYTHON%" -m ensurepip --quiet
    "%PYTHON%" -m pip install --quiet -r app\requirements.txt
    if errorlevel 1 (
        echo.
        echo [!] Dependency install failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo ok > "%DEPS_MARKER%"
    echo [*] Dependencies installed.
)

echo [*] Starting server on http://localhost:%PORT%
start "" http://localhost:%PORT%
"%PYTHON%" app\app.py

pause
```

- [ ] **Step 2: Write `scripts/portable-template/README.txt`**

```
WhisperDeck (portable)
=======================

1. Double-click WhisperDeck.bat
2. Wait for the browser tab to open (first launch takes a minute or two
   to finish setup - it needs internet access for that one time)
3. That's it - transcribe, diarize, summarize, identify speakers.

To close the app: close the black console window that opened alongside
your browser tab - that's what's running the server.

To remove WhisperDeck completely: delete this whole folder. Nothing is
installed anywhere else on your computer.

Your transcripts, uploads, and settings live in the "data" folder next
to this file. Back that folder up if you want to keep them when moving
to a new copy of WhisperDeck.
```

- [ ] **Step 3: Commit**

```bash
git add scripts/portable-template/WhisperDeck.bat.template scripts/portable-template/README.txt
git commit -m "feat: add portable launcher and README templates"
```

---

### Task 3: `scripts/build_release.ps1`

**Files:**
- Create: `scripts/build_release.ps1`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `scripts/portable-template/WhisperDeck.bat.template`,
  `scripts/portable-template/README.txt` (Task 2); `app.py`'s
  `version="0.6.0"` string (`app.py:94`) for naming the output zip.
- Produces: `dist/WhisperDeck-portable-v<version>.zip`. Running
  `scripts/build_release.ps1` with no arguments is the entire rebuild
  workflow.

No pytest coverage for a PowerShell script — Task 4 is this task's test
(build it, then run the result end to end in an isolated environment).

- [ ] **Step 1: Add build output directories to `.gitignore`**

In `.gitignore`, add:

```
dist/
scripts/.build-cache/
```

- [ ] **Step 2: Write `scripts/build_release.ps1`**

```powershell
<#
Rebuilds the WhisperDeck portable zip from the current working tree.
Run with no arguments: powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$CacheDir   = Join-Path $PSScriptRoot ".build-cache"
$BuildDir   = Join-Path $RepoRoot "dist\WhisperDeck-portable"
$DistDir    = Join-Path $RepoRoot "dist"
$PythonVer  = "3.13.1"
$PythonUrl  = "https://www.python.org/ftp/python/$PythonVer/python-$PythonVer-embed-amd64.zip"
$FfmpegUrl  = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# --- Fetch (and cache) the embeddable Python runtime ---
$PythonZip = Join-Path $CacheDir "python-embed-$PythonVer.zip"
if (-not (Test-Path $PythonZip)) {
    Write-Host "[*] Downloading embeddable Python $PythonVer..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZip
} else {
    Write-Host "[*] Using cached embeddable Python $PythonVer"
}

# --- Fetch (and cache) the static ffmpeg build ---
$FfmpegZip = Join-Path $CacheDir "ffmpeg-latest-win64-gpl.zip"
if (-not (Test-Path $FfmpegZip)) {
    Write-Host "[*] Downloading ffmpeg..."
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip
} else {
    Write-Host "[*] Using cached ffmpeg build"
}

# --- Fresh build tree ---
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "[*] Extracting Python runtime..."
Expand-Archive -Path $PythonZip -DestinationPath (Join-Path $BuildDir "python") -Force

Write-Host "[*] Extracting ffmpeg..."
$FfmpegExtractTmp = Join-Path $CacheDir "ffmpeg-extract-tmp"
if (Test-Path $FfmpegExtractTmp) { Remove-Item -Recurse -Force $FfmpegExtractTmp }
Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtractTmp -Force
$FfmpegBinSrc = Get-ChildItem -Path $FfmpegExtractTmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1 | Select-Object -ExpandProperty DirectoryName
New-Item -ItemType Directory -Force -Path (Join-Path $BuildDir "ffmpeg") | Out-Null
Copy-Item (Join-Path $FfmpegBinSrc "ffmpeg.exe") (Join-Path $BuildDir "ffmpeg\ffmpeg.exe")
Copy-Item (Join-Path $FfmpegBinSrc "ffprobe.exe") (Join-Path $BuildDir "ffmpeg\ffprobe.exe")

Write-Host "[*] Copying app source..."
$AppDest = Join-Path $BuildDir "app"
New-Item -ItemType Directory -Force -Path $AppDest | Out-Null
foreach ($item in @("app.py", "backends", "services", "database", "static", "requirements.txt", "__init__.py")) {
    Copy-Item (Join-Path $RepoRoot $item) $AppDest -Recurse -Force
}

Write-Host "[*] Writing launcher and README..."
$LauncherTemplate = Get-Content (Join-Path $RepoRoot "scripts\portable-template\WhisperDeck.bat.template") -Raw
$LauncherTemplate.Replace("__PORT__", "9781") | Set-Content -Path (Join-Path $BuildDir "WhisperDeck.bat") -NoNewline
Copy-Item (Join-Path $RepoRoot "scripts\portable-template\README.txt") (Join-Path $BuildDir "README.txt")

# --- Version + zip ---
$VersionLine = Select-String -Path (Join-Path $RepoRoot "app.py") -Pattern 'version="([\d.]+)"' | Select-Object -First 1
$Version = $VersionLine.Matches[0].Groups[1].Value
$ZipPath = Join-Path $DistDir "WhisperDeck-portable-v$Version.zip"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

Write-Host "[*] Zipping to $ZipPath ..."
Compress-Archive -Path (Join-Path $BuildDir "*") -DestinationPath $ZipPath

Write-Host "[*] Done: $ZipPath"
```

- [ ] **Step 3: Run the build script**

Run: `powershell -ExecutionPolicy Bypass -File scripts\build_release.ps1`
Expected: ends with `[*] Done: <repo>\dist\WhisperDeck-portable-v0.6.0.zip`
and that file exists.

If either download URL 404s (upstream moved/renamed a release), update
the corresponding `$PythonVer`/`$PythonUrl`/`$FfmpegUrl` variable at the
top of the script to a current one and re-run — this is the expected
maintenance path, not a design flaw.

- [ ] **Step 4: Commit**

```bash
git add scripts/build_release.ps1 .gitignore
git commit -m "feat: add build_release.ps1 to assemble the portable zip"
```

---

### Task 4: End-to-end clean-room verification

**Files:** none (verification only, no code changes).

**Interfaces:** none — this task consumes the zip produced by Task 3 and
produces a pass/fail verdict plus a short fix-and-recheck loop if it fails.

- [ ] **Step 1: Extract the built zip into an isolated scratch directory**

```bash
mkdir -p /tmp/portable_smoke
cd /tmp/portable_smoke
unzip -q "<repo>/dist/WhisperDeck-portable-v0.6.0.zip" -d WhisperDeck
```

- [ ] **Step 2: Launch it exactly as a double-click would, with no system Python/ffmpeg assumed**

```bash
cd /tmp/portable_smoke/WhisperDeck
cmd /c WhisperDeck.bat
```

(Run this in the background/a separate terminal since it blocks on the
running server — do not `Ctrl+C` it until Step 5.)

Expected: console prints `[*] First launch - installing dependencies...`,
then after it finishes, `[*] Starting server on http://localhost:9781`,
and a browser tab opens to that URL showing the WhisperDeck UI.

- [ ] **Step 3: Confirm zero-key transcription actually works from this instance**

With the server from Step 2 still running, from another terminal:

```bash
curl -s -c /tmp/portable_cookies.txt -X POST http://127.0.0.1:9781/api/register \
  -H "Content-Type: application/json" -d '{"username":"smoke","password":"smoketest123"}'
curl -s -b /tmp/portable_cookies.txt http://127.0.0.1:9781/api/providers \
  | python -c "import json,sys; d=json.load(sys.stdin); print([p['id'] for p in d if p['configured']])"
```

Expected: the printed list includes `'moonshine'`.

- [ ] **Step 4: Confirm ffmpeg-dependent paths work too (the actual point of Task 1)**

```bash
curl -s -b /tmp/portable_cookies.txt -X POST http://127.0.0.1:9781/api/transcribe \
  -F "file=@<repo>/test.mp4" -F "provider=moonshine"
```

Expected: JSON response with `"status":"completed"` and
`"provider":"moonshine"` — proves the bundled ffmpeg was found and used
for the mp4-to-mp3 transcode step (Moonshine is a LOCAL_PROVIDER, so per
`app.py`'s `needs_transcode` logic this only transcodes because `.mp4` is
outside `local_readable_exts` — exactly the path Task 1's `FFMPEG_DIR`
lookup has to serve correctly).

- [ ] **Step 5: Stop the server and clean up**

```bash
# stop the WhisperDeck.bat process (Ctrl+C in its terminal, or):
taskkill //F //IM python.exe //FI "WINDOWTITLE eq WhisperDeck*" 2>/dev/null || true
rm -rf /tmp/portable_smoke /tmp/portable_cookies.txt
```

Per existing project convention, never `taskkill` by bare image name
(`python.exe`) without a filter — this uses a `WINDOWTITLE` filter
specifically to avoid touching any other running Python process.

- [ ] **Step 6: If any step failed, fix and re-run from Step 1**

Common failure points to check first: `FFMPEG_DIR` not being set before
`app\app.py` launches (check `WhisperDeck.bat`'s `set FFMPEG_DIR=...`
line executed before the `"%PYTHON%" app\app.py` line), or the ffmpeg
zip's internal folder structure not matching what
`Get-ChildItem -Recurse -Filter "ffmpeg.exe"` expects in
`build_release.ps1` (BtbN's zip nests binaries under a versioned
subfolder — the recursive search handles that, but confirm by listing
`dist/WhisperDeck-portable/ffmpeg/` after a build).

- [ ] **Step 7: No commit for this task** — it's verification only. If
Step 6 required a code fix, that fix gets its own commit under whichever
Task's file it touched (Task 1 or Task 3).

---

## Self-Review Notes

- **Spec coverage:** package layout (Task 3), first-launch/relaunch flow
  (Task 2's `.bat` template), app changes / ffmpeg discovery (Task 1),
  build process (Task 3), testing (Task 4). README.txt and
  known-limitations text folded into Task 2's template content per the
  spec's "documented in README.txt" note.
- Not building: auto-update, code signing, Start-Menu installer — all
  explicitly out of scope per the spec's "Known limitations" section.
