#!/usr/bin/env python3
"""own_delta: the changelog guard blames the fixer only for the PR's own files. A merge of main
drags main's release CHANGELOG.md into head..new; that is not the fixer's edit. A changelog edit
the fixer made on the branch still counts. Scratch git repo, no agents, no gh."""
import atexit, importlib.machinery, importlib.util, pathlib, shutil, subprocess, tempfile
import os as _os, pathlib as _pl, tempfile as _tf
_os.environ.setdefault("PR_CHAIN_CONFIG", str(_pl.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml"))
_os.environ.setdefault("KIT_WORK", _tf.mkdtemp(prefix="kitwork--prc"))

tmp = pathlib.Path(tempfile.mkdtemp(prefix="changelog-guard--prc"))
atexit.register(shutil.rmtree, tmp, True)

src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
chain = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader))
loader.exec_module(chain)

CHANGELOGS = ("CHANGELOG.md", "kollabor/updates/CHANGELOG.md")


def git(*a):
    return subprocess.run(["git", "-C", str(tmp), "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *a],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(path, text, msg):
    (tmp / path).parent.mkdir(parents=True, exist_ok=True)
    (tmp / path).write_text(text)
    git("add", path)
    git("commit", "-qm", msg)


def changed(old, new):
    return git("diff", "--name-only", f"{old}..{new}").split()


def blamed(old, new):
    """What the guard in fix() would blame the fixer for."""
    return [c for c in chain.own_delta(tmp, new, changed(old, new)) if c in CHANGELOGS]


git("init", "-q", "-b", "main")
git("config", "user.email", "t@example.invalid")
git("config", "user.name", "t")
commit("app.py", "x = 1\n", "base")
commit("CHANGELOG.md", "# 0.22.0\n", "changelog exists")

# main moves on with a release commit that edits CHANGELOG.md
git("checkout", "-qb", "pr")
commit("app.py", "x = 2\n", "the PR's work")
head = git("rev-parse", "HEAD")
git("checkout", "-q", "main")
commit("CHANGELOG.md", "# 0.22.1\n", "chore(release): prepare 0.22.1")
git("update-ref", "refs/remotes/origin/main", git("rev-parse", "main"))

# case 1: the fixer resolves a conflict by merging main. CHANGELOG.md rides along; not its edit.
git("checkout", "-q", "pr")
git("merge", "--no-edit", "-q", "origin/main")
commit("app.py", "x = 3\n", "the fix")
new = git("rev-parse", "HEAD")
assert "CHANGELOG.md" in changed(head, new), "setup: the merge must put CHANGELOG.md in head..new"
assert blamed(head, new) == [], f"merge of main must not be blamed on the fixer: {blamed(head, new)}"
assert "app.py" in chain.own_delta(tmp, new, changed(head, new)), "the PR's own file must survive own_delta"

# case 2: the fixer edits CHANGELOG.md itself on the branch. Still blocked.
head2 = new
commit("CHANGELOG.md", "# 0.22.1\n\nfixer wrote here\n", "fixer edits the changelog")
new2 = git("rev-parse", "HEAD")
assert blamed(head2, new2) == ["CHANGELOG.md"], f"a real changelog edit must still block: {blamed(head2, new2)}"

# case 3: the nested changelog path is caught the same way
head3 = new2
commit("kollabor/updates/CHANGELOG.md", "notes\n", "fixer edits the nested changelog")
new3 = git("rev-parse", "HEAD")
assert blamed(head3, new3) == ["kollabor/updates/CHANGELOG.md"], blamed(head3, new3)

# case 4: no origin/main ref (a worktree that never fetched): fall back to the raw range, guard stays on
git("update-ref", "-d", "refs/remotes/origin/main")
assert chain.own_delta(tmp, new3, ["CHANGELOG.md"]) == ["CHANGELOG.md"], "no origin/main must not disable the guard"

print("test_changelog_guard: PASS (4 cases)")

# The cases above exercise own_delta directly, so they stay green even if fix() stops calling it.
# Pin the wiring: the guard inside fix() must run its file list through own_delta.
import inspect, re as _re
body = inspect.getsource(chain.fix)
guard = [ln for ln in body.splitlines() if "edited a CHANGELOG file" in ln]
assert guard, "fix() no longer has the changelog guard"
window = body[:body.index(guard[0])].rsplit("changed = ", 1)[-1]
assert "own_delta(" in window, "fix()'s changelog guard does not run its file list through own_delta"
assert _re.search(r"own_delta\(\s*wt\s*,\s*new\s*,\s*changed\s*\)", body), \
    "fix() calls own_delta with unexpected arguments"

print("test_changelog_guard: wiring pinned (fix() routes the guard through own_delta)")
