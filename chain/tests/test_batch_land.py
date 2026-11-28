"""Batch landing ([land] mode = "batch"): real git in a scratch repo, an in-process fake gh, no network, no agents."""
import atexit, contextlib, importlib.machinery, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, tempfile, time, types

tmp = pathlib.Path(tempfile.mkdtemp(prefix="batch--prc"))
atexit.register(shutil.rmtree, tmp, True)
root = pathlib.Path(__file__).resolve().parents[2]
os.environ["PR_CHAIN_CONFIG"] = str(root / "chain/tests/test-config.toml")
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt")
os.environ.pop("PR_CHAIN_ENGINE", None)
# no test reaches GitHub: a gh that always fails sits first on PATH
(tmp / "nogh").mkdir(); (tmp / "nogh" / "gh").write_text("#!/bin/sh\necho 'gh is stubbed in tests' >&2\nexit 1\n"); (tmp / "nogh" / "gh").chmod(0o755)
os.environ["PATH"] = str(tmp / "nogh") + os.pathsep + os.environ.get("PATH", "")
loader = importlib.machinery.SourceFileLoader("prchain", str(root / "bin/pr-chain"))
sys.dont_write_bytecode = True
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain", loader)); loader.exec_module(pc)
assert str(pc.WORK).startswith(str(tmp)), pc.WORK
import config, merge_flow  # noqa: E402,E401  on sys.path once pr-chain is loaded

fails = []
pc.log = lambda m: None
def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok: fails.append(name)

ORIG = dict(pc.__dict__)
REAL_SH = pc.sh
REPO = pc.REPO
REQUIRED = list(pc.kit.CHECKS)
WRITES = ("create", "close", "ready", "edit", "review", "merge", "comment", "rerun")


def ok_proc(out="", rc=0, err=""):
    return types.SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class World:
    """A bare origin, the chain's checkout, a second clone that plays the PR authors, and a fake GitHub."""
    count = 0

    def __init__(self, base_age_hours=0):
        World.count += 1
        d = tmp / f"world{World.count}"
        self.up, self.dev, self.repo = d / "up.git", d / "dev", d / "checkout"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.up)], check=True)
        subprocess.run(["git", "clone", "-q", str(self.up), str(self.dev)], check=True, capture_output=True)
        self.g("checkout", "-q", "-b", "main")
        self.base = self.commit({"a.py": "1\n", "b.py": "2\n"}, "base", age=base_age_hours)
        self.g("push", "-q", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(self.up), str(self.repo)], check=True, capture_output=True)
        self.prs, self.calls, self.lands, self.chains, self.next_batch = {}, [], [], [], 900
        shutil.rmtree(pc.WORK, ignore_errors=True)
        pc.WORK.mkdir(parents=True)
        pc.__dict__.update(ORIG)
        pc.CFG.land_mode, pc.CFG.max_base_age_hours, pc.CFG.batch_branch_prefix = "batch", 48.0, "merge-flow/batch"
        pc.log = lambda m: None
        pc.CHECKOUT, pc.DRY, pc.sh = self.repo, False, self.sh
        pc.chain_label = lambda pr, want: None
        pc.cleanup_pr = lambda *a, **k: None
        pc.run_chain = lambda n, a, from_gate=False, approved=None, seed=None: (
            self.chains.append(dict(pr=n, seed=seed, approved=approved, from_gate=from_gate)), "CHAINED")[1]
        pc.land = self.land

    def g(self, *a, cwd=None, env=None):
        e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", **(env or {})}
        return subprocess.run(["git", *a], cwd=cwd or self.dev, check=True, capture_output=True, text=True, env=e).stdout.strip()

    def commit(self, files, msg, age=0):
        for path, text in files.items():
            (self.dev / path).write_text(text)
        self.g("add", "-A")
        stamp = f"{int(time.time() - age * 3600)} +0000"
        self.g("commit", "-q", "-m", msg, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        return self.g("rev-parse", "HEAD")

    def pr(self, n, files, ready=True):
        self.g("checkout", "-q", "-b", f"pr{n}", self.base)
        head = self.commit(files, f"feat: pr {n}")
        self.g("push", "-q", "origin", f"pr{n}", f"pr{n}:refs/pull/{n}/head")
        self.prs[n] = dict(number=n, state="OPEN", head=head, branch=f"pr{n}", checks="SUCCESS", base="main")
        if ready:
            self.ready(n)
        return head

    def ready(self, n):
        h = self.prs[n]["head"]
        pc.set_state(n, status="BATCH-READY", head=h, gate_head=h, reviewer="rev review, gate audit")

    def push(self, n, files):
        self.g("checkout", "-q", f"pr{n}")
        head = self.commit(files, "fix: more")
        self.g("push", "-q", "origin", f"pr{n}", f"pr{n}:refs/pull/{n}/head")
        self.prs[n]["head"] = head
        return head

    def move_main(self, files, age=0):
        self.g("checkout", "-q", "main")
        sha = self.commit(files, "main moves", age=age)
        self.g("push", "-q", "origin", "main")
        self.g("fetch", "-q", "origin", cwd=self.repo)
        return sha

    def view(self, n):
        p = self.prs[n]
        rollup = [dict(name=c, conclusion="" if p["checks"] == "PENDING" else p["checks"],
                       state="PENDING" if p["checks"] == "PENDING" else "",
                       detailsUrl=f"https://github.com/{REPO}/actions/runs/7{n}/job/1") for c in REQUIRED]
        return dict(number=n, state=p["state"], title=f"pr {n}", body=f"Fixes #{n}00", isDraft=p.get("draft", False), headRefName=p["branch"],
                    headRefOid=p["head"], mergeStateStatus="BEHIND", author={"login": "owner"}, isCrossRepository=False,
                    closingIssuesReferences=[{"number": n * 100}], files=[{"path": "x.py"}], baseRefName=p["base"],
                    statusCheckRollup=rollup, additions=1, deletions=0, labels=[dict(name=x) for x in p.get("labels", [])],
                    url=f"https://github.com/{REPO}/pull/{n}", reviewDecision=p.get("review", "APPROVED"))

    def sh(self, *c, **k):
        if c and str(c[0]).endswith("fold.py") or any(str(x).endswith("fold.py") for x in c):
            self.calls.append(("fold.py",))
            return ok_proc(rc=2)
        if c[:1] != ("gh",):
            return REAL_SH(*c, **k)
        a = c[1:]
        self.calls.append(a)
        if a[:2] == ("pr", "view"):
            return ok_proc(json.dumps(self.view(int(a[2]))))
        if a[:2] == ("pr", "list"):
            head = a[a.index("--head") + 1] if "--head" in a else None
            return ok_proc(json.dumps([self.view(n) for n, p in self.prs.items()
                                       if p["state"] == "OPEN" and head in (None, p["branch"])]))
        if a[:2] == ("issue", "list"):
            return ok_proc("[]")
        if a[:2] == ("pr", "create"):
            opt = {a[i]: a[i + 1] for i in range(2, len(a) - 1) if a[i].startswith("--")}
            n, branch = self.next_batch, opt["--head"]
            self.next_batch += 1
            head = self.g("rev-parse", f"refs/heads/{branch}", cwd=self.up)
            self.prs[n] = dict(number=n, state="OPEN", head=head, branch=branch, checks="PENDING", base=opt["--base"],
                               body=opt["--body"], title=opt["--title"])
            return ok_proc(f"https://github.com/{REPO}/pull/{n}\n")
        if a[:2] == ("pr", "close"):
            n = int(a[2])
            self.prs[n]["state"] = "CLOSED"
            if "--delete-branch" in a:
                self.g("update-ref", "-d", f"refs/heads/{self.prs[n]['branch']}", cwd=self.up)
            return ok_proc()
        if a[:1] == ("api",) and "/git/ref/heads/" in a[1]:
            branch = a[1].split("/git/ref/heads/")[1]
            return ok_proc(next(p["head"] for p in self.prs.values() if p["branch"] == branch) + "\n")
        return ok_proc()

    def land(self, pr, head, reviewer, approve_only=False):
        self.lands.append((pr, head, reviewer))
        self.prs[pr]["state"] = "MERGED"
        self.g("update-ref", "refs/heads/main", head, cwd=self.up)  # a merge puts the window's history on main
        return "MERGED"

    def writes(self):
        return [c for c in self.calls if c[0] in ("pr", "label", "run") and c[1] in WRITES or c[:1] == ("api",) and "-X" in c]

    def batch(self):
        f = pc.WORK / "batch.json"
        return json.loads(f.read_text()) if f.exists() else None

    def remote_batches(self):
        return self.g("for-each-ref", "--format=%(refname:short)", "refs/heads/merge-flow", cwd=self.up).split()


def st(n):
    return json.loads((pc.WORK / "prs" / str(n) / "STATE.json").read_text())


def ledger_rows(stage):
    f = pc.WORK / "ledger.jsonl"
    return [r for r in map(json.loads, f.read_text().splitlines() if f.exists() else []) if r.get("stage") == stage]


ARGS = types.SimpleNamespace(land=True, review_model="rev", gate_model="gate", ui_model="ui", fix_model="fix", no_post=True)


@contextlib.contextmanager
def section(name):
    """A missing feature fails the section's name instead of ending the whole file."""
    try:
        yield
    except Exception as e:  # noqa: BLE001
        print(f"FAIL {name}: {type(e).__name__}: {str(e)[:300]}")
        fails.append(name)


# ---------------------------------------------------------------- config
with section("config"):
    def cfg(data):
        c = config.Cfg()
        c.load(data, env={}, near=config.UNSET, fallback=config.UNSET, folder=config.UNSET)
        return c
    d = cfg({})
    check("config: defaults", (d.land_mode, d.max_base_age_hours, d.batch_branch_prefix), ("pr", 48.0, "merge-flow/batch"))
    s = cfg({"land": {"mode": "batch", "max_base_age_hours": 6, "batch_branch_prefix": "x/y"}})
    check("config: [land] values", (s.land_mode, s.max_base_age_hours, s.batch_branch_prefix), ("batch", 6.0, "x/y"))
    import logging
    seen = []
    h = logging.Handler(); h.emit = lambda r: seen.append((r.levelname, r.getMessage())); logging.getLogger().addHandler(h)
    check("config: an unknown mode reads as pr", cfg({"land": {"mode": "bacth"}}).land_mode, "pr")
    check("config: an unknown mode logs a warning", [lv for lv, m in seen if "bacth" in m], ["WARNING"])
    logging.getLogger().removeHandler(h)

# ---------------------------------------------------------------- needs_main, fold, review
with section("behind but clean"):
    w = World(); head = w.pr(1, {"f1.py": "1\n"}); main = w.move_main({"m.py": "m\n"})
    check("needs_main: batch, behind and clean -> False", pc.needs_main(head, main), False)
    check("fold: batch leaves a behind-but-clean PR unfolded", (pc.fold(1, pc.pr_info(1)), [c for c in w.calls if c == ("fold.py",)]), (head, []))
    pc.DRY = True
    verdict, _, rhead = pc.review(1, 1, "review", "rev", False)
    check("review: batch reviews a behind-but-clean head as it is", (verdict != "BEHIND", rhead), (True, head))
    pc.DRY, pc.CFG.land_mode = False, "pr"
    check("needs_main: pr mode, behind -> True", pc.needs_main(head, main), True)
    check("review: pr mode says BEHIND", pc.review(1, 2, "review", "rev", False)[0], "BEHIND")
    pc.heal_train = lambda: None
    pc.fold(1, pc.pr_info(1))
    check("fold: pr mode folds as today", [c for c in w.calls if c == ("fold.py",)], [("fold.py",)])

with section("head contains main"):
    w = World(); head = w.pr(1, {"f1.py": "1\n"}); w.g("fetch", "-q", "origin", cwd=w.repo)
    check("needs_main: head holds main -> False in batch mode", pc.needs_main(head, w.base), False)
    pc.CFG.land_mode = "pr"
    check("needs_main: head holds main -> False in pr mode", pc.needs_main(head, w.base), False)

with section("conflict folds"):
    w = World(); head = w.pr(1, {"a.py": "pr\n"}); main = w.move_main({"a.py": "main\n"})
    pc.heal_train = lambda: None
    check("needs_main: batch, conflict -> True", pc.needs_main(head, main), True)
    check("fold: a behind PR that conflicts folds (conflict -> None)", (pc.fold(1, pc.pr_info(1)), [c for c in w.calls if c == ("fold.py",)]), (None, [("fold.py",)]))
    check("review: batch says BEHIND on a conflict", pc.review(1, 1, "review", "rev", False)[0], "BEHIND")

with section("old base folds"):
    w = World(base_age_hours=72); head = w.pr(1, {"f1.py": "1\n"}); main = w.move_main({"m.py": "m\n"})
    pc.heal_train = lambda: None
    check("needs_main: batch, base older than the limit -> True", pc.needs_main(head, main), True)
    pc.fold(1, pc.pr_info(1))
    check("fold: an old base folds", [c for c in w.calls if c == ("fold.py",)], [("fold.py",)])
    pc.CFG.max_base_age_hours = 100.0
    check("needs_main: batch, base inside a longer limit -> False", pc.needs_main(head, main), False)

# ---------------------------------------------------------------- the chain ends BATCH-READY
for engine in ("py", "flow"):
    with section(f"chain end ({engine})"):
        w = World()
        pc.__dict__.update(ORIG); pc.log = lambda m: None
        pc.CFG.land_mode, pc.DRY = "batch", False
        pc.sh = lambda *c, **k: ok_proc()
        os.environ["PR_CHAIN_ENGINE"] = engine
        info = dict(number=1, state="OPEN", files=[{"path": "x.py"}], author={"login": "owner"}, isCrossRepository=False,
                    headRefName="pr", title="t", body="")
        pc.pr_info = lambda n: info
        pc.branch_head = lambda i: "h0"; pc.fetch = lambda i: "m"; pc.claim = lambda n: True; pc.release = lambda n: None
        pc.fold = lambda n, i: "h0"
        pc.chain_label = lambda pr, want: None; pc.cleanup_pr = lambda *a, **k: None
        def review(pr, rnd, kind, model, post, old=None, extra="", **kw):
            vp = tmp / f"v-{engine}-{kind}.md"; vp.write_text("PASS\nhead h0\n"); return "PASS", vp, "h0"
        pc.review = review
        pc.ci_red = lambda pr, head: []; pc.gate_findings = lambda pr: []; pc.ci_pending = lambda pr, head: False
        pc.wait_ci = lambda pr, head, minutes=45: "GREEN"
        landed = []
        pc.land = lambda pr, head, reviewer, approve_only=False: (landed.append((pr, head, approve_only)), "APPROVED" if approve_only else "MERGED")[1]
        a = types.SimpleNamespace(max_rounds=4, max_audits=2, vision_ok=False, no_gate=False, no_post=True, no_human=False, land=True,
                                  max_ui=3, review_model="rev", gate_model="gate", ui_model="ui", fix_model="fix")
        got = pc.run_chain(1, a)
        check(f"chain ({engine}): ends BATCH-READY", got, "BATCH-READY")
        check(f"chain ({engine}): one approve-only land call", landed, [(1, "h0", True)])
        s = st(1)
        check(f"chain ({engine}): state keeps gate_head and reviewer", (s.get("status"), s.get("gate_head"), bool(s.get("reviewer"))),
              ("BATCH-READY", "h0", True))
        pc.land = lambda pr, head, reviewer, approve_only=False: "LAND-FAILED: STOP: required checks not green: {'ci/tests': 'FAILURE'}"
        check(f"chain ({engine}): a refused approval is not BATCH-READY", pc.run_chain(1, a), "CI-RED")
        pc.CFG.land_mode = "pr"; landed.clear()
        pc.land = lambda pr, head, reviewer, approve_only=False: (landed.append((pr, head, approve_only)), "MERGED")[1]
        check(f"chain ({engine}): pr mode still merges", (pc.run_chain(1, a), landed), ("MERGED", [(1, "h0", False)]))
    os.environ.pop("PR_CHAIN_ENGINE", None)

with section("land() approve-only"):
    w = World()
    pc.land = ORIG["land"]
    ran = []
    pc.pr_info = lambda n: dict(author={"login": "owner"})
    pc.sh = lambda *c, **k: (ran.append(c), ok_proc("approved as bot\n"))[1]
    check("land(approve_only): returns APPROVED", pc.land(1, "h" * 40, "r", approve_only=True), "APPROVED")
    check("land(approve_only): passes the flag and touches no git", (len(ran), ran[0][-1]), (1, "--approve-only"))

with section("pick"):
    w = World(); h = w.pr(1, {"f1.py": "1\n"})
    for head in (h, "0" * 40):
        why = pc.owner_reason(dict(number=1, headRefOid=head, author={"login": "owner"}), "")
        check(f"pick: BATCH-READY is never free ({'same' if head == h else 'new'} head)", bool(why and "batch" in why), True)
    check("BATCH-READY is resume's", "BATCH-READY" in pc.RESUME_OWNED, True)
    end = pc.FLOW["end_states"].get("BATCH-READY") or {}
    check("flow.json: BATCH-READY is a waiting state, not retryable", (end.get("group"), bool(end.get("retryable"))), ("waiting", False))

# ---------------------------------------------------------------- the batch step
with section("open and land"):
    w = World()
    h1, h2 = w.pr(1, {"f1.py": "1\n"}), w.pr(2, {"f2.py": "2\n"})
    main = w.move_main({"m.py": "m\n"})
    rows = pc.batch_step(ARGS)
    b = w.batch()
    check("open: one batch PR", [c[:2] for c in w.writes()], [("pr", "create")])
    check("open: batch.json keys", sorted(b), sorted(["pr", "url", "branch", "base", "commit", "heads", "opened", "queue", "held"]))
    check("open: batch.json values", (b["pr"], b["url"], b["base"], b["heads"], b["queue"], b["held"]),
          (900, f"https://github.com/{REPO}/pull/900", main, {"1": h1, "2": h2}, [], []))
    check("open: branch name", bool(re.fullmatch(r"merge-flow/batch-\d{8}-\d{6}", b["branch"])), True)
    check("open: opened stamp", bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", b["opened"])), True)
    check("open: the pushed branch is the commit", w.g("rev-parse", b["branch"], cwd=w.up), b["commit"])
    check("open: the body is merge-flow's", merge_flow.parse_window_body(w.prs[900]["body"]), (main, {1: h1, 2: h2}))
    check("open: the body links the issues", ("Refs #100" in w.prs[900]["body"], "Refs #200" in w.prs[900]["body"]), (True, True))
    check("open: each PR knows its batch", (st(1).get("batch"), st(2).get("batch")), (900, 900))
    row = ledger_rows("batch-open")
    check("open: ledger row uses batch= and prs=", [(r.get("batch"), r.get("prs"), "pr" in r) for r in row], [(900, [1, 2], False)])
    check("open: batch-plan logged", [(r.get("batch"), "pr" in r) for r in ledger_rows("batch-plan")], [(None, False)])

    w.calls.clear(); pc.batch_step(ARGS)
    check("pending: waits", (w.writes(), w.lands, w.batch()["pr"]), ([], [], 900))
    w.prs[900]["checks"] = "SUCCESS"
    rows = pc.batch_step(ARGS)
    check("green: lands the batch PR at its commit", [(p, h) for p, h, _ in w.lands], [(900, b["commit"])])
    check("green: both PRs end MERGED", (st(1)["status"], st(2)["status"]), ("MERGED", "MERGED"))
    check("green: the why names the batch", st(1)["why"], "landed in merge-flow batch #900")
    check("green: rows", sorted(rows), [(1, "MERGED"), (2, "MERGED")])
    check("green: cleared", (w.batch(), st(1).get("batch")), ({"pr": None, "queue": [], "held": []}, None))
    check("green: ledger batch-land", [(r["batch"], r["prs"]) for r in ledger_rows("batch-land")], [(900, [1, 2])])

with section("merged elsewhere"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.g("update-ref", "refs/heads/main", w.batch()["commit"], cwd=w.up)  # merged by hand: the window is on main
    for n in (1, 2, 900):
        w.prs[n]["state"] = "MERGED"
    pc.batch_step(ARGS)
    check("batch merged between laps: PRs end MERGED", (st(1)["why"], st(2)["status"], w.batch()["pr"], w.lands),
          ("landed in merge-flow batch #900", "MERGED", None, []))

with section("merged batch, a head not on main"):
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.g("push", "-q", "origin", f"{h1}:refs/heads/main")  # main got #1's approved head, not #2's
    w.prs[900]["state"] = "MERGED"
    pc.batch_step(ARGS)
    check("merged batch: #1 ends MERGED", (st(1)["status"], st(1)["why"]), ("MERGED", "landed in merge-flow batch #900"))
    check("merged batch: #2 stays BATCH-READY with no batch", (st(2)["status"], st(2).get("batch")), ("BATCH-READY", None))
    check("merged batch: a batch-land row marks #2 missing",
          [(r["batch"], r["prs"]) for r in ledger_rows("batch-land") if r.get("missing")], [(900, [2])])

with section("land refused"):
    for msg, closed in (("LAND-FAILED: STOP: #900 is BEHIND", True), ("LAND-FAILED: STOP: approval refused", False)):
        w = World(); w.pr(1, {"f1.py": "1\n"})
        pc.batch_step(ARGS); w.prs[900]["checks"] = "SUCCESS"
        pc.land = lambda pr, head, reviewer, approve_only=False, m=msg: m
        pc.batch_step(ARGS)
        check(f"refused ({msg[-14:]}): {'closed' if closed else 'left open'}", (w.prs[900]["state"], w.batch()["pr"], st(1)["status"]),
              ("CLOSED", None, "BATCH-READY") if closed else ("OPEN", 900, "BATCH-READY"))

with section("verify refuses"):
    w = World(); w.pr(1, {"f1.py": "1\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "SUCCESS"
    real_verify = merge_flow.verify
    merge_flow.verify = lambda *a: ["not the approved heads"]
    pc.batch_step(ARGS)
    merge_flow.verify = real_verify
    check("verify problems: nothing lands", (w.lands, st(1)["status"]), ([], "BATCH-READY"))

with section("red of two splits"):
    w = World(); h1, h2 = w.pr(1, {"f1.py": "1\n"}), w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "FAILURE"
    pc.batch_step(ARGS)
    check("red 2: closed", (w.prs[900]["state"], w.remote_batches()), ("CLOSED", []))
    check("red 2: two queue groups", (w.batch()["pr"], w.batch()["queue"]), (None, [[1], [2]]))
    check("red 2: no chain runs", w.chains, [])
    check("red 2: ledger batch-split", [(r["batch"], r["prs"]) for r in ledger_rows("batch-split")], [(900, [1, 2])])
    pc.batch_step(ARGS)
    check("red 2: the next batch holds the first group only", (w.batch()["heads"], w.batch()["queue"]), ({"1": h1}, [[2]]))

with section("red of one"):
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "FAILURE"
    pc.batch_step(ARGS)
    check("red 1: closed, chain runs once", (w.prs[900]["state"], [c["pr"] for c in w.chains]), ("CLOSED", [1]))
    seed = w.chains[0]["seed"]
    text = seed.read_text()
    check("red 1: seed name", bool(re.fullmatch(r"batch-red-\d+\.md", seed.name)) and seed.parent == pc.WORK / "prs" / "1", True)
    check("red 1: seed head", text.splitlines()[:3], ["FAIL", f"head {h1}", "Findings:"])
    check("red 1: seed finding", (text.splitlines()[3].startswith("- P1 batch-ci:0 - "), REQUIRED[0] in text, "/actions/runs/7900/" in text, "merge" in text),
          (True, True, True, True))
    check("red 1: no new batch in the same lap", (w.batch()["pr"], len([c for c in w.writes() if c[:2] == ("pr", "create")])), (None, 1))

with section("main moved"):
    w = World(); w.pr(1, {"f1.py": "1\n"})
    pc.batch_step(ARGS)
    main = w.move_main({"m.py": "m\n"})
    pc.batch_step(ARGS)
    check("main moved: the batch PR is closed with its branch and a reason",
          [("--delete-branch" in c, "main moved" in c[c.index("--comment") + 1]) for c in w.writes() if c[:2] == ("pr", "close")], [(True, True)])
    check("main moved: planned again on the new main", (w.batch()["pr"], w.batch()["base"]), (901, main))
    check("main moved: ledger batch-close", [r["batch"] for r in ledger_rows("batch-close")], [900])
    check("main moved: the PR was not folded or rerun", w.chains, [])

with section("pushed head"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.push(1, {"f1.py": "new\n"})
    pc.batch_step(ARGS)
    check("pushed head: batch closed", w.prs[900]["state"], "CLOSED")
    check("pushed head: that PR's chain runs from the top", [(c["pr"], c["seed"], c["approved"], c["from_gate"]) for c in w.chains],
          [(1, None, None, False)])
    check("pushed head: the others go in a new batch", w.batch()["heads"], {"2": h2})

with section("left the batch"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    pc.set_state(1, status="RESTING")
    pc.batch_step(ARGS)
    check("a PR no longer BATCH-READY closes the batch", (w.prs[900]["state"], list(w.batch()["heads"]), st(1).get("batch")), ("CLOSED", ["2"], None))
    w.prs[2]["state"] = "CLOSED"
    pc.batch_step(ARGS)
    check("a closed PR ends CLOSED and closes the batch", (st(2)["status"], w.prs[901]["state"], w.batch()["pr"]), ("CLOSED", "CLOSED", None))

with section("member retargeted off main"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"
    w.prs[1]["base"] = "release"  # the same head, now aimed at another base
    pc.batch_step(ARGS)
    reasons = [c[c.index("--comment") + 1] for c in w.writes() if c[:2] == ("pr", "close")]
    check("retarget: nothing lands", w.lands, [])
    check("retarget: the batch PR closes with a one-line reason that names #1",
          (w.prs[900]["state"], [("\n" not in r, "#1" in r) for r in reasons]), ("CLOSED", [(True, True)]))
    check("retarget: ledger batch-close", [r["batch"] for r in ledger_rows("batch-close")], [900])
    check("retarget: #1 leaves the batch; #2 goes in the next one and #1 stays held",
          (st(1).get("batch"), w.batch().get("pr"), w.batch().get("heads"), w.batch().get("held")), (None, 901, {"2": h2}, [1]))

with section("batch PR retargeted off main"):
    w = World(); h1, h2 = w.pr(1, {"f1.py": "1\n"}), w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"
    w.prs[900]["base"] = "release"  # the batch PR itself is aimed at another base; its checks are green
    pc.batch_step(ARGS)
    reasons = [c[c.index("--comment") + 1] for c in w.writes() if c[:2] == ("pr", "close")]
    check("batch retarget: nothing lands", w.lands, [])
    check("batch retarget: the batch PR closes with a one-line reason that names the retarget",
          (w.prs[900]["state"], reasons), ("CLOSED", [f"Closed: the batch PR was retargeted off {pc.CFG.branch}; planning again."]))
    check("batch retarget: ledger batch-close", [r["batch"] for r in ledger_rows("batch-close")], [900])
    check("batch retarget: both members stay BATCH-READY", (st(1)["status"], st(2)["status"]), ("BATCH-READY", "BATCH-READY"))
    check("batch retarget: the members are planned again", (w.batch().get("pr"), w.batch().get("heads")), (901, {"1": h1, "2": h2}))

with section("changes requested in an open batch"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"
    w.prs[1]["review"] = "CHANGES_REQUESTED"
    pc.batch_step(ARGS)
    reasons = [c[c.index("--comment") + 1] for c in w.writes() if c[:2] == ("pr", "close")]
    check("changes requested: nothing lands", w.lands, [])
    check("changes requested: the batch PR closes with a one-line reason that names #1",
          (w.prs[900]["state"], [("\n" not in r, "#1" in r) for r in reasons]), ("CLOSED", [(True, True)]))
    check("changes requested: #1 ends NEEDS-PERSON with the reason", (st(1)["status"], st(1).get("why", "")),
          ("NEEDS-PERSON", "approval withdrawn after BATCH-READY: changes requested; a person decides"))
    check("changes requested: #2 is planned again alone", (w.batch().get("pr"), w.batch().get("heads")), (901, {"2": h2}))

with section("draft in an open batch"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"
    w.prs[1]["draft"] = True
    pc.batch_step(ARGS)
    check("draft: nothing lands", w.lands, [])
    check("draft: the batch PR closes, #1 ends NEEDS-PERSON, #2 is planned again",
          (w.prs[900]["state"], st(1)["status"], w.batch().get("pr"), w.batch().get("heads")), ("CLOSED", "NEEDS-PERSON", 901, {"2": h2}))

with section("approval dismissed, no batch"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
    w.prs[1]["review"] = "REVIEW_REQUIRED"
    pc.batch_step(ARGS)
    check("approval dismissed: #1 is not planned, #2 is", (w.batch().get("heads"), st(1)["status"]), ({"2": h2}, "NEEDS-PERSON"))
    check("approval dismissed: #1's reason says so", "approval dismissed" in st(1).get("why", ""), True)

with section("no review required"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    w.prs[1]["review"] = w.prs[2]["review"] = ""
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"
    pc.batch_step(ARGS)
    check("no review required: the batch lands and both PRs end MERGED",
          ([p for p, _, _ in w.lands], st(1)["status"], st(2)["status"]), ([900], "MERGED", "MERGED"))

with section("withdrawn approval, dry run"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"; w.prs[1]["review"] = "CHANGES_REQUESTED"
    w.calls.clear(); said = []
    pc.DRY, pc.log = True, said.append
    pc.batch_step(ARGS)
    pc.DRY, pc.log = False, lambda m: None
    check("withdrawn dry: writes nothing, lands nothing, ends nothing",
          (w.writes(), w.lands, st(1)["status"], w.prs[900]["state"]), ([], [], "BATCH-READY", "OPEN"))
    check("withdrawn dry: logs the NEEDS-PERSON it would end", any("[dry] #1 would end NEEDS-PERSON" in m for m in said), True)
    check("withdrawn dry: the plan leaves #1 out", any("lands [2]" in m for m in said), True)

with section("pair clash"):
    w = World(); h1 = w.pr(1, {"a.py": "one\n"}); w.pr(2, {"a.py": "two\n"})
    pc.batch_step(ARGS)
    b = w.batch()
    check("pair clash: the later PR is held", (b["heads"], b["held"], st(2)["status"], w.chains), ({"1": h1}, [2], "BATCH-READY", []))

with section("main clash and old base"):
    w = World(); w.pr(1, {"a.py": "pr\n"}); h2 = w.pr(2, {"f2.py": "2\n"}); w.move_main({"a.py": "main\n"})
    pc.batch_step(ARGS)
    check("clash with main goes through the chain", ([c["pr"] for c in w.chains], w.batch()["heads"]), ([1], {"2": h2}))
    w = World(base_age_hours=72); w.pr(1, {"f1.py": "1\n"}); w.move_main({"m.py": "m\n"})
    pc.batch_step(ARGS)
    check("an old base goes through the chain, no batch", ([c["pr"] for c in w.chains], w.batch()["pr"], w.writes()), ([1], None, []))

with section("already on base and stacked"):
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"}); w.prs[2]["base"] = "pr1"
    w.g("push", "-q", "origin", f"{h1}:main")
    pc.batch_step(ARGS)
    check("already on base: ends MERGED", st(1)["status"], "MERGED")
    check("a stacked PR never enters a batch", (w.batch()["pr"], w.batch()["held"]), (None, [2]))

with section("dry run"):
    for stage in ("plan", "green", "red", "main moved", "pushed"):
        w = World(); w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
        if stage != "plan":
            pc.batch_step(ARGS)
        if stage in ("green", "red"):
            w.prs[900]["checks"] = "SUCCESS" if stage == "green" else "FAILURE"
        if stage == "main moved":
            w.move_main({"m.py": "m\n"})
        if stage == "pushed":
            w.push(1, {"f1.py": "new\n"})
        snap = lambda: {str(f.relative_to(pc.WORK)): f.read_text() for f in pc.WORK.rglob("*") if f.is_file()}
        before, branches = snap(), w.remote_batches()
        w.calls.clear(); w.lands.clear(); w.chains.clear()
        said = []
        pc.DRY, pc.log = True, said.append
        pc.batch_step(ARGS)
        check(f"dry ({stage}): writes nothing", (w.writes(), w.lands, w.chains, snap() == before, w.remote_batches() == branches),
              ([], [], [], True, True))
        check(f"dry ({stage}): logs its decisions", any("[dry]" in m for m in said), True)

# ---------------------------------------------------------------- parallel review findings (A to K)
class Killed(BaseException):
    """The process dies: no `except Exception` runs."""


def die_after(w, verb):
    """The next `gh pr <verb>` reaches GitHub, then the process dies before anything is written locally."""
    def sh(*c, **k):
        r = w.sh(*c, **k)
        if c[:3] == ("gh", "pr", verb):
            pc.sh = w.sh
            raise Killed
        return r
    pc.sh = sh


def lap_that_dies():
    try:
        pc.batch_step(ARGS)
    except Killed:
        return True
    return False


def creates(w):
    return len([c for c in w.writes() if c[:2] == ("pr", "create")])


with section("A: fixer and main"):
    def fixed_on(verdict_name, files={"f1.py": "1\n"}, mode="batch"):
        """True when the fixer's worktree starts with main merged in."""
        w = World(); head = w.pr(1, files); main = w.move_main({"m.py": "m\n", "a.py": "main\n"})
        pc.CFG.land_mode = mode
        pc.issues_text = lambda info: ""
        seen = []
        def agent(role, model, brief, wt, d, pr, **kw):
            seen.append(pc.sh("git", "-C", str(wt), "merge-base", "--is-ancestor", main, "HEAD").returncode == 0
                        or bool(pc.sh("git", "-C", str(wt), "diff", "--name-only", "--diff-filter=U").stdout.strip()))
            return {}
        pc.run_agent = agent
        vp = tmp / verdict_name; vp.write_text(f"FAIL\nhead {head}\nFindings:\n- P1 x:1 - y\n")
        pc.fix(1, 1, "fix", vp, head, None)
        return seen
    check("A: the fixer merges main only for a conflict, a batch-red seed or pr mode (not a clean recent head in batch mode)",
          (fixed_on("verdict.md"), fixed_on("batch-red-1.md"), fixed_on("verdict.md", {"a.py": "pr\n"}), fixed_on("verdict.md", mode="pr")),
          ([False], [True], [True], [True]))

with section("B: config values"):
    def refused(land):
        try:
            cfg({"land": land})
        except SystemExit as e:
            return next(iter(land)) in str(e)
        except Exception:  # noqa: BLE001 - not the clear error
            return False
        return False
    for bad in (-1, float("nan"), float("inf"), "soon"):
        check(f"B: max_base_age_hours = {bad!r} is refused by name", refused({"max_base_age_hours": bad}), True)
    for bad in ("bad prefix", "../outside", "x..y"):
        check(f"B: batch_branch_prefix = {bad!r} is refused by name", refused({"batch_branch_prefix": bad}), True)

with section("C: killed after pr create"):
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"})
    die_after(w, "create"); died = lap_that_dies()
    pc.batch_step(ARGS)
    b = w.batch() or {}
    check("C: the open batch PR is adopted, no second one",
          (died, creates(w), b.get("pr"), b.get("heads"), b.get("pending"), st(1).get("batch")),
          (True, 1, 900, {"1": h1}, None, 900))
    w.prs[900]["checks"] = "SUCCESS"; pc.batch_step(ARGS)
    check("C: the adopted batch lands", ([p for p, _, _ in w.lands], st(1)["status"]), ([900], "MERGED"))
    w = World(); w.pr(1, {"f1.py": "1\n"})
    die_after(w, "create"); lap_that_dies()
    w.prs[900]["head"] = w.base  # the open PR no longer holds the planned commit
    pc.batch_step(ARGS)
    check("C: a stray PR at another head is closed and a new one planned",
          (w.prs[900]["state"], creates(w), (w.batch() or {}).get("pr")), ("CLOSED", 2, 901))
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_save(dict(pr=None, queue=[[1], [2]], held=[]))
    seen = []
    def no_create(*c, **k):
        if c[:3] == ("gh", "pr", "create"):
            seen.append(w.batch())
            raise Killed
        return w.sh(*c, **k)
    pc.sh = no_create; lap_that_dies(); pc.sh = w.sh
    check("C: the intent is on disk before the PR is created",
          [(s.get("pr"), s.get("pending"), s.get("heads"), bool(s.get("branch") and s.get("commit") and s.get("base") and s.get("opened"))) for s in seen],
          [(None, True, {"1": h1}, True)])
    pc.batch_step(ARGS)
    check("C: with no PR the intent is dropped and the same queued group is planned",
          ((w.batch() or {}).get("heads"), (w.batch() or {}).get("queue"), (w.batch() or {}).get("pending")), ({"1": h1}, [[2]], None))

with section("D: killed after closing a red batch of two"):
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "FAILURE"
    die_after(w, "close"); died = lap_that_dies()
    pc.batch_step(ARGS)
    check("D: the split queue survives", (died, (w.batch() or {}).get("heads"), (w.batch() or {}).get("queue")), (True, {"1": h1}, [[2]]))

with section("D: killed after closing a red batch of one"):
    w = World(); w.pr(1, {"f1.py": "1\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "FAILURE"
    die_after(w, "close"); died = lap_that_dies()
    pc.batch_step(ARGS)
    check("D: the fixer seed survives and runs, no new batch",
          (died, [(c["pr"], bool(c["seed"] and c["seed"].exists())) for c in w.chains], creates(w)), (True, [(1, True)], 1))
    w.chains.clear(); pc.set_state(1, status="RESTING"); pc.batch_step(ARGS)
    check("D: the seed is consumed once", (w.chains, (w.batch() or {}).get("fix")), ([], None))

with section("E: a member's check turned red"):
    for result in ("FAILURE", "CANCELLED"):
        w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
        pc.batch_step(ARGS)
        w.prs[900]["checks"] = "SUCCESS"; w.prs[1]["checks"] = result
        pc.batch_step(ARGS)
        check(f"E ({result}): nothing lands, the batch closes, #1 runs its chain, #2 is planned again",
              (w.lands, w.prs[900]["state"], [(c["pr"], c["seed"]) for c in w.chains], (w.batch() or {}).get("heads")),
              ([], "CLOSED", [(1, None)], {"2": h2}))

with section("F: labels"):
    for label in ("merge/hold", "hold", "do-not-merge", "status/blocked", "wontfix"):
        w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
        pc.batch_step(ARGS)
        w.prs[900]["checks"] = "SUCCESS"; w.prs[1]["labels"] = [label]
        pc.batch_step(ARGS)
        check(f"F ({label}): #1 is withdrawn and the batch closes",
              (w.lands, w.prs[900]["state"], st(1)["status"], (w.batch() or {}).get("heads")), ([], "CLOSED", "NEEDS-PERSON", {"2": h2}))
    got = []
    for labels in ([], [pc.CFG.human_label]):
        w = World(); w.pr(1, {"f1.py": "1\n"}); w.prs[1]["labels"] = [pc.CFG.human_label]
        pc.set_state(1, gate_at="2026-01-01T00:00:00Z", gate_comment=1)  # it passed the human gate
        pc.batch_step(ARGS)
        w.prs[900]["checks"] = "SUCCESS"; w.prs[1]["labels"] = labels
        pc.batch_step(ARGS)
        got.append(([p for p, _, _ in w.lands], st(1)["status"]))
    check("F: after the human gate a removed label withdraws, a kept one lands", got, [([], "NEEDS-PERSON"), ([900], "MERGED")])

with section("G: draft batch PR"):
    w = World(); w.pr(1, {"f1.py": "1\n"})
    pc.batch_step(ARGS)
    w.prs[900]["checks"] = "SUCCESS"; w.prs[900]["draft"] = True
    said = []; pc.log = said.append
    pc.batch_step(ARGS); w.pr(2, {"f2.py": "2\n"}); pc.batch_step(ARGS)
    check("G: a draft batch is never landed, readied, closed or replaced",
          (w.lands, [c[:2] for c in w.writes()], w.prs[900]["state"], (w.batch() or {}).get("pr")), ([], [("pr", "create")], "OPEN", 900))
    check("G: logged once", len([m for m in said if "draft" in m]), 1)

with section("H: a push while the batch lands"):
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"}); w.pr(2, {"f2.py": "2\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "SUCCESS"
    def land(pr, head, reviewer, approve_only=False):
        w.push(1, {"f1.py": "late\n"})
        return w.land(pr, head, reviewer)
    pc.land = land
    pc.batch_step(ARGS)
    check("H: the pushed member stays BATCH-READY, the other ends MERGED", (st(1)["status"], st(2)["status"]), ("BATCH-READY", "MERGED"))

with section("K: cap"):
    w = World()
    for n in range(1, 23):
        w.pr(n, {f"f{n}.py": f"{n}\n"})
    pc.batch_step(ARGS)
    b = w.batch()
    check("K: one plan holds 20 PRs in rank order, the rest wait",
          (len(b["heads"]), b["held"], w.chains, st(21)["status"], st(22)["status"],
           sorted(map(int, b["heads"])) == sorted([r["number"] for r in pc.rank([p for p in pc.list_prs() if p["number"] < 900])][:20])),
          (20, [], [], "BATCH-READY", "BATCH-READY", True))

with section("L: excluded while it waits"):
    for line in ("1 operator hold", "branch:pr1 owned by someone else"):
        w = World(); w.pr(1, {"f1.py": "1\n"}); h2 = w.pr(2, {"f2.py": "2\n"})
        pc.batch_step(ARGS)
        w.prs[900]["checks"] = "SUCCESS"; pc.EXCLUDE.write_text(line + "\n")
        pc.batch_step(ARGS)
        check(f"L ({line.split()[0]}): the open batch closes, nothing lands, #2 is planned without #1",
              (w.lands, w.prs[900]["state"], st(1)["status"], (w.batch() or {}).get("heads"), w.chains),
              ([], "CLOSED", "BATCH-READY", {"2": h2}, []))
        w.prs[901]["checks"] = "SUCCESS"; pc.batch_step(ARGS); w.push(1, {"f1.py": "new\n"}); pc.batch_step(ARGS)
        check(f"L ({line.split()[0]}): skipped each lap, even after a push",
              ([p for p, _, _ in w.lands], st(1)["status"], (w.batch() or {}).get("heads"), w.chains),
              ([901], "BATCH-READY", None, []))
    w = World(); h1 = w.pr(1, {"f1.py": "1\n"}); pc.EXCLUDE.write_text("1 operator hold\n")
    pc.batch_step(ARGS)
    held = (creates(w), st(1)["status"])
    pc.EXCLUDE.write_text(""); pc.batch_step(ARGS)
    check("L: planned again once the exclusion is removed", (held, (w.batch() or {}).get("heads")), ((0, "BATCH-READY"), {"1": h1}))
    w = World(); w.pr(1, {"f1.py": "1\n"})
    pc.batch_step(ARGS); w.prs[900]["checks"] = "FAILURE"
    die_after(w, "close"); lap_that_dies()
    pc.EXCLUDE.write_text("1 operator hold\n"); pc.batch_step(ARGS)
    held = (w.chains[:], creates(w))
    pc.EXCLUDE.write_text(""); pc.batch_step(ARGS)
    check("L: an owed fixer seed waits out the exclusion",
          (held, [(c["pr"], bool(c["seed"])) for c in w.chains]), (([], 1), [(1, True)]))

# ---------------------------------------------------------------- resume and doctor
with section("resume"):
    w = World(); w.pr(1, {"f1.py": "1\n"})
    check("resume: batch mode runs the batch step", (pc.resume_all(ARGS), w.batch()["pr"]), ([], 900))
    w = World(); w.pr(1, {"f1.py": "1\n"}); pc.STOPFILE.write_text("stop")
    pc.resume_all(ARGS)
    check("resume: STOP file, no batch step", (w.batch(), w.writes()), (None, []))
    w = World(); w.pr(1, {"f1.py": "1\n"}); pc.CFG.land_mode = "pr"
    pc.resume_all(ARGS)
    check("rollback: pr mode lands a BATCH-READY PR through the chain", ([(c["pr"], bool(c["approved"])) for c in w.chains], w.batch()), ([(1, True)], None))

with section("doctor"):
    w = World(); w.pr(1, {"f1.py": "1\n"}); w.prs[1]["state"] = "MERGED"
    pc.run_janitor = lambda: None; pc.loop_alive = lambda *a: True
    pc.doctor()
    check("doctor leaves a merged BATCH-READY PR to resume", st(1)["status"], "BATCH-READY")

# ---------------------------------------------------------------- land_pr.py --approve-only, against a fake gh on PATH
with section("land_pr.py"):
    fake = tmp / "fakegh"; fake.mkdir()
    (fake / "gh").write_text(f"""#!{sys.executable}
import json, os, sys
d = os.environ["FAKE_GH"]; a = sys.argv[1:]
w = json.load(open(d + "/world.json"))
open(d + "/calls.jsonl", "a").write(json.dumps(a) + "\\n")
if a[:2] == ["auth", "token"]: print("tok")
elif a[:2] == ["pr", "view"]: print(json.dumps(w["pr"]))
elif a[0] == "api" and any(x.endswith("/reviews") for x in a): print(w["reviews"])
""")
    (fake / "gh").chmod(0o755)
    head = "a" * 40
    def land_pr(status="BEHIND", reviews="", flag=True, check_result="SUCCESS"):
        pr = dict(state="OPEN", isDraft=True, author={"login": "owner"}, headRefOid=head, baseRefOid="b" * 40, mergeStateStatus=status,
                  reviewDecision="", statusCheckRollup=[dict(name=c, conclusion=check_result) for c in REQUIRED], mergedBy=None, mergeCommit=None)
        (fake / "world.json").write_text(json.dumps(dict(pr=pr, reviews=reviews)))
        (fake / "calls.jsonl").write_text("")
        env = {**os.environ, "PATH": str(fake) + os.pathsep + os.environ["PATH"], "FAKE_GH": str(fake), "KIT_REPO": REPO,
               "KIT_MERGER": "bot", "KIT_CHECKS": ",".join(REQUIRED), "KIT_WORK": str(tmp / "landwork")}
        p = subprocess.run([sys.executable, "-B", str(root / "agent-kit/land/land_pr.py"), "1", head, "bot", head, "rev",
                            *(["--approve-only"] if flag else [])], capture_output=True, text=True, env=env, timeout=60)
        calls = [json.loads(x) for x in (fake / "calls.jsonl").read_text().splitlines()]
        did = [("status" if any("/statuses/" in x for x in c) else c[1]) for c in calls
               if c[:1] == ["pr"] and c[1] in ("review", "ready", "merge") or any("/statuses/" in x for x in c)]
        return p.returncode, did, p.stdout + p.stderr
    for status in ("BEHIND", "DIRTY"):
        rc, did, out = land_pr(status)
        check(f"approve-only ({status}): status, approval and ready (the sequence a live batch landed with), no merge", (rc, did), (0, ["status", "review", "ready"]))
    rc, did, out = land_pr(reviews=f"bot {head}")
    check("approve-only: this head already approved -> no second approval", (rc, did), (0, ["status", "ready"]))
    rc, did, out = land_pr(reviews=f"bot {'c' * 40}\nsomeone {head}")
    check("approve-only: another head or account does not count", (rc, did), (0, ["status", "review", "ready"]))
    rc, did, out = land_pr(check_result="FAILURE")
    check("approve-only: a red check still stops", (rc != 0, did), (True, []))
    rc, did, out = land_pr(flag=False)
    check("without the flag BEHIND still stops before any write", (rc != 0, did, "is BEHIND" in out), (True, [], True))

print("\nFAILED: " + ", ".join(fails) if fails else "\nall batch landing checks passed")
sys.exit(1 if fails else 0)
