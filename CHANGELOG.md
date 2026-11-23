# Changelog

All notable changes to pr-chain. Versions follow [Semantic Versioning](https://semver.org): a breaking
change to `pr-chain.toml`, the `pr-chain` command or the work-folder layout bumps the major version
(the minor version while it is 0.x).

## [Unreleased]

### Added
- `[[merge_checks]]` in `pr-chain.toml`: the lint and test commands the fold runs on a PR merged with main, so a
  non-Python repo (Rust, TypeScript) can use the chain. None configured keeps the Python default (ruff + pytest).

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
