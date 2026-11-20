#!/usr/bin/env python3
"""An installed kit keeps the user's changes outside the package: [limits] in pr-chain.toml overrides flow.json, an
edited prompt lives beside the config and wins, an unedited prompt still comes from the kit, and the board's Save
Prompt writes the user's copy, never the kit's. Scratch dirs only."""
import json, pathlib, subprocess, sys, tempfile
root = pathlib.Path(__file__).resolve().parents[2]
d = pathlib.Path(tempfile.mkdtemp(prefix="pr-chain-user-")).resolve()
(d / "prompts").mkdir()
(d / "prompts" / "review.md").write_text("MINE {{CALLS_RULE}}\n")
base = (root / "chain/tests/test-config.toml").read_text()
(d / "pr-chain.toml").write_text(base + "\n[limits]\nprs_at_once = 7\n")
probe = f"""
import sys, json, pathlib, importlib.machinery, importlib.util
sys.path.insert(0, {str(root / 'chain')!r})
import config
assert config.load_flow()["limits"]["prs_at_once"]["value"] == 7, "limits override"
assert config.prompt_file("review.md").read_text().startswith("MINE"), "edited prompt wins"
assert config.prompt_file("fix.md") == config.PROMPTS_BASE / "fix.md", "unedited prompt comes from the kit"
l = importlib.machinery.SourceFileLoader("board", {str(root / 'bin/pr-chain-board')!r})
b = importlib.util.module_from_spec(importlib.util.spec_from_loader("board", l)); l.exec_module(b)
kit_fix = (config.PROMPTS_BASE / "fix.md").read_text()
ok, msg = b.apply_config({{"save_prompt": {{"name": "fix.md", "text": kit_fix + "extra line\\n"}}}})
assert ok, msg
assert (pathlib.Path({str(d)!r}) / "prompts" / "fix.md").read_text().endswith("extra line\\n"), "save writes the user copy"
assert (config.PROMPTS_BASE / "fix.md").read_text() == kit_fix, "kit copy untouched"
print("user overrides: ok")
"""
env = {"PATH": "/usr/bin:/bin", "HOME": str(d), "PR_CHAIN_CONFIG": str(d / "pr-chain.toml"), "KIT_WORK": str(d / "work")}
r = subprocess.run([sys.executable, "-B", "-c", probe], env=env, capture_output=True, text=True)
print(r.stdout.strip() or r.stderr[-800:])
(d / "pr-chain.toml").write_text(base + "\n[limits]\nnot_a_limit = 1\n")
r2 = subprocess.run([sys.executable, "-B", "-c", f"import sys; sys.path.insert(0, {str(root / 'chain')!r}); import config; config.load_flow()"],
                    env=env, capture_output=True, text=True)
bad_ok = r2.returncode != 0 and "not a flow.json limit" in r2.stderr
print("unknown limit is an error: " + ("ok" if bad_ok else "FAIL " + r2.stderr[-300:]))
sys.exit(0 if r.returncode == 0 and bad_ok else 1)
