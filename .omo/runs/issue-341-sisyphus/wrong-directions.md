# wrong-directions.md — issue #341 (sisyphus)

## Prompt discrepancies

1. **`deep` as subagent_type does not resolve** — the issue-runner prompt says "use `deep`" as a subagent_type, but `task(subagent_type="deep", ...)` returns `Unknown agent: "deep"`. `deep` only resolves as a `category` parameter (`task(category="deep", ...)`). The prompt's Agent assignments per phase table should be updated to use `category="deep"` or `task(category="deep", ...)` instead of "use `deep`."

2. **Prompt says "`deep` is the heavy-reasoning tier" and "`ultrabrain` has been removed from `oh-my-openagent.json`"** — this is correct per the live config (neither `deep` nor `ultrabrain` exists as a subagent_type), but the resolution instruction in the same paragraph (`use a `deep` agent for actual reasoning`) is phrased as if `deep` is a direct agent name, when it's only a category.

## Investigation errors

3. **investigation.md (deep agent) claimed `count_distinct_speakers` was imported at `services/llm_jobs.py:796` for the voice_match branch.** Actual code: the voice_match branch has a local `from services.relabel import count_distinct_speakers` at line 796, but that's a local import inside the voice_match branch — not available at the rediarize branch (line 681). The first test run failed with `NameError: name 'count_distinct_speakers' is not defined`. Fixed by adding the import to the rediarize branch's local import at line 678.

## Pre-existing test failures

4. **`test_voice_match_recomputes_speaker_count_on_merge` fails on clean master** — the test mocks `voice_id_service.identify` but the voice_match path calls `voice_id_service.identify_detailed` instead (line 763). The mock patch doesn't intercept the actual call, so segments are never renamed. Pre-dates this PR, not in scope.

