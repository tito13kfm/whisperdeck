# Wrong directions and execution discrepancies

- The workflow requires LSP diagnostics on changed worktree files, but the available `lsp_diagnostics` tool rejected both worktree paths with `LSP file path must be inside request cwd: C:\Claude\whisperdesk\.claude\worktrees\issue-306-sisyphus\...`. Recommendation: allow an explicit worktree cwd or document diagnostics as unavailable in fresh worktrees. Direct targeted, related, and full pytest suites passed.
- `verify_self_audit.py` accepted the report with 0 blocking findings and 1 advisory for the LSP six-check evidence wording. The advisory is retained honestly rather than manufacturing a source citation.

- **Vacuous test (caught by /audit-pr):** v1 used 40 segments (2 batches) and cancelled on the last batch; `correct_transcript` returned "ok", `_finish()` zeroed counters, so the guard removal did not fail the test. Corrected to 60 segments (3 batches, cancel on middle batch). Recommendation: the investigation document should require a "what does _finish do and when does it fire" analysis for any progress-counter test.
