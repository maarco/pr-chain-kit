#!/usr/bin/env python3
"""ruff_added: spans come from the PR's own delta (merge-base(origin/main, head)..head), not
base..head. A fixer that merges main drags main's whole file set into base..head; main's lint
debt (CI selects only E9,F63,F7,F82, so 169 ruff errors ride along) must not be blamed on the
fixer (#1235 round 1). The fixer's own added violation still flags. Scratch git repo, no agents, no gh."""
import atexit, importlib.machinery, importlib.util, pathlib, re, shutil, subprocess, tempfile

import os
os.environ.setdefault("PR_CHAIN_CONFIG", str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml"))
import sys
if "KIT_PYTHON" not in os.environ:  # a python with ruff; skip when none is around
    for cand in (sys.executable, shutil.which("python3") or ""):
        if cand and subprocess.run([cand, "-m", "ruff", "--version"], capture_output=True).returncode == 0:
            os.environ["KIT_PYTHON"] = cand
            break
    else:
        print("skip test_ruff_added_delta: no python with ruff (set KIT_PYTHON)"); sys.exit(0)
tmp = pathlib.Path(tempfile.mkdtemp(prefix="ruff-added-delta-")).resolve()
atexit.register(shutil.rmtree, tmp, True)

src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
chain = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader))
loader.exec_module(chain)


def git(*a, cwd=None):
    return subprocess.run(["git", "-C", str(cwd or tmp), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                           "-c", "user.email=t@example.com", "-c", "user.name=t", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


wt = tmp / "wt"
(tmp / "origin").mkdir()
git("init", "-qb", "main", "--bare", cwd=tmp / "origin")
git("clone", str(tmp / "origin"), str(wt))
g = lambda *a: git(*a, cwd=wt)

(wt / "README.md").write_text("root\n"); g("add", "README.md"); g("commit", "-qm", "root")
g("push", "-u", "origin", "main")

g("checkout", "-qb", "pr")
(wt / "a.py").write_text("print(1)\n"); g("add", "a.py"); g("commit", "-qm", "own line")
A = g("rev-parse", "HEAD")  # PR head before the fix round

g("checkout", "main")
(wt / "f_main.py").write_text("import os\nprint(2)\n")  # main's own F401, the landmine
g("add", "f_main.py"); g("commit", "-qm", "main debt"); g("push", "origin", "main")

g("checkout", "pr")
g("merge", "-qm", "origin/main", "origin/main")  # the fixer's fold, clean
(wt / "a.py").write_text("import os\nprint(1)\n")  # the fixer's own violation, added line 1
g("add", "a.py"); g("commit", "-qm", "fixer edit")
D = g("rev-parse", "HEAD")

changed = g("diff", "--name-only", f"{A}..{D}").split()
assert "f_main.py" in changed and "a.py" in changed, changed

bad = chain.ruff_added(wt, A, D, changed)
codes = [b.split(" ")[1] for b in bad]
files = [b.split(":")[0] for b in bad]
assert "F401" in codes and "f_main.py" not in files, f"fixer's own violation must flag: {bad}"
assert not any("f_main.py" in b for b in bad), f"main's F401 blamed on the fixer: {bad}"

# Control: the raw base..head range the old code used would still hand out spans over main's
# file, i.e. the landmine is real and this fix is what removes it.
spans_main = subprocess.run(["git", "-C", str(wt), "diff", "-U0", f"{A}..{D}", "--", "f_main.py"],
                            capture_output=True, text=True).stdout
assert re.search(r"^@@ -\S+ \+(\d+)", spans_main, re.M), "control broken: raw range should still show main's file as added"

print("test_ruff_added_delta: PASS (main-origin violation skipped, fixer's own flags, raw-range control still covers main)")
