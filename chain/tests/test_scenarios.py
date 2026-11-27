"""Every way a chain can end, replayed on stubs (no gh, no agents, no git), plus what `pick` and `resume` do next.
The end states are read out of the script itself: a new die() status with no row in POLICY fails this test.
Replays from the real ledger (2026-11-09 to 11-13): DoD gate refusing the merge, stale worktree ERROR, reviewer
with no verdict, CI timeout, fixer blocked, same findings twice, outside-contributor PR parked at the human gate."""
import atexit, importlib.machinery, importlib.util, json, os, pathlib, re, shutil, sys, tempfile, time, types

tmp = pathlib.Path(tempfile.mkdtemp(prefix="scen--prc"))
atexit.register(shutil.rmtree, tmp, True)
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt")
# no test reaches GitHub: a gh that always fails sits first on PATH for every module this file loads
(tmp / "nogh").mkdir(); (tmp / "nogh" / "gh").write_text("#!/bin/sh\necho 'gh is stubbed in tests' >&2\nexit 1\n"); (tmp / "nogh" / "gh").chmod(0o755)
os.environ["PATH"] = str(tmp / "nogh") + os.pathsep + os.environ.get("PATH", "")
src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
sys.dont_write_bytecode = True
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader)); loader.exec_module(pc)
assert str(pc.WORK).startswith(str(tmp)), pc.WORK
fails = []
pc.log = lambda m: None  # the chain's own stage lines would bury the check lines
GH_CALLS = []  # no test may reach GitHub: the STUCK scenario filed a real issue (#1051) on 11-14 before this stub existed
pc.sh = lambda *c, **k: (GH_CALLS.append(c), __import__("types").SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

# what `pick` does with a PR whose chain ended in this state, head unchanged
POLICY = {
    "retry":  ["ERROR", "NO-VERDICT", "UI-NO-VERDICT", "MAIN-MOVING", "CI-TIMEOUT", "BUILD-FAILED", "HOLD-UI", "CI-RED"],  # machinery failed
    "free":   ["CLAIMED", "STOPPED"],                                   # always free again
    "rest":   ["RESTING"],                                              # models could not finish it: back after a timed rest
    "parked": ["POLICY-HOLD", "STUCK", "NEEDS-PERSON", "READY", "MERGED", "CLOSED", "NOT-OURS",  # a person or a new head
               "CI-HEAD-MOVED", "AWAIT-HUMAN", "LAND-STOPPED", "WAITING-CI"],                   # the last two are `resume`'s job
}
known = {s for v in POLICY.values() for s in v}
text = src.read_text()
emitted = set(re.findall(r'die\((?:pr|getattr\(a, "pr", 0\)), "([A-Z-]+)"', text)) - {"CI-"}
emitted |= set(re.findall(r'"([A-Z]+-[A-Z-]+|[A-Z]{5,})" if ', text)) & set(re.findall(r'"([A-Z-]+)"', text))
emitted |= {"CI-TIMEOUT", "CI-RED", "CI-HEAD-MOVED", "AWAIT-HUMAN", "LAND-STOPPED", "MERGED", "HOLD-UI"}
emitted -= {"FAIL", "PASS", "BEHIND", "CONFLICT", "STACK-DOWN", "GREEN", "DISPUTE", "FIXED", "BLOCKED", "OPEN"}
check("every end state in the script has a pick policy", sorted(emitted - known), [])

# ---------------------------------------------------------------- pick
N = 99999
pc.gh_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no gh in tests"))
P = dict(number=N, headRefOid="h0", author={"login": "owner"})
def state(**kw):
    sp = pc.state_path(N); sp.parent.mkdir(parents=True, exist_ok=True); sp.write_text(json.dumps(kw))
def free(p=P):
    return pc.owner_reason(p, "") is None
for s in POLICY["retry"]:
    state(status=s, head="h0", why="x"); check(f"pick: {s} at the same head is free again", free(), True)
    state(status=s, head="h0", why="x", retries={"h0": pc.RETRY_CAP}); check(f"pick: {s} is parked after {pc.RETRY_CAP} tries", free(), False)
    check(f"pick: {s} is free at a new head", free(dict(P, headRefOid="h1")), True)
for s in POLICY["free"]:
    state(status=s, head="h0"); check(f"pick: {s} is free", free(), True)
now = time.time()
state(status="RESTING", head="h0", rest_until=now + 600); check("pick: RESTING before its time is not free", free(), False)
state(status="RESTING", head="h0", rest_until=now - 1); check("pick: RESTING after its time is free", free(), True)
check("pick: RESTING is free at a new head", free(dict(P, headRefOid="h1")), True)
state(status="running", head="h0")
for i, hours in enumerate([1, 2, 4, 8, 8]):  # rests at one head double, max 8 h; never parked for a person
    pc.die(N, "RESTING", "round cap")
    st = json.loads(pc.state_path(N).read_text())
    check(f"die: rest {i + 1} at one head lasts {hours} h", round((st.get("rest_until", 0) - time.time()) / 3600), hours)
pc.set_state(N, head="h1"); pc.die(N, "RESTING", "x")
check("die: rests start over at a new head", round((json.loads(pc.state_path(N).read_text()).get("rest_until", 0) - time.time()) / 3600), 1)
for s in POLICY["parked"]:
    state(status=s, head="h0"); check(f"pick: {s} stays parked at the same head", free(), False)
    if s not in ("AWAIT-HUMAN", "WAITING-CI"):  # `resume` owns both at any head
        check(f"pick: {s} is free at a new head", free(dict(P, headRefOid="h1")), True)
state(status="ERROR", head="h0"); check("pick: an outside contributor PR is never free", free(dict(P, author={"login": "stranger"})), False)
state(status="running", head="h0")
for i in range(pc.RETRY_CAP):
    pc.die(N, "ERROR", "git worktree add: missing but already registered worktree")
    check(f"die: ERROR try {i + 1} leaves it {'free' if i + 1 < pc.RETRY_CAP else 'parked'}", free(), i + 1 < pc.RETRY_CAP)
pc.set_state(N, head="h1"); pc.die(N, "ERROR", "x")
check("die: the try count starts over at a new head", json.loads(pc.state_path(N).read_text())["retries"], {"h1": 1})

# ---------------------------------------------------------------- run_chain: one scenario per end state
FIXKW = []  # keyword args of each fix() call: done_in = the reviewer already fixed it in its own worktree
EXTRAS = []  # the `extra` text each review stage was handed, in order (last scenario only)
def scenario(name, want, reviews=(("review", "PASS"), ("gate", "PASS")), fixes=(), uis=(), ci="GREEN", land="MERGED", ui=False,
             author="owner", prstate="OPEN", claim=True, stop=False, fold=True, cover=None, heads=None, red=(), rerun=(), **flags):
    st = dict(head="h0", n=0)
    info = dict(number=1, state=prstate, files=[{"path": "packages/kollabor-webui/frontend/src/App.tsx" if ui else "kollabor/x.py"}],
                author={"login": author}, isCrossRepository=False, headRefName="pr", title="t", body="")
    shutil.rmtree(pc.WORK / "prs" / "1", ignore_errors=True)
    if "pre_state" in flags:
        pc.state_path(1).write_text(json.dumps(flags.pop("pre_state")))
    for rel, body in flags.pop("pre_files", {}).items():
        f = pc.WORK / "prs" / "1" / rel; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(body)
    pc.STOPFILE.parent.mkdir(parents=True, exist_ok=True)
    pc.STOPFILE.write_text("stop") if stop else pc.STOPFILE.unlink(missing_ok=True)
    pc.pr_info = lambda pr: info
    pc.branch_head = lambda i: heads if heads and not rs else st["head"]  # heads: the head once every review ran
    pc.fetch = lambda i: "m"
    pc.claim = lambda pr: claim
    pc.release = lambda pr: None
    def fold_(pr, i):
        if fred[0]:
            fred[0] = False; raise getattr(pc, "FoldRed", RuntimeError)("FAILED tests/unit/test_x.py::t - assert 1 == 2")
        return st["head"] if fold else None
    pc.fold = fold_
    k = [0]
    def vfile(v, body="- P1 x.py:1 - x - y"):
        k[0] += 1; vp = tmp / f"v{k[0]}.md"; vp.write_text(f"{v}\nhead {st['head']}\nFindings:\n{body}\n"); return vp
    rs, fs, us, cs, ls = list(reviews), list(fixes), list(uis), [ci] if isinstance(ci, str) else list(ci), [land] if isinstance(land, str) else list(land)
    cvs = list(flags.pop("covers", ()))  # per-lap landing-audit verdicts (kollab#1214); falls back to the one `cover` tuple
    EXTRAS.clear(); FIXKW.clear()
    fred = [flags.pop("fold_red", False)]
    def review(pr, rnd, kind, model, post, old=None, extra="", **kw):
        EXTRAS.append(extra)
        kd, v = rs.pop(0); assert kd == kind, (name, kd, kind); return v, vfile(v or "junk"), st["head"]
    def fix(pr, rnd, model, vp, head, last, **kw):
        FIXKW.append(kw)
        r = fs.pop(0)
        if r == "OK": st["n"] += 1; st["head"] = f"h{st['n']}"; return st["head"], "pushed"
        if r == "MAIN": st["n"] += 1; st["head"] = f"hm{st['n']}"; return st["head"], "pushed"  # the fixer's push only merged main (#1101, 11-16: the head was behind when it started)
        if r == "BODY": return head, "body-only fix posted"  # new-pr-body.md applied, no push: the head stands (kollab#1214)
        return ("DISPUTE", "no") if r == "DISPUTE" else (None, r)
    pc.review, pc.fix = review, fix
    reds = list(red)
    reruns = list(rerun)
    pc.ci_red = lambda pr, head: [{"name": "ci/tests"}] if reds and reds.pop(0) else []
    pc.ci_rerun = lambda pr, head, bad: bool(reruns and reruns.pop(0))
    pc.ci_findings = lambda pr, rnd, head, bad: vfile("FAIL", "- P1 ci:0 - required CI check `ci/tests` is red")
    pc.finding_key = (lambda t: "same") if flags.pop("same_findings", False) else (lambda t: t)
    pc.ui_review = lambda pr, rnd, head, a: (us.pop(0), vfile("ui"), head)
    pc.human_gate = lambda pr, head, ui_vp, a, **kw: pc.die(pr, "AWAIT-HUMAN", "screenshots posted")
    gates = list(flags.pop("gates", ()))
    pc.gate_findings = lambda pr: gates.pop(0) if gates else []
    pend = flags.pop("ci_pending", False)  # one value, or one per look: the look before each review, then the landing look
    pend = list(pend) if isinstance(pend, (list, tuple)) else [pend]
    pc.ci_pending = lambda pr, head: pend.pop(0) if len(pend) > 1 else pend[0]
    pc.wait_ci = lambda pr, head, minutes=45: cs.pop(0) if len(cs) > 1 else cs[0]
    pc.land = lambda pr, head, reviewer: ls.pop(0) if len(ls) > 1 else ls[0]
    pc.cover_moved_head = lambda pr, a, old, tag, first: (cvs.pop(0) if cvs else cover)
    a = types.SimpleNamespace(max_rounds=4, max_audits=2, vision_ok=False, no_gate=False, no_post=True, no_human=False, land=True,
                              max_ui=3, review_model="rev", gate_model="gate", ui_model="ui", fix_model="fix")
    kw = {x: flags.pop(x) for x in ("from_gate", "approved") if x in flags}
    a.land = not flags.pop("no_land", False)
    a.__dict__.update(flags)
    got = scenario.got = pc.run_chain(1, a, **kw)
    if want is None:  # a caller that judges the result itself (the many-run treadmill below)
        pc.STOPFILE.unlink(missing_ok=True)
        return dict(reviews_left=len(rs), fixes_left=len(fs), covers_left=len(cvs))
    check(name, got, want)
    if want == "CLAIMED":  # a live claim elsewhere: this run writes no state at all (auditor #6)
        check(name + " (no state written)", pc.state_path(1).exists() and json.loads(pc.state_path(1).read_text()).get("status"), False)
    else:
        check(name + " (state file agrees)", json.loads(pc.state_path(1).read_text()).get("status"), want)
    pc.STOPFILE.unlink(missing_ok=True)
    return dict(reviews_left=len(rs), fixes_left=len(fs), covers_left=len(cvs))

R, G = ("review", "PASS"), ("gate", "PASS")
DOD = ("LAND-FAILED: STOP: dod/ready-for-review failed after ready: pr-gates DoD: 1 failed item(s):\n"
       "- regression_test: no regression test covers finding id(s): F63, F7, F82 under tests/")
scenario("happy path: review, audit, CI, merge", "MERGED")
scenario("no --land stops at READY", "READY", no_land=True)
scenario("merge refused by the DoD gate (replay #970)", "LAND-STOPPED", land=DOD)
scenario("merge refused: PR body has no issue link (replay #1001)", "LAND-STOPPED", land="LAND-FAILED: d_issue: no 'Fixes #N' / 'Closes #N'")
scenario("main moved while CI ran: fold again, then merge", "MERGED", land=["LAND-FAILED: PR is BEHIND", "MERGED"])
scenario("main moved during the merge wait, GitHub's other wording (replay #1058): fold again, then merge", "MERGED",
         land=["LAND-FAILED: STOP: gh pr merge failed: X Pull request is not mergeable: the head branch is not up to date with the base branch.", "MERGED"])
scenario("CI head moved once: wait again, then merge", "MERGED", ci=["HEAD-MOVED", "GREEN"])
scenario("CI still running at landing: the chain frees its slot", "WAITING-CI", ci_pending=[False, True])
check("WAITING-CI keeps the head resume will land", json.loads(pc.state_path(1).read_text()).get("gate_head"), "h0")
# no reviewer reads a head whose required checks are still running: flow.json ci_pending_wait
left = scenario("CI still running before the review: no reviewer starts", "WAITING-CI", ci_pending=[True])
check("CI wait before the review: no review or gate ran", left["reviews_left"], 2)
check("CI wait before the review: resume knows no gate exists", json.loads(pc.state_path(1).read_text()).get("ci_wait"), "review")
# the merge gate runs before any reviewer (replay #549: a feature PR with no spec reached UI review and a person's look)
left = scenario("merge-gate preflight: findings go to the fixer before any review", "MERGED",
                gates=[["WARNING spec_or_fix_brief: feature PR needs a changed docs/specs/*.md file"]], fixes=["OK"])
check("merge-gate preflight: fixer ran first, then review and audit", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
scenario("CI timeout (replay)", "CI-TIMEOUT", ci="TIMEOUT after 45 min")
scenario("CI red", "CI-RED", ci="RED ci/tests")
scenario("reviewer wrote no verdict (replay, 9 times)", "NO-VERDICT", reviews=[("review", None)])
scenario("audit wrote no verdict", "NO-VERDICT", reviews=[R, ("gate", None)])
scenario("main keeps moving before the review", "MAIN-MOVING", reviews=[("review", "BEHIND")] * 4)
scenario("main keeps moving before the audit", "MAIN-MOVING", reviews=[R] + [("gate", "BEHIND")] * 6)
# a fixer that cannot fix a finding says why; the next reviewer weighs that evidence (replay #1012: an OS limit main shares)
scenario("fixer blocked: the next reviewer weighs the fixer's evidence, then passes", "MERGED",
         reviews=[("review", "FAIL"), R, G], fixes=["fixer said BLOCKED P1 an OS limit main shares"])
check("fixer blocked: the re-review was handed the fixer's report", len(EXTRAS) > 1 and "an OS limit main shares" in EXTRAS[1], True)
left = scenario("fixer blocked, the reviewer keeps the same findings", "RESTING",
                reviews=[("review", "FAIL")] * 2, fixes=["fixer said BLOCKED x"], same_findings=True)
check("fixer blocked, same findings: one re-review ran, then it stopped", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
# review-fix: the reviewer that fails a PR fixes it itself; a NEW reviewer checks the fix
scenario("review-fix: the reviewer fixes its own FAIL, a fresh reviewer passes", "MERGED", reviews=[("review", "FAIL"), R, G], fixes=["OK"])
check("review-fix: the fix came from the reviewer's own worktree", bool(FIXKW) and bool(FIXKW[0].get("done_in")), True)
left = scenario("review-fix made no commit: the plain fixer takes it", "MERGED", reviews=[("review", "FAIL"), R, G], fixes=["fixer made no commit", "OK"])
check("review-fix no commit: plain fixer ran without done_in", len(FIXKW) == 2 and not FIXKW[1].get("done_in"), True)
scenario("the only fix breaks a rule (history rewrite, a hold in the PR body): POLICY-HOLD", "POLICY-HOLD",
         reviews=[("review", "FAIL")], fixes=["fixer said BLOCKED POLICY history rewrite needed", "fixer said BLOCKED POLICY x"])
left = scenario("red tests in the fold (replay #937): the fixer gets them, then review, audit, merge", "MERGED", fold_red=True, fixes=["OK"])
check("fold red: the plain fixer ran first, then both reviews", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
scenario("fixer wrote no report: no re-review", "RESTING", reviews=[("review", "FAIL")], fixes=["fixer said NO-REPORT"] * 2)
scenario("fixer made no commit (replay)", "RESTING", reviews=[("review", "FAIL")], fixes=["fixer made no commit"] * 2)
scenario("same findings two rounds in a row (replay)", "RESTING", reviews=[("review", "FAIL")] * 2, fixes=["OK"], same_findings=True)
scenario("audit fails more than --max-audits", "RESTING", reviews=[R, ("gate", "FAIL"), R, ("gate", "FAIL"), R], fixes=["OK", "OK"])
scenario("fix then pass then merge", "MERGED", reviews=[("review", "FAIL"), R, G], fixes=["OK"])
scenario("fixer disputes, reviewer then passes", "MERGED", reviews=[("review", "FAIL"), R, G], fixes=["DISPUTE"])
scenario("conflict with main after the audit", "RESTING", reviews=[("review", "FAIL")], fixes=["conflict unresolved"], fold=False)
scenario("PR already merged or closed", "CLOSED", prstate="MERGED")
scenario("outside contributor PR", "NOT-OURS", author="stranger")
scenario("another chain holds the PR", "CLAIMED", claim=False)
scenario("STOP file present", "STOPPED", stop=True)
scenario("UI PR: screenshots posted, waits for a person", "AWAIT-HUMAN", ui=True, uis=["PASS"])
scenario("UI PR: scratch stack would not start", "BUILD-FAILED", ui=True, uis=["STACK-DOWN"])
scenario("UI PR: vision reviewer gave no verdict", "UI-NO-VERDICT", ui=True, uis=[None])
scenario("UI PR: a NOT-SHOWN answer is no verdict (11-16): no human gate, retried like any missing verdict", "UI-NO-VERDICT", ui=True, uis=["NOT-SHOWN"])
scenario("UI PR: vision review fails past --max-ui", "RESTING", ui=True, uis=["FAIL"] * 4, fixes=["OK"] * 3)
scenario("UI PR: UI fixer blocked (replay #648, #549)", "RESTING", ui=True, uis=["FAIL"], fixes=["fixer said BLOCKED"])
scenario("land: audit before landing fails", "RESTING", reviews=[("gate", "FAIL")], from_gate=True)
scenario("land: audit passes, merge", "MERGED", reviews=[G], from_gate=True)
scenario("head moved after the audit, addendum covers it", "MERGED", heads="h9", cover=("PASS", tmp, "h9", "addendum"))
scenario("head moved after the audit, new audit fails", "RESTING", heads="h9", cover=("FAIL", tmp, "h9", "audit"))
scenario("approved by a person: straight to CI and merge", "MERGED", reviews=[], ui=True, approved={"gate_head": "h0"})

# kollab#1214: an approved PR whose landing audit fails had no fixer (it rested and replayed the same FAIL at the
# same head forever, #1055 11-16: three rests on one stale test count). The findings now go to one landing fixer.
left = scenario("approved PR: landing audit FAIL, text-only fix, audit runs again, lands", "MERGED", reviews=[], ui=True,
                approved={"gate_head": "h0"}, heads="h9", fixes=["BODY"],
                covers=[("FAIL", tmp, "h9", "audit"), ("PASS", tmp, "h9", "audit")],
                pre_files={"rland1-gate/verdict.md": "FAIL\nhead h9\nFindings:\n- P2 pr.md - the test count went stale\n"})
check("land-fix: the audit ran again at the same head after the text-only fix", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
lf = pc.WORK / "prs" / "1"
renamed = sorted(x.name for x in lf.glob("rland1-gate*"))
check("land-fix: the fixed head's stored FAIL is retired (.old rename), so no run replays it",
      len(renamed) == 1 and renamed[0].startswith("rland1-gate.old"), True)
scenario("approved PR: landing fixer pushed code: RESTING, the approval stands for no new head", "RESTING", reviews=[], ui=True,
         approved={"gate_head": "h0"}, heads="h9", fixes=["OK"], covers=[("FAIL", tmp, "h9", "audit")])
st = json.loads(pc.state_path(1).read_text())
check("land-fix: the why says the fixer pushed code", "pushed code" in (st.get("why") or ""), True)
check("land-fix: no gate_head was written for the pushed head (no standing approval)", st.get("gate_head"), None)
check("land-fix: STATE keys the audited head, so pick sees the pushed head as new and free", st.get("head"), "h9")
# the first live use of the landing fixer (#1101, 11-16 20:46): it fixed the PR text, but the head had moved by main, so the push it
# made was a merge of main. That is no code: the approval stands and the audit runs again, instead of a 1 h rest and a full review.
_moo = pc.main_only_move; pc.main_only_move = lambda old, new: str(new).startswith("hm")
left = scenario("approved PR: landing fixer's push only merged main: the approval stands, the audit runs again, lands", "MERGED", reviews=[], ui=True,
                approved={"gate_head": "h0"}, heads="h9", fixes=["MAIN"],
                covers=[("FAIL", tmp, "h9", "audit"), ("PASS", tmp, "h9", "audit")],
                pre_files={"rland1-gate/verdict.md": "FAIL\nhead h9\nFindings:\n- P2 pr.md - the PR text omits changed files\n"})
check("land-fix: a main-only push still gets the audit again", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
pc.main_only_move = _moo
scenario("approved PR: landing fixer disputes the audit findings: RESTING", "RESTING", reviews=[], ui=True,
         approved={"gate_head": "h0"}, heads="h9", fixes=["DISPUTE"], covers=[("FAIL", tmp, "h9", "audit")])
check("land-fix: the why names the dispute", "disputed" in (json.loads(pc.state_path(1).read_text()).get("why") or ""), True)
left = scenario("approved PR: a third landing FAIL at one head rests with no third fixer", "RESTING", reviews=[], ui=True,
                approved={"gate_head": "h0"}, heads="h9", fixes=["BODY", "BODY"],
                covers=[("FAIL", tmp, "h9", "audit")] * 3)
check("land-fix: at most two fixer runs at one head (the third FAIL rests)", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
check("land-fix: the cap is counted per head in STATE", json.loads(pc.state_path(1).read_text()).get("land_fixes"), {"h9": 2})
# The push is a fresh attempt on a PR that had converged (it was approved). With 7 earlier rests the next run would otherwise
# start the Sol redesign before anyone reviewed the push (rest_total >= REST_STOP and no standing approval for the new head).
scenario("approved PR with 7 rests: landing fixer pushed code: RESTING", "RESTING", reviews=[], ui=True,
         approved={"gate_head": "h0"}, heads="h9", fixes=["OK"], covers=[("FAIL", tmp, "h9", "audit")],
         pre_state=dict(status="running", head="h0", rest_total=7, rests={"h0": 7}))
check("land-fix: a code push restarts the rest count below the redesign threshold",
      json.loads(pc.state_path(1).read_text()).get("rest_total", 99) < pc.REST_STOP, True)

# a failed final screenshot round (replay #1012 23:43): the next run hands the UI findings to the fixer before any review
uiv = tmp / "ui-final.md"; uiv.write_text("FAIL\nhead h0\nFindings:\n- P2 shot.png - no file picker at 390px - touch\n")
left = scenario("final UI FAIL: next run's fixer takes the UI findings first", "MERGED", reviews=[R, G], fixes=["OK"],
                pre_state=dict(status="RESTING", head="h0", ui_seed=str(uiv)))
check("final UI FAIL seed: fixer ran first, then review and audit", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
check("final UI FAIL seed: used once", json.loads(pc.state_path(1).read_text()).get("ui_seed") or "", "")

# whack-a-mole (replay #1036: 7 cycles, a new git hole each time): after 2 rests the review sees every earlier finding as one class
old = {"r1-review.old1/verdict.md": "FAIL\nhead a\nFindings:\n- P2 x.py:9 - git aliases run programs - alias\n",
       "r2-review.old2/verdict.md": "FAIL\nhead b\nFindings:\n- P2 x.py:9 - --paginate runs the pager - pager\n"}
scenario("rested twice: the review is told the findings are one class", "MERGED", pre_state=dict(status="RESTING", head="h0", rest_total=2), pre_files=old)
check("class history reaches the first review", bool(EXTRAS) and "git aliases run programs" in EXTRAS[0] and "--paginate runs the pager" in EXTRAS[0], True)
scenario("rested twice and round 1 is a fixer job: the later review still gets the history", "MERGED", fold_red=True, fixes=["OK"],
         pre_state=dict(status="RESTING", head="h0", rest_total=2), pre_files=old)
check("class history survives a fixer round (replay #1036 10:39)", bool(EXTRAS) and "git aliases run programs" in EXTRAS[0], True)
# STUCK is not the end: a strong redesign agent takes the whole finding history first
left = scenario("rested six times: a redesign agent fixes the class, then review, audit, merge", "MERGED",
                pre_state=dict(status="RESTING", head="h0", rest_total=6), pre_files=old, fixes=["OK"])
check("redesign: the first fix ran with the redesign brief", bool(FIXKW) and FIXKW[0].get("prompt") == "redesign.md", True)
check("redesign: the rest count starts over after a pushed redesign", json.loads(pc.state_path(1).read_text()).get("rest_total"), 0)
scenario("redesign tried twice already: STUCK, no more agent runs", "STUCK", pre_state=dict(status="RESTING", head="h0", rest_total=6, redesigns=2),
         pre_files=old, reviews=[])
scenario("the redesign agent is blocked: STUCK", "STUCK", pre_state=dict(status="RESTING", head="h0", rest_total=6), pre_files=old,
         reviews=[], fixes=["fixer said BLOCKED the class needs a product decision"])
# STUCK means this head is unfixable as designed, not that the PR is cursed: a real new push lifts it
scenario("STUCK at an old head: a real new push lifts it, then review, gate, merge", "MERGED",
         pre_state=dict(status="STUCK", head="h9", stuck_head="h9", rest_total=6, redesigns=2), pre_files=old)
st1 = json.loads(pc.state_path(1).read_text())
check("STUCK lifted: rest count, redesigns and lift count start over", (st1.get("rest_total"), st1.get("redesigns"), st1.get("stuck_lifts")), (0, 0, 1))
scenario("STUCK lifted twice already: the next STUCK cycle ends NEEDS-PERSON", "NEEDS-PERSON",
         pre_state=dict(status="STUCK", head="h9", stuck_head="h9", rest_total=6, redesigns=2, stuck_lifts=2), pre_files=old, reviews=[])
left = scenario("a person removed chain/needs-person: every counter starts over, then merge", "MERGED",
                pre_state=dict(status="NEEDS-PERSON", head="h0", chain_label="chain/needs-person", rest_total=6, redesigns=2, stuck_lifts=2),
                pre_files=old)
st1 = json.loads(pc.state_path(1).read_text())
check("NEEDS-PERSON released: counters start over", (st1.get("rest_total"), st1.get("redesigns"), st1.get("stuck_lifts")), (0, 0, 0))
GH_CALLS.clear()
scenario("going STUCK labels the PR and says why once", "STUCK", pre_state=dict(status="RESTING", head="h0", rest_total=6, redesigns=2),
         pre_files=old, reviews=[])
check("STUCK: chain/stuck label added", [c[-1] for c in GH_CALLS if c[:3] == ("gh", "pr", "edit") and "--add-label" in c], ["chain/stuck"])
check("STUCK: one comment on the PR", sum(1 for c in GH_CALLS if c[:3] == ("gh", "pr", "comment")), 1)
check("STUCK: remembers the head it stopped on", json.loads(pc.state_path(1).read_text()).get("stuck_head"), "h0")
GH_CALLS.clear()
scenario("round cap: RESTING shows the chain/resting label", "RESTING", reviews=[("review", "FAIL")] * 4, fixes=["OK"] * 4)
check("RESTING: chain/resting label added", [c[-1] for c in GH_CALLS if c[:3] == ("gh", "pr", "edit") and "--add-label" in c], ["chain/resting"])
GH_CALLS.clear()
scenario("a run after a rest clears the label, then merges", "MERGED", pre_state=dict(status="RESTING", head="h0", chain_label="chain/resting"))
check("label cleared when the state clears", [c[-1] for c in GH_CALLS if c[:3] == ("gh", "pr", "edit") and "--remove-label" in c][:1], ["chain/resting"])
state(status="STUCK", head="h0", stuck_head="h0"); check("pick: STUCK stays parked at its own head", free(), False)
check("pick: STUCK is free after a new push", free(dict(P, headRefOid="h1")), True)
state(status="NEEDS-PERSON", head="h0", chain_label="chain/needs-person")
check("pick: NEEDS-PERSON is held while its label is on, even at a new head",
      free(dict(P, headRefOid="h1", labels=[{"name": "chain/needs-person"}])), False)
check("pick: NEEDS-PERSON is free once a person removed the label", free(dict(P, labels=[])), True)
state(status="NO-VERDICT", head="h0", retries={"h0": 3}, chain_label="chain/needs-person")
check("pick: retry cap with chain/needs-person on stays parked", free(dict(P, labels=[{"name": "chain/needs-person"}])), False)
check("pick: retry cap is free once a person removed the label", free(dict(P, labels=[])), True)
# ---------------------------------------------------------------- very low limits: an agent that can never finish
F = ("review", "FAIL")
scenario("max_rounds 1, the review always fails: RESTING after one round", "RESTING", reviews=[F], fixes=["OK"], max_rounds=1)
check("max_rounds 1: why names the round cap", "round cap" in json.loads(pc.state_path(1).read_text())["why"], True)
scenario("max_audits 1, the gate always fails: RESTING after one gate", "RESTING",
         reviews=[R, ("gate", "FAIL"), R], fixes=["OK"], max_audits=1)
check("max_audits 1: why names the audit cap", json.loads(pc.state_path(1).read_text())["why"], "the audit failed 1 times")
scenario("max_ui 1, the UI review always fails: RESTING after one UI fix", "RESTING", ui=True, uis=["FAIL", "FAIL"], fixes=["OK"], max_ui=1)
scenario("max_rounds 0: not even one review runs", "RESTING", reviews=[], max_rounds=0)
dt = pc.LIM["drift_tries"]; pc.LIM["drift_tries"] = pc.DRIFT_TRIES = 1
scenario("drift_tries 1, main always moving: MAIN-MOVING after one fold", "MAIN-MOVING", reviews=[("review", "BEHIND")] * 2)
pc.LIM["drift_tries"] = pc.DRIFT_TRIES = dt
# -1 = no limit: an agent that needs ten rounds gets them, and one that never finishes still cannot run forever
scenario("max_rounds -1: ten failing rounds, then a pass, then merge", "MERGED", reviews=[F] * 10 + [R, G], fixes=["OK"] * 10, max_rounds=-1)
ms = pc.LIM["max_steps"]; pc.LIM["max_steps"] = 120
if os.environ.get("PR_CHAIN_ENGINE") == "flow":  # the step guard is the engine's; the old loop has only its round cap
    scenario("max_rounds -1 and a reviewer that never passes: max_steps ends the run", "ERROR", reviews=[F] * 60, fixes=["OK"] * 60,
             max_rounds=-1)
    check("max_steps: why names the step guard", "nodes without reaching an end state" in json.loads(pc.state_path(1).read_text())["why"], True)
pc.LIM["max_steps"] = ms
# every fix push restarts CI, so a run parks for CI after one round; the round count must carry into the next run or the
# round cap never fires (replay #1244 11-17: four runs, each "round 1", review FAIL -> fix -> wait, no end)
scenario("CI wait after a fix: run 1 parks at round 2", "WAITING-CI", reviews=[F], fixes=["OK"], ci_pending=[False, True], max_rounds=2)
st1 = json.loads(pc.state_path(1).read_text())
check("CI wait: the parked run saves its round", st1.get("ci_wait_round"), 2)
scenario("CI wait after a fix: run 2 continues at round 2 and hits the round cap", "RESTING",
         pre_state={k: v for k, v in st1.items() if k != "chain_label"}, reviews=[F], fixes=["OK"], ci_pending=[False, True], max_rounds=2)
# the treadmill: every run goes over max_rounds 1, run after run. It must stop on its own: 6 rests -> redesign fails -> STUCK;
# a person pushes (lift 1, 2); the third STUCK cycle ends NEEDS-PERSON. No run count may grow without bound.
st, ends, labels = {}, [], []
for run in range(60):
    if st.get("status") == "NEEDS-PERSON":
        break
    pre = {k: v for k, v in st.items() if k not in ("rest_until", "status")} | {"status": st.get("status", "RESTING"), "head": "h0"}
    if st.get("status") == "STUCK":
        pre["stuck_head"] = "pushed-by-a-person"  # what a person's push looks like to the next run
    redesign = st.get("status") != "STUCK" and (st.get("rest_total") or 0) >= pc.REST_STOP  # a lift resets the counts first and (st.get("redesigns") or 0) < pc.REDESIGN_TRIES
    GH_CALLS.clear()
    scenario(f"treadmill run {run + 1}", None, pre_state=pre, max_rounds=1, reviews=[] if redesign else [F],
             fixes=["fixer said BLOCKED the class needs a product decision"] if redesign else ["OK"])
    st = json.loads(pc.state_path(1).read_text())
    ends.append(scenario.got)
    labels += [c[-1] for c in GH_CALLS if c[:3] == ("gh", "pr", "edit") and "--add-label" in c]
check("treadmill: ends NEEDS-PERSON on its own", ends[-1], "NEEDS-PERSON")
check("treadmill: 3 STUCK cycles of 6 rests, then the person gate (22 runs)", (len(ends), ends.count("RESTING"), ends.count("STUCK")), (22, 18, 3))
check("treadmill: every parked run was labelled on GitHub", sorted(set(labels)), ["chain/needs-person", "chain/resting", "chain/stuck"])
# a review PASS from an earlier run stands when only main moved: the next run goes straight to the gate (replay #1223 11-16)
d77, H = pc.WORK / "prs" / "77", "a" * 40
def verdict_at(rel, v, t):
    f = d77 / rel / "verdict.md"; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(f"{v}\nhead {H}\n"); os.utime(f, (t, t))
verdict_at("r1-review", "PASS", 100)
check("carry: an earlier review PASS at this head stands", pc.carried_pass(77, H, review=True), H)
check("carry: a review PASS is not a gate PASS", pc.carried_pass(77, H), None)
verdict_at("r1a1-gate", "FAIL", 200)
check("carry: a later gate FAIL voids the review PASS", pc.carried_pass(77, H, review=True), None)

# ---------------------------------------------------------------- resume: LAND-STOPPED and outside PRs
shutil.rmtree(pc.WORK / "prs", ignore_errors=True)
calls = []
# red CI goes to the fixer before any reviewer or vision model sees the head (replay #998: 5 failing tests, UI review ran anyway)
left = scenario("red CI at the start: fixer first, then review, audit, merge", "MERGED", red=[True], fixes=["OK"])
check("red CI: the fixer ran and both review stages ran after it", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
left = scenario("red CI and the fixer is blocked: stops, no reviewer ran", "RESTING", red=[True], fixes=["fixer said BLOCKED flaky"])
check("red CI blocked: no review was spent", left["reviews_left"], 2)
left = scenario("CI still red after the fix: second fixer lap, no reviewer in between", "MERGED", red=[True, True], fixes=["OK", "OK"])
check("red twice: two fixes, then one review and one audit", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
left = scenario("fixer says the red test is flaky, rerun is allowed: round is not spent, CI rechecked", "MERGED", red=[True, False], fixes=["fixer said BLOCKED flaky"], rerun=[True])
check("flaky rerun: no PR fix was applied, review and audit ran", left, dict(reviews_left=0, fixes_left=0, covers_left=0))
left = scenario("flaky rerun already used on this head: rests", "RESTING", red=[True], fixes=["fixer said BLOCKED flaky"], rerun=[False])
check("CI-RED at the merge step is retried by the loop", "CI-RED" in pc.RETRYABLE, True)
left = scenario("UI stack broken (replay #998): machinery state, no PR fixer", "BUILD-FAILED", ui=True, uis=["STACK-DOWN"], fixes=["OK"])
check("stack broken: the PR fixer never ran", left["fixes_left"], 1)

# the real ui_review: a STACK-BROKEN first line becomes STACK-DOWN, posts nothing, and is judged again on the next run
ucalls = []
real = importlib.machinery.SourceFileLoader("pc2", str(pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain")).load_module()
_real_sh0 = real.sh
real.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="") if c[:1] == ("gh",) else _real_sh0(*c, **k)  # tests never reach GitHub: every gh call is a no-op success unless a check stubs sh itself
real.pr_info = lambda pr: dict(title="t", body="", author={"login": "owner"}, files=[{"path": "packages/kollabor-webui/frontend/src/App.tsx"}])
real.fetch = lambda i: "m"
real.make_wt = lambda wt, head: wt.mkdir(parents=True, exist_ok=True)
real.drop_wt = lambda wt: None
real.issues_text = lambda i: ""
real.render = lambda tpl, **kv: "brief"
real.sh = lambda *c, **k: (ucalls.append(c), types.SimpleNamespace(returncode=0, stdout="http://127.0.0.1:1\n", stderr=""))[1]
def agent(role, model, brief, cwd, outdir, pr):
    (outdir / "ui-verdict.md").write_text("STACK-BROKEN\nhead h0\nversion banner, session never starts\n"); ucalls.append(("agent",)); return {}
real.run_agent = agent
shutil.rmtree(real.WORK / "prs" / "7", ignore_errors=True)
ns = types.SimpleNamespace(ui_model="ui", no_post=False)
check("ui_review maps STACK-BROKEN to STACK-DOWN", real.ui_review(7, 1, "h0", ns)[0], "STACK-DOWN")
check("STACK-BROKEN is not posted on the PR", [c for c in ucalls if c and c[0] == "gh"], [])
n = len([c for c in ucalls if c == ("agent",)])
real.ui_review(7, 1, "h0", ns)
check("a STACK-BROKEN file is not reused as a verdict on the next run", len([c for c in ucalls if c == ("agent",)]), n + 1)

# Follow-up: a gap main already has that the PR cannot close is listed apart, filed once as an issue, and is not a finding
check("finding_key ignores the Follow-up section", real.finding_key("FAIL\nFindings:\n- P1 a.py:1 - x - y\nFollow-up:\n- P2 b.py:2 - z - w\n"),
      real.finding_key("FAIL\nFindings:\n- P1 a.py:1 - x - y\n"))
ff, fu, sh0 = getattr(real, "file_followups", None), [], real.sh
check("the chain files Follow-up items (file_followups exists)", ff is not None, True)
if ff:
    real.sh = lambda *c, **k: (fu.append(c), types.SimpleNamespace(returncode=0, stdout="https://github.com/o/r/issues/77\n", stderr=""))[1]
    shutil.rmtree(real.WORK / "prs" / "8", ignore_errors=True)
    fv = tmp / "followup.md"
    fv.write_text("PASS\nhead h0\nFindings:\nnone\nFollow-up:\n- P2 a.py:3 - a gap main has - a daemonizing job\nReviewer audit: none\n- not this\n")
    ff(8, fv); ff(8, fv)
    made = [c for c in fu if c[:3] == ("gh", "issue", "create")]
    check("Follow-up: one issue per PR, however many PASS verdicts list it", len(made), 1)
    check("Follow-up: only the Follow-up bullets go in the issue", bool(made) and "a gap main has" in str(made[0]) and "not this" not in str(made[0]), True)
    check("Follow-up: the issue number is kept in STATE", json.loads(real.state_path(8).read_text()).get("followup_issue"), 77)
    fv.write_text("PASS\nhead h0\nFindings:\nnone\nFollow-up: none\nReviewer audit: none\n- not this\n")
    shutil.rmtree(real.WORK / "prs" / "8", ignore_errors=True)
    n = len(fu); ff(8, fv)
    check("Follow-up: none files nothing", len(fu), n)
    real.sh = sh0

# the real ci_red / ci_findings (gh stubbed): which checks count as red, and what the fixer is handed
real.DRY = False
H = "a" * 40
def rollup(*rows, head=H):
    real.gh_json = lambda *a, **k: dict(headRefOid=head, statusCheckRollup=[dict(name=n, conclusion=c, detailsUrl=u) for n, c, u in rows])
rollup(("ci/tests", "SUCCESS", ""), ("ci/standards-check", "SUCCESS", ""))
check("ci_red: all green is not red", real.ci_red(5, H), [])
rollup(("ci/tests", "", ""), ("ci/standards-check", "SUCCESS", ""))
check("ci_red: pending is not red", real.ci_red(5, H), [])
rollup(("ci/tests", "FAILURE", ""))
check("ci_red: a required check failing is red", [c["name"] for c in real.ci_red(5, H)], ["ci/tests"])
rollup(("ci/tests (support, python)", "FAILURE", ""), ("ci/tests", "SUCCESS", ""))
check("ci_red: a failing shard alone does not count while the required check is not red", real.ci_red(5, H), [])
rollup(("ci/tests", "TIMED_OUT", ""))
check("ci_red: timed out is red", len(real.ci_red(5, H)), 1)
rollup(("ci/tests", "FAILURE", ""), head="b" * 40)
check("ci_red: a moved head is not judged on the old head's checks", real.ci_red(5, H), [])
rollup(("some/other-check", "FAILURE", ""))
check("ci_red: a non-required check failing is red (the merge step refuses it too, #1047)", [c["name"] for c in real.ci_red(5, H)], ["some/other-check"])
shutil.rmtree(real.WORK / "prs" / "5", ignore_errors=True)
real.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stderr="", stdout=
    "job\tstep\t2026-11-13T01:00:00.0Z FAILED packages/x/tests/test_a.py::test_one - KeyError: 'failed'\n"
    "job\tstep\t2026-11-13T01:00:01.0Z some unrelated line\n"
    "job\tstep\t2026-11-13T01:00:02.0Z 5 failed, 981 passed in 70s\n")
vp = real.ci_findings(5, 1, H, [dict(name="ci/tests", detailsUrl="https://github.com/o/r/actions/runs/11/job/22")])
txt = vp.read_text()
check("ci_findings: first line is FAIL and names the head", txt.splitlines()[:2], ["FAIL", f"head {H}"])
check("ci_findings: the failing test lines reach the fixer", "test_one" in txt and "5 failed" in txt and "unrelated" not in txt, True)
check("ci_findings: tells the fixer to say BLOCKED on a flake", "BLOCKED" in txt, True)
check("ci_findings: the file is parsed as FAIL by the chain", real.parse_verdict(vp), "FAIL")
nsf = tmp / "notshown.md"; nsf.write_text("NOT-SHOWN\nhead h0\nFindings:\nnone\nCannot demonstrate: needs a second peer\n")
check("parse_verdict: NOT-SHOWN is no verdict (11-16: the reviewer must shoot it or FAIL)", real.parse_verdict(nsf), None)
junk = tmp / "junk.md"; junk.write_text("maybe\n")
check("parse_verdict: junk is still no verdict", real.parse_verdict(junk), None)
check("ci_findings: the same-findings guard sees a key", bool(real.finding_key(txt) is not None), True)
vp = real.ci_findings(5, 2, H, [dict(name="ci/tests")])
check("ci_findings: a check with no log link still writes a file", real.parse_verdict(vp), "FAIL")
# a check run with no Actions job (CodeQL's results check, the dod check): its summary and the open alerts are the evidence (#1065)
def gh_api(summary, alerts):
    return lambda *c, **k: types.SimpleNamespace(returncode=0, stderr="", stdout=(
        alerts if any("code-scanning" in str(x) for x in c) else summary if any("check-runs" in str(x) for x in c) else ""))
real.sh = gh_api("1 configured alert\nCodeQL found 3 alerts\n",
                 "py/clear-text-logging-sensitive-data pkg/cli.py:219 - This expression logs sensitive data (secret) as clear text.\n")
txt = real.ci_findings(5, 3, H, [dict(name="CodeQL", detailsUrl="https://github.com/o/r/runs/987")]).read_text()
check("ci_findings: CodeQL's open alerts reach the fixer with file and line", "py/clear-text-logging-sensitive-data pkg/cli.py:219" in txt, True)
check("ci_findings: the check run's own summary reaches the fixer", "CodeQL found 3 alerts" in txt, True)
check("ci_findings: a CodeQL false positive is disputed, not called flaky", "DISPUTE" in txt and "flaky" not in txt, True)
DOD = "dod/ready-for-review"
real.sh = gh_api("Definition of done: 1 item(s) missing\npr-gates DoD: 1 failed item(s):\n- threads_resolved: 3 unresolved review thread(s)\n", "")
rollup((DOD, "FAILURE", "https://github.com/o/r/runs/988"))
check("ci_red: a dod check red only on land-step items (threads, base) is not the fixer's", real.ci_red(5, H), [])
rollup((DOD, "FAILURE", "https://github.com/o/r/runs/988"), ("CodeQL", "FAILURE", "https://github.com/o/r/runs/987"))
check("ci_red: CodeQL stays red beside a land-step-only dod", [c["name"] for c in real.ci_red(5, H)], ["CodeQL"])
real.sh = gh_api("pr-gates DoD: 2 failed item(s):\n- threads_resolved: 1 unresolved review thread(s)\n- spec_linked: no docs/specs file\n", "")
rollup((DOD, "FAILURE", "https://github.com/o/r/runs/988"))
check("ci_red: a dod check with any other failed item stays red", [c["name"] for c in real.ci_red(5, H)], [DOD])
real.sh = gh_api("", "")
rollup((DOD, "FAILURE", "https://github.com/o/r/runs/988"))
check("ci_red: a dod check whose summary cannot be read stays red", [c["name"] for c in real.ci_red(5, H)], [DOD])

# real ci_rerun: one rerun of the failed jobs per head, never a second
reran = []
real.DRY = False
real.sh = lambda *c, **k: (reran.append(c), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
real.wait_ci = lambda pr, head, minutes=45: "GREEN"
real.time = types.SimpleNamespace(**{**{k: getattr(time, k) for k in dir(time) if not k.startswith("_")}, "sleep": lambda s: None})
shutil.rmtree(real.WORK / "prs" / "6", ignore_errors=True)
bad = [dict(name="ci/tests", detailsUrl="https://github.com/o/r/actions/runs/11/job/22"), dict(name="ci/tests (x)", detailsUrl="https://github.com/o/r/actions/runs/11/job/23")]
check("ci_rerun: first call reruns the failed jobs and returns True", real.ci_rerun(6, H, bad), True)
check("ci_rerun: gh run rerun 11 --failed was issued once", sum(1 for c in reran if c[:3] == ("gh", "run", "rerun") and "11" in c and "--failed" in c), 1)
check("ci_rerun: second call on the same head is refused", real.ci_rerun(6, H, bad), False)
check("ci_rerun: nothing to rerun without a run link", real.ci_rerun(6, "c" * 40, [dict(name="ci/tests")]), False)
shutil.rmtree(real.WORK / "prs" / "6", ignore_errors=True)

# a resolved PR drops the human-approved label (replay #970: merged, label left behind)
lab = []
real.DRY = False
real.sh = lambda *c, **k: (lab.append(c), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
real.release = lambda pr: None
real.ledger = lambda **k: None
real.set_state = lambda pr, **k: None
real.log = lambda m: None
for st in ("MERGED", "CLOSED"):
    lab.clear(); real.die(9, st, "x")
    check(f"die {st}: the human-approved label is removed", [c for c in lab if "--remove-label" in c], [("gh", "pr", "edit", "9", "-R", real.REPO, "--remove-label", real.HUMAN_LABEL)])
lab.clear(); real.die(9, "AWAIT-HUMAN", "x")
check("die AWAIT-HUMAN: the label is left alone", [c for c in lab if "--remove-label" in c], [])

# a new human gate drops an older approval label (replay #1184, 11-26: label from 11-16 stayed on the PR, the gate
# comment of 11-25 only counts a label added after it, so the PR sat in AWAIT-HUMAN looking approved)
gate_dir = tmp / "gate9" / "r1-ui"; (gate_dir / "shots").mkdir(parents=True); (gate_dir / "shots" / "a.png").write_bytes(b"png")
(gate_dir / "ui-verdict.md").write_text("PASS\nSummary: fine")
real.push_shots = lambda pr, head, shots: ("c0", "shots/9")
def gate_sh(*c, **k):
    lab.append(c)
    out = "https://github.com/o/r/pull/9#issuecomment-123" if c[:3] == ("gh", "pr", "comment") else \
          "2026-11-26T00:00:00Z" if c[:2] == ("gh", "api") else real.HUMAN_LABEL if c[:3] == ("gh", "label", "list") else ""
    return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
real.sh = gate_sh
for had in (True, False):
    lab.clear()
    real.gh_json = lambda *a, **k: {"labels": [{"name": real.HUMAN_LABEL}] if had else []}
    real.human_gate(9, "h" * 40, gate_dir / "ui-verdict.md", None)
    order = [("remove" if "--remove-label" in c else "comment") for c in lab if "--remove-label" in c or c[:3] == ("gh", "pr", "comment")]
    body = next((c[c.index("--body") + 1] for c in lab if c[:3] == ("gh", "pr", "comment")), "")
    if had:
        check("gate: an older human-approved label is removed before the gate comment", order, ["remove", "comment"])
        check("gate: the comment says the old label was removed", "was removed" in body, True)
    else:
        check("gate: no label, nothing removed", order, ["comment"])
        check("gate: no label, no removal note", "was removed" in body, False)
real.sh = lambda *c, **k: (lab.append(c), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]

def put(pr, **kw):
    sp = pc.state_path(pr); sp.parent.mkdir(parents=True, exist_ok=True); sp.write_text(json.dumps(kw)); return sp
infos = {}
pc.pr_info = lambda pr: infos[pr]
pc.run_chain = lambda pr, a, **kw: (calls.append((pr, sorted(kw), (kw.get("approved") or {}).get("gate_head"))), "MERGED")[1]
pc.human_signal = lambda pr, st: (calls.append((pr, "polled")), (None, None))[1]
pc.branch_head = lambda i: "h0"
upd = {}
pc.pr_updated = lambda pr: upd[pr]
a = types.SimpleNamespace(land=True)
mk = lambda author="owner", st="OPEN": dict(state=st, author={"login": author})
sp = put(970, status="LAND-STOPPED", head="h0", why=DOD, land_tries={"h0": 1}); infos[970] = mk(); upd[970] = sp.stat().st_mtime - 60
check("resume: LAND-STOPPED with nothing changed on the PR is left alone", (pc.resume_all(a), calls), ([], []))
upd[970] = time.time() + 60
check("resume: LAND-STOPPED lands again after the PR changed (gate comment or body fixed)", pc.resume_all(a), [(970, "MERGED")])
check("resume: the landing retry skips review and uses the stopped head", calls, [(970, ["approved"], "h0")])
calls.clear(); put(970, status="LAND-STOPPED", head="h0", why=DOD, land_tries={"h0": pc.LAND_TRIES})
check(f"resume: LAND-STOPPED is left alone after {pc.LAND_TRIES} refused merges", (pc.resume_all(a), calls), ([], []))
put(970, status="LAND-STOPPED", head="h0", why=DOD); infos[970] = mk(st="MERGED")
check("resume: LAND-STOPPED on a PR merged elsewhere becomes CLOSED", pc.resume_all(a), [(970, "CLOSED")])
put(1015, status="AWAIT-HUMAN", head="h0", gate_head="h0", gate_at="t"); infos[1015] = mk(author="stranger")
check("resume: an outside PR waiting at the human gate becomes NOT-OURS (replay #1015)", pc.resume_all(a), [(1015, "NOT-OURS")])
check("resume: the outside PR is not polled again", (pc.resume_all(a), calls), ([], []))
put(1009, status="AWAIT-HUMAN", head="h0", gate_head="h0", gate_at="t"); infos[1009] = mk()
check("resume: our PR at the human gate is still polled", (pc.resume_all(a), calls), ([], [(1009, "polled")]))
check("resume with nothing moved names the PRs still at the human gate (it printed nobody is waiting while #766 was, 11-13)",
      getattr(pc, "resume_note", lambda: None)(), "no PR moved; still waiting on a person: #1009")
pc.die(970, "LAND-STOPPED", DOD)
check("die: refused merges are counted per head", json.loads(pc.state_path(970).read_text())["land_tries"], {"h0": 1})



# review-fix: a reviewer that changed the code may not PASS it; its worktree stays for fix() to check and push
drops = []
real.pr_info = lambda pr: dict(title="t", body="", author={"login": "owner"}, files=[], headRefName="pr", closingIssuesReferences=[])
real.branch_head, real.contains_main, real.fetch = (lambda i: "h0"), (lambda h, m: True), (lambda i: "m")
real.drop_wt, real.sh = (lambda wt: drops.append(wt)), (lambda *c, **k: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
def agent_pass(role, model, brief, cwd, outdir, pr):
    (outdir / "verdict.md").write_text("PASS\nhead h0\nFindings:\nnone\n"); return {}
real.run_agent = agent_pass
cwds = []
def agent_cwd(role, model, brief, cwd, outdir, pr):
    cwds.append(pathlib.Path(cwd)); (outdir / "verdict.md").write_text("PASS\nhead h0\nFindings:\nnone\n"); return {}
real.run_agent, real.make_wt = agent_cwd, (lambda wt, head: wt.mkdir(parents=True, exist_ok=True))
shutil.rmtree(real.WORK / "prs" / "9", ignore_errors=True)
real.self_changed = lambda wt, head: False
real.review(9, 1, "review", "m", False, fix_too=True)
check("review: the agent starts inside the pinned worktree, not the brief folder", bool(cwds) and cwds[0] == real.wt_for(real.WORK / "prs" / "9" / "r1-review"), True)
real.run_agent = agent_pass
for changed, want, kept in ((True, "FAIL", True), (False, "PASS", False)):
    shutil.rmtree(real.WORK / "prs" / "9", ignore_errors=True); drops.clear()
    real.self_changed = lambda wt, head, c=changed: c
    v, vp, _ = real.review(9, 1, "review", "m", False, fix_too=True)
    check(f"review-fix: PASS {'with' if changed else 'without'} its own changes counts as {want}", v, want)
    check(f"review-fix: worktree {'kept for the push' if kept else 'dropped'}", not drops, kept)

# doctor: stale state set to GitHub's, a stray approval label removed, the loop being down paged once, needs-you rows listed
dk = getattr(real, "doctor", None)
check("doctor exists", dk is not None, True)
if dk:
    real.set_state = pc.set_state.__class__(pc.set_state.__code__, real.__dict__)  # an earlier block stubbed it out
    shutil.rmtree(real.WORK / "prs", ignore_errors=True)
    for n, st in ((11, dict(status="READY", head="a")), (12, dict(status="RESTING", head="b", rest_until=time.time() + 60)),
                  (13, dict(status="POLICY-HOLD", head="c", why="hold in the PR body")), (14, dict(status="AWAIT-HUMAN", head="d"))):
        real.state_path(n).write_text(json.dumps(st))
    real.state_path(15).write_text(json.dumps(dict(status="ERROR", head="e")))
    real.state_path(16).write_text(json.dumps(dict(status="RESTING", head="f")))
    me, them = {"login": "owner"}, {"login": "stranger"}
    real.gh_json = lambda *a, **k: [dict(number=12, labels=[{"name": "human-approved"}], author=me), dict(number=13, labels=[], author=me),
                                    dict(number=14, labels=[{"name": "human-approved"}], author=me),
                                    dict(number=15, labels=[{"name": "human-approved"}], author=me),
                                    dict(number=16, labels=[{"name": "human-approved"}], author=them),
                                    dict(number=17, labels=[{"name": "human-approved"}], author=me), dict(number=18, labels=[], author=me),
                                    dict(number=31, labels=[{"name": "human-approved"}], author=me),
                                    dict(number=32, labels=[{"name": "human-approved"}], author=me),
                                    dict(number=33, labels=[], author=me)]
    # two dead runs: 17 died while landing an approved PR (replay #1057 20:14, a tripwire stop), 18 died mid-review
    real.state_path(17).write_text(json.dumps(dict(status="running", head="g", started="2026-11-14T20:11:20", gate_head="g0")))
    real.state_path(18).write_text(json.dumps(dict(status="running", head="h", started="2026-11-14T20:00:00")))
    for n in (31, 32):  # both RESTING after a gate; only 31 still has the person's approval (11-16: #1101 rested on a conflict right after it)
        real.state_path(n).write_text(json.dumps(dict(status="RESTING", head="r", gate_head="r", gate_at="2026-11-16T10:00:00Z")))
    real.human_signal = lambda pr, st: ("approved", "t") if pr == 31 else (None, None)
    real.state_path(33).write_text(json.dumps(dict(status="RESTING", head="r", rest_until=time.time() + 60)))  # open, between runs, build output left from a run that ended
    (real.WORK / "prs" / "33" / "r1-ui" / "stack" / "dist").mkdir(parents=True)
    (real.WORK / "prs" / "33" / "r1-ui" / "stack" / "dist" / "bundle.js").write_text("x")
    # a merged PR (19) and one the list wrongly shows as gone while GitHub still says OPEN (20), each with UI stack build output left behind
    for n in (19, 20):
        real.state_path(n).write_text(json.dumps(dict(status="MERGED", head="m")))
        (real.WORK / "prs" / str(n) / "r1-ui" / "stack" / "dist").mkdir(parents=True)
        (real.WORK / "prs" / str(n) / "r1-ui" / "stack" / "dist" / "bundle.js").write_text("x")
    real.pr_state = lambda n: {19: "MERGED", 20: "OPEN"}.get(n)
    with open(real.LEDGER, "a") as fh:
        for row in (dict(pr=17, stage="end", status="AWAIT-HUMAN", ts="2026-11-14T19:49:20"), dict(pr=17, stage="end", status="WAITING-CI", ts="2026-11-14T19:56:52"),
                    dict(pr=18, stage="end", status="RESTING", ts="2026-11-14T19:00:00"), dict(pr=17, stage="end", status="RESTING", ts="2026-11-14T21:00:00")):
            fh.write(json.dumps(row) + "\n")
    dcalls = []
    real.sh = lambda *c, **k: (dcalls.append(c), types.SimpleNamespace(returncode=1 if c[:1] == ("pgrep",) else 0, stdout="", stderr=""))[1]
    rows = dk(); dk()
    kinds = {(r.get("pr"), r["kind"]) for r in rows}
    check("doctor: READY on a PR GitHub no longer lists becomes CLOSED", json.loads(real.state_path(11).read_text())["status"], "CLOSED")
    check("doctor: the label comes off a RESTING PR", any(c[:3] == ("gh", "pr", "edit") and "12" in c and "human-approved" in c for c in dcalls), True)
    check("doctor: the label stays on a PR at the human gate", any(c[:3] == ("gh", "pr", "edit") and "14" in c for c in dcalls), False)
    check("doctor: the label stays on a retryable ERROR PR (replay #790)", any(c[:3] == ("gh", "pr", "edit") and "15" in c for c in dcalls), False)
    check("doctor: an outside PR's label is never touched (replay #1015)", any(c[:3] == ("gh", "pr", "edit") and "16" in c for c in dcalls), False)
    check("doctor: POLICY-HOLD and AWAIT-HUMAN are listed as needing the maintainer", {(13, "needs-you"), (14, "needs-you")} <= kinds, True)
    check("doctor: loop down pages the operator once, not every lap", sum(1 for c in dcalls if "notify-stub" in str(c[:1])), 1)
    check("doctor: writes doctor.json for the board", (real.WORK / "doctor.json").exists(), True)
    check("doctor: a RESTING PR whose approval still stands keeps its label (replay #1101, 11-16)", any(c[:3] == ("gh", "pr", "edit") and "31" in c and "human-approved" in c for c in dcalls), False)
    check("doctor: a RESTING PR whose approval no longer stands loses its label", any(c[:3] == ("gh", "pr", "edit") and "32" in c and "human-approved" in c for c in dcalls), True)
    check("doctor: a merged PR's UI stack build output is removed", (real.WORK / "prs" / "19" / "r1-ui" / "stack" / "dist").exists(), False)
    check("doctor: an open PR between runs loses the build output an ended run left", (real.WORK / "prs" / "33" / "r1-ui" / "stack" / "dist").exists(), False)
    check("doctor: nothing is removed while GitHub says the PR is still open", (real.WORK / "prs" / "20" / "r1-ui" / "stack" / "dist").exists(), True)
    check("doctor: a landing that died goes back to WAITING-CI, its approval kept (replay #1057 20:14)",
          json.loads(real.state_path(17).read_text())["status"], "WAITING-CI")
    check("doctor: a review that died is set STOPPED, free for the next run", json.loads(real.state_path(18).read_text())["status"], "STOPPED")

# resume: a PR waiting for CI whose head moved only by merging main lands (an addendum covers it); real changes start over
g9 = importlib.machinery.SourceFileLoader("pc9", str(src)).load_module()
g9.WORK, g9.STOPFILE = tmp / "w9", tmp / "w9" / "STOP"
(g9.WORK / "prs" / "19").mkdir(parents=True)
g9.state_path(19).write_text(json.dumps(dict(status="WAITING-CI", head="h0", gate_head="h0")))
g9.log = lambda m: None
g9.pr_info = lambda pr: dict(state="OPEN", author={"login": "owner"}, headRefOid="h1")
g9.branch_head, g9.fetch = (lambda info: "h1"), (lambda info: "m")
runs = []
g9.run_chain = lambda pr, a, **k: (runs.append(k), "MERGED")[1]
for main_only, want in ((True, "approved"), (False, "from the top")):
    runs.clear(); g9.main_only_move = lambda old, new, _m=main_only: _m
    g9.resume_all(types.SimpleNamespace())
    got = "approved" if runs and runs[0].get("approved") else "from the top" if runs else "nothing"
    check(f"resume: WAITING-CI, head moved {'only by merging main' if main_only else 'by real changes'} -> {want}", got, want)
# a PR parked before its first review has no gate to land on: resume leaves it while CI runs; once CI settles it marks it
# ci_settled and the loop's threads pick it (11-17: resume ran 51 settled PRs inline one at a time while 3 threads sat idle)
for pending in (True, False):
    g9.state_path(19).write_text(json.dumps(dict(status="WAITING-CI", head="h1", ci_wait="review")))
    runs.clear(); g9.ci_pending = lambda pr, h, _p=pending: _p
    g9.resume_all(types.SimpleNamespace())
    check(f"resume: WAITING-CI before the review, CI {'pending' if pending else 'settled'} -> no inline run", runs, [])
    check(f"resume: CI {'pending' if pending else 'settled'} -> ci_settled {not pending}",
          bool(json.loads(g9.state_path(19).read_text()).get("ci_settled")), not pending)
    g9.ps_text = ""
    p19 = dict(number=19, headRefOid="h1", author={"login": "owner"})
    check(f"pick: WAITING-CI with CI {'pending' if pending else 'settled'} is {'held' if pending else 'free'}", g9.owner_reason(p19, "") is None, not pending)
g9.resume_all(types.SimpleNamespace())
check("resume: a settled PR stays the threads' job (no second look, no inline run)", runs, [])
g9.state_path(19).write_text(json.dumps(dict(status="WAITING-CI", head="h1", ci_wait="review", ci_wait_at={"h1": time.time() - 2 * 3600})))
runs.clear(); g9.ci_pending = lambda pr, h: True; g9.ci_runs_open = lambda h: False
g9.resume_all(types.SimpleNamespace())
check("resume: required checks never finished before the review -> a fixer gets it", bool(runs and runs[0].get("seed")), True)
# a run GitHub has queued (or is still running) is CI on its way, not a PR fault (replay #1244 11-17: CI queued 40+ min)
g9.state_path(19).write_text(json.dumps(dict(status="WAITING-CI", head="h1", ci_wait="review", ci_wait_at={"h1": time.time() - 2 * 3600})))
runs.clear(); g9.ci_runs_open = lambda h: True
g9.resume_all(types.SimpleNamespace())
check("resume: CI queued on GitHub past the limit -> keep waiting, no fixer", runs, [])

# resume: landings go before a person's comments, which start a whole fixer chain (replay 11-14 21:02: #1057 behind #1050)
g10 = importlib.machinery.SourceFileLoader("pc10", str(src)).load_module()
g10.WORK, g10.STOPFILE = tmp / "w10", tmp / "w10" / "STOP"
for n, st in ((20, dict(status="AWAIT-HUMAN", head="h1", gate_head="h1", gate_at="t")), (21, dict(status="WAITING-CI", head="h1", gate_head="h1"))):
    (g10.WORK / "prs" / str(n)).mkdir(parents=True)
    g10.state_path(n).write_text(json.dumps(st))
g10.log = lambda m: None
g10.pr_info = lambda pr: dict(state="OPEN", author={"login": "owner"}, headRefOid="h1")
g10.branch_head, g10.fetch, g10.ci_pending = (lambda info: "h1"), (lambda info: "m"), (lambda pr, h: False)
g10.human_signal = lambda pr, st: ("feedback", [("t", "make the button smaller")])
order = []
g10.run_chain = lambda pr, a, **k: (order.append((pr, "seed" if k.get("seed") else "approved" if k.get("approved") else "top")), "X")[1]
g10.resume_all(types.SimpleNamespace())
check("resume: the approved landing runs before the comment's fixer chain", order, [(21, "approved"), (20, "seed")])

# red CI on a test main also fails: one rerun, no fixer (replay #1057 21:32, test_network_attach red on main 2 of 4 runs)
g11 = importlib.machinery.SourceFileLoader("pc11", str(src)).load_module()
g11.WORK = tmp / "w11"; (g11.WORK / "prs" / "22").mkdir(parents=True); g11.state_path(22).write_text("{}")
g11.LEDGER = tmp / "w11" / "ledger.jsonl"; g11.log = lambda m: None
LOGS = {"5": "x Z FAILED tests/unit/test_a.py::test_flaky - assert 1 == 4\n", "77": "Z FAILED tests/unit/test_a.py::test_flaky - x\n",
        "78": "Z FAILED tests/unit/test_b.py::test_other - x\n"}
g11.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stdout=LOGS.get(c[3], "") if c[:3] == ("gh", "run", "view") else "", stderr="")
g11.gh_json = lambda *a: [dict(databaseId=77, conclusion="failure"), dict(databaseId=76, conclusion="success"), dict(databaseId=78, conclusion="failure")]
reruns = []
g11.ci_rerun = lambda pr, head, bad, why="": (reruns.append(why), True)[1]
g11.ci_red = lambda pr, head: []
bad5 = [dict(name="ci/tests (beryl)", detailsUrl="https://github.com/o/r/actions/runs/5/job/9")]
check("main flake: a red test main also fails is rerun, no fixer", (g11.main_flake_rerun(22, "h", bad5), len(reruns)), (True, 1))
LOGS["5"] += "Z FAILED tests/unit/test_c.py::test_new - x\n"; reruns.clear()
check("main flake: a red test main does not fail goes to the fixer", (g11.main_flake_rerun(22, "h", bad5), len(reruns)), (False, 0))
check("main flake: a check with no job log goes to the fixer",
      g11.main_flake_rerun(22, "h", [dict(name="CodeQL", detailsUrl="https://github.com/o/r/runs/1")]), False)
g11.ci_red = lambda pr, head: bad5; LOGS["5"] = "Z FAILED tests/unit/test_a.py::test_flaky - x\n"
check("main flake: still red after the rerun goes to the fixer", g11.main_flake_rerun(22, "h", bad5), False)

# an approval stands for its head and main-only moves of it, on any path (replay #1057 21:32: approved, then CI-RED)
g12 = importlib.machinery.SourceFileLoader("pc12", str(src)).load_module()
g12.LEDGER = tmp / "w11" / "ledger.jsonl"; g12.log = lambda m: None; g12.fetch = lambda i: "m"
g12.ci_red = lambda pr, head: []
st12 = dict(gate_head="g0", gate_at="t")
for cur, main_only, sig, want in (("g0", False, "approved", True), ("g1", True, "approved", True), ("g1", False, "approved", False),
                                  ("g0", False, "feedback", False), ("g0", False, None, False)):
    g12.main_only_move = lambda old, new, _m=main_only: _m
    g12.human_signal = lambda pr, st, _s=sig: (_s, [])
    got = g12.standing_approval(23, {}, st12, cur) is not None
    check(f"approval stands: head {cur}, main-only {main_only}, signal {sig} -> {want}", got, want)
# red CI at an approved head (replay #1184 11-16: CI-RED 3 times at landing, then needs-person, never a fixer)
g12.main_only_move, g12.human_signal = (lambda old, new: False), (lambda pr, st: ("approved", []))
g12.ci_red = lambda pr, head: [{"name": "ci/tests"}]
for flake, want in ((True, True), (False, False)):
    g12.main_flake_rerun = lambda pr, head, bad, _f=flake: _f
    check(f"approval with red CI: {'a test main also fails is rerun, approval stands' if flake else 'this PR broke a test, the fixer gets it'}",
          g12.standing_approval(23, {}, st12, "g0") is not None, want)

# a comment that says approved is not a change request (replay #1057 20:41)
g13 = importlib.machinery.SourceFileLoader("pc13", str(src)).load_module()
for body, want in (("UI GATE (human-gate review by koordinator, pinned to head d236935b): APPROVED.\n\nReviewed evidence ...", True),
                   ("LGTM", True), ("\nlooks good to me", True), ("Not approved: the button overlaps the draft", False),
                   ("make the stop button smaller, it covers the text", False), ("unapproved", False)):
    check(f"approval note: {body.strip()[:40]!r} -> {want}", bool(g13.APPROVAL_NOTE.search(next((l for l in body.splitlines() if l.strip()), ""))), want)

# a GitHub hiccup must not end a chain (replay #598 00:39: "GraphQL: Something went wrong" right after a push)
g3 = importlib.machinery.SourceFileLoader("pc3", str(src)).load_module()
g3.GH_RETRY_WAIT = (0, 0)
seq = []
def flaky(*c, **k):
    seq.append(c)
    if len(seq) == 1:
        raise RuntimeError("gh pr view 598 -R: GraphQL: Something went wrong while executing your query")
    return types.SimpleNamespace(returncode=0, stdout='{"ok": 1}', stderr="")
g3.sh = flaky
try:
    got = g3.gh_json("pr", "view", "598")
except RuntimeError as e:
    got = str(e)
check("gh_json: a transient GitHub error is retried", got, {"ok": 1})
seq.clear()
def broken(*c, **k):
    seq.append(c); raise RuntimeError("gh pr view: no pull requests found")
g3.sh = broken
try:
    g3.gh_json("pr", "view", "1")
except RuntimeError:
    pass
check("gh_json: a real error is not retried", len(seq), 1)

# fold.py merges main in ONE shared worktree: two chains folding at once collided (replay #954 + #790, 00:43, 4 threads)
import fcntl
g4 = importlib.machinery.SourceFileLoader("pc4", str(src)).load_module()
held = []
def fold_sh(*c, **k):
    if any("fold.py" in str(x) for x in c):
        lk = getattr(g4, "FOLD_LOCK", None) or tmp / "no-lock-in-script"
        try:
            fh = open(lk, "a"); fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB); held.append(False); fh.close()
        except BlockingIOError:
            held.append(True)
    return types.SimpleNamespace(returncode=0, stdout="", stderr="")
g4.sh, g4.fetch, g4.branch_head, g4.contains_main, g4.pr_info = fold_sh, (lambda i: "m"), (lambda i: "h"), (lambda h, m: False), (lambda pr: {})
g4.kit = types.SimpleNamespace(TRAIN=tmp)
g4.fold(5, dict(isCrossRepository=False, headRefOid="h"))
check("fold: fold.py runs while holding the shared fold lock", held, [True])

# gate_findings: failures and warnings from main's own gate script become findings; tool errors and land-time checks do not
g5 = importlib.machinery.SourceFileLoader("pc5", str(src)).load_module()
gf = getattr(g5, "gate_findings", None)
check("gate_findings exists", gf is not None, True)
if gf:
    outs = []
    def gsh(*c, **k):
        if c[:2] == ("git", "-C"):
            return types.SimpleNamespace(returncode=0, stdout="x = 1\n", stderr="")
        return outs.pop(0)
    g5.sh = gsh
    def run(rc, out):
        outs.append(types.SimpleNamespace(returncode=rc, stdout=out, stderr="")); return gf(5)
    check("gates: all passed, no warning -> nothing", run(0, "pr-gates DoD: all enabled checks passed (x).\n"), [])
    got = run(0, "pr-gates DoD: all enabled checks passed (x).\nWarnings: 1 item(s) need attention:\n- WARNING spec_or_fix_brief: feature PR needs a spec\n")
    check("gates: a warning is a finding", got, ["WARNING spec_or_fix_brief: feature PR needs a spec"])
    got = run(1, "pr-gates DoD: 2 failed item(s):\n- linked_issue: no Fixes #N\n- threads_resolved: 2 unresolved review thread(s)\n- base_current: PR is behind\n")
    check("gates: failures are findings; threads and base are left to the land step", got, ["linked_issue: no Fixes #N"])
    got = run(1, "pr-gates DoD: 1 failed item(s):\n- regression_test: no test covers F3\nAudited finding-reference suppressions:\n- F11 occurrence 1 in x\n")
    check("gates: audit notes are not findings", got, ["regression_test: no test covers F3"])
    check("gates: a tool error never blocks", run(2, "pr-gates DoD: error: boom\n"), [])

# a refused merge must not stay ready: the merge bot merges ready+approved+green PRs (replay #549: merged by itself at 01:14)
g6 = importlib.machinery.SourceFileLoader("pc6", str(src)).load_module()
g6.DRY, dc = False, []
g6.sh = lambda *c, **k: (dc.append(c), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
g6.release = lambda pr: None
g6.die(77, "LAND-STOPPED", "dod gate red")
check("LAND-STOPPED puts the PR back to draft", any(c[:3] == ("gh", "pr", "ready") and "--undo" in c for c in dc), True)
dc.clear(); g6.die(77, "RESTING", "x")
check("other end states leave draft status alone", any(c[:3] == ("gh", "pr", "ready") for c in dc), False)

# the config tripwire stops only on damage (15 stops on 11-14 were live kollab sessions saving their own settings, none an agent's)
g7 = importlib.machinery.SourceFileLoader("pc7", str(src)).load_module()
cf = tmp / "cfg.json"; g7.TRIPWIRE_CFG = cf
base = {"kollabor": {"updates": {"last_check_timestamp": 1, "github_repo": "o/r", "check_enabled": True},
                     "permissions": {"approval_mode": "trust_all"},
                     "llm": {"profiles": {"a": {"model": "m", "api_key": "k1", "base_url": "u", "max_tokens": 5}}}},
        "config_version": "1"}
def trip(edit):
    cf.write_text(json.dumps(base)); before = g7.cfg_snapshot()
    d = json.loads(json.dumps(base)); edit(d); cf.write_text(json.dumps(d)) if d is not None else cf.unlink()
    return g7.cfg_damage(before, g7.cfg_snapshot())
def setp(*path_val):
    *path, val = path_val
    def f(d):
        n = d
        for k in path[:-1]:
            n = n[k]
        n[path[-1]] = val
    return f
check("tripwire: the update-check stamp is recorded, not a stop", trip(setp("kollabor", "updates", "last_check_timestamp", 2)),
      ([], ["kollabor.updates.last_check_timestamp"]))
check("tripwire: a session saving its approval mode is recorded, not a stop (replay 20:14)",
      trip(setp("kollabor", "permissions", "approval_mode", "default"))[0], [])
check("tripwire: a changed model, version stamp or token budget is not a stop",
      trip(lambda d: (setp("kollabor", "llm", "profiles", "a", "model", "n")(d), setp("config_version", "2")(d),
                      setp("kollabor", "llm", "profiles", "a", "max_tokens", 9)(d)))[0], [])
check("tripwire: a profile a person adds (with its own key) is not a stop",
      trip(setp("kollabor", "llm", "profiles", "b", {"model": "x", "api_key": "k2"})),
      ([], ["kollabor.llm.profiles.b.model", "kollabor.llm.profiles.b.api_key"]))
check("tripwire: a changed api key stops", trip(setp("kollabor", "llm", "profiles", "a", "api_key", "k9"))[0], ["kollabor.llm.profiles.a.api_key"])
check("tripwire: a changed endpoint stops", trip(setp("kollabor", "llm", "profiles", "a", "base_url", "evil"))[0], ["kollabor.llm.profiles.a.base_url"])
check("tripwire: a changed update repo stops", trip(setp("kollabor", "updates", "github_repo", "x/y"))[0], ["kollabor.updates.github_repo"])
check("tripwire: a removed setting stops", trip(lambda d: d["kollabor"]["llm"]["profiles"].pop("a"))[0][:1], ["kollabor.llm.profiles.a.model"])
check("tripwire: a wiped file stops (replay 11-08)", len(trip(lambda d: d.clear())[0]) > 0, True)
cf.unlink(); check("tripwire: a deleted file stops", g7.cfg_damage({"a": "1"}, g7.cfg_snapshot())[0], ["the file is absent"])
cf.write_text("{not json"); check("tripwire: an unreadable file stops", g7.cfg_damage({"a": "1"}, g7.cfg_snapshot())[0], ["the file is unreadable"])
cf.write_text(json.dumps(base)); check("tripwire: snapshots hold digests, never values", "k1" in json.dumps(g7.cfg_snapshot()), False)

# the 45-minute fresh-push rule must not block the chain's own fixer pushes (replay 01:46: 813/954/1012 blocked after the tripwire)
g8 = importlib.machinery.SourceFileLoader("pc8", str(src)).load_module()
g8.LEDGER = tmp / "ledger8.jsonl"; g8.LEDGER.write_text(json.dumps({"pr": 1, "stage": "push", "head": "abc123"}) + "\n")
check("chain_pushed: a head the chain pushed", g8.chain_pushed("abc123"), True)
check("chain_pushed: a head someone else pushed", g8.chain_pushed("zzz999"), False)

# a chain that died leaves STATE at running: the board then says "in the chain now" (replay 01:48 after the tripwire)
g9 = importlib.machinery.SourceFileLoader("pc9", str(src)).load_module()
shutil.rmtree(g9.WORK / "prs", ignore_errors=True); shutil.rmtree(g9.CLAIMS, ignore_errors=True)
g9.state_path(21).write_text(json.dumps(dict(status="running", head="a")))
g9.state_path(22).write_text(json.dumps(dict(status="running", head="b")))
(g9.CLAIMS / "22").mkdir(parents=True, exist_ok=True); (g9.CLAIMS / "22" / "pid").write_text(str(os.getpid()))
g9.gh_json = lambda *a, **k: [dict(number=21, labels=[], author={"login": "owner"}), dict(number=22, labels=[], author={"login": "owner"})]
g9.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
g9.doctor()
check("doctor: running with no live chain becomes STOPPED", json.loads(g9.state_path(21).read_text())["status"], "STOPPED")
check("doctor: running with a live chain stays running", json.loads(g9.state_path(22).read_text())["status"], "running")

# public text: no local paths, no model names (audit 11-14: 37 of 110 comments had paths, 7 named the model)
g11 = importlib.machinery.SourceFileLoader("pc11", str(src)).load_module()
out = g11.scrub("Haiku audit: ran `HOME=/Users/someone/backups/pr-chain/scratch-home KOLLAB_NO_KEYRING=1 /Users/someone/dev/kollab/.venv/bin/python -m pytest` in ~/backups/pr-chain/prs/5/r1-review/wt; the review found it (codex:gpt-6-luna)\nOpus audit: none")
check("scrub: no home paths", "/Users/" in out or "~/" in out, False)
check("scrub: no model names", any(m in out.lower() for m in ("haiku", "opus", "luna", "codex", "sonnet")), False)
check("scrub: the finding text survives", "pytest" in out and "found it" in out, True)
# a verdict already posted for this kind at this head is not posted again (audit: #790 got 4 PASS comments in 17 min)
g11.DRY = False; posts = []
g11.sh = lambda *c, **k: (posts.append(c), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
shutil.rmtree(g11.WORK / "prs" / "12", ignore_errors=True)
(tmp / "v.md").write_text("PASS\nhead h1\n")
g11.post_verdict(12, "review", "h1", "m1", tmp / "v.md") if hasattr(g11, "post_verdict") else None
g11.post_verdict(12, "review", "h1", "m1", tmp / "v.md") if hasattr(g11, "post_verdict") else None
check("post_verdict: one comment per kind and head", len([c for c in posts if c[:3] == ("gh", "pr", "comment")]), 1)

# a second run on a PR whose claim is live must leave that chain alone (auditor #6, seen live 08:21: resume vs #1047)
g12 = importlib.machinery.SourceFileLoader("pc12", str(src)).load_module()
g12.DRY = False
shutil.rmtree(g12.WORK / "prs" / "41", ignore_errors=True); shutil.rmtree(g12.CLAIMS / "41", ignore_errors=True)
g12.state_path(41).write_text(json.dumps(dict(status="running", head="h", why="")))
(g12.CLAIMS / "41").mkdir(parents=True, exist_ok=True); (g12.CLAIMS / "41" / "pid").write_text(str(os.getpid()))
g12.claim = lambda pr: False
g12.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="")
g12.run_chain(41, types.SimpleNamespace())
check("CLAIMED: the live chain's claim is kept", (g12.CLAIMS / "41" / "pid").exists(), True)
check("CLAIMED: the live chain's state is not overwritten", json.loads(g12.state_path(41).read_text())["status"], "running")

# any failing check blocks the merge step (land_pr wants all green), so it must reach the fixer (replay #1047: import-smoke)
rollup(("ci/tests", "SUCCESS", ""), ("import-smoke", "FAILURE", ""))
check("ci_red: a failing non-required check outside ci/tests is red", [c["name"] for c in real.ci_red(5, H)], ["import-smoke"])
rollup(("ci/tests (amber)", "FAILURE", ""), ("ci/tests", "SUCCESS", ""))
check("ci_red: a ci/tests shard alone still does not count", real.ci_red(5, H), [])

# a finished UI stage removes its own stack build and scratch HOME (disk filled 11-14 12:29), and keeps its logs
g13 = importlib.machinery.SourceFileLoader("pc13", str(src)).load_module()
stk = tmp / "stk"; [(stk / n).mkdir(parents=True, exist_ok=True) for n in ("dist", "home", "project", "shim")]
(stk / "engine.log").write_text("x"); (stk / "stack.json").write_text("{}")
(stk / "home" / ".kollab" / "logs").mkdir(parents=True, exist_ok=True); (stk / "home" / ".kollab" / "logs" / "kollab.log").write_text("daemon said why")
g13.clean_stack(stk)
check("clean_stack: build and scratch dirs gone, logs kept", sorted(x.name for x in stk.iterdir()), ["engine.log", "home-logs", "stack.json"])
check("clean_stack: the daemon log from the scratch HOME survives", (stk / "home-logs" / ".kollab" / "logs" / "kollab.log").read_text(), "daemon said why")

# a process an agent left running inside its worktree is stopped before the worktree goes (leaked kollab daemons, 11-14)
import subprocess as _sp
g14 = importlib.machinery.SourceFileLoader("pc14", str(src)).load_module()
wtd = tmp / "leakwt"; wtd.mkdir(exist_ok=True)
child = _sp.Popen(["/bin/sleep", "60"], cwd=str(wtd))
time.sleep(0.5)
stopper = getattr(g14, "stop_procs_in", None)
check("stop_procs_in exists", stopper is not None, True)
if stopper:
    stopper(wtd)
    time.sleep(1.5)
check("a process left in a worktree is stopped", child.poll() is not None, True)
if child.poll() is None:
    child.kill()

# a removed worktree's code index goes with it (codebase-memory-mcp kept 338 dead indexes, 67 GB, and filled the disk 11-14)
g15 = importlib.machinery.SourceFileLoader("pc15", str(src)).load_module()
idx = tmp / "cmm"; idx.mkdir(exist_ok=True); g15.CODE_INDEX = idx
wt15 = tmp / "some" / "wt"; wt15.mkdir(parents=True, exist_ok=True)
name = str(wt15.resolve()).lstrip("/").replace("/", "-")
for suf in (".db", ".db-wal", ".db-shm"): (idx / (name + suf)).write_text("x")
(idx / "Users-other-project.db").write_text("keep")
g15.drop_index(wt15) if hasattr(g15, "drop_index") else None
check("drop_index removes the worktree's index files", sorted(x.name for x in idx.iterdir()), ["Users-other-project.db"])

# one at a time: the focus PR keeps its slot through its own CI waits; every other free PR waits for it
g16 = importlib.machinery.SourceFileLoader("pc16", str(src)).load_module()
g16.WORK = tmp / "w16"; g16.WORK.mkdir(parents=True); g16.FOCUS = g16.WORK / "focus.json"; g16.DRY = False
g16.LIM["prs_at_once"] = 1
def rows16(**owners):
    return [dict(number=n, owner=owners.get(f"p{n}")) for n in (1, 2, 3)]
r = rows16()
check("focus: the best free PR is the focus, the rest wait", (g16.focus(r), [x["owner"] is None for x in r]), ([1], [True, False, False]))
r = rows16(p1="waiting for CI before its review; resume starts the chain when the checks finish")
check("focus: while it waits on its own CI nothing else is free", (g16.focus(r), [x["owner"] is None for x in r]), ([1], [False, False, False]))
r = rows16(p1="claimed by live chain pid 5")
check("focus: while it runs nothing else is free", (g16.focus(r), any(x["owner"] is None for x in r)), ([1], False))
r = rows16(p1="waiting for the human gate (approve with the human-approved label, or comment)")
check("focus: parked on a person, the next PR takes the focus", g16.focus(r), [2])
r = [x for x in rows16() if x["number"] != 2]
check("focus: merged (gone from the list), the next PR takes the focus", g16.focus(r), [1])
g16.LIM["prs_at_once"] = 2; g16.FOCUS.write_text("[]")
check("focus: prs_at_once 2 keeps two", g16.focus(rows16()), [1, 2])

# a cancelled required check is rerun once per head, never reviewed or landed on CI that never ran
g17 = importlib.machinery.SourceFileLoader("pc17", str(src)).load_module()
g17.WORK = tmp / "w17"; (g17.WORK / "prs" / "40").mkdir(parents=True); g17.LEDGER = g17.WORK / "ledger.jsonl"
g17.DRY, g17.log = False, (lambda m: None)
reruns17 = []
g17.sh = lambda *c, **k: (reruns17.append(c[3]), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
roll = [dict(name="ci/tests", conclusion="CANCELLED", detailsUrl="https://github.com/o/r/actions/runs/77/job/1"),
        dict(name="ci/standards-check", conclusion="SUCCESS"), dict(name="ci/security-scan", conclusion="SUCCESS")]
g17.gh_json = lambda *a: dict(headRefOid="h40", statusCheckRollup=roll)
g17.FOCUS = g17.WORK / "focus.json"; g17.FOCUS.write_text("[41]"); g17.LIM["prs_at_once"] = 1
check("cancelled CI: a PR outside the focus is not rerun (it would refill the queue)", (g17.ci_pending(40, "h40"), reruns17), (False, []))
g17.FOCUS.write_text("[40]")
check("cancelled CI: first look reruns it and waits", (g17.ci_pending(40, "h40"), reruns17), (True, ["77"]))
check("cancelled CI: a second look at the same head does not rerun again", (g17.ci_pending(40, "h40"), reruns17), (False, ["77"]))

print("\nFAILED: " + ", ".join(fails) if fails else "\nall scenario checks passed")
sys.exit(1 if fails else 0)
