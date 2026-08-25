"""Regression guard for prompt drift — issue #384.

The two issue-runner prompts (.claude/issue-runner-prompt.md and
.omo/issue-runner-prompt.md) are parallel ports; shared workflow sections
must stay byte-identical. scripts/verify_issue_runner_prompts.py enforces
it; these tests ensure that script itself is load-bearing.
"""

from __future__ import annotations

import pathlib
import shutil
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


def test_guard_catches_mutation_precondition():
    """Precondition: Phase 0 extracts cleanly and matches on the current tree."""
    import scripts.verify_issue_runner_prompts as mod

    claude_path = REPO_ROOT / ".claude" / "issue-runner-prompt.md"
    omo_path = REPO_ROOT / ".omo" / "issue-runner-prompt.md"
    claude_text = claude_path.read_text(encoding="utf-8")
    omo_text = omo_path.read_text(encoding="utf-8")

    claude_p0 = mod.extract_phase0_body(claude_text)
    omo_p0 = mod.extract_phase0_body(omo_text)
    assert claude_p0 is not None and omo_p0 is not None, "Phase 0 extraction failed"
    assert claude_p0 == omo_p0, "Precondition: Phase 0 should currently match (fix in this PR)"
    assert "comments" in claude_p0, "Precondition: mutation target word must be inside Phase 0"


def test_guard_catches_mutation_via_cli(tmp_path):
    """End-to-end: a mutated Phase 0 on disk must make the script exit 1 with a diff.

    Runs the actual script through subprocess against a throwaway repo layout,
    so a regression in check_shared_sections()'s dispatch (e.g. the fallback
    branch silently returning no errors) would be caught here even though it
    would not be caught by comparing strings in memory.
    """
    import scripts.verify_issue_runner_prompts as mod

    claude_path = REPO_ROOT / ".claude" / "issue-runner-prompt.md"
    omo_path = REPO_ROOT / ".omo" / "issue-runner-prompt.md"
    claude_text = claude_path.read_text(encoding="utf-8")
    omo_text = omo_path.read_text(encoding="utf-8")

    claude_p0 = mod.extract_phase0_body(claude_text)
    assert claude_p0 is not None

    # Mutate only inside the extracted Phase 0 region, then splice back —
    # mutating the full-file text directly could land the change in a
    # tool-specific section instead, in which case the guard would (correctly)
    # still pass and this test would fail for the wrong reason.
    mutated_p0 = claude_p0.replace("comments", "comment", 1)
    assert mutated_p0 != claude_p0, "Mutation did not change the Phase 0 body"
    mutated_claude_text = claude_text.replace(claude_p0, mutated_p0, 1)

    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    (fake_repo / ".claude").mkdir()
    (fake_repo / ".omo").mkdir()
    shutil.copy(SCRIPT, fake_repo / "scripts" / "verify_issue_runner_prompts.py")
    (fake_repo / ".claude" / "issue-runner-prompt.md").write_text(mutated_claude_text, encoding="utf-8")
    (fake_repo / ".omo" / "issue-runner-prompt.md").write_text(omo_text, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(fake_repo / "scripts" / "verify_issue_runner_prompts.py")],
        capture_output=True,
        text=True,
        cwd=str(fake_repo),
    )

    assert result.returncode == 1, (
        f"Guard did not fail on a mutated Phase 0:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Phase 0 differs" in result.stderr, (
        f"Guard failed but not with the expected diff message:\nstderr: {result.stderr}"
    )

