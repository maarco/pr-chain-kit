# Stable release updates for managed installations

## Ownership and supported installs

Updates are supported only after the operator runs `pr-chain updates install <directory>`. The resulting root contains immutable `releases/vX.Y.Z/` trees, an atomically replaced `current` symlink, an update lock and status under `state/`, and a stable `bin/pr-chain` wrapper. `current` owns runtime code only. Configuration, prompt edits, work, logs, credentials, service definitions and user-managed files remain in their existing locations. The installer refuses an existing destination and publishes a new install with one directory rename after staging and validation.

Existing editable checkouts, `uvx`, `uv tool`, `pipx`, and arbitrary virtual environments are unsupported for automatic replacement. They remain usable without updates. The operator can explicitly create a separate managed install and point their service or PATH at its `bin` directory.

## Trusted release identity

The updater contacts only `https://api.github.com/repos/maarco/pr-chain-kit/releases/latest` and the corresponding canonical GitHub source archive endpoint over HTTPS. GitHub's latest-release endpoint excludes drafts and prereleases; tags must match `vMAJOR.MINOR.PATCH` and the archive's `pr_chain_kit.__version__` must match the tag. This trusts GitHub's release/tag and TLS control; it does not claim maintainer-signed artifact verification. No arbitrary URL, PR, branch, or moving `main` is accepted.

## Staging, validation and activation

The source archive is size-bounded and extracted into a private temporary directory after rejecting links, special files and path traversal. Before activation the updater checks the version, compiles the tree, verifies command/loop/board entrypoints and board assets, and runs the staged `pr-chain path` probe. A complete release is renamed into its immutable version directory. Activation creates a sibling symlink and atomically replaces `current`. The activated tree is checked again; a failure atomically restores the prior target. An `activating` status record lets a later status call detect an interrupted switch, validate and recover a fully selected release, or report that the old version stayed selected. A later check retries.

All loops for an installation serialize check, stage and activation with one OS file lock. Every running launcher resolves its script path to the selected immutable release directory before it starts; child chain commands use that same resolved tree. A pointer switch cannot change code under an active chain or loop. Existing loops adopt a new release only after their locally tracked and observed chains are idle, by replacing themselves with the new loop. Other loops and chains continue their pinned release. A board already running stays pinned until its normal operator restart. Old release directories are retained for manual rollback and are never garbage-collected automatically.

## Operator controls and failure behavior

Updates default off. `pr-chain updates enable` adds/updates `[updates].enabled` in the configured user TOML while preserving other text; `disable` turns it off. Enabled managed loops check at most once per six hours. `pr-chain updates status` shows installed and available versions, status, timestamp and error. `pr-chain updates check` performs an explicit check and activation even if automatic updates are disabled. No release is an idle status. Network, API, archive, validation and activation errors leave the previous selected version usable and are recorded for inspection; the loop retries after six hours, and an operator can retry immediately with `updates check`.

Manual recovery: run `pr-chain updates status`; if needed, select a retained `<root>/releases/vX.Y.Z` directory by creating a unique temporary symlink beside `current` and replacing `current` with `os.replace` (for example, with a short Python script using `Path.symlink_to("releases/vX.Y.Z")` and `os.replace`). Then run `<root>/bin/pr-chain updates status`. Do not edit a release directory in place. The repository's tested automatic manager currently targets POSIX systems with symlinks and `fcntl.flock`; Windows and non-POSIX filesystems are unsupported.

## Validation coverage

Isolated fixtures cover initial managed installation, default-off behavior, no release, successful activation, failed release verification, network failure, interrupted pointer replacement, simultaneous updater processes, and preservation of operator files outside the managed code root. No production service or installation is modified by the feature tests.
