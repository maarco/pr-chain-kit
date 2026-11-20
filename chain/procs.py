"""Working folder of every process: /proc on Linux, lsof elsewhere (macOS). Used to find processes standing in a worktree."""
import os
import shutil
import subprocess
from pathlib import Path


def proc_cwds(own_only=False):
    """{pid: cwd}. own_only keeps this user's processes. Raises RuntimeError when the cwds cannot be read at all."""
    if Path("/proc/self/cwd").exists():
        out = {}
        for d in Path("/proc").iterdir():
            if not d.name.isdigit():
                continue
            try:
                if own_only and d.stat().st_uid != os.getuid():
                    continue
                out[int(d.name)] = os.readlink(d / "cwd")
            except OSError:  # gone meanwhile, or another user's
                continue
        return out
    lsof = shutil.which("lsof") or ("/usr/sbin/lsof" if Path("/usr/sbin/lsof").exists() else None)
    if not lsof:
        raise RuntimeError("neither /proc nor lsof is available to read process working folders")
    cmd = [lsof, "-a", "-d", "cwd", "-Fpn"] + (["-u", str(os.getuid())] if own_only else ["-c", ""])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("lsof timed out") from e
    if r.returncode and not r.stdout:
        raise RuntimeError(f"lsof failed: {r.stderr.strip()[:150]}")
    out, pid = {}, None
    for ln in r.stdout.splitlines():
        if ln.startswith("p"):
            pid = int(ln[1:])
        elif ln.startswith("n") and pid:
            out[pid] = ln[1:]
    return out
