"""worktree-janitor (bin/worktree-janitor): what goes and what stays, on a scratch repo. No GitHub, no real worktrees."""
import atexit, importlib.machinery, importlib.util, json, os, pathlib, shutil, subprocess, sys, tempfile

os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
tmp = pathlib.Path(tempfile.mkdtemp(prefix="janitor--prc"))
atexit.register(shutil.rmtree, tmp, True)
src = pathlib.Path(__file__).resolve().parents[2] / "bin/worktree-janitor"
loader = importlib.machinery.SourceFileLoader("janitor", str(src))
sys.dont_write_bytecode = True
wj = importlib.util.module_from_spec(importlib.util.spec_from_loader("janitor", loader)); loader.exec_module(wj)
fails = []
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
up, repo, wt = tmp / "up.git", tmp / "repo", tmp / "wt"; wt.mkdir()
def g(*a, cwd=repo):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(up)], check=True)
subprocess.run(["git", "clone", "-q", str(up), str(repo)], check=True, capture_output=True)
g("checkout", "-q", "-b", "main"); (repo / "a.txt").write_text("1\n"); g("add", "-A"); g("commit", "-q", "-m", "base"); g("push", "-q", "origin", "main")

def branch_with_commit(name, merge=False):
    g("checkout", "-q", "-b", name, "main"); (repo / f"{name}.txt").write_text("x\n"); g("add", "-A"); g("commit", "-q", "-m", name)
    sha = g("rev-parse", "HEAD"); g("checkout", "-q", "main")
    if merge: g("merge", "-q", "--no-ff", "-m", f"merge {name}", name); g("push", "-q", "origin", "main")
    return sha
def add_wt(folder, branch=None, detach_at=None):
    p = wt / folder
    g("worktree", "add", "-q", *(["--detach", str(p), detach_at] if detach_at else [str(p), branch]))
    return p

sha_merged = branch_with_commit("b-merged", merge=True)
sha_dirty = branch_with_commit("b-dirty", merge=True)
sha_unmerged = branch_with_commit("b-unmerged")
sha_closed = branch_with_commit("b-closed")
sha_open = branch_with_commit("b-open")
sha_train = branch_with_commit("b-train", merge=True)
w_merged, w_dirty, w_unmerged = add_wt("merged", "b-merged"), add_wt("dirty", "b-dirty"), add_wt("unmerged", "b-unmerged")
w_closed, w_open, w_train = add_wt("closed", "b-closed"), add_wt("open", "b-open"), add_wt("agent-train", "b-train")
w_detached = add_wt("review-detached", detach_at=sha_merged)
(w_dirty / "scratch.txt").write_text("untracked")
prs = [dict(number=1, state="CLOSED", headRefName="b-closed", headRefOid=sha_closed),
       dict(number=2, state="OPEN", headRefName="b-open", headRefOid=sha_open),
       dict(number=3, state="MERGED", headRefName="b-merged", headRefOid=sha_merged)]
(tmp / "prs.json").write_text(json.dumps(prs))
idx = tmp / "idx"; idx.mkdir()
for w in (w_merged, w_closed, w_unmerged):
    stem = str(w.resolve()).lstrip("/").replace("/", "-")
    for suf in (".db", ".db-wal"): (idx / (stem + suf)).write_text("x" * 100)

rows = {pathlib.Path(r["worktree"]).name: r for r in wj.worktrees(repo)}
J = lambda name, busy=(), idle=99.0: wj.judge(rows[name], repo, "origin/main", prs, set(busy), [], now_idle=lambda p: idle)
check("merged branch, clean, idle: goes", J("merged")[0], "remove")
check("a detached review checkout of a merged commit: goes", J("review-detached")[0], "remove")
check("the reason names HEAD and the idle hours", (sha_merged[:8] in J("merged")[1], "idle 99 h" in J("merged")[1]), (True, True))
check("a closed PR's reason names the PR", "closed PR #1;" in J("closed")[1], True)
check("a dirty worktree stays", J("dirty"), ("keep", "modified, untracked or ignored files"))
check("unmatched commits stay", J("unmerged"), ("keep", "HEAD does not meet removal criteria"))
check("closed PR whose last commit this is: goes", J("closed")[0], "remove")
check("open PR: stays", J("open"), ("keep", "its PR is open"))
check("a process standing in it: stays", J("merged", busy=[str(w_merged)])[1], "a process is inside")
check("a process in a subfolder counts too", J("merged", busy=[str(w_merged / "sub")])[1], "a process is inside")
check("touched an hour ago: stays", J("merged", idle=1.0)[0], "keep")
check("a fold worktree (name ends -train): stays", J("agent-train")[1], "a fold worktree (agent-kit needs it to exist)")
check("the main checkout: stays", wj.judge(rows["repo"], repo, "origin/main", prs, set(), [], now_idle=lambda p: 99.0), ("keep", "main checkout"))
check("a chain-owned prefix: stays", wj.judge(rows["merged"], repo, "origin/main", prs, set(), [str(wt) + os.sep], now_idle=lambda p: 99.0)[1], "the pr-chain's own (it cleans these)")

# Ignored data is still user data, even when git worktree remove would discard it.
ignored = w_merged / "scratch.local"
with open(repo / ".git" / "info" / "exclude", "a") as f: f.write("\nscratch.local\n")
ignored.write_text("do not discard")
check("ignored file stays", J("merged")[1], "modified, untracked or ignored files")
ignored.unlink()

real_git = wj.git
def status_error(repo_arg, *args, **kwargs):
    if args and args[0] == "status": return subprocess.CompletedProcess(args, 1, "", "error")
    return real_git(repo_arg, *args, **kwargs)
wj.git = status_error
check("status failure stays", J("merged")[1], "cannot inspect git status")
wj.git = real_git
check("activity scan failure stays", wj.judge(rows["merged"], repo, "origin/main", prs, set(), [], now_idle=lambda p: (_ for _ in ()).throw(OSError("denied")))[1], "cannot inspect activity")
real_scan = wj.procs.proc_cwds
wj.procs.proc_cwds = lambda **k: (_ for _ in ()).throw(RuntimeError("neither /proc nor lsof"))
try:
    wj.busy_dirs()
    check("process scan failure aborts", False, True)
except RuntimeError:
    check("process scan failure aborts", True, True)
wj.procs.proc_cwds = real_scan

def fetch_error(repo_arg, *args, **kwargs):
    if args and args[0] == "fetch": return subprocess.CompletedProcess(args, 1, "", "error")
    return real_git(repo_arg, *args, **kwargs)
wj.git = fetch_error
try:
    wj.main(["--apply", "--repo", str(repo), "--prs-json", str(tmp / "prs.json"), "--lock", str(tmp / "fetch.lock")])
    check("fetch failure aborts", False, True)
except SystemExit as exc:
    check("fetch failure aborts", "fetch failed" in str(exc) and w_merged.exists(), True)
wj.git = real_git

# A detached closed-PR commit with no local branch must remain reachable.
sha_orphan = g("commit-tree", "-m", "orphan", g("rev-parse", "main^{tree}"))
w_orphan = add_wt("orphan-detached", detach_at=sha_orphan)
orphan_row = next(r for r in wj.worktrees(repo) if pathlib.Path(r["worktree"]).resolve() == w_orphan.resolve())
orphan_prs = prs + [dict(number=4, state="CLOSED", headRefName="b-orphan", headRefOid=sha_orphan)]
check("detached closed PR without local branch stays", wj.judge(orphan_row, repo, "origin/main", orphan_prs, set(), [], now_idle=lambda p: 99.0)[1], "PR commit is not preserved by a local branch")

# A recent nested file counts as activity even when the top directory is old.
nested = w_unmerged / "subdir"; nested.mkdir(); (nested / "recent").write_text("x")
old = 1000000000
os.utime(w_unmerged, (old, old))
check("nested activity is detected", wj.idle_hours(w_unmerged) < 1, True)

# the command itself: dry run changes nothing, --apply removes exactly the "remove" ones and their indexes
env2 = {**env, "WORKTREE_JANITOR_IDLE_H": "0"}
def cli(*extra):
    return subprocess.run([sys.executable, "-B", str(src), "--repo", str(repo), "--prs-json", str(tmp / "prs.json"), "--index-dir", str(idx),
                           "--skip-prefix", "/nonexistent/", "--lock", str(tmp / "janitor.lock"), *extra], capture_output=True, text=True, env=env2)
import fcntl
held = open(tmp / "janitor.lock", "w"); fcntl.flock(held, fcntl.LOCK_EX)  # another run is in progress
out = cli("--apply")
check("a second run while one is active does nothing and says so", (out.returncode, "another run is active" in out.stdout, w_merged.exists()), (0, True, True))
fcntl.flock(held, fcntl.LOCK_UN); held.close()
out = cli()
check("dry run: exits 0 and says would remove", (out.returncode, "would remove 3 worktree(s)" in out.stdout), (0, True))
check("dry run: nothing is removed", (w_merged.exists(), w_closed.exists(), len(list(idx.iterdir()))), (True, True, 6))
out = cli("--apply")
check("apply: exits 0 and says removed", (out.returncode, "removed 3 worktree(s)" in out.stdout), (0, True))
check("apply: merged, closed-PR and detached worktrees are gone", (w_merged.exists(), w_closed.exists(), w_detached.exists()), (False, False, False))
check("apply: dirty, unmerged, open and train worktrees stay", (w_dirty.exists(), w_unmerged.exists(), w_open.exists(), w_train.exists()), (True, True, True, True))
check("apply: the code index of a worktree that stays is left alone", len([f for f in idx.iterdir() if "-unmerged." in f.name]), 2)
check("apply: indexes of the two removed worktrees are gone", [f.name for f in idx.iterdir() if "-merged." in f.name or "-closed." in f.name], [])
check("apply: branches are never deleted", g("branch", "--list", "b-merged", "b-closed").count("b-"), 2)
check("apply: git no longer lists the removed worktrees", [n for n in ("merged", "closed", "review-detached") if str(wt / n) in g("worktree", "list", "--porcelain")], [])

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
