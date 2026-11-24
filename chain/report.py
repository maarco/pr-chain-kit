"""pr-chain report: how well the chain works, read from the ledger, the per-PR STATE files and main's git history.

Read-only: it writes nothing (no ledger row, no STATE, no GitHub call). Figures that come from a guess are marked
"(guess)" in the output: the rest-cause buckets, and the revert / bug-fix follow-up match, which reads commit subjects
and touched files on main and cannot know why a commit was made.

Entry point: bin/pr-chain report [--json] [--since YYYY-MM-DD] [--no-git] [--main BRANCH]
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path

FOLLOWUP_DAYS = 14  # a fix commit on main this soon after a merge, touching the PR's files, counts as a follow-up (guess)
HOT_FILE_COMMITS = 8  # a file touched by more commits than this in the history is too shared to link a fix to one PR (guess)
DAY = 86400


def _epoch(ts):
    """Ledger rows carry local wall-clock time (time.strftime, no zone). mktime reads it in this machine's zone."""
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def _pr(v):
    """Ledger rows hold the PR as an int, or as a string of digits from some stages. Anything else is not a PR."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return None


def load_ledger(path):
    """(rows, bad_lines). A torn or hand-edited line is counted, never fatal."""
    rows, bad = [], 0
    p = Path(path)
    if not p.exists():
        return rows, bad
    with open(p, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                bad += 1
                continue
            if isinstance(r, dict):
                rows.append(r)
            else:
                bad += 1
    return rows, bad


def load_states(work):
    """{pr: STATE dict} for every prs/<n>/STATE.json that parses."""
    out = {}
    for sp in Path(work, "prs").glob("*/STATE.json"):
        if not sp.parent.name.isdigit():
            continue
        try:
            out[int(sp.parent.name)] = json.loads(sp.read_text())
        except (OSError, ValueError):
            continue
    return out


# Rest causes, bucketed from the `why` text of a RESTING end row (guess: the buckets are this file's, not the chain's).
REST_CAUSES = [
    ("same findings twice", re.compile(r"same findings", re.I)),
    ("fixer produced nothing usable", re.compile(r"nothing usable", re.I)),
    ("round cap reached", re.compile(r"round cap", re.I)),
    ("conflict with main", re.compile(r"conflict", re.I)),
    ("UI review failed", re.compile(r"\bUI\b", re.I)),
    ("audit failed on moved head", re.compile(r"audit of the moved head", re.I)),
    ("landing fixer pushed", re.compile(r"landing fixer", re.I)),
]


# ERROR end rows are script exceptions. Most are the chain's own plumbing, not the PR's code (guess: buckets are this file's).
ERROR_CAUSES = [
    ("fold: train worktree dirty", re.compile(r"train worktree is dirty", re.I)),
    ("fold: PR still open against a base", re.compile(r"is OPEN against", re.I)),
    ("git worktree add failed", re.compile(r"worktree add", re.I)),
    ("disk full", re.compile(r"No space left", re.I)),
    ("tests failed in fold", re.compile(r"short test summary|pytest", re.I)),
    ("gh/GitHub call failed", re.compile(r"GraphQL|gh pr", re.I)),
]


def _cause(table, why):
    for name, rx in table:
        if rx.search(why or ""):
            return name
    return "other"


def rest_cause(why):
    return _cause(REST_CAUSES, why)


def error_cause(why):
    return _cause(ERROR_CAUSES, why)


def _median(xs):
    return round(statistics.median(xs), 1) if xs else None


def _mean(xs):
    return round(statistics.fmean(xs), 1) if xs else None


def build(rows, states=None, commits=None, since=None):
    """The report as a plain dict. `rows` is the ledger in file order, `states` {pr: STATE}, `commits` the output of
    git_history() or None when git was not read. `since` is an epoch: rows older than it are ignored."""
    states = states or {}
    if since is not None:
        rows = [r for r in rows if (_epoch(r.get("ts")) or 0) >= since]

    by_pr = defaultdict(list)
    for r in rows:
        n = _pr(r.get("pr"))
        if n is not None:
            by_pr[n].append(r)

    final, attempts = {}, Counter()
    for n, rs in by_pr.items():
        for r in rs:
            if r.get("stage") == "end" and r.get("status"):
                attempts[r["status"]] += 1
                final[n] = r  # the last end row is where the PR was left
    end_mix = Counter(r["status"] for r in final.values())

    stale = sorted(n for n, st in states.items() if n in final and st.get("status") and st["status"] != final[n]["status"])

    merged = []
    for n in sorted(final):
        if final[n]["status"] != "MERGED":
            continue
        rs = by_pr[n]
        reviews = {r.get("round") for r in rs if r.get("stage") == "verdict" and r.get("kind") == "review" and r.get("round")}
        agent_secs = sum(float(r.get("secs") or 0) for r in rs if r.get("stage") == "agent")
        first = next((_epoch(r.get("ts")) for r in rs if _epoch(r.get("ts"))), None)
        end = _epoch(final[n].get("ts"))
        merged.append({
            "pr": n,
            "review_rounds": len(reviews),
            "agent_min": round(agent_secs / 60, 1),
            "wall_min": round((end - first) / 60, 1) if first and end else None,
            "rests": sum(1 for r in rs if r.get("stage") == "end" and r.get("status") == "RESTING"),
        })

    rests, errors = [], Counter()
    for n in sorted(by_pr):
        for r in by_pr[n]:
            if r.get("stage") != "end":
                continue
            if r.get("status") == "RESTING":
                rests.append({"pr": n, "ts": r.get("ts"), "cause": rest_cause(r.get("why")), "why": (r.get("why") or "")[:160]})
            elif r.get("status") == "ERROR":
                errors[error_cause(r.get("why"))] += 1

    out = {
        "rows": len(rows),
        "first_ts": rows[0].get("ts") if rows else None,
        "last_ts": rows[-1].get("ts") if rows else None,
        "prs_seen": len(by_pr),
        "prs_with_end": len(final),
        "end_mix": dict(end_mix.most_common()),
        "end_rows": dict(attempts.most_common()),
        "merged": merged,
        "merged_summary": {
            "count": len(merged),
            "review_rounds_median": _median([m["review_rounds"] for m in merged]),
            "review_rounds_mean": _mean([m["review_rounds"] for m in merged]),
            "agent_min_median": _median([m["agent_min"] for m in merged]),
            "agent_min_mean": _mean([m["agent_min"] for m in merged]),
            "wall_min_median": _median([m["wall_min"] for m in merged if m["wall_min"] is not None]),
        },
        "rests": {
            "total": len(rests),
            "prs": len({r["pr"] for r in rests}),
            "causes": dict(Counter(r["cause"] for r in rests).most_common()),
            "items": rests,
        },
        "errors": {"total": sum(errors.values()), "causes": dict(errors.most_common())},
        "data_checks": {
            "state_files": len(states),
            "state_vs_ledger_mismatch": stale,
            "ledger_prs_without_state": sorted(set(final) - set(states))[:50] if states else None,
            "rows_on_non_pr_keys": sum(1 for r in rows if r.get("pr") is not None and _pr(r.get("pr")) is None),
        },
        "followups": None,
    }
    if commits is not None:
        out["followups"] = followups(merged_prs=[m["pr"] for m in merged], merged_ts={m["pr"]: _epoch(final[m["pr"]].get("ts")) for m in merged}, commits=commits)
    return out


def git_history(checkout, branch="main", since=None):
    """Commits on `branch` (first parent), newest first: [{sha, epoch, subject, files}]. None if git cannot read it."""
    cmd = ["git", "-C", str(checkout), "log", "--first-parent", "--name-only", "--format=%x1e%H%x1f%ct%x1f%s", branch]
    if since:
        cmd.insert(-1, f"--since={since}")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if p.returncode != 0:
        return None
    out = []
    for block in p.stdout.split("\x1e"):
        if not block.strip():
            continue
        head, _, rest = block.partition("\n")
        parts = head.split("\x1f")
        if len(parts) != 3:
            continue
        sha, ct, subject = parts
        files = {f.strip() for f in rest.splitlines() if f.strip()}
        out.append({"sha": sha, "epoch": int(ct), "subject": subject, "files": files})
    return out


# A PR's commit on main: a squash ("Title (#N)") or a merge commit ("Merge pull request #N from owner/branch").
PR_COMMIT = re.compile(r"\(#(\d+)\)\s*$|^Merge pull request #(\d+)\b")
REVERT_PR = re.compile(r"#(\d+)\b")


def followups(merged_prs, merged_ts, commits):
    """Per merged PR: a revert on main, and fix commits within FOLLOWUP_DAYS that touch the PR's files (both guesses).
    The PR's own commit is the oldest commit on main that carries its number (squash or merge commit).
    Files touched by more than HOT_FILE_COMMITS commits are ignored for the match: a shared file links everything."""
    touched = Counter(f for c in commits for f in c["files"])
    hot = {f for f, k in touched.items() if k > HOT_FILE_COMMITS}
    by_number = {}
    for c in reversed(commits):  # oldest first: the commit that first landed the number is the PR's own
        m = PR_COMMIT.search(c["subject"])
        if m and not c["subject"].startswith("Revert"):
            by_number.setdefault(int(m.group(1) or m.group(2)), c)
    reverts = defaultdict(list)
    for c in commits:
        if c["subject"].startswith("Revert"):
            for m in REVERT_PR.finditer(c["subject"]):
                reverts[int(m.group(1))].append(c["sha"][:10])
    res = []
    for n in merged_prs:
        commit = by_number.get(n)
        item = {"pr": n, "commit": commit["sha"][:10] if commit else None, "reverted_by": reverts.get(n, []), "fix_followups": []}
        t0 = merged_ts.get(n) or (commit["epoch"] if commit else None)
        if commit and t0:
            files = commit["files"] - hot
            for c in commits:
                if c is commit or not (t0 < c["epoch"] <= t0 + FOLLOWUP_DAYS * DAY):
                    continue
                if files and re.search(r"\bfix", c["subject"], re.I) and (c["files"] - hot) & files:
                    item["fix_followups"].append({"sha": c["sha"][:10], "subject": c["subject"][:100]})
        res.append(item)
    return {
        "window_days": FOLLOWUP_DAYS,
        "hot_file_commits": HOT_FILE_COMMITS,
        "merged_with_commit": sum(1 for r in res if r["commit"]),
        "reverted": sum(1 for r in res if r["reverted_by"]),
        "with_fix_followup": sum(1 for r in res if r["fix_followups"]),
        "items": [r for r in res if r["reverted_by"] or r["fix_followups"]],
    }


def render(rep):
    L = []
    add = L.append
    add(f"pr-chain report: {rep['rows']} ledger rows, {rep['first_ts']} .. {rep['last_ts']}, {rep['prs_seen']} PRs seen, "
        f"{rep['prs_with_end']} with an end state")
    add("")
    add("End state per PR (last end row for each PR; a PR's retries are not counted here)")
    for k, v in rep["end_mix"].items():
        add(f"  {k:14} {v}")
    add("End rows, retries included: " + ", ".join(f"{k} {v}" for k, v in rep["end_rows"].items()))
    add("")
    ms = rep["merged_summary"]
    add(f"Merged PRs: {ms['count']}")
    add(f"  review rounds per PR   median {ms['review_rounds_median']}   mean {ms['review_rounds_mean']}")
    add(f"  agent minutes per PR   median {ms['agent_min_median']}   mean {ms['agent_min_mean']}  (sum of agent runs)")
    add(f"  wall minutes per PR    median {ms['wall_min_median']}  (first ledger row to merge; includes waiting, guess for multi-run PRs)")
    add("")
    rs = rep["rests"]
    add(f"Rests: {rs['total']} across {rs['prs']} PRs. Causes (guess: bucketed from the why text):")
    for k, v in rs["causes"].items():
        add(f"  {k:32} {v}")
    add("")
    fu = rep["followups"]
    if fu is None:
        add("Reverts and fix follow-ups: not read (no git checkout, or --no-git)")
    else:
        add(f"Reverts and fix follow-ups (guess: subject and file match on main, {fu['window_days']}-day window)")
        add(f"  merged PRs matched to a commit: {fu['merged_with_commit']}; reverted: {fu['reverted']}; "
            f"with a fix follow-up: {fu['with_fix_followup']}")
        for it in fu["items"][:40]:
            tags = []
            if it["reverted_by"]:
                tags.append("reverted by " + ", ".join(it["reverted_by"]))
            if it["fix_followups"]:
                tags.append(f"{len(it['fix_followups'])} fix commit(s): " + "; ".join(f["sha"] for f in it["fix_followups"][:3]))
            add(f"  #{it['pr']}: " + " | ".join(tags))
    add("")
    er = rep["errors"]
    add(f"Script errors (ERROR end rows, retries included): {er['total']} (guess: buckets from the why text)")
    for k, v in er["causes"].items():
        add(f"  {k:36} {v}")
    add("")
    dc = rep["data_checks"]
    add(f"Data checks: {dc['state_files']} STATE files; STATE status differs from the ledger's last end row for "
        f"PRs {dc['state_vs_ledger_mismatch'][:20] or 'none'}; rows on non-PR keys (issue builds, scout): {dc['rows_on_non_pr_keys']}")
    return "\n".join(L) + "\n"


def run(work, *, as_json=False, since_days=None, checkout=None, branch="main", use_git=True, out=None):
    """What `bin/pr-chain report` runs. Returns the process exit code."""
    import sys
    out = out or sys.stdout
    rows, bad = load_ledger(Path(work, "ledger.jsonl"))
    since = None
    if since_days is not None:
        since = time.time() - since_days * DAY
    rep_commits = None
    if use_git and checkout:
        since_arg = time.strftime("%Y-%m-%d", time.localtime(since - 30 * DAY)) if since else None
        rep_commits = git_history(checkout, branch, since_arg)
    rep = build(rows, load_states(work), commits=rep_commits, since=since)
    rep["bad_ledger_lines"] = bad
    if as_json:
        out.write(json.dumps(rep, indent=1, default=str) + "\n")
    else:
        out.write(render(rep))
        if bad:
            out.write(f"({bad} ledger line(s) did not parse and were skipped)\n")
    return 0
