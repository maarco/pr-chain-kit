# Starter pack: the CI/CD setup pr-chain expects on the target repo

Everything here is generic: copy the pieces you want into your repo's `.github/`, `scripts/`, `changes/`
and `docs/specs/`, replace the PLACEHOLDER / YOUR-ACCOUNT / OWNER/REPO markers, and switch on the repo
settings named at the bottom. These files came from a production repo that runs pr-chain all day; the
`ci.yml` here is a distilled template (the original also had package-specific web-ui, voice and heavy
lanes), everything else is the real file with names swapped for placeholders.

## What each piece does

| File | What it does | pr-chain needs it |
|---|---|---|
| `.github/workflows/ci.yml` | Lint (narrow ruff set), compile check, sharded unit tests, merge-queue support | yes — `ci/tests`, `ci/standards-check` are the default required checks |
| `.github/workflows/pr-gates-dod.yml` + `scripts/pr_gates_dod.py` | Definition-of-done check run (`dod/ready-for-review`): PR body has `Fixes #N`, bug fixes have a Fix brief, features touch `docs/specs/`; posts exactly one check run, never comments; controlled by `.github/pr-gates.toml` | yes — draft PRs must pass this before the merge gate |
| `.github/workflows/auto-merge.yml` | The merge queue: a labeled/approved PR is queued, merged onto latest main, checked, then merged | yes — pr-chain's `--land` relies on the queue, not on direct pushes |
| `.github/workflows/auto-merge-relay.yml` | Relay that re-runs CI on main after a queue merge (GITHUB_TOKEN pushes trigger nothing) | yes |
| `.github/workflows/ci-after-auto-merge.yml` | CI against main itself after each queued merge | recommended |
| `.github/workflows/pr-overlap.yml` + `.github/scripts/pr_overlap_detector.py` | Marks two open PRs that edit the same lines so the chain picks one | recommended |
| `.github/workflows/stale-pr-sweeper.yml` + `.github/scripts/stale_pr_sweeper.py` | Comments on PRs with no activity, closes them after a grace period | optional |
| `.github/scripts/setup-branch-protection.sh` | One command to configure branch protection, required checks, CODEOWNERS review; `--check` verifies | yes (run once) |
| `.github/CODEOWNERS` | Code-owner review; the two accounts in pr-chain.toml `[accounts]` own everything | yes |
| `scripts/changelog.py` + `changes/README.md` | The changelog-fragment rule: a PR adds `changes/<issue>.<section>.md`, never edits `CHANGELOG.md`; `check` enforces it in CI | yes — matches pr-chain.toml `[changelog]` |
| `docs/specs/TEMPLATE.md`, `docs/specs/FIX_BRIEF.md` | Spec template and fix-brief format the DoD gate looks for | yes (DoD) |
| `../SOFTWARE_DEVELOPMENT_LIFECYCLE.md` | The whole human+agent development flow these gates enforce; adapt account names and worktree paths | read first |

## Order to adopt

1. Copy `.github/CODEOWNERS`, `scripts/changelog.py`, `changes/README.md`, `docs/specs/` and edit the placeholders.
2. Copy `ci.yml`, rename jobs to your check names, point the shards at your test layout.
3. Run `bash .github/scripts/setup-branch-protection.sh --check` (read-only), then without `--check` from an
   admin account to switch on branch protection and the required checks.
4. Copy `pr-gates-dod.yml` + `scripts/pr_gates_dod.py`, add `.github/pr-gates.toml` with
   `dod.enabled = true` (until then the job exits 0 and posts nothing).
5. Copy `auto-merge.yml`, `auto-merge-relay.yml`, `ci-after-auto-merge.yml` and add the merge queue in
   repo Settings > Branches ("Require merge queue").
6. Optional: `pr-overlap.yml`, `stale-pr-sweeper.yml`.

## Repo settings to switch on (Settings tab)

- Branches > Branch protection on `main`: require a pull request before merging, require review from
  code owners, require status checks (`ci/tests`, `ci/standards-check`, `dod/ready-for-review`),
  require branch up to date, require merge queue, block force pushes.
- The required check names must exactly match the ones in pr-chain.toml `[accounts] checks`.
- Actions > General > Workflow permissions: read-only; the merge workflows use a dedicated PAT or app
  only where they must write.
