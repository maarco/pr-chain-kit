# Changelog

All notable changes to pr-chain. Versions follow [Semantic Versioning](https://semver.org): a breaking
change to `pr-chain.toml`, the `pr-chain` command or the work-folder layout bumps the major version
(the minor version while it is 0.x).

## [Unreleased]

## [0.1.0] - 2026-11-20

### Added
- `pr-chain` command (`init`, `board`, `loop`, `janitor`, `path`; anything else goes to the chain), runnable with `uvx`.
- One config file: `~/.config/pr-chain/pr-chain.toml`, or `pr-chain.local.toml` in a clone.
- Review, review-fix, audit, UI review, CI wait and merge stages, each run by a fresh agent.
- Read-only reviewer sandbox on macOS (`[agent] require_sandbox`).
- `[limits]` overrides for `flow.json`; board prompt edits saved beside the config.
- Read-only web board, starter pack of CI workflows for the target repo.
