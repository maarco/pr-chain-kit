# pr-chain-kit

pr-chain, extracted into a standalone, project-agnostic repo: a script that takes one GitHub PR at a time
through review → fix → re-review → audit → UI vision review → CI → merge, every stage run by a fresh
agent (claude/codex CLIs). The script — never the agents — pushes, posts and merges.

- How to operate it: `docs/OPERATOR.md`
- How it is built and why: `docs/design.md`, `chain/ENGINE.md`
- Proof the port works: `docs/PROOF.md`
- The CI/CD setup it expects on the target repo: `starter-pack/README.md`

## Layout

```
pr-chain.toml        one config file: every project-specific value (the commented default)
bin/                 pr-chain, pr-chain-loop, pr-chain-board, worktree-janitor
chain/               the engine: flow.json, prompts/, models.json, commands.json, rates.json,
                     board pages, chain_engine.py, chain_steps.py, ui_shoot.py, tests/
agent-kit/           vendored helpers: kit.py, land/ (fold, land_pr, changelog_merge)
examples/kollab/     the config + per-project files that reproduce the original kollab setup
starter-pack/        generic CI/CD: workflows, DoD gate, branch protection, changelog rule
docs/                OPERATOR.md, design.md, PROOF.md
```

## Use it on your repo

1. Copy this repo somewhere, fill in `pr-chain.toml` (slug, checkout, accounts, paths, labels,
   changelog, UI, and a per-project field guide with your repo map and local check commands — start
   from `examples/kollab/field-guide-project.md`).
2. Adopt the CI from `starter-pack/` on the target repo.
3. `python3 -B bin/pr-chain pick` shows the first eligible PR; `python3 -B bin/pr-chain run <pr> --land`
   runs the loop on it. `nohup python3 -B bin/pr-chain-loop &` runs it continuously.
4. `python3 -B bin/pr-chain-board 8790 &` gives the read-only board at http://127.0.0.1:8790.

Private values (your real accounts, local paths) go in `pr-chain.local.toml` at the repo root: it is
gitignored and loaded instead of `pr-chain.toml` when it exists.

Agents run unattended with every permission check turned off (`--dangerously-skip-permissions` /
`--dangerously-bypass-approvals-and-sandbox` in `chain/commands.json`). Run the chain on a machine and
accounts you are fine handing to an agent.

Env beats the file: `PR_CHAIN_CONFIG` (which toml), `KIT_REPO`, `KIT_CHECKOUT`, `KIT_WORK`,
`PR_CHAIN_WT_ROOT`, `PR_CHAIN_MODELS_FILE`, `PR_CHAIN_FLOW`, `PR_CHAIN_<ROLE>_MODEL`.

## Tests

```bash
for t in chain/tests/test_*.py; do python3 -B "$t"; done
```

Scratch git repos and stubs only — no `gh`, no agents, no network. The suite runs against the
neutral fixture `chain/tests/test-config.toml`.

## License

MIT, see `LICENSE`.
