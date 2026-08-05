"""scripts/ must not contain anything pytest will try to collect.

The scripts in there are manual probes against a live Lemonade server, and they
do their work at module top level, so merely importing one fires HTTP requests.
Four of them were named `test_*.py`:

- `test_correction_models.py` and `test_model_params.py` each defined
  `def test(model, label, extra_params=None)`, so pytest collected `test` as a
  test case and errored with `fixture 'model' not found`.
- `test_qwen_mtp.py` and `test_reasoning_models.py` had no test-shaped function,
  so they imported "successfully" and reported nothing -- while making real
  network calls during collection and blocking ~40s each on timeouts.

`testpaths = tests` hid all of this from a bare `pytest`, so it only surfaced
when someone ran `pytest .` or pointed pytest at the repo root, which is
exactly what a run verifying "the full suite" is likely to do.

`pytest.ini` now sets `norecursedirs = scripts`, and the files are `probe_*.py`.
This test is the third layer: it fails if a collectible filename reappears
there, because the two config guards are easy to not notice and easy to undo.
"""
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"


def test_scripts_dir_has_no_pytest_collectible_filenames():
    offenders = sorted(
        p.name
        for p in SCRIPTS_DIR.glob("*.py")
        if p.name.startswith("test_") or p.name.endswith("_test.py")
    )
    assert offenders == [], (
        f"{SCRIPTS_DIR.name}/ contains files pytest will collect if it ever "
        f"walks this directory: {offenders}. These scripts run network calls at "
        f"import time, so collection alone has side effects and can hang. Name "
        f"manual probes probe_<thing>.py, and put real tests in tests/."
    )


def test_the_probes_are_still_there_under_their_new_names():
    """Guard against 'fixing' the check above by deleting the probes. They are
    working diagnostic tools for the local model server, not dead code."""
    present = {p.name for p in SCRIPTS_DIR.glob("probe_*.py")}
    expected = {
        "probe_correction_models.py",
        "probe_model_params.py",
        "probe_qwen_mtp.py",
        "probe_reasoning_models.py",
    }
    missing = sorted(expected - present)
    assert not missing, f"probe scripts disappeared rather than being renamed: {missing}"
