"""Named commands (commands.json, stage value cmd:<name>) in pr-chain and the board's save/delete guards. Scratch files only."""
import atexit, importlib.machinery, importlib.util, json, os, pathlib, shutil, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="cmds--prc")); atexit.register(shutil.rmtree, tmp, True)
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["PR_CHAIN_MODELS_FILE"] = str(tmp / "models.json")
sys.dont_write_bytecode = True
def load(name, path):
    l = importlib.machinery.SourceFileLoader(name, str(path)); m = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, l)); l.exec_module(m); return m
pc = load("prchain", pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain")
bd = load("board", pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain-board")
assert pc.COMMANDS_FILE == tmp / "commands.json", pc.COMMANDS_FILE
bd.MODELS_FILE, bd.COMMANDS_FILE, bd.PROFILES_FILE = tmp / "models.json", tmp / "commands.json", tmp / "profiles.json"
fails = []
def check(name, got, want):
    ok = got == want; print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)
def exits(fn):
    try: fn(); return False
    except SystemExit: return True

luna = 'zsh -lic "codex exec -m gpt-6-luna -c model_reasoning_effort=high \'Read <brief.md> and follow it exactly.\' </dev/null"'
(tmp / "commands.json").write_text(json.dumps({"luna": luna, "noslot": "claude -p go"}))
(tmp / "models.json").write_text(json.dumps({"review": "cmd:luna"}))
(tmp / "profiles.json").write_text(json.dumps({"p1": {"fix": "cmd:luna"}}))

c = pc.resolve_cmd("cmd:luna")
check("label is the short model name", str(c), "codex:gpt-6-luna")
check("runner reads the label", pc.runner(c)[0], "codex")
check("brief path fills the slot", pc.agent_cmd(c, brief="/w/b.md")[2].endswith("'Read /w/b.md and follow it exactly.' </dev/null"), True)
check("plain model untouched", pc.resolve_cmd("haiku"), "haiku")
check("missing name exits", exits(lambda: pc.resolve_cmd("cmd:nope")), True)
check("no <brief.md> slot exits", exits(lambda: pc.resolve_cmd("cmd:noslot")), True)
check("Cmd serializes as its label", json.dumps({"m": c}), '{"m": "codex:gpt-6-luna"}')

check("board: save needs slot", bd.apply_config({"save_command": {"name": "x", "cmd": "claude -p go"}})[0], False)
check("board: astra refused", bd.apply_config({"save_command": {"name": "x", "cmd": "codex -m gpt-6-astra <brief.md>"}})[0], False)
check("board: bad name refused", bd.apply_config({"save_command": {"name": "a b", "cmd": "x <brief.md>"}})[0], False)
check("board: save ok", bd.apply_config({"save_command": {"name": "x", "cmd": "x <brief.md>"}})[0], True)
check("board: delete used by stage refused", bd.apply_config({"delete_command": "luna"})[0], False)
check("board: unknown cmd in stages refused", bd.validate_models({"review": "cmd:nope"}) is not None, True)
check("board: delete unused ok", bd.apply_config({"delete_command": "x"})[0], True)
check("board: file keeps the rest", sorted(json.loads((tmp / "commands.json").read_text())), ["luna", "noslot"])
print("PASS" if not fails else f"FAIL {len(fails)}"); sys.exit(1 if fails else 0)
