"""Shared settings for agent-kit tools. Every value has an environment override and a default.

A host that runs the chain in-process does not use the environment: it calls kit.configure(cfg) once with the chain's
config.CFG, and every setting below is replaced from it. Without that call the values come from the variables, as before.

Import it from a tool in a subfolder with:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import kit  # noqa: E402

Variables (shell tools read the same names through kit.sh):
    KIT_AGENT        name of the agent running the tool                     default: agent
    KIT_REPO         GitHub repo the PR tools act on, owner/name             default: owner/name (pr-chain sets it from pr-chain.toml)
    KIT_CHECKOUT     local checkout of that repo; its main stays read-only  default: ~/dev/repo
    KIT_WORK         where tools keep logs, state and review folders        default: ~/backups/<KIT_AGENT>-work
    KIT_TRAIN        worktree used to merge main into PR branches           default: ~/dev/repo-wt/<KIT_AGENT>-train
    KIT_PYTHON       python that has the repo's dev dependencies            default: <KIT_CHECKOUT>/.venv/bin/python
    KIT_MERGER       GitHub account that merges                             default: merger-account
    KIT_CHECKS       required checks, comma separated                       default: ci/tests,ci/standards-check,ci/security-scan
    KIT_HUB_ME       hub names that count as this agent, comma separated   default: <KIT_AGENT>
    KIT_COORDINATOR  the hub's coordinator agent                            default: koordinator
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _path(name: str, default: str | Path) -> Path:
    return Path(_env(name, str(default))).expanduser()


AGENT = _env("KIT_AGENT", "agent")
REPO = _env("KIT_REPO", "owner/name")
OWNER, NAME = REPO.split("/", 1)
CHECKOUT = _path("KIT_CHECKOUT", "~/dev/repo")
WORK = _path("KIT_WORK", f"~/backups/{AGENT}-work")
TRAIN = _path("KIT_TRAIN", f"~/wt/{AGENT}-train")
PYTHON = _path("KIT_PYTHON", CHECKOUT / ".venv/bin/python")
MERGER = _env("KIT_MERGER", "merger-account")
CHECKS = tuple(c.strip() for c in _env("KIT_CHECKS", "ci/tests,ci/standards-check,ci/security-scan").split(",") if c.strip())
HUB_ME = tuple(n.strip() for n in _env("KIT_HUB_ME", AGENT).split(",") if n.strip())
COORDINATOR = _env("KIT_COORDINATOR", "koordinator")


def configure(cfg) -> None:
    """Replace every setting from a chain config.Cfg (its slug, checkout, work, train, python, merger, checks, agent)."""
    global AGENT, REPO, OWNER, NAME, CHECKOUT, WORK, TRAIN, PYTHON, MERGER, CHECKS, HUB_ME
    AGENT = cfg.agent
    REPO, OWNER, NAME = cfg.slug, cfg.owner, cfg.name
    CHECKOUT, WORK, TRAIN, PYTHON = cfg.checkout, cfg.work, cfg.train, cfg.python
    MERGER, CHECKS = cfg.merger, tuple(cfg.checks)
    HUB_ME = (AGENT,)


def check_results(rollup):
    """statusCheckRollup -> {check name: result of its NEWEST run}; a rerun lists several runs per name."""
    runs = sorted(rollup, key=lambda c: c.get("startedAt") or c.get("createdAt") or "")
    return {c.get("name") or c.get("context"): c.get("conclusion") or c.get("state") for c in runs}


def work(*parts: str) -> Path:
    """A path under KIT_WORK, creating KIT_WORK when it is missing."""
    WORK.mkdir(parents=True, exist_ok=True)
    return WORK.joinpath(*parts)


def gh_bin() -> str:
    """The gh to run: the chain's one resolver (config.gh_bin: [tools] gh, then PATH, then the old paths)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "chain"))
        import config
        return config.gh_bin()
    except ImportError:
        return shutil.which("gh") or "gh"


def gh_env(account: str) -> dict[str, str]:
    """os.environ plus GH_TOKEN for one logged-in gh account. Never prints the token; never runs `gh auth switch`."""
    token = subprocess.run([gh_bin(), "auth", "token", "--user", account], capture_output=True, text=True,
                           check=True).stdout.strip()
    return dict(os.environ, GH_TOKEN=token)
