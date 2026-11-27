"""The list exposes the recorded stop and the head it belongs to."""
import importlib.machinery
import importlib.util
from pathlib import Path

root = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader("blocker_board", str(root / "bin/pr-chain-board"))
board = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(board)

row = dict(number=1385, title="Example", author={}, headRefName="example", headRefOid="new-head",
           isDraft=True, state="OPEN", updatedAt="2026-01-01T00:00:00Z", additions=1,
           deletions=0, changedFiles=1, statusCheckRollup=[])
board.gh_json = lambda *args: [row]
board.gh_cache["list:open"] = (0, [])
board.tok_maps = lambda: ({},)
board.usd_maps = lambda: ({},)
board.chain_state = lambda n: {"status": "POLICY-HOLD", "why": "explain the hold", "head": "old-head"}
result = board.pr_list("open")["prs"][0]
assert (result["chain"], result["chain_why"], result["chain_head"], result["head"]) == (
    "POLICY-HOLD", "explain the hold", "old-head", "new-head")
print("PASS")
