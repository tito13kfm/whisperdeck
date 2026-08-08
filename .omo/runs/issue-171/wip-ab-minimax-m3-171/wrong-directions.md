# Wrong directions

## `explore-hard` is not a valid `subagent_type` in this build

The `oh-my-openagent.json` config defines both `explore` and `explore-hard` as named agents with different model backings, but the `task()` tool's accepted `subagent_type` values do not include `explore-hard`. Only `explore` is available for the heavy-reasoning "find the kind dispatch in services/llm_jobs.py and explain every branch" workload.

Effect on this run: I am using `explore` for both dispatches. The prompt says "explore-hard — anything requiring actual reasoning". The lighter-weight `explore` may miss subtle branch logic in the LLM job dispatch — I will re-verify any complex claims with a direct `Read` after it returns. Recommended fix: the `task` tool's allowed subagent_type list needs to be reconciled with the config, or the prompt's "use explore-hard for hard work" guidance needs to be replaced with "use explore for everything; re-verify complex claims with a direct Read".

The prompt also calls out `scout` and `plan` as agent names that may be missing — neither is in the tool's list either. The actual `plan` agent IS in the list (this run did not invoke it; not a regression yet). `scout` is not.

---

## minimax-m3 run (2026-07-27) — confirmed prior findings, added new ones

### `explore-hard` is still not a valid subagent_type in this build (CONFIRMED)

Same finding as the aborted prior minimax-m3 attempt and the prior deepseek-pro run — third independent confirmation. The `task()` tool's accepted `subagent_type` list does not include `explore-hard`. The prompt's "Phase 1 (investigate): ... explore-hard — anything requiring actual reasoning" guidance is wrong for the current build. Used `explore` for Phase 1 and re-verified complex claims with direct `Read` calls.

**Recommended fix** (already in AGENTS.md's known-doc-error list): either add `explore-hard` to the `task` tool's allowed list, or update the prompt to say "use `explore` for everything; re-verify complex claims with a direct Read".

### `scout` not in the tool's agent list (CONFIRMED, separately)

The prompt's "Known doc error" says `scout` may not resolve and tells me to use `explore`/`explore-hard` as a fallback. This minimax-m3 run did not invoke `scout`, so I cannot independently confirm — but the prompt is the source of truth, and the other "known doc error" (`explore-hard`) is confirmed real, so I trust the prompt here.

### `plan` is in the tool's list (CONFIRMED)

System prompt's `task` tool description lists `plan` under "Available agent types". The aborted prior run's wrong-directions.md said "The actual `plan` agent IS in the list" — confirmed for this run too. Did not invoke it; this run did Phase 1 with direct Reads + `explore`, which is sufficient for the scope.

### `codegraph_explore` query budget

Codegraph was not invoked this run. The investigation is purely direct-Read-driven because the LlmJob dispatch table is small and the touchpoints are well-scoped from the existing `LlmJob` pattern. If a future run is broader, codegraph is the cheaper first call.

### Issue body has no acceptance-criteria checklist (CONFIRMED)

Confirmed by the prior deepseek-pro run too. The issue is open-ended feature scope, no formal Definition of Done. The investigation file lists my implicit criteria; the self-audit will checkmark against those.

---

## minimax-m3 run (2026-07-27) — Phase 3, second batch of findings

### Browser-tier (e2e-regression-http) skipped — explicit, not silent

This change adds a new UI affordance: bank row tag pills, detail-page tag
section, `bankQuery` filter extended to tags, new `tagging` job on the
Queue screen. AGENTS.md testing-tiers rule says a UI-visible change
warrants at least a targeted browser flow check. This session has no
Playwright MCP browser tool available, so the e2e-regression-http 16-scenario
suite was not run. The static + unit test coverage stands in.

**Recommended fix for the human**: run the e2e-regression-http suite
before merging this branch. The targeted flow to drive is in
`self-audit.md` under "Browser-tier skip".

### Test bug: `user.id is None at first commit` (twice in this run)

I wrote two tests where I did `db_session.add(user); db_session.add(ProviderConfig(user_id=user.id, ...)); db_session.commit()` without committing the user first. SQLAlchemy doesn't assign the autoincrement id until the next flush, so the `user.id` in the second `add` was `None`, and the ProviderConfig was committed with `user_id=None`. `resolve_provider_key` then returned empty, and the test got the "no API key saved" skip path.

The fix in both places: `db_session.commit()` after `add(user)` and before
`add(ProviderConfig(...))`. This is a "test the model, not the side
effect" trap — the helper would have hidden it if it had been used. Worth
recording so future test-writing doesn't repeat.

### Test bug: 1-char tag strings in test inputs

I wrote a test with `["a", "b"]` as the LLM's tag output. The
`_normalize` helper has `_MIN_TAG_LEN = 2` and correctly drops
single-character tags. The test asserted they came back, which failed.

The fix: use realistic tag strings (`"alpha"`, `"beta"`) in the test
inputs. Recorded because the failure looked like a code bug at first
glance, not a test-input bug.

### No Python LSP installed in this environment

`lsp_diagnostics` for the changed files returned "file path must be
inside request cwd" rather than diagnostics — the Python LSP (pyright /
basedpyright / ty / ruff) is not installed. Static verification fell
back to `python -c "import ast; ast.parse(...)"` for the changed files
(8/8 parsed cleanly) and to running the full pytest suite (464/464
passing). Not a blocker, but worth noting if a future run sees different
diagnostic output and wonders why.

