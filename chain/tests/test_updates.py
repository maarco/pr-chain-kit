#!/usr/bin/env python3
"""Transactional update contract tests use temporary managed installs and synthetic release archives."""
import io
import multiprocessing
import os
import pathlib
import sys
import tarfile
import tempfile
import urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from pr_chain_kit import updates
sys.path.insert(0, str(ROOT / "chain"))
import config as chain_config
assert chain_config.Cfg().updates_enabled is False, "automatic updates must default off"


def archive(version="0.2.0", *, complete=True):
    files = {
        "pr_chain_kit/__init__.py": f'__version__ = "{version}"\n',
        "pr_chain_kit/cli.py": 'from pathlib import Path\nimport sys\nprint(Path(__file__).resolve().parent.parent)\n',
        "pr_chain_kit/updates.py": "# fixture\n",
        "pr-chain.toml": "[repo]\nslug = 'owner/name'\n",
        "bin/pr-chain": "#!/bin/sh\n",
        "bin/pr-chain-loop": "#!/bin/sh\n",
        "bin/pr-chain-board": "#!/bin/sh\n",
        "chain/board/config.html": "board fixture\n",
        "chain/board/wiki.html": "wiki fixture\n",
        "chain/board/spend.html": "spend fixture\n",
        "chain/board/prs.html": "prs fixture\n",
        "chain/board/issues.html": "issues fixture\n",
        "chain/config.py": """from pathlib import Path
class C:
 work=Path('/tmp'); models_file=Path('/dev/null'); board_config=''; checkout=Path('/tmp'); worktree_root=Path('/tmp'); slug=''
CFG=C(); CFG_PATH=Path('/dev/null'); CONFIGURED=True; PROMPTS_USER=Path('/tmp'); PROMPTS_BASE=Path('/tmp')
def ensure_standalone(): pass
def load_flow(): return {'limits': {'prs_at_once': {'value': 1}}}
def gh_bin(): return 'gh'
def prompt_names(): return []
def prompt_file(name): return Path('/tmp') / name
""",
        "bin/pr-chain-board": (ROOT / "bin" / "pr-chain-board").read_text(),
    }
    if not complete:
        del files["bin/pr-chain-board"]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, value in files.items():
            data = value.encode()
            info = tarfile.TarInfo(f"maarco-pr-chain-kit-fixture/{name}")
            info.size = len(data)
            info.mode = 0o755 if name.startswith("bin/") else 0o644
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def fixture():
    base = pathlib.Path(tempfile.mkdtemp(prefix="pr-chain-updates-")).resolve()
    root = base / "managed"
    source = ROOT
    installed = updates.install(root, source, ROOT / "pr_chain_kit")
    assert installed == "v0.1.0"
    return base, root


def race_worker(root, release, barrier, calls):
    def fake_latest():
        with calls.get_lock():
            calls.value += 1
        return "v0.2.0", release
    updates.latest_release = fake_latest
    barrier.wait()
    updates.update(pathlib.Path(root))


def main():
    base, root = fixture()
    # The installed entrypoint hands its arguments to the real CLI unchanged (python -c already leaves them in sys.argv[1:]).
    entry = __import__("subprocess").run([str(root / "bin" / "pr-chain"), "path"], capture_output=True, text=True, timeout=15)
    assert entry.returncode == 0 and pathlib.Path(entry.stdout.strip()).resolve() == (root / "current").resolve(), entry.stdout + entry.stderr
    cfg = base / "pr-chain.toml"
    cfg.write_text("[repo]\nslug='owner/project'\n\n[updates]\nenabled=false\n")
    prompt = base / "prompts" / "review.md"
    prompt.parent.mkdir()
    prompt.write_text("operator prompt\n")
    work = base / "work" / "runs.json"
    work.parent.mkdir()
    work.write_text("operator work\n")
    credentials = base / "credentials.json"
    credentials.write_text("operator credentials\n")
    service = base / "pr-chain.service"
    service.write_text("operator service definition\n")
    old_config = os.environ.get("PR_CHAIN_CONFIG")
    os.environ["PR_CHAIN_CONFIG"] = str(cfg)
    cfg.chmod(0o600)
    old = updates.status(root)["installed_version"]
    assert old == "v0.1.0"
    assert "enabled=false" in cfg.read_text().replace(" ", "")
    updates.set_enabled(True)
    assert "enabled=true" in cfg.read_text().replace(" ", "")
    updates.set_enabled(False)
    assert prompt.read_text() == "operator prompt\n" and work.read_text() == "operator work\n"
    assert credentials.read_text() == "operator credentials\n" and service.read_text() == "operator service definition\n"
    assert cfg.stat().st_mode & 0o777 == 0o600, "enabling updates must preserve private config permissions"
    assert updates.root_for(ROOT / "pr_chain_kit") is None, "an ordinary checkout is not managed"

    original_latest = updates.latest_release
    updates.latest_release = lambda: ("", b"")
    assert updates.update(root)["status"] == "no-release"

    updates.latest_release = lambda: (_ for _ in ()).throw(urllib.error.URLError("offline fixture"))
    failed = updates.update(root)
    assert failed["status"] == "error" and "offline fixture" in failed["error"]
    assert updates.status(root)["installed_version"] == old

    updates.latest_release = lambda: ("v0.2.0", archive(complete=False))
    failed_verify = updates.update(root)
    assert failed_verify["status"] == "error" and "missing" in failed_verify["error"]
    assert updates.status(root)["installed_version"] == old

    updates.latest_release = lambda: ("v0.2.0", archive())
    real_validate = updates._validate
    def fail_new(root_arg, release, expected):
        if expected == "0.2.0":
            raise RuntimeError("post-activation verification fixture")
        return real_validate(root_arg, release, expected)
    updates._validate = fail_new
    failed_activate = updates.update(root)
    assert failed_activate["status"] == "error" and updates.status(root)["installed_version"] == old
    updates._validate = real_validate

    # A crash after the atomic pointer replacement leaves either complete release selected;
    # status recovers the actual target even if the completion record was not written.
    real_activate = updates._activate
    def interrupt_after_swap(root_arg, version):
        real_activate(root_arg, version)
        if version == "v0.2.0":
            raise KeyboardInterrupt()
    updates._activate = interrupt_after_swap
    updates.latest_release = lambda: ("v0.2.0", archive())
    try:
        updates.update(root)
    except KeyboardInterrupt:
        pass
    assert updates.status(root)["installed_version"] == "v0.2.0"
    updates._activate = real_activate

    # Two simultaneous loop processes can stage the same release; flock keeps current atomic and valid.
    base2, root2 = fixture()
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    calls = ctx.Value("i", 0)
    release = archive()
    workers = [ctx.Process(target=race_worker, args=(str(root2), release, barrier, calls)) for _ in range(2)]
    for p in workers: p.start()
    for p in workers: p.join(30)
    assert all(p.exitcode == 0 for p in workers), [p.exitcode for p in workers]
    assert updates.status(root2)["installed_version"] == "v0.2.0"
    assert (root2 / "current" / "bin" / "pr-chain-board").is_file()

    # The stable managed entrypoint is usable after activation and reports the current version.
    result = __import__("subprocess").run([str(root2 / "bin" / "pr-chain"), "path"],
                                          capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and pathlib.Path(result.stdout.strip()).resolve() == (root2 / "current").resolve(), result.stdout + result.stderr
    updates.latest_release = original_latest
    if old_config is None:
        os.environ.pop("PR_CHAIN_CONFIG", None)
    else:
        os.environ["PR_CHAIN_CONFIG"] = old_config
    print("updates: install/default-off/no-release/offline/verification rollback/interrupted activation/race/data preservation: ok")


if __name__ == "__main__":
    main()
