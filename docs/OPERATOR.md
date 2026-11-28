# pr-chain operator guide

One PR at a time. Every stage is a NEW agent, so nothing reviews or fixes its own work. The script (not
the agents) pushes, posts and merges. This guide is the generic version of the operator skill the chain
was run with in production. Commands are written `pr-chain ...` (installed with `uvx`/`uv tool install`); from
a clone use `python3 -B bin/pr-chain ...`. Every project-specific value lives in one `pr-chain.toml` (see
`chain/config.py`); kit paths below are relative to the kit (`pr-chain path` prints it).

## Where it lives

| Thing | Path |
|---|---|
| The command | `pr-chain` (`pr_chain_kit/cli.py`): `init`, `board`, `loop`, `janitor`, `path`; anything else goes to `bin/pr-chain` |
| The script | `bin/pr-chain` |
| Continuous runner | `bin/pr-chain-loop` |
| Read-only web board | `bin/pr-chain-board` → http://127.0.0.1:8790 |
| Prompts, model config, rates, tests, board pages | the kit's `chain/` (`prompts/`, `models.json`, `commands.json`, `rates.json`, `tests/`, `board/`) |
| Your changes (kept across upgrades) | beside your config: `models.json`, `commands.json`, `profiles.json` (`[paths] models_file`), `prompts/<name>.md` (edited prompts), `rates.json`, and `[limits]` inside `pr-chain.toml` |
| What every agent is told | `chain/prompts/field-guide.md` plus the per-project file `pr-chain.toml [project] field_guide` points to |
| Every limit, label, end state and agent budget | `chain/flow.json` (`limits`, `labels`, `end_states`, `agent_calls`, `intake`, `nodes`); `[limits]` in `pr-chain.toml` overrides any `limits` value |
| Flow engine, built and OFF | `chain/chain_engine.py`, `chain_steps.py`, design `chain/ENGINE.md` |
| Config | `PR_CHAIN_CONFIG`, else `pr-chain.local.toml` in a clone, else `~/.config/pr-chain/pr-chain.toml` (written by `pr-chain init`); template `pr-chain.toml`, worked example `examples/kollab/` |
| All run state | the `[paths] work` folder, default `~/.local/state/pr-chain/work` (ledger.jsonl, prs/<n>/, logs/, focus.json); worktrees in `~/.local/state/pr-chain/wt` |
| Shared helpers | `agent-kit/` (`kit.py`, `land/fold.py`, `land/land_pr.py`, `land/changelog_merge.py`) |
| Worktree cleanup for non-chain lanes | `bin/worktree-janitor` |
| Design history and known weak spots | `docs/design.md` |

## Stage order

0. Carry before review. A new run first looks for an earlier audit or addendum PASS of this PR with no
   FAIL after it. Same head: review and audit are skipped. Head moved only by merging the base branch:
   one merge-only addendum (~3 minutes) covers the new head. No earlier PASS: the full review runs.
1. Fold the base branch in if the branch is behind (ordinary merge, no model). Then one look at CI: a red
   required check sends the failing log lines to the fixer and no reviewer runs on that head. When every
   failing test also failed in the base's recent CI runs, the script reruns the failed jobs once instead
   of calling a fixer (`main_flake_rerun`).
2. REVIEW (read-only, clean copy at the exact head). Writes `verdict.md`; line 1 is `PASS` or `FAIL`.
3. FAIL → FIX (new agent, red-first, commits locally; the script checks and pushes). Back to 2. Cap: `--max-rounds` (default 4, `flow.json limits.max_rounds`).
4. PASS → GATE audit (cap `max_audits`, 2). Audit FAIL goes to a fixer and the loop continues.
5. UI PRs only (when `[ui] enabled`): a scratch app stack (the `[ui] stack_script`), then a vision review
   by a fresh agent whose brief carries a block about this one PR. Line 1 of `ui-verdict.md` is `PASS`,
   `FAIL` or `STACK-BROKEN`. FAIL → UI fixer and only the vision review reruns (cap `max_ui`, 3). PASS →
   the screenshots are posted on the PR and it waits for a person (`AWAIT-HUMAN`).
6. Required CI, then merge (approve as the non-author account from `[accounts] approver_of`, merge
   commit). A person's approval (the `[labels] human_approved` label after the gate comment) stands for
   the gate head and any base-only move of it on every later run. A push that changes no UI file (`[ui]
   paths_regex`) keeps it too: the run still reviews, audits and shoots the new head, then the gate moves
   to that head without asking again and says so on the PR. Checks still running: the PR ends
   `WAITING-CI`, its slot is freed, and `resume` lands it once CI is green.

## What each agent is told

A brief is the stage's file in `chain/prompts/` (or your edited copy in `prompts/` beside your config, which wins) with `field-guide.md` (plus the per-project guide from
`pr-chain.toml`) inlined where the file says `{{GUIDE}}`. `addendum.md` and `scout.md` carry no guide.
The brief an agent actually got is `brief.md` in its round folder under `<work>/prs/<n>/`.

- Agents have no GitHub access on purpose: they run with a full shell and approvals off, so the script
  strips `GH_TOKEN`/`GITHUB_TOKEN` and points `GH_CONFIG_DIR` at an empty folder. The script posts their
  files (`verdict.md` as PR comments, `new-pr-body.md` as the PR body, `Follow-up:` bullets as issues).
  A fixer that ends BLOCKED over GitHub access means the brief is wrong, not the PR.
- Agents run the local check commands from the project field guide and paste each result line into their
  report. The script checks only: clean tree, no changelog edit (the `[changelog] forbidden` list), lint
  on added lines, and for builders a `[changelog] fragment_dir` fragment plus a test file — then pushes.
- Unrelated findings become issues, ranked: each distinct `Follow-up:` bullet is filed as its own issue
  with a `priority/p<N>` label; `pick_issue()` sorts by that label first, so the harmful ones build first.
- Config-derived placeholders `render()` fills from `pr-chain.toml`: `{{ENV}}` (the `[agent] env`
  prefix), `{{RULES_DOC}}`, `{{FE}}`, `{{FRAG_DIR}}`, `{{PW_CACHE}}`.

## End states and what each needs

| Status | Meaning | Next action |
|---|---|---|
| `running` | a chain process owns it now | wait; read the newest round folder |
| `READY` | review and audit passed, no `--land` | `pr-chain land <n> --land` |
| `AWAIT-HUMAN` | UI screenshots are on the PR | a person adds the human-approved label or comments; `pr-chain resume` then lands it or sends the comment to a fixer |
| `RESTING` | the models could not finish it at this head (round/audit cap, same findings twice, UI cap, a conflict) | nothing by hand; `why` in STATE.json names the reason. Retried after 1, 2, 4, then 8 hours; a new head frees it at once |
| `STUCK` | rested several times with new findings in the same area; the chain filed a design issue and stopped | read `why` and the filed issue |
| `NEEDS-PERSON` | a new push lifted STUCK more than `stuck_lifts_max` times and it came back each time; label `chain/needs-person` | a person reads `why`; removing the label restarts it with fresh counters |
| `POLICY-HOLD` | the only fix breaks a project rule (history rewrite, force push), or the PR body declares a hold only its owner can lift | the owner decides; the chain does not retry |
| `BATCH-READY` | `[land] mode = "batch"`: approved at the audited head | nothing: `resume` lands it in a batch PR |
| `WAITING-CI` | everything passed, required checks still running; the slot is free | nothing: `resume` lands it when CI is green |
| `CI-TIMEOUT` / `CI-RED` | required checks did not go green (a base flake was already rerun once) | nothing: the loop runs it again (3 per head); red required checks go to the fixer before any review |
| `BUILD-FAILED` | the scratch UI stack did not build, or the UI reviewer answered `STACK-BROKEN` | the loop retries (3 per head); the fault is in the stack script or the app, not the PR |
| `LAND-STOPPED` | merge refused; `why` holds the reason (example: PR body has no `Fixes #N` link) | fix the named thing on the PR; `resume` lands it again once the PR changed, 3 times per head |
| `MAIN-MOVING` | still behind base after 3 folds | run it again later |
| `NO-VERDICT` / `UI-NO-VERDICT` / `ERROR` | the machinery failed, not the PR | nothing: `pick` hands it out again at the same head, 3 times per head; after that read that folder's `out.jsonl` tail |
| `NOT-OURS` | the PR author is not in `[accounts] authors` | never touched by design |
| `MERGED` | done | nothing |

Folder names under `prs/<n>/`: `r2-review` = round 2 review, `r1a1-gate` = round 1 audit 1, `r2-ui` =
round 2 vision review, `r1c-addendum` = the merge-only addendum that stood in for round 1's review and
audit. Suffixes `.old<digits>` / `.stale<digits>` are earlier tries kept for the record.

## Commands

```bash
pr-chain init                 # write ~/.config/pr-chain/pr-chain.toml and check gh, git and an agent CLI
pr-chain pick                 # which PR is next and who owns the rest (--all shows skipped rows)
pr-chain run 970 --land       # the loop: review/fix/audit/UI/CI/merge
pr-chain land 970 --land      # skip review/fix: gate + land at the current head
pr-chain resume               # PRs waiting on a person
pr-chain status               # one line per PR the chain has touched
pr-chain models               # effective model per stage and its source
pr-chain clean 970            # remove leftover worktrees (never --force)
pr-chain path                 # where the kit's own files live
```

- `run <pr>` without `--land` stops at `READY`. `--dry-run` launches nothing.
- `auto` takes the first free PR; with none it builds the first free issue into a DRAFT PR; with none it
  scouts. A `NO-BUILD` file in the work folder switches issue building off.
- Model for a stage: CLI flag > env `PR_CHAIN_<ROLE>_MODEL` > the models file (`[paths] models_file`, env `PR_CHAIN_MODELS_FILE`; `pr-chain init` copies the kit's to `~/.config/pr-chain/`) > built-in default. The shipped `chain/models.json` uses Claude CLI commands (Sonnet for work stages, Opus for the gate); the kollab setup (Codex) is `examples/kollab/models.json`.
  A `cmd:<name>` value runs the full command stored under that name in `chain/commands.json` (must
  contain `<brief.md>`; editable on the board /config page; profiles in `profiles.json`). Both sit beside the models file.

## Loop and board

Check one copy of each is enough: `pgrep -fl "pr-chain-loop|pr-chain-board"`.

```bash
nohup pr-chain loop >> "<work>/logs/loop.log" 2>&1 &
nohup pr-chain board 8790 >> "<work>/logs/board.log" 2>&1 &
```

- Start the loop from a shell whose `python3` is your system interpreter, not an activated project venv.
- Each loop lap: `resume`, `pick`, `run <first FREE PR> --land`; no free PR → `auto` if a slot is free,
  else look again in 10 minutes. A PR is tried at most twice per 6 hours.
- Threads: `<work>/board-config.json` `{"threads": 1..8}`, editable on the board. `pick` marks only
  `limits.prs_at_once` PRs free (set it with `[limits] prs_at_once` in `pr-chain.toml`); with 1, threads above 1 run nothing.
- The board (pages `/`, `/prs`, `/issues`, `/spend`, `/wiki`, `/config`; JSON under `/api/`) never writes to GitHub.
  Save Prompt on `/wiki` writes your copy in `prompts/` beside your config (old text in `prompts/.history/`);
  from a clone it edits `chain/prompts/` directly.

## Stable release updates

Updates default off and require an explicit managed installation. Ordinary checkouts, `uvx`, `uv tool`, and `pipx`
installs are unsupported for automatic replacement and remain untouched. Create a separate managed installation,
put its `bin/` directory on PATH, and point the loop/board service at that command:

```bash
pr-chain updates install ~/.local/share/pr-chain/managed
export PATH="$HOME/.local/share/pr-chain/managed/bin:$PATH"
pr-chain updates enable
pr-chain updates status
```

The destination must not already exist. If your existing setup uses `PR_CHAIN_CONFIG`, keep that environment value
for the managed command; a checkout-local `pr-chain.local.toml` is not copied into the managed release.

An enabled managed loop checks the latest stable GitHub release at most every six hours. Loops serialize staging
and activation. New work uses the activated immutable version; active chains stay pinned to the version they started
with, then an idle loop replaces itself on the new version. A running board stays on its starting version until
restarted. Previous versions are retained. `pr-chain updates check` retries immediately; `pr-chain updates disable`
turns automatic checks off. Status reports installed and available versions and any error. See [the managed update
spec](specs/17-managed-updates.md) for the trust, recovery and unsupported-platform details.

## Guards

- Ours only: PRs whose author is not in `[accounts] authors` are never touched (`NOT-OURS`).
- One chain per PR (`<work>/locks/<pr>`), one shared `ops` lock for multi-PR commands, one claim per PR.
- A head someone else pushed is left alone for `limits.head_min_age_minutes` (45).
- The tripwire (when `[tripwire] settings_file` is set): every setting in that file is snapshotted before
  and after each agent as names and digests, never values. It stops every chain only on damage: the file
  gone or unreadable, a setting removed, or a credential/endpoint/repo setting changed. Any other edit is
  recorded. Compare the TRIPWIRE ledger row with `<work>/backup/` before removing STOP.
- Agents get no GitHub credentials and a scratch HOME. No force push, no rebase, no admin merge, never
  `gpt-6-astra`.
- `<work>/exclude.txt` lists PRs `pick` must skip, one per line as `<pr> <reason>`.
- A finished chain removes its worktrees and code indexes; `pr-chain doctor` runs the same sweep by hand.

## Editing the code

1. Edit `bin/pr-chain` on a branch; a chain already running keeps the code it started with.
2. Run the tests (`python3 -B chain/tests/test_*.py`, scratch git repos and stubs; no agents, no `gh`).
3. `pr-chain` edits need no restart. `pr-chain-board` edits need a board restart. `pr-chain-loop` edits
   apply only after a loop restart.
4. Prompt files are read fresh for every agent launch. Never add a new `{{NAME}}` placeholder while a
   chain runs: old code does not fill it, `render()` raises, and that chain's next stage dies. Land the
   code that passes the key first, or use a plain marker line the new code replaces.
5. Verdict parsers read fixed spots: verdict files have the word on line 1, and `head`, `Findings:`,
   `Follow-up:` blocks start at the left edge; report files have the word on the LAST line.

## Never

- Never run `pr-chain stop`, kill a chain or the loop, or create the STOP file unless the owner said to.
- Never print an `err.txt` or a Codex rollout file whole; they can hold a session token inside a URL.
- Never push to a branch the chain owns while its status is `running`.
- Never move a chain PR forward by hand: no skip flags over a UI FAIL, no editing STATE.json or verdict
  files. A PR that stalls is a chain bug: fix the brief or the script, then rerun.
