# pr-chain engine: the chain runs flow.json

The code loads flow.json and runs the chain the way the JSON says. The JSON is the program, not a
description of it. Editing flow.json changes the chain without a Python change.

## What lives where

| flow.json (the operator edits) | Python (`pr-chain/steps.py`) |
|---|---|
| which step runs next, for every outcome | the body of each step kind: run an agent, merge main, read CI, merge |
| entry points (run, land, approved, seed) | each step returns one outcome word, plus a reason when it ends the run |
| counters, limits, what happens at a limit | the engine loop (`pr-chain/engine.py`), about 60 lines |
| end states, their labels, retryable flags | |
| per-step args: model role, prompt file, verdict kind | |

A new *kind* of step needs one Python function. Reordering, adding or removing steps, rerouting an outcome, changing a
limit, a label, a model role or a prompt is a flow.json edit only.

## Node format

```json
"review": {
  "do": "agent_review",
  "args": {"kind": "review", "role": "review", "prompt": "review.md"},
  "count": "rounds", "limit": "max_rounds", "on_limit": "RESTING",
  "next": {"pass": "audit_needed", "fail": "same_findings", "behind": "drift", "no_verdict": "NO-VERDICT"}
}
```

- `do`: a step function in steps.py. Unknown name: the engine refuses to start (test_flow fails first).
- `args`: passed to the step as a dict.
- `count` / `limit` / `on_limit`: the engine adds 1 to the counter each time it enters the node. Over the limit it goes to
  `on_limit` instead of running the step. Counters live for one run unless the counter is listed in `persist`.
- `next`: outcome word to node or end state. An outcome with no edge ends the run as ERROR naming the node and outcome.
- End state: the engine calls `die(pr, state, why)`; die() keeps the labels, rests and retries it has today.

## Engine rules

1. Start at `entries[<entry>]`: `run` (normal), `land` (`pr-chain land`, READY), `approved` (human-approved, WAITING-CI
   after the gate), `seed` (a person's comment or a never-finished CI goes to the fixer first).
2. Before every node with `"kind": "agent"` the engine checks the STOP file (end state STOPPED).
3. Every node entered and every outcome is a ledger row: `{"stage": "step", "node", "outcome"}`.
4. Global guard: more than `limits.max_steps` nodes in one run ends it as ERROR (no loop can run forever, whatever the JSON says).
5. The engine holds no chain logic. Everything that decides a route is a step returning an outcome.

## Proof before the switch

1. tests/test_scenarios.py (374 checks), test_ui_loop.py, test_drift.py: unchanged tests, green on the engine.
2. tests/test_flow.py: every `do` exists, every outcome a step can return has an edge (steps declare their outcomes),
   every node reachable, no edge to nowhere.
3. `pr-chain run <pr> --dry-run` on a real open PR walks the graph and prints each node.
4. One real PR end to end on the engine, ledger read back.
5. Then `run_chain()` becomes one call into the engine, and the old body is deleted.

## Work plan

| Step | Files | Done when |
|---|---|---|
| 1. format + node graph | flow.json, ENGINE.md | every branch of run_chain has a node |
| 2. engine loop | pr-chain/engine.py | unit checks: counters, on_limit, missing edge, max_steps, STOP |
| 3. step functions | pr-chain/steps.py | one function per `do`, each a slice of today's run_chain |
| 4. parity | tests (unchanged) | all green with run_chain pointed at the engine |
| 5. live | bin/pr-chain | dry run, one real PR, then the switch |
