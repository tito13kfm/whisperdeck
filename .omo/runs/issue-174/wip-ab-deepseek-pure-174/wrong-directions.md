# Wrong Directions — issue #174, wip/ab-deepseek-pure-174

## 1. AGENTS.md claims explore/explore-hard are local (still wrong)

AGENTS.md line ~127 and the model table say `explore` uses `Qwen3.5-4B-MTP-GGUF` (local) and `explore-hard` uses `DeepSeek-Qwen3-8B-GGUF` (local), subject to the 2-agent local concurrency cap.

**Live config** (`~/.config/opencode/oh-my-openagent.json`, lines 20-29) maps BOTH to `openrouter/inclusionai/ling-3.0-flash:free` — a cloud/OpenRouter model. No local cap applies to either.

This was already a known doc error (the workflow prompt calls it out), but it's worth reconfirming: the live config, not AGENTS.md, is the source of truth for agent→model mapping. Using cloud explore agents let me fire 3 in parallel without capping.

**Recommended fix:** Remove the static model table from AGENTS.md. The explore section should read: "Which agent types go to which models is controlled by `oh-my-openagent.json` — check that file, not this doc. The 2-agent concurrency cap applies to any agent/category currently mapped to a `lemonade/` model prefix."

## 2. Worktree file-write gotcha

I wrote `services/search.py` and `tests/test_search.py` to the main repo checkout (`C:/Claude/whisperdesk/...`) instead of the worktree (`C:/Claude/whisperdesk-174/...`). The write tool uses absolute paths and I resolved against the wrong directory.

**Why it mattered:** The worktree shares the same `.git` directory but has its own index. Files written to the main repo's working tree were on the master branch's file system, not the worktree branch's. After copying to the worktree and deleting from main repo, the worktree correctly showed them as untracked files on the feature branch.

**Recommended fix:** None for the workflow — this is a tool usage error on my part. But the report-file scoping note in Setup correctly warns about this exact class of error for `.omo/` report files. Consider adding: "Source code edits (not just reports) also go to the worktree, not the main repo. Resolve all write paths against the worktree directory."

## 3. LSP unavailable (expected)

`basedpyright` is not installed and user previously declined installation. No diagnostics available. Tests served as the sole correctness check.

**Recommended fix:** None. This is a known environment state. The project's tests are the primary correctness mechanism.
