# Wrong Directions — issue #125, variant `minimax-m3`

## 2026-07-26 — explore-hard agent not exposed by `task` tool

**What I tried:** `task(subagent_type="explore-hard", ...)` per the OMO
issue-runner-prompt's Phase 1 agent assignments.

**What happened:** `task` tool errored: `Unknown agent: "explore-hard".
Available agents: [..., explore, general, librarian, ...]`. The `task()`
tool's agent-name registry only exposes `explore`; the `explore-hard` key
defined in `~/.config/opencode/oh-my-openagent.json` (model
`lemonade/DeepSeek-Qwen3-8B-GGUF`) is not surfaced as a callable subagent
in this environment.

**Fix taken:** Fell back to `task(subagent_type="explore", ...)` per the
prompt's "use explore/explore-hard instead" rule (which the prompt
phrased for scout/plan but applies symmetrically). Two parallel `explore`
dispatches under the 2-local-agent cap. Both completed; both returned
usable results (one had to retry a couple of grep/glob calls because
its first attempts used Linux-style paths against a Windows worktree, but
the final answer was correct and matched my own direct reads of the
files).

**Recommended doc fix for next runner of this prompt:** the prompt
assumes `explore-hard` resolves. Either the prompt should explicitly say
"if explore-hard fails, fall back to explore" (it currently only says
this for scout/plan), or the project's oh-my-openagent config should
not define an agent the dispatcher can't reach. The fall-back rule
applies symmetrically and is fine, this log just records that the same
rule fired here.

## 2026-07-26 — agent 1's "fix pattern seen elsewhere" claim was wrong

**What the agent said:** "The fix pattern seen elsewhere (e.g.,
`/api/providers/{name}` at line 817-841) wraps the insert in a
try/except block, but that pattern is missing from `/api/register`."

**What was actually true:** I read `app.py:816-841` directly. The
ProviderConfig PUT route does NOT wrap the insert in a try/except. It
has the same race-prone SELECT-then-INSERT shape as the register route.
The agent hallucinated a precedent that doesn't exist.

**Fix taken:** Ignored the suggested pattern (it doesn't exist), kept
the fix focused on `/api/register` per the issue's scope. The
ProviderConfig PUT route and the VoiceProfile enrollment flow in
`services/voice_id.py:enroll_voice` are out-of-scope siblings with the
same shape — documented in `investigation.md` and called out in the
final report; not fixed in this PR to keep the A/B comparison clean.

**Recommended agent prompt tweak:** the explore agent's job is "map
the surface, don't propose fixes." When the agent did propose a fix
shape, it was wrong. Worth reinforcing the read-only scope in the
prompt if a future runner sees the same drift.

## 2026-07-26 — issue body line numbers are stale (recurring pattern, noted in prompt)

**What the issue claims:** `app.py:348-353`.

**What was actually true on `master` (HEAD `58906b9`):** the register
route is at `app.py:403-422`, with the duplicate check at
`app.py:414-415` and the `create_user` call at `app.py:419`. The shape
of the bug matches; the line numbers drifted.

**Fix taken:** Verified by direct read of the file. Implemented against
the actual current line numbers, not the issue's stale ones. Noted in
`investigation.md` so the diff self-documents the discrepancy.

**This is a recurring pattern the prompt already warns about** — issue
bodies in this tracker have stale line numbers. The agent reports and
my own direct reads converged on the same actual location, so the fix
landed on the right code.
