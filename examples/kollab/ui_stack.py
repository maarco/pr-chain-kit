#!/usr/bin/env python3
"""ui_stack: build one worktree's web UI and serve it on a throwaway scratch stack, so a reviewer can screenshot it.

  ui_stack.py up   --wt <worktree> --dir <outdir> [--python <py>]   build dist, start engine + web UI, print the URL
  ui_stack.py down --dir <outdir>                                   stop what `up` started
  ui_stack.py shoot --dir <outdir> --routes /,/settings [--out <shots dir>]   3 widths x dark/light + overflow numbers

Isolation (proven recipe, memory webui-test-stack-isolation): its own HOME (never ~/.kollab), its own ports (engine 7500+,
web 8200+, never 7433/8080 which are the operator's real stack), KOLLAB_HUB_SOLO=1, BROWSER=true, KOLLAB_WEBUI_HOSTS unset,
PYTHONSAFEPATH=1 so the worktree code is what runs, and the dist is built from the worktree (a stale bundle lies).
"""
import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The engine lists every agent presence under /tmp/kollabor-hub (hardcoded). Shots go in public PR comments, so the stack
# processes may not LIST that folder: real agent names would show in the sidebar. Only the listing is denied, a stat is
# not: the hub's own startup runs mkdir(exist_ok=True) on the folder, and the old rule (deny file-read* on the whole
# subpath) made that raise, so no daemon ever published a socket and every session start hung (found 11-13 on #648).
HUB_ROOT = "/private/tmp/kollabor-hub"  # /tmp is a symlink; sandbox rules match the real path


def sandbox_cmd(root=HUB_ROOT):
    return ["/usr/bin/sandbox-exec", "-p", f'(version 1)(allow default)(deny file-read-data (literal "{root}"))']


SANDBOX = sandbox_cmd()
FORBIDDEN = {7433, 8080, 7434, 8081, 5173}


def free_port(start):
    for p in range(start, start + 200):
        if p in FORBIDDEN:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise SystemExit("no free port")


def stack_env(home, wt):
    shim = Path(home).parent / "shim"
    shim.mkdir(exist_ok=True)
    # Shots go into public PR comments: the UI shows socket.gethostname(), so report a neutral name.
    (shim / "sitecustomize.py").write_text("import socket\nsocket.gethostname = lambda: 'devbox'\n")
    srcs = [str(shim)] + [str(p) for p in sorted(Path(wt).glob("packages/*/src"))] + [str(wt)]
    # A neutral env: the stack must not look like a child of some hub agent.
    # The parent's agent markers (AGENT_PID / TOOL_SHELL) used to leak in, and
    # the engine's own detached-identity daemon spawn then hit cli_main's
    # nested-agent guard and died with exit code 2 - every UI review stack
    # reported STACK-BROKEN ("daemon koordinator failed to launch"), so no PR
    # UI could be reviewed (2026-11-14, PRs 1050/1057).
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KOLLAB_", "MENTIKO_", "SCIE", "PEX_"))
           and k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    proj = Path(home).parent / "project"
    proj.mkdir(exist_ok=True)
    env.update(KOLLAB_PROJECT_ROOT=str(proj), HOME=str(home), KOLLAB_NO_KEYRING="1", KOLLAB_HUB_SOLO="1", BROWSER="true", PYTHONSAFEPATH="1",
               PYTHONPATH=os.pathsep.join(srcs))
    return env


STAMPED = "packages/kollabor-webui/frontend/package.json"


def restore_stamp(wt):
    """Put package.json back to its committed text once the build is done. The stamp only feeds the build, and a worktree
    with a modified tracked file is one `git worktree remove` refuses to drop (found 11-13: one leftover checkout per UI review)."""
    subprocess.run(["git", "-C", str(wt), "checkout", "--", STAMPED], capture_output=True, timeout=30)


def match_bundle_version(wt):
    """The engine reports `<base>+<sha8>` from a source checkout (kollabor_engine.runtime_version) but the bundle is built
    with the bare package.json version, so the web UI shows a version-mismatch banner on every scratch stack. Stamp the
    throwaway worktree's package.json with the engine's own identity before the build. Returns the version stamped, or None."""
    import re
    try:
        base = re.search(r'^version\s*=\s*["\']([^"\']+)', (wt / "packages/kollabor-engine/pyproject.toml").read_text(), re.M).group(1)
        sha = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=wt, capture_output=True, text=True, timeout=5, check=True).stdout.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", sha):
            return None
        pj = wt / STAMPED
        data = json.loads(pj.read_text())
        data["version"] = f"{base}+{sha[:8]}"
        pj.write_text(json.dumps(data, indent=2) + "\n")
        return data["version"]
    except Exception:
        return None


def up(a):
    wt, d = Path(a.wt).resolve(), Path(a.dir).resolve()
    d.mkdir(parents=True, exist_ok=True)
    home = d / "home"
    (home / ".kollab").mkdir(parents=True, exist_ok=True)
    (home / ".kollab" / "config.json").write_text(json.dumps({"application": {"setup_completed": True}}))
    env = stack_env(home, wt)
    fe = wt / "packages" / "kollabor-webui" / "frontend"
    dist = d / "dist"
    stamped = match_bundle_version(wt)
    (d / "bundle-version.txt").write_text(str(stamped))
    try:
        b = subprocess.run(["node", "scripts/build.mjs", "--outDir", str(dist)], cwd=fe, env=env, capture_output=True, text=True, timeout=900)
    finally:
        if stamped:
            restore_stamp(wt)
    (d / "build.log").write_text((b.stdout + b.stderr)[-6000:])
    if b.returncode or not (dist / "index.html").exists():
        print("BUILD FAILED; tail of build.log:\n" + (b.stdout + b.stderr)[-800:])
        return 2
    ep, wp = free_port(7500), free_port(8200)
    pids = []
    for name, cmd, extra in (
        ("engine", [a.python, "-m", "kollabor_engine", "serve", "--port", str(ep)], {}),
        ("web", [a.python, "-c", "from kollabor_webui import main; main()"],
         {"KOLLAB_WEBUI_PORT": str(wp), "KOLLAB_ENGINE_URL": f"http://127.0.0.1:{ep}", "KOLLAB_FRONTEND_DIST": str(dist)}),
    ):
        log = open(d / f"{name}.log", "w")
        cmd = SANDBOX + cmd if os.path.exists("/usr/bin/sandbox-exec") else cmd
        p = subprocess.Popen(cmd, cwd=wt, env={**env, **extra}, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        pids.append(p.pid)
        (d / "stack.json").write_text(json.dumps({"pids": pids, "engine_port": ep, "web_port": wp}))
        if name == "engine":
            time.sleep(3)
    url = f"http://127.0.0.1:{wp}"
    for _ in range(60):
        try:
            if urllib.request.urlopen(url + "/", timeout=2).status == 200:
                (d / "stack.json").write_text(json.dumps({"pids": pids, "engine_port": ep, "web_port": wp, "url": url}))
                print(url)
                return 0
        except Exception:
            time.sleep(1)
    print(f"STACK DID NOT COME UP; see {d}/engine.log and {d}/web.log")
    down(a)
    return 3


def sweep(d):
    """Stop anything still running from inside the stack folder. The engine starts its MCP servers in sessions of their own,
    so killing the stack's process groups misses them (four orphans per UI review before 2026-11-13). Its kollab daemons (one
    per web session) detach to launchd and name the stack only in their environment, so HOME=<stack>/home counts too (`ps -E`;
    32 daemons, 1.4 GB, outlived four reviews before 11-13). Returns the pids it signalled."""
    root = str(Path(d).resolve()) + "/"
    home = re.compile(r"(?:^|\s)HOME=" + re.escape(root) + r"home(?:\s|$)")
    me = {os.getpid(), os.getppid()}
    pids = []
    linux = Path("/proc/self/environ").exists()  # macOS `ps -E` prints the environment; Linux keeps it in /proc/<pid>/environ
    for flags, hit in ((["-axww"], lambda s: root in s), (None if linux else ["-axww", "-E"], home.search)):
        if flags is None:
            for env in Path("/proc").glob("[0-9]*/environ"):
                try:
                    text = " " + env.read_bytes().replace(b"\0", b" ").decode(errors="replace")
                except OSError:
                    continue
                pid = int(env.parent.name)
                if pid not in me | set(pids) and hit(text):
                    pids.append(pid)
            continue
        for ln in subprocess.run(["ps", *flags, "-o", "pid=,command="], capture_output=True, text=True).stdout.splitlines():
            head, _, rest = ln.strip().partition(" ")
            if head.isdigit() and int(head) not in me | set(pids) and hit(rest):
                pids.append(int(head))
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        if sig == signal.SIGTERM and pids:
            time.sleep(1)
    return pids


def down(a):
    f = Path(a.dir) / "stack.json"
    if f.exists():
        for pid in json.loads(f.read_text()).get("pids", []):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        time.sleep(2)
        for pid in json.loads(f.read_text()).get("pids", []):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        f.unlink()
    sweep(a.dir)
    return 0


def shoot(a):
    st = json.loads((Path(a.dir) / "stack.json").read_text())
    out = Path(a.out or Path(a.dir) / "shots")
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["uv", "run", "-q", "--no-project", "--with", "playwright==1.62.0", "python", "-I", str(HERE / "ui_shoot.py"),
           "--url", st["url"], "--routes", a.routes, "--out", str(out)]
    return subprocess.run(cmd, timeout=900).returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--wt", required=True)
    u.add_argument("--dir", required=True)
    u.add_argument("--python", default=sys.executable)
    d = sub.add_parser("down")
    d.add_argument("--dir", required=True)
    s = sub.add_parser("shoot")
    s.add_argument("--dir", required=True)
    s.add_argument("--routes", default="/")
    s.add_argument("--out")
    a = ap.parse_args()
    sys.exit({"up": up, "down": down, "shoot": shoot}[a.cmd](a))


if __name__ == "__main__":
    main()
