"""scripts/verify_self_audit.py: the build-freshness check's two environment
assumptions, both of which were wrong when it ran against a git worktree.
"""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load():
    path = REPO_ROOT / "scripts" / "verify_self_audit.py"
    spec = importlib.util.spec_from_file_location("verify_self_audit", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


verify_self_audit = _load()


def test_rebuild_command_keeps_the_output_basename(tmp_path):
    """esbuild writes `//# sourceMappingURL=<outfile basename>.map` into the
    bundle, so rebuilding `rack.min.js` under a temp *filename* changes those
    bytes and reports every --sourcemap bundle as stale however fresh it is.
    The rebuild has to land in a temp directory under the real basename.
    """
    cmd = "esbuild static/rack.js --bundle --minify --sourcemap --outfile=static/rack.min.js"
    full_cmd, tmp_out = verify_self_audit.rebuild_command(
        cmd, "static/rack.min.js", str(tmp_path))

    assert Path(tmp_out).name == "rack.min.js"
    assert f'--outfile="{tmp_out}"' in full_cmd
    # The original outfile must be gone, or the check would overwrite the
    # committed artifact it is supposed to be comparing against.
    assert "--outfile=static/rack.min.js" not in full_cmd
    # Everything else about the script is preserved, flags included.
    assert "--sourcemap" in full_cmd
    assert "--bundle" in full_cmd
    assert full_cmd.startswith("esbuild static/rack.js ")
    assert Path(tmp_out).parent == tmp_path


def test_rebuild_command_quotes_a_temp_path_containing_spaces(tmp_path):
    """The temp dir comes from the OS, and %TEMP% contains a space whenever the
    Windows username does. Unquoted, the shell splits the path and esbuild fails
    with an unrelated error that reads like a build problem.
    """
    spaced = tmp_path / "dir with space"
    spaced.mkdir()
    full_cmd, tmp_out = verify_self_audit.rebuild_command(
        "esbuild static/rack.js --bundle --outfile=static/rack.min.js",
        "static/rack.min.js", str(spaced))

    assert " " in tmp_out
    assert f'--outfile="{tmp_out}"' in full_cmd
    # The bare path must not appear unquoted anywhere.
    assert f"--outfile={tmp_out} " not in full_cmd + " "


def test_rebuild_command_writes_outside_the_repo(tmp_path):
    """Sanity guard on the above: the rebuilt path must be under the temp dir,
    never a repo-relative path that could clobber a committed bundle.
    """
    _, tmp_out = verify_self_audit.rebuild_command(
        "esbuild a.css --minify --outfile=static/rack.min.css",
        "static/rack.min.css", str(tmp_path))
    assert Path(tmp_out).is_absolute()
    assert tmp_path in Path(tmp_out).parents


def _bundle_bytes(outfile):
    """What a --sourcemap build emits: the sourceMappingURL follows the outfile's
    own name. This is the behavior that made a temp-filename rebuild look stale.
    """
    return b"minified;\n//# sourceMappingURL=" + Path(outfile).name.encode() + b".map\n"


def _fake_esbuild_repo(tmp_path, committed_bytes):
    """A repo whose only build script is esbuild-shaped, with src and artifact."""
    repo = tmp_path / "repo"
    (repo / "static").mkdir(parents=True)
    (repo / "static" / "rack.js").write_bytes(b"source")
    (repo / "static" / "rack.min.js").write_bytes(committed_bytes)
    (repo / "package.json").write_text(json.dumps({"scripts": {
        "build:js": "esbuild static/rack.js --bundle --minify --sourcemap --outfile=static/rack.min.js",
    }}), encoding="utf-8")
    return repo


def _stub_run(monkeypatch, rebuilt_for, returncode=0, stderr=""):
    """Stand in for esbuild. List-form calls (node_bin_dirs' git probe) fail so
    PATH discovery is inert; the shell-form build call writes rebuilt_for(out).
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list):
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        captured["cmd"] = cmd
        out = re.search(r'--outfile="([^"]+)"', cmd).group(1)
        captured["outfile"] = out
        if returncode == 0:
            Path(out).write_bytes(rebuilt_for(out))
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(verify_self_audit.subprocess, "run", fake_run)
    return captured


def test_check_build_freshness_passes_a_current_sourcemap_bundle(tmp_path, monkeypatch):
    """End-to-end regression test for the false positive: the stand-in build
    emits a sourceMappingURL derived from the outfile name, exactly as esbuild
    does, so a rebuild into a temp *filename* would differ from the committed
    bundle and be reported stale even though nothing changed.
    """
    repo = _fake_esbuild_repo(tmp_path, _bundle_bytes("static/rack.min.js"))
    captured = _stub_run(monkeypatch, _bundle_bytes)

    assert verify_self_audit.check_build_freshness(repo) == []
    assert Path(captured["outfile"]).name == "rack.min.js"
    # Built somewhere else, so the committed artifact is never overwritten by
    # the very check that is supposed to be comparing against it.
    committed = repo / "static" / "rack.min.js"
    assert Path(captured["outfile"]) != committed
    assert repo not in Path(captured["outfile"]).parents
    assert committed.read_bytes() == _bundle_bytes("static/rack.min.js")


def test_check_build_freshness_still_reports_a_genuinely_stale_bundle(tmp_path, monkeypatch):
    """The other direction: the check has to keep catching what it exists for
    (PR #256's bundle that was never rebuilt), not just stop crying wolf.
    """
    repo = _fake_esbuild_repo(tmp_path, b"stale;\n//# sourceMappingURL=rack.min.js.map\n")
    _stub_run(monkeypatch, _bundle_bytes)

    findings = verify_self_audit.check_build_freshness(repo)

    assert len(findings) == 1
    assert findings[0].startswith("STALE BUILD [build:js]")
    assert "static/rack.min.js" in findings[0]


def test_check_build_freshness_reports_a_failed_rebuild(tmp_path, monkeypatch):
    repo = _fake_esbuild_repo(tmp_path, _bundle_bytes("static/rack.min.js"))
    _stub_run(monkeypatch, _bundle_bytes, returncode=1, stderr="'esbuild' is not recognized")

    findings = verify_self_audit.check_build_freshness(repo)

    assert len(findings) == 1
    assert findings[0].startswith("BUILD [build:js]: rebuild failed (1)")
    assert "not recognized" in findings[0]


def test_node_bin_dirs_finds_the_main_checkout_from_a_worktree(tmp_path):
    """A git worktree has no node_modules of its own (gitignored), so looking
    only under the detected repo root made every build check fail with
    "'esbuild' is not recognized". The main checkout is the parent of the
    common git dir, and its node_modules/.bin has to be offered too.
    """
    main = tmp_path / "main"
    (main / "node_modules" / ".bin").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(main), check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=str(main), check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=str(main), check=True)
    (main / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=str(main), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(main), check=True)

    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", "wtbranch"],
                   cwd=str(main), check=True)
    assert not (wt / "node_modules").exists()

    dirs = verify_self_audit.node_bin_dirs(wt)

    expected = str((main / "node_modules" / ".bin").resolve())
    assert expected in dirs, f"main checkout .bin missing from {dirs}"


def test_node_bin_dirs_skips_dirs_that_do_not_exist(tmp_path):
    """Only real directories go on PATH, and no duplicates: a plain checkout
    whose git-common-dir parent IS the repo root must not list it twice.
    """
    repo = tmp_path / "repo"
    (repo / "node_modules" / ".bin").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)

    dirs = verify_self_audit.node_bin_dirs(repo)

    assert dirs == [str((repo / "node_modules" / ".bin").resolve())]

    bare = tmp_path / "nonode"
    bare.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(bare), check=True)
    assert verify_self_audit.node_bin_dirs(bare) == []


# --- mutation-check evidence ------------------------------------------------
# A mutation-check box used to be a sentence, and a sentence can be written
# without running anything. These cover the four real shapes that shipped.

def _audit(tmp_path, body):
    p = tmp_path / "self-audit.md"
    p.write_text(body, encoding="utf-8")
    return p


def test_mutation_transcript_accepts_an_observed_run(tmp_path):
    """The shape both prompts now require: a runner, a green count, a red count."""
    findings = verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
[x] `test_voice_match_skips_cancelled` — mutation check:
    ran: .venv/Scripts/python.exe -m pytest tests/test_voice_match_job.py -q  ->  3 passed
    mutated: `_match_speakers` body -> `return None`; reran  ->  1 failed, 2 passed
    restored: reran  ->  3 passed
"""))
    assert findings == []


def test_mutation_claim_without_evidence_is_blocking(tmp_path):
    """The old format. It asserts the outcome instead of showing it, which is
    exactly how four runs shipped a mutation that had never been applied."""
    findings = verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
[x] test_populate_fts_backfills — mutation check: fails with function body replaced by return (or None/False/0/[] of declared return type)? yes
"""))
    assert len(findings) == 1
    assert findings[0].startswith("MUTATION CLAIM NOT EVIDENCED")
    assert "runner invocation" in findings[0]
    assert "pass count" in findings[0]
    assert "failure count" in findings[0]


def test_mutation_na_exemption_is_rejected(tmp_path):
    """Issue #246's box, verbatim in shape. The test it exempted failed 100% of
    the time and had only ever been `node -c` syntax-checked, so this `N/A` was
    the last thing standing between a permanently-red test and the PR."""
    findings = verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
[x] test_detail_poll_tagging_fingerprint_changes — mutation check: N/A (e2e browser test, not a unit test with replaceable function body)
"""))
    assert len(findings) == 1
    assert findings[0].startswith("MUTATION EXEMPTION")


def test_mutation_green_only_is_blocking(tmp_path):
    """Running the test proves it passes; it does not prove it can fail. A box
    showing only a green run is the vacuous case (#205, #120) still getting
    through."""
    findings = verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
[x] test_thing — mutation check:
    ran: pytest tests/test_thing.py -q  ->  1 passed
"""))
    assert len(findings) == 1
    assert findings[0].startswith("MUTATION CLAIM NOT EVIDENCED")
    assert "failure count" in findings[0]
    # The parts it did supply must not be reported as missing.
    assert "runner invocation" not in findings[0]
    assert "pass count" not in findings[0].split("Missing", 1)[1].split("failure count")[0]


def test_unchecked_mutation_box_is_left_alone(tmp_path):
    """An honest `[ ]` is explicitly allowed to ship. Only `[x]` claims are
    checked, so this must not fire."""
    assert verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
[ ] test_thing — mutation check: NOT delivered, ran out of time
""")) == []


def test_multiple_mutation_boxes_are_scored_independently(tmp_path):
    """Blocks are delimited by indentation, so an evidenced box must not absorb
    the following unevidenced one (or vice versa)."""
    findings = verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
[x] test_good — mutation check:
    ran: pytest tests/a.py -q  ->  2 passed
    mutated: `f` -> `return None`; reran  ->  1 failed
[x] test_bad — mutation check: yes, it fails under mutation
[x] test_also_bad — mutation check: N/A
"""))
    assert len(findings) == 2
    assert findings[0].startswith("MUTATION CLAIM NOT EVIDENCED")
    assert "test_bad" in findings[0]
    assert findings[1].startswith("MUTATION EXEMPTION")
    assert "test_also_bad" in findings[1]


def test_bulleted_mutation_box_is_recognized(tmp_path):
    """Real self-audits write `- [x] ...` as often as `[x] ...`; the bullet must
    not make the box invisible to the check."""
    findings = verify_self_audit.check_mutation_transcripts(_audit(tmp_path, """
- [x] test_thing — mutation check: fails when the body returns None? yes
"""))
    assert len(findings) == 1
    assert findings[0].startswith("MUTATION CLAIM NOT EVIDENCED")


# --- main-checkout hygiene ---------------------------------------------------
# Run artifacts are written to <MAIN>/.omo/runs/, so the main checkout being on
# the wrong branch means the reports describe a tree that is not checked out
# there. Throwaway repos, so these do not depend on this machine's state.

def _repo_with_audit(tmp_path, branch="master", extra_file=None):
    """A repo shaped like the main checkout, with a self-audit under
    .omo/runs/issue-1/<branch>/ where a real run would put it."""
    repo = tmp_path / "main"
    audit_dir = repo / ".omo" / "runs" / "issue-1" / "some-branch"
    audit_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "master", "."], cwd=str(repo), check=True)
    (repo / "tracked.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=str(repo), check=True)
    if branch != "master":
        subprocess.run(["git", "checkout", "-q", "-b", branch], cwd=str(repo), check=True)
    if extra_file:
        (repo / extra_file).parent.mkdir(parents=True, exist_ok=True)
        (repo / extra_file).write_text("dirty", encoding="utf-8")
    audit = audit_dir / "self-audit.md"
    audit.write_text("[x] something — delivered\n", encoding="utf-8")
    return repo, audit


def test_main_checkout_on_master_and_clean_passes(tmp_path):
    _, audit = _repo_with_audit(tmp_path)
    assert verify_self_audit.check_main_checkout(audit) == []


def test_main_checkout_on_a_feature_branch_is_blocking(tmp_path):
    """The real incident: a session ran `git checkout worktree-issue-109-...`
    in the main checkout, and every file that branch predated vanished from
    disk. Nothing was lost, but it reads as data loss and went unnoticed for
    two days the first time it happened."""
    _, audit = _repo_with_audit(tmp_path, branch="worktree-issue-109-voiceid-fallback")
    findings = verify_self_audit.check_main_checkout(audit)
    assert len(findings) == 1
    assert findings[0].startswith("MAIN CHECKOUT ON WRONG BRANCH")
    assert "worktree-issue-109-voiceid-fallback" in findings[0]
    assert "checkout master" in findings[0]


def test_run_artifacts_do_not_count_as_a_dirty_main_checkout(tmp_path):
    """A run writes its own reports into <MAIN>/.omo/runs/ while it works, so
    those must not trip the dirty check or the gate would fire on every run."""
    _, audit = _repo_with_audit(tmp_path)
    assert verify_self_audit.check_main_checkout(audit) == []
    # The audit file itself is untracked under .omo/runs/ and is ignored above;
    # add a second artifact to be explicit about the allowance.
    (audit.parent / "token-usage.md").write_text("x", encoding="utf-8")
    assert verify_self_audit.check_main_checkout(audit) == []


def test_stray_edit_in_the_main_checkout_is_blocking(tmp_path):
    """A Phase 2 edit that landed in the main checkout instead of the worktree
    looks exactly like this, and has happened for real."""
    _, audit = _repo_with_audit(tmp_path, extra_file="services/thing.py")
    findings = verify_self_audit.check_main_checkout(audit)
    assert len(findings) == 1
    assert findings[0].startswith("MAIN CHECKOUT DIRTY")
    assert "services/thing.py" in findings[0]


def test_both_problems_are_reported_together(tmp_path):
    _, audit = _repo_with_audit(tmp_path, branch="feature", extra_file="app.py")
    findings = verify_self_audit.check_main_checkout(audit)
    assert len(findings) == 2
    assert any(f.startswith("MAIN CHECKOUT ON WRONG BRANCH") for f in findings)
    assert any(f.startswith("MAIN CHECKOUT DIRTY") for f in findings)


def test_missing_audit_directory_is_not_a_finding(tmp_path):
    """Other checks already report on a missing path; this one must stay silent
    rather than emit a confusing git error from an unusable cwd."""
    assert verify_self_audit.check_main_checkout(tmp_path / "nope" / "self-audit.md") == []


# --- independent review ------------------------------------------------------
# One PR shipped with no Oracle pass, no external verdict, and no disclosure of
# either. The prompts' fallback language only ever covered a call that failed.
# The two accepted shapes are the two runners' real situations: opencode runs
# Oracle in Phase 3.75, Claude Code has no in-run independent pass at all.

def test_recorded_oracle_pass_satisfies_the_gate(tmp_path):
    findings = verify_self_audit.check_independent_review(_audit(tmp_path, """
[x] tests pass
Independent review: Oracle (Phase 3.75) - APPROVE, two non-blocking watch-outs deferred.
"""))
    assert findings == []


def test_claude_runner_disclosure_satisfies_the_gate(tmp_path):
    """The Claude runner has no Phase 3.75 by design, so demanding an Oracle
    verdict would fire on every one of its runs. Its sanctioned disclosure has
    to pass, and it is the reason this check can be blocking at all."""
    findings = verify_self_audit.check_independent_review(_audit(tmp_path, """
Independent review: none in-run. This workflow has no independent-model audit
pass; independent review happens via /audit-pr after the PR is opened.
"""))
    assert findings == []


def test_no_independent_review_line_at_all_is_blocking(tmp_path):
    """issue-285: no Oracle in token-usage.md, no Phase 3.75 in self-audit.md,
    no external verdict. Nothing distinguished it from a clean pass."""
    findings = verify_self_audit.check_independent_review(_audit(tmp_path, """
[x] `_finalize_if_done` guard - delivered, confirmed at services/queue.py:486
[x] full suite - 848 passed
"""))
    assert len(findings) == 1
    assert findings[0].startswith("INDEPENDENT REVIEW NOT RECORDED")


def test_an_evasive_independent_review_line_is_blocking(tmp_path):
    """A line that neither claims a pass nor discloses its absence is worth
    less than no line, because it looks like the gate was honored."""
    findings = verify_self_audit.check_independent_review(_audit(tmp_path, """
Independent review: reviewed the diff carefully myself.
"""))
    assert len(findings) == 1
    assert findings[0].startswith("INDEPENDENT REVIEW UNCLEAR")


def test_none_in_run_without_naming_the_external_route_is_blocking(tmp_path):
    """"None" alone is a skip. The disclosure only discharges the gate because
    it also names where independent review actually happens."""
    findings = verify_self_audit.check_independent_review(_audit(tmp_path, """
Independent review: none in-run.
"""))
    assert len(findings) == 1
    assert findings[0].startswith("INDEPENDENT REVIEW UNCLEAR")


def test_a_checked_box_carrying_the_line_is_recognized(tmp_path):
    """Runs write this either as prose or as a checklist item; both are fine."""
    findings = verify_self_audit.check_independent_review(_audit(tmp_path, """
- [x] Independent review: Oracle verdict APPROVE
"""))
    assert findings == []


# --- token-usage cross-check -------------------------------------------------
# The file reports what was DELEGATED and treats the orchestrator's own turns as
# free, so runs that worked inline reported near-zero. Four more recorded an
# Oracle verdict while leaving the largest paid call out of the table.

def _audit_pair(tmp_path, audit_body, usage_body=None):
    audit = _audit(tmp_path, audit_body)
    if usage_body is not None:
        (tmp_path / "token-usage.md").write_text(usage_body, encoding="utf-8")
    return audit


def test_oracle_claimed_but_absent_from_the_table_is_blocking(tmp_path):
    audit = _audit_pair(
        tmp_path,
        "Independent review: Oracle (Phase 3.75) - APPROVE\n",
        "| Agent | Model |\n|---|---|\n| deep | deepseek-v4-pro |\n"
        "Orchestrator: ~40k tokens.\n",
    )
    findings = verify_self_audit.check_token_usage(audit)
    assert len(findings) == 1
    assert findings[0].startswith("TOKEN USAGE OMITS ORACLE")


def test_oracle_in_the_table_passes(tmp_path):
    """Matched on the agent name, not the backing model: the config that maps
    `oracle` to a model is swapped often, so a model-name match would rot."""
    audit = _audit_pair(
        tmp_path,
        "Independent review: Oracle (Phase 3.75) - APPROVE\n",
        "| oracle | whatever-model-backs-it-today | Cloud |\n"
        "Orchestrator (sisyphus): ~60k tokens.\n",
    )
    assert verify_self_audit.check_token_usage(audit) == []


def test_a_run_with_no_oracle_pass_is_not_asked_for_an_oracle_row(tmp_path):
    audit = _audit_pair(
        tmp_path,
        "Independent review: none in-run; via /audit-pr after the PR.\n",
        "| Explore | Sonnet |\nOrchestrator (Opus): ~90k tokens.\n",
    )
    assert verify_self_audit.check_token_usage(audit) == []


def test_missing_orchestrator_line_is_advisory(tmp_path, monkeypatch, capsys):
    """Two runs reported no model spend at all for work a model did inline.

    Asserted through main() rather than on the finding alone, because
    "advisory" is a claim about the exit code, and the prefix string on its own
    proves nothing about how main() classifies it."""
    audit = _audit_pair(
        tmp_path,
        "Independent review: none in-run; via /audit-pr after the PR.\n",
        "No model calls were made. All work used deterministic tools.\n",
    )
    findings = verify_self_audit.check_token_usage(audit)
    assert len(findings) == 1
    assert findings[0].startswith(verify_self_audit.ADVISORY_PREFIXES)

    monkeypatch.setattr(sys, "argv", [
        "verify_self_audit.py", str(audit),
        "--skip-build-check", "--repo-root", str(tmp_path),
    ])
    assert verify_self_audit.main() == 0
    out = capsys.readouterr().out
    assert "0 blocking finding(s), 1 advisory" in out


def test_a_missing_independent_review_line_makes_main_exit_nonzero(
        tmp_path, monkeypatch, capsys):
    """The other half of the same contract: this one is blocking, so the same
    invocation shape has to come back nonzero."""
    audit = _audit_pair(
        tmp_path,
        "[x] guard added - delivered\n",
        "| deep | some-model |\nOrchestrator: ~10k tokens.\n",
    )
    monkeypatch.setattr(sys, "argv", [
        "verify_self_audit.py", str(audit),
        "--skip-build-check", "--repo-root", str(tmp_path),
    ])
    assert verify_self_audit.main() == 1
    assert "INDEPENDENT REVIEW NOT RECORDED" in capsys.readouterr().out


def test_missing_token_usage_file_is_advisory_not_blocking(tmp_path):
    """It has shipped entirely missing on a real run, but the four-file gate is
    a separate step and the checker runs before it."""
    audit = _audit_pair(tmp_path, "Independent review: Oracle - APPROVE\n")
    findings = verify_self_audit.check_token_usage(audit)
    assert len(findings) == 1
    assert findings[0].startswith("TOKEN USAGE INCOMPLETE")


# --- worktree resolution -----------------------------------------------------
# Substring matching in both directions can resolve the wrong checkout. Seven
# naming patterns are in the wild, so this warns rather than failing: flipping
# to exact matching today would hard-fail most runs.

def _worktree_repo(tmp_path, branch, report_dir):
    repo = tmp_path / "main"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master", "."], cwd=str(repo), check=True)
    (repo / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "f.txt"], cwd=str(repo), check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        cwd=str(repo), check=True)
    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", branch, str(wt)],
        cwd=str(repo), check=True)
    audit_dir = repo / ".omo" / "runs" / "issue-1" / report_dir
    audit_dir.mkdir(parents=True)
    audit = audit_dir / "self-audit.md"
    audit.write_text("[x] x\n", encoding="utf-8")
    return repo, wt, audit


def test_exact_branch_name_resolves_with_no_warning(tmp_path, monkeypatch):
    repo, wt, audit = _worktree_repo(tmp_path, "issue-42-thing", "issue-42-thing")
    monkeypatch.chdir(repo)
    path, matched = verify_self_audit.find_worktree_for_branch_dir(audit)
    assert path == wt.resolve() or Path(path).resolve() == wt.resolve()
    assert matched == "issue-42-thing"


def test_a_substring_report_directory_still_resolves_but_reports_the_branch(
        tmp_path, monkeypatch):
    """The real shape: a report directory named `sisyphus` substring-matches
    every branch containing it, so the checker can verify the wrong code. It
    still resolves, and main() turns the name difference into an advisory."""
    repo, wt, audit = _worktree_repo(tmp_path, "issue-261-sisyphus", "sisyphus")
    monkeypatch.chdir(repo)
    path, matched = verify_self_audit.find_worktree_for_branch_dir(audit)
    assert Path(path).resolve() == wt.resolve()
    assert matched == "issue-261-sisyphus"
    assert matched != audit.resolve().parent.name


def test_no_matching_worktree_returns_a_pair_of_nones(tmp_path, monkeypatch):
    """The caller unpacks two values unconditionally, so the miss path has to
    keep the same shape."""
    repo, _wt, audit = _worktree_repo(tmp_path, "issue-42-thing", "totally-unrelated")
    monkeypatch.chdir(repo)
    assert verify_self_audit.find_worktree_for_branch_dir(audit) == (None, None)
