#!/usr/bin/env python3
"""Merge the base branch in the train worktree, check it, and push normally.

Usage: fold.py <pr> [--push]
WRITES: a merge commit in the KIT_TRAIN worktree (detached head); with --push, also pushes it to the PR branch on GitHub.
Without --push it stops after the checks. A conflict stops with the merge left open for a hand resolve;
after resolving and committing, run it again with --push (it then skips the merge and only checks + pushes).
Changelog-only conflicts are resolved with the sibling changelog_merge.py. When main has scripts/changelog.py (the
changes/ entry files), a PR's own new CHANGELOG bullets then move into changes/<issue>.<section>.md in a separate commit
and both changelog copies go back to main's text; anything but pure new entries stops for a hand move.
Exit 0 = pushed (or checks passed without --push), 2 = conflict, 3 = checks failed, 1 = anything else.
Reads: KIT_REPO, KIT_TRAIN, KIT_PYTHON.
"""
import fnmatch
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "chain"))
import kit  # noqa: E402
import config  # noqa: E402
from config import CFG  # noqa: E402
config.ensure_standalone()
kit.configure(CFG)

R = kit.REPO
WT = kit.TRAIN
PY = kit.PYTHON
pr, push = sys.argv[1], "--push" in sys.argv


def run(*cmd, check=True, **kw):
    p = subprocess.run(cmd, cwd=WT, capture_output=True, text=True, **kw)
    if check and p.returncode:
        sys.exit(f"STOP: {' '.join(cmd[:4])}: {(p.stderr or p.stdout)[-600:]}")
    return p


def dev_run(*cmd, check=True, **kw):
    """Run a check in the merged worktree's CI-equivalent environment: [repo] run_prefix ({wt} = the worktree),
    else the configured python for `python ...` commands."""
    if CFG.run_prefix:
        return run(*[a.replace("{wt}", str(WT)) for a in CFG.run_prefix], *cmd, check=check, **kw)
    return run(str(PY) if cmd[0] == "python" else cmd[0], *cmd[1:], check=check, **kw)


COPIES = CFG.changelog_forbidden or ("CHANGELOG.md",)
BASE = CFG.branch
SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")


def unreleased_entries(text):
    """{section: [entry, ...]} under '## [Unreleased]'; an entry is a '- ' line plus its indented lines."""
    out, section, lines = {}, None, text.split("\n")
    if "## [Unreleased]" not in lines:
        return out
    for line in lines[lines.index("## [Unreleased]") + 1:]:
        if line.startswith("## "):
            break
        if line.startswith("### "):
            section = line[4:].strip().lower()
        elif line.startswith("- ") and section:
            out.setdefault(section, []).append(line)
        elif line.startswith("  ") and out.get(section):
            out[section][-1] += "\n" + line
    return out


def move_changelog_entries(issue):
    """Move the PR's own new CHANGELOG bullets into changes/<issue>.<section>.md and commit (main's check #855)."""
    if not CFG.fragment_dir or not (WT / "scripts/changelog.py").exists() or branch.startswith(("release/", "changelog/")):
        return
    if not run("git", "diff", "--name-only", main, "HEAD", "--", *COPIES).stdout.strip():
        return
    diff = run("git", "diff", "-U0", main, "HEAD", "--", COPIES[0]).stdout
    texts = [run("git", "show", f"HEAD:{c}").stdout for c in COPIES]
    if texts[0] != texts[1]:
        sys.exit(f"STOP: #{pr}'s two changelog copies differ; move its entry by hand")
    before = unreleased_entries(run("git", "show", f"{main}:{COPIES[0]}").stdout)
    after = unreleased_entries(texts[0])
    added = {s: [e for e in es if e not in before.get(s, [])] for s, es in after.items()}
    added = {s: es for s, es in added.items() if es}
    plus = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    minus = [ln for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---")]
    moved = [ln for es in added.values() for e in es for ln in e.split("\n")]
    extra = [ln for ln in plus if ln.strip() and not ln.startswith("### ") and ln not in moved]
    if minus or extra or not added or set(added) - set(SECTIONS):
        sys.exit(f"STOP: #{pr}'s changelog edit is not only new entries under known headings "
                 f"(removed {len(minus)} lines, {len(extra)} other lines, sections {sorted(added)}); move it by hand")
    for s, es in added.items():
        f = WT / CFG.fragment_dir / f"{issue}.{s}.md"
        if f.exists():
            sys.exit(f"STOP: changes/{f.name} already exists; move #{pr}'s entry by hand")
        f.write_text("\n".join(es) + "\n", encoding="utf-8")
    run("git", "restore", "--source", main, "--", *COPIES)
    run("git", "add", "--", "changes", *COPIES)
    run("git", "commit", "-q", "-m", f"docs(changelog): move this PR's entry to changes/ (#{issue})")
    # The check compares commits (base..HEAD), so it runs after the commit. A failure stops before any push.
    chk = run(str(PY), "scripts/changelog.py", "check", "--diff-base", main, "--branch", branch, check=False)
    if chk.returncode:
        sys.exit(f"STOP: changelog check after the move (local commit only, nothing pushed): "
                 f"{(chk.stderr or chk.stdout)[-400:]}")
    print(f"#{pr}: moved {len(moved)} changelog lines to changes/{issue}.<section>.md ({', '.join(added)})")


info = json.loads(subprocess.run(["gh", "pr", "view", pr, "-R", R, "--json",
                                  "headRefName,headRefOid,state,baseRefName,closingIssuesReferences"],
                                 capture_output=True, text=True, check=True).stdout)
branch, head = info["headRefName"], info["headRefOid"]
if info["state"] != "OPEN" or info["baseRefName"] != BASE:
    sys.exit(f"STOP: #{pr} is {info['state']} against {info['baseRefName']}")
run("git", "fetch", "-q", "origin", BASE, branch)
remote = run("git", "rev-parse", f"origin/{branch}").stdout.strip()
if remote != head:
    sys.exit(f"STOP: branch {branch} is at {remote[:8]}, PR says {head[:8]}")
main = run("git", "rev-parse", f"origin/{BASE}").stdout.strip()

cur = run("git", "rev-parse", "HEAD").stdout.strip()
parents = run("git", "rev-list", "--parents", "-n1", "HEAD").stdout.split()[1:]
resumed = parents == [head, main]  # a hand-resolved merge of this exact pair
anc = lambda a: run("git", "merge-base", "--is-ancestor", a, "HEAD", check=False).returncode == 0
if not resumed and cur != head and anc(head) and anc(main):
    sys.exit(f"STOP: HEAD {cur[:8]} already holds #{pr} + {BASE} plus local commits; push or drop them by hand first")
if not resumed:
    if run("git", "status", "--porcelain", check=False).stdout.strip():
        sys.exit("STOP: train worktree is dirty; finish or abort the open merge first")
    run("git", "checkout", "-q", "--detach", head)
    if run("git", "merge-base", "--is-ancestor", main, head, check=False).returncode == 0:
        print(f"#{pr} already contains origin/{BASE} {main[:8]}")
    else:
        m = run("git", "merge", "--no-edit", "-m", f"Merge origin/{BASE} into {branch}", f"origin/{BASE}", check=False)
        if m.returncode:
            files = run("git", "diff", "--name-only", "--diff-filter=U").stdout.split()
            print(f"CONFLICT #{pr} in: {' '.join(files)}")
            if not set(files) <= set(COPIES):
                sys.exit(2)
            fix = run(sys.executable, str(pathlib.Path(__file__).with_name("changelog_merge.py")), str(WT), check=False)
            print(fix.stdout.strip() or fix.stderr[-300:])
            if fix.returncode or run("git", "diff", "--name-only", "--diff-filter=U").stdout.strip():
                sys.exit(2)
            run("git", "commit", "-q", "--no-edit")
refs = info.get("closingIssuesReferences") or []
move_changelog_entries(refs[0]["number"] if refs else pr)
new = run("git", "rev-parse", "HEAD").stdout.strip()

changed = run("git", "diff", "--name-only", f"{main}...HEAD").stdout.split()
pys = [f for f in changed if f.endswith(".py") and (WT / f).exists()]
tests = [f for f in pys if pathlib.Path(f).name.startswith("test_") and "/tmux/" not in f]
src = [str(p) for g in CFG.src_globs for p in sorted(WT.glob(g))]
env = dict(os.environ, PYTHONPATH=os.pathsep.join(src + [str(WT)]), **CFG.agent_env)
if CFG.import_guard:
    where = dev_run("python", "-c", "import " + CFG.import_guard + "; print(" + CFG.import_guard + ".__file__)", env=env).stdout.strip()
    if not where.startswith(str(WT)):
        sys.exit(f"STOP: tests would import {where}, not the train worktree")
bad = []


def configured_checks():
    """[[merge_checks]] from the config: each runs once in the merged worktree when a changed file matches."""
    for c in CFG.merge_checks:
        files = [f for f in changed if (WT / f).exists() and any(fnmatch.fnmatch(f, m) or fnmatch.fnmatch(pathlib.Path(f).name, m) for m in c["match"])]
        if not files:
            continue
        cmd = [a for x in c["cmd"] for a in (files if x == "{files}" else [x.replace("{wt}", str(WT))])]
        t = dev_run(*cmd, check=False, env=env, timeout=c["timeout"])
        out = (t.stdout + t.stderr).strip()
        print(f"{c['name']}:", out.splitlines()[-1] if out else f"exit {t.returncode}")
        if t.returncode:
            bad.append(f"{c['name']}: " + out[-1500:])


if CFG.merge_checks:
    configured_checks()
    pys, tests = [], []  # the configured checks replace the Python default
if pys:
    # Same rule set as CI's standards-check (ci.yml): main itself carries style warnings, so a full ruff run stops on noise.
    lint = dev_run("python", "-m", "ruff", "check", "--select", "E9,F63,F7,F82", *pys, check=False)
    if lint.returncode:
        bad.append("ruff: " + lint.stdout[-400:])
# One pytest call per import root: two test folders can both be packages whose top name is `tests`,
# so collecting them in one process fails with "No module named ...".
groups = {}
for f in tests:
    d = (WT / f).parent
    while (d / "__init__.py").exists():
        d = d.parent
    groups.setdefault(str(d), []).append(f)
for part in groups.values():
    t = dev_run("python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *part, check=False, env=env, timeout=900)
    print("pytest:", t.stdout.strip().splitlines()[-1] if t.stdout.strip() else t.stderr[-300:])
    if t.returncode:
        bad.append("pytest: " + t.stdout[-1500:])
print(f"#{pr} {branch}: {head[:8]} + main {main[:8]} -> {new[:8]}; {len(changed)} files, "
      + (f"checks {', '.join(c['name'] for c in CFG.merge_checks)}" if CFG.merge_checks else f"ruff on {len(pys)}, tests {len(tests)}"))
if bad:
    print("\n".join(bad))
    sys.exit(3)
if push and new != head:
    run("git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    print(f"pushed {new}")
print(json.dumps({"pr": pr, "branch": branch, "old": head, "base": main, "new": new}))
