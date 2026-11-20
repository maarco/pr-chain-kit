# Fixer brief: PR #{{PR}}, round {{ROUND}}

You are a NEW agent. A reviewer who is not you failed this PR. You close what the review found, completely, and nothing else.
You do not review your own work, push, post, approve or merge. The script checks and pushes after you finish, and a fresh
reviewer who has never seen your work judges the result.

Read first, in this order (read each once):
1. The verdict below. 2. {{DIR}}/pr.md and {{DIR}}/issues.md. 3. `{{WT}}/{{RULES_DOC}}` (skim the rules only).

## Verdict to fix (from the reviewer, pinned head {{HEAD}})
{{VERDICT}}

Evidence that came with this verdict (screenshots in `shots/`, the reviewer's probes in `probes/`, CI lines): {{VERDICT_DIR}}
A finding that names a `.png` is a UI finding: open that file with your image reader and look at it before you touch code.

## Merge state of your worktree
{{MERGE_STATE}}

## Where you work
Worktree {{WT}} (branch {{BRANCH}}, detached at the PR head). Edit only inside it. Your notes and report go in {{DIR}}.

{{GUIDE}}

## How a good fix here is done
The ways this stage fails: fixing the one case the reviewer named while its siblings stay broken (the next reviewer finds the
next case, and the PR burns its rounds); "fixing" a finding that was never real; ending BLOCKED over something optional.

1. Triage every finding before you edit anything. Start {{DIR}}/fix-report.md now with one line per finding and update it as you
   go, so a cut-off run still leaves a usable report. For each finding, run the trigger the reviewer gave and write down what happened:
   - `confirmed`: you saw the wrong behavior. Fix it.
   - `not reproduced`: the trigger does not show the defect on this head. Do not change code for it. Keep the command and its
     output; this is DISPUTE evidence.
   - `cannot fix here`: real, but no change inside this PR can close it (an OS limit that main shares, a rule that forbids the
     only fix). Keep the evidence.
   - P3: optional. Skip it unless it sits on a line you already change. A P3 never makes the status BLOCKED.
2. Find the cause, then the class. For a confirmed finding, find where the wrong value is produced, not where it is noticed.
   `grep -rn` every caller of the function you will change and every sibling file with the same pattern. Decide the one place
   where a change closes the finding for all of them. When this PR has failed before for the same kind of case, the design is
   the defect: prefer allowing only known-good inputs over blocking bad ones one at a time.
3. Red-first, per confirmed bug:
   a. Write the regression test. Run it. It must fail, for the reason in the finding. Copy the failing line into the report.
   b. `git add <test file> && git commit -m "test(<scope>): <what it proves> (refs #<issue>)" -- <test file>`.
   c. Write the fix. Run the test. It passes without you editing it.
   d. `git commit -m "fix(<scope>): <what changed> (refs #<issue>)" -- <source paths>`.
   Never weaken, skip, xfail or delete an existing assertion to get green; a test that guards real behavior and now fails
   means your fix is wrong. A finding no test can reach (doc wording, PR text) says so in its commit message.
4. A UI finding: the fix is in the component or style the shot shows. Reuse what is in `src/components/ui/` and the design
   tokens; plain text and lucide icons; no pills, accent bars or decorative borders. You cannot take screenshots; the next
   UI reviewer does. Say in the report exactly which element changed and which shot should now look different.
5. PR text: when a finding is about the PR body, or your change makes the body wrong, or the body lacks `Fixes #N`, a
   `## Fix brief` section with `Root cause:` and `Regression test:` lines, or a `## How to see it` section, write the complete
   corrected body to {{DIR}}/new-pr-body.md. That is a normal fix and ends FIXED.
6. Before you finish, in this order:
   - Run the field guide's local checks for every area you touched and paste each result line into the report.
   - `git -C {{WT}} diff --stat {{HEAD}}..HEAD`, then read your own diff once. Every hunk must belong to a finding. Remove
     anything else (debug prints, reformatting, unrelated cleanup) with a new commit, never by rewriting history.
   - `git -C {{WT}} status --short` shows nothing uncommitted.
7. Conflicts with main (see "Merge state"): resolve them first, keep both sides' intent, `git commit --no-edit`, rerun the tests
   of every conflicted file.

Rules that never bend: commit by explicit paths; never edit a changelog file (the project section names them); no AI
attribution; never rebase or force; the order of commits that already exist cannot be fixed and is never BLOCKED: skip it and say so.

## Finish
{{DIR}}/fix-report.md holds, per finding: its triage result, the commit that fixes it or the evidence for why not, then the
checks you ran with their result lines, then anything you could not do. Its LAST line is exactly one of these, and your last
output line repeats it:

| Last line | Use it when | What happens next |
|---|---|---|
| `FIXED` | every confirmed P0, P1 and P2 is committed (or written to new-pr-body.md) and the tree is clean. Skipped P3 notes are fine. | the script pushes; a fresh reviewer reviews the new head |
| `DISPUTE` | no confirmed finding remains because the required ones did not reproduce. Evidence is in the report. | the next reviewer weighs your evidence |
| `BLOCKED <one-line reason>` | a confirmed P0, P1 or P2 cannot be fixed inside this PR | the next reviewer weighs it; twice the same parks the PR for a person |
| `BLOCKED POLICY <one-line reason>` | the only fix breaks a project rule (history rewrite, force push), or the PR body declares a hold only its owner can lift | the PR is parked until a person acts |

Mixed result (some fixed, some disputed): commit the fixes, explain the disputed ones in the report, end `FIXED`.
Having no GitHub access is never a reason for BLOCKED or BLOCKED POLICY: every GitHub action has a file in the field guide.
A safety-rule slip of your own (a command run without the scratch `HOME=` prefix and the project section's extra variables) is never a reason either: rerun it correctly and go on.
