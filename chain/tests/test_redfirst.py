#!/usr/bin/env python3
"""redfirst_flags: only the branch's own commits count. Test edits a merge brings in (main or
another branch) are not the fixer's; a fixer editing its own red test still flags.
Scratch git repo, no agents, no gh."""
import atexit, importlib.machinery, importlib.util, pathlib, shutil, subprocess, tempfile
import os as _os, pathlib as _pl, tempfile as _tf
_os.environ.setdefault("PR_CHAIN_CONFIG", str(_pl.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml"))
_os.environ.setdefault("KIT_WORK", _tf.mkdtemp(prefix="kitwork--prc"))

tmp = pathlib.Path(tempfile.mkdtemp(prefix="redfirst--prc"))
atexit.register(shutil.rmtree, tmp, True)

src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
chain = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader))
loader.exec_module(chain)


def git(*a):
    return subprocess.run(["git", "-C", str(tmp), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(path, text, msg):
    (tmp / path).parent.mkdir(parents=True, exist_ok=True)
    (tmp / path).write_text(text)
    git("add", path)
    git("commit", "-qm", msg)


git("init", "-q", "-b", "main")
git("config", "user.email", "t@example.invalid")
git("config", "user.name", "t")
commit("app.py", "x = 1\n", "base")
git("checkout", "-qb", "side")
commit("tests/test_side.py", "a\n", "side adds a test")
commit("tests/test_side.py", "b\n", "side edits it")
git("checkout", "-q", "main")
git("checkout", "-qb", "pr")
old = git("rev-parse", "HEAD")
commit("tests/test_fix.py", "red\n", "red test")
commit("app.py", "x = 2\n", "fix")
git("merge", "-q", "--no-ff", "-m", "merge side", "side")
got = chain.redfirst_flags(tmp, old, git("rev-parse", "HEAD"))
assert got == [], got  # the merged branch's add-then-edit is not the fixer's
commit("tests/test_fix.py", "edited\n", "fixer edits its red test")
got = chain.redfirst_flags(tmp, old, git("rev-parse", "HEAD"))
assert got == ["tests/test_fix.py"], got
print("all redfirst checks passed")
