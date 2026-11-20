"""Priority order for bin/pr-chain: pick_issue() and rank() take p0 before p3. Stubs only (no gh, no agents)."""
import atexit, importlib.machinery, importlib.util, os, pathlib, shutil, sys, tempfile, types

tmp = pathlib.Path(tempfile.mkdtemp(prefix="prio--prc"))
atexit.register(shutil.rmtree, tmp, True)
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt")
src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
sys.dont_write_bytecode = True
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader)); loader.exec_module(pc)
assert str(pc.WORK).startswith(str(tmp)), pc.WORK
fails = []
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

L = lambda *names: [{"name": n} for n in names]
check("label_prio p0", pc.label_prio(L("bug", "priority/p0")), 0)
check("label_prio none", pc.label_prio(L("bug")), 9)
check("label_prio plain names", pc.label_prio(["priority/p2", "priority/p1"]), 1)
check("linked_issues all sources", pc.linked_issues({"closingIssuesReferences": [{"number": 1}], "body": "Fixes #2",
      "title": "x (#3)", "headRefName": "fix/issue-4-y"}), {1, 2, 3, 4})
check("linked_issues missing fields", pc.linked_issues({}), set())

# ---- pick_issue: priority beats ready-for-agent; nothing else taken
issues = [
    {"number": 10, "title": "ready p3", "labels": L("bug", "priority/p3", "status/ready-for-agent"), "assignees": []},
    {"number": 20, "title": "p0 bug", "labels": L("bug", "priority/p0"), "assignees": []},
    {"number": 30, "title": "p1 ready", "labels": L("bug", "priority/p1", "status/ready-for-agent"), "assignees": []},
    {"number": 5, "title": "p1 not ready", "labels": L("bug", "priority/p1"), "assignees": []},
]
def fake_gh(*a):
    return issues if a[0] == "issue" else []
pc.gh_json = fake_gh
pc.sh = lambda *a, **k: types.SimpleNamespace(stdout="", returncode=0)
check("pick_issue takes p0 over a ready p3", pc.pick_issue(), 20)
issues.pop(1)
check("pick_issue: same level, ready-for-agent first", pc.pick_issue(), 30)
issues[:] = [i for i in issues if i["number"] != 30]
check("pick_issue: p1 before p3", pc.pick_issue(), 5)

# ---- pick_issue looks at every open issue, not a window of the first 60 (11-16: 66 free issues sat after row 60, nothing was built)
issues[:] = [{"number": 100 + n, "title": f"held {n}", "labels": L("bug", "priority/p1", "status/ready-for-human"), "assignees": []} for n in range(70)]
issues.append({"number": 500, "title": "free bug past row 60", "labels": L("bug", "priority/p3"), "assignees": []})
check("pick_issue finds the free issue after 70 held ones", pc.pick_issue(), 500)
check("pick_issue: the priority it records is that issue's own (p3)", pc.pick_issue.prio, 3)

# ---- rank: free first, then priority via linked issue, then size
pc.owner_reason = lambda p, ps: p.get("_owner")
def pr(n, size, **kw):
    return dict({"number": n, "files": [], "additions": size, "deletions": 0, "mergeStateStatus": "CLEAN",
                 "labels": [], "body": "", "title": f"pr {n}", "headRefName": f"b{n}", "closingIssuesReferences": []}, **kw)
prs = [pr(1, 5), pr(2, 500, body="Fixes #1154"), pr(3, 50, headRefName="fix/issue-77-x"),
       pr(4, 1, labels=L("priority/p1")), pr(5, 1, body="Fixes #1154", _owner="running")]
order = [r["number"] for r in pc.rank(prs, prios={1154: 0, 77: 2})]
check("rank: p0 big PR, then p1 label, then p2 link, then unlabelled, owned last", order, [2, 4, 3, 1, 5])
check("rank: unknown linked issue counts as no priority", pc.rank([pr(9, 1, body="Fixes #999")], prios={})[0]["prio"], 9)

# ---- auto: only a p0/p1 issue jumps ahead of a lower-priority PR
check("p0 issue before unlabelled PR", pc.issue_jumps_queue(9, 0), True)
check("p1 issue before p2 PR", pc.issue_jumps_queue(2, 1), True)
check("p1 issue waits for p1 PR", pc.issue_jumps_queue(1, 1), False)
check("p2 issue waits for unlabelled PR", pc.issue_jumps_queue(9, 2), False)
pc.rank = lambda prs, prios=None: [dict(number=7, prio=9, owner=None, size=1, mergeStateStatus="CLEAN", ui=False, author={"login": "bot"})]
pc.list_prs = lambda: []
check("cmd_pick records the best free PR priority", (pc.cmd_pick(types.SimpleNamespace(all=False)), pc.cmd_pick.prio), (7, 9))

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
