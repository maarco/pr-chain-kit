"""Drift checks for bin/pr-chain. Git part runs on a scratch repo; chain part runs on stubs (no gh, no agents)."""
import atexit, importlib.machinery, importlib.util, os, pathlib, shutil, subprocess, sys, tempfile, types

tmp = pathlib.Path(tempfile.mkdtemp(prefix="drift--prc"))
atexit.register(shutil.rmtree, tmp, True)
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt")
src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
spec = importlib.util.spec_from_loader("prchain", loader)
sys.dont_write_bytecode = True
pc = importlib.util.module_from_spec(spec); loader.exec_module(pc)
assert str(pc.WORK).startswith(str(tmp)), pc.WORK
fails = []
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

# ---- A. main_only_move on a real scratch repo
up, repo = tmp / "up.git", tmp / "repo"
def g(*a, cwd=repo):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True,
                          env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).stdout.strip()
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(up)], check=True)
subprocess.run(["git", "clone", "-q", str(up), str(repo)], check=True, capture_output=True)
def commit(path, text, msg):
    f = repo / path; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(text); g("add", "-A"); g("commit", "-q", "-m", msg); return g("rev-parse", "HEAD")
g("checkout", "-q", "-b", "main"); commit("a.py", "1\n", "base"); g("push", "-q", "origin", "main")
g("checkout", "-q", "-b", "pr"); audited = commit("feature.py", "x\n", "feat")
g("checkout", "-q", "-b", "other", "main"); commit("other.py", "o\n", "other branch work")
g("checkout", "-q", "main"); commit("b.py", "2\n", "main moves"); g("push", "-q", "origin", "main"); g("fetch", "-q", "origin")
g("checkout", "-q", "pr"); g("merge", "-q", "--no-edit", "origin/main"); merged = g("rev-parse", "HEAD")
pc.CHECKOUT = repo
check("merge of main only -> True", pc.main_only_move(audited, merged), True)
moved = commit("changes/12.fixed.md", "- entry\n", "docs(changelog): move entry")
check("merge of main + changelog move -> True", pc.main_only_move(audited, moved), True)
code = commit("feature.py", "y\n", "sneaky code change")
check("merge of main + code commit -> False", pc.main_only_move(audited, code), False)
check("same head -> False", pc.main_only_move(audited, audited), False)
check("not a descendant -> False", pc.main_only_move(code, audited), False)
g("checkout", "-q", "-b", "pr2", audited); g("merge", "-q", "--no-edit", "other"); om = g("rev-parse", "HEAD")
check("merge of a non-main branch -> False", pc.main_only_move(audited, om), False)
g("checkout", "-q", "-b", "pr3", audited); only_cl = commit("CHANGELOG.md", "c\n", "changelog only, no merge")
check("no merge at all -> False", pc.main_only_move(audited, only_cl), False)

# ---- A2. carried_pass: an earlier run's audited PASS survives a main-only move, and nothing else
import time as _t
vd = pc.WORK / "prs" / "900"
def verdict(folder, word, head, age):
    f = vd / folder / "verdict.md"; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(f"{word}\nhead {head}\n")
    os.utime(f, (_t.time() - age, _t.time() - age))
verdict("r1-review", "PASS", audited, 300); verdict("r1a1-gate", "PASS", audited, 200)
check("carried: audited PASS + merge of main -> the audited head", pc.carried_pass(900, merged), audited)
check("carried: same head already audited -> that head", pc.carried_pass(900, audited), audited)
check("carried: a code commit since -> None", pc.carried_pass(900, code), None)
verdict("r2-review", "FAIL", merged, 100)
check("carried: a later FAIL voids the PASS -> None", pc.carried_pass(900, merged), None)
check("carried: a PR with no verdicts -> None", pc.carried_pass(901, merged), None)

# ---- A2b. a stored FAIL is replayed only while no prompt or field-guide file is newer than it (#1055, 11-16)
pd = tmp / "prompts"; pd.mkdir(); real_prompts, pc.PROMPTS = pc.PROMPTS, pd
(pd / "field-guide.md").write_text("g\n"); os.utime(pd / "field-guide.md", (_t.time() - 100,) * 2)
v_old, v_new = tmp / "v-old.md", tmp / "v-new.md"
for f, age in ((v_old, 200), (v_new, 50)):
    f.write_text("FAIL\nhead x\n"); os.utime(f, (_t.time() - age,) * 2)
check("stale FAIL (older than the guide) is judged again", pc.judged_by_current_rules(v_old), False)
check("FAIL written after the last prompt edit is replayed", pc.judged_by_current_rules(v_new), True)
pc.PROMPTS = real_prompts

# ---- A3. file_followups: one issue per distinct item, priority label from the P level, never filed twice
calls, real_sh = [], pc.sh
def fake_sh(*a, **k):
    if a[:3] == ("gh", "issue", "create"):
        calls.append(a); return types.SimpleNamespace(returncode=0, stdout=f"https://github.com/x/y/issues/{500 + len(calls)}\n", stderr="")
    return real_sh(*a, **k)
pc.sh = fake_sh
fv = vd / "r9-ui" / "ui-verdict.md"; fv.parent.mkdir(parents=True, exist_ok=True)
fv.write_text("FAIL\nhead x\nFindings:\n- P2 a.png: row - wrong - fix\nFollow-up:\n- P1 a.png: composer - stop button covers the draft - reserve room\n- P3 b.png: label - casing differs - match sibling\nSummary: ok\n")
pc.file_followups(900, fv, area="area/webui")
labels = [[a[i + 1] for i, x in enumerate(a) if x == "--label"] for a in calls]
check("followups: one issue per item, on a FAIL verdict too", len(calls), 2)
check("followups: a P1 item gets priority/p1 and the area label", labels[0] if labels else None, ["bug", "priority/p1", "area/webui"])
check("followups: a P3 item gets priority/p3", labels[1][1] if len(labels) > 1 else None, "priority/p3")
pc.file_followups(900, fv, area="area/webui")
check("followups: the same items are not filed twice", len(calls), 2)
pc.sh = real_sh

# ---- A4. the loop check works from a child of the loop (macOS pgrep leaves a caller's ancestors out; the doctor runs inside resume)
import time
lpd = tmp / ("lp" + os.urandom(4).hex()) / "bin"; lpd.mkdir(parents=True)
(lpd / "child.py").write_text(
    "import importlib.machinery, importlib.util, sys\n"
    "sys.dont_write_bytecode = True\n"
    f"l = importlib.machinery.SourceFileLoader('prchain', {str(src)!r})\n"
    "m = importlib.util.module_from_spec(importlib.util.spec_from_loader('prchain', l)); l.exec_module(m)\n"
    "print(m.loop_alive(sys.argv[1]))\n")
(lpd / "pr-chain-loop").write_text(  # stands in for the loop: runs the check as its own child, then waits to be released
    "import os, subprocess, sys, time\n"
    "r = subprocess.run([sys.executable, '-B', sys.argv[1], sys.argv[2]], capture_output=True, text=True)\n"
    "open(sys.argv[3], 'w').write(r.stdout.strip() or r.stderr.strip()[-300:])\n"
    "t = time.time()\n"
    "while not os.path.exists(sys.argv[4]) and time.time() - t < 30: time.sleep(0.1)\n")
pat = rf"^[^ ]*python[^ ]* -B {lpd}/pr-chain-loop"
res, rel = tmp / "child-result", tmp / "release"
fake = subprocess.Popen([sys.executable, "-B", str(lpd / "pr-chain-loop"), str(lpd / "child.py"), pat, str(res), str(rel)])
atexit.register(fake.kill)
t0 = time.time()
while not res.exists() and time.time() - t0 < 20: time.sleep(0.1)
outside = pc.loop_alive(pat)
if outside:
    check("loop_alive: true when asked from a child of the loop", res.read_text() if res.exists() else None, "True")
    check("loop_alive: true when asked from outside the loop", outside, True)
else:  # a sandbox that hides other processes from pgrep (a peer's run on 11-16 hit this): nothing here can be judged, so say so
    print("skip loop_alive checks: pgrep in this environment cannot see another process, not even from outside it")
rel.write_text("go"); fake.wait(timeout=20)
check("loop_alive: false once the loop has exited", pc.loop_alive(pat), False)

# ---- A5. a RESTING end keys the rest on the head GitHub shows now (#1101 spun every 40 s on 11-16: STATE.head was stale, so owner_reason never saw the rest)
import json as _json
pc.state_path(77).parent.mkdir(parents=True, exist_ok=True)
pc.set_state(77, status="running", head="OLDHEAD", rests={"OLDHEAD": 3}, rest_total=4)
real_pr_info = pc.pr_info
pc.pr_info = lambda n: {"headRefOid": "NEWHEAD", "isCrossRepository": True}
pc.die(77, "RESTING", "conflict with main after the human approval")
st = _json.loads(pc.state_path(77).read_text())
check("die RESTING: STATE.head becomes the PR's head on GitHub", st["head"], "NEWHEAD")
check("die RESTING: the rest is counted at that head, from zero", st["rests"], {"NEWHEAD": 1})
check("die RESTING: rest_total keeps counting", st["rest_total"], 5)
pr77 = {"number": 77, "headRefOid": "NEWHEAD", "author": {"login": "owner"}}
check("owner_reason: the PR is resting, not free, at its real head", str(pc.owner_reason(pr77, "")).startswith("resting until"), True)
pc.pr_info = lambda n: (_ for _ in ()).throw(RuntimeError("gh down"))
pc.set_state(78, status="running", head="H78")
pc.die(78, "RESTING", "x")
check("die RESTING: GitHub down falls back to STATE.head", _json.loads(pc.state_path(78).read_text())["head"], "H78")
pc.pr_info = real_pr_info

# ---- A6. a finished chain leaves no worktree, code index or UI stack output behind (leftovers of finished PRs fill the disk)
cidx = tmp / "cmm"; cidx.mkdir(); pc.CODE_INDEX = cidx
idx_stem = str(pc.WT_ROOT).lstrip("/").replace("/", "-")
real_sh = pc.sh
pc.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="") if c[:1] == ("gh",) else real_sh(*c, **k)  # git is real, gh is not
def mkwt(name, dirty=False):
    p = pc.WT_ROOT / name; pc.WT_ROOT.mkdir(parents=True, exist_ok=True)
    g("worktree", "add", "-q", "--detach", str(p), "HEAD")
    if dirty: (p / "scratch.txt").write_text("untracked")  # an untracked file makes a plain `git worktree remove` refuse
    return p
def mkjunk(pr):
    wt = mkwt(f"pr-{pr}-r1-review", dirty=True)
    orphan = pc.WORK / "prs" / str(pr) / "r2-review.old123" / "wt"; orphan.mkdir(parents=True); (orphan / "f").write_text("x")
    dist = pc.WORK / "prs" / str(pr) / "r1-ui" / "stack" / "dist"; dist.mkdir(parents=True); (dist / "b.js").write_text("x")
    for suf in (".db", ".db-wal"): (cidx / f"{idx_stem}-pr-{pr}-r1-review{suf}").write_text("x")
    return wt, orphan, dist
def listed(path): return str(path) in g("worktree", "list", "--porcelain")
wt9, orphan9, dist9 = mkjunk(9)
other = mkwt("pr-90-r1-review")  # PR 90 is not PR 9
cl = getattr(pc, "cleanup_pr", None)
check("pr_leftovers sees the worktree, orphan, stack output and indexes", len(getattr(pc, "pr_leftovers", lambda n: [])(9)), 5)
pc.set_state(9, status="running", head="H"); (pc.CLAIMS / "9").mkdir(parents=True, exist_ok=True); (pc.CLAIMS / "9" / "pid").write_text(str(os.getpid()))
check("cleanup_pr: a live chain on the PR means nothing is touched", (cl(9, force=True) if cl else None, wt9.exists()), (0, True))
shutil.rmtree(pc.CLAIMS / "9")
if cl: cl(9, force=False)
check("cleanup_pr (PR still open): a dirty worktree stays", wt9.exists(), True)
check("cleanup_pr (PR still open): the UI stack build output goes", dist9.exists(), False)
check("cleanup_pr (PR still open): the code index goes", sorted(f.name for f in cidx.glob("*-pr-9-*")), [])
if cl: cl(9, force=True)
check("cleanup_pr (merged or closed): the dirty worktree goes and git forgets it", (wt9.exists(), listed(wt9)), (False, False))
check("cleanup_pr (merged or closed): the orphan round worktree goes", orphan9.exists(), False)
check("cleanup_pr: another PR's worktree (pr-90) is left alone", other.exists(), True)
check("pr_leftovers is empty afterwards", list(getattr(pc, "pr_leftovers", lambda n: ["x"])(9)), [])
# the chain's own end funnels through die(): a merged PR loses everything, an open one only what a plain remove can take
wt9, orphan9, dist9 = mkjunk(9)
pc.die(9, "MERGED", "MERGED")
check("die MERGED: worktree, orphan and stack output are all gone", (wt9.exists(), orphan9.exists(), dist9.exists()), (False, False, False))
wt9, orphan9, dist9 = mkjunk(9); clean_wt = mkwt("pr-9-r3-review")
pc.pr_info = lambda n: {"headRefOid": "H2", "isCrossRepository": True}
pc.set_state(9, status="running", head="H")
pc.die(9, "RESTING", "round cap")
check("die RESTING (PR still open): the clean worktree goes, the dirty one stays", (clean_wt.exists(), wt9.exists()), (False, True))
pc.cleanup_pr(9, force=True) if cl else None
# a new build waits while the Mac is nearly full
pc.DISK_FLOOR_GB = 1e9
check("build_issue: skipped (so the loop backs off) under the disk floor", str(pc.build_issue(5, None)).startswith("skipped: only "), True)
pc.DISK_FLOOR_GB = 0
pc.sh = real_sh

# ---- A7. the doctor starts the worktree janitor only against the real work dir (a test run must never remove real worktrees)
launched = []
real_popen = pc.subprocess.Popen
pc.subprocess.Popen = lambda *a, **k: launched.append(a) or types.SimpleNamespace()
pc.run_janitor()
pc.subprocess.Popen = real_popen
check("run_janitor: a test work dir launches nothing and leaves no stamp", (launched, (pc.WORK / "janitor.last").exists()), ([], False))

# ---- B-E. run_chain with stubs
def scenario(name, review_script, land_script, ci_script=None, main_only=True, want=None, want_kinds=None, approved=None, fold_script=None, carried=None):
    st = dict(head="h0", n=0, kinds=[], folds=0, lands=0, behind=False)
    info = dict(number=1, state="OPEN", files=[{"path": "a.py"}], author={"login": "owner"}, isCrossRepository=False, headRefName="pr", title="t", body="")
    pc.pr_info = lambda pr: info
    pc.branch_head = lambda i: st["head"]
    pc.fetch = lambda i: "m"
    pc.claim = lambda pr: True
    pc.release = lambda pr: None
    pc.main_only_move = lambda old, new: main_only
    pc.carried_pass = lambda pr, cur, review=False: None if review else carried  # the head an earlier run audited as PASS (None = review from scratch)
    pc.fix = lambda *a_, **k_: (None, "fixer made no commit")  # no scenario may reach a real fixer
    def fold(pr, i):
        st["folds"] += 1
        if fold_script and fold_script(st) is None: return None
        if st["behind"]:
            st["n"] += 1; st["head"] = f"h{st['n']}"; st["behind"] = False
        return st["head"]
    pc.fold = fold
    pc.ci_red = lambda pr, head: []
    pc.gate_findings = lambda pr: []
    pc.ci_pending = lambda pr, head: False
    rs = list(review_script)
    def review(pr, rnd, kind, model, post, old=None, extra="", **kw):
        v = rs.pop(0)
        if isinstance(v, tuple): want_kind, v = v; assert want_kind == kind, (name, want_kind, kind)
        if v == "BEHIND-NOW": st["behind"] = True; v = "BEHIND"
        if v == "BEHIND": st["kinds"].append(kind + ":BEHIND"); return "BEHIND", None, st["head"]
        st["kinds"].append(f"{kind}@{st['head']}" + (f"<-{old}" if old else ""))
        vp = tmp / f"v{len(st['kinds'])}.md"; vp.write_text(f"{v}\nhead {st['head']}\nFindings:\n- P1 a.py:1 - x - y\n")
        return v, vp, st["head"]
    pc.review = review
    cs = list(ci_script or [])
    pc.wait_ci = lambda pr, head, minutes=45: (cs.pop(0) if cs else "GREEN")
    ls = list(land_script)
    def land(pr, head, reviewer):
        st["lands"] += 1; st["landed"] = (head, reviewer); r = ls.pop(0)
        if "BEHIND" in r: st["behind"] = True
        return r
    pc.land = land
    a = types.SimpleNamespace(max_rounds=4, max_audits=2, vision_ok=False, no_gate=False, no_post=True, no_human=False, land=True, max_ui=2,
                              review_model="rev", gate_model="gate", ui_model="ui", fix_model="fix")
    got = pc.run_chain(1, a, approved=approved)
    check(name + " status", got, want)
    if want_kinds is not None: check(name + " stages", st["kinds"], want_kinds)
    return st

BEH = "LAND-FAILED: STOP: #1 is BEHIND"
st = scenario("B main moves during Haiku review, then during CI",
              [("review", "PASS"), ("gate", "BEHIND-NOW"), ("gate", "PASS"), ("addendum", "PASS")], [BEH, "MERGED"], want="MERGED",
              want_kinds=["review@h0", "gate:BEHIND", "gate@h1", "addendum@h2<-h1"])
check("B landed at the addendum head", st["landed"][0], "h2")
check("B approval note names the addendum", "addendum" in st["landed"][1], True)
scenario("C main moves on every landing try", [("review", "PASS"), ("gate", "PASS"), ("addendum", "PASS"), ("addendum", "PASS")],
         [BEH, BEH, BEH], want="LAND-STOPPED", want_kinds=["review@h0", "gate@h0", "addendum@h1<-h0", "addendum@h2<-h1"])
scenario("D addendum fails -> full audit", [("review", "PASS"), ("gate", "PASS"), ("addendum", "FAIL"), ("gate", "PASS")], [BEH, "MERGED"],
         want="MERGED", want_kinds=["review@h0", "gate@h0", "addendum@h1<-h0", "gate@h1"])
scenario("E head moved by a real push -> full audit, no addendum", [("review", "PASS"), ("gate", "PASS"), ("gate", "PASS")], [BEH, "MERGED"],
         main_only=False, want="MERGED", want_kinds=["review@h0", "gate@h0", "gate@h1"])
scenario("F audit fails on the moved head", [("review", "PASS"), ("gate", "PASS"), ("gate", "FAIL")], [BEH], main_only=False, want="RESTING")
scenario("G no drift: unchanged path", [("review", "PASS"), ("gate", "PASS")], ["MERGED"], want="MERGED", want_kinds=["review@h0", "gate@h0"])
scenario("H main keeps moving before the audit", [("review", "PASS")] + [("gate", "BEHIND-NOW")] * 3, [], want="MAIN-MOVING")
scenario("I missing verdict still stops as NO-VERDICT", [("review", None)], [], want="NO-VERDICT")
st = scenario("J approved at the human gate, main moved since", [("addendum", "PASS")], ["MERGED"], want="MERGED",
              want_kinds=["addendum@h1<-h0"], approved={"gate_head": "h0"}, fold_script=lambda s: s.update(behind=(s["folds"] == 1)) or 1)
scenario("K earlier audited PASS + main-only move -> addendum only, no review, no audit", [("addendum", "PASS")], ["MERGED"],
         carried="hold", want="MERGED", want_kinds=["addendum@h0<-hold"])
scenario("L carried, addendum fails -> full audit passes", [("addendum", "FAIL"), ("gate", "PASS")], ["MERGED"],
         carried="hold", want="MERGED", want_kinds=["addendum@h0<-hold", "gate@h0"])
scenario("M carried, addendum and audit fail -> findings go to the fixer", [("addendum", "FAIL"), ("gate", "FAIL")], [],
         carried="hold", want="RESTING", want_kinds=["addendum@h0<-hold", "gate@h0"])
scenario("N carried, main moves under the addendum -> fold and carry again", [("addendum", "BEHIND-NOW"), ("addendum", "PASS")], ["MERGED"],
         carried="hold", want="MERGED", want_kinds=["addendum:BEHIND", "addendum@h1<-hold"])
scenario("P this head was already audited -> no review, no audit, no addendum", [], ["MERGED"], carried="h0", want="MERGED", want_kinds=[])
scenario("O carried, no verdict from addendum or audit -> NO-VERDICT", [("addendum", None), ("gate", None)], [], carried="hold", want="NO-VERDICT")
print("\nFAILED: " + ", ".join(fails) if fails else "\nall drift checks passed")
sys.exit(1 if fails else 0)
