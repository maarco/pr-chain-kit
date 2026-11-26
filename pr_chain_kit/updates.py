"""Opt-in, transactional updates for an explicitly managed pr-chain installation.

Managed layout: <root>/releases/vX.Y.Z/, <root>/current (symlink), and
<root>/bin/pr-chain. Running commands resolve `current` once to an immutable
release directory, so an activation cannot change code under a live chain.
"""
from __future__ import annotations

import ast
import fcntl
import json
import os
import re
import stat
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = "maarco/pr-chain-kit"
API = f"https://api.github.com/repos/{REPO}"
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def _request(url: str, timeout: float = 8) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "pr-chain-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"GitHub returned HTTP {response.status}")
        return response.read(64 * 1024 * 1024 + 1)


def latest_release() -> tuple[str, bytes]:
    """Fetch one published stable release and its source archive, with bounded I/O."""
    try:
        data = json.loads(_request(f"{API}/releases/latest"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return "", b""  # no published stable release is an ordinary idle state
        raise RuntimeError(f"GitHub releases API returned HTTP {exc.code}") from exc
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not check GitHub releases (network/API error): {exc}") from exc
    tag = data.get("tag_name", "")
    if data.get("draft") or data.get("prerelease") or not TAG_RE.fullmatch(tag):
        raise RuntimeError(f"latest release has an unsupported stable version tag: {tag!r}")
    # Use GitHub's canonical archive endpoint for the fixed repository and exact release tag.
    try:
        archive = _request(f"{API}/tarball/{tag}", timeout=20)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"could not download release {tag}: {exc}") from exc
    if len(archive) > 64 * 1024 * 1024:
        raise RuntimeError(f"release {tag} source archive exceeds 64 MiB")
    return tag, archive


def _version(root: Path) -> str:
    init = root / "__init__.py" if (root / "__init__.py").is_file() else root / "pr_chain_kit" / "__init__.py"
    tree = ast.parse(init.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise RuntimeError("release is missing a literal pr_chain_kit.__version__")


def _tag_version(tag: str) -> tuple[int, int, int] | None:
    match = TAG_RE.fullmatch(tag)
    return tuple(map(int, match.groups())) if match else None


def _extract(archive: bytes, dest: Path) -> Path:
    """Extract a GitHub source tarball without links, path traversal, or special files."""
    with tarfile.open(fileobj=__import__("io").BytesIO(archive), mode="r:gz") as tf:
        members = tf.getmembers()
        if len(members) > 20000 or sum(m.size for m in members if m.isfile()) > 128 * 1024 * 1024:
            raise RuntimeError("release archive exceeds the 20,000-entry or 128 MiB expanded-size limit")
        roots = {m.name.split("/", 1)[0] for m in members if m.name}
        if len(roots) != 1:
            raise RuntimeError("release archive has an unexpected top-level layout")
        prefix = next(iter(roots)) + "/"
        for member in members:
            name = member.name
            if not name.startswith(prefix) or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise RuntimeError(f"release archive contains an unsafe entry: {name!r}")
            rel = name[len(prefix):]
            if not rel:
                continue
            target = (dest / rel).resolve()
            if dest.resolve() not in target.parents and target != dest.resolve():
                raise RuntimeError(f"release archive path escapes staging: {name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tf.extractfile(member)
                if source is None:
                    raise RuntimeError(f"release archive entry is unreadable: {name!r}")
                with source, target.open("xb") as out:
                    shutil.copyfileobj(source, out)
                target.chmod(member.mode & 0o755)
    return dest


def _managed_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    if not (root / "current").is_symlink() or not (root / "bin" / "pr-chain").is_file():
        raise RuntimeError(f"{root} is not a managed pr-chain installation; install it with `pr-chain updates install` first")
    return root


def root_for(package_dir: Path) -> Path | None:
    """Return the stable managed root only when this module is running from its current target."""
    release = package_dir.resolve().parent
    if release.parent.name != "releases":
        return None
    root = release.parent.parent
    try:
        if (root / "current").resolve() == release and (root / "bin" / "pr-chain").is_file():
            return root
    except OSError:
        pass
    return None


def set_enabled(enabled: bool) -> None:
    """Set the user-owned config toggle with a text-preserving update."""
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pr-chain"
    path = Path(os.environ.get("PR_CHAIN_CONFIG") or config_dir / "pr-chain.toml").expanduser()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            out.write("[updates]\nenabled = " + str(enabled).lower() + "\n")
        return
    original = path.read_text()
    table = re.search(r"(?m)^\[updates\]\s*$", original)
    value = f"enabled = {str(enabled).lower()}"
    if table:
        start = table.end()
        next_table = re.search(r"(?m)^\[[^\]]+\]\s*$", original[start:])
        end = start + next_table.start() if next_table else len(original)
        block = original[start:end]
        if re.search(r"(?m)^enabled\s*=.*$", block):
            block = re.sub(r"(?m)^enabled\s*=.*$", value, block, count=1)
        else:
            block = "\n" + value + block
        updated = original[:start] + block + original[end:]
    else:
        updated = original.rstrip() + "\n\n[updates]\n" + value + "\n"
    # Parse before replacing; retain all existing operator data and fail closed on malformed TOML.
    import tomllib
    tomllib.loads(updated)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(updated)
    os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
    os.replace(tmp, path)


def _status_path(root: Path) -> Path:
    return root / "state" / "update-status.json"


def _write_status(root: Path, status: dict) -> None:
    path = _status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2) + "\n")
    os.replace(tmp, path)


def _activate(root: Path, version: str) -> None:
    release = root / "releases" / version
    if not release.is_dir():
        raise RuntimeError(f"staged release {version} is missing")
    link = root / f".current-{os.getpid()}"
    try:
        link.symlink_to(Path("releases") / version)
        os.replace(link, root / "current")
    finally:
        try:
            link.unlink()
        except FileNotFoundError:
            pass


def _validate_files(release: Path, expected: str) -> None:
    if _version(release) != expected:
        raise RuntimeError(f"installed version verification failed for {expected}")
    for source in release.rglob("*.py"):
        compile(source.read_bytes(), str(source), "exec")
    required = (release / "bin" / "pr-chain", release / "bin" / "pr-chain-loop",
                release / "bin" / "pr-chain-board", release / "chain" / "board" / "config.html")
    missing = [str(p.relative_to(release)) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError("release is missing required entrypoints or board assets: " + ", ".join(missing))
    probe = subprocess.run([sys.executable, str(release / "pr_chain_kit" / "cli.py"), "path"], check=True,
                           timeout=10, capture_output=True, text=True)
    if Path(probe.stdout.strip()).resolve() != release.resolve():
        raise RuntimeError("installed pr-chain path validation returned the wrong release directory")


def _validate(root: Path, release: Path, expected: str) -> None:
    _validate_files(release, expected)
    if (root / "current").resolve() != release.resolve():
        raise RuntimeError("managed activation did not select the validated release")
    board_probe = """import importlib.machinery, importlib.util, sys, urllib.request
from http.server import ThreadingHTTPServer
from threading import Thread
sys.path.insert(0, sys.argv[1] + '/chain')
loader=importlib.machinery.SourceFileLoader('pr_chain_board_probe', sys.argv[1] + '/bin/pr-chain-board')
spec=importlib.util.spec_from_loader(loader.name, loader); board=importlib.util.module_from_spec(spec); loader.exec_module(board)
server=ThreadingHTTPServer(('127.0.0.1', 0), board.H); Thread(target=server.serve_forever, daemon=True).start()
try:
 for route in ('/', '/config', '/wiki', '/spend', '/prs', '/issues'):
  with urllib.request.urlopen('http://127.0.0.1:%d%s' % (server.server_port, route), timeout=3) as response:
   assert response.status == 200 and response.headers.get_content_type() == 'text/html', route
finally: server.shutdown(); server.server_close()
"""
    subprocess.run([sys.executable, "-B", "-c", board_probe, str(release)], check=True, timeout=15,
                   capture_output=True, env={**os.environ, "PR_CHAIN_CONFIG": str(release / "pr-chain.toml"),
                                            "PYTHONDONTWRITEBYTECODE": "1"})


def install(root: Path, source: Path, package_dir: Path) -> str:
    """Create an explicit managed install by copying this known kit; refuses existing roots."""
    root = root.expanduser().absolute()
    if root.is_symlink():
        raise RuntimeError(f"refusing symlinked installation root {root}")
    if root.exists():
        raise RuntimeError(f"refusing existing installation destination {root}")
    version = "v" + _version(package_dir)
    root.parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix=f".{root.name}.install-", dir=root.parent))
    (stage_root / "releases").mkdir()
    (stage_root / "bin").mkdir()
    (stage_root / "state").mkdir()
    stage = stage_root / "releases" / version
    stage.mkdir()
    try:
        # Copy only runtime source and data. Operator files remain outside the install root.
        for name in ("bin", "chain", "agent-kit", "examples", "starter-pack", "vendor", "pr-chain.toml", "SOFTWARE_DEVELOPMENT_LIFECYCLE.md"):
            item = source / name
            if item.exists():
                if item.is_symlink():
                    raise RuntimeError(f"refusing symlinked runtime source {item}")
                if item.is_dir() and any(Path(base, entry).is_symlink() for base, dirs, files in os.walk(item)
                                          for entry in dirs + files):
                    raise RuntimeError(f"refusing runtime source containing symlinks: {item}")
                shutil.copytree(item, stage / name, symlinks=False) if item.is_dir() else shutil.copy2(item, stage / name)
        if package_dir.is_symlink() or any(Path(base, entry).is_symlink() for base, dirs, files in os.walk(package_dir)
                                           for entry in dirs + files):
            raise RuntimeError(f"refusing package source containing symlinks: {package_dir}")
        shutil.copytree(package_dir, stage / "pr_chain_kit", symlinks=False,
                        ignore=shutil.ignore_patterns("kit", "__pycache__", "*.pyc"))
        wrapper = stage_root / "bin" / "pr-chain"
        wrapper.write_text("#!/bin/sh\nROOT=$(CDPATH= cd -- \"$(dirname -- \"$0\")/..\" && pwd)\n"
                           "PR_CHAIN_MANAGED_PYTHONPATH=\"$ROOT/current\" exec \"${PYTHON:-python3}\" -B -c "
                           "'import os,runpy,sys; sys.path.insert(0, os.environ[\"PR_CHAIN_MANAGED_PYTHONPATH\"]); "
                           "runpy.run_module(\"pr_chain_kit.cli\", run_name=\"__main__\")' \"$@\"\n")
        wrapper.chmod(0o755)
        _validate_files(stage, version[1:])
        _activate(stage_root, version)
        _write_status(stage_root, {"installed_version": version, "available_version": version, "status": "installed", "checked_at": int(time.time()), "error": None})
        os.replace(stage_root, root)
        final = root / "releases" / version
        _validate(root, final, version[1:])
        return version
    except BaseException:
        shutil.rmtree(stage_root, ignore_errors=True)
        # The root becomes visible only at the final directory rename, before the loopback probe.
        if root.exists() and not root.is_symlink():
            shutil.rmtree(root, ignore_errors=True)
        raise


def update(root: Path, *, activate: bool = True, min_interval: int = 0) -> dict:
    root = _managed_root(root)
    lock_path = root / "state" / "update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            previous = json.loads(_status_path(root).read_text())
            if min_interval and time.time() - int(previous.get("checked_at", 0)) < min_interval:
                previous["installed_version"] = (root / "current").resolve().name
                previous["_throttled"] = True
                return previous
        except (OSError, ValueError, TypeError):
            pass
        installed = (root / "current").resolve().name
        status = {"installed_version": installed, "available_version": None, "status": "checking", "checked_at": int(time.time()), "error": None}
        try:
            tag, archive = latest_release()
            if not tag:
                status["status"] = "no-release"
                _write_status(root, status)
                return status
            status["available_version"] = tag
            available_tuple, installed_tuple = _tag_version(tag), _tag_version(installed)
            if tag == installed:
                status["status"] = "current"
            elif installed_tuple is not None and available_tuple < installed_tuple:
                status["status"] = "newer-installed"
            elif not activate:
                status["status"] = "available"
            else:
                releases = root / "releases"
                stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=releases))
                try:
                    _extract(archive, stage)
                    if _version(stage) != tag[1:]:
                        raise RuntimeError(f"release tag {tag} does not match bundled version {_version(stage)}")
                    # Validate syntax and the package's own path command before making the release visible.
                    _validate_files(stage, tag[1:])
                    required = (stage / "bin" / "pr-chain", stage / "bin" / "pr-chain-loop",
                                stage / "bin" / "pr-chain-board", stage / "chain" / "board" / "config.html")
                    if any(not p.is_file() for p in required):
                        raise RuntimeError("release archive is missing required entrypoints or board assets")
                    probe = subprocess.run([sys.executable, "-B", "-m", "pr_chain_kit.cli", "path"], check=True,
                                           timeout=10, capture_output=True, text=True, cwd=stage,
                                           env={**os.environ, "PYTHONPATH": str(stage), "PYTHONDONTWRITEBYTECODE": "1"})
                    if Path(probe.stdout.strip()).resolve() != stage.resolve():
                        raise RuntimeError("staged pr-chain path validation returned the wrong installation root")
                    final = releases / tag
                    if final.exists():
                        # A prior complete stage is reusable only if its version matches the release tag.
                        _validate_files(final, tag[1:])
                        shutil.rmtree(stage)
                    else:
                        os.replace(stage, final)
                    previous = (root / "current").resolve().name
                    status["status"] = "activating"
                    _write_status(root, status)
                    _activate(root, tag)
                    try:
                        _validate(root, final, tag[1:])
                    except Exception:
                        _activate(root, previous)
                        raise
                    status["status"] = "updated"
                finally:
                    shutil.rmtree(stage, ignore_errors=True)
        except Exception as exc:
            status["status"] = "error"
            status["error"] = str(exc)
        _write_status(root, status)
        return status


def maybe_update(root: Path, *, interval: int = 6 * 60 * 60) -> dict | None:
    """Automatic loop check with a shared six-hour throttle across all loop processes."""
    result = update(root, min_interval=interval)
    return None if result.pop("_throttled", False) else result


def status(root: Path) -> dict:
    root = _managed_root(root)
    lock_path = root / "state" / "update.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(_status_path(root).read_text())
        except (OSError, ValueError):
            data = {"available_version": None, "status": "unknown", "error": None}
        installed = (root / "current").resolve().name
        if data.get("status") == "activating":
            available = data.get("available_version")
            old = data.get("installed_version")
            if installed == available and _tag_version(installed):
                try:
                    _validate(root, root / "releases" / installed, installed[1:])
                    data.update(status="recovered", error=None)
                except Exception as exc:
                    if old and (root / "releases" / old).is_dir():
                        _activate(root, old)
                        installed = old
                    data.update(status="error", error=f"interrupted activation failed validation: {exc}")
            else:
                data.update(status="interrupted", error="activation did not switch versions; previous version remains selected")
            data["installed_version"] = installed
            _write_status(root, data)
        data["installed_version"] = (root / "current").resolve().name
        return data
