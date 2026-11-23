"""UI-step loop checks for bin/pr-chain, on stubs (no gh, no agents, no git)."""
import atexit, importlib.machinery, importlib.util, os, pathlib, shutil, sys, tempfile, types

tmp = pathlib.Path(tempfile.mkdtemp(prefix="uiloop--prc"))
atexit.register(shutil.rmtree, tmp, True)
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt")
src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain", str(src))
sys.dont_write_bytecode = True
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader)); loader.exec_module(pc)
assert str(pc.WORK).startswith(str(tmp)), pc.WORK
_real_sh = pc.sh  # git stays real; no gh call may reach GitHub (die() edits labels on PR 1 otherwise)
pc.sh = lambda *c, **k: types.SimpleNamespace(returncode=0, stdout="", stderr="") if c[:1] == ("gh",) else _real_sh(*c, **k)
fails = []
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

def scenario(name, reviews, uis, fixes, want, want_trace, max_ui=3):
    st = dict(head="h0", n=0, trace=[])
    info = dict(number=1, state="OPEN", files=[{"path": "packages/kollabor-webui/frontend/src/App.tsx"}], author={"login": "owner"},
                isCrossRepository=False, headRefName="pr", title="t", body="")
    assert any(pc.UI_RE.search(f["path"]) for f in info["files"])
    pc.pr_info = lambda pr: info
    pc.branch_head = lambda i: st["head"]
    pc.fetch = lambda i: "m"
    pc.claim = lambda pr: True
    pc.release = lambda pr: None
    pc.fold = lambda pr, i: st["head"]
    pc.ci_red = lambda pr, head: []
    pc.gate_findings = lambda pr: []
    pc.ci_pending = lambda pr, head: False
    def vfile(v):
        vp = tmp / f"v{len(st['trace'])}.md"; vp.write_text(f"{v}\nhead {st['head']}\nFindings:\n- P1 App.tsx:1 - x - y\n"); return vp
    rs, us, fs = list(reviews), list(uis), list(fixes)
    def review(pr, rnd, kind, model, post, old=None, extra="", **kw):
        k, v = rs.pop(0); assert k == kind, (name, k, kind)
        st["trace"].append(f"{kind}@{st['head']}"); return v, vfile(v), st["head"]
    def ui_review(pr, rnd, head, a):
        v = us.pop(0); st["trace"].append(f"ui@{head}"); return v, vfile(v), head
    def fix(pr, rnd, model, vp, head, last, **kw):
        r = fs.pop(0); st["trace"].append(f"fix@{head}")
        if r != "OK": return (None, "fixer said BLOCKED") if r == "BLOCKED" else ("DISPUTE", "no")
        st["n"] += 1; st["head"] = f"h{st['n']}"; return st["head"], "pushed"
    pc.review, pc.ui_review, pc.fix = review, ui_review, fix
    pc.human_gate = lambda pr, head, ui_vp, a, **kw: (st.update(gate=head), "AWAIT-HUMAN")[1]
    a = types.SimpleNamespace(max_rounds=4, max_audits=2, vision_ok=False, no_gate=False, no_post=True, no_human=False, land=True,
                              max_ui=max_ui, review_model="rev", gate_model="gate", ui_model="ui", fix_model="fix")
    check(name + " status", pc.run_chain(1, a), want)
    check(name + " stages", st["trace"], want_trace)
    return st

R = [("review", "PASS"), ("gate", "PASS")]
st = scenario("A UI passes first time: no fixer, no second audit", R, ["PASS"], [], "AWAIT-HUMAN", ["review@h0", "gate@h0", "ui@h0"])
check("A human gate sees the audited head", st["gate"], "h0")
st = scenario("B two UI fails: only fix + UI review repeat, then one audit", R + [("gate", "PASS")], ["FAIL", "FAIL", "PASS"], ["OK", "OK"],
              "AWAIT-HUMAN", ["review@h0", "gate@h0", "ui@h0", "fix@h0", "ui@h1", "fix@h1", "ui@h2", "gate@h2"])
check("B human gate sees the final head", st["gate"], "h2")
scenario("C UI never passes: rests after --max-ui", R, ["FAIL"] * 4, ["OK"] * 3, "RESTING",
         ["review@h0", "gate@h0", "ui@h0", "fix@h0", "ui@h1", "fix@h1", "ui@h2", "fix@h2", "ui@h3"])
scenario("D UI fixer blocked: rests", R, ["FAIL"], ["BLOCKED"], "RESTING", ["review@h0", "gate@h0", "ui@h0", "fix@h0"])
scenario("E audit fails after the UI fix: code fixer, then a full lap", R + [("gate", "FAIL"), ("review", "PASS"), ("gate", "PASS")],
         ["FAIL", "PASS", "PASS"], ["OK", "OK"], "AWAIT-HUMAN",
         ["review@h0", "gate@h0", "ui@h0", "fix@h0", "ui@h1", "gate@h1", "fix@h1", "review@h2", "gate@h2", "ui@h2"])
scenario("F UI review gives no verdict after a fix", R, ["FAIL", None], ["OK"], "UI-NO-VERDICT", ["review@h0", "gate@h0", "ui@h0", "fix@h0", "ui@h1"])
# G (11-16): NOT-SHOWN is no longer an answer. The reviewer must produce THE shot (the PR's recipe or proof, its own
# staging, terminal shots from the PR's tmux specs) or FAIL with a fix agents can make. A NOT-SHOWN file parses as no
# verdict: no human gate, no comment asking a person; the run ends UI-NO-VERDICT and is retried like any missing verdict.
ns = tmp / "ns.md"; ns.write_text("NOT-SHOWN\nhead h0\n")
st = scenario("G UI NOT-SHOWN is no verdict: no human gate", R, [pc.parse_verdict(ns)], [], "UI-NO-VERDICT", ["review@h0", "gate@h0", "ui@h0"])
check("G no human gate", st.get("gate"), None)
# [ui] enabled = false really turns UI review off (it was read and then ignored, 11-23)
import subprocess
off = tmp / "ui-off.toml"
off.write_text(pathlib.Path(os.environ["PR_CHAIN_CONFIG"]).read_text().replace("[ui]\nenabled = true", "[ui]\nenabled = false"))
probe = (f"import importlib.machinery as m, importlib.util as u; l = m.SourceFileLoader('p', {str(src)!r}); "
         "p = u.module_from_spec(u.spec_from_loader('p', l)); l.exec_module(p); "
         "print(bool(p.UI_RE.search('packages/kollabor-webui/frontend/src/App.tsx')))")
r = subprocess.run([sys.executable, "-B", "-c", probe], env={**os.environ, "PR_CHAIN_CONFIG": str(off)}, capture_output=True, text=True)
check("[ui] enabled = false: a .tsx change is not a UI change", r.stdout.strip(), "False")
print("\nFAILED: " + ", ".join(fails) if fails else "\nall UI loop checks passed")
sys.exit(1 if fails else 0)
