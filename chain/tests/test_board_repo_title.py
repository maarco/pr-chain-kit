"""Every board page names its repo and shows open issue and PR totals."""
import importlib.machinery
import importlib.util
import os
import pathlib
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parents[2]
os.environ["PR_CHAIN_CONFIG"] = str(ROOT / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = tempfile.mkdtemp(prefix="kitwork--prc")
l = importlib.machinery.SourceFileLoader("board", str(ROOT / "bin/pr-chain-board"))
bd = importlib.util.module_from_spec(importlib.util.spec_from_loader("board", l)); l.exec_module(bd)

calls = []
def fake(key, ttl, args, fresh=False):
    calls.append(args)
    return {"data": {"repository": {"issues": {"totalCount": 509}, "pullRequests": {"totalCount": 12}}}}
bd.gh_json = fake
owner, name = bd.CONF.slug.split("/")
assert bd.repo_view() == {"repo": bd.CONF.slug, "issues": 509, "prs": 12}, bd.repo_view()
assert f"owner={owner}" in calls[0] and f"name={name}" in calls[0], calls[0]

def down(*a, **k):
    raise RuntimeError("gh failed")
bd.gh_json = down
assert bd.repo_view() == {"repo": bd.CONF.slug, "issues": None, "prs": None}, "gh down: the name still shows"

tag = '<script src="/repo.js" defer></script>'
assert bd.PAGE.count(tag) == 1, "board page"
for page in ("prs", "issues", "spend", "config", "wiki"):
    assert (ROOT / f"chain/board/{page}.html").read_text().count(tag) == 1, page
assert "/api/repo" in bd.REPO_JS.read_text()
print("PASS")
