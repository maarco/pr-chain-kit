
## After your verdict: you also fix it (review-fix)

This replaces a separate fixer. Your verdict.md stays exactly as written; a NEW reviewer checks your fix afterwards, so you
never judge your own code.
- PASS: change nothing, commit nothing. Stop. (A reviewer who passes a PR and also changes it is turned into a FAIL by the script.)
- FAIL: fix every P0, P1 and P2 finding you listed, in {{WT}} (detached at {{HEAD}}). You may write files in it now. You
  already reproduced each finding with a probe, so:
  1. Find where the wrong value is produced. `grep -rn` every caller of the function you will change and every sibling with
     the same pattern; fix at the one place that closes all of them.
  2. Red-first per bug: turn your probe into a regression test in the repo's test folder, run it (it fails), commit it ALONE
     (`git commit -m "test(<scope>): ... (refs #<issue>)" -- <test file>`); then the fix in a second commit that never edits that test.
     Never weaken, skip or xfail an existing assertion. A finding no test can reach (doc wording, PR text) says so in its commit.
  3. PR text problems, or a body missing `Fixes #N`, `## Fix brief` (`Root cause:`, `Regression test:`) or `## How to see it`:
     write the complete corrected body to {{DIR}}/new-pr-body.md; the script posts it.
  4. Before your last commit: the field guide's local checks for every area you touched; read your own diff once
     (`git -C {{WT}} diff --stat {{HEAD}}..HEAD`); `git -C {{WT}} status --short` is clean.
  Field guide rules apply unchanged: HOME prefix on every command, explicit-path commits, no CHANGELOG edit, no AI attribution,
  no rebase, no force, no gh, no push.
- Write {{DIR}}/fix-report.md: the commit for each finding, the checks with their result lines. Its LAST line is exactly one of:
  `FIXED` (all P0/P1/P2 findings committed or written to new-pr-body.md, tree clean),
  `BLOCKED <one-line reason>` (a finding cannot be fixed inside this PR; give the evidence, a fresh reviewer weighs it),
  `BLOCKED POLICY <one-line reason>` (the only fix breaks a project rule, such as a history rewrite or force push, or the PR body
  itself declares a hold that only its owner can lift).
  Having no GitHub access is never a reason for BLOCKED or BLOCKED POLICY.
