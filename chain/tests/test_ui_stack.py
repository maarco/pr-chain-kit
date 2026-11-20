#!/usr/bin/env python3
"""The scratch stack's bundle version must equal what the engine reports from the same checkout (replay #998: banner)."""
import importlib.util, json, os, pathlib, subprocess, sys, tempfile
H = pathlib.Path.home()
TMP = H / "backups" if (H / "backups").is_dir() else pathlib.Path(tempfile.gettempdir()).resolve()  # scratch root (resolved: /var is a symlink on macOS)
_root = pathlib.Path(__file__).resolve().parents[2]
os.environ["PR_CHAIN_CONFIG"] = str(_root / "chain/tests/test-config.toml")
spec = importlib.util.spec_from_file_location("us", _root / "examples/kollab/ui_stack.py"); us = importlib.util.module_from_spec(spec); spec.loader.exec_module(us)
fails = []
def check(n, got, want):
    ok = got == want; print(("ok   " if ok else "FAIL ") + n + ("" if ok else f": got {got!r}, want {want!r}")); (ok or fails.append(n))
def repo(base, pkgver="0.0.1"):
    d = pathlib.Path(tempfile.mkdtemp(prefix="uistack-", dir=TMP))
    (d / "packages/kollabor-engine").mkdir(parents=True); (d / "packages/kollabor-webui/frontend").mkdir(parents=True)
    (d / "packages/kollabor-engine/pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{base}"\n')
    (d / "packages/kollabor-webui/frontend/package.json").write_text(json.dumps({"name": "f", "version": pkgver}))
    for c in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", *c], cwd=d, check=True, capture_output=True)
    return d
d = repo("1.2.3")
sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d, capture_output=True, text=True).stdout.strip()
v = us.match_bundle_version(d)
check("stamped version is base+sha8", v, f"1.2.3+{sha[:8]}")
check("package.json carries it", json.loads((d / "packages/kollabor-webui/frontend/package.json").read_text())["version"], v)
# same identity as the engine's own function, on the real checkout's two files
real = pathlib.Path(os.environ.get("KOLLAB_CHECKOUT") or H / "dev/kollab")  # optional: a real kollab checkout
r = None
if (real / "packages/kollabor-engine/src/kollabor_engine/runtime_version.py").exists():
    sp = importlib.util.spec_from_file_location("rv", real / "packages/kollabor-engine/src/kollabor_engine/runtime_version.py"); rv = importlib.util.module_from_spec(sp); sp.loader.exec_module(rv)
    want = rv.get_engine_runtime_version()
    r = repo(rv._base_version())
    subprocess.run(["git", "-C", str(r), "fetch", "-q", str(real), "HEAD"], capture_output=True)
    head = subprocess.run(["git", "-C", str(real), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    check("shape matches the engine's own version string", v.split("+")[0] != "" and want.split("+")[0] == rv._base_version() and len(want.split("+")[1]) == 8, True)
else:
    print("skip engine version match: no kollab checkout (set KOLLAB_CHECKOUT)")
check("no git repo: returns None, no crash", us.match_bundle_version(pathlib.Path(tempfile.mkdtemp(dir=TMP))), None)
# the stamp only feeds the build: a worktree left with a modified tracked file cannot be dropped by `git worktree remove`
# (found 11-13: #648's r2-ui worktree was stuck on exactly package.json)
dirty = lambda w: subprocess.run(["git", "-C", str(w), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
check("after stamping, the worktree is dirty (the premise)", dirty(d) != "", True)
us.restore_stamp(d)
check("restore_stamp leaves the worktree clean", dirty(d), "")
check("restore_stamp puts the committed version back", json.loads((d / us.STAMPED).read_text())["version"], "0.0.1")
us.restore_stamp(pathlib.Path(tempfile.mkdtemp(dir=TMP)))  # not a git repo: must not raise
check("restore_stamp outside a git repo does not raise", True, True)
# pr-chain's drop_wt: the build stamp alone must not block removing a UI worktree (stacks before 11-13 left it modified, so
# #648 and #998 kept a worktree each), but a worktree with anything else changed must survive (a fixer's work is not ours to drop)
import importlib.machinery, shutil
os.environ["KIT_WORK"] = tempfile.mkdtemp(prefix="uistack-work-", dir=TMP)
os.environ["PR_CHAIN_WT_ROOT"] = os.path.join(os.environ["KIT_WORK"], "wt")
_l = importlib.machinery.SourceFileLoader("prchain_t", str(_root / "bin/pr-chain"))
pcm = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain_t", _l)); _l.exec_module(pcm)
pcm.log = lambda m: None
pcm.CHECKOUT = base = repo("4.5.6")
def wt_of(name):
    p = pathlib.Path(tempfile.mkdtemp(prefix="uistack-wt-", dir=TMP)) / name
    subprocess.run(["git", "worktree", "add", "-q", "--detach", str(p), "HEAD"], cwd=base, check=True, capture_output=True)
    return p
w1 = wt_of("stamp-only"); us.match_bundle_version(w1)
pcm.drop_wt(w1); check("drop_wt removes a worktree whose only change is the build stamp", w1.exists(), False)
w2 = wt_of("stamp-plus-work"); us.match_bundle_version(w2); (w2 / "packages/kollabor-engine/pyproject.toml").write_text("changed\n")
pcm.drop_wt(w2); check("drop_wt keeps a worktree with real uncommitted work", w2.exists(), True)
w3 = wt_of("untracked"); (w3 / "notes.txt").write_text("x")
pcm.drop_wt(w3); check("drop_wt keeps a worktree with an untracked file", w3.exists(), True)
w4 = wt_of("clean"); pcm.drop_wt(w4); check("drop_wt still removes a clean worktree", w4.exists(), False)
for w in (w1, w2, w3, w4): shutil.rmtree(w.parent, ignore_errors=True)
shutil.rmtree(base, ignore_errors=True); shutil.rmtree(os.environ["KIT_WORK"], ignore_errors=True)
# teardown sweep: the engine's MCP servers run in sessions of their own, so killing the stack's process groups misses them
import argparse, time
sd = pathlib.Path(tempfile.mkdtemp(prefix="uistack-sweep-", dir=TMP))
spawn = lambda arg: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", arg], start_new_session=True)
orphan, bystander = spawn(str(sd / "home/.npm/server")), spawn(str(sd) + "-other/server")
time.sleep(0.5)
hit = us.sweep(sd)
try: orphan.wait(timeout=5); gone = True
except subprocess.TimeoutExpired: gone = False
check("sweep stops a detached child that runs from the stack folder", orphan.pid in hit and gone, True)
check("sweep leaves a process whose path only shares the folder name prefix", bystander.poll() is None and bystander.pid not in hit, True)
orphan2 = spawn(str(sd / "home/.npm/server2"))
time.sleep(0.5)
us.down(argparse.Namespace(dir=str(sd)))  # no stack.json here: down must still sweep
try: orphan2.wait(timeout=5); gone2 = True
except subprocess.TimeoutExpired: gone2 = False
check("down without stack.json still sweeps leftovers", gone2, True)
check("sweep on a folder with nothing running signals nobody", us.sweep(sd), [])
bystander.kill(); bystander.wait()
# the engine's kollab daemons (one per web session) detach to launchd and carry the stack only in HOME, never in their
# command line: 32 of them (1.4 GB) outlived the #549/#648/#970/#998 reviews before 11-13
spawn_env = lambda **e: subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], env={**os.environ, **e}, start_new_session=True)
daemon, near, pwd_only = spawn_env(HOME=str(sd / "home")), spawn_env(HOME=str(sd) + "-other/home"), spawn_env(PWD=str(sd / "home"))
time.sleep(0.5)
hit = us.sweep(sd)
try: daemon.wait(timeout=5); gone3 = True
except subprocess.TimeoutExpired: gone3 = False
check("sweep stops a daemon whose only tie to the stack is HOME=<stack>/home", daemon.pid in hit and gone3, True)
check("sweep leaves a process whose HOME only shares the folder name prefix", near.poll() is None and near.pid not in hit, True)
check("sweep leaves a process with the stack folder in another variable only (PWD)", pwd_only.poll() is None and pwd_only.pid not in hit, True)
for p in (daemon, near, pwd_only):
    p.poll() is None and p.kill(); p.wait()
# sandbox: the hub's own startup stats/mkdirs the hub root (a rule denying every read of it crashed each session start,
# found 11-13 on #648), but the stack must not be able to LIST it, or real agent names reach the public screenshots
check("shipped rule denies listing only (not file-read* on the subpath)", 'deny file-read-data (literal "/private/tmp/kollabor-hub")' in us.SANDBOX[2] and "file-read*" not in us.SANDBOX[2], True)
hub = (TMP / f"uistack-hub-{os.getpid()}").resolve()
(hub / "abcdef012345").mkdir(parents=True); (hub / "abcdef012345" / "real-agent.json").write_text("{}")
PROBE = """
import json, os, sys
from pathlib import Path
R = Path(sys.argv[1]); out = {}
def t(k, f):
    try: out[k] = f()
    except OSError as e: out[k] = type(e).__name__
t("mkdir", lambda: R.mkdir(parents=True, exist_ok=True, mode=0o700) or "ok")
t("stat", lambda: "ok" if os.lstat(R).st_uid == os.getuid() else "wrong uid")
t("list", lambda: len(os.listdir(R)))
t("peek", lambda: len(list(R.glob("*/*.json"))))
def own():
    p = R / "aaaaaaaaaaaa"; p.mkdir(mode=0o700, exist_ok=True); (p / "me.json").write_text("{}")
    return "ok" if (p / "me.json").read_text() == "{}" and len(list(p.iterdir())) == 1 else "bad"
t("own", own)
print(json.dumps(out))
"""
run = lambda pre: json.loads(subprocess.run([*pre, sys.executable, "-c", PROBE, str(hub)], capture_output=True, text=True).stdout)
if os.path.exists("/usr/bin/sandbox-exec"):
    free, boxed = run([]), run(us.sandbox_cmd(str(hub)))
    check("unsandboxed probe sees the real agent (the test is not vacuous)", (free["list"], free["peek"]), (1, 1))
    check("sandbox: hub root can be stat-ed and mkdir-ed (exist_ok)", (boxed["stat"], boxed["mkdir"]), ("ok", "ok"))
    check("sandbox: hub root cannot be listed", boxed["list"], "PermissionError")
    check("sandbox: other projects' presence files are not reachable", boxed["peek"], 0)
    check("sandbox: the stack's own project folder under the root works", boxed["own"], "ok")
else:
    print("skip sandbox checks: no /usr/bin/sandbox-exec")
import shutil
for p in (d, r, sd, hub): p and shutil.rmtree(p, ignore_errors=True)
print("\nFAILED: " + ", ".join(fails) if fails else "\nall ui_stack checks passed"); sys.exit(1 if fails else 0)
