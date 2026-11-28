"""Merge flow, dry run: plan one merge that lands every ready PR.

Ready PRs keep their heads. Every PR is checked against main, and every pair of PRs that
shares a path is merged with ``git merge-tree`` (seconds, no CI). A clash marks the later
PR for a fix, after any fix it depends on. The rest pair up by shared files into one
combined commit, which merges onto main as the window commit. Merges are git objects
only: nothing is pushed and nothing is written to GitHub.

    merge-flow                       # every ready PR
    merge-flow --prs 1450,1458       # just these
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IGNORED_PREFIXES = ("changes/",)  # one fragment per PR, never shared
CI_MINUTES = 7.2  # estimate: one CI run on the window
FIX_MINUTES = 8.4  # estimate: one fix agent run
FIX_AGENTS = 4
OPEN_PR_LIMIT = 1000
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "merge-flow",
    "GIT_AUTHOR_EMAIL": "merge-flow@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "merge-flow",
    "GIT_COMMITTER_EMAIL": "merge-flow@users.noreply.github.com",
}


@dataclass(frozen=True)
class Head:
    number: int
    sha: str
    files: frozenset[str] = frozenset()


def git(repo: Path, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True, text=True, env=_ENV)
    if proc.returncode not in ok:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.returncode, proc.stdout.strip()


def _nul_paths(repo: Path, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, list[str]]:
    proc = subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True, env=_ENV)
    if proc.returncode not in ok:
        raise RuntimeError(f"git {' '.join(args)} failed: {os.fsdecode(proc.stderr).strip()}")
    return proc.returncode, [os.fsdecode(name) for name in proc.stdout.split(b"\0") if name]


def changed_files(repo: Path, base: str, sha: str) -> frozenset[str]:
    """Files the PR changes since it branched from ``base``, changelog fragments left out."""
    fork = git(repo, "merge-base", base, sha)[1]
    # a rename lists both paths
    _, names = _nul_paths(repo, "diff", "--name-only", "-z", "--no-renames", fork, sha)
    return frozenset(name for name in names if not name.startswith(IGNORED_PREFIXES))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def share_path(a: frozenset[str], b: frozenset[str]) -> bool:
    """Whether changed paths are equal or one is a folder prefix of the other."""
    return any(x == y or x.startswith(y + "/") or y.startswith(x + "/") for x in a for y in b)


def clash(repo: Path, x: str, y: str) -> list[str] | None:
    """Files that clash when ``x`` and ``y`` merge, or None when they merge cleanly."""
    rc, records = _nul_paths(repo, "merge-tree", "--write-tree", "--name-only", "-z", "--no-messages", x, y, ok=(0, 1))
    return sorted(set(records[1:])) if rc else None


def merge(repo: Path, x: str, y: str, message: str) -> str | None:
    """A merge commit with parents ``x`` and ``y``, or None on a clash."""
    rc, out = git(repo, "merge-tree", "--write-tree", x, y, ok=(0, 1))
    if rc:
        return None
    return git(repo, "commit-tree", out.splitlines()[0], "-p", x, "-p", y, "-m", message)[1]


def _build_tree(repo: Path, heads: list[Head]) -> tuple[list[dict[str, Any]], list[list[list[int]]]]:
    """Pair the most similar nodes level by level until one combined commit is left."""
    nodes = [{"sha": h.sha, "files": h.files, "prs": [h.number]} for h in heads]
    failed: set[frozenset[int]] = set()
    levels: list[list[list[int]]] = []
    while len(nodes) > 1:
        pairs = sorted(
            ((i, j) for i in range(len(nodes)) for j in range(i + 1, len(nodes))),
            key=lambda p: (-jaccard(nodes[p[0]]["files"], nodes[p[1]]["files"]), p),
        )
        used: set[int] = set()
        merged: list[dict[str, Any]] = []
        for i, j in pairs:
            a, b = nodes[i], nodes[j]
            key = frozenset((min(a["prs"]), min(b["prs"])))
            if i in used or j in used or key in failed:
                continue
            sha = merge(repo, a["sha"], b["sha"], f"merge-flow: #{min(a['prs'])} + #{min(b['prs'])}")
            if sha is None:  # a clash only three or more PRs make together
                failed.add(key)
                continue
            used |= {i, j}
            merged.append({"sha": sha, "files": a["files"] | b["files"], "prs": sorted(a["prs"] + b["prs"])})
        if not merged:
            break
        levels.append([m["prs"] for m in merged])
        nodes = merged + [n for k, n in enumerate(nodes) if k not in used]
    return nodes, levels


def plan(repo: Path, base: str, heads: list[Head], agents: int = FIX_AGENTS) -> dict[str, Any]:
    """Plan one window: what clashes, what needs a fix and in which order, what lands now.

    ``heads`` are in ready order; of two clashing PRs the later one takes the fix.
    """
    already = [h.number for h in heads if git(repo, "merge-base", "--is-ancestor", h.sha, base, ok=(0, 1))[0] == 0]
    prs = [h.number for h in heads]
    heads = [h for h in heads if h.number not in already]
    clashes: list[dict[str, Any]] = []
    depth: dict[int, int] = {}
    for j, b in enumerate(heads):
        partners: list[int] = []
        files = clash(repo, base, b.sha)
        if files is not None:
            clashes.append({"prs": [b.number], "with": "main", "files": files})
        for a in heads[:j]:
            if share_path(a.files, b.files) and (pair := clash(repo, a.sha, b.sha)) is not None:
                clashes.append({"prs": [a.number, b.number], "files": pair})
                partners.append(a.number)
        if files is not None or partners:
            depth[b.number] = 1 + max((depth.get(p, 0) for p in partners), default=0)

    window = [h for h in heads if h.number not in depth]
    roots, levels = _build_tree(repo, window)
    roots.sort(key=lambda n: -len(n["prs"]))
    window_commit, landed, held = None, [], []
    current = base
    for node in roots:
        sha = merge(repo, current, node["sha"], f"merge-flow: land {len(node['prs'])} PRs")
        if sha is None:
            held += node["prs"]
            continue
        current, window_commit = sha, sha
        landed += node["prs"]

    per_depth = [sum(1 for d in depth.values() if d == lv) for lv in range(1, max(depth.values(), default=0) + 1)]
    fix_rounds = sum(math.ceil(n / agents) for n in per_depth)
    return {
        "base": base,
        "prs": prs,
        "already_on_base": sorted(already),
        "clashes": clashes,
        "needs_fix": [{"pr": n, "depth": d} for n, d in sorted(depth.items(), key=lambda kv: (kv[1], kv[0]))],
        "fix_rounds": fix_rounds,
        "levels": levels,
        "landed": sorted(landed),
        "held": sorted(held),
        "window_commit": window_commit,
        "minutes_to_land": round(fix_rounds * FIX_MINUTES + CI_MINUTES, 1),
    }


def _latest(checks: list[dict[str, Any]]) -> dict[str, bool]:
    """Pass or fail per check name, newest run only: a rerun replaces the run before it."""
    latest: dict[str, bool] = {}
    for check in sorted(
        checks, key=lambda c: (c.get("startedAt") is None, c.get("startedAt") or "")
    ):  # queued = newest
        name = check.get("name") or check.get("context") or ""
        latest[name] = (check.get("conclusion") or check.get("state")) == "SUCCESS"
    return latest


def green(rollup: list[dict[str, Any]], required: tuple[str, ...]) -> bool:
    """Every required check passes on its newest run; with no names, every check reported (at least one)."""
    results = _latest(rollup)
    need = set(required) or set(results)
    return bool(need) and all(results.get(name) for name in need)


def ready_prs(items: list[dict[str, Any]], required: tuple[str, ...] = (), base: str | None = None) -> list[int]:
    """Numbers of open PRs that are not drafts, are approved and pass every required check.

    With no ``required`` names, every check the PR reports is required. With ``base``, only PRs into that branch.
    """
    return sorted(
        item["number"]
        for item in items
        if not item.get("isDraft")
        and item.get("reviewDecision") == "APPROVED"
        and green(item.get("statusCheckRollup") or [], required)
        and (base is None or item.get("baseRefName") == base)
        and ("approvedCommits" not in item or item.get("headRefOid") in item["approvedCommits"])
    )


def verify(repo: Path, base: str, heads: dict[int, str], window: str) -> list[str]:
    """Problems that stop ``window`` from landing; empty when it is exactly ``heads`` merged onto ``base``."""
    problems = []
    if git(repo, "rev-parse", f"{window}^1", ok=(0, 128))[1] != base:
        problems.append(f"window {window[:9]} does not sit directly on base {base[:9]}")
    for n, sha in sorted(heads.items()):
        if git(repo, "merge-base", "--is-ancestor", sha, window, ok=(0, 1))[0]:
            problems.append(f"#{n} head {sha[:9]} is not in the window")
    # Beyond the listed heads, a window holds only the flow's own merges: one per head, none of them plain commits.
    rest = git(repo, "rev-list", "--parents", window, f"^{base}", *(f"^{sha}" for sha in heads.values()))[
        1
    ].splitlines()
    if len(rest) != len(heads) or any(len(line.split()) < 3 for line in rest):
        problems.append("the window brings in commits beyond the listed heads and the flow's merges")
    commits = {line.split()[0] for line in rest}
    allowed_parents = {base, *heads.values(), *commits}
    for line in rest:
        sha, *parents = line.split()
        clean_merge = len(parents) == 2 and all(parent in allowed_parents for parent in parents)
        if clean_merge:
            rc, merged_tree = git(repo, "merge-tree", "--write-tree", *parents, ok=(0, 1))
            clean_merge = not rc and bool(merged_tree.splitlines()) and merged_tree.splitlines()[0] == _tree(repo, sha)
        if not clean_merge:
            problems.append(f"the window carries a commit that is not a clean merge of approved heads: {sha[:9]}")
    replay = plan(repo, base, [Head(n, sha, changed_files(repo, base, sha)) for n, sha in sorted(heads.items())])
    if replay["landed"] != sorted(heads) or replay["window_commit"] is None:
        problems.append(f"the listed heads no longer merge cleanly: lands {replay['landed']}")
    elif _tree(repo, replay["window_commit"]) != _tree(repo, window):
        problems.append("the window's files differ from the listed heads merged onto base")
    return problems


def _tree(repo: Path, commit: str) -> str:
    return git(repo, "rev-parse", f"{commit}^{{tree}}")[1]


_MARKER = "<!-- merge-flow "


def window_body(base: str, heads: dict[int, str], issues: list[int] | None = None) -> str:
    rows = "\n".join(f"| #{n} | `{sha[:12]}` |" for n, sha in sorted(heads.items()))
    data = json.dumps({"base": base, "heads": {str(n): sha for n, sha in sorted(heads.items())}})
    refs = "\n".join(f"Refs #{n}" for n in sorted(set(issues or []) - set(heads)))
    issue_lines = f"{refs}\n\n" if refs else ""
    return (
        f"Lands {len(heads)} approved PRs in one merge onto `{base[:12]}`. Each PR keeps its reviewed head; "
        f"this PR adds no change of its own (checked by `merge-flow --land`).\n\n"
        f"{issue_lines}| PR | Head |\n| --- | --- |\n{rows}\n\n{_MARKER}{data} -->\n"
    )


def _linked_issues(repo: str, heads: dict[int, str]) -> list[int]:
    issues: set[int] = set()
    for number in heads:
        pr = json.loads(_gh("pr", "view", str(number), "--repo", repo, "--json", "closingIssuesReferences,body"))
        issues.update(item["number"] for item in pr.get("closingIssuesReferences", []))
        issues.update(int(match) for match in re.findall(r"\b(?:Fixes|Closes|Resolves|Refs)\s+#(\d+)\b", pr.get("body") or "", re.IGNORECASE))
    return sorted(issues - set(heads))


def parse_window_body(body: str) -> tuple[str, dict[int, str]] | None:
    start = body.find(_MARKER)
    if start < 0:
        return None
    data = json.loads(body[start + len(_MARKER) : body.index("-->", start)])
    return data["base"], {int(n): sha for n, sha in data["heads"].items()}


def _gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def open_window(root: Path, remote: str, repo: str, branch: str, result: dict[str, Any], heads: list[Head]) -> str:
    """Push the planned window commit and open its PR against ``branch``; returns the PR URL."""
    landed = {h.number: h.sha for h in heads if h.number in result["landed"]}
    head = f"merge-flow/window-{result['window_commit'][:12]}"
    title = "chore(merge-flow): land " + ", ".join(f"#{n}" for n in sorted(landed))
    if len(title) > 256:
        title = f"chore(merge-flow): land {len(landed)} PRs into {branch}"
        if len(title) > 256:
            title = f"chore(merge-flow): land {len(landed)} PRs"
    issues = _linked_issues(repo, landed)
    git(root, "push", "-q", "--no-follow-tags", remote, f"{result['window_commit']}:refs/heads/{head}")
    return _gh(
        "pr", "create", "--repo", repo, "--base", branch, "--head", head, "--title", title,
        "--body", window_body(result["base"], landed, issues),
    ).strip()  # fmt: skip


def land_window(root: Path, remote: str, repo: str, branch: str, number: int, checks: tuple[str, ...]) -> int:
    """Re-check a window PR against GitHub and git, then approve and merge it (run as the merging account)."""
    pr = json.loads(_gh("pr", "view", str(number), "--repo", repo, "--json", "body,state"))
    parsed = parse_window_body(pr["body"])
    if pr["state"] != "OPEN" or parsed is None:
        print(f"#{number} is not an open merge-flow window")
        return 1
    base, heads = parsed
    refspecs = [f"+refs/heads/{branch}:refs/merge-flow/base", f"+refs/pull/{number}/head:refs/merge-flow/window"]
    refspecs += [f"+refs/pull/{n}/head:refs/merge-flow/pr/{n}" for n in heads]
    fetch(root, remote, refspecs)
    window = git(root, "rev-parse", "refs/merge-flow/window")[1]
    problems = []
    if git(root, "rev-parse", "refs/merge-flow/base")[1] != base:
        problems.append(f"{branch} moved since the window was planned; plan a new window")
    for n, sha in sorted(heads.items()):
        if git(root, "rev-parse", f"refs/merge-flow/pr/{n}")[1] != sha:
            problems.append(f"#{n} has a new head since it was approved")
    problems += verify(root, base, heads, window)
    current = json.loads(
        _gh("pr", "view", str(number), "--repo", repo, "--json", "state,baseRefName,headRefOid,statusCheckRollup")
    )
    if current["state"] != "OPEN":
        problems.append(f"the window PR is no longer open (state: {current['state']})")
    if current["baseRefName"] != branch:
        problems.append(f"the window PR targets {current['baseRefName']}, not {branch}")
    if window != current["headRefOid"]:
        problems.append("the window head moved since its checks were read; run --land again")
    open_prs = _gh_open_prs(repo)
    not_ready = sorted(set(heads) - set(ready_prs(open_prs, checks, branch)))
    now = {item["number"]: item.get("headRefOid") for item in open_prs}
    for n, sha in sorted(heads.items()):
        if now.get(n) != sha:  # read with the readiness above, so a later push cannot slip in
            problems.append(f"#{n} on GitHub is not the approved head {sha[:9]} any more")
    if not_ready:
        problems.append("not ready any more: " + ", ".join(f"#{n}" for n in not_ready))
    if not green(current["statusCheckRollup"] or [], checks):
        problems.append("the window's required checks are not all green yet")
    if problems:
        print("\n".join(f"not landing #{number}: {p}" for p in problems))
        return 1
    note = f"Verified: exactly the approved heads of {', '.join(f'#{n}' for n in sorted(heads))} merged onto {branch}."
    _gh("pr", "review", str(number), "--repo", repo, "--approve", "--body", note)
    refused = None
    try:
        _gh(
            "pr", "merge", str(number), "--repo", repo, "--merge", "--match-head-commit", window
        )  # GitHub refuses a moved head
    except (subprocess.CalledProcessError, RuntimeError) as e:
        refused = e
    state = json.loads(_gh("pr", "view", str(number), "--repo", repo, "--json", "state"))["state"]
    if refused and state != "MERGED":  # an auto-merge bot can merge it on the approval, before the explicit merge
        raise refused
    if state == "MERGED":
        print(f"landed #{number}: " + ", ".join(f"#{n}" for n in sorted(heads)))
        return 0
    if state == "OPEN":
        print(f"window #{number} is queued in GitHub's merge queue and is not merged yet")
        return 2
    print(f"window #{number} has state {state}")
    return 1


def github_repo(url: str) -> str:
    """``owner/name`` from a GitHub remote URL (https, ssh or scp form)."""
    m = re.match(r"(?:(?:https?|ssh|git)://)?(?:[^@/]+@)?github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise ValueError(f"not a GitHub remote: {url}")
    return f"{m[1]}/{m[2]}"


def fetch(repo: Path, remote: str, refspecs: list[str]) -> None:
    """Fetch into ``refspecs`` only: no tags follow, no FETCH_HEAD."""
    git(repo, "fetch", "-q", "--no-tags", "--no-write-fetch-head", remote, *refspecs)


def _gh_open_prs(repo: str) -> list[dict[str, Any]]:
    fields = "number,isDraft,reviewDecision,statusCheckRollup,baseRefName,headRefOid"
    out = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", str(OPEN_PR_LIMIT), "--json", fields],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    items = json.loads(out)
    if len(items) >= OPEN_PR_LIMIT:
        raise SystemExit(f"open PR list reached the {OPEN_PR_LIMIT}-PR limit; the list may be incomplete")
    for item in items:  # gh pr list leaves review commits empty; the reviews API has them
        if item.get("reviewDecision") == "APPROVED" and not item.get("isDraft"):
            jq = '.[] | select(.state == "APPROVED") | .commit_id'
            approved = _gh("api", "--paginate", f"repos/{repo}/pulls/{item['number']}/reviews", "--jq", jq)
            item["approvedCommits"] = approved.split()
    return items


def _summary(result: dict[str, Any]) -> str:
    lines = [f"base {result['base'][:9]}, {len(result['prs'])} ready PRs"]
    if result["already_on_base"]:
        lines.append(f"already on base: {', '.join(f'#{n}' for n in result['already_on_base'])}")
    for c in result["clashes"]:
        who = " + ".join(f"#{n}" for n in c["prs"]) + (" vs main" if c.get("with") == "main" else "")
        lines.append(f"  clash {who}: {', '.join(c['files'])}")
    for fix in result["needs_fix"]:
        lines.append(f"  fix #{fix['pr']} (round {fix['depth']})")
    landed = ", ".join(f"#{n}" for n in result["landed"]) or "none"
    lines.append(f"lands now in one merge: {landed}")
    if result["held"]:
        lines.append(f"held (clash only in combination): {', '.join(f'#{n}' for n in result['held'])}")
    lines.append(f"fix rounds {result['fix_rounds']}, about {result['minutes_to_land']} min to land all")
    return "\n".join(lines)


def _pr_number(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError(f"a PR number is 1 or more, not {text}")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default="origin", help="GitHub remote to read PRs from and fetch")
    parser.add_argument("--base", default="main")
    parser.add_argument("--prs", help="comma-separated PR numbers in ready order; default: every ready PR")
    parser.add_argument("--checks", default="", help="comma-separated required check names; default: every check")
    parser.add_argument("--json", type=Path, help="also write the plan here")
    parser.add_argument("--open", action="store_true", help="push the window commit and open its PR")
    parser.add_argument("--land", type=_pr_number, metavar="PR", help="verify a window PR, then approve and merge it")
    args = parser.parse_args(argv)

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel")[1])
    repo = github_repo(git(root, "remote", "get-url", args.remote)[1])  # PRs listed and fetched from one repo
    checks = tuple(filter(None, args.checks.split(",")))
    if args.land:
        return land_window(root, args.remote, repo, args.base, args.land, checks)
    listed: dict[int, str] = {}  # head each ready PR had when it was judged ready
    if args.prs:
        numbers = [int(n) for n in args.prs.split(",")]
    else:
        items = _gh_open_prs(repo)
        numbers = ready_prs(items, checks, args.base)
        listed = {item["number"]: item.get("headRefOid", "") for item in items if item["number"] in numbers}
    refspecs = [f"+refs/heads/{args.base}:refs/merge-flow/base"]
    refspecs += [f"+refs/pull/{n}/head:refs/merge-flow/pr/{n}" for n in numbers]
    fetch(root, args.remote, refspecs)
    base = git(root, "rev-parse", "refs/merge-flow/base")[1]
    heads = []
    for n in numbers:
        sha = git(root, "rev-parse", f"refs/merge-flow/pr/{n}")[1]
        if listed.get(n, sha) != sha:
            print(f"skipped #{n}: it moved after it was judged ready")
            continue
        heads.append(Head(n, sha, changed_files(root, base, sha)))

    result = plan(root, base, heads)
    print(_summary(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    if args.open and result["window_commit"]:
        print(open_window(root, args.remote, repo, args.base, result, heads))
    return 0


if __name__ == "__main__":
    sys.exit(main())
