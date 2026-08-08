# Token Usage — issue #125, variant `minimax-m3`

> Live token / cost numbers for this run live in OpenCode's own usage
> panel, not anything this file can read. This file lists the
> sub-sessions spawned, the model backing each, and where context went.

## Sub-sessions / agents spawned this run

| Phase | Sub-session | Model | Local/Cloud | Notes |
|-------|-------------|-------|-------------|-------|
| Phase 0 | (this main thread) | `opencode-go/minimax-m3` | Cloud | Orchestrator, the human's chosen variant label. |
| Phase 1 | `bg_4ca15217` (`ses_05ecc158fffeNRKk0oZXYxmSHd`) | `lemonade/Qwen3.5-4B-MTP-GGUF` | Local | `explore` agent (fallback from `explore-hard`, which the `task` tool does not expose) — register route + IntegrityError surface. |
| Phase 1 | `bg_6cbebac1` (`ses_05ecbf365ffecAl1jd6fRr7TKW`) | `lemonade/Qwen3.5-4B-MTP-GGUF` | Local | `explore` agent — User model + registration tests. |
| Phase 2 | (none) | n/a | n/a | Fix implemented directly by the main thread. The fix is mechanical transcription of `investigation.md`'s plan; dispatching to a `deep`/`ultrabrain` agent would have been heavier than the work. See "Cuts for next time" below. |
| Phase 3 | (none) | n/a | n/a | Static source check + regression test. Tests run via the main repo's `.venv\Scripts\python.exe -m pytest` (the worktree itself has no `.venv` because `git worktree add` does not copy it — caught early, see "Cuts for next time"). |

## Where context went

- **Phase 1 (2 local agents, 2-cap hit):** two parallel `explore` dispatches, narrow file-scoped prompts. The local concurrency cap was respected; both ran simultaneously, total wall time ~5 min. The 4B model is the lighter of the two local models and worked fine for these read-only file-mapped lookups; the prompts would not have benefited materially from the 8B (`explore-hard`) variant.
- **No librarian dispatch:** no unfamiliar library involved. SQLAlchemy + FastAPI were already in the dependency tree. The fix is one `try/except` against a documented `sqlalchemy.exc.IntegrityError`.
- **No `ultrabrain`/`deep` dispatch:** the fix is small and the spec (in `investigation.md`) is complete. The delegation-exception note in the system prompt explicitly allows direct implementation when a Phase 1 investigation file already contains a complete, unambiguous plan. This run qualifies.
- **No live server / browser cycle:** AGENTS.md's testing tier 1 covers this change. Tier 2 (`e2e-regression-http`) is for "changes that change request/response contracts or cross-feature flow" — this change preserves the success-path 200 response and the existing synchronous 400 response, only adds a 400 in the previously-broken 500 path. No contract change.
- **The worktree's missing `.venv` was caught by the user mid-run.** I started searching the filesystem for a Python interpreter, the user pointed at the main repo's venv directly. This is a `.git worktree add` behavior, not a project bug — `git worktree` shares the `.git` directory but not untracked files. For future A/B runs, skip the filesystem search and point at the main repo's `.venv\Scripts\python.exe` immediately.

## Cuts for next time

1. **Skip the filesystem-wide search for a Python interpreter when the worktree has no `.venv`.** The main repo's `.venv\Scripts\python.exe` is the right answer every time for this repo. The worktree's cwd is irrelevant — pytest just needs an interpreter with the right packages.
2. **For this class of bug (single-route try/except wrap), don't dispatch to a heavy-reasoning agent.** The investigation already contains the exact fix shape; the remaining work is mechanical transcription. Reserve `ultrabrain`/`deep` for work that still requires judgment.
3. **The static check +1 / −1 file-swap to confirm the test fails without the fix is a strong gate.** I did it here (revert the try/except, run the test, see the original `UNIQUE constraint failed: users.username` error propagate out, restore the fix). Took ~30 seconds. Should be the default for every bug-fix PR with a regression test.
4. **`from_end=true` on every `background_output` call.** Used it on both Phase 1 dispatches; cut the noise from the agent framework's per-turn `<analysis>` blocks.
5. **Discovered and re-discovered that the `task` tool doesn't expose `explore-hard` even though the config defines it.** Worth pre-flighting the available agent names with a single no-op dispatch before Phase 1 if a future runner is uncertain.
