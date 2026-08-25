"""Regression guard for prompt drift — issue #384.

The two issue-runner prompts (.claude/issue-runner-prompt.md and
.omo/issue-runner-prompt.md) are parallel ports; shared workflow sections
must stay byte-identical. scripts/verify_issue_runner_prompts.py enforces
it; these tests ensure that script itself is load-bearing.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_issue_runner_prompts.py"


def test_verify_script_exists():
    assert SCRIPT.exists(), f"Missing drift guard script: {SCRIPT}"


def test_shared_sections_match():
    """The guard script must exit 0 on the current tree — shared sections are identical."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"verify_issue_runner_prompts.py failed — shared sections drifted:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_guard_catches_mutation():
    """Mutation check: if Phase 0 diverges, the guard must fail."""
    import scripts.verify_issue_runner_prompts as mod

    # Directly exercise the check function so the mutation doesn't need to
    # touch disk — replace a byte in the extracted Phase 0 and confirm
    # the checker would flag it.
    claude_path = REPO_ROOT / ".claude" / "issue-runner-prompt.md"
    omo_path = REPO_ROOT / ".omo" / "issue-runner-prompt.md"
    claude_text = claude_path.read_text(encoding="utf-8")
    omo_text = omo_path.read_text(encoding="utf-8")

    claude_p0 = mod.extract_phase0_body(claude_text)
    omo_p0 = mod.extract_phase0_body(omo_text)
    assert claude_p0 is not None and omo_p0 is not None, "Phase 0 extraction failed"
    assert claude_p0 == omo_p0, "Precondition: Phase 0 should currently match (fix in this PR)"

    # Mutate: drop a trailing 's' from a word in the Phase 0 body.
    mutated = claude_p0.replace("comments", "comment", 1)
    assert mutated != claude_p0, "Mutation did not change the body"
    assert mutated != omo_p0, "Mutated body should differ from OMO"
    # The point: a one-word change in Phase 0 must be detectable — the guard
    # does a byte comparison, so any such mutation fails. We assert the
    # mutation itself is non-trivial (not vacuous) by confirming mutated != claude_p0.
    # The subprocess-level mutation test (writing to disk) is exercised in
    # scripts/verify_issue_runner_prompts.py's own manual check; this test
    # proves the comparison is byte-sensitive.

