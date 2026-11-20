"""Board Agent Output: claude -p stream-json transcripts parse; hook/system lines are skipped."""
import importlib.machinery, importlib.util, json, pathlib, sys
sys.dont_write_bytecode = True
l = importlib.machinery.SourceFileLoader("board", str(pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain-board"))
bd = importlib.util.module_from_spec(importlib.util.spec_from_loader("board", l)); l.exec_module(bd)
J = lambda o: json.dumps(o, separators=(",", ":"))
raw = [J({"type": "system", "subtype": "hook_started"}),
       J({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "plan"}, {"type": "text", "text": "hello"}]}}),
       J({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}}),
       J({"type": "user", "message": {"content": [{"type": "tool_result", "content": "a\nb\nc\nd"}]}}),
       J({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/x/y.py"}}]}}),
       "not json"]
ev = bd.stream_events(raw)
got = [(e["k"], e["t"]) for e in ev]
want = [("think", "plan"), ("say", "hello"), ("run", "git status"), ("out", "a"), ("out", "b"), ("out", "c"), ("tool", "Read /x/y.py")]
assert got == want, got
assert bd.log_epoch("no time") == 0.0 and bd.log_epoch("[00:00:01] x") > 0
# phone view: iOS Safari inflates small wrapped text unless text-size-adjust is pinned (replay: chat and tool-call text stayed big after the px sizes were cut)
assert "-webkit-text-size-adjust:100%" in bd.PAGE, "html must pin text-size-adjust"
import re
import os as _os, pathlib as _pl, tempfile as _tf
_os.environ.setdefault("PR_CHAIN_CONFIG", str(_pl.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml"))
_os.environ.setdefault("KIT_WORK", _tf.mkdtemp(prefix="kitwork--prc"))
mob = re.search(r"@media\(max-width:700px\)\{#feed\{[^\n]*\.e\.run,\.e\.out,\.e\.tool\{font-size:(\d+(?:\.\d+)?)px", bd.PAGE)
assert mob and float(mob.group(1)) <= 9, "tool-call text must be 9px or less at phone widths"
# an open tab reloads itself when the board restarts with a new page: token in the page, token in /api/state, one reload per token
assert bd.PAGE.count("const BUILD='__BUILD__'") == 1 and re.fullmatch(r"[0-9a-f]{10}", bd.BUILD), "page must embed the build token"
assert "s.build!==BUILD" in bd.PAGE and "sessionStorage.getItem('rl')" in bd.PAGE, "tick must reload once per new token"
print("PASS")
