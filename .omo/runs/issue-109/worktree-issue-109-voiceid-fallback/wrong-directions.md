# Wrong directions, issue #109

Written as each discrepancy surfaced during execution.

## 1. The issue's proposed fix (b) is not implementable as worded

Issue text: "Don't fallback during `identify()` -- return `None` and let the caller handle it."

`identify()` never returns `None` today (`services/voice_id.py:221-250` pre-fix). It returns
`[]` in every "nothing to report" case, including total extraction failure. Returning `None`
would change `/api/voices/identify`'s JSON from `"matches": []` to `"matches": null`, a visible
API contract change the issue does not call out.

Also, deleting the fallback outright breaks librosa-only installs: `_detect_backend()` selects
`librosa_mfcc` when neither speechbrain nor pyannote is importable, and MFCC is the *primary*
model there, not a degradation. Any fix has to distinguish the two, which is what
`_is_degraded_model()` does.

**Delivered instead:** keep `identify()`'s `list[dict]` contract, add `identify_detailed()` that
reports `degraded` / `compared` / `skipped_model_mismatch` / `warning` alongside the matches.

## 2. The issue's proposed fix (a) would have been write-only

Issue text: "Propagate the fallback as a warning to the job result_json so the UI can show it."

`result_json` is not in `serialize_llm_job()` (`services/llm_jobs.py:48-70`), so it never reaches
the frontend's `t.voice_match_job`, and `app.py:2775`'s `/api/transcripts/{id}/runs/{kind}`
allowlist does not include `voice_match`. Writing `result_json` for this job kind would have
produced data nothing can read, plus a UI feature (a run-history diff view for voice_match) that
is outside this issue.

**Delivered instead:** the warning goes into `job.error`, which is the codebase's existing
"completed but degraded" channel for `LlmJob` and is already rendered (red meta line on the Queue
screen, `static/rack.js:3473`/`3479`/`3502`/`3509`).

## 3. Issue-runner prompt: "use the main checkout's installed binaries" for builds

`.claude/issue-runner-prompt.md` says fresh worktrees have no `node_modules` and to use the main
checkout's installed binaries (`npx esbuild` from the main repo path, or its `node_modules/.bin/`).
On this machine `C:\Claude\WhisperDeck\node_modules` does not exist either, and there is no global
`esbuild` on PATH, so both suggested routes fail.

**What worked:** `npx --yes esbuild@0.25 static/rack.js --bundle --minify --sourcemap
--outfile=static/rack.min.js`, run from the worktree root (matches `package.json`'s `build:js`).

**Recommended fix to the prompt:** say `npx --yes esbuild@<version from package.json>` outright,
and drop the claim that the main checkout has installed binaries.

`scripts/verify_self_audit.py` has the same problem one level down: it shells out to bare `esbuild`
and reports `rebuild failed (1): 'esbuild' is not recognized` as a **blocking** finding for both
`build:js` and `build:css` on this machine. That makes its build gate unrunnable here regardless of
what the change touches (`build:css` fails identically on a change that never opens
`static/rack.css`). A tool that cannot run should not report the same severity as a tool that ran
and found a defect. **Recommended fix:** have the script fall back to
`npx --yes esbuild@<version>` when `esbuild` is not on PATH, and if it still cannot run, classify
the result as "check could not run" rather than a blocking finding.

Verified this toolchain is the right one rather than assuming: rebuilding HEAD's unmodified
`static/rack.js` with that exact command reproduced the committed `static/rack.min.js`
byte-for-byte (`cmp` clean).

## 4. Scratchpad path: the 8.3 short form is not resolvable

The session's scratchpad is given as `C:\Users\T1B92~1.KUR\AppData\Local\...`. PowerShell's
`Set-Location` rejects it ("An object at the specified path C:\Users\T1B92~1.KUR does not exist"),
though `New-Item` accepts it. The long form `C:\Users\t.kurash\AppData\Local\...` works everywhere.

## 5. Sibling findings deliberately left alone

- **Stale `_last_backend_error`** (`services/voice_id.py:32`, set on failure at 347/377/391, never
  reset on success, on a process-wide singleton). Surfaced by the Phase 1 sibling sweep. Already
  filed as issue #110, so fixing it here would collide with that work.
- **`_finish()` guards only `"cancelled"`** (`services/llm_jobs.py:322`). Combined with the
  catch-all at `services/llm_jobs.py:757-761`, anything that raises *after* a branch calls
  `_finish(..., "completed", ...)` silently flips a completed job to failed. Found by the Phase 1.5
  completion-race check. Not reachable for voice_match today and not reachable after this change
  either (the error string is assembled before `_finish`), so it is reported rather than fixed.
  The `correction` branch already carries the live version of this shape: `_finish("completed")`
  at 381 followed by `enqueue_pipeline_classify` at 397. Worth its own issue.

## 6. Consequence of the job switching to `identify_detailed()`

Three pre-existing tests in `tests/test_voice_match_job.py` stubbed
`services.llm_jobs.voice_id_service.identify`. Once the job called `identify_detailed` instead,
those stubs stopped intercepting and the real embedding path ran (librosa tried to load a 4-byte
fake mp3). They were updated to stub `identify_detailed` via a shared `_outcome()` helper. This is
a genuine blast-radius item, not a flake: any future external stub of `identify` for the job path
has the same problem.
