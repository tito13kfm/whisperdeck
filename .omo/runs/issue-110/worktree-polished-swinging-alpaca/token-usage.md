# Token usage / agent ledger — issue #110

Run branch: `worktree-polished-swinging-alpaca`
Orchestrator: Opus (Claude Code, `/issue-claude 110`). All design, Phase 2 edits, test authoring,
Phase 3.5 self-audit, and Phase 4 were done inline on Opus.

| Phase | Agent | Model | subagent_type | Purpose |
|---|---|---|---|---|
| 1 | Investigate voice_id error state race | **Sonnet** | `Explore` (read-only) | Map `_last_backend_error` reads/writes, enumerate all call sites and their thread contexts, sibling sweep for other racy attributes, evaluate the issue's three proposed fixes against the real call graph |
| 1.5 | *(not dispatched)* | Fable | `general-purpose` | Completion-race check. **Not triggered** — see `self-audit.md`. Phase 1 surfaced no "mark state completed, then fire a side effect in the same try block" shape: in the `voice_match` branch (`services/llm_jobs.py:690-752`) `_finish(db, job, "completed", error)` at line 752 is the terminal statement of the branch, and `_finish` itself (`services/llm_jobs.py:319-330`) only sets the terminal state behind a cancel-wins guard with no enqueue/callback after it. The Fable call budget was therefore not spent. |
| 2 | *(none)* | Haiku | `general-purpose` | No bounded mechanical sub-edit arose. All five edits to `services/voice_id.py` were judgment calls (thread-story design) and were made inline. |
| 3 | Verify voice_id thread-safety fix | **Sonnet** | `general-purpose` | Run the suite, drive red-green for all four new tests (four independent fix-reverting mutations plus trivial-constant mutations), confirm the worktree diff is unchanged after each temporary mutation, run the neighbouring call-site suites and the full suite, and do the tier-2 substitute static contract check on the `app.py` error paths |

Agent count: **2 dispatched** (both Sonnet). Zero Fable calls, zero Haiku calls.
