"""Defer only reviewer-owned DoD failures to the review/land stages."""

import os
import subprocess
import sys
from pathlib import Path


def run_probe(tmp_path, assertions):
    root = Path(__file__).resolve().parents[2]
    source = root / "bin" / "pr-chain"
    code = f"""
import importlib.machinery
import importlib.util
import pathlib
import sys
import types

sys.dont_write_bytecode = True
source = pathlib.Path({str(source)!r})
loader = importlib.machinery.SourceFileLoader("prchain_review_gate_probe", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
{assertions}
"""
    env = os.environ.copy()
    env["PR_CHAIN_CONFIG"] = str(root / "chain/tests/test-config.toml")
    env["KIT_WORK"] = str(tmp_path / "work")
    env["PR_CHAIN_WT_ROOT"] = str(tmp_path / "worktrees")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_only_current_head_approval_wait_bypasses_fixer(tmp_path):
    assertions = """
head = "d0306c318bf72c9f5065b4ff2ba4ea60bfaa52a2"
pending = "- regression_test: independent current-head approval required for finding id(s): F3; approval must follow the latest finding revision"
warning = "- WARNING regression_test: red-first execution is not verified by this gate for finding id(s): F3; the independent reviewer must verify the commit order"
dod = {"name": "dod/ready-for-review", "conclusion": "FAILURE", "detailsUrl": "https://github.com/example-org/example-repo/runs/42"}
module.DRY = False
module.check_summary = lambda _url: [pending, warning]
module.gh_json = lambda *a, **k: {"headRefOid": head, "statusCheckRollup": [dod]}
module.main_flake_rerun = lambda *a: False
assert module.later_owned(dod)
assert module.ci_red(1295, head) == []

# A genuine DoD defect keeps the check red.
module.check_summary = lambda _url: [pending, warning, "- regression_test: no changed test text references finding id(s): F3 under tests"]
assert not module.later_owned(dod)
assert [c["name"] for c in module.ci_red(1295, head)] == ["dod/ready-for-review"]

# A real required CI failure remains visible beside an approval wait.
test = {"name": "ci/tests", "conclusion": "FAILURE", "detailsUrl": "https://example.test/run"}
module.check_summary = lambda _url: [pending, warning]
module.gh_json = lambda *a, **k: {"headRefOid": head, "statusCheckRollup": [dod, test]}
assert [c["name"] for c in module.ci_red(1295, head)] == ["ci/tests"]
"""
    run_probe(tmp_path, assertions)


def test_preflight_defers_exact_review_wait_only(tmp_path):
    assertions = f"""
pending = "- regression_test: independent current-head approval required for finding id(s): F3; approval must follow the latest finding revision"
warning = "- WARNING regression_test: red-first execution is not verified by this gate for finding id(s): F3; the independent reviewer must verify the commit order"
module.WORK = pathlib.Path({str(tmp_path / "work")!r})
newline = chr(10)
report = newline.join(["pr-gates DoD: 1 failed item(s):", pending, "Warnings: 1 item(s) need attention:", warning, "Audited finding-reference suppressions:", ""])
def fake_sh(*args, **kwargs):
    if args[0] == "git":
        return types.SimpleNamespace(returncode=0, stdout="stub", stderr="")
    if args[0] == sys.executable:
        return types.SimpleNamespace(returncode=1, stdout=report, stderr="")
    raise AssertionError(args)
module.sh = fake_sh
assert module.gate_findings(1295) == []

# Unknown failures and warnings still block the preflight.
report = report.replace("Audited finding-reference suppressions:", "- WARNING spec_or_fix_brief: missing fix brief" + newline + "Audited finding-reference suppressions:")
assert module.gate_findings(1295) == ["WARNING spec_or_fix_brief: missing fix brief"]
"""
    run_probe(tmp_path, assertions)


if __name__ == "__main__":  # the kit runs each test file as a script; no pytest needed
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
            print("ok   " + name)
