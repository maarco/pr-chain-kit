"""Merge-flow batch on the board: GET /api/batch (open batch, none, missing, broken), batch-* ledger rows without a
pr field next to the APIs the main page calls, and the Merge-Flow Batch panel in the page. Stubs only: no gh, no network."""
import http.server, importlib.machinery, importlib.util, json, os, pathlib, shutil, sys, tempfile, threading, time, urllib.request
sys.dont_write_bytecode = True
ROOT = pathlib.Path(__file__).resolve().parents[2]
WORK = pathlib.Path(tempfile.mkdtemp(prefix="batchwork--"))
os.environ["PR_CHAIN_CONFIG"] = str(ROOT / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(WORK)  # set before the import: the board reads its paths then, so nothing real is touched
l = importlib.machinery.SourceFileLoader("board", str(ROOT / "bin/pr-chain-board"))
bd = importlib.util.module_from_spec(importlib.util.spec_from_loader("board", l)); l.exec_module(bd)
bd.WORK = WORK


def put(rel, text):
    p = WORK / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# stubs: GitHub reads and the pr-chain pick run are replaced by these rows
def gh_pr(n, title):
    return {"number": n, "title": title, "author": {"login": "maarco"}, "headRefName": f"feat/{n}", "isDraft": False, "state": "OPEN",
            "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED", "labels": [], "mergedAt": None, "closedAt": None,
            "updatedAt": "2026-11-27T10:00:00Z", "additions": 1, "deletions": 0, "changedFiles": 1, "statusCheckRollup": []}


GH = {"list:open": [gh_pr(1510, "approved, batch-ready"), gh_pr(1511, "approved, batch-ready too"), gh_pr(1520, "stuck")],
      "ilist:open": [{"number": 40, "title": "an issue", "state": "OPEN", "updatedAt": "2026-11-27T10:00:00Z", "createdAt": "2026-11-20T10:00:00Z",
                      "labels": [], "assignees": [], "closedByPullRequestsReferences": [{"number": 1510}]}]}


def fake_gh_json(key, ttl, args, fresh=False):
    data = GH.get(key, [])
    bd.gh_cache[key] = (time.time(), data)  # pr_list and issue_list read the age from here
    return data


bd.gh_json = fake_gh_json
bd.refresh_pick = lambda: None
bd.table.update(loaded=True, at=time.time(), tried=time.time())  # no LiteLLM download

# work folder: three batch-ready PRs (one with a batch field), a STUCK PR, a broken STATE.json, one merged PR
for n, status, extra in [(1510, "BATCH-READY", {"batch": 1530}), (1511, "BATCH-READY", {}), (1512, "BATCH-READY", {}), (1520, "STUCK", {})]:
    put(f"prs/{n}/STATE.json", json.dumps(dict(status=status, **extra)))
put("prs/1521/STATE.json", "{not json")
put("prs/1501/STATE.json", json.dumps(dict(status="MERGED")))
# ledger: normal rows, 12 batch rows (seq 0..11), one batch row with no ts and no pr, a non-batch stage that must be ignored, one broken line
ROWS = [{"stage": "end", "pr": 1501, "status": "MERGED", "why": "", "ts": "2026-11-27T09:00:00"},
        {"stage": "verdict", "pr": 1510, "verdict": "PASS", "ts": "2026-11-27T09:30:00"}]
ROWS += [{"stage": "batch-plan" if i % 2 == 0 else "batch-ci", "batch": 1530, "prs": [1501, 1502], "result": f"run {i}", "seq": i,
          "ts": f"2026-11-27T10:{i:02d}:00"} for i in range(12)]
ROWS += [{"stage": "batch-split", "batch": None, "prs": [1502], "why": "red, split in halves"}, {"stage": "batchy", "seq": 99}]
put("ledger.jsonl", "\n".join(json.dumps(r) for r in ROWS) + "\n{broken ledger line\n")
OPEN = {"pr": 1530, "url": "https://github.com/o/r/pull/1530", "branch": "merge-flow/batch-20261128-041200",
        "base": "a" * 40, "commit": "b" * 40, "heads": {"1501": "c" * 40, "1502": "d" * 40},
        "opened": "2026-11-28T04:12:00Z", "queue": [[1503, 1504]], "held": [1505]}
put("batch.json", json.dumps(OPEN))

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), bd.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read().decode()


def batch_api():
    st, ctype, body = get("/api/batch")
    assert st == 200 and ctype.startswith("application/json"), (st, ctype)
    return json.loads(body)


# an open batch: the PR, its heads, queue and held lists; ready = BATCH-READY PRs only; recent = last 10 batch-* rows, newest first
b = batch_api()
assert b["open"]["pr"] == 1530 and b["open"]["heads"]["1501"] == "c" * 40 and b["open"]["queue"] == [[1503, 1504]] and b["open"]["held"] == [1505], b["open"]
assert b["ready"] == [1510, 1511, 1512], b["ready"]
assert len(b["recent"]) == 10 and b["recent"][0]["stage"] == "batch-split" and "ts" not in b["recent"][0], b["recent"][0]
assert [r["seq"] for r in b["recent"][1:]] == list(range(11, 2, -1)), [r.get("seq") for r in b["recent"]]
assert all(r["stage"].startswith("batch-") and "pr" not in r for r in b["recent"]), b["recent"]

# no batch open: pr null, a missing file, a broken file, a file that is not an object: open is null, nothing else changes
put("batch.json", json.dumps(dict(OPEN, pr=None)))
assert batch_api()["open"] is None
os.remove(WORK / "batch.json")
assert batch_api()["open"] is None
put("batch.json", '{"pr": 1530, "heads": ')
b = batch_api()
assert b["open"] is None and b["ready"] == [1510, 1511, 1512] and len(b["recent"]) == 10, b
put("batch.json", "[1530]")
assert batch_api()["open"] is None
put("batch.json", json.dumps(OPEN))

# no work folder at all: every part is empty, never a 500
saved, bd.WORK = bd.WORK, pathlib.Path(tempfile.mkdtemp(prefix="batchempty--"))
assert batch_api() == {"open": None, "ready": [], "recent": []}
bd.WORK = saved

# the APIs the main page calls still answer with batch rows in the ledger
s = json.loads(get("/api/state")[2])
assert "error" not in s, s.get("error")
assert [e["pr"] for e in s["ended"]] == [1501], s["ended"]
prs = json.loads(get("/api/prs?state=open")[2])
assert "error" not in prs, prs.get("error")
by = {p["n"]: p for p in prs["prs"]}
assert by[1510]["chain"] == "BATCH-READY" and by[1510]["batch"] == 1530 and by[1511]["chain"] == "BATCH-READY" and by[1511]["batch"] is None, by[1510]
assert by[1520]["chain"] == "STUCK", by[1520]
iss = json.loads(get("/api/issues?state=open")[2])
assert "error" not in iss and iss["issues"][0]["n"] == 40, iss

# the page carries the panel and the BATCH-READY wording
st, ctype, html = get("/")
assert st == 200 and "text/html" in ctype, (st, ctype)
assert "Merge-Flow Batch" in html and 'id="batch"' in html and "/api/batch" in html
assert "batch-ready" in bd.PAGE and "in batch #" in bd.PAGE and "BATCH-READY" in bd.PAGE

srv.shutdown()
srv.server_close()
shutil.rmtree(WORK, ignore_errors=True)
print("PASS")
