"""flow.json is the chain's program (ENGINE.md). This test fails when it cannot run: an unknown step, an outcome a step can
return with no edge, an edge to nowhere, an unreachable node, a limit nobody reads, an end state the code sets that the graph
lacks, or labels that disagree with the code. No gh, no agents."""
import ast, atexit, importlib.machinery, importlib.util, json, os, pathlib, re, shutil, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp(prefix="flow--prc"))
atexit.register(shutil.rmtree, tmp, True)
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt")
root = pathlib.Path(__file__).resolve().parents[2]
src = root / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
sys.dont_write_bytecode = True
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader)); loader.exec_module(pc)
import chain_engine, chain_steps  # noqa: E402  on sys.path once pr-chain is loaded
flow = json.loads(chain_engine.FLOW_PATH.read_text())
steps_src = (root / "chain/chain_steps.py").read_text()
text = src.read_text() + steps_src + (root / "chain/chain_engine.py").read_text()
fails = []
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

nodes, ends, limits = flow["nodes"], flow["end_states"], flow["limits"]
check("the engine finds nothing that would stop it mid-run", chain_engine.check(flow), [])

# ---- every outcome word a step can return has an edge at every node that runs that step
fns = {f.name: f for f in ast.parse(steps_src).body if isinstance(f, ast.FunctionDef)}
def outs(e, seen):
    """Outcome words an expression can evaluate to: string constants, both arms of an if, dict values and .get defaults,
    and whatever a called step helper returns. Call arguments (dict keys, messages) are not outcomes."""
    if isinstance(e, ast.Constant) and isinstance(e.value, str) and re.fullmatch(r"[a-z_]+", e.value):
        return {e.value}
    if isinstance(e, ast.IfExp):
        return outs(e.body, seen) | outs(e.orelse, seen)
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Name) and e.func.id in fns and e.func.id not in seen:
        return returns(e.func.id, seen)
    if isinstance(e, ast.Call) and isinstance(e.func, ast.Attribute) and e.func.attr == "get" and isinstance(e.func.value, ast.Dict):
        return set().union(*(outs(v, seen) for v in e.func.value.values), *(outs(x, seen) for x in e.args[1:]))
    return set()
def returns(name, seen=()):
    out = set()
    for r in (x for x in ast.walk(fns[name]) if isinstance(x, ast.Return) and x.value is not None):
        out |= outs(r.value, seen + (name,))
    return out - {"ended"}
step_of = {fn.__name__: k for k, fn in chain_steps.STEPS.items()}
missing = []
for nid, nd in nodes.items():
    fn = chain_steps.STEPS[nd["do"]].__name__
    missing += [f"{nid} ({nd['do']}) can return {o!r}" for o in sorted(returns(fn)) if o not in nd["next"]]
check("every outcome a step can return has an edge", missing, [])

# ---- reachability: every node and end state can be reached from start
seen, todo = set(), [flow["start"]]
while todo:
    n = todo.pop()
    if n in seen:
        continue
    seen.add(n)
    nd = nodes.get(n, {})
    todo += list(nd.get("next", {}).values()) + ([nd["on_limit"]] if nd.get("on_limit") else [])
    todo += list(ends.get(n, {}).get("resume", {}).values())
check("every node is reachable from start", sorted(set(nodes) - seen), [])
reach_by_code = {"ERROR", "STOPPED"}  # the engine itself routes here (exceptions, max_steps, STOP before an agent)
check("every end state is reachable", sorted(k for k in ends if k not in seen | reach_by_code and not ends[k].get("unused")), [])
check("every resume edge lands on a node", [(s, t) for s, v in ends.items() for t in v.get("resume", {}).values() if t not in nodes], [])

# ---- end states: everything the code can set is in the graph
not_status = {"FAIL", "PASS", "BEHIND", "CONFLICT", "STACK-DOWN", "GREEN", "DISPUTE", "FIXED", "BLOCKED", "OPEN", "BLOCKED POLICY", "DRY"}
emitted = set()
for line in text.splitlines():
    if "die(" in line and "def die" not in line:
        emitted |= set(re.findall(r'"([A-Z]{2,}(?:-[A-Z]+)*)"', line)) - not_status
emitted |= {"CI-RED", "CI-TIMEOUT", "CI-HEAD-MOVED", "MERGED", "LAND-STOPPED", "AWAIT-HUMAN"}
check("every end state the code sets is in flow.json", sorted(emitted - set(ends)), [])
check("retryable states come from flow.json", sorted(pc.RETRYABLE), sorted(k for k, v in ends.items() if v.get("retryable")))

# ---- limits: every one is read (by the code or by a node), none is hardcoded beside it
used = set(re.findall(r'LIM\["([a-z_]+)"\]', text)) | {v["limit"] for v in nodes.values() if v.get("limit")}
check("every limit in flow.json is read", sorted(set(limits) - used), [])
check("nothing reads a limit flow.json lacks", sorted(used - set(limits)), [])
for const, key in (("RETRY_CAP", "retry_cap"), ("LAND_TRIES", "land_tries"), ("DRIFT_TRIES", "drift_tries"),
                   ("LAND_FIX_CAP", "land_fix_cap"), ("REDESIGN_TRIES", "redesign_tries"), ("REST_STOP", "rest_stop")):
    check(f"{const} equals flow.json {key}", getattr(pc, const), limits[key]["value"])
for flag, key in (("max-rounds", "max_rounds"), ("max-audits", "max_audits"), ("max-ui", "max_ui")):
    check(f"--{flag} defaults to flow.json {key}", bool(re.search(rf'"--{flag}", type=int, default=LIM\["{key}"\]', text)), True)

# ---- labels and roles
labels = flow["labels"]
check("every end-state label has a description", sorted(v["label"] for v in ends.values() if v.get("label") and v["label"] not in labels), [])
check("needs_person_label has a description", flow["needs_person_label"] in labels, True)
check("descriptions fit GitHub's 100 characters", [k for k, v in labels.items() if len(v) > 100], [])
check("code labels the same states as flow.json", pc.CHAIN_LABELS, {k: v["label"] for k, v in ends.items() if v.get("label")})
roles = set(pc.MODEL_ROLES) | {"redesign"}
check("every agent node names a real role", [(n, v.get("args", {}).get("role")) for n, v in nodes.items()
                                             if v.get("kind") == "agent" and v.get("args", {}).get("role") not in roles], [])
check("every counter in the docs names a real limit", [k for k, v in flow["counters"].items() if v.get("limit") and v["limit"] not in limits], [])

# ---- -1 means unlimited, everywhere : tool calls, the agent timeout, any limit a node reads
import math, types  # noqa: E401,E402
check("agent_calls covers every stage the code budgets", sorted(flow.get("agent_calls", {})),
      ["addendum", "build", "fix", "gate", "redesign", "review", "scout", "ui"])
check("a -1 call budget is no limit", all(pc.CAPS[k] == math.inf for k, v in flow.get("agent_calls", {}).items() if v < 0), True)
check("limits: -1 reads as no limit", [k for k, v in limits.items() if v["value"] < 0 and pc.LIM[k] != math.inf], [])
check("agent timeout -1 means no timeout", (limits.get("agent_timeout_minutes", {}).get("value"), getattr(pc, "AGENT_TIMEOUT", "missing")), (-1, None))
check("engine: --max-rounds -1 is no limit", chain_engine.limit_of(types.SimpleNamespace(a=types.SimpleNamespace(max_rounds=-1), pc=pc), "max_rounds"), math.inf)
(tmp / "p").mkdir(); (tmp / "p" / "t.md").write_text("{{CALLS_RULE}}")
pc.config.PROMPTS_BASE = pc.config.PROMPTS_USER = tmp / "p"
check("brief: no limit says so, never 'inf tool calls'", pc.render("t.md", CALLS=math.inf, CALLS_SOFT=math.inf).startswith("No tool-call limit"), True)
check("brief: a set budget still reads as a number", pc.render("t.md", CALLS=80, CALLS_SOFT=72).startswith("You have 80 tool calls. At 72"), True)
check("no brief states its own call budget: every one uses {{CALLS_RULE}}", sorted(f.name for f in (root / "chain/prompts").glob("*.md")
      if "{{CALLS}}" in f.read_text() or re.search(r"\b\d+ tool calls", f.read_text())), [])

# every claude profile that streams JSON passes --verbose: claude -p refuses stream-json without it and the agent dies in 2 s
# as NO-VERDICT (replay 11-16 23:46: the gate moved to claude-haiku-max and #1180's gate died that way)
cmds = json.loads((root / "chain/commands.json").read_text())
check("claude stream-json profiles pass --verbose", sorted(k for k, v in cmds.items() if v.startswith("claude ") and "stream-json" in v
                                                          and "--verbose" not in v), [])

# the builder stops at max_open_prs chain PRs (11-17: a CI jam made every PR WAITING-CI, so auto built 23 new PRs);
# outside authors' PRs don't count
pc.WORK.mkdir(parents=True, exist_ok=True)
real_gh, real_claim, cap, real_floor = pc.gh_json, pc.claim, pc.LIM["max_open_prs"], pc.DISK_FLOOR_GB
pc.DISK_FLOOR_GB = 0  # the floor reads this host's free disk; these checks are about the cap (test_drift covers the floor)
pc.claim = lambda key: False  # under the cap the builder goes on to claim: stop it there
pc.LIM["max_open_prs"] = 3
pc.gh_json = lambda *a: [{"author": {"login": "owner"}}] * 2 + [{"author": {"login": "bot"}}] + [{"author": {"login": "someone"}}] * 9
check("builder: at the open-PR cap it builds nothing", pc.build_issue(1, None).startswith("skipped: 3 chain PRs are open"), True)
pc.gh_json = lambda *a: [{"author": {"login": "owner"}}] * 2 + [{"author": {"login": "someone"}}] * 9
check("builder: under the cap it goes on (outside PRs don't count)", pc.build_issue(1, None), "CLAIMED")
pc.LIM["max_open_prs"] = math.inf
check("builder: max_open_prs -1 never caps", pc.build_issue(1, None), "CLAIMED")
pc.gh_json, pc.claim, pc.LIM["max_open_prs"], pc.DISK_FLOOR_GB = real_gh, real_claim, cap, real_floor

# an approved PR whose required CI is red goes through the normal run (rerun or fixer), not the landing shortcut (#1184 11-16)
saved = {k: getattr(pc, k) for k in ("human_signal", "ci_red", "DRY", "main_flake_rerun")}
pc.DRY, pc.human_signal, pc.main_flake_rerun = False, (lambda pr, st: ("approved", None)), (lambda pr, head, bad: False)
st_ok = {"gate_head": "a" * 40}
pc.ci_red = lambda pr, head: [{"name": "ci/tests"}]
check("approval: red CI drops the landing shortcut", pc.standing_approval(1, {}, st_ok, "a" * 40), None)
pc.ci_red = lambda pr, head: []
check("approval: green or pending CI keeps it", pc.standing_approval(1, {}, st_ok, "a" * 40), st_ok)
for k, v in saved.items():
    setattr(pc, k, v)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall flow checks passed")
sys.exit(1 if fails else 0)
