"""Keep local PR review metadata out of public PR body snapshots."""

import os
import subprocess
import sys
from pathlib import Path


def test_pr_snapshot_is_only_the_exact_public_body(tmp_path):
    root = Path(__file__).resolve().parents[2]
    source = root / "bin" / "pr-chain"
    code = f"""
import importlib.machinery
import importlib.util
import pathlib
import sys

sys.dont_write_bytecode = True
source = pathlib.Path({str(source)!r})
loader = importlib.machinery.SourceFileLoader("prchain_snapshot_probe", str(source))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)
info = {{
    "title": "fix(compaction): preserve active task instructions",
    "author": {{"login": "private-review-metadata"}},
    "body": "## Fix brief\\n\\nFixes #942",
}}
assert module.public_pr_body(info) == info["body"]
note = module.pr_body_guidance(info, pathlib.Path("/tmp/prs/1245"))
assert "GitHub title (a separate field, not part of the body)" in note
assert "only the exact current GitHub PR body" in note
assert "include only the complete corrected body" in note
assert "private-review-metadata" not in note
print("PR body snapshot contract passed")
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
    assert "PR body snapshot contract passed" in result.stdout


if __name__ == "__main__":  # the kit runs each test file as a script; no pytest needed
    import tempfile
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
            print("ok   " + name)
