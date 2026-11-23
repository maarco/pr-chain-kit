#!/usr/bin/env python3
"""plugin_api public wrappers for what a host used to reach through plugin_api._readers(): pr_diff, runs, run, save_config.
Each returns data or {"error": ...} and never raises. Child interpreter, scratch folders, a gh shim; no network."""
import json, os, pathlib, shutil, stat, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parents[2]
d = pathlib.Path(tempfile.mkdtemp(prefix="plugin-wrap--prc")).resolve()
(d / "checkout").mkdir()
shim = d / "shim"
shim.mkdir()
DIFF = "diff --git a/x.txt b/x.txt\\n--- a/x.txt\\n+++ b/x.txt\\n@@ -1 +1 @@\\n-old\\n+new\\n"
(shim / "gh").write_text(f'#!/bin/sh\nif [ "$1 $2 $3" = "pr diff 7" ]; then printf \'{DIFF}\'; exit 0; fi\n'
                         'echo "boom" >&2; exit 1\n')
(shim / "gh").chmod(0o755)
env = {k: v for k, v in os.environ.items() if not k.startswith(("KIT_", "PR_CHAIN_", "KOLLAB_", "GH_", "GITHUB_"))}
env.update(PATH=f"{shim}:{env['PATH']}", HOME=str(d / "home"))

CODE = f"""
import json, sys
sys.path.insert(0, {str(root / 'chain')!r})
import config, plugin_api as p
out = {{}}
def call(name, *a):
    try:
        r = getattr(p, name)(*a)
    except BaseException as e:
        r = "RAISED " + repr(e)
    return r
# unconfigured: answers, never raises
out["u_diff"], out["u_runs"], out["u_run"], out["u_save"] = call("pr_diff", 7), call("runs", "pr", 7), call("run", "x"), call("save_config", dict(save_prompt=1))
config.configure(dict(repo=dict(slug="octo/widgets", checkout={str(d / 'checkout')!r})), {str(d / 'data')!r})
out["diff"] = call("pr_diff", 7)
out["diff_bad_number"] = call("pr_diff", "abc")
out["diff_gh_fails"] = call("pr_diff", 8, True)
out["runs"] = call("runs", "pr", 7)
out["runs_issue"] = call("runs", "issue", 3)
out["runs_bad_kind"] = call("runs", "bogus", 1)
out["runs_bad_number"] = call("runs", "pr", "x")
out["run_bad"] = call("run", "../../etc/passwd")
out["run_missing"] = call("run", "7/nothing")
out["save_notdict"] = call("save_config", "nope")
out["save_unknown"] = call("save_config", dict(save_prompt=dict(name="nope.md", text="x")))
out["save_empty"] = call("save_config", dict(save_prompt=dict(name="fix.md", text="  ")))
text = (config.PROMPTS_BASE / "fix.md").read_text()
out["save_ok"] = call("save_config", dict(save_prompt=dict(name="fix.md", text=text + "one more line\\n")))
out["saved_file"] = (config.PROMPTS_USER / "fix.md").read_text().endswith("one more line\\n")
print(json.dumps(out, default=str))
"""
pr = subprocess.run([sys.executable, "-B", "-c", CODE], capture_output=True, text=True, env=env, cwd=d, timeout=120)
assert pr.returncode == 0, (pr.stdout + pr.stderr)[-1500:]
o = json.loads(pr.stdout.strip().splitlines()[-1])
for k, v in o.items():
    assert not (isinstance(v, str) and v.startswith("RAISED")), (k, v)
for k in ("u_diff", "u_runs", "u_run", "u_save"):
    assert isinstance(o[k], dict) and ("setup" in o[k] or "error" in o[k]), (k, o[k])
assert "x.txt" in json.dumps(o["diff"]) and "error" not in o["diff"], o["diff"]
for k in ("diff_bad_number", "diff_gh_fails", "runs_bad_kind", "runs_bad_number", "run_bad", "run_missing", "save_notdict",
          "save_unknown", "save_empty"):
    assert isinstance(o[k], dict) and o[k].get("error"), (k, o[k])
assert "groups" in o["runs"] and "groups" in o["runs_issue"], (o["runs"], o["runs_issue"])
assert "error" not in o["save_ok"] and o["saved_file"], o["save_ok"]
print("PASS")
