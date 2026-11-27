#!/usr/bin/env python3
"""Thread cap: the loop, the board server and both board pages allow 1 to 8 threads, and agree."""
import atexit, importlib.machinery, importlib.util, json, pathlib, re, shutil, sys, tempfile
import os as _os
sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parents[2]
_os.environ.setdefault("PR_CHAIN_CONFIG", str(ROOT / "chain/tests/test-config.toml"))
_os.environ.setdefault("KIT_WORK", tempfile.mkdtemp(prefix="kitwork--prc"))
tmp = pathlib.Path(tempfile.mkdtemp(prefix="threads--prc"))
atexit.register(shutil.rmtree, tmp, True)


def load(name, rel):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / rel))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


loop, board = load("prloop", "bin/pr-chain-loop"), load("board", "bin/pr-chain-board")
cfg = tmp / "board-config.json"
loop.CFG = board.CFG_FILE = cfg

for want, saved in ((8, 8), (8, 9), (1, 0), (5, 5)):
    cfg.write_text(json.dumps({"threads": saved}))
    assert loop.threads() == want, (saved, loop.threads())
    assert board.read_threads() == want, (saved, board.read_threads())

ok, msg = board.apply_config({"threads": 8})
assert ok and json.loads(cfg.read_text())["threads"] == 8, (ok, msg)
for bad in (0, 9, True, "8"):
    ok, msg = board.apply_config({"threads": bad})
    assert not ok and msg == "threads must be an integer 1-8", (bad, ok, msg)

for page in ("chain/board/config.html", "chain/board/wiki.html"):
    html = (ROOT / page).read_text()
    assert re.search(r'id="threads"[^>]*min="1" max="8"', html), page
    assert "1-4" not in html, page

print("all thread cap checks passed")
