# pr-chain design

Distilled from the original project diary (the diary itself stayed private). Everything here describes
built, tested behavior; the code and `chain/flow.json` are the source of truth.

## What it does

One PR at a time. A fresh agent runs every stage, so no agent grades or fixes its own work. The script —
never the agents — pushes, posts and merges.

1. `pick` ranks open PRs and skips the ones someone owns (running agent, live claim, uncommitted lane
   worktree, head pushed under `head_min_age_minutes`, `exclude.txt`). First free row = target; priority
   labels first, clean before DIRTY, smallest first.
2. Fold: if the branch lacks the base branch, `agent-kit/land/fold.py --push` merges it in (ordinary
   merge, no model).
3. REVIEW: a fresh read-only reviewer in a clean worktree at the exact head, scratch HOME. Writes
   `verdict.md`; first line PASS or FAIL, findings as `- P2 path:line - wrong - trigger`.
4. FAIL: a NEW fixer gets the findings, works red-first (failing test first, fix second) in its own
   worktree, commits locally. The script checks (clean tree, no changelog edit, lint on added lines,
   branch not moved) and pushes with an ordinary push.
5. A NEW reviewer reviews the new head. Loop to 3, cap `max_rounds` (4). Same files flagged twice
   running = the chain rests instead of thrashing.
6. PASS: a stronger AUDIT runs (cap `max_audits`, 2) — reads the verdicts and fix reports, checks each
   finding is fixed and each fix was red-first, then does its own review. Audit FAIL goes to a fixer.
7. UI PRs (diff matches `[ui] paths_regex`): a scratch app stack, a vision review with real screenshots,
   then the human gate (screenshots posted on the PR; the human-approved label lands it).
8. Required CI, then `land_pr.py`: approve as the non-author account, merge commit, match-head.

## The engine question

`chain/flow.json` is the chain's program: nodes, outcomes, limits, labels, per-agent call budgets.
`chain/chain_engine.py` can run the whole graph from it (`PR_CHAIN_ENGINE=flow`), while the default `py`
path runs the hand-written body; both produce the same end states. The engine exists because the
hand-written body grew a state machine inside `if` statements; `ENGINE.md` holds its proof steps.

## Drift: when the base branch moves mid-run

Other chains merge PRs while this one runs, so a PR goes BEHIND several times per run.

- A stage answers BEHIND: fold and run that stage again, up to `drift_tries` (3); then `MAIN-MOVING`.
- After the audit PASS and before every landing try: fold again. If the head moved only by merging the
  base (plus changelog-only commits), a short merge-only addendum (~3 minutes) covers the new head;
  anything else reruns the full audit.
- A base-only move after the human gate keeps the human approval; only a real push reruns the chain.
- A UI verdict carries to the new head when the diff between the two heads lists no UI file.

## The carried PASS

A new run looks for an earlier audit or addendum PASS with no FAIL after it: same head skips review and
audit; a base-only move gets the addendum. This is most of the saving on a busy repo — a review round
costs 10-20 minutes, an addendum 3.

## Failure economics

- Every FAIL costs a fixer round (10-20 min) and one of 4 rounds; a PASS that missed a defect ships it.
  The prompts tell agents this: be right, not safe.
- A verdict written before the newest prompt edit is retired and the head judged again
  (`judged_by_current_rules`): the rules it was judged by have changed.
- Identical findings twice, or a fixer with nothing usable, rests the PR instead of burning rounds.
- Red CI goes to the fixer BEFORE any review; a test the base also fails is rerun once
  (`main_flake_rerun`), not fixed.
- Unrelated findings (defects on main the PR does not touch) never fail a verdict; they are filed as
  priority-ranked issues and picked up by the builder.

## Guards

| Guard | Where |
|---|---|
| one chain per PR, one claim per PR, an `ops` lock for multi-PR commands | `<work>/locks/`, `<work>/claims/` |
| STOP file checked between stages | `pr-chain stop` |
| settings-file tripwire after every agent (digests, never values; stops only on damage) | `cfg_snapshot`/`cfg_damage` |
| wall-clock timeout per agent (`agent_timeout_minutes`), call budgets in the brief | `run_agent`, `CAPS` |
| agents get no GitHub credentials (empty `GH_CONFIG_DIR`, tokens stripped) and a scratch HOME | `run_agent` |
| no force push, no rebase, no admin merge, never `gpt-6-astra` | script + `land_pr.py` |
| verdict comments scrubbed of local paths | `scrub()` |
| outside contributors' PRs never touched | `[accounts] authors` |
| a finished chain removes its worktrees and code indexes | `cleanup_pr`, `bin/worktree-janitor` |

## Known weak spots

- Two UI reviewers of one diff shoot different states; the per-PR brief block narrows this, but nothing
  enforces a shot list.
- A UI fixer that ends DISPUTE puts the PR in RESTING; its evidence is not handed to the next UI reviewer
  the way a code fixer's dispute is.
- Nothing checks the "Not shot:" list in a UI verdict.
- Spend dollars on the board are an API-rate estimate, not a bill; subscription runs cost nothing per
  token and long-context surcharges are not modelled.
- `pick`'s owner check is a heuristic; a hand edit in an open editor is invisible to it (use exclude.txt).

## Not built (deliberately)

- Parallel chains over disjoint files (concurrency is `prs_at_once` focus PRs, on purpose).
- A hard token cap per agent (wall clock only; the call budget is prompt text).
- An advisor escalation when a fixer ends BLOCKED.
- A baseline-vs-chain pilot study.
