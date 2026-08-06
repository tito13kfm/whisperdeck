# Issue #110 Investigation: `_last_backend_error` shared mutable state across threads

Worktree read: `C:\Claude\WhisperDeck\.claude\worktrees\polished-swinging-alpaca` (fresh checkout of `origin/master`).

Note on the issue text: its cited line `services/voice_id.py:32` is actually correct (the `__init__` assignment), but its cited call site `app.py:1538` is **stale** -- that line in the current checkout is unrelated code inside `update_transcript_settings`'s per-file-overrides validation (`file_settings[{idx}] must be an object` block), not `enroll_speaker_from_transcript`. The real `enroll_speaker_from_transcript` route is at `app.py:2412-2413`, with its `add_clip()` call at `app.py:2460`. All line numbers below were verified against the current worktree content.

## 1. Structure of `services/voice_id.py`

Class: `VoiceIdentificationService` (`services/voice_id.py:23`). Singleton instance: `voice_id_service = VoiceIdentificationService()` at `services/voice_id.py:403`.

- **Initialized:** `services/voice_id.py:32` -- `self._last_backend_error = None` in `__init__`.
- **Writes (3 sites, all inside private `_embed_*` helpers):**
  - `services/voice_id.py:347` -- `_embed_speechbrain()`, on any `Exception` from torchaudio/speechbrain.
  - `services/voice_id.py:377` -- `_embed_pyannote()`, on any `Exception` from torch/soundfile/pyannote.
  - `services/voice_id.py:390-391` -- `_embed_mfcc()`, on librosa `Exception`, but only `if not self._last_backend_error` (i.e. it won't clobber a more specific speechbrain/pyannote error when MFCC is used as the fallback after a primary-backend failure -- note this itself is a read-modify-write on the same shared field with no synchronization).
- **Reads (2 sites, both in public methods):**
  - `services/voice_id.py:87` -- `enroll()`, builds the `reason` suffix for the raised `ValueError`.
  - `services/voice_id.py:131` -- `add_clip()`, same pattern.
  - `identify()` (`services/voice_id.py:221-224`) does **not** read `_last_backend_error` at all -- on `_extract_embedding()` returning `None` it just returns `[]`. This matters for section 5.

All three writers are reached only through `_extract_embedding()` (`services/voice_id.py:299-312`), which is itself called from three public methods: `enroll()` (line 78), `add_clip()` (line 129), `identify()` (line 222).

## 2. Every caller / entry point in scope

### Call sites of `_extract_embedding` (repo-wide grep, excluding `docs/`)
- `services/voice_id.py:78` (`enroll`), `:129` (`add_clip`), `:222` (`identify`) -- the only production call sites; it's a private (`_`-prefixed) method with no other callers in `app.py` or `services/llm_jobs.py`.
- Test call sites: `tests/test_voice_id.py` (directly calls/monkeypatches `_extract_embedding` extensively, e.g. lines 62, 75, 88, 222, 244, 261, 277, 282, 303, 316, 337, 356, 383, 401, 419, 438, 451, 467, 488, 505, 551, 587, 592, 617), `tests/test_voice_match_job.py:102`.

### Call sites of `enroll` / `add_clip` / `identify` on the singleton
- `app.py:3350` -- `POST /api/voices/enroll` -> `voice_id_service.enroll(...)`.
- `app.py:2460` -- `POST /api/transcripts/{transcript_id}/enroll-speaker` (`enroll_speaker_from_transcript`, defined at `app.py:2412-2413`) -> `voice_id_service.add_clip(...)`.
- `app.py:3417` -- `POST /api/voices/{profile_id}/clips` (`add_voice_clip`, `app.py:3400-3401`) -> `voice_id_service.add_clip(...)`.
- `app.py:3381` -- `POST /api/voices/identify` (`identify_speaker`, `app.py:3363-3364`) -> `voice_id_service.identify(...)`.
- `services/llm_jobs.py:729` -- inside the `voice_match` job branch (`services/llm_jobs.py:690` onward), wrapped in a local closure `_identify()` (line 728) and dispatched via `loop.run_in_executor(None, _identify)` at `services/llm_jobs.py:731`.

No other production call sites of `enroll`/`add_clip`/`identify` exist (`app.py` and `services/llm_jobs.py` are the only non-test importers of `voice_id_service`).

### Which thread each call site actually runs on (verified by reading the code, not assumed)
- `app.py:3350` `enroll_voice` -- `async def` route (`app.py:3332`), calls `voice_id_service.enroll(...)` with a plain (non-`await`) call. FastAPI/Starlette executes `async def` path functions directly on the **event loop thread** -- no executor offload here. **Runs on the event loop thread.**
- `app.py:2460` `enroll_speaker_from_transcript` -- `async def` route (`app.py:2413`), calls `voice_id_service.add_clip(...)` as a plain synchronous call, no `await`, no `run_in_executor`/`to_thread`. **Runs on the event loop thread**, and additionally blocks the loop for the duration of embedding extraction (a pre-existing perf issue, not this bug, but the same call is the racy one the issue names).
- `app.py:3417` `add_voice_clip` -- same shape, `async def` route (`app.py:3401`), plain call. **Runs on the event loop thread.**
- `app.py:3381` `identify_speaker` -- same shape, `async def` route (`app.py:3364`), plain call to `voice_id_service.identify(...)`. **Runs on the event loop thread.** (This call writes `_last_backend_error` as a side effect of a failing `_extract_embedding()`, even though `identify()` never reads it back -- see section 1.)
- `services/llm_jobs.py:731` -- the **only** call site that is *not* on the event loop thread. The comment at `services/llm_jobs.py:722-726` documents the intent explicitly: `identify()` is dispatched via `loop.run_in_executor(None, _identify)`, i.e. Python's default `ThreadPoolExecutor`. **Runs on a worker thread from the default executor pool**, once per diarized segment, inside the `voice_match` job (`services/llm_jobs.py:690`).

So the real race is: the `voice_match` job's executor thread (`llm_jobs.py:731`) can write `_last_backend_error` (via a failing embedding extraction inside `identify()`) at the same wall-clock moment that any of the three event-loop routes (`enroll_voice`, `enroll_speaker_from_transcript`, `add_voice_clip`) is reading `_last_backend_error` after its *own* extraction failed -- because they're different threads touching the same unsynchronized instance attribute. The issue only names `enroll_speaker_from_transcript`; `enroll_voice` (`app.py:3350`) and `add_voice_clip` (`app.py:3417`) are equally exposed and are missing from the issue's own description -- the Complement Rule flags both as the "second caller(s)" a partial fix would miss if it only patched the route the issue happened to mention.

`_MAX_CONCURRENT_CPU_JOBS = 1` (`services/llm_jobs.py:45`) only caps concurrency **among jobs of `CPU_KINDS` (`rediarize`, `voice_match`) pulled through the LLM job queue** (`services/llm_jobs.py:43,853`). It does nothing to serialize a running `voice_match` job against the three `app.py` routes, which never go through that queue/cap mechanism at all -- they're called directly from HTTP handlers. The issue's own "currently mitigated by..." claim is only a partial mitigation (job vs. job), not a mitigation for the job vs. route race that is the actual subject of the issue.

## 3. Sibling sweep -- other instance attributes on `VoiceIdentificationService`

All `self._...` assignments in `services/voice_id.py`:

| Attribute | Set at | Verdict |
|---|---|---|
| `self.voices_dir` | `services/voice_id.py:27` (`__init__` only) | **Not racy** -- read-only after init (only read, e.g. `services/voice_id.py:325`, `tests/test_voice_id.py:520-521`). |
| `self._backend` | `services/voice_id.py:29` (`__init__`, via `_detect_backend()`); also directly overwritten in tests (`tests/test_voice_id.py:182`, `tests/test_voice_match_job.py:123` patches `services.llm_jobs.voice_id_service._backend`) | **Read-only-after-init in production** -- never reassigned outside `__init__` in `services/voice_id.py` itself. Read from both the event loop (`app.py:588,589,969,3386`) and executor thread (`services/llm_jobs.py:691`), but since production code never mutates it after construction, concurrent reads are safe. Not part of this bug. |
| `self._classifier` | `services/voice_id.py:30` (`__init__`, `None`); lazily set in `_get_classifier()` at `services/voice_id.py:321-327` | **Racy -- a second, previously unnamed bug.** `_get_classifier()` is a classic unguarded check-then-act lazy-init: `if self._classifier is None: ... self._classifier = EncoderClassifier.from_hparams(...)`. It is reachable from `_embed_speechbrain()` (line 335), which is reachable from `_extract_embedding()` -- i.e. from `enroll`/`add_clip`/`identify`, i.e. from the same event-loop routes and the same `run_in_executor` thread identified in section 2. Two threads racing into `_get_classifier()` simultaneously (e.g. a `voice_match` executor-thread call overlapping an `enroll_voice` event-loop call) can both observe `None`, both call the slow `EncoderClassifier.from_hparams(...)` (which loads/downloads model weights from `savedir`), and one assignment silently clobbers the other -- wasted CPU/IO at best, a corrupted partial download on disk at worst if two loads race on the same `savedir` path (`services/voice_id.py:325`, `os.path.join(self.voices_dir, "_models", "ecapa")` -- the same path for every instance sharing `voices_dir`, which the singleton always does). This is a materially worse race than the error-text one the issue names, and it's on the same call graph. |
| `self._pyannote_inference` | `services/voice_id.py:31` (`__init__`, `None`); lazily set in `_get_pyannote_inference()` at `services/voice_id.py:351-357` | **Racy -- same class of bug as `_classifier`.** Identical unguarded check-then-act lazy-init pattern, reachable from `_embed_pyannote()` (line 365) via the same call graph. |
| `self._last_backend_error` | `services/voice_id.py:32` | **Racy -- the bug named by the issue.** See sections 1-2. |

Module-level mutables in `services/voice_id.py`: `_DEFAULT_VOICES_DIR` (`services/voice_id.py:20`) is a plain string computed once at import time -- not mutated anywhere, not racy. `voice_id_service` (`services/voice_id.py:403`) is the module-level singleton itself; it is not reassigned anywhere, only its instance attributes (covered above) are mutated.

**Explicit answer:** the sweep found two additional racy attributes beyond `_last_backend_error` -- `self._classifier` and `self._pyannote_inference` -- both unguarded lazy-init caches reachable from the exact same event-loop-vs-executor-thread call graph. Any fix that only addresses `_last_backend_error` and stops there is incomplete with respect to the underlying "no synchronization on shared instance state across the enroll routes and the voice_match executor thread" hazard class, even though those two are arguably a distinct (and worse) bug than what issue #110 titles.

## 4. Existing lock/threading primitives to reuse

Repo-wide grep for `threading.Lock`, `threading.local`, `RLock`, `asyncio.Lock`, and `_lock` (`*.py`, all of `services/` and the whole repo):

- **No `threading.Lock`, `threading.local`, `RLock`, or `asyncio.Lock` exists anywhere in the Python source of this repo** (confirmed with a repo-wide grep restricted to `*.py`, zero matches).
- The closest existing concurrency-control convention is `asyncio.Semaphore`, used in `services/queue.py`:
  - `services/queue.py:412,456` -- `local_provider_lock: asyncio.Semaphore` parameter, `async with local_provider_lock:` guarding a critical section.
  - `services/queue.py:780` -- `local_provider_lock = asyncio.Semaphore(1)` constructed once and threaded through the transcription job pipeline.
  - This is an `asyncio.Semaphore`, which only guards coroutines cooperating on the **same event loop** -- it does **not** provide mutual exclusion against a `run_in_executor` worker thread, so it is not a drop-in fix for this issue even though it's the repo's nearest precedent for "shared critical section."
- `_MAX_CONCURRENT_CPU_JOBS`/`_MAX_CONCURRENT_IO_JOBS` (`services/llm_jobs.py:44-45`) is a plain integer cap enforced by counting rows/tasks (`services/llm_jobs.py:853`), not a lock primitive.
- There is a documented, deliberate assumption elsewhere in the same file that cross-thread access to shared resources is safe **for SQLite specifically** because of `check_same_thread=False` (comment at `services/llm_jobs.py:722-726`), but no equivalent statement or primitive exists for the in-memory service attributes.

**Conclusion:** the repo has no existing lock/thread-local shape to reuse for this exact problem. Whatever fix is chosen for issue #110 would be the first `threading.Lock`/`threading.local` in the codebase -- worth flagging to the orchestrator/reviewer since "reuse an existing shape" (the stated repo convention) doesn't apply here; the nearest analog (`asyncio.Semaphore` in `services/queue.py`) doesn't solve the event-loop-vs-thread-pool problem.

## 5. Evaluating the issue's three proposed fixes against the real call graph

What `enroll()`/`add_clip()`/`identify()` actually return today:
- `enroll()` (`services/voice_id.py:69-112`) returns a `VoiceProfile` ORM object, or **raises `ValueError`** (message built at lines 87-92, includes the `reason` from `_last_backend_error`) on extraction failure.
- `add_clip()` (`services/voice_id.py:114-140`) returns a `VoiceClip` ORM object, or **raises `ValueError`** (lines 131-135, same `reason` pattern) on extraction failure.
- `identify()` (`services/voice_id.py:221-250`) returns `list[dict]` of matches, or an **empty list** on extraction failure -- it never raises and never surfaces `_last_backend_error` at all.

How callers consume the error text:
- `app.py:3359-3360` (`enroll_voice`) and `app.py:3421-3426` (`add_voice_clip`) both catch the exception generically (`except Exception`/`except ValueError`) and forward `str(e)` straight into `HTTPException(..., detail=str(e))` -- the frontend presumably renders `detail` verbatim as the error message shown to the user (issue's stated impact: "Wrong error message shown to user in `enroll()`/`add_clip()` diagnostics").
- `app.py:2438-2441` (`enroll_speaker_from_transcript`, the route the issue names) does **not** currently catch `ValueError` from `add_clip()` at all in the block shown (lines 2444-2462 have no `try/except ValueError` around the `add_clip` call itself -- only the earlier `extract_clips_concat` call at line 2438-2441 is wrapped) -- a `ValueError` from `add_clip()` here would propagate as an unhandled 500, not a clean 400. That's a separate defect worth flagging to the orchestrator, but not this issue's scope.
- `services/llm_jobs.py:737-741` -- the `voice_match` job never sees `_last_backend_error` at all, since `identify()` swallows extraction failures into an empty match list (`matches = []` triggers no special handling beyond `skipped += 1` at line 741, i.e. it's caught by the surrounding generic `except Exception` at line 740, which fires on other errors, not the `None`/`[]` path -- extraction failure inside `identify()` just silently produces zero matches for that segment, no error text is ever read back into the job's `result_json` or `job.error`).

Given this, evaluating the issue's three options:

1. **`threading.local()`** -- Wrong shape for this call graph. `_last_backend_error` needs to be visible to the *same logical request* across nested method calls (`_extract_embedding` -> `_embed_*` -> back up to `enroll`/`add_clip`), and every one of those calls happens on a single thread already (either the event loop thread or one `run_in_executor` worker thread) -- so thread-local storage would actually work correctly for isolating the four production call sites in section 2 from each other. It's a legitimate option, but it doesn't fix anything for `identify()` (which never reads the attribute anyway), so it's solving only the `enroll`/`add_clip` half of the graph and leaves the two lazy-init caches from section 3 completely untouched (thread-local wouldn't help `_classifier`/`_pyannote_inference`, which are meant to be shared/cached across calls, not per-thread -- making them thread-local would defeat their entire caching purpose, e.g. every executor thread would reload the model). Also increases object-identity complexity for no real gain since the value only needs to survive one call chain.
2. **Return the error as part of `_extract_embedding()`'s return value** -- Cleanest fit for the actual call graph. `_extract_embedding()` has exactly 3 production callers (`enroll` line 78, `add_clip` line 129, `identify` line 222), all in the same file, all already pattern-matching on `result is None`. Changing its signature to something like `Optional[tuple[np.ndarray, str]], Optional[str]` (or a small dataclass/namedtuple with an explicit error field) touches exactly those 3 call sites plus the 3 `_embed_*`/`_mfcc_fallback` internal helpers that currently mutate `self._last_backend_error` (lines 304,309,311,316,347,377,390-391) -- no external module (`app.py`, `services/llm_jobs.py`) calls `_extract_embedding()` directly (it's `_`-prefixed and only called within `services/voice_id.py`), so **zero external signature changes are required**; only `enroll()`/`add_clip()`/`identify()` keep their existing public signatures and return types. `identify()` currently "ignores" the error (returns `[]`), and would continue to be free to ignore the new inline error value if desired, or start surfacing it -- either way nothing forces `identify()`'s external contract to change. This is the option consistent with removing all reads/writes of `self._last_backend_error` as *shared* state, since the value would now flow through a normal return value/local variable, eliminating the race entirely rather than mitigating it with a lock.
3. **Lock around reads/writes** -- Works for `_last_backend_error` alone, but per section 4 there is no existing lock primitive in the repo, so this introduces a new pattern with no precedent, and it doesn't address the (arguably worse) `_classifier`/`_pyannote_inference` lazy-init races in section 3, which are a similar-in-kind bug on the same object. A lock could be reused for all three attributes together (one `self._state_lock` guarding writes to `_last_backend_error`, `_classifier`, and `_pyannote_inference`), which would be the most complete fix of the three proposed *if* the orchestrator wants to fix the whole sibling class of bugs in one pass rather than just the one the issue names.

**What breaks with option 2:** `tests/test_voice_id.py:200-209` (`test_enroll_error_includes_underlying_reason_when_all_backends_fail`) directly sets `svc._last_backend_error = "..."` (line 204) *without* going through `_extract_embedding()` at all (it monkeypatches `_embed_speechbrain`/`_embed_mfcc` to return `None` but never touches `_last_backend_error` via a return value) and then asserts the string appears in `enroll()`'s raised message. If `enroll()` stops reading `self._last_backend_error` and starts reading a value returned inline from `_extract_embedding()`, this test's construction (pre-seeding the instance attribute) would need to be rewritten to instead have `_extract_embedding()` (or the underlying `_embed_*` mocks) return the error string -- this is a required, not incidental, test update for any option-2 fix. Similarly `tests/test_voice_id.py:180-197` (`test_embed_pyannote_sets_last_backend_error_on_failure`) asserts directly on `svc._last_backend_error` after calling `_embed_pyannote()` -- this test would need to change to assert on `_embed_pyannote()`'s new return shape instead (or `_extract_embedding()`'s), since `_embed_pyannote` currently returns only the embedding (`Optional[np.ndarray]`) with the error as a side channel.

## 6. Existing tests touching this state

- `tests/test_voice_id.py` (`C:\Claude\WhisperDeck\.claude\worktrees\polished-swinging-alpaca\tests\test_voice_id.py`):
  - Line 180-197 `test_embed_pyannote_sets_last_backend_error_on_failure` -- asserts `"pyannote" in svc._last_backend_error` and `"gated repo" in svc._last_backend_error` directly against the instance attribute.
  - Line 200-209 `test_enroll_error_includes_underlying_reason_when_all_backends_fail` -- pre-sets `svc._last_backend_error` manually (line 204) and asserts the substring surfaces in the `ValueError` raised by `enroll()`.
  - Many other tests in this file (lines 56-88, 222-617) call/monkeypatch `_extract_embedding` or the `_embed_*` methods directly but do not assert on `_last_backend_error`.
  - None of these tests are multithreaded -- each uses a fresh `VoiceIdentificationService(voices_dir=str(tmp_path/...))` instance (helper `_svc(tmp_path)`), so no existing test exercises the actual cross-thread race; they only assert the single-threaded happy-path semantics of "the last write is the one read back."
- `tests/test_voice_match_job.py` (`C:\Claude\WhisperDeck\.claude\worktrees\polished-swinging-alpaca\tests\test_voice_match_job.py`) -- patches `voice_id_service.identify` (lines 74, 196, 276) or `._extract_embedding` (line 102) or `._backend` (line 123) at the module/singleton level for the `voice_match` job; does not touch `_last_backend_error`.
- `tests/test_relabel_undo.py` (`C:\Claude\WhisperDeck\.claude\worktrees\polished-swinging-alpaca\tests\test_relabel_undo.py:206`) -- patches `services.llm_jobs.voice_id_service.identify`; does not touch `_last_backend_error`.
- `tests/test_speaker_naming.py` (`C:\Claude\WhisperDeck\.claude\worktrees\polished-swinging-alpaca\tests\test_speaker_naming.py`) -- patches `app.voice_id_service.add_clip`/`_extract_embedding` (lines 161, 179, 206, 236, 283, 372) end-to-end through the HTTP routes; its error-text assertions (lines 183 `"no backend"`, 241/288 `"boom"`) all come from a mocked `side_effect=ValueError(...)`, not from the real `_last_backend_error` interpolation path -- so this file doesn't constrain the fix.

**Constraint on the fix:** yes -- `tests/test_voice_id.py:196-197` and `:204/209` assert directly on `_last_backend_error` as an instance attribute (both reading and pre-seeding it). Any fix that removes `_last_backend_error` as an instance attribute (options 1 and 2) requires updating these two tests; a pure lock-around-existing-attribute fix (option 3) would not require changing them.

## 7. Testing tiers policy (`AGENTS.md`)

Quoted verbatim from `AGENTS.md:171-181` in the worktree:

> ## Testing tiers: match test cost to change blast radius
>
> Don't run full browser-driven e2e audits for every small change; reserve them for milestones. Pick the tier by what the change actually touches:
>
> 1. **Unit/integration test for the touched path** -- default for any change. Fast, run every time, no exception.
> 2. **`e2e-regression-http` (scripted 16-scenario Playwright regression, requires a live browser tool)** -- before merging anything that changes request/response contracts or cross-feature flow (queue/job routing, serializer shape, multi-step API behavior). If no Playwright MCP tool is available, substitute a static contract check (verify the serializer/field list directly in source) plus the existing unit/integration suite, and say so explicitly rather than silently skipping the tier.
> 3. **Full browser e2e (`e2e-ux-audit`, `e2e-ux-audit-deep`)** -- reserve for pre-release checkpoints or after a batch of changes lands, not per-PR. Any change with a runtime/UI surface should drive the affected flow in a targeted manual/scripted check, not the full 6-journey or deep audit suite.
>
> Rule of thumb: a backend fix scoped to one module doesn't need a browser; a UI-visible or cross-cutting change does, but scope the runtime check to the flow that changed, not the whole app.

This is a backend fix scoped to `services/voice_id.py` (and possibly its 4-5 call sites' error-handling in `app.py`/`services/llm_jobs.py`), with no request/response contract change if option 2 is chosen (return types of the public `enroll`/`add_clip`/`identify` methods stay the same) -- so **tier 1 (unit/integration test for the touched path)** is what this change needs: update `tests/test_voice_id.py:180-209` for the new error-plumbing shape, and add a genuine concurrency-reproduction test (e.g. two threads/an executor + direct call racing into `_extract_embedding`/`_get_classifier`) to actually prove the fix, since the Complement Rule / mutation-check note (`AGENTS.md:181`) requires that new tests fail if the fix were reverted -- none of the current tests exercise multiple threads, so a same-thread test alone would not catch a regression of the actual race.
