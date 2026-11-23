# Changelog

All notable changes to pr-chain. Versions follow [Semantic Versioning](https://semver.org): a breaking
change to `pr-chain.toml`, the `pr-chain` command or the work-folder layout bumps the major version
(the minor version while it is 0.x).

## [Unreleased]

### Added
- The kit can be driven as a library by a host (the kollab plugin). `chain/config.py` reads nothing on import; a host calls
  `configure(settings, data_dir)` and every folder (work, worktrees, claims, prompts, fold worktree) sits under `data_dir`,
  so two installs on one host never collide. Prompts are seeded into `data_dir/prompts` once and never overwritten.
  `chain/plugin_api.py` has plain read functions (state, PRs, issues, spend, config, badge); `chain/atomic.py` writes state
  JSON whole or not at all. Stage agents no longer inherit `GH_*`, `GITHUB_*`, `KIT_*`, `PR_CHAIN_*` or `KOLLAB_*` variables.
  Standalone use is unchanged: the `bin/` scripts call `configure_standalone()`.
- `[[merge_checks]]` in `pr-chain.toml`: the lint and test commands the fold runs on a PR merged with main, so a
  non-Python repo (Rust, TypeScript) can use the chain. None configured keeps the Python default (ruff + pytest).
- Linux reviewers run under bubblewrap (`bwrap`): the checkout, the worktree root and tracked symlink targets are
  mounted read-only, the same paths macOS `sandbox-exec` denies. `require_sandbox = false` is no longer needed on Linux.
- Several repos on one host: each loop tags the chains it starts with `--repo <slug>` and counts only its own
  repo's chains against its threads; the picker ignores other repos' chains. Untagged chains (started by hand) still
  count for every repo. A `--repo` that does not match the config is refused.
- `exclude.txt` takes `branch:<prefix> <reason>` lines: every PR whose head branch starts with the prefix is skipped.
- The PR picker parks a PR stacked on a branch other than the default branch instead of picking it every lap.

### Fixed
- `pr-chain-loop` with no repo set (or only the template `owner/name`) exits 0 with a message naming the repo setting; it used
  to treat the template as a real repo and keep calling `gh`.
- `[ui] enabled = false` turns UI review off; it was read and then ignored, so every .tsx change still went to UI review.
- UI review with no `[ui] stack_script` ends STACK-DOWN with a clear reason, so the PR is held, never merged unreviewed.

### Changed
- The fold worktree defaults to `<worktree_root>/../pr-chain-train`, inside the same root as the stage worktrees
  (was agent-kit's `~/wt/<agent>-train`).
- `worktree-janitor` skips only the chain's own `pr-*` and `issue-*` folders in the worktree root; anything else
  there is judged like every other worktree (it was never cleaned before).

## [0.1.0] - 2026-11-20

### Added
- `pr-chain` command (`init`, `board`, `loop`, `janitor`, `path`; anything else goes to the chain), runnable with `uvx`.
- One config file: `~/.config/pr-chain/pr-chain.toml`, or `pr-chain.local.toml` in a clone.
- Review, review-fix, audit, UI review, CI wait and merge stages, each run by a fresh agent.
- Read-only reviewer sandbox on macOS (`[agent] require_sandbox`).
- `[limits]` overrides for `flow.json`; board prompt edits saved beside the config.
- Read-only web board, starter pack of CI workflows for the target repo.
- Runs on Linux and macOS: process working folders come from `/proc` on Linux and `lsof` on macOS; a missing tool is a failed call, not a crash.
