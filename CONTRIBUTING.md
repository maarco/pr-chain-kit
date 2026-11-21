# Contributing to pr-chain

Bug reports, fixes and new features are welcome. Open an issue first for anything bigger than a bug fix,
so the design can be agreed before you build it.

## Set up

```bash
git clone https://github.com/maarco/pr-chain-kit && cd pr-chain-kit
for t in chain/tests/test_*.py; do python3 -B "$t"; done     # Python 3.11+, git; ruff optional
```

The tests use scratch git repos and stubs only: no `gh`, no agents, no network. Run the chain itself from
the clone with `python3 -B bin/pr-chain ...` and a `pr-chain.local.toml` (gitignored) at the repo root.

## Where things live

| Change | Files |
|---|---|
| The chain's steps, routing and limits | `chain/flow.json`, `chain/chain_steps.py`, `chain/chain_engine.py` (see `chain/ENGINE.md`) |
| What agents are told | `chain/prompts/*.md` |
| Config keys | `chain/config.py` and the commented template `pr-chain.toml` |
| The board | `bin/pr-chain-board`, pages in `chain/board/` |
| The `pr-chain` command and `init` | `pr_chain_kit/cli.py` |
| CI for the repos the chain runs on | `starter-pack/` |

## Rules for a PR

- One change per PR, linked to its issue (`Fixes #N`).
- New behavior gets a test in `chain/tests/` that fails without the change. Tests stay stub-only.
- Add a line under `## [Unreleased]` in `CHANGELOG.md` (not needed for docs-only or test-only changes).
- A new setting goes in `chain/config.py` with a safe default, plus a commented line in `pr-chain.toml`.
- Nothing project-specific in the kit: names, paths and commands of one repo belong in `pr-chain.toml`
  or `examples/`.
- No real account names, hostnames, home paths or tokens anywhere, screenshots included.
- Board changes: attach a screenshot.

CI runs every test file on Linux and macOS (Python 3.11 and 3.13), builds the package, and smoke-tests
the `pr-chain` command. A maintainer reviews and merges.

## Releases (maintainers)

Versions follow Semantic Versioning. The version lives only in `pr_chain_kit/__init__.py`.

1. Set `__version__` in `pr_chain_kit/__init__.py`.
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and add a new empty `## [Unreleased]` above it.
3. Commit, then `git tag vX.Y.Z && git push origin main vX.Y.Z`.

The tag runs `.github/workflows/release.yml`: all tests, a check that the tag matches the version and the
changelog, build, upload to PyPI, and a GitHub release with that changelog section as notes.

One-time setup, before the first release:

- PyPI: Account settings > Publishing > add a pending publisher: project `pr-chain`, owner `maarco`,
  repository `pr-chain-kit`, workflow `release.yml`, environment `pypi`. No PyPI token is stored anywhere.
- GitHub: Settings > Environments > new environment `pypi` (optionally require a reviewer, so every
  upload waits for a click).
- GitHub: Settings > Branches > protect `main`: require a pull request and the `ci` checks.
