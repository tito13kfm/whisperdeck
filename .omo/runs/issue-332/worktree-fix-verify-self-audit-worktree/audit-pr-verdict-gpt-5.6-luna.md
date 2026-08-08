## PR Audit: #332 fix(tooling): stop verify_self_audit.py reporting false build findings   (reviewer: GPT-5.6 Luna, independent third family)

VERDICT: APPROVE

### Blocking            (empty = none)
- None.

### Should fix
- [robustness] `scripts/verify_self_audit.py:149-156` passes a Windows temporary output path into a `shell=True` command without quoting it. Failure scenario: when `%TEMP%` contains a space, such as `C:\Users\Jane Doe\AppData\Local\Temp`, esbuild receives a truncated `--outfile` value and the freshness check reports a rebuild failure even for a valid repository. Fix: quote the generated path, or avoid shell parsing by constructing an argv command.

### Nits
- The new tests cover the two extracted helpers but do not exercise `check_build_freshness()` end to end with a fake esbuild command.
- Static scan found existing `asyncio.run()` calls in documentation/tests and `services/cost.py:96`; the changed checker code neither adds nor reaches an async request path, and `services/cost.py` explicitly guards against a running loop.

### Honesty check
- self-audit.md [x] lines verified: 0/0. No self-report artifacts were present for PR branch `worktree-fix-verify-self-audit-worktree`; the nearby issue-112 artifacts belong to a different branch and were not used as this PR's self-report.
- Vacuous / loosened tests: none found in the changed tests. The four tests assert concrete path membership, exact list contents, and exact command transformation.
- Undisclosed scope (diff vs claims): none. The diff contains only the checker changes and their focused tests.

### Read scope
- Full read of `scripts/verify_self_audit.py` and `tests/test_verify_self_audit.py`.

### Summary
The two reported worktree failures are fixed: the checker now exposes the main checkout's binaries and rebuilds under the artifact's basename, preserving source-map references. The touched test file passes with 4 tests, and the full worktree suite passes with 830 passed and 22 deselected. The remaining path-quoting issue is environment-specific robustness, not a defect in the normal checkout used here.
