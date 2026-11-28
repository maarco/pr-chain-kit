#!/usr/bin/env python3
"""Approve, ready and merge one PR at an exact head. Stops on anything unexpected. Prints no tokens.

Usage: land_pr.py <number> <full head sha> <approver account> <reviewed sha> <reviewer and verdict source> [--approve-only]
--approve-only (batch landing): the same checks without the BEHIND/DIRTY stop, then the status and the approval (none
when the approver already approved this head), and exit 0 before marking ready or merging.
WRITES to GitHub: a review/independent commit status, an approval (as the approver), ready-for-review and the merge
(as KIT_MERGER); appends to $KIT_WORK/landings.jsonl.
Refuses unless the reviewed sha IS the head. Appends PR, head, base, reviewer, the CI run links of the required
checks on that exact head, and the merge commit to landings.jsonl (private audit evidence).
Reads: KIT_REPO, KIT_MERGER, KIT_CHECKS, KIT_WORK; env LAND_PAUSE (seconds to pause after ready, default 0).
"""
import calendar
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import kit  # noqa: E402

REPO = kit.REPO
REQUIRED = set(kit.CHECKS)
FIELDS = "state,isDraft,author,headRefOid,baseRefOid,mergeStateStatus,reviewDecision,statusCheckRollup,mergedBy,mergeCommit"
LOG = kit.work("landings.jsonl")
APPROVE_ONLY = "--approve-only" in sys.argv[6:]
number, head, approver, reviewed, reviewer = sys.argv[1:6]
if reviewed != head:
    sys.exit(f"STOP: reviewed {reviewed[:8]} is not the head {head[:8]}; the review must pin the exact head")


def gh(user, *args):
    proc = subprocess.run([kit.gh_bin(), *args], capture_output=True, text=True, env=kit.gh_env(user))
    if proc.returncode:
        sys.exit(f"STOP: gh {' '.join(args[:2])} failed: {proc.stderr[:300]}")
    return proc.stdout


def state():
    return json.loads(gh(approver, "pr", "view", number, "-R", REPO, "--json", FIELDS))


def results(s):
    return kit.check_results(s["statusCheckRollup"])


def latest_dod():
    """The newest dod/ready-for-review run on the head: (completed epoch or None, conclusion, summary)."""
    runs = json.loads(gh(approver, "api", f"repos/{REPO}/commits/{head}/check-runs?check_name=dod/ready-for-review"))
    runs = sorted(runs.get("check_runs", []), key=lambda r: r.get("started_at") or "")
    if not runs:
        return None, None, ""
    run = runs[-1]
    done = run.get("completed_at")
    stamp = calendar.timegm(time.strptime(done, "%Y-%m-%dT%H:%M:%SZ")) if done else None
    return stamp, run.get("conclusion"), ((run.get("output") or {}).get("summary") or "")[:400]


s = state()
if s["state"] != "OPEN" or s["headRefOid"] != head:
    sys.exit(f"STOP: #{number} is {s['state']} at {s['headRefOid']}, expected OPEN at {head}")
if s["author"]["login"] == approver:
    sys.exit(f"STOP: {approver} wrote #{number} and cannot approve it")
found = results(s)
missing = {name: found.get(name) for name in REQUIRED if found.get(name) != "SUCCESS"}
if missing:
    sys.exit(f"STOP: required checks not green on {head[:8]}: {missing}")
ci_runs = {c.get("name") or c.get("context"): c.get("detailsUrl") or c.get("targetUrl")
           for c in s["statusCheckRollup"] if (c.get("name") or c.get("context")) in REQUIRED}
print(f"#{number} required checks green on {head[:8]}:", "; ".join(f"{k} {v}" for k, v in sorted(ci_runs.items())))
if s["mergeStateStatus"] in ("BEHIND", "DIRTY") and not APPROVE_ONLY:
    sys.exit(f"STOP: #{number} is {s['mergeStateStatus']}")

gh(approver, "api", "-X", "POST", f"repos/{REPO}/statuses/{head}", "-f", "state=success",
   "-f", "context=review/independent", "-f", "description=Independent review passed at this head")
approved = APPROVE_ONLY and f"{approver} {head}" in gh(
    approver, "api", "--paginate", f"repos/{REPO}/pulls/{number}/reviews",
    "--jq", '.[] | select(.state == "APPROVED") | "\\(.user.login) \\(.commit_id)"').splitlines()
if not approved:
    gh(approver, "pr", "review", number, "-R", REPO, "--approve", "--body", f"Reviewed at {head}")
print("already approved by" if approved else "approved as", approver)
if s["isDraft"]:  # approve-only too: a live batch landed PRs that were ready and approved
    gh(kit.MERGER, "pr", "ready", number, "-R", REPO)
    print("marked ready")
if APPROVE_ONLY:
    sys.exit(0)
time.sleep(int(os.environ.get("LAND_PAUSE", "0")))  # gives the dry-run bot time to log its decision
ready_at = time.time()

end = time.time() + 300
while True:
    s = state()
    if s["headRefOid"] != head:
        sys.exit(f"STOP: head moved to {s['headRefOid']}")
    if s["mergeStateStatus"] == "CLEAN":
        break
    red = {k for k, v in results(s).items() if v in ("FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED")}
    if s["mergeStateStatus"] == "UNSTABLE" and not red:
        # Nothing is failing: UNSTABLE here is pending non-required checks (relay/dispatch-main-ci can
        # lag minutes after approval) or the by-design SKIPPED Auto-merge 'merge' job on PR heads.
        if time.time() > end:
            pending = sorted(k for k, v in results(s).items() if v not in ("SUCCESS", "SKIPPED", "NEUTRAL"))
            sys.exit(f"STOP: UNSTABLE with nothing failing; still pending: {pending}")
        time.sleep(15)
        continue
    if s["mergeStateStatus"] == "UNSTABLE" and red == {"dod/ready-for-review"}:
        # The gate re-runs when the PR turns ready; a red result from before then is stale.
        done, conclusion, summary = latest_dod()
        if done is not None and done >= ready_at - 5 and conclusion == "failure":
            sys.exit(f"STOP: dod/ready-for-review failed after ready: {summary}")
        if time.time() > end:
            sys.exit(f"STOP: dod/ready-for-review still red and not re-run since ready: {summary}")
        time.sleep(15)
        continue
    if s["mergeStateStatus"] in ("BEHIND", "DIRTY") or time.time() > end:
        waiting = {k: v for k, v in results(s).items() if v != "SUCCESS"}
        sys.exit(f"STOP: {s['mergeStateStatus']} {s['reviewDecision']}; not green: {waiting}")
    time.sleep(15)

# The merge bot merges an approved, ready, CLEAN PR on its own; merging here too races it
# ("Merge already in progress"). Wait for the bot, and merge directly only if it has not acted.
end = time.time() + 240
while time.time() < end:
    s = state()
    if s["state"] == "MERGED" or s["headRefOid"] != head:
        break
    time.sleep(15)
if s["state"] == "OPEN" and s["headRefOid"] == head:
    print("the merge bot did not merge within 4 min; merging directly")
    gh(kit.MERGER, "pr", "merge", number, "-R", REPO, "--merge", "--match-head-commit", head)
    time.sleep(3)
s = state()
merge = (s.get("mergeCommit") or {}).get("oid")
print(f"#{number} {s['state']} by {(s.get('mergedBy') or {}).get('login')} merge commit {merge}")
with open(LOG, "a") as fh:
    fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "pr": number, "head": head,
                         "base": s.get("baseRefOid"), "reviewed": reviewed, "reviewer": reviewer, "approver": approver,
                         "ci": ci_runs, "state": s["state"], "merge_commit": merge}) + "\n")
