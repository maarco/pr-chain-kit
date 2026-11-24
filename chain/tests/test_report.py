#!/usr/bin/env python3
"""pr-chain report (chain/report.py): end states, rounds, minutes, rests, reverts and follow-ups from fixture rows.
One test builds a real git repo to check the git reader. No agents, no gh."""
import json, pathlib, shutil, subprocess, sys, tempfile, time

sys.dont_write_bytecode = True
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import report  # noqa: E402

fails = []
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok:
        fails.append(name)

def ts(day, hh=0, mm=0):
    return f"2026-11-{day:02d}T{hh:02d}:{mm:02d}:00"

ROWS = [
    # PR 10: two review rounds, one rest, then merged
    {"pr": 10, "stage": "agent", "role": "review", "secs": 120, "ts": ts(9, 0, 0)},
    {"pr": 10, "stage": "verdict", "kind": "review", "round": 1, "verdict": "FAIL", "ts": ts(9, 0, 3)},
    {"pr": 10, "stage": "agent", "role": "fix", "secs": 300, "ts": ts(9, 0, 10)},
    {"pr": 10, "stage": "verdict", "kind": "review", "round": 2, "verdict": "PASS", "ts": ts(9, 1, 0)},
    {"pr": 10, "stage": "verdict", "kind": "gate", "round": 2, "verdict": "PASS", "ts": ts(9, 1, 1)},
    {"pr": 10, "stage": "end", "status": "RESTING", "why": "the same findings came back two rounds in a row", "ts": ts(9, 2, 0)},
    {"pr": 10, "stage": "end", "status": "MERGED", "why": "MERGED", "ts": ts(9, 12, 0)},
    # PR "11" written as a string in one stage; ends CI-RED then RESTING; never merged
    {"pr": "11", "stage": "push", "round": 1, "ts": ts(10, 0, 0)},
    {"pr": "11", "stage": "end", "status": "CI-RED", "why": "", "ts": ts(10, 1, 0)},
    {"pr": "11", "stage": "end", "status": "RESTING", "why": "fixer produced nothing usable: fixer said NO-REPORT", "ts": ts(10, 2, 0)},
    {"pr": "11", "stage": "end", "status": "AWAIT-HUMAN", "why": "", "ts": ts(11, 0, 0)},
    # PR 12: merged, reverted later; PR 13: merged, one fix follow-up
    {"pr": 12, "stage": "agent", "role": "review", "secs": 60, "ts": ts(12, 0, 0)},
    {"pr": 12, "stage": "end", "status": "MERGED", "why": "MERGED", "ts": ts(12, 0, 30)},
    {"pr": 13, "stage": "agent", "role": "review", "secs": 60, "ts": ts(12, 1, 0)},
    {"pr": 13, "stage": "end", "status": "MERGED", "why": "MERGED", "ts": ts(12, 1, 30)},
    {"stage": "doctor", "kind": "stale-state", "ts": ts(12, 2, 0)},   # no PR: ignored
    {"pr": "x", "stage": "end", "status": "ERROR", "ts": ts(12, 2, 1)},  # not a number: counted in data checks
]

# Commits on main, newest first, as git_history() returns them.
COMMITS = [
    {"sha": "c4" * 20, "epoch": 0, "subject": "Fix crash in landing (#14)", "files": {"src/b.py"}},
    {"sha": "c3" * 20, "epoch": 0, "subject": 'Revert "Add thing (#12)"', "files": {"src/a.py"}},
    {"sha": "c2" * 20, "epoch": 0, "subject": "Add thing (#12)", "files": {"src/a.py"}},
    {"sha": "c1" * 20, "epoch": 0, "subject": "Add other (#13)", "files": {"src/b.py"}},
]
# Put commit times on the same local clock the ledger uses: the merge of 13 at day 12 01:30, the fix a week later.
COMMITS[0]["epoch"] = int(report._epoch(ts(19)))
COMMITS[1]["epoch"] = int(report._epoch(ts(13)))
COMMITS[2]["epoch"] = int(report._epoch(ts(12, 0, 30)))
COMMITS[3]["epoch"] = int(report._epoch(ts(12, 1, 30)))

rep = report.build(ROWS, states={10: {"status": "MERGED"}, 11: {"status": "WAITING-CI"}}, commits=COMMITS)
check("pr string coerced: 11 is one PR", rep["end_mix"].get("AWAIT-HUMAN"), 1)
check("end mix uses last end row per PR", rep["end_mix"], {"MERGED": 3, "AWAIT-HUMAN": 1})
check("non-PR key counted, not a PR", rep["data_checks"]["rows_on_non_pr_keys"], 1)
check("ERROR on a non-PR key is not counted", rep["errors"], {"total": 0, "causes": {}})
check("ERROR cause: dirty train worktree", report.error_cause("fold.py failed rc=3: STOP: train worktree is dirty; finish"), "fold: train worktree dirty")
check("retries counted in end_rows", rep["end_rows"].get("RESTING"), 2)
m = {x["pr"]: x for x in rep["merged"]}
check("PR 10 review rounds from verdict rows", m[10]["review_rounds"], 2)
check("PR 10 agent minutes", m[10]["agent_min"], 7.0)
check("PR 10 wall minutes", m[10]["wall_min"], 12 * 60 - 0)
check("PR 10 rests", m[10]["rests"], 1)
check("rest cause bucket", rep["rests"]["causes"], {"same findings twice": 1, "fixer produced nothing usable": 1})
check("state mismatch: PR 11 STATE vs ledger", rep["data_checks"]["state_vs_ledger_mismatch"], [11])
fu = rep["followups"]
check("revert detected on PR 12", [i["reverted_by"] for i in fu["items"] if i["pr"] == 12], [["c3" * 5]])
check("fix follow-up on PR 13 touching its file", [len(i["fix_followups"]) for i in fu["items"] if i["pr"] == 13], [1])
check("merged PRs matched to a commit", fu["merged_with_commit"], 2)
check("merge-commit subject maps to its PR", report.followups([21], {21: 0}, [{"sha": "d" * 40, "epoch": 5, "subject": "Merge pull request #21 from owner/fix/x", "files": {"a"}}])["merged_with_commit"], 1)
check("PR's own commit is the oldest with its number", report.followups([13], {13: report._epoch(ts(12, 1, 30))}, COMMITS)["items"][0]["commit"], "c1" * 5)

# A shared file touched by many commits does not link a fix to a PR
busy = [{"sha": f"{i:040x}", "epoch": 10 + i, "subject": f"Touch shared (#{100 + i})", "files": {"shared.py"}} for i in range(report.HOT_FILE_COMMITS + 2)]
busy.append({"sha": "f" * 40, "epoch": 100, "subject": "Fix something (#999)", "files": {"shared.py"}})
busy.append({"sha": "e" * 40, "epoch": 50, "subject": "Own change (#500)", "files": {"shared.py"}})
hot_fu = report.followups([500], {500: 50}, sorted(busy, key=lambda c: -c["epoch"]))
check("hot file: fix on a shared file is not a follow-up", hot_fu["items"], [])

# Reader on a real repo: first-parent order, subjects, files
d = pathlib.Path(tempfile.mkdtemp(prefix="report--prc"))
try:
    import os
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", "-b", "main", str(d)], check=True, env=env)
    for name, body in [("a.py", "1\n"), ("b.py", "2\n")]:
        (d / name).write_text(body)
        subprocess.run(["git", "-C", str(d), "add", name], check=True, env=env)
        subprocess.run(["git", "-C", str(d), "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"Add {name} (#7)"], check=True, env=env)
    hist = report.git_history(d, "main")
    check("git_history: two commits, newest first", [h["subject"] for h in hist], ["Add b.py (#7)", "Add a.py (#7)"])
    check("git_history: files per commit", hist[0]["files"], {"b.py"})
    check("git_history: missing branch gives None", report.git_history(d, "nope"), None)
finally:
    shutil.rmtree(d, ignore_errors=True)

# Empty ledger and a no-git run must not crash
empty = report.build([], states={}, commits=None)
check("empty ledger: no merged PRs", empty["merged_summary"]["count"], 0)
check("empty ledger: no medians", empty["merged_summary"]["review_rounds_median"], None)
check("no git: followups None", empty["followups"], None)

# The CLI path: a fixture work folder, text and JSON
w = pathlib.Path(tempfile.mkdtemp(prefix="reportwork--prc"))
try:
    (w / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in ROWS) + "\nnot json\n")
    import io
    buf = io.StringIO()
    rc = report.run(w, use_git=False, out=buf)
    check("run: exit 0", rc, 0)
    check("run: text names merged count", "Merged PRs: 3" in buf.getvalue(), True)
    check("run: bad line noted", "did not parse" in buf.getvalue(), True)
    buf = io.StringIO()
    report.run(w, as_json=True, use_git=False, out=buf)
    check("run: json parses", json.loads(buf.getvalue())["merged_summary"]["count"], 3)
finally:
    shutil.rmtree(w, ignore_errors=True)

print("\nall ok" if not fails else f"\n{len(fails)} failed: {fails}")
sys.exit(1 if fails else 0)
