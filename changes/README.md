# Changelog entries

A PR that changes what a user or operator sees adds one file here. It does not edit `CHANGELOG.md`:

    changes/<name>.<section>.md

- `<name>` is the number of the GitHub issue the PR fixes (`42.fixed.md`), or a short slug when there is none
  (`pr-chain-report.added.md`). A second entry for the same name adds `-2`, `-3` and so on.
- `<section>` is one of `added`, `changed`, `deprecated`, `removed`, `fixed` or `security`, the
  [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) headings.
- The file holds the entry as it will read in the changelog: a `- ` bullet in plain language. A longer entry
  continues on lines indented by two spaces.

Example, `changes/pr-chain-report.added.md`:

    - `pr-chain report` (read-only): end state per PR and reverts on main.

Two PRs never write the same file, so their entries cannot conflict.

`CHANGELOG.md` changes only in release PRs (branch `release/...`) and changelog repair PRs (branch `changelog/...`).
The `changelog` CI job refuses a direct edit anywhere else and checks every file here
(`python3 scripts/changelog.py check`).

The release runs `python3 scripts/changelog.py build`. It puts each entry first under its heading in
`## [Unreleased]`, one heading per section, and deletes the entry files. Then the release renames `[Unreleased]`
as CONTRIBUTING.md describes.
