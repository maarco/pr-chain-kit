"""One config file for everything project-specific: pr-chain.toml at the repo root (or $PR_CHAIN_CONFIG).

Load order: environment (KIT_*, PR_CHAIN_* — the variables the scripts already read) wins over the file, so a
test or an operator can override one value without editing anything. Everything in the file is a default.

Relative paths in the file resolve against the repo root; ~ expands. The neutral pr-chain.toml ships safe
defaults; examples/kollab/pr-chain.toml reproduces the original hard-coded setup exactly.
"""
from __future__ import annotations

import os
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # the repo root: bin/, chain/, pr-chain.toml
CHAIN_DIR = ROOT / "chain"


USER_CFG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pr-chain" / "pr-chain.toml"  # `pr-chain init` writes it
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "pr-chain"  # default work + worktrees


def _cfg_path() -> Path:
    """PR_CHAIN_CONFIG, else <kit>/pr-chain.local.toml (a clone), else ~/.config/pr-chain/pr-chain.toml, else the template."""
    if os.environ.get("PR_CHAIN_CONFIG"):
        return Path(os.environ["PR_CHAIN_CONFIG"])
    for c in (ROOT / "pr-chain.local.toml", USER_CFG):
        if c.exists():
            return c
    return ROOT / "pr-chain.toml"


def _p(v: str) -> Path:
    """~ expands; a relative path resolves next to the config file when it exists there, else inside the kit."""
    p = Path(v).expanduser()
    if p.is_absolute():
        return p
    near = _cfg_path().parent / p
    return near if near.exists() else ROOT / p


class Cfg:
    """Flat view over pr-chain.toml. Every attribute is resolved (paths absolute, env applied)."""

    def __init__(self, data: dict):
        repo = data.get("repo", {})
        acc = data.get("accounts", {})
        paths = data.get("paths", {})
        labels = data.get("labels", {})
        chlog = data.get("changelog", {})
        ui = data.get("ui", {})
        agent = data.get("agent", {})
        trip = data.get("tripwire", {})
        proj = data.get("project", {})
        notify = data.get("notify", {})
        self.limits = dict(data.get("limits", {}))  # overrides flow.json limits.<name>.value, e.g. prs_at_once = 12

        self.slug = os.environ.get("KIT_REPO") or repo.get("slug", "owner/name")
        self.owner, self.name = self.slug.split("/", 1)
        self.branch = repo.get("default_branch", "main")
        self.import_guard = repo.get("import_guard", "")            # module whose import must resolve inside a worktree
        self.src_globs = tuple(repo.get("src_globs", ("packages/*/src", "src")))
        self.rules_doc = repo.get("rules_doc", "CONTRIBUTING.md")   # the repo rules every agent skims
        self.run_prefix = list(repo.get("run_prefix", []))           # merge-check command prefix, {wt} = worktree; empty = python
        self.checkout = Path(os.environ.get("KIT_CHECKOUT") or _p(repo.get("checkout", "~/dev/repo")))
        self.python = Path(os.environ.get("KIT_PYTHON") or _p(repo.get("python") or str(self.checkout / ".venv/bin/python")))

        self.authors = tuple(acc.get("authors", []))
        self.trusted = tuple(acc.get("trusted", []))
        self.merger = os.environ.get("KIT_MERGER") or acc.get("merger", "")
        self.approver_of = dict(acc.get("approver_of", {}))
        self.checks = tuple(c.strip() for c in (os.environ.get("KIT_CHECKS") or ",".join(acc.get("checks", []))).split(",") if c.strip())

        self.work_configured = _p(paths.get("work") or str(STATE / "work"))  # what the file says, before any KIT_WORK override
        self.work = Path(os.environ.get("KIT_WORK") or self.work_configured)
        self.worktree_root = Path(os.environ.get("PR_CHAIN_WT_ROOT") or _p(paths.get("worktree_root") or str(STATE / "wt")))
        self.lane_root = _p(paths["lane_root"]) if paths.get("lane_root") else self.worktree_root.parent
        self.board_config = _p(paths["board_config"]) if paths.get("board_config") else ""
        self.lane_status_dir = _p(paths["lane_status_dir"]) if paths.get("lane_status_dir") else None  # <dir>/*<pr>*/STATUS.md of other writers
        # models.json; commands.json and profiles.json are read from the same folder
        self.models_file = Path(os.environ.get("PR_CHAIN_MODELS_FILE") or (_p(paths["models_file"]) if paths.get("models_file") else CHAIN_DIR / "models.json"))

        self.human_label = labels.get("human_approved", "human-approved")
        self.block_labels = frozenset(labels.get("blocked", []))

        self.changelog_forbidden = tuple(chlog.get("forbidden", []))
        self.fragment_dir = chlog.get("fragment_dir", "")

        self.ui_enabled = bool(ui.get("enabled", True))
        self.ui_paths_re = ui.get("paths_regex", r"(\.tsx$|\.css$|\.html$)")
        self.ui_proof_re = ui.get("proof_regex", r"frontend/tests/.+\.mjs$")
        self.ui_frontend_dir = ui.get("frontend_dir", "")
        self.ui_stamp_file = ui.get("stamp_file", "")
        self.ui_stack_script = _p(ui["stack_script"]) if ui.get("stack_script") else None
        self.ui_shoot_script = _p(ui["shoot_script"]) if ui.get("shoot_script") else CHAIN_DIR / "ui_shoot.py"
        self.term_shots_script = _p(ui["term_shots_script"]) if ui.get("term_shots_script") else None
        self.playwright_browsers = _p(ui["playwright_browsers_path"]) if ui.get("playwright_browsers_path") else None

        self.agent_env = dict(agent.get("env", {}))
        self.require_sandbox = bool(agent.get("require_sandbox", True))  # reviewers refuse to start without sandbox-exec

        self.tripwire_file = _p(trip["settings_file"]) if trip.get("settings_file") else None

        self.field_guide = _p(proj["field_guide"]) if proj.get("field_guide") else None

        # [[merge_checks]] the fold runs on the PR merged with main: name, match (globs on changed paths), cmd (list;
        # "{files}" = the matched files; the check runs once when any changed file matches), timeout (seconds).
        # None configured = the Python default (ruff E9/F63/F7/F82 on changed .py, pytest on changed test_*.py).
        self.merge_checks = [dict(name=c["name"], match=tuple(c.get("match", ["*"])), cmd=list(c["cmd"]),
                                  timeout=int(c.get("timeout", 900))) for c in data.get("merge_checks", [])]
        self.notify_cmd = notify.get("command", "")  # pages the operator when the loop is down; empty = no page


CFG_PATH = _cfg_path()
if os.environ.get("PR_CHAIN_CONFIG") and not CFG_PATH.exists():
    raise SystemExit(f"PR_CHAIN_CONFIG points at {CFG_PATH}, which does not exist")
CFG = Cfg(tomllib.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {})


FLOW_FILE = Path(os.environ.get("PR_CHAIN_FLOW") or CHAIN_DIR / "flow.json")  # PR_CHAIN_FLOW: try a variant
PROMPTS_BASE = CHAIN_DIR / "prompts"
# Prompt edits (board Save Prompt) go beside the config, so an installed kit keeps them across upgrades and a prompt
# nobody edited still updates with the kit. From a clone (config inside the kit) edits go to chain/prompts itself.
PROMPTS_USER = PROMPTS_BASE if ROOT in CFG_PATH.resolve().parents else CFG_PATH.resolve().parent / "prompts"


def prompt_file(name: str) -> Path:
    """The prompt the chain uses: the user's edited copy when there is one, else the kit's."""
    mine = PROMPTS_USER / name
    return mine if mine.is_file() else PROMPTS_BASE / name


def prompt_names() -> list:
    return sorted({f.name for d in (PROMPTS_BASE, PROMPTS_USER) if d.is_dir() for f in d.glob("*.md") if f.is_file()})


def load_flow() -> dict:
    """flow.json with pr-chain.toml [limits] applied on top (the flow itself always comes from the kit)."""
    flow = json.loads(FLOW_FILE.read_text())
    for k, v in CFG.limits.items():
        if k not in flow["limits"]:
            raise SystemExit(f"pr-chain.toml [limits] {k}: not a flow.json limit (known: {', '.join(sorted(flow['limits']))})")
        flow["limits"][k]["value"] = v
    return flow


def kit_env_defaults():
    """os.environ.setdefault calls so the vendored agent-kit kit.py picks the toml up unchanged."""
    for k, v in (("KIT_REPO", CFG.slug), ("KIT_CHECKOUT", str(CFG.checkout)),
                 ("KIT_PYTHON", str(CFG.python)), ("KIT_MERGER", CFG.merger),
                 ("KIT_CHECKS", ",".join(CFG.checks)),
                 # the fold worktree sits beside the stage worktrees, inside the same root (not agent-kit's ~/wt default)
                 ("KIT_TRAIN", str(CFG.worktree_root.parent / f"{os.environ.get('KIT_AGENT', 'pr-chain')}-train"))):
        if v:
            os.environ.setdefault(k, v)
