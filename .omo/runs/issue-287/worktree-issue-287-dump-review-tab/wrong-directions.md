# Wrong directions — issue #287

Discrepancies found while executing instructions (issue text, `.claude/issue-runner-prompt.md`, AGENTS.md, skills). Written as they happen, not backfilled.

## 1. `gh pr list --search "closes #<N>"` returns a false positive

Phase 0 says to treat "has a merged PR closing it" as done. `gh pr list --search "closes #287" --state merged` returns merged PR #294 ("feat(frontend): voice dump kind picker and Dump notes board (#286)"), because `--search` full-text matches the PR body and #294's body mentions #287 as a sibling. #287 was still `OPEN`.

**Recommended fix** for `.claude/issue-runner-prompt.md` Phase 0: keep the search as a hint, but treat the issue's `state` as authoritative. If `state == OPEN`, that issue is the target regardless of what the search returns; use the merged-PR search only to catch an issue a merge failed to auto-close.

## 2. The issue's own literal spec value does not exist on the wire

Issue #287 states the tab "renders from `t.voice_dump_job.result_json.items`". It cannot: `serialize_llm_job` (`services/llm_jobs.py:47-69`) emits no `result_json` key, and `_dictation_job_fields` (`app.py:412-464`) sets `"voice_dump_job": serialize_llm_job(vd_job)`. Both read directly and confirmed. `t.voice_dump_job.result_json` is `undefined` in the browser.

The tab instead reads `GET /api/transcripts/{id}/runs/voice_dump` → `runs[...].result.items`, which is how `formatHtml()` already sources job results.

**Recommended fix**: this is an issue-body error, not a prompt error. Worth a comment on #287 noting the corrected data source so a future reader does not "fix" the working code back to the broken spec.

## 3. Pre-existing bug: the voice_note Notes tab reads the same nonexistent field

Not in scope for #287 (its acceptance criteria require the voice_note tab be left *unaffected*), but found while checking whether the mirrored path already solved this problem. Two independent defects in `voiceNoteHtml`:

1. `static/rack.js` `voiceNoteHtml()` carries a comment asserting "The serializer already exposes `voice_note_job.result_json`, so we read from there". That is false (see item 2). The guard `if (!job || !job.result_json)` therefore always fires, so a **successfully completed** voice-note chain renders "No voice-note result yet."
2. The `notes` branch of `renderDetailBody` never binds `[data-dact]`, and the delegated `#detail-body` handler `detailBodyClick` dispatches only `[data-export-*]` and `[data-seg-*]`. So `voiceNoteHtml`'s own "Rerun chain" and "Discard note" buttons are **dead** — clicking them does nothing.

**Recommended fix**: separate issue. Either add `result_json` to `serialize_llm_job` (contract change, affects `test_all_kinds_have_same_job_field_names` and `test_serialize_transcript_contract`) or switch `voiceNoteHtml` to the `/runs/voice_note` route as the Dump Review tab does, and add the missing `[data-dact]` bind. Deliberately not fixed here.

## 4. Backend hazards found by the Phase 1.5 completion-race check (out of frontend scope)

The mandated completion-race review (Fable) confirmed three backend issues. None are touched by this frontend-only PR, and the new UI is designed to avoid triggering two of them, but they are real:

1. `services/llm_jobs.py` voice_dump branch: the `job.status == "cancelled"` recheck happens at the *top* of each span iteration, so a cancel landing during the **final** `_structure_from_text` await is not seen before `job.result_json = {"items": items}` is committed. Net effect: a cancelled job can carry a fully committed draft. The voice_note branch does do the post-final-await recheck, so this is an inconsistency between siblings. *Mitigation in this PR*: `dumpReviewHtml` gates the editable draft on `job.status === 'completed'` and shows a distinct "cancelled" state, rather than gating on whether items exist.
2. `app.py` `save_voice_dump_draft`: resolves the job via `latest_job` with no status filter and writes `result_json["items"]` unconditionally. If a rerun is still `running`, a saved draft is written into the running job and then destroyed wholesale by the worker's own completion write. *Mitigation in this PR*: the UI only offers Save draft when the job is `completed`, so the client never posts into a running job. The route itself is still unguarded.
3. `app.py` `rerun_voice_dump_chain` + `finalize_voice_dump`: rerun does not check for existing `VoiceDumpItem` rows, finalize sets no marker on the job and does not delete prior rows, and `sequence_index` restarts at 0 per finalize. So complete → finalize → rerun → finalize inserts a duplicate batch that interleaves with the first under `order_by(sequence_index)`. *Mitigation in this PR*: the Rerun button is offered **only** from dead-end states (failed, cancelled, zero items), never from a completed draft or an already-finalized dump, so no new frontend path reaches this hazard.

**Recommended fix**: one backend issue covering all three (a `finalized_at`/`finalized` marker on the job, a status guard on save-draft, a post-final-await cancel recheck, and either idempotent finalize or a rerun guard).

## 5. Runner prompt's build/venv assumptions are half right on this machine

`.claude/issue-runner-prompt.md` "Infra notes" says fresh worktrees lack `node_modules` and to use "the main checkout's installed binaries: `npx esbuild ...` from the main repo path, or its `node_modules/.bin/`".

- **`node_modules` does not exist in the main checkout either** (`C:\Claude\WhisperDeck\node_modules` is absent), so `node_modules/.bin/esbuild` is not available anywhere. `npx --yes esbuild@0.25.0` works (pins the version `package.json` declares in `devDependencies`).
- The `.venv` note is correct: `C:\Claude\WhisperDeck\.venv\Scripts\python.exe` exists and works against worktree test paths.

**Recommended fix**: change the build note to `npx --yes esbuild@<version from package.json devDependencies>`, and drop the `node_modules/.bin/` suggestion or make it conditional on the directory actually existing.

## 6. `package.json build:js` did not match how the committed bundle was built

`build:js` was `esbuild static/rack.js --bundle --minify --outfile=static/rack.min.js` — no `--sourcemap`. But the committed `static/rack.min.js` ends with `//# sourceMappingURL=rack.min.js.map`, and `static/rack.min.js.map` is tracked. Verified empirically: building HEAD's `static/rack.js` with the declared command produces a file byte-identical to the committed bundle **except** for the missing trailing `sourceMappingURL` line. So the committed artifacts were not reproducible from the declared script.

Also verified the committed `.map` was **current**, not stale: its `sourcesContent` for `rack.js` matched `git show HEAD:static/rack.js` exactly. So simply dropping the sourcemap would have been a real regression (losing working devtools mapping and orphaning a tracked file), not a cleanup.

**Fixed here**: added `--sourcemap` to `build:js` so the declared command reproduces both committed artifacts. Confirmed a fresh build with the corrected command is byte-identical to both `static/rack.min.js` and `static/rack.min.js.map`. Disclosed as a `[decision]` in `self-audit.md` since it is outside the issue's stated `Files:` list.

## 7. `scripts/verify_self_audit.py` cannot verify any `--sourcemap` bundle

Consequence of item 6. The script extracts `esbuild <src> ... --outfile=<out>` from `package.json` and rebuilds via `cmd.replace(f"--outfile={out}", "--outfile={tmp}")` (`scripts/verify_self_audit.py:106`), then byte-diffs the temp output against the committed file. With `--sourcemap` in the command, esbuild derives the `sourceMappingURL` comment from the **outfile name**, so the temp build ends with `//# sourceMappingURL=<tmpname>.map` while the committed file says `rack.min.js.map`. The byte-diff can therefore never match, regardless of whether the bundle is actually fresh.

**Recommended fix**: in `scripts/verify_self_audit.py`, either strip a trailing `//# sourceMappingURL=...` line from both sides before comparing, or rebuild into a temp *directory* using the original outfile basename instead of rewriting the filename.

**Correction after actually running the checker**: the `--sourcemap` byte-diff problem above is real by inspection but was never reached. `scripts/verify_self_audit.py` fails earlier, on both bundles, with:

```
BUILD [build:js]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
BUILD [build:css]: rebuild failed (1): 'esbuild' is not recognized as an internal or external command
```

It shells out to the raw `esbuild` binary name, which is not on PATH on this machine (no `node_modules` anywhere, see item 5). `build:css` fails identically and nothing in this branch touches CSS, which confirms the failure is environmental rather than caused by this change. So the build-freshness check is currently a no-op on this machine for every PR, not just this one.

**Additional recommended fix**: have `scripts/verify_self_audit.py` invoke the script through the package manager (`npm run <script>`) or fall back to `npx --yes esbuild@<devDependencies version>`, so the check actually runs instead of reporting a blocking finding that is really "the tool isn't installed". As written, a genuinely stale bundle and a missing esbuild are indistinguishable in its output.

**Worked around here**: bundle freshness was proved the way the check intends but correctly — copied `static/rack.js`, `static/batch_aggregate.js`, `static/dump_review.js` into a temp directory, ran the exact corrected `build:js` command with the real `--outfile=static/rack.min.js` basename, and byte-compared. Both `rack.min.js` and `rack.min.js.map` came out identical. Recorded in `self-audit.md`.

## 8. Serena MCP is activated on the main checkout, not the worktree

Serena's instructions (delivered as MCP server instructions, and per the user's global CLAUDE.md treated as a binding session-start precondition) direct that its symbolic tools be preferred over `Read`/`Edit` for code files. But `initial_instructions` reported "The project with name 'WhisperDeck' at C:\Claude\WhisperDeck is activated", and Serena resolves every `relative_path` against that activated project root. Any Serena **edit** in this run would therefore have landed in the main checkout, not the worktree — silently defeating the worktree isolation the runner prompt requires.

Resolution used: Serena for read-only verification (`find_symbol` on `serialize_llm_job` and `_dictation_job_fields`, where main-checkout content is identical to a fresh worktree), and the built-in `Read`/`Edit`/`Write` tools for everything that mutates worktree files.

**Recommended fix** for `.claude/issue-runner-prompt.md`: add an explicit note to the Setup section that MCP code-intelligence servers may be pinned to the main checkout, so their editing tools must not be used inside a worktree run unless the server is re-activated on the worktree path. Verified at the end of the run via `git -C C:\Claude\WhisperDeck diff --stat` that nothing leaked into the main checkout.
