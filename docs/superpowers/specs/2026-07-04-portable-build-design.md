# Portable Build — Design

## Purpose

WhisperDeck currently requires Python, pip, and manual setup steps to run
(see INSTALL.md). That's fine for a developer but a hard wall for a
non-technical person. This adds a `WhisperDeck-portable.zip` release
artifact: download, extract anywhere, double-click one `.bat` file, the
app opens in a browser. No Python install, no terminal, no admin rights.

## Audience & constraints

- Target user: non-technical, no dev tools installed, Windows only.
- Internet access at first launch is assumed (to `pip install` deps and
  let Moonshine download its model — both already true of the existing
  app; nothing new here).
- Install experience: unzip + double-click, not a Start-Menu-integrated
  installer. No code-signing cert exists, so a real `.exe` installer
  would trip Windows SmartScreen with no better trust story than the
  batch file anyway — a zip avoids that overhead for no loss of trust.
- Must remain rebuildable by the agent on request ("rebuild the
  installer") without the user doing anything manually.

## Package layout

```
WhisperDeck-portable/
  WhisperDeck.bat          <- double-click entry point
  python/                  <- embeddable CPython 3.13 (self-contained)
  ffmpeg/ffmpeg.exe         <- static ffmpeg build, needed by hosted providers
  app/                      <- app source (app.py, backends/, services/,
                                database/, static/, requirements.txt,
                                database migrations if any)
  data/                     <- created on first run: db, uploads,
                                transcripts, voices — same layout the
                                dev app already uses, just relocated
                                inside the portable folder
  README.txt                <- three lines: double-click WhisperDeck.bat,
                                wait for the browser tab, that's it
```

Deleting the extracted folder removes the app completely — no registry
entries, no PATH changes, no files written outside the folder.

## First-launch vs subsequent-launch flow

`WhisperDeck.bat`:
1. `cd` to its own directory (so it works regardless of where it's
   extracted).
2. Check for a marker file `data\.deps_installed`. If absent:
   - Run `python\python.exe -m ensurepip`, then
     `python\python.exe -m pip install -r app\requirements.txt`.
   - On success, write the marker file.
   - On failure (no internet, etc.), print a plain-English error and
     `pause` so the window doesn't just vanish.
3. Launch `python\python.exe app\app.py` with `PORT` pinned (9781, same
   as today) and `DATA_DIR` pointed at `..\data` (see "App changes"
   below).
4. Open the default browser to `http://localhost:9781` (via `start`)
   once the port is confirmed listening (simple retry loop, few hundred
   ms apart, few-second timeout).
5. Leave the console window open — closing it stops the server. This is
   the simplest correct mental model for a non-technical user ("the
   black window is the app running").

Re-launching later: marker file present, skips straight to step 3.

## App changes required

Today `DATA_DIR`/`UPLOAD_DIR`/etc. in `app.py` are hardcoded relative to
`BASE_DIR` (the app.py file's own directory) — see `app.py:45-49`. That
already makes data live next to the code, which is exactly what the
portable layout wants (`app/../data` per the layout above) with zero
code change, since `BASE_DIR` in the portable layout is `app/`. No
change needed there — confirmed by reading the existing code, not
assumed.

ffmpeg discovery: `services/audio_prep.py` must find the bundled
`ffmpeg/ffmpeg.exe` instead of relying on PATH. Add an `FFMPEG_PATH` env
var (set by `WhisperDeck.bat` before launching python) that
`audio_prep.py` checks first, falling back to plain `"ffmpeg"` (PATH
lookup) for the normal dev/installed-Python case — this is the one
actual code change the feature needs. (To confirm during planning: check
`audio_prep.py`'s current ffmpeg invocation before writing this task.)

## Build process — `scripts/build_release.ps1`

A checked-in PowerShell script, run on request ("rebuild the
installer"), not part of any CI/release-gating pipeline (this is a
manually-triggered convenience, not an automated release process):

1. Download (or reuse a cached copy under `scripts/.build-cache/`) the
   official CPython embeddable zip and a static ffmpeg build, so repeat
   builds don't re-download unchanged binaries.
2. Assemble the `WhisperDeck-portable/` tree fresh each time: copy
   current `app.py`, `backends/`, `services/`, `database/`, `static/`,
   `requirements.txt` into `app/`; extract python/ffmpeg from cache;
   write `WhisperDeck.bat` and `README.txt` from templates in
   `scripts/portable-template/`.
3. Zip the result to `dist/WhisperDeck-portable-vX.Y.zip` (version pulled
   from `app.py`'s existing version string, e.g. `"WhisperDeck v0.6"`).
4. Does NOT include `tests/`, `docs/`, `.git`, existing `data/` — a
   fresh install always starts with an empty `data/`.

Running the script is the entire "rebuild" workflow — no manual copying,
no remembering what changed. `dist/` and `scripts/.build-cache/` are
gitignored (build output and downloaded binaries, not source).

## Known limitations (documented in README.txt, not solved here)

- First launch needs internet (dep install + Moonshine model download).
  Same requirement the existing dev install already has.
- No auto-update. A new portable zip is a fresh download; the user's
  `data/` folder can be copied into the new extract manually to keep
  transcripts. Not building an updater — out of scope, revisit only if
  this becomes a real pain point.
- Unsigned `.bat`/`ffmpeg.exe`/embeddable Python may prompt a Windows
  SmartScreen/Defender warning on first run. Documented, not
  suppressed — code signing is a separate cost/tooling decision, not
  part of this design.

## Testing

- Same clean-room method already proven for the Moonshine default-provider
  change earlier this session: extract the built zip into a scratch
  directory with no system Python on PATH (or a renamed/hidden one, to
  simulate the target machine honestly), double-click-equivalent
  (`cmd /c WhisperDeck.bat`), confirm it reaches a working transcribe
  round-trip with zero manual steps.
