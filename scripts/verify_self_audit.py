#!/usr/bin/env python3
"""Mechanically verify a self-audit.md before it's trusted.

Six deterministic checks, no model calls:

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

3. Mutation-check evidence: every `[x] ... mutation check` box must show a
   runner invocation, a pass count, a failure count, and the line the runner
   printed when it failed, so the box records what was observed instead of
   what was predicted. Four runs in one week shipped a mutation claim that had
   never been applied, and a fifth used `mutation check: N/A` on a test that
   failed unconditionally and had only ever been syntax-checked. Exemptions
   are rejected outright. The failure line is required because counts alone
   proved forgeable: PR #353 reported `1 failed` for a test that passes with
   or without the code under it.

4. Main-checkout hygiene: the main checkout must be on master with nothing
   but `.omo/runs/` artifacts modified. The prompts already require the clean
   half; the branch half is new, because a session ran `git checkout <branch>`
   in the main checkout twice, and run artifacts are written there, so a
   wrong-branch main checkout files reports against a tree nobody audited.

5. Independent review disclosure: self-audit.md must carry one
   `Independent review:` line saying either that an Oracle pass ran, or that
   this runner has no in-run independent pass and review happens via
   /audit-pr. One PR shipped with no independent review of any kind and never
   disclosed it; the prompts' fallback language only ever covered a call that
   failed, never one that was skipped. Where an Oracle pass IS claimed, the
   sibling token-usage.md has to name it, because four runs recorded an Oracle
   verdict in self-audit.md while omitting the single largest paid per-run
   cost from their agent table.

6. Six-check evidence: the six add-on checks the prompts require (value-space
   exhaustiveness, boundary cardinality, delivery chain, done == total,
   deferrals matched against the issue text, suite count tied to its
   invocation) must each rest on a file:line or a command, not on prose.
   Check 2 skips any line without a citation, so these were the one part of
   the checklist nothing mechanical could reach: issue #346 shipped two of
   them written from reasoning, and an independent reviewer blocked both as
   false. Blocking is narrow here, a bare `N/A` with nothing behind it, since
   a check that blocks an honest run just teaches the next one to invent a
   citation.

This does not replace a real review -- keyword overlap is a cheap smoke
test, not a semantic check -- but it costs no tokens, is exact where it can
be exact (file/line existence, build byte-diff), and would have caught the
two most expensive misses from PR #256's audit round without needing any
of the four reviewers who missed them to look harder.

Usage:
    python scripts/verify_self_audit.py PATH/TO/self-audit.md [--repo-root PATH]

This judges a self-audit produced by one of the two issue runners, and check 5
holds it to what those prompts require. Pointed at a self-audit written outside
a runner, it will report the missing `Independent review:` line as blocking,
which is the intended reading rather than an edge case: a self-audit that does
not say whether anything else looked at the work is incomplete.

Exits nonzero if any BLOCKING finding is reported. Advisory findings
(`ADVISORY_PREFIXES`) are printed but do not fail the run.
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
    invoked from.

    Returns `(path, matched_branch)` for the first match, preferring an exact
    one, or `(None, None)`.

    The fallback is substring matching in both directions, which is loose
    enough to resolve the wrong worktree: seven different naming patterns have
    been used for this one workflow, and one report directory was the bare word
    `sisyphus`, which is a substring of every sisyphus branch. Both prompts now
    require the directory name to equal the branch name exactly. Until the
    directories in the wild conform, a fuzzy hit still resolves (flipping to
    exact matching today would turn silent-wrong-worktree into a hard failure
    for most runs), but `main()` reports it, because verifying the wrong
    checkout silently is the worse of the two outcomes."""
    branch_dir = self_audit_path.resolve().parent.name
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if proc.returncode != 0:
        return None, None

    worktree_path = None
    fuzzy = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            worktree_path = line[len("worktree "):].strip()
        elif line.startswith("branch ") and worktree_path:
            branch = line[len("branch "):].strip()
            branch = branch.removeprefix("refs/heads/")
            if branch == branch_dir:
                return Path(worktree_path), branch
            if fuzzy is None and (branch_dir in branch or branch in branch_dir):
                fuzzy = (Path(worktree_path), branch)
    if fuzzy is not None:
        return fuzzy
    return None, None


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


MUTATION_HEADER_RE = re.compile(
    r"^(?:[-*]\s*)?\[x\]\s*(?P<name>.*?)\bmutation check\b", re.IGNORECASE
)
# An exemption is the failure mode this check exists for. Issue #246 wrote
# `mutation check: N/A (e2e browser test, ...)` on a test that failed 100% of
# the time and had never been run, only `node -c` syntax-checked.
MUTATION_EXEMPTION_RE = re.compile(
    r"\b(?:n/a|n\.a\.|not applicable|skipped|can'?t be mutated|"
    r"cannot be mutated|no replaceable function body)\b",
    re.IGNORECASE,
)
MUTATION_RUNNER_RE = re.compile(
    r"\b(?:pytest|node\s+--test|npm\s+(?:run\s+)?test|vitest)\b", re.IGNORECASE
)
# Counts, not adjectives. "fails with the body replaced by return? yes" is a
# prediction; "-> 1 failed" is an observation. Only the latter matches.
MUTATION_GREEN_RE = re.compile(r"\b\d+\s+passed\b", re.IGNORECASE)
MUTATION_RED_RE = re.compile(r"\b\d+\s+(?:failed|error|errors)\b", re.IGNORECASE)
# A count says a test went red. It does not say WHICH check went red, and a
# count is the one part of a transcript that can be written without running
# anything. PR #353's box read `reran -> 1 failed, progress_done was 2` for a
# test that passes with or without the guard, because the assertion it makes
# is restored by a later cleanup path either way. So the mutated run also has
# to show the line the runner printed when it failed: pytest's `E ` prefix, a
# `FAILED <path>::<test>` line, the assert expression, or the exception class.
# None of these is impossible to fabricate. All of them are specific enough
# that a reviewer can check them against the test source, which a bare count
# is not.
MUTATION_FAILURE_LINE_RE = re.compile(
    r"(?m)^\s*E\s+\S"                          # pytest's failure detail prefix
    r"|\bFAILED\s+\S+::\S+"                    # pytest's failure summary line
    r"|\bassert\s+\S+\s*(?:==|!=|<=|>=|<|>|\bis\b|\bin\b|\bnot\b)"  # the assertion
    r"|\b[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)\b",              # or what raised
)


def check_mutation_transcripts(self_audit_path: Path):
    """Every `[x] ... mutation check` box must carry observed output, not a
    predicted outcome.

    Both prompts have required a mutation-check line per new test for a while,
    and the line was reliably written from reasoning rather than from running
    anything. Four runs in one week shipped a box whose stated mutation had
    never been applied: a test asserting inside a loop over a list that was
    empty under the mutation, a fixture that never created the row the branch
    needed, an `assert True` body, and one test that failed unconditionally.

    A prediction and an observation are not distinguishable by reading the
    claim, so this requires the shape of an observation: a runner invocation,
    a pass count for the unmutated run, and a failure count for the mutated
    one. Counts rather than words, because "fails if replaced by return" is
    exactly the prediction being rejected.

    Counts alone turned out to be forgeable. PR #353 wrote `reran -> 1 failed,
    progress_done was 2` about a test that passes with or without the code
    under it, because a later cleanup path restores the state it asserts on
    either way, and an independent reviewer had to read the source to see it.
    So the mutated run must also show what the runner printed when it failed,
    the assert or the exception, not just how many tests were red. That is
    still forgeable, but it names something a reviewer can check against the
    test in one read.
    """
    findings = []
    lines = self_audit_path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        m = MUTATION_HEADER_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        label = (m.group("name") or "").strip(" -–—:") or lines[i].strip()
        # The transcript lives on the indented continuation lines beneath the
        # box, so the block is the header plus every indented non-blank line
        # that follows it.
        block = [lines[i]]
        j = i + 1
        while j < len(lines) and lines[j].strip() and lines[j][:1].isspace():
            block.append(lines[j])
            j += 1
        text = "\n".join(block)

        if MUTATION_EXEMPTION_RE.search(text):
            findings.append(
                f"MUTATION EXEMPTION: '{label}' claims the mutation check does "
                f"not apply. There is no exemption. A browser test over a "
                f"bundled function is still mutable: remove the line, rebuild, "
                f"re-run, restore, rebuild. Replace this with an actual "
                f"transcript (ran / mutated / restored, each with its result)."
            )
        else:
            missing = []
            if not MUTATION_RUNNER_RE.search(text):
                missing.append("a runner invocation (pytest, node --test, npm test)")
            if not MUTATION_GREEN_RE.search(text):
                missing.append("an unmutated pass count (e.g. `1 passed`)")
            if not MUTATION_RED_RE.search(text):
                missing.append("a mutated failure count (e.g. `1 failed`)")
            if not MUTATION_FAILURE_LINE_RE.search(text):
                missing.append(
                    "the line the runner printed when it failed (the `E assert "
                    "...` detail, a `FAILED <file>::<test>` line, or the "
                    "exception), not just the count"
                )
            if missing:
                findings.append(
                    f"MUTATION CLAIM NOT EVIDENCED: '{label}' states an outcome "
                    f"without showing one. Missing {', and '.join(missing)}. "
                    f"Run the test, apply the mutation, run it again, and paste "
                    f"both observed results under the box."
                )
        i = j
    return findings


ALLOWED_MAIN_DIRTY_PREFIXES = (".omo/runs/",)
ALLOWED_MAIN_DIRTY_FILES = ("scheduled_tasks.lock",)


def check_main_checkout(self_audit_path: Path):
    """The main checkout must be parked on master with nothing but run
    artifacts modified.

    Both prompts already tell a run to confirm `git -C <MAIN> diff --stat`
    shows only `.omo/runs/` files, and that gate has caught a stray edit
    landing in the main checkout. It cannot catch the main checkout sitting on
    the wrong BRANCH, which has now happened twice. The second time, a session
    ran a plain `git checkout <branch>` in the main checkout instead of making
    a worktree; every file the branch predated vanished from disk, including a
    just-merged docs file and the tracked `.omo/issue-runner-prompt.md`, which
    looked exactly like data loss. Nothing was lost either time, but the first
    went unnoticed for two days.

    Run artifacts are written to `<MAIN>/.omo/runs/...`, so a run whose main
    checkout is on some other branch is filing its reports against a tree that
    does not match the code it audited.
    """
    findings = []
    audit_dir = self_audit_path.resolve().parent
    if not audit_dir.exists():
        return findings
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(audit_dir), capture_output=True, text=True, timeout=10,
        )
        if common.returncode != 0:
            return findings
        main_root = Path(common.stdout.strip()).parent

        branch = subprocess.run(
            ["git", "-C", str(main_root), "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        current = branch.stdout.strip() if branch.returncode == 0 else "(detached HEAD)"
        if current != "master":
            findings.append(
                f"MAIN CHECKOUT ON WRONG BRANCH: {main_root} is on '{current}', "
                f"not master. Branch work belongs in a worktree. While the main "
                f"checkout sits elsewhere, files that branch predates look "
                f"deleted and these run artifacts describe a tree that is not "
                f"checked out there. Fix: "
                f"git -C \"{main_root}\" checkout master"
            )

        # -uall matters: plain --porcelain collapses an untracked directory to
        # a single `?? .omo/` entry, so a prefix match on `.omo/runs/` would
        # never fire and every run artifact would read as a stray edit.
        status = subprocess.run(
            ["git", "-C", str(main_root), "status", "--porcelain", "-uall"],
            capture_output=True, text=True, timeout=20,
        )
        if status.returncode == 0:
            stray = []
            for line in status.stdout.splitlines():
                path = line[3:].strip().strip('"')
                if path.startswith(ALLOWED_MAIN_DIRTY_PREFIXES):
                    continue
                if path in ALLOWED_MAIN_DIRTY_FILES:
                    continue
                stray.append(path)
            if stray:
                shown = ", ".join(sorted(stray)[:8])
                more = "" if len(stray) <= 8 else f" (+{len(stray) - 8} more)"
                findings.append(
                    f"MAIN CHECKOUT DIRTY: {main_root} has changes outside "
                    f".omo/runs/: {shown}{more}. A Phase 2 edit that landed in "
                    f"the main checkout instead of the worktree looks exactly "
                    f"like this."
                )
    except (OSError, subprocess.SubprocessError):
        return findings
    return findings


INDEPENDENT_REVIEW_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?independent review:\s*(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)
ORACLE_RE = re.compile(r"\boracle\b|\bphase\s*3\.75\b", re.IGNORECASE)
NO_ORACLE_RE = re.compile(r"\bnone in-run\b", re.IGNORECASE)
AUDIT_PR_RE = re.compile(r"/audit-pr\b", re.IGNORECASE)


def independent_review_statement(text: str):
    """The `Independent review:` line plus its wrapped continuation.

    The sanctioned disclosure is two sentences and runs wrap it, so the token
    that discharges it (`/audit-pr`) routinely lands on the following line.
    Reading only the matched line rejects the exact text the prompt asks for.
    The statement ends at the first blank line or the next checklist box.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = INDEPENDENT_REVIEW_RE.match(line)
        if not m:
            continue
        parts = [m.group("rest")]
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                break
            if re.match(r"^\s*(?:[-*]\s*)?\[[ xX]\]", nxt):
                break
            parts.append(nxt.strip())
        return " ".join(parts)
    return None


def check_independent_review(self_audit_path: Path):
    """Exactly one thing: did anything other than the author look at this, and
    if not, does the file say so.

    Both prompts already handle an Oracle call that FAILED. Neither could
    detect one that was never attempted, and one PR shipped that way: no Oracle
    entry in `token-usage.md`, no Phase 3.75 section in `self-audit.md`, and no
    external verdict either. The skip was never disclosed, so nothing
    downstream could tell it apart from a clean pass.

    The Claude runner genuinely has no in-run independent pass, by design, so
    this cannot simply demand an Oracle verdict. Both prompts now mandate one
    literal `Independent review:` line, and this matches that line rather than
    a paraphrase of it, because a paraphrase is exactly what an absent gate
    looks like.
    """
    findings = []
    text = self_audit_path.read_text(encoding="utf-8")
    stated = independent_review_statement(text)

    if stated is None:
        findings.append(
            "INDEPENDENT REVIEW NOT RECORDED: no `Independent review:` line in "
            "self-audit.md. Write one, and make it true. Either "
            "`Independent review: Oracle (Phase 3.75) - <verdict>, <one line>`, "
            "or, on a runner with no in-run independent pass, "
            "`Independent review: none in-run. <why>; independent review "
            "happens via /audit-pr after the PR is opened.` A PR already "
            "shipped with no second pair of eyes and no disclosure, which is "
            "indistinguishable from a clean one after the fact."
        )
        return findings

    if ORACLE_RE.search(stated):
        return findings
    if NO_ORACLE_RE.search(stated) and AUDIT_PR_RE.search(stated):
        return findings

    findings.append(
        f"INDEPENDENT REVIEW UNCLEAR: the `Independent review:` line reads "
        f"'{stated}', which names neither an Oracle/Phase 3.75 pass nor the "
        f"sanctioned `none in-run ... /audit-pr` disclosure. Say which one "
        f"actually happened; an ambiguous line here is worth less than no line."
    )
    return findings


ORACLE_IN_USAGE_RE = re.compile(r"\boracle\b", re.IGNORECASE)
ORCHESTRATOR_IN_USAGE_RE = re.compile(r"\borchestrator\b", re.IGNORECASE)


def check_token_usage(self_audit_path: Path):
    """`token-usage.md` under-reports in one direction, and not at random.

    Its job is delegation transparency. In practice it reports what was
    DELEGATED and treats the orchestrator's own turns as free, so a run whose
    orchestrator did the work inline reports near-zero, which is backwards from
    the cost reality. Two runs reported literally no model spend for work a
    model did.

    Separately, four runs recorded an Oracle verdict in `self-audit.md` while
    leaving Oracle out of the agent table. Oracle is the single largest paid
    per-run cost, so that omission is not random with respect to cost. If the
    self-audit claims the pass, the table has to show it.

    Matching is on the agent name, not the backing model: the config that maps
    `oracle` to a model is swapped often, and a model-name match would rot by
    design.
    """
    findings = []
    usage_path = self_audit_path.resolve().parent / "token-usage.md"
    if not usage_path.exists():
        findings.append(
            "TOKEN USAGE INCOMPLETE (advisory): no token-usage.md beside "
            f"{self_audit_path.name}. It has shipped entirely missing on a real "
            "run. An empty file is an honest nothing-to-report; an absent one "
            "is not a report at all."
        )
        return findings

    usage = usage_path.read_text(encoding="utf-8", errors="replace")
    audit = self_audit_path.read_text(encoding="utf-8")

    stated = independent_review_statement(audit)
    claims_oracle = bool(stated and ORACLE_RE.search(stated))

    if claims_oracle and not ORACLE_IN_USAGE_RE.search(usage):
        findings.append(
            "TOKEN USAGE OMITS ORACLE: self-audit.md records an Oracle pass, "
            "and token-usage.md never mentions it. Oracle is the largest paid "
            "per-run cost here, so leaving it out is the one omission that "
            "matters most. Add its row."
        )

    if not ORCHESTRATOR_IN_USAGE_RE.search(usage):
        findings.append(
            "TOKEN USAGE INCOMPLETE (advisory): token-usage.md has no line for "
            "the orchestrator's own consumption. The orchestrator is a model "
            "and it did some of this work; a table of only the delegated calls "
            "reports near-zero for a run that did everything inline. One line "
            "with an estimate is enough."
        )

    return findings


# The six checks the prompts add on top of the promise list. Matched on the
# label each one is written under, plus the section heading they sit beneath,
# because a run that reworded one label should still be recognized as having
# answered it.
#
# Anchored to the start of the box, because these are labels, not phrases.
# Matching them anywhere in the line claimed a box that read `[x] LSP
# diagnostics -- ... direct pytest and full suite passed` as the suite-count
# check and flagged it for citing nothing, on issue #306's real self-audit. A
# box announces which of the six it is answering in its opening words or it
# is not one of them.
SIX_CHECK_LABELS = (
    ("value-space", re.compile(r"value[- ]space", re.IGNORECASE)),
    ("boundary-cardinality", re.compile(r"boundary cardinalit", re.IGNORECASE)),
    ("delivery-chain", re.compile(r"delivery chain", re.IGNORECASE)),
    ("progress-counters", re.compile(r"done\s*==\s*total|progress counter", re.IGNORECASE)),
    ("deferrals", re.compile(r"(?:every )?deferral", re.IGNORECASE)),
    ("suite-count", re.compile(r"(?:a )?suite count|full (?:test )?suite\b", re.IGNORECASE)),
)
# How far into the box a label may start. Enough to clear the decoration a
# label picks up in practice (`**`, a backtick, a leading `A `), not enough to
# reach a phrase in the middle of a sentence.
SIX_LABEL_LEAD = 12
SIX_CHECK_HEADING_RE = re.compile(r"^#+\s+.*\bsix\b", re.IGNORECASE)
# `N/A` is a legitimate answer for several of these on a given change. It is
# rejected only when nothing checkable stands behind it.
SIX_EXEMPTION_RE = re.compile(
    r"\b(?:n/a|n\.a\.|not applicable|does not apply|doesn'?t apply)\b",
    re.IGNORECASE,
)
# A `path/to/file.ext:123` citation anywhere in the box or its continuations.
SIX_CITATION_RE = re.compile(r"[\w./\\-]+\.\w+:\d+")
# Or the command that establishes the claim. `git diff --stat` showing no
# frontend file is real evidence for a delivery-chain N/A; the sentence "this
# is a backend-only change" is not. The backticks are required, not
# decoration: a bare word match turns prose like "find the other caller" or
# "git history shows" into evidence, and this check exists precisely because
# prose was passing for evidence.
SIX_COMMAND_RE = re.compile(
    r"`[^`]*\b(?:git|grep|rg|ls|find|pytest|npm|node|python3?|gh|esbuild)\b[^`]*`",
    re.IGNORECASE,
)


BOX_LINE_RE = re.compile(r"^(?:[-*]\s*)?\[[x ]\]\s*(?P<body>.+)$", re.IGNORECASE)


def six_check_blocks(text: str):
    """Yield (label, header_line, block_text) for each `[x]` six-check box.

    A box owns its own line plus any indented continuation beneath it, the
    same ownership rule the mutation-transcript check uses. It stops at the
    next box even when that box is itself indented: a self-audit written as
    an indented bullet list (`  - [x] ...`) would otherwise let one box
    swallow the next one's citation and pass on borrowed evidence, which is
    the exact false pass this check exists to close.
    """
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        m = BOX_LINE_RE.match(stripped)
        if not m or not re.match(r"^(?:[-*]\s*)?\[x\]", stripped, re.IGNORECASE):
            continue
        body = m.group("body")
        label = next(
            (
                name
                for name, rx in SIX_CHECK_LABELS
                for hit in [rx.search(body)]
                if hit and hit.start() <= SIX_LABEL_LEAD
            ),
            None,
        )
        if label is None:
            continue
        block = [raw]
        for follow in lines[i + 1:]:
            if not follow.strip():
                block.append(follow)
                continue
            if not follow[:1].isspace() or BOX_LINE_RE.match(follow.strip()):
                break
            block.append(follow)
        yield label, stripped, "\n".join(block)


def check_six_checks(self_audit_path: Path):
    """The six add-on checks must carry evidence, not reasoning.

    Every other `[x]` on this checklist points at something: a file:line, a
    test name, a mutation transcript. The six checks the prompts bolted on
    later were answerable in prose, and issue #346 shipped two that a paid
    reviewer then blocked as false, one of them a concrete wrong claim about
    cancellation progress state. Neither was reachable by `check_citations`,
    because a line with no citation is a line that check skips entirely.

    So: a `file:line`, or the command that establishes the claim. Blocking is
    kept narrow on purpose. An `N/A` with nothing behind it is rejected,
    because that is the shape both #346 misses had. Anything else missing
    evidence is advisory, since a checker that blocks an honest run teaches
    the next run to manufacture a citation, which is worse than the prose it
    replaced.
    """
    findings = []
    text = self_audit_path.read_text(encoding="utf-8", errors="replace")
    blocks = list(six_check_blocks(text))

    if not blocks:
        has_heading = any(SIX_CHECK_HEADING_RE.match(l) for l in text.splitlines())
        findings.append(
            "SIX-CHECK BLOCK MISSING (advisory): no `[x]` line matches any of "
            "the six add-on checks (value-space exhaustiveness, boundary "
            "cardinality, delivery chain, done == total, deferrals matched "
            "against the issue text, suite count tied to its invocation)."
            + (
                " A section heading for them is present, so they were probably "
                "answered under wording this check does not recognize."
                if has_heading
                else " The section appears to be absent entirely."
            )
        )
        return findings

    for label, header, block in blocks:
        if SIX_CITATION_RE.search(block) or SIX_COMMAND_RE.search(block):
            continue
        shown = header if len(header) <= 120 else header[:117] + "..."
        if SIX_EXEMPTION_RE.search(block):
            findings.append(
                f"SIX-CHECK WITHOUT EVIDENCE: the {label} box is answered N/A "
                f"with no file:line and no command behind it -- '{shown}'. N/A "
                "is a fine answer; it still needs what makes it true (a "
                "`git diff --stat` with no frontend file in it, a grep that "
                "returns nothing, the citation showing the single code path). "
                "Two boxes of exactly this shape were blocked as false by a "
                "reviewer on issue #346."
            )
        else:
            findings.append(
                f"SIX-CHECK WITHOUT CITATION (advisory): the {label} box cites "
                f"nothing checkable -- '{shown}'. Add the file:line it rests on "
                "or the command you ran, so a later reader can confirm it "
                "without re-deriving your reasoning."
            )

    return findings


ADVISORY_PREFIXES = (
    "WEAK CITATION",
    "TOKEN USAGE INCOMPLETE",
    "WORKTREE NAME MISMATCH",
    "SIX-CHECK WITHOUT CITATION",
    "SIX-CHECK BLOCK MISSING",
)


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

    findings = []
    branch_dir = args.self_audit_path.resolve().parent.name
    if args.repo_root is None:
        detected, matched = find_worktree_for_branch_dir(args.self_audit_path)
        if detected is not None:
            print(f"Auto-detected repo root: {detected}")
            args.repo_root = detected
            if matched != branch_dir:
                findings.append(
                    f"WORKTREE NAME MISMATCH (advisory): the report directory "
                    f"'{branch_dir}' does not equal the branch it resolved to, "
                    f"'{matched}'. It matched on a substring, which can resolve "
                    f"to the wrong checkout and verify code this run never "
                    f"touched. Name the report directory after the branch, "
                    f"exactly."
                )
        else:
            print(f"No matching worktree found, defaulting repo root to CWD: {Path.cwd()}")
            args.repo_root = Path.cwd()

    if not args.skip_build_check:
        findings += check_build_freshness(args.repo_root)
    findings += check_citations(args.repo_root, args.self_audit_path)
    findings += check_mutation_transcripts(args.self_audit_path)
    findings += check_main_checkout(args.self_audit_path)
    findings += check_independent_review(args.self_audit_path)
    findings += check_token_usage(args.self_audit_path)
    findings += check_six_checks(args.self_audit_path)

    if not findings:
        print("OK: no stale builds, no suspect line citations, "
              "every mutation check evidenced, main checkout on master "
              "and clean, independent review recorded, six add-on checks "
              "evidenced.")
        return 0

    blocking = [f for f in findings if not f.startswith(ADVISORY_PREFIXES)]
    advisory = [f for f in findings if f.startswith(ADVISORY_PREFIXES)]

    print(f"{len(blocking)} blocking finding(s), {len(advisory)} advisory:\n")
    for f in blocking + advisory:
        print(f"- {f}\n")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
