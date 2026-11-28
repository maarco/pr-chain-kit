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
        return dict(number=n, state=p["state"], title=f"pr {n}", body=f"Fixes #{n}00", isDraft=True, headRefName=p["branch"],
                    headRefOid=p["head"], mergeStateStatus="BEHIND", author={"login": "owner"}, isCrossRepository=False,
                    closingIssuesReferences=[{"number": n * 100}], files=[{"path": "x.py"}], baseRefName=p["base"],
                    statusCheckRollup=rollup, additions=1, deletions=0, labels=[], reviewDecision="APPROVED")

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
            return ok_proc(json.dumps([self.view(n) for n, p in self.prs.items() if p["state"] == "OPEN"]))
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
    for n in (1, 2, 900):
        w.prs[n]["state"] = "MERGED"
    pc.batch_step(ARGS)
    check("batch merged between laps: PRs end MERGED", (st(1)["why"], st(2)["status"], w.batch()["pr"], w.lands),
          ("landed in merge-flow batch #900", "MERGED", None, []))

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
        check(f"approve-only ({status}): status and approval, no ready, no merge", (rc, did), (0, ["status", "review"]))
    rc, did, out = land_pr(reviews=f"bot {head}")
    check("approve-only: this head already approved -> no second approval", (rc, did), (0, ["status"]))
    rc, did, out = land_pr(reviews=f"bot {'c' * 40}\nsomeone {head}")
    check("approve-only: another head or account does not count", (rc, did), (0, ["status", "review"]))
    rc, did, out = land_pr(check_result="FAILURE")
    check("approve-only: a red check still stops", (rc != 0, did), (True, []))
    rc, did, out = land_pr(flag=False)
    check("without the flag BEHIND still stops before any write", (rc != 0, did, "is BEHIND" in out), (True, [], True))

print("\nFAILED: " + ", ".join(fails) if fails else "\nall batch landing checks passed")
sys.exit(1 if fails else 0)
