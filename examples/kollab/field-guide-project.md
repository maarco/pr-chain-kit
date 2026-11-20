## Project section: kollab (this repo's map, checks and rules)

### Scratch-command prefix
Every python, pytest, ruff, node, npm or npx command also carries `KOLLAB_NO_KEYRING=1` beside `HOME={{SCRATCH_HOME}}`.

### Forbidden, on top of the generic rules
- Never read or write `~/.kollab`. Never start kollab, an engine, a daemon or a web UI yourself. Never touch ports 7433, 8080,
  7434-7439, 8081-8089.
- Never read these whole: `plugins/hub/plugin.py` (18,000 lines), `kollabor/application.py` (4,000 lines).

### Where things are (worktree {{WT}})
| Area | Path |
|---|---|
| app wiring, CLI, slash commands | `kollabor/`, `kollabor/commands/` |
| providers, profiles, conversation | `packages/kollabor-ai/` |
| tools, shell, MCP, agent loading | `packages/kollabor-agent/` |
| terminal UI | `packages/kollabor-tui/` |
| engine and RPC | `packages/kollabor-engine/`, `packages/kollabor-rpc/` |
| web UI server and frontend | `packages/kollabor-webui/`, frontend in `packages/kollabor-webui/frontend/` (`src/`, `tests/`) |
| hub, peers, vaults | `plugins/hub/` |
| unit tests | `tests/unit/` (CI runs every `tests/unit/test_*.py`) |
| repo rules | `CLAUDE.md`, `bundles/skills/kollab-development/SKILL.md`, `changes/README.md` |

### The checks GitHub CI runs, and how to run each one here
Run the ones that match the files in play. Python is `{{PY}}`; imports need `PYTHONPATH={{SRC}}`. Always from `{{WT}}`.

| CI check | Local command (add the HOME prefix) |
|---|---|
| ci/tests | `PYTHONPATH={{SRC}} {{PY}} -m pytest -q -x -p no:cacheprovider <test files> 2>&1 \| tail -30` |
| ci/tests (engine, rpc) | `PYTHONPATH={{SRC}} {{PY}} -m pytest -o asyncio_mode=auto packages/kollabor-engine/tests packages/kollabor-rpc/tests -q 2>&1 \| tail -15` (only when those packages are touched) |
| ci/standards-check | `{{PY}} -m ruff check <touched .py files>`; `{{PY}} -m py_compile <touched .py files>`; `PYTHONPATH={{SRC}} {{PY}} scripts/changelog.py check` (CI lints the whole tree with a narrower rule set, compiles three entry files and gives the changelog check the PR's diff base; these per-file commands are stricter on the files you touch) |
| ci/tests (webui) | in `packages/kollabor-webui/frontend`: `npx tsc --noEmit 2>&1 \| tail -20`; `node scripts/check-test-discovery.mjs`; `npx vitest run 2>&1 \| tail -20`; `node --test $(grep -L 'from "vitest"' tests/*.test.ts) 2>&1 \| tail -20` (node_modules is already linked) |
| browser proofs | in the frontend folder: `npm run build 2>&1 \| tail -5`, then `node tests/<name>.mjs <output folder>`; each proof saves screenshots into that folder and its first 30 lines list its switches |
| dod/ready-for-review | the PR body needs a `Fixes #N` line (`Closes #N` and `Refs #N` also count); a bug-fix PR also needs a `## Fix brief` heading whose section names the `Root cause` and the `Regression test`; a feature PR needs a changed file under `docs/specs/` (start from `docs/specs/TEMPLATE.md`) |
| ci/security-scan | gitleaks: no secret, token or key in any committed file (private host names are a repo rule above, not a CI check) |

Known false alarms, check before you blame the PR:
- A test that fails the same way on main is not this PR's defect: `git stash` is not allowed, so prove it with
  `git -C {{WT}} restore --source <main sha> -- <non-test files>`, rerun, then restore the head and confirm `git status --short` is clean.
- A timing test that fails once (`secure_conversation`, anything with a 10 second wait): rerun it once before reporting it.
- Two unit tests fail only when the shell exports `KOLLAB_WEBUI_HOSTS`: rerun with `env -u KOLLAB_WEBUI_HOSTS` in front.

### This repo's changelog rule
- A user-visible change adds `changes/<issue>.<section>.md` holding one `- ` bullet that ends with `(#<issue>)`. Never edit
  `CHANGELOG.md` or `kollabor/updates/CHANGELOG.md`.
