#!/usr/bin/env python3
"""Guard against drift between the two issue-runner prompts.

The two prompts (.claude/issue-runner-prompt.md and .omo/issue-runner-prompt.md)
are parallel ports for different harnesses. Most of their workflow (Phase 0,
Complement Rule, per-claim verdict, testing tiers) must stay identical; only
tool-specific sections (Setup worktree mechanism, Delegation table, Phase 1.5
agent name, Phase 3.75 Oracle) are allowed to diverge.

This script extracts explicitly marked shared sections (delimited by sentinels)
and asserts byte identity. If a future edit changes one side without the other,
this fails with a diff.

Usage:
    python scripts/verify_issue_runner_prompts.py
    python scripts/verify_issue_runner_prompts.py --check-backup  # also warn on stale backup
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CLAUDE_PROMPT = REPO_ROOT / ".claude" / "issue-runner-prompt.md"
OMO_PROMPT = REPO_ROOT / ".omo" / "issue-runner-prompt.md"

# Shared sections are the substrings between these markers inside each file.
# Both files must contain each sentinel pair, and the extracted bodies must be
# byte-identical. Adding a new shared section means adding the same sentinel
# pair to both files — the script then enforces it.
#
# Sentinel syntax: <!-- shared:<name> -->  ...  <!-- /shared:<name> -->
SENTINEL_RE = re.compile(
    r"<!-- shared:(?P<name>[a-z0-9_-]+) -->\n(?P<body>.*?)<!-- /shared:\1 -->",
    re.DOTALL,
)

# Fallback: if no sentinels are present yet, fall back to checking Phase 0
# as the single implicit shared block. This keeps the guard active from day
# one, before sentinels are added to the prompts.
PHASE0_RE = re.compile(
    r"(## Phase 0: resolve the real target issue\n)(.*?)(\n## Setup)",
    re.DOTALL,
)


def extract_sentinel_sections(text: str) -> dict[str, str]:
    return {m.group("name"): m.group("body") for m in SENTINEL_RE.finditer(text)}


def extract_phase0_body(text: str) -> str | None:
    m = PHASE0_RE.search(text)
    if not m:
        return None
    # Phase 0 is header + body up to Setup; include the header line so
    # heading-level drift is also caught.
    return m.group(1) + m.group(2)


def check_shared_sections() -> list[str]:
    errors: list[str] = []

    if not CLAUDE_PROMPT.exists():
        errors.append(f"Missing: {CLAUDE_PROMPT}")
        return errors
    if not OMO_PROMPT.exists():
        errors.append(f"Missing: {OMO_PROMPT}")
        return errors

    claude_text = CLAUDE_PROMPT.read_text(encoding="utf-8")
    omo_text = OMO_PROMPT.read_text(encoding="utf-8")

    claude_sentinel = extract_sentinel_sections(claude_text)
    omo_sentinel = extract_sentinel_sections(omo_text)

    if claude_sentinel or omo_sentinel:
        # Sentinel mode: both files must have exactly the same set of shared blocks.
        all_names = sorted(set(claude_sentinel) | set(omo_sentinel))
        for name in all_names:
            if name not in claude_sentinel:
                errors.append(f"Shared section '{name}' present in OMO prompt but missing in Claude prompt")
                continue
            if name not in omo_sentinel:
                errors.append(f"Shared section '{name}' present in Claude prompt but missing in OMO prompt")
                continue
            if claude_sentinel[name] != omo_sentinel[name]:
                diff = difflib.unified_diff(
                    claude_sentinel[name].splitlines(keepends=True),
                    omo_sentinel[name].splitlines(keepends=True),
                    fromfile=f".claude/issue-runner-prompt.md:shared:{name}",
                    tofile=f".omo/issue-runner-prompt.md:shared:{name}",
                )
                errors.append(
                    f"Shared section '{name}' differs between prompts:\n"
                    + "".join(diff).rstrip()
                )
        return errors

    # No sentinels yet — fall back to implicit Phase 0 check.
    claude_p0 = extract_phase0_body(claude_text)
    omo_p0 = extract_phase0_body(omo_text)
    if claude_p0 is None:
        errors.append("Could not extract Phase 0 from Claude prompt")
    if omo_p0 is None:
        errors.append("Could not extract Phase 0 from OMO prompt")
    if claude_p0 is not None and omo_p0 is not None and claude_p0 != omo_p0:
        diff = difflib.unified_diff(
            claude_p0.splitlines(keepends=True),
            omo_p0.splitlines(keepends=True),
            fromfile=".claude/issue-runner-prompt.md#Phase 0",
            tofile=".omo/issue-runner-prompt.md#Phase 0",
        )
        errors.append("Phase 0 differs between prompts (shared workflow):\n" + "".join(diff).rstrip())
    return errors


def check_stale_backup() -> list[str]:
    # Machine-local backup that Nothing reads. Issue #384 says delete or refresh.
    # We only warn here; the file is outside the repo so a CI failure would be
    # unfixable via PR. The warning surfaces the drift without blocking.
    candidates = [
        pathlib.Path.home() / ".config" / "opencode" / "prompts" / "whisperdesk-issue-runner-prompt.backup.md",
    ]
    warnings: list[str] = []
    for p in candidates:
        if p.exists():
            warnings.append(f"Stale backup still exists: {p} — delete it per #384 (nothing reads it)")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-backup", action="store_true", help="also warn about stale backup file")
    parser.add_argument("--strict-backup", action="store_true", help="treat backup warning as error")
    args = parser.parse_args()

    errors = check_shared_sections()
    warnings: list[str] = []
    if args.check_backup or args.strict_backup:
        warnings = check_stale_backup()

    for w in warnings:
        print(f"WARN: {w}", file=sys.stderr)

    if warnings and args.strict_backup:
        errors.extend(warnings)

    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    if warnings:
        print("PASS (with warnings — stale backup present but shared sections match)")
    else:
        print("PASS: shared sections match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
