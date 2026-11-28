"""One config file for everything project-specific: pr-chain.toml at the repo root (or $PR_CHAIN_CONFIG).

Importing this module reads nothing: CFG starts unconfigured, with every folder pointing at os.devnull. One call fills it.
  configure_standalone()      the bin/ scripts and `pr-chain init`: the file, then KIT_* / PR_CHAIN_* variables win over it
                              (a test or an operator can override one value without editing anything).
  configure(settings, data_dir)  a host (the kollab plugin): settings as a dict, every folder under data_dir; no
                              environment variable and no file is read.
Everything in the file is a default.

Relative paths in the file resolve against the repo root; ~ expands. The neutral pr-chain.toml ships safe
defaults; examples/kollab/pr-chain.toml reproduces the original hard-coded setup exactly.
"""
from __future__ import annotations

import os
import json
import logging
import re
import shutil
import stat
import tempfile
import tomllib
from pathlib import Path

from atomic import write_json_atomic

ROOT = Path(__file__).resolve().parents[1]          # the repo root: bin/, chain/, pr-chain.toml
CHAIN_DIR = ROOT / "chain"
ENV_PREFIXES = ("KIT_", "PR_CHAIN_", "KOLLAB_", "GH_", "GITHUB_")  # variables a child process never inherits
UNSET = Path(os.devnull)                            # a folder nobody configured: a write there fails, it never lands somewhere real
TEMPLATE_SLUG = "owner/name"                        # the placeholder repo in the shipped pr-chain.toml: it names no real repo
SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
AGENT = "pr-chain"                                  # the agent name this kit runs as (KIT_AGENT overrides it standalone)


def user_config_path() -> Path:
    """~/.config/pr-chain/pr-chain.toml, where `pr-chain init` writes it. Read at call time, not import."""
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pr-chain" / "pr-chain.toml"


def state_home() -> Path:
    """The default folder for work + worktrees. Read at call time, not import."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "pr-chain"


def _cfg_path() -> Path:
    """PR_CHAIN_CONFIG, else <kit>/pr-chain.local.toml (a clone), else ~/.config/pr-chain/pr-chain.toml, else the template."""
    if os.environ.get("PR_CHAIN_CONFIG"):
        return Path(os.environ["PR_CHAIN_CONFIG"])
    for c in (ROOT / "pr-chain.local.toml", user_config_path()):
        if c.exists():
            return c
    return ROOT / "pr-chain.toml"


def _p(v: str, near: Path, fallback: Path) -> Path:
    """~ expands; a relative path resolves next to the config file when it exists there, else under the fallback."""
    p = Path(v).expanduser()
    if p.is_absolute():
        return p
    return near / p if (near / p).exists() else fallback / p


class Cfg:
    """Flat view over the settings. Every attribute is resolved (paths absolute, env applied). load() fills this same
    object in place, so a module that did `from config import CFG` holds the configured one after configure()."""

    def __init__(self):
        self.load({}, env={}, near=UNSET, fallback=UNSET, folder=UNSET)

    def load(self, data: dict, *, env: dict, near: Path, fallback: Path, folder=None) -> None:
        """folder None = standalone (XDG state folders, env overrides). Otherwise every folder sits under folder and env
        is whatever the caller passes (a host passes {}), so no variable can move anything."""
        standalone = folder is None
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
        tools = data.get("tools", {})
        land = data.get("land", {})
        self.limits = dict(data.get("limits", {}))  # overrides flow.json limits.<name>.value, e.g. prs_at_once = 12

        self.slug = env.get("KIT_REPO") or repo.get("slug") or ""
        self.owner, _, self.name = self.slug.partition("/")
        # unset, malformed or the template placeholder: not configured. Nothing crashes; callers check this flag.
        self.configured = bool(SLUG_RE.match(self.slug)) and self.slug != TEMPLATE_SLUG
        self.branch = repo.get("default_branch", "main")
        self.import_guard = repo.get("import_guard", "")            # module whose import must resolve inside a worktree
        self.src_globs = tuple(repo.get("src_globs", ("packages/*/src", "src")))
        self.rules_doc = repo.get("rules_doc", "CONTRIBUTING.md")   # the repo rules every agent skims
        self.run_prefix = list(repo.get("run_prefix", []))           # merge-check command prefix, {wt} = worktree; empty = python
        self.checkout = Path(env.get("KIT_CHECKOUT") or _p(repo.get("checkout") or ("~/dev/repo" if standalone else str(UNSET)), near, fallback))
        self.python = Path(env.get("KIT_PYTHON") or _p(repo.get("python") or str(self.checkout / ".venv/bin/python"), near, fallback))

        self.authors = tuple(acc.get("authors", []))
        self.trusted = tuple(acc.get("trusted", []))
        self.merger = env.get("KIT_MERGER") or acc.get("merger", "")
        self.approver_of = dict(acc.get("approver_of", {}))
        self.checks = tuple(c.strip() for c in (env.get("KIT_CHECKS") or ",".join(acc.get("checks", []))).split(",") if c.strip())

        if standalone:
            self.work_configured = _p(paths.get("work") or str(state_home() / "work"), near, fallback)  # what the file says, before any KIT_WORK override
            self.work = Path(env.get("KIT_WORK") or self.work_configured)
            self.worktree_root = Path(env.get("PR_CHAIN_WT_ROOT") or _p(paths.get("worktree_root") or str(state_home() / "wt"), near, fallback))
        else:
            self.work_configured = self.work = folder / "work"
            self.worktree_root = folder / "wt"
        self.agent = env.get("KIT_AGENT") or AGENT
        self.train = Path(env["KIT_TRAIN"]) if env.get("KIT_TRAIN") else self.worktree_root.parent / f"{self.agent}-train"  # beside the stage worktrees
        self.lane_root = _p(paths["lane_root"], near, fallback) if paths.get("lane_root") else self.worktree_root.parent
        self.board_config = _p(paths["board_config"], near, fallback) if paths.get("board_config") else ""
        self.lane_status_dir = _p(paths["lane_status_dir"], near, fallback) if paths.get("lane_status_dir") else None  # <dir>/*<pr>*/STATUS.md of other writers
        # models.json; commands.json and profiles.json are read from the same folder
        self.models_file = Path(env.get("PR_CHAIN_MODELS_FILE") or (_p(paths["models_file"], near, fallback) if paths.get("models_file") else CHAIN_DIR / "models.json"))

        self.human_label = labels.get("human_approved", "human-approved")
        self.block_labels = frozenset(labels.get("blocked", []))

        self.changelog_forbidden = tuple(chlog.get("forbidden", []))
        self.fragment_dir = chlog.get("fragment_dir", "")

        self.ui_enabled = bool(ui.get("enabled", True))
        self.ui_paths_re = ui.get("paths_regex", r"(\.tsx$|\.css$|\.html$)")
        self.ui_proof_re = ui.get("proof_regex", r"frontend/tests/.+\.mjs$")
        self.ui_frontend_dir = ui.get("frontend_dir", "")
        self.ui_stamp_file = ui.get("stamp_file", "")
        self.ui_stack_script = _p(ui["stack_script"], near, fallback) if ui.get("stack_script") else None
        self.ui_shoot_script = _p(ui["shoot_script"], near, fallback) if ui.get("shoot_script") else CHAIN_DIR / "ui_shoot.py"
        self.term_shots_script = _p(ui["term_shots_script"], near, fallback) if ui.get("term_shots_script") else None
        self.playwright_browsers = _p(ui["playwright_browsers_path"], near, fallback) if ui.get("playwright_browsers_path") else None

        # [land]: "pr" lands each approved PR on its own; "batch" lands approved PRs together through one batch PR
        self.land_mode = land.get("mode", "pr")
        if self.land_mode not in ("pr", "batch"):
            logging.getLogger(__name__).warning('pr-chain.toml [land] mode = %r is not "pr" or "batch"; using "pr"', self.land_mode)
            self.land_mode = "pr"
        self.max_base_age_hours = float(land.get("max_base_age_hours", 48))
        self.batch_branch_prefix = str(land.get("batch_branch_prefix") or "merge-flow/batch")

        self.gh = str(tools.get("gh") or "")  # [tools] gh: the gh binary; empty = the first gh on PATH
        self.agent_env = dict(agent.get("env", {}))
        # [agent] env_allowlist: when set (even empty) a stage agent sees only these names plus [agent] env. None = off.
        self.env_allowlist = tuple(agent["env_allowlist"]) if agent.get("env_allowlist") is not None else None
        self.agent_home = str(agent.get("home") or "")  # "per-stage": a fresh HOME for every stage agent; "" = the parent's
        self.require_sandbox = bool(agent.get("require_sandbox", True))  # reviewers refuse to start without sandbox-exec

        self.tripwire_file = _p(trip["settings_file"], near, fallback) if trip.get("settings_file") else None

        self.field_guide = _p(proj["field_guide"], near, fallback) if proj.get("field_guide") else None

        # [[merge_checks]] the fold runs on the PR merged with main: name, match (globs on changed paths), cmd (list;
        # "{files}" = the matched files; the check runs once when any changed file matches), timeout (seconds).
        # None configured = the Python default (ruff E9/F63/F7/F82 on changed .py, pytest on changed test_*.py).
        self.merge_checks = [dict(name=c["name"], match=tuple(c.get("match", ["*"])), cmd=list(c["cmd"]),
                                  timeout=int(c.get("timeout", 900))) for c in data.get("merge_checks", [])]
        self.notify_cmd = notify.get("command", "")  # pages the operator when the loop is down; empty = no page


CFG = Cfg()                       # unconfigured: nothing is read until configure() or configure_standalone() runs
CONFIGURED = False                # set by either configure call
CFG_PATH = None                   # the file configure_standalone() read; None for a host
FLOW_FILE = CHAIN_DIR / "flow.json"
PROMPTS_BASE = CHAIN_DIR / "prompts"
PROMPTS_USER = UNSET / "prompts"  # where a user's prompt edits live; set by either configure call


def configure_standalone() -> Cfg:
    """The bin/ scripts' entry: pr-chain.toml, with KIT_* and PR_CHAIN_* variables winning. Call once, before reading CFG."""
    global CFG_PATH, FLOW_FILE, PROMPTS_USER, CONFIGURED
    CONFIGURED = True
    CFG_PATH = _cfg_path()
    if os.environ.get("PR_CHAIN_CONFIG") and not CFG_PATH.exists():
        raise SystemExit(f"PR_CHAIN_CONFIG points at {CFG_PATH}, which does not exist")
    data = tomllib.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}
    CFG.load(data, env=dict(os.environ), near=CFG_PATH.parent, fallback=ROOT)
    FLOW_FILE = Path(os.environ.get("PR_CHAIN_FLOW") or CHAIN_DIR / "flow.json")  # PR_CHAIN_FLOW: try a variant
    # Prompt edits (board Save Prompt) go beside the config, so an installed kit keeps them across upgrades and a prompt
    # nobody edited still updates with the kit. From a clone (config inside the kit) edits go to chain/prompts itself.
    PROMPTS_USER = PROMPTS_BASE if ROOT in CFG_PATH.resolve().parents else CFG_PATH.resolve().parent / "prompts"
    return CFG


SEEDED = ".seeded.json"  # beside the seeded prompts: the kit prompt names already copied, so a deleted one stays deleted


def seed_prompts(dest: Path) -> None:
    """Copy each kit prompt into dest the first time that name is seen. An edited prompt is never overwritten and a
    prompt the user deleted is never copied again; a prompt a later kit version adds is copied once. Safe to call every run."""
    try:
        done = set(json.loads((dest / SEEDED).read_text()))
    except (OSError, ValueError):
        done = set()
    new = [f for f in sorted(PROMPTS_BASE.glob("*.md")) if f.name not in done]
    if not new:
        return
    dest.mkdir(parents=True, exist_ok=True)
    for f in new:
        if not (dest / f.name).exists():
            shutil.copyfile(f, dest / f.name)
    write_json_atomic(dest / SEEDED, sorted(done | {f.name for f in new}))


def ensure_standalone() -> Cfg:
    """For a script run on its own (a land script, the janitor): configure from the file and environment unless a host
    or an earlier call already did."""
    return CFG if CONFIGURED else configure_standalone()


def configure(settings: dict, data_dir) -> Cfg:
    """The host's entry (the kollab plugin): settings as a dict, every folder under data_dir. Reads no environment
    variable and no file, so two installs on one host never share settings or folders."""
    global CFG_PATH, FLOW_FILE, PROMPTS_USER, CONFIGURED
    CONFIGURED = True
    data = Path(data_dir)
    CFG.load(dict(settings or {}), env={}, near=data, fallback=data, folder=data)
    CFG_PATH = None
    FLOW_FILE = CHAIN_DIR / "flow.json"
    PROMPTS_USER = data / "prompts"
    seed_prompts(PROMPTS_USER)
    return CFG


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
    """os.environ.setdefault calls so the vendored agent-kit kit.py picks the toml up unchanged. Does nothing under a host
    (configure() was called): a host's settings never go into the process environment."""
    if CONFIGURED and CFG_PATH is None:
        return
    for k, v in (("KIT_REPO", CFG.slug), ("KIT_CHECKOUT", str(CFG.checkout)),
                 ("KIT_PYTHON", str(CFG.python)), ("KIT_MERGER", CFG.merger),
                 ("KIT_CHECKS", ",".join(CFG.checks)),
                 # the fold worktree sits beside the stage worktrees, inside the same root (not agent-kit's ~/wt default)
                 ("KIT_TRAIN", str(CFG.train))):
        if v:
            os.environ.setdefault(k, v)


GH_FALLBACKS = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh")


def gh_bin() -> str:
    """The gh to run: [tools] gh when set, else the first gh on PATH, else a known install path, else plain "gh"."""
    return CFG.gh or shutil.which("gh") or next((g for g in GH_FALLBACKS if os.path.exists(g)), "gh")


def child_env(environ=None) -> dict:
    """The environment for a child process (a stage agent, a ruff run): the parent's variables minus every KIT_,
    PR_CHAIN_, KOLLAB_, GH_ and GITHUB_ one, so no GitHub token or host setting reaches it. HOME and PATH stay, so the
    agent CLIs keep their logins."""
    src = os.environ if environ is None else environ
    return {k: v for k, v in src.items() if not k.startswith(ENV_PREFIXES)}


TOKEN_PREFIXES = ("ghp_", "github_pat_", "gho_", "ghs_", "ghu_")  # GitHub token formats: a value like this is never passed on


def stage_env(extra=None, home=None, environ=None) -> dict:
    """The environment for one stage agent. Default: child_env() plus extra plus [agent] env. With [agent] env_allowlist
    set: only the listed names from the parent (still without KIT_/PR_CHAIN_/KOLLAB_/GH_/GITHUB_), then extra and
    [agent] env, and every value that starts like a GitHub token is dropped whatever its name. home replaces HOME."""
    src = os.environ if environ is None else environ
    if CFG.env_allowlist is None:
        env = child_env(src)
    else:
        env = child_env({k: v for k, v in src.items() if k in CFG.env_allowlist})
    env.update(extra or {})
    env.update(CFG.agent_env)
    if CFG.env_allowlist is not None:
        env = {k: v for k, v in env.items() if not str(v).startswith(TOKEN_PREFIXES)}
    if home is not None:
        env["HOME"] = str(home)
    return env


def make_stage_home():
    """A fresh empty HOME under the work folder when [agent] home = "per-stage", else None. Pair with remove_stage_home."""
    if CFG.agent_home != "per-stage":
        return None
    base = Path(CFG.work) / "stage-home"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="stage-", dir=base))


def remove_stage_home(path) -> None:
    if path is None or not os.path.lexists(path):
        return
    if os.path.islink(path):
        # Remove the link itself; never walk or chmod what it points to.
        try:
            os.unlink(path)
        except OSError as exc:
            logging.getLogger(__name__).error("Could not remove stage HOME link %s: %s", path, exc)
        return
    try:
        shutil.rmtree(path)
        return
    except OSError:
        pass

    # File-descriptor walk with O_NOFOLLOW: a root or child swapped for a symlink is never followed.
    nofollow_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def make_owner_writable(fd):
        try:
            os.fchmod(fd, stat.S_IMODE(os.fstat(fd).st_mode) | stat.S_IRWXU)
        except OSError:
            pass

    target = Path(path)
    try:
        top = os.open(target, nofollow_dir)
    except OSError:
        top = None
    if top is not None:
        try:
            make_owner_writable(top)
            for _, dirs, _, dir_fd in os.fwalk(".", dir_fd=top, follow_symlinks=False):
                for name in dirs:
                    try:
                        fd = os.open(name, nofollow_dir, dir_fd=dir_fd)
                    except OSError:
                        continue
                    try:
                        make_owner_writable(fd)
                    finally:
                        os.close(fd)
        finally:
            os.close(top)
    try:
        shutil.rmtree(target)
    except OSError as exc:
        logging.getLogger(__name__).error("Could not remove stage HOME %s after retry: %s", target, exc)
