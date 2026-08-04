"""scripts/verify_self_audit.py: the build-freshness check's two environment
assumptions, both of which were wrong when it ran against a git worktree.
"""
import importlib.util
import subprocess
from pathlib import Path

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
    assert f"--outfile={tmp_out}" in full_cmd
    # The original outfile must be gone, or the check would overwrite the
    # committed artifact it is supposed to be comparing against.
    assert "--outfile=static/rack.min.js" not in full_cmd
    # Everything else about the script is preserved, flags included.
    assert "--sourcemap" in full_cmd
    assert "--bundle" in full_cmd
    assert full_cmd.startswith("esbuild static/rack.js ")
    assert Path(tmp_out).parent == tmp_path


def test_rebuild_command_writes_outside_the_repo(tmp_path):
    """Sanity guard on the above: the rebuilt path must be under the temp dir,
    never a repo-relative path that could clobber a committed bundle.
    """
    _, tmp_out = verify_self_audit.rebuild_command(
        "esbuild a.css --minify --outfile=static/rack.min.css",
        "static/rack.min.css", str(tmp_path))
    assert Path(tmp_out).is_absolute()
    assert tmp_path in Path(tmp_out).parents


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
