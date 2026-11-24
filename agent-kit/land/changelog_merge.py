#!/usr/bin/env python3
"""Resolve a changelog-only merge conflict: main's text plus the PR's own added bullets, nothing else.

Usage: changelog_merge.py <worktree>
WRITES: every configured changelog copy in the given worktree, and stages them
(`git add`). No GitHub writes, no commit.
Run in a worktree with `git merge origin/main` in progress (HEAD = PR side, MERGE_HEAD = main).
Exit 0 = both changelogs resolved and staged, 4 = needs a hand resolve (the PR edits or removes existing lines,
or a bullet lands outside [Unreleased]).
KIT_* variables: none (the worktree is the argument).
"""
import difflib
import pathlib
import subprocess
import sys
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "chain"))
import config  # noqa: E402
from config import CFG  # noqa: E402
config.ensure_standalone()
FILES = CFG.changelog_forbidden or ("CHANGELOG.md",)
wt = pathlib.Path(sys.argv[1])


def git(*args):
    return subprocess.run(["git", *args], cwd=wt, capture_output=True, text=True, check=True).stdout


def section_bounds(lines):
    """Index of '## [Unreleased]' and of the next '## ' heading."""
    start = next(i for i, l in enumerate(lines) if l.startswith("## [Unreleased]"))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def pr_additions(base, pr):
    """[(section heading, [lines])] the PR added under [Unreleased]; None if it changed anything else."""
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, pr, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag != "insert":
            return None
        start, end = section_bounds(pr)
        if not start < j1 <= end:
            return None
        heading = next((pr[k] for k in range(j1 - 1, start, -1) if pr[k].startswith("### ")), None)
        for line in pr[j1:j2]:
            if line.startswith("### "):  # the PR opened a section; main may lack it
                heading = line
                out.append((heading, []))
            elif heading is None:
                if line.strip():
                    return None  # a bullet above every section heading
            else:
                if not out or out[-1][0] != heading:
                    out.append((heading, []))
                out[-1][1].append(line)
    return out


_b = ["# Changelog\n", "\n", "## [Unreleased]\n", "\n", "### Fixed\n", "- a\n", "\n", "## [1.0]\n"]
_p = _b[:7] + ["### Added\n", "- b (#9).\n", "\n"] + _b[7:]
assert pr_additions(_b, _p) == [("### Added\n", ["- b (#9).\n", "\n"])], pr_additions(_b, _p)
assert pr_additions(_b, _b[:6] + ["- c\n"] + _b[6:]) == [("### Fixed\n", ["- c\n"])]


merge_base = git("merge-base", "HEAD", "MERGE_HEAD").strip()
results = {}
for f in FILES:
    base = git("show", f"{merge_base}:{f}").splitlines(keepends=True)
    pr = git("show", f"HEAD:{f}").splitlines(keepends=True)
    main = git("show", f"MERGE_HEAD:{f}").splitlines(keepends=True)
    adds = pr_additions(base, pr)
    if adds is None:
        sys.exit(print(f"HAND: the PR changes {f} beyond adding [Unreleased] bullets") or 4)
    start, end = section_bounds(main)
    for heading, block in adds:
        block = [l for l in block if l not in main[start:end]]
        if not block:
            continue
        at = next((i for i in range(start, end) if main[i] == heading), None)
        if at is None:  # a section main's [Unreleased] lacks: open it at the end of [Unreleased]
            block = [heading] + block + ["\n"]
            main[end:end] = block
            end += len(block)
            continue
        main[at + 1:at + 1] = block
        end += len(block)
    results[f] = "".join(main)

if results[FILES[0]] != results[FILES[1]]:
    sys.exit(print("HAND: the two changelogs differ after the resolve") or 4)
for f, text in results.items():
    (wt / f).write_text(text)
    git("add", f)
added = git("diff", "--cached", "--numstat", "MERGE_HEAD", "--", FILES[0]).split()
print(f"changelogs resolved: main + {added[0] if added else 0} PR line(s), removed {added[1] if added else 0}")
