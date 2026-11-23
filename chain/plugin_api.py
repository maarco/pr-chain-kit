"""Read functions a host (the kollab plugin) calls to show the chain: state, PRs, issues, spend, config and a badge.

Plain functions that return JSON-ready dicts and lists. No web framework is imported here: the host wraps each one in
whatever routes it serves. The data comes from bin/pr-chain-board's readers, loaded on first use, after the host has
called config.configure(). Until a repo is configured every function answers with the setup message and makes no gh call.
Reads change no repo or host setting, but a read may refresh the board's caches (the gh cache and the rate table)
inside the install's work folder, each replaced whole. A failed read answers {"error": "..."} rather than raising,
like the board's own routes.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import config as _config  # the module; this file also defines config(), the route function

SETUP = "No repo is set. Set the repo (owner/name) in the plugin settings, then reload."
_board = None


def _configured() -> bool:
    return _config.CONFIGURED and _config.CFG.configured


def _readers():
    """bin/pr-chain-board as a module (its HTTP server only starts under __main__), loaded once."""
    global _board
    if _board is None:
        path = str(_config.ROOT / "bin" / "pr-chain-board")
        loader = importlib.machinery.SourceFileLoader("pr_chain_board", path)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("pr_chain_board", loader))
        loader.exec_module(mod)
        _board = mod
    return _board


def _read(fn_name: str, *args):
    try:
        return getattr(_readers(), fn_name)(*args)
    except Exception as e:  # a half-written file or a gh failure must not take the host's route down
        return {"error": str(e)[:300]}


def _gate(fn_name: str, *args):
    """A read that needs the checkout (the PR and issue lists): a clear answer when repo.checkout is not set."""
    if _config.CFG.checkout == _config.UNSET:
        return {"configured": True, "error": "repo.checkout is not set"}
    return _read(fn_name, *args)


def _unset() -> dict:
    return {"configured": False, "setup": SETUP}


def state(pick_pr: int | None = None) -> dict:
    """The loop and the chains: what runs now, what runs next, recent results. pick_pr: show that chain's agent."""
    if not _configured():
        return _unset()
    out = _read("state", pick_pr)
    return {"configured": True, **out} if isinstance(out, dict) else out


def prs(which: str = "open", fresh: bool = False):
    """The repo's pull requests with their chain state. which: open, closed, merged or all."""
    return _gate("pr_list", which, fresh) if _configured() else _unset()


def pr(number: int, fresh: bool = False) -> dict:
    """One pull request: chain state, linked issue, review conversation and every step it took."""
    return _gate("pr_detail", int(number), fresh) if _configured() else _unset()


def issues(which: str = "open", fresh: bool = False):
    """The repo's issues with what the chain did about each."""
    return _gate("issue_list", which, fresh) if _configured() else _unset()


def issue(number: int, fresh: bool = False) -> dict:
    """One issue and the chain's work on it."""
    return _gate("issue_detail", int(number), fresh) if _configured() else _unset()


def spend() -> dict:
    """Token and dollar totals: today, per PR, per finished chain run."""
    return _read("spend_table") if _configured() else _unset()


def config() -> dict:
    """The model and command choice per stage, as the board's config page shows it (read only)."""
    return _read("config_state") if _configured() else _unset()


def badge() -> dict:
    """A short text for a menu badge: how many chains run now ("" when none) and a hint line."""
    if not _configured():
        return {"text": "", "hint": SETUP}
    s = state()
    live = s.get("live") if isinstance(s, dict) else None
    n = len(live) if isinstance(live, list) else 0
    return {"text": str(n) if n else "", "hint": f"{n} chain(s) running" if n else "No chain is running"}
