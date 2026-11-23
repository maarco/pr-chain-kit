#!/usr/bin/env python3
"""Stage-agent environment. Default: unchanged (parent env minus KIT_/PR_CHAIN_/KOLLAB_/GH_/GITHUB_, then [agent] env).
[agent] env_allowlist = [...]: the child gets only those names plus [agent] env, the GH_/GITHUB_ strip still applies, and
any value that looks like a GitHub token (ghp_, github_pat_, gho_, ghs_, ghu_) is dropped whatever the name.
[agent] home = "per-stage": every stage agent gets a fresh HOME under the work folder, created and removed by the kit."""
import atexit, importlib.machinery, importlib.util, os, pathlib, shutil, sys, tempfile

sys.dont_write_bytecode = True
root = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "chain"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="agent-env--prc")).resolve()
atexit.register(shutil.rmtree, tmp, True)
import config  # noqa: E402

PARENT = {"PATH": "/usr/bin:/bin", "HOME": "/srv/home", "LANG": "C", "TERM": "xterm", "EDITOR": "vi",
          "GH_TOKEN": "plain", "GITHUB_TOKEN": "plain", "KIT_REPO": "x", "KOLLAB_X": "x",
          "MY_KEY": "ghp_aaaa", "OTHER": "github_pat_bbbb", "A": "gho_c", "B": "ghs_d", "C": "ghu_e", "KEEP": "notatoken"}


def host(**agent):
    config.configure({"repo": {"slug": "octo/widgets", "checkout": "/srv/w"}, "agent": agent}, tmp / "data")
    return config.CFG


# 1. default: unchanged. No allowlist, so an odd-named token value is NOT filtered here (opt-in only).
host(env={"KOLLAB_NO_KEYRING": "1"})
env = config.stage_env(environ=PARENT)
assert env == dict(config.child_env(PARENT), KOLLAB_NO_KEYRING="1"), env
assert env["MY_KEY"] == "ghp_aaaa" and "GH_TOKEN" not in env

# 2. allowlist: only those names + [agent] env; strip still applies; token-looking values go whatever the name
host(env_allowlist=["PATH", "LANG", "GH_TOKEN", "MY_KEY", "OTHER", "A", "B", "C", "KEEP", "NOPE"],
     env={"AGENT_VAR": "1", "BAD": "github_pat_zzz", "FINE": "ok"})
env = config.stage_env(environ=PARENT)
assert env == {"PATH": "/usr/bin:/bin", "LANG": "C", "KEEP": "notatoken", "AGENT_VAR": "1", "FINE": "ok"}, env

# 2b. an empty allowlist is still opt-in: nothing but [agent] env
host(env_allowlist=[], env={"ONLY": "me"})
assert config.stage_env(environ=PARENT) == {"ONLY": "me"}

# 2c. extra (per-call values such as GH_CONFIG_DIR) are added and survive the allowlist strip
host(env_allowlist=["PATH"])
env = config.stage_env(extra={"GH_CONFIG_DIR": "/x/no-gh", "GIT_TERMINAL_PROMPT": "0"}, environ=PARENT)
assert env == {"PATH": "/usr/bin:/bin", "GH_CONFIG_DIR": "/x/no-gh", "GIT_TERMINAL_PROMPT": "0"}, env

# 3. per-stage HOME: fresh, distinct, under the work folder, created and removed by the kit
cfg = host(home="per-stage")
a, b = config.make_stage_home(), config.make_stage_home()
assert a != b and a.is_dir() and b.is_dir()
assert cfg.work in a.parents, (a, cfg.work)
(a / "marker").write_text("x")
assert config.stage_env(home=a, environ=PARENT)["HOME"] == str(a)
config.remove_stage_home(a)
config.remove_stage_home(b)
config.remove_stage_home(None)  # no-op
assert not a.exists() and not b.exists()
host()
assert config.make_stage_home() is None, "unset: no per-stage home"
assert config.stage_env(environ=PARENT)["HOME"] == "/srv/home"

# 4. run_agent uses all of it: a stub agent prints its environment and HOME
env_os = {k: v for k, v in os.environ.items() if not k.startswith(("KIT_", "PR_CHAIN_", "KOLLAB_", "GH_", "GITHUB_"))}
os.environ.clear(); os.environ.update(env_os)
os.environ.update(PR_CHAIN_CONFIG=str(root / "chain/tests/test-config.toml"), KIT_WORK=str(tmp / "work"),
                  PR_CHAIN_WT_ROOT=str(tmp / "wt"), STRAY_SECRET="ghp_leak", LANG="C")
loader = importlib.machinery.SourceFileLoader("prchain", str(root / "bin/pr-chain"))
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader)); loader.exec_module(pc)
dump = tmp / "dump.txt"
pc.runner = lambda m: ("stub", "stub")
pc.agent_cmd = lambda model, prompt, brief=None: ["sh", "-c", f'echo "HOME=$HOME" > {dump}; env >> {dump}']
pc.CFG.agent_env = {"FROM_CFG": "1"}


def run():
    d = tmp / "out"
    r = pc.run_agent("fix", None, tmp / "brief.md", tmp, d, 1)
    assert r["rc"] == 0, r
    return dump.read_text()


out = run()  # default: the stray token variable passes (unchanged behaviour), HOME is the parent's
assert "STRAY_SECRET=ghp_leak" in out and f"HOME={os.environ.get('HOME', '')}\n" in out, out
pc.CFG.env_allowlist = ["PATH", "STRAY_SECRET"]
pc.CFG.agent_home = "per-stage"
out = run()
assert "STRAY_SECRET" not in out and "FROM_CFG=1" in out, out
home = out.splitlines()[0][len("HOME="):]
assert home and home != os.environ.get("HOME") and str(pc.CFG.work) in home, home
assert not pathlib.Path(home).exists(), "the kit removes the per-stage HOME after the run"
print("PASS")
