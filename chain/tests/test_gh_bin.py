#!/usr/bin/env python3
"""One gh resolver: config [tools] gh, else the first gh on PATH, else the old fixed paths. The board, bin/pr-chain's
sh(), the agent-kit and the janitor all use it, so a gh shim first on PATH is the gh that runs. Scratch dirs only."""
import os, pathlib, stat, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parents[2]
d = pathlib.Path(tempfile.mkdtemp(prefix="gh-bin--prc")).resolve()
shimdir = d / "shim"
shimdir.mkdir()
shim = shimdir / "gh"
shim.write_text('#!/bin/sh\necho "shim-gh $*"\n')
shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
emptydir = d / "empty"
emptydir.mkdir()
env = {k: v for k, v in os.environ.items() if not k.startswith(("KIT_", "PR_CHAIN_", "KOLLAB_", "GH_", "GITHUB_"))}
cfgfile = str(root / "chain/tests/test-config.toml")


def child(code, path, **extra):
    e = dict(env, PATH=path, PR_CHAIN_CONFIG=cfgfile, KIT_WORK=str(d / "work"), PR_CHAIN_WT_ROOT=str(d / "wt"))
    e.update(extra)
    p = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=e, cwd=d)
    assert p.returncode == 0, (p.stdout + p.stderr)[-1500:]
    return p.stdout.strip().splitlines()[-1]


CH = f"import sys; sys.path.insert(0, {str(root / 'chain')!r}); import config\n"
LOAD = ("import importlib.machinery, importlib.util\n"
        "def load(name, path):\n"
        "    l = importlib.machinery.SourceFileLoader(name, path)\n"
        "    m = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, l)); l.exec_module(m); return m\n")
PATH_SHIM = f"{shimdir}:/usr/bin:/bin"

# 1. no config, shim first on PATH: the resolver returns the shim
got = child(CH + "config.configure_standalone()\nprint(config.gh_bin())", PATH_SHIM)
assert got == str(shim), f"gh_bin with a shim on PATH: {got}"

# 2. [tools] gh wins over PATH (host settings)
got = child(CH + "config.configure({'tools': {'gh': '/opt/custom/gh'}}, sys.argv[1] if len(sys.argv) > 1 else '.')\n"
            "print(config.gh_bin())", PATH_SHIM)
assert got == "/opt/custom/gh", f"[tools] gh must win: {got}"

# 3. nothing on PATH and no config: a usable name or old fixed path, never an error
got = child(CH + "config.configure_standalone()\nprint(config.gh_bin())", str(emptydir))
assert got.endswith("gh"), got

# 4. the board's GH is the shim
got = child(LOAD + CH + "config.configure_standalone()\n"
            f"b = load('board', {str(root / 'bin/pr-chain-board')!r})\nprint(b.GH)", PATH_SHIM)
assert got == str(shim), f"board GH: {got}"

# 5. bin/pr-chain sh('gh', ...) runs the shim
got = child(LOAD + f"pc = load('prchain', {str(root / 'bin/pr-chain')!r})\nprint(pc.sh('gh', 'version').stdout.strip())", PATH_SHIM)
assert got == "shim-gh version", f"pr-chain sh gh: {got}"

# 6. agent-kit and the janitor resolve through the same helper
got = child(f"import sys; sys.path.insert(0, {str(root / 'agent-kit')!r}); import kit; print(kit.gh_bin())", PATH_SHIM)
assert got == str(shim), f"kit.gh_bin: {got}"
src = (root / "bin/worktree-janitor").read_text() + (root / "agent-kit/land/land_pr.py").read_text()
assert 'run("gh"' not in src and '["gh"' not in src, "janitor and land_pr must not hard-code gh"
assert "/opt/homebrew/bin/gh" not in (root / "bin/pr-chain-board").read_text(), "board must not prefer a fixed path"

# fold.py resolves the configured gh even when PATH contains no gh; subprocess is mocked, so this cannot reach GitHub.
fold_config = d / "fold.toml"
fold_config.write_text((root / "chain/tests/test-config.toml").read_text() + '\n[tools]\ngh = "/opt/custom/gh"\n')
fold = str(root / "agent-kit/land/fold.py")
code = """import runpy, subprocess, sys
from types import SimpleNamespace
from unittest.mock import patch
calls = []
def fake_run(argv, **kwargs):
    calls.append((list(argv), kwargs))
    return SimpleNamespace(returncode=0, stdout='{"headRefName":"branch","headRefOid":"abc","state":"CLOSED","baseRefName":"main","closingIssuesReferences":[]}', stderr="")
with patch("subprocess.run", side_effect=fake_run):
    sys.argv = [sys.argv[0], "1"]
    try:
        runpy.run_path(FOLD_PATH)
    except SystemExit:
        pass
assert len(calls) == 1, calls
assert calls[0][0][0] == "/opt/custom/gh", calls[0][0]
print(calls[0][0][0])
""".replace("FOLD_PATH", repr(fold))
got = child(code, str(emptydir), PR_CHAIN_CONFIG=str(fold_config))
assert got == "/opt/custom/gh", f"fold.py gh: {got}"
print("PASS")
