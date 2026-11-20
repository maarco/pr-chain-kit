"""pr-chain engine: runs flow.json (see ENGINE.md). Start at flow["start"], run each node's `do` step, follow next[outcome].
It holds no chain logic: every routing decision is a step's outcome word plus an edge in flow.json."""
import json
import os
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

import chain_steps

import config  # noqa: E402
FLOW_PATH = config.FLOW_FILE  # PR_CHAIN_FLOW: try a variant; [limits] in pr-chain.toml applies on top


class Live:
    """Attribute view of bin/pr-chain's globals, read on every access, so a test stub on the module reaches every step."""

    def __init__(self, g):
        object.__setattr__(self, "_g", g)

    def __getattr__(self, k):
        try:
            return self._g[k]
        except KeyError:
            raise AttributeError(k) from None


def limit_of(ctx, key):
    v = getattr(ctx.a, key, None)  # --max-rounds and friends override flow.json for one run; -1 = no limit
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float("inf") if v < 0 else v
    return ctx.pc.LIM[key]


def check(flow):
    """Problems that would stop the engine mid-run, found before it starts: unknown steps, edges to nowhere."""
    nodes, ends = flow["nodes"], flow["end_states"]
    bad = [f"{k}: unknown step {v['do']!r}" for k, v in nodes.items() if v["do"] not in chain_steps.STEPS]
    bad += [f"{k}: {o} -> {t} goes nowhere" for k, v in nodes.items() for o, t in v["next"].items() if t not in nodes and t not in ends]
    bad += [f"{k}: on_limit -> {v['on_limit']} goes nowhere" for k, v in nodes.items() if v.get("on_limit") and v["on_limit"] not in nodes
            and v["on_limit"] not in ends]
    if flow["start"] not in nodes:
        bad.append(f"start {flow['start']!r} is not a node")
    return bad


def run(g, pr, a, from_gate=False, approved=None, seed=None, flow=None):
    pc = Live(g)
    flow = flow or config.load_flow()
    problems = check(flow)
    if problems:
        return pc.die(pr, "ERROR", "flow.json: " + "; ".join(problems)[:300])
    nodes, ends = flow["nodes"], flow["end_states"]
    ctx = SimpleNamespace(pc=pc, pr=pr, a=a, from_gate=from_gate, approved=approved, seed=seed, counts=defaultdict(int), why="",
                          ended=None, node=None)
    chain_steps.init(ctx)
    at, steps = flow["start"], 0
    while True:
        if at in ends:
            if ends[at].get("no_die"):
                return at
            return pc.die(pr, at, ctx.why or ends[at]["means"])
        steps += 1
        if steps > pc.LIM["max_steps"]:
            ctx.why, at = f"flow ran {steps - 1} nodes without reaching an end state; last node {at}", "ERROR"
            continue
        node, ctx.node = nodes[at], at
        if node.get("uncount"):
            ctx.counts[node["uncount"]] -= 1
        if node.get("count"):
            c = node["count"]
            ctx.counts[c] += 1
            if node.get("limit") and ctx.counts[c] > limit_of(ctx, node["limit"]):
                ctx.why = node.get("why", f"{c} reached its limit").format(count=ctx.counts[c], limit=limit_of(ctx, node["limit"]))
                at = node["on_limit"]
                continue
        if node.get("kind") == "agent" and pc.STOPFILE.exists():
            ctx.why, at = "STOP file", "STOPPED"
            continue
        outcome = chain_steps.STEPS[node["do"]](ctx, node.get("args") or {})
        if outcome == "ended":  # the step ended the run itself (the human gate posts and parks); its status stands
            return ctx.ended
        pc.log(f"#{pr} [{at}] {outcome}")
        nxt = node["next"].get(outcome)
        if nxt is None:
            ctx.why, at = f"flow.json: node {at} has no edge for outcome {outcome!r}", "ERROR"
            continue
        at = nxt
