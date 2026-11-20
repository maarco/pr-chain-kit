# Proof the port works

All commands run from the repo root on 2026-11-18, with
`PR_CHAIN_CONFIG=$PWD/examples/kollab/pr-chain.toml` and `KIT_WORK` pointed at a scratch folder
(the real chain work folder is never touched).

## 1. Tests — `python3 -B` on every copied test file

```
test_board_agent.py: rc=0 | PASS
test_changelog_guard.py: rc=0 | test_changelog_guard: wiring pinned (fix() routes the guard through own_delta)
test_commands.py: rc=0 | PASS
test_drift.py: rc=0 | all drift checks passed
test_flow.py: rc=0 | all flow checks passed
test_janitor.py: rc=0 | all passed
test_loop.py: rc=0 | all loop checks passed
test_priority.py: rc=0 | all passed
test_redfirst.py: rc=0 | all redfirst checks passed
test_scenarios.py: rc=0 | all scenario checks passed
test_term_shots.py: rc=0 | all term_shots checks passed
test_ui_loop.py: rc=0 | all UI loop checks passed
test_ui_stack.py: rc=0 | all ui_stack checks passed
```

13/13 files, every exit code 0, run against `examples/kollab/pr-chain.toml` — the kit reproduces the
original setup's behavior.

Two janitor fixes were needed during the port and are covered by its test: busy-process and
skip-prefix comparisons now resolve macOS `/var` → `/private/var` symlinks before comparing.

## 2. Commands with the example config (GitHub calls read-only)

```
$ python3 -B bin/pr-chain --help        # rc=0
usage: pr-chain [-h]
                {models,pick,run,auto,land,issue,scout,resume,status,doctor,stop,clean} ...

$ python3 -B bin/pr-chain models        # rc=0
review  cmd:codex-luna-max       (<repo>/chain/models.json)
        runs: zsh -lic "codex exec ... -m gpt-6-luna ... 'Read <brief.md> and follow it exactly.' </dev/null"
...

$ python3 -B bin/pr-chain pick          # rc=0, read-only gh pr list against the configured repo
   PR   size state    ui author       owner/free
  653   4943 BEHIND   UI bot          FREE
 ...
next target: #653
```

`pick` has no write path: it reads `gh pr list`/`pr view` and local state only. `--dry-run` exists on
`run`/`auto`/`land` and launches nothing.

## 3. Privacy grep

```
$ grep -rn '/Users/|10.0.0.|192.168.|@gmail' --exclude-dir=.git . | grep -v '^./examples/kollab/'
bin/pr-chain:832:    text = re.sub(r"(?:/Users/[^/\s]+|~)/[^\s`'\")\]]*", "<local path>", text)
chain/tests/test_term_shots.py:58:  out = ts.scrub("cwd /Users/realuser/x  someone@mail.test  REALUSER", toks)
chain/tests/test_term_shots.py:59:  check("scrub: same length", len(out), ...)
chain/tests/test_scenarios.py:871:  out = g11.scrub("Haiku audit: ran `HOME=/Users/someone/... ")
chain/tests/test_scenarios.py:872:  check("scrub: no home paths", "/Users/" in out or "~/" in out, False)
```

Every remaining hit is the scrubber itself or a fake fixture (`realuser`, `someone`) that PROVES
`scrub()` strips local paths — none is real data. Account names outside `examples/kollab/`: none since the test fixture change. Earlier note:
GitHub-login fixture values in 4 test files, because the tests run against the kollab example config
whose `[accounts] authors` are those two public accounts; no other personal data is in the tree.
`kollab` as a word outside `examples/kollab/` appears only as pointers to that example folder.

## Live runs, 2026-11-18 (after the neutral-defaults change)

- kollab example config, read-only `pick`: listed #1235 (DIRTY, UI) and #1261 (BEHIND), next target #1235.
- kollab example config, `run 1261 --dry-run` in a scratch work folder: fold step, review launch line, `RESULT: DRY`.
- Second, non-kollab repo (`cli/cli`, branch `trunk`, root `pr-chain.toml` with only slug, branch and authors changed):
  `pick` listed 3 PRs and named a next target. A dry `run` there needs a local checkout and was not done.
- Tests now run against `chain/tests/test-config.toml` (the kollab example with neutral accounts `owner`/`bot`):
  13/13 pass.

## 4. Installed package (uvx)

Built with `uv build`, run with `uvx --from dist/pr_chain-0.1.0-py3-none-any.whl pr-chain ...` and a scratch
`XDG_CONFIG_HOME` / `XDG_STATE_HOME`:

```
pr-chain init                      # wrote ~/.config/pr-chain/pr-chain.toml + models/commands/profiles.json
pr-chain pick                      # rc=0, read the configured repo
pr-chain run <pr> --dry-run        # RESULT: NOT-OURS for a PR by an account not in [accounts] authors
pr-chain board 8796                # /, /prs, /wiki, /config and /three/three.module.js all 200
[limits] prs_at_once = 5           # board /api/config reports prs_at_once 5
POST /api/config save_prompt fix.md
  -> ~/.config/pr-chain/prompts/fix.md holds the edit, old text in prompts/.history/
  -> the packaged chain/prompts/fix.md is unchanged
```

`chain/tests/test_user_overrides.py` covers the same rules without a build.
