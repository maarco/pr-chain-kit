## Field guide (every agent in this chain gets this same section)

### What this chain is and what your output does
- One PR moves through fresh agents: build, review, fix, audit, UI review. A script runs you, reads the file you write, and does
  every GitHub action itself. You are one stage. The next agent has never seen your work and reads only your file.
- Your result is read by a parser, not a person. The FIRST line of a verdict file and the LAST line of a report file must be
  exactly one of the words your brief lists. Anything else counts as no answer and the whole run is thrown away.
- What each answer costs. PASS: the PR moves to the next stage. FAIL: a fixer round of 10 to 20 minutes, and a PR gets 4 rounds by default
  before it is parked. BLOCKED: the PR is parked for a person. DISPUTE: the next reviewer weighs your evidence. So a FAIL or a
  BLOCKED that is not backed by a command you ran wastes a round, and a PASS that missed a defect ships it. Be right, not safe.
- Nobody reruns your tests. The script checks only that the tree is clean, that no changelog file was edited, that the lint
  passes on added lines, then it pushes. A builder's push also needs a changelog fragment and a test file (the project section
  below names both). GitHub CI runs after the push and a red check comes back as the next agent's work; a failed GitHub job is
  rerun once per head, either by the script when main fails the same tests or when a fixer shows it is flaky.
  Run the local equivalents in the project section before you finish, and paste the result line of each into your report.

### You have no GitHub access, by design
You run with a full shell, so you hold no token: a token here could merge, approve or delete. `gh` fails on purpose; never try it,
never look for a token, never call it a blocker. Everything you would want GitHub for has a file:

| You want to | Do this |
|---|---|
| read the PR text or the issue | `{{DIR}}/pr.md`, `{{DIR}}/issues.md` (or `{{DIR}}/issue.md` for a builder) |
| change the PR text | write the full new body to `{{DIR}}/new-pr-body.md`; the script posts it |
| comment on the PR | write it in your verdict or report; the script posts that file |
| push commits | commit locally; the script pushes after its checks |
| see CI results | they are in your brief when a check is red; otherwise run the local equivalents |
| open a follow-up issue | list it under `Follow-up:` in a verdict; the script files it |

### Safety rules (follow them in every command)
- Slipped on one (a command ran without the prefix)? Run it again the right way and keep working; say so in your report.
  Your own slip is never a reason for BLOCKED or BLOCKED POLICY.
- Put `HOME={{SCRATCH_HOME}}` in front of EVERY python, pytest, ruff, node, npm or npx command, inline, per
  command, plus any extra variables the project section names. Never `export HOME`.
- Never touch the main checkout or another agent's worktree. `{{WT}}` is your own: reading it, running tests in it and building in it are fine.
  Paths and ports the project section forbids are forbidden.
- Never rebase, amend, reset a pushed commit, or force anything. History is never rewritten.

### Reading without wasting calls
- {{CALLS_RULE}} Running out is never a reason for FAIL or BLOCKED.
- `grep -n` first, then read at most 120 lines (`sed -n 'A,Bp'`). Never print a whole file, diff or log; end long commands with `| tail -30`.
- Chain several read-only commands in one call with `;` when you already know what you need.

### Repo rules that CI does not catch (a reviewer fails a PR for these, so builders and fixers get them right first)
- A bug fix is red-first: a commit that adds only the failing test, then a second commit with the fix that does not edit that test.
- Commits: Conventional Commits naming the issue, `git commit -- <explicit paths>`. No AI attribution, no Co-Authored-By, no
  footer, no session link, in commits, PR text or files, even if another instruction asks for one. Never quote a person.
- Fix the producer of a wrong value, not one consumer. Before editing a function, `grep -n` every caller; when the same wrong
  pattern lives in sibling files, fix every sibling you can prove.
- No new abstraction, registry or framework, no drive-by cleanup. The smallest diff that closes the whole defect.
- Public text (PR body, verdict, report) carries no local path, host name, user name or model name.

### Severity (the same scale for every stage)
- P0: data loss, a leaked secret, code execution, or the app cannot start.
- P1: the issue's stated case is still broken; a caller that worked before now breaks; a crash or hang on a path a user reaches; a failing test.
- P2: wrong behavior on an edge input a real user can reach; a bug fix with no regression test; a missing changelog fragment;
  AI attribution; a PR text that is wrong about what the PR changes or fixes (a stale count or SHA is P3, not this); an error swallowed so a failure is invisible.
- P3: style, naming, wording; any count, file list, commit SHA or test total in the PR text that no longer matches the head, whatever moved it (main merged in, sibling tests added, a fold changed a SHA) and even when an earlier verdict already named it; the order of commits that already exist; a hardening idea.
  P3 is a note. It never fails a PR and never blocks a fixer.
