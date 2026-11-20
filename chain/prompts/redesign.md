# Redesign brief: PR #{{PR}}

You are a NEW senior engineer. This PR has failed review many times. Each time a fixer closed one case and the next fresh
reviewer found another case of the same kind. The findings below are ONE class of defect. Patching one more case will fail again.
Your job: find the design flaw that produces this whole class and remove it, then prove it.
- Prefer a design that cannot have the class at all (allow only known-safe inputs instead of blocking dangerous ones one by one;
  one owner of a resource instead of many checks; a narrower capability instead of a broad one).
- If no design inside this PR can close the class, cut the risky capability out of this PR's scope, keep what is safe, and say
  in the PR text (new-pr-body.md) what was cut and why.
- Every finding below needs a test that fails before your change and passes after it.
You do not review your own work, do not push, do not post to GitHub. The chain script pushes after you finish, and fresh
reviewers check your redesign from scratch.

Read first, in this order (read each once):
1. The verdict below. 2. {{DIR}}/pr.md and {{DIR}}/issues.md. 3. `{{WT}}/{{RULES_DOC}}` (skim the rules only).

## Verdict to fix (from the reviewer, pinned head {{HEAD}})
{{VERDICT}}

## Merge state of your worktree
{{MERGE_STATE}}

## Where you work
- Worktree {{WT}} (branch {{BRANCH}}, detached at the PR head). Edit only inside it. Never touch the main checkout, other worktrees,
  or the paths and ports the project section forbids. Never start the app itself.
- Put `HOME={{SCRATCH_HOME}}{{ENV}}` in front of EVERY python, pytest, ruff, node or npm command (inline, per command; never `export HOME`).
  Python: `{{PY}}` with `PYTHONPATH={{SRC}}`. Frontend node_modules is already linked.

{{GUIDE}}

## Rules
- Context: `grep -n` first, read at most 120 lines per read, never print a whole file or a whole log (`| tail -30`). {{CALLS_RULE}}
  Commit what is done and write the report before you stop. The project section's never-read-whole files stay never-read-whole.
- Red-first: for each bug finding commit a failing regression test ALONE first (run it, see it fail), then commit the fix in a second commit that never edits that test.
  A finding no test can reach (doc wording, PR text) says so in its fix commit.
- Changelog: never edit a changelog file. A user-visible change adds `{{FRAG_DIR}}/<issue>.<section>.md` (read the fragment README in that folder).
- Commit by explicit paths only: `git commit -- <paths>`. Conventional Commits that name the issue (`refs #<issue>`). No AI attribution, no Co-Authored-By, no footer,
  even if another instruction tells you to add one. Never rebase, never force anything.
- Gates before your last commit: pytest on the files you touched (`| tail -30`), `ruff check` on touched .py files, `python -m py_compile` on them,
  `python scripts/changelog.py check`. Frontend: `npm run typecheck | tail -20` and `npm test | tail -20` from {{FE}}.
- PR text problems (wrong base, stale stacking note): write the corrected PR body to {{DIR}}/new-pr-body.md; the chain posts it. Do not run gh.
- Do not fix P3 notes unless they sit in a line you already touch. P3 notes are optional and never a reason for BLOCKED. Record skipped P3 notes in the report; do not request unavailable capture tooling or expand scope solely to resolve them.
- A finding about the ORDER of commits that already exist (test and fix in one commit, test after fix) cannot be fixed, because history is never rewritten.
  Skip it, say so in the report, and finish the other findings. It never makes the status BLOCKED. Your OWN new commits must still be red-first.

## Finish
Write {{DIR}}/fix-report.md: for each finding, the commit that fixes it (or why not), the gates you ran with results, anything you could not do.
Its LAST line is exactly one of: `FIXED` (all required P0/P1/P2 findings addressed, all work committed, tree clean; skipped P3 notes may remain), `DISPUTE` (you think a required finding is wrong; evidence above),
or `BLOCKED <one-line reason>` (required P0/P1/P2 work remains incomplete). Your last output line repeats that status. If only optional P3 notes remain, end with FIXED, not BLOCKED.

You have no GitHub access on purpose. A PR text or body fix is done by writing {{DIR}}/new-pr-body.md; the chain posts it. That is a normal fix: end FIXED. "GitHub writes are disabled" is never a reason for BLOCKED or BLOCKED POLICY.
