"""Kit side of the kollab plugin: the host entry point, per-install folders, seeded prompts, atomic state writes,
the read API, the unconfigured loop, and the child environment.

Standalone and stdlib only, like the other chain/tests: CI runs `python3 -B chain/tests/test_kollab_plugin.py`.
Every check that loads the kit runs in a child interpreter, so one check's environment never leaks into another.
"""
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parents[2]
CHAIN = ROOT / "chain"
sys.path.insert(0, str(CHAIN))
PREFIXES = ("KIT_", "PR_CHAIN_", "KOLLAB_", "GH_", "GITHUB_")

HOST = "import json, sys, config as c\nc.configure(json.load(open(sys.argv[1])), sys.argv[2])\n"
REPORT = (
    "print(json.dumps(dict(configured=c.CFG.configured, slug=c.CFG.slug, checkout=str(c.CFG.checkout), "
    "authors=list(c.CFG.authors), trusted=list(c.CFG.trusted), merger=c.CFG.merger, checks=list(c.CFG.checks), "
    "work=str(c.CFG.work), wt=str(c.CFG.worktree_root), train=str(c.CFG.train), prompts=str(c.PROMPTS_USER))))\n"
)
HOST_SETTINGS = {
    "repo": {"slug": "octo/widgets", "checkout": "/srv/widgets", "python": "/srv/widgets/.venv/bin/python"},
    "accounts": {"authors": ["alice", "bob"], "trusted": ["alice"], "merger": "carol", "checks": ["ci/tests"]},
}


def plugin_env(data):
    """The child's environment: no KIT_, PR_CHAIN_, KOLLAB_ or GitHub token variable from the developer's shell, and
    HOME and the XDG folders under the data folder, so a run never reads or writes the developer's real home."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(PREFIXES)}
    env.update(HOME=str(data), XDG_CONFIG_HOME=str(pathlib.Path(data, "config")),
               XDG_STATE_HOME=str(pathlib.Path(data, "state")))
    return env


def child(code, data, args=(), extra=None, cwd=CHAIN):
    """Run `code` in a fresh interpreter (plugin_env plus `extra`) and return the JSON it printed last."""
    env = plugin_env(data)
    env.update(extra or {})
    out = subprocess.run([sys.executable, "-B", "-c", code, *map(str, args)], cwd=str(cwd), env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_configure_takes_settings_and_ignores_environment():
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        settings = pathlib.Path(root, "settings.json")
        settings.write_text(json.dumps(HOST_SETTINGS))
        data = pathlib.Path(root, "data")
        leak = {"KIT_REPO": "env/leak", "PR_CHAIN_CONFIG": str(pathlib.Path(root, "missing.toml"))}
        got = child(HOST + REPORT, data, args=[settings, data], extra=leak)
        assert got["configured"] is True and got["slug"] == "octo/widgets", got
        assert got["checkout"] == "/srv/widgets" and got["merger"] == "carol", got
        assert got["authors"] == ["alice", "bob"] and got["trusted"] == ["alice"], got
        assert got["checks"] == ["ci/tests"], got
        assert got["work"] == str(data / "work") and got["wt"] == str(data / "wt"), got
        assert got["train"] == str(data / "pr-chain-train"), got
        assert got["prompts"] == str(data / "prompts"), got


def test_import_reads_no_environment_or_file():
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        toml = pathlib.Path(root, "pr-chain.toml")
        toml.write_text('[repo]\nslug = "file/repo"\n')
        got = child("import json, config as c\nprint(json.dumps(dict(configured=c.CFG.configured, slug=c.CFG.slug)))\n",
                    pathlib.Path(root, "data"), extra={"PR_CHAIN_CONFIG": str(toml), "KIT_REPO": "env/leak"})
        assert got == {"configured": False, "slug": ""}, got


def test_unset_host_configures_nothing_and_api_answers_setup():
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        data = pathlib.Path(root, "data")
        code = ("import json, sys, config as c, plugin_api as p\n"
                "c.configure({}, sys.argv[1])\n"
                "print(json.dumps(dict(state=p.state(), badge=p.badge())))\n")
        got = child(code, data, args=[data])
        assert got["state"]["configured"] is False and got["state"]["setup"], got
        assert got["badge"]["text"] == "" and got["badge"]["hint"], got


def test_package_data_resolves_from_a_copied_layout():
    with tempfile.TemporaryDirectory(prefix="prc-copy-") as root:
        kit = pathlib.Path(root, "pr_chain_kit", "kit")
        shutil.copytree(CHAIN, kit / "chain", ignore=shutil.ignore_patterns("tests", "__pycache__"))
        shutil.copy(ROOT / "pr-chain.toml", kit / "pr-chain.toml")
        code = ("import json, sys\nsys.path.insert(0, sys.argv[1])\nimport config as c\n"
                "print(json.dumps(dict(root=str(c.ROOT), flow=c.FLOW_FILE.is_file(), "
                "prompts=len(list(c.PROMPTS_BASE.glob('*.md'))))))\n")
        got = child(code, pathlib.Path(root, "data"), args=[kit / "chain"], cwd=root)
        assert pathlib.Path(got["root"]).resolve() == kit.resolve(), got
        assert got["flow"] and got["prompts"] > 0, got


def test_two_installs_on_one_host_keep_work_and_claims_apart():
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        settings = pathlib.Path(root, "settings.json")
        settings.write_text(json.dumps(HOST_SETTINGS))
        installs = {}
        for name in ("install-a", "install-b"):
            data = pathlib.Path(root, name)
            installs[name] = (data, child(HOST + REPORT, data, args=[settings, data]))
        (a, got_a), (b, got_b) = installs["install-a"], installs["install-b"]
        assert got_a["work"] == str(a / "work") and got_b["work"] == str(b / "work"), (got_a, got_b)
        assert got_a["wt"] == str(a / "wt") and got_b["wt"] == str(b / "wt"), (got_a, got_b)
        assert got_a["prompts"] == str(a / "prompts") and got_b["prompts"] == str(b / "prompts"), (got_a, got_b)


def test_prompts_seed_once_and_edits_survive():
    kit_names = sorted(p.name for p in (CHAIN / "prompts").glob("*.md"))
    assert kit_names, "the kit ships stage prompts"
    code = HOST + ("print(json.dumps(dict(names=sorted(p.name for p in c.PROMPTS_USER.glob('*.md')), "
                   "pick=str(c.prompt_file(%r)))))\n" % kit_names[1])
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        settings = pathlib.Path(root, "settings.json")
        settings.write_text(json.dumps(HOST_SETTINGS))
        data = pathlib.Path(root, "data")
        first = child(code, data, args=[settings, data])
        assert first["names"] == kit_names, first
        edited = pathlib.Path(data, "prompts", kit_names[0])
        edited.write_text("edited by the user")
        child(code, data, args=[settings, data])
        assert edited.read_text() == "edited by the user", "a reconfigure overwrote a user edit"
        pathlib.Path(data, "prompts", kit_names[1]).unlink()
        last = child(code, data, args=[settings, data])
        assert kit_names[1] not in last["names"], "a deleted prompt was seeded again"
        assert last["pick"] == str(CHAIN / "prompts" / kit_names[1]), last


def test_atomic_write_keeps_the_old_file_when_the_replace_fails():
    import atomic  # red until chain/atomic.py exists
    with tempfile.TemporaryDirectory(prefix="prc-atomic-") as d:
        target = pathlib.Path(d, "STATE.json")
        atomic.write_json_atomic(target, {"v": 1})
        real_replace = os.replace

        def failing_replace(src, dst):
            raise OSError("simulated failure during replace")

        os.replace = failing_replace
        try:
            try:
                atomic.write_json_atomic(target, {"v": 2})
                raise AssertionError("the failing replace must raise")
            except OSError:
                pass
        finally:
            os.replace = real_replace
        assert json.loads(target.read_text()) == {"v": 1}, target.read_text()
        assert [p.name for p in pathlib.Path(d).iterdir()] == ["STATE.json"], list(pathlib.Path(d).iterdir())


def test_bin_pr_chain_state_writes_use_the_atomic_helper():
    src = (ROOT / "bin/pr-chain").read_text()
    assert src.count("write_json_atomic(") >= 5, src.count("write_json_atomic(")
    assert not re.findall(r"\.write_text\(json\.dumps\(", src), "state JSON must go through write_json_atomic"


def test_plugin_api_serves_the_read_routes_without_fastapi():
    code = ("import json, sys\nsys.modules['fastapi'] = None\n"
            "import plugin_api as p\n"
            "print(json.dumps({n: callable(getattr(p, n, None)) for n in "
            "('state', 'prs', 'pr', 'issues', 'issue', 'spend', 'config', 'badge')}))\n")
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        routes = child(code, pathlib.Path(root, "data"))
    assert all(routes.values()), routes
    for path in CHAIN.glob("*.py"):
        assert not re.search(r"^\s*(from|import) fastapi\b", path.read_text(), re.M), path.name


def test_loop_exits_zero_with_a_repo_message_and_never_calls_gh():
    with tempfile.TemporaryDirectory(prefix="prc-host-") as root:
        data = pathlib.Path(root, "data")
        fake = pathlib.Path(root, "fakebin")
        fake.mkdir()
        marker = pathlib.Path(root, "gh-called")
        gh = fake / "gh"
        gh.write_text(f"#!/bin/sh\necho called >> '{marker}'\nexit 1\n")
        gh.chmod(0o755)
        env = plugin_env(data)
        env["PATH"] = f"{fake}{os.pathsep}/usr/bin{os.pathsep}/bin"
        proc = subprocess.Popen([sys.executable, "-B", str(ROOT / "bin/pr-chain-loop")], cwd=str(ROOT), env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            out, err = proc.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # the loop and anything it spawned
            proc.communicate()
            raise AssertionError("the loop kept running with no repo set; it should exit at once") from None
        assert proc.returncode == 0, err[-2000:]
        assert "repo" in (out + err).lower(), out[-500:]
        assert not marker.exists(), "gh was called with no repo set"


def test_child_env_strips_github_tokens_and_host_variables():
    import config  # red until config.child_env exists
    leaked = {"PATH": "/usr/bin:/bin", "HOME": "/srv/home", "GH_TOKEN": "x", "GITHUB_TOKEN": "x",
              "GH_CONFIG_DIR": "x", "KIT_REPO": "x", "PR_CHAIN_CONFIG": "x", "KOLLAB_HOST": "x"}
    env = config.child_env(environ=leaked)
    left = sorted(k for k in env if k.startswith(PREFIXES))
    assert not left, f"variables that should be stripped: {left}"
    assert env.get("PATH") == "/usr/bin:/bin", "PATH must survive"
    assert env.get("HOME") == "/srv/home", "HOME must survive so the stage agents keep their CLI logins"


def test_new_kit_files_carry_no_home_paths_or_personal_names():
    for name in ("chain/config.py", "chain/atomic.py", "chain/plugin_api.py"):
        text = (ROOT / name).read_text()
        for marker in ("/Users/", "/home/"):
            assert marker not in text, (name, marker)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:  # every check runs; one failure does not hide the rest
            failed.append(t.__name__)
            print(f"FAIL {t.__name__}: {type(e).__name__}: {str(e)[:300]}", flush=True)
        else:
            print(f"ok {t.__name__}", flush=True)
    if failed:
        print(f"kollab plugin: {len(failed)} of {len(tests)} checks failed")
        sys.exit(1)
    print(f"kollab plugin: {len(tests)} checks passed")
