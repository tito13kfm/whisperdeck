#!/usr/bin/env python3
"""Mechanically verify a self-audit.md before it's trusted.

Two deterministic checks, no model calls:

1. Build freshness: for every esbuild `<src> ... --outfile=<out>` script in
   package.json, rebuild <src> to a temp file and diff it byte-for-byte
   against the committed <out>. Catches a source file changing without its
   bundle being regenerated (PR #256: rack.js changed, rack.min.js didn't,
   so the whole feature was dead in the served bundle). mtime comparison
   doesn't work here -- a fresh git checkout stamps every file with the same
   checkout time, so "is the source newer than the build" is meaningless
   without actually rebuilding and comparing content.

2. Line-citation sanity: for every self-audit.md line of the form
   `[x] <description> -- delivered[,] confirmed at <file>:<line>[-<line2>]`,
   pull significant identifier/word tokens out of <description> and check
   whether ANY of them appear within a generous window around the cited
   line(s). Zero overlap means the citation almost certainly points at the
   wrong code (confirmed pattern on PR #256's self-audit: 5+ of 12 citations
   pointed at unrelated code after the file drifted).

This does not replace a real review -- keyword overlap is a cheap smoke
test, not a semantic check -- but it costs no tokens, is exact where it can
be exact (file/line existence, build byte-diff), and would have caught the
two most expensive misses from PR #256's audit round without needing any
of the four reviewers who missed them to look harder.

Usage:
    python scripts/verify_self_audit.py PATH/TO/self-audit.md [--repo-root PATH]

Exits nonzero if any finding is reported.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

STOPWORDS = {
    "the", "a", "an", "and", "or", "in", "on", "at", "to", "of", "for",
    "with", "delivered", "confirmed", "not", "yes", "no", "this", "that",
    "is", "are", "was", "were", "it", "its", "as", "via", "per", "each",
    "own", "new", "add", "adds", "added", "line", "lines", "file", "test",
}


def parse_build_pairs(repo_root: Path):
    """Extract (src, out) pairs from esbuild-style npm scripts in package.json."""
    pkg_path = repo_root / "package.json"
    if not pkg_path.exists():
        return []
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    pairs = []
    for name, cmd in (pkg.get("scripts") or {}).items():
        m = re.search(r"esbuild\s+(\S+).*?--outfile=(\S+)", cmd)
        if m:
            pairs.append((name, cmd, m.group(1), m.group(2)))
    return pairs


def find_worktree_for_branch_dir(self_audit_path: Path):
    """Self-audit files live at .omo/runs/issue-<N>/<branch-name>/self-audit.md.
    That <branch-name> directory is conventionally named after the git branch
    the run is on. Resolve it to the actual worktree path via `git worktree
    list`, so citation/build checks run against the checkout that actually
    has the new files -- regardless of which directory the script itself was
    invoked from. Returns None if no worktree's branch matches."""
    branch_dir = self_audit_path.resolve().parent.name
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    worktree_path = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            worktree_path = line[len("worktree "):].strip()
        elif line.startswith("branch ") and worktree_path:
            branch = line[len("branch "):].strip()
            branch = branch.removeprefix("refs/heads/")
            if branch == branch_dir or branch_dir in branch or branch in branch_dir:
                return Path(worktree_path)
    return None


def node_bin_dirs(repo_root: Path):
    """node_modules/.bin dirs to put on PATH, most-specific first.

    A git worktree has no node_modules of its own (it's gitignored), so a run
    against one would fail every build check with "'esbuild' is not
    recognized" unless the main checkout's binaries are also offered. The main
    checkout is the parent of the common git dir.
    """
    dirs = [repo_root / "node_modules" / ".bin"]
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            common = Path(proc.stdout.strip())
            if not common.is_absolute():
                common = (repo_root / common).resolve()
            dirs.append(common.parent / "node_modules" / ".bin")
    except (OSError, subprocess.SubprocessError):
        pass
    seen, out = set(), []
    for d in dirs:
        r = str(d.resolve()) if d.exists() else None
        if r and r not in seen:
            seen.add(r)
            out.append(r)
    return out


def rebuild_command(cmd: str, out: str, tmp_dir: str):
    """Rewrite an esbuild script to build into tmp_dir, keeping out's basename.

    Returns (full_cmd, tmp_path). The basename must survive: esbuild derives the
    emitted `//# sourceMappingURL=` from the outfile name, so building
    `rack.min.js` as `tmpXXXX.js` changes those bytes and makes every
    --sourcemap bundle look stale regardless of whether it actually is.

    The path is quoted because it comes from the OS temp dir, which can contain
    spaces (a Windows username with a space puts one in %TEMP%), and the command
    runs through a shell. Double quotes rather than shlex.quote: this runs under
    cmd.exe on Windows, which doesn't understand POSIX single-quoting.
    """
    tmp_path = str(Path(tmp_dir) / Path(out).name)
    return cmd.replace(f"--outfile={out}", f'--outfile="{tmp_path}"'), tmp_path


def check_build_freshness(repo_root: Path):
    findings = []
    for script_name, cmd, src, out in parse_build_pairs(repo_root):
        src_path = repo_root / src
        out_path = repo_root / out
        if not src_path.exists():
            findings.append(f"BUILD [{script_name}]: source {src} does not exist")
            continue
        if not out_path.exists():
            findings.append(f"BUILD [{script_name}]: built artifact {out} does not exist")
            continue
        with tempfile.TemporaryDirectory() as tmp_dir:
            full_cmd, tmp_path = rebuild_command(cmd, out, tmp_dir)
            env = dict(os.environ)
            bin_dirs = node_bin_dirs(repo_root)
            if bin_dirs:
                env["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + env.get("PATH", "")
            proc = subprocess.run(
                full_cmd, shell=True, cwd=str(repo_root), env=env,
                capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                findings.append(
                    f"BUILD [{script_name}]: rebuild failed ({proc.returncode}): "
                    f"{proc.stderr.strip()[:300]}"
                )
                continue
            rebuilt_bytes = Path(tmp_path).read_bytes()
            committed_bytes = out_path.read_bytes()
            if rebuilt_bytes != committed_bytes:
                findings.append(
                    f"STALE BUILD [{script_name}]: {out} does not match a fresh "
                    f"build of {src} (sizes: committed={len(committed_bytes)}b, "
                    f"fresh={len(rebuilt_bytes)}b). Run `npm run {script_name}` "
                    f"(or the parent `build` script) and commit the result."
                )
    return findings


CITATION_RE = re.compile(
    r"^\[x\]\s*(?P<desc>.+?)\s*[-–—]{1,2}\s*(?:delivered|NOT delivered)"
    r".*?(?:confirmed )?at\s+(?P<file>[\w./\\-]+\.\w+)"
    r"(?::(?P<line1>\d+)(?:-(?P<line2>\d+))?)?",
    re.IGNORECASE,
)


def extract_tokens(desc: str):
    """Split into STRONG tokens (literal code identifiers -- backticked spans,
    dotted state fields like S.batchFilter, function calls like loadQueue(),
    dashed selectors/attrs like data-bact) and WEAK tokens (plain English
    words). Plain words are unreliable alone in a file where the same
    vocabulary (batch/action/cancel/open) recurs throughout -- a wrong
    citation can still pick up 2-3 coincidental weak matches nearby. A
    strong-token match is a far more specific signal and should be required
    whenever the description actually names an identifier."""
    # Descriptions conventionally lead with a "<filename>: " label (e.g.
    # "rack.js: Batch filter dropdown..."). That label isn't part of the
    # claim's content -- it's metadata -- and its filename would otherwise
    # get picked up as a bogus "strong" dotted identifier that can never
    # actually appear inside that same file's own source.
    desc = re.sub(r"^\s*[\w./\\-]+\.\w+\s*:\s*", "", desc)

    strong, weak = set(), set()

    for m in re.finditer(r"`([^`]+)`", desc):
        strong.add(m.group(1).lower())
    for m in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b", desc):
        strong.add(m.group(0).lower())
    for m in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\s*\(\)", desc):
        strong.add(m.group(0).split("(")[0].lower())
    for m in re.finditer(r"\b[a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z][a-zA-Z0-9]*)+\b", desc):
        strong.add(m.group(0).lower())

    for m in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_./#-]{3,}\b", desc):
        tok = m.group(0).strip(".,:;()[]{}").lower()
        if len(tok) < 4 or tok in STOPWORDS or tok in strong:
            continue
        weak.add(tok)

    return strong, weak


def check_citations(repo_root: Path, self_audit_path: Path, window: int = 15):
    findings = []
    text = self_audit_path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        m = CITATION_RE.match(raw_line.strip())
        if not m or not m.group("line1"):
            continue  # no file:line citation on this line (e.g. test-name-only, or NOT delivered)
        desc = m.group("desc")
        file_rel = m.group("file")
        line1 = int(m.group("line1"))
        line2 = int(m.group("line2")) if m.group("line2") else line1
        file_path = repo_root / file_rel
        if not file_path.exists():
            findings.append(f"CITATION: '{desc}' cites {file_rel} which does not exist")
            continue
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        lo = max(0, line1 - 1 - window)
        hi = min(len(lines), line2 + window)
        if line1 - 1 >= len(lines):
            findings.append(
                f"CITATION: '{desc}' cites {file_rel}:{line1}, past end of file "
                f"({len(lines)} lines)"
            )
            continue
        window_text = "\n".join(lines[lo:hi]).lower()
        strong, weak = extract_tokens(desc)
        strong_hits = [t for t in strong if t in window_text]
        weak_hits = [t for t in weak if t in window_text]
        snippet = "\n".join(lines[max(0, line1 - 1):min(len(lines), line1 + 2)])
        indented = snippet.replace(chr(10), chr(10) + "    ")

        if strong:
            verdict_bad = not strong_hits
            evidence = sorted(strong)[:6]
        else:
            verdict_bad = len(weak_hits) < 2
            evidence = sorted(weak)[:6]

        if verdict_bad:
            findings.append(
                f"SUSPECT CITATION: '{desc}' -> {file_rel}:{line1}"
                f"{'-' + str(line2) if line2 != line1 else ''} -- "
                f"{'no strong identifier' if strong else 'fewer than 2 weak'} "
                f"match(es) among {evidence} within +/-{window} lines. "
                f"Actual content there:\n    {indented}"
            )
        elif not strong:
            findings.append(
                f"WEAK CITATION (low confidence, not blocking): '{desc}' -> "
                f"{file_rel}:{line1}{'-' + str(line2) if line2 != line1 else ''} "
                f"-- only generic words matched ({sorted(weak_hits)}), no literal "
                f"identifier in the description to check against. Consider citing "
                f"a specific function/selector/attribute name instead of prose."
            )
    return findings


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("self_audit_path", type=Path)
    ap.add_argument(
        "--repo-root", type=Path, default=None,
        help="Defaults to auto-detecting the worktree whose branch matches "
             "the self-audit's parent directory name, falling back to CWD "
             "if no match is found.",
    )
    ap.add_argument("--skip-build-check", action="store_true")
    args = ap.parse_args()

    if args.repo_root is None:
        detected = find_worktree_for_branch_dir(args.self_audit_path)
        if detected is not None:
            print(f"Auto-detected repo root: {detected}")
            args.repo_root = detected
        else:
            print(f"No matching worktree found, defaulting repo root to CWD: {Path.cwd()}")
            args.repo_root = Path.cwd()

    findings = []
    if not args.skip_build_check:
        findings += check_build_freshness(args.repo_root)
    findings += check_citations(args.repo_root, args.self_audit_path)

    if not findings:
        print("OK: no stale builds, no suspect line citations.")
        return 0

    blocking = [f for f in findings if not f.startswith("WEAK CITATION")]
    advisory = [f for f in findings if f.startswith("WEAK CITATION")]

    print(f"{len(blocking)} blocking finding(s), {len(advisory)} advisory:\n")
    for f in blocking + advisory:
        print(f"- {f}\n")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
