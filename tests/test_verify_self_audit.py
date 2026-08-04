"""scripts/verify_self_audit.py: the build-freshness check's two environment
assumptions, both of which were wrong when it ran against a git worktree.
"""
import importlib.util
import json
import re
import subprocess
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
