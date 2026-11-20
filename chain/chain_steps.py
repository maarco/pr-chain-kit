"""pr-chain steps: one function per `do` in flow.json, each a slice of the old run_chain. A step does its work through
ctx.pc (bin/pr-chain's own helpers, read live so test stubs apply) and returns one outcome word; flow.json says where
that outcome goes. A step that ends the run sets ctx.why (the reason die() records) and returns the outcome that
flow.json routes to an end state."""
import json
import re
from pathlib import Path


def init(ctx):
    ctx.prev, ctx.info, ctx.st0, ctx.ui, ctx.first_head, ctx.rested = {}, None, {}, False, None, 0
    ctx.head = ctx.vp = ctx.verdict = ctx.fixed_in = ctx.prev_key = ctx.red_bad = ctx.carry_old = None
    ctx.src, ctx.extra, ctx.class_note = "", "", ""
    ctx.carried = ctx.covered = ctx.need_ui = False
    ctx.ui_vp = ctx.audited = ctx.uv = ctx.uvp = ctx.uhead = ctx.ui_tag = None
    ctx.reviewer = ctx.shot_head = ctx.cur = ctx.how = ctx.gp = None


def _head(ctx):
    return ctx.pc.branch_head(ctx.pc.pr_info(ctx.pr))


def _model(ctx, args):
    return getattr(ctx.a, f"{args.get('role', 'fix')}_model")


def _rnd(ctx):
    return ctx.counts["rounds"]


def _verdict(ctx):
    """What every verdict did in the old loop: log it, stop a dry run, record round/head/verdict. True = dry run ends."""
    pc = ctx.pc
    pc.log(f"#{ctx.pr} round {_rnd(ctx)} {ctx.src}: {ctx.verdict} at {ctx.head[:8] if ctx.head else '?'}")
    if pc.DRY:
        pc.log("[dry] stopping after the review launch line; no fixer, audit or land in a dry run")
        pc.release(ctx.pr)
        return True
    pc.set_state(ctx.pr, round=_rnd(ctx), head=ctx.head, last_verdict=ctx.verdict)
    return False


def _route(ctx):
    if ctx.verdict == "BEHIND":
        ctx.pc.ledger(pr=ctx.pr, stage="drift", where=f"review {_rnd(ctx)}", head=ctx.head)
        return "behind"
    if ctx.verdict is None:
        ctx.why = "reviewer wrote no PASS/FAIL line; see " + str(ctx.pc.WORK / "prs" / str(ctx.pr))
        return "no_verdict"
    return "pass" if ctx.verdict == "PASS" else "fail"


# ---------------------------------------------------------------- start of a run
def noop(ctx, args):
    return "ok"


def stop_file(ctx, args):
    pc = ctx.pc
    if pc.STOPFILE.exists():
        ctx.why = "STOP file" if _rnd(ctx) else f"STOP file present: {pc.STOPFILE.read_text().strip()[:100]}"
        return "stop"
    return "go"


def claim(ctx, args):
    pc = ctx.pc
    if not pc.claim(ctx.pr):  # another live chain owns it: touch nothing (die() would release its claim and overwrite its state)
        pc.log(f"#{ctx.pr} CLAIMED: another chain holds this PR; leaving it alone")
        pc.ledger(pr=ctx.pr, stage="claimed-skip")
        return "claimed"
    return "ok"


def start_run(ctx, args):
    pc, sp = ctx.pc, ctx.pc.state_path(ctx.pr)
    ctx.prev = json.loads(sp.read_text()) if sp.exists() else {}  # how the last run ended
    if not pc.DRY:
        pc.set_state(ctx.pr, status="running", started=pc.now(), why="", last_verdict="", ci_wait="", ci_settled=False)
        pc.chain_label(ctx.pr, None)  # the parked state clears while a chain runs; die() labels it again if it parks
    ctx.info = pc.pr_info(ctx.pr)
    return "ok"


def pr_open(ctx, args):
    pc, info = ctx.pc, ctx.info
    if info["state"] != "OPEN":
        ctx.why = f"PR is {info['state']}"
        return "closed"
    if info["author"]["login"] not in pc.OUR_AUTHORS:
        ctx.why = "outside contributor PR: the chain only works on PRs opened by our own accounts"
        return "not_ours"
    ctx.ui = any(pc.UI_RE.search(f["path"]) for f in info["files"])
    ctx.first_head = pc.branch_head(info)
    sp = pc.state_path(ctx.pr)
    ctx.st0 = json.loads(sp.read_text()) if sp.exists() else {}
    ctx.rested = ctx.st0.get("rest_total") or 0
    return "ok"


def lift_or_release(ctx, args):
    pc, pr, prev, fh = ctx.pc, ctx.pr, ctx.prev, ctx.first_head
    if not pc.DRY and prev.get("chain_label") == pc.NEEDS_PERSON_LABEL:
        # owner_reason hands this PR out only after a person removed chain/needs-person (or a retry cap's head moved): start over
        fresh = (dict(retries={}, rest_total=0, rests={}, redesigns=0, stuck_lifts=0, stuck_head="")
                 if prev.get("status") == "NEEDS-PERSON" else dict(retries={}))
        pc.set_state(pr, **fresh)
        ctx.st0 = dict(ctx.st0, **fresh)
        ctx.rested = ctx.st0.get("rest_total") or 0
        pc.ledger(pr=pr, stage="needs-person-released", head=fh)
    elif not pc.DRY and prev.get("status") == "STUCK" and (prev.get("stuck_head") or prev.get("head")) not in (None, "", fh):
        pc.fetch(ctx.info)  # a STUCK from before 11-16 has no stuck_head: its last head stands in
        if not pc.main_only_move(prev.get("stuck_head") or prev["head"], fh):  # a real push, not only a merge of main
            lifts = (prev.get("stuck_lifts") or 0) + 1
            if lifts > pc.LIM["stuck_lifts_max"]:
                pc.set_state(pr, head=fh)
                ctx.why = (f"a new push lifted STUCK {lifts - 1} times and it came back each time; "
                           f"remove the {pc.NEEDS_PERSON_LABEL} label to let the chain try again")
                return "needs_person"
            pc.set_state(pr, rest_total=0, rests={}, redesigns=0, stuck_lifts=lifts, stuck_head="")
            ctx.st0, ctx.rested = dict(ctx.st0, rest_total=0, rests={}, redesigns=0), 0
            pc.log(f"#{pr} new push {fh[:8]} lifts STUCK ({lifts} of {pc.LIM['stuck_lifts_max']})")
            pc.ledger(pr=pr, stage="stuck-lifted", head=fh, n=lifts)
    return "ok"


def mode(ctx, args):
    ctx.need_ui = ctx.ui and not ctx.a.vision_ok  # a UI PR gets the vision review; --vision-ok says a person already did it
    ctx.head = ctx.first_head
    if ctx.approved:  # an approval skips all review
        ctx.head, ctx.need_ui = ctx.approved["gate_head"], False
        return "approved"
    return "land" if ctx.from_gate else "run"  # `land` skips the review loop and goes straight to the audit


def standing_approval(ctx, args):
    pc = ctx.pc
    if ctx.seed is None and not pc.DRY and ctx.ui and ctx.st0.get("gate_head") and ctx.st0.get("gate_at"):
        ap = pc.standing_approval(ctx.pr, ctx.info, ctx.st0, ctx.first_head)
        if ap:
            ctx.approved, ctx.head, ctx.need_ui = ap, ap["gate_head"], False
            return "approved"
    return "none"


def rest_check(ctx, args):
    pc = ctx.pc
    if ctx.rested < pc.REST_STOP:
        return "below"
    return "redesign" if (ctx.st0.get("redesigns") or 0) < pc.REDESIGN_TRIES else "stuck"


def agent_redesign(ctx, args):
    # whack-a-mole that the class note did not end: one strong agent redesigns the whole class before the PR is called STUCK
    pc, pr = ctx.pc, ctx.pr
    tries = (ctx.st0.get("redesigns") or 0) + 1
    pc.set_state(pr, redesigns=tries)
    hv = pc.WORK / "prs" / str(pr) / f"rd{tries}-history.md"
    hv.write_text(f"FAIL\nhead {ctx.first_head}\nFindings:\n" + "\n".join(pc.finding_history(pr, 25)) + "\n")
    pc.log(f"#{pr} rested {ctx.rested} times: redesign {tries} of {pc.REDESIGN_TRIES} ({pc.REDESIGN_MODEL})")
    model = pc.resolve_cmd(pc.REDESIGN_MODEL) if not pc.DRY else pc.REDESIGN_MODEL
    new_head, note = pc.fix(pr, f"rd{tries}", model, hv, ctx.first_head, None, prompt="redesign.md")
    pc.ledger(pr=pr, stage="redesign", n=tries, head=new_head, note=(note or "")[:160])
    if new_head and new_head != "DISPUTE":
        pc.set_state(pr, rest_total=0, rests={}, head=new_head)
        ctx.rested, ctx.first_head, ctx.head = 0, new_head, new_head
        return "landed"
    ctx.rested = pc.REST_STOP
    return "failed"


def stuck_report(ctx, args):
    pc, pr, fh = ctx.pc, ctx.pr, ctx.first_head
    hist = pc.finding_history(pr)
    if not ctx.st0.get("stuck_issue") and hist and not pc.DRY:
        c = pc.sh("gh", "issue", "create", "-R", pc.REPO, "--label", "bug", "--title",
                  f"Design needed: #{pr} keeps failing review in the same area"[:120],
                  "--body", pc.scrub(f"#{pr} came back {ctx.rested} times. Each fix closed one case and the next review found another, "
                                     "so the approach itself needs a decision (for example an allowlist instead of blocking cases one "
                                     "by one).\n\nFindings so far:\n" + "\n".join(hist)))
        m = re.search(r"/issues/(\d+)", c.stdout)
        if m:
            pc.set_state(pr, stuck_issue=int(m.group(1)))
    if not pc.DRY:  # parked at this head (owner_reason); a real new push lifts it at the top of the next run
        pc.set_state(pr, stuck_head=fh, head=fh)
        iss = json.loads(pc.state_path(pr).read_text()).get("stuck_issue")
        pc.sh("gh", "pr", "comment", str(pr), "-R", pc.REPO, "--body", pc.scrub(
            f"{pc.MARK}stuck head={fh} -->\nThe chain stopped on this PR: it came back {ctx.rested} times with new findings in "
            "the same area" + (f", so the approach needs a decision (#{iss})" if iss else "") + ". A new push that is more than a "
            f"merge of main starts the chain again. After {pc.LIM['stuck_lifts_max']} such restarts it waits for a person."))
    ctx.why = f"rested {ctx.rested} times with new findings in the same area; design issue filed; a new push restarts it"
    return "done"


def ui_seed(ctx, args):
    st0 = ctx.st0
    if ctx.seed is None and st0.get("ui_seed"):
        if st0.get("head") == ctx.first_head and Path(st0["ui_seed"]).exists():
            ctx.seed = Path(st0["ui_seed"])  # a failed final screenshot round: its findings go to the fixer first
        ctx.pc.set_state(ctx.pr, ui_seed="")
    return "ok"


def round_setup(ctx, args):
    p = ctx.prev  # a run that parked for CI before its review: its round and last findings go on (replay #1244 11-17)
    if p.get("status") == "WAITING-CI" and p.get("ci_wait") == "review" and p.get("ci_wait_round"):
        ctx.counts["rounds"], ctx.prev_key = p["ci_wait_round"] - 1, p.get("ci_wait_key")
    hist = ctx.pc.finding_history(ctx.pr) if ctx.rested >= 2 else []
    if hist:  # whack-a-mole guard: every review in this run sees every earlier finding as one class
        ctx.class_note = (f"This PR has come back {ctx.rested} times; each fix closed one case and the next review found another. "
                          "Earlier findings:\n" + "\n".join(hist) + "\nTreat them as ONE class of defect. FAIL unless the design closes "
                          "the whole class; when you fix, close the class (prefer allowing only known-safe cases over blocking dangerous "
                          "ones one at a time).")
    return "ok"


# ---------------------------------------------------------------- one round: what replaces the review, then the review
def seed(ctx, args):
    ctx.fixed_in = None
    if ctx.seed is None:
        return "none"
    ctx.verdict, ctx.vp, ctx.head = "FAIL", ctx.seed, _head(ctx)  # a person asked for changes: the fixer takes them first
    ctx.seed, ctx.src = None, "review replaced by a person's comments"
    ctx.pc.log(f"#{ctx.pr} human feedback goes to the fixer first ({ctx.vp.name})")
    return "dry" if _verdict(ctx) else "seed"


def fold_main(ctx, args):
    pc, pr, rnd = ctx.pc, ctx.pr, _rnd(ctx)
    folded = pc.fold_or_red(pr)
    if isinstance(folded, pc.FoldRed):  # main merges in but tests fail: the fixer first, no reviewer
        ctx.head = _head(ctx)
        ctx.vp = pc.WORK / "prs" / str(pr) / f"r{rnd}-fold-red.md"
        ctx.vp.write_text(f"FAIL\nhead {ctx.head}\nFindings:\n- P1 fold:0 - tests fail once origin/main is merged into this branch - "
                          "merge origin/main, reproduce, fix; say BLOCKED when main alone fails the same way\n"
                          + "\n".join("    " + ln for ln in str(folded).splitlines()[-40:]) + "\n")
        ctx.verdict, ctx.src = "FAIL", "review replaced by red tests in the fold"
        return "dry" if _verdict(ctx) else "tests_red"
    if folded is None:  # conflict: the fixer resolves it
        ctx.vp = pc.WORK / "prs" / str(pr) / f"r{rnd}-conflict.md"
        ctx.vp.write_text("FAIL\nhead (conflict)\nFindings:\n- P1 merge-conflict:0 - the branch conflicts with main - "
                          "`git merge origin/main` stops with conflicts; resolve them, keep both sides' intent\n")
        ctx.verdict, ctx.head, ctx.src = "FAIL", _head(ctx), "review replaced by a merge conflict"
        return "dry" if _verdict(ctx) else "conflict"
    return "clean"


def ci_red(ctx, args):
    pc = ctx.pc
    bad = pc.ci_red(ctx.pr, _head(ctx))
    if bad and not pc.main_flake_rerun(ctx.pr, _head(ctx), bad):  # a test main also fails is rerun once instead
        ctx.head = _head(ctx)
        ctx.verdict, ctx.vp, ctx.src = "FAIL", pc.ci_findings(ctx.pr, _rnd(ctx), ctx.head, bad), "review replaced by red CI"
        ctx.red_bad = bad
        return "dry" if _verdict(ctx) else "red"
    return "not_red"


def merge_gates(ctx, args):
    pc, pr = ctx.pc, ctx.pr
    gbad = pc.gate_findings(pr)  # the merge gate first: a PR that cannot merge gets no review yet
    if not gbad:
        return "ok"
    ctx.head = _head(ctx)
    ctx.vp = pc.WORK / "prs" / str(pr) / f"r{_rnd(ctx)}-gates.md"
    ctx.vp.write_text(f"FAIL\nhead {ctx.head}\nFindings:\n" + "".join(
        f"- P1 gates:0 - {g} - the merge gate (scripts/pr_gates_dod.py, check dod/ready-for-review) reports it. Fix the PR: "
        "a feature needs a docs/specs file from docs/specs/TEMPLATE.md, a fix needs a Fix brief section (docs/specs/FIX_BRIEF.md), "
        "PR body problems go in new-pr-body.md. A false positive (an id that is not a finding): say so with evidence, end BLOCKED\n"
        for g in gbad))
    ctx.verdict, ctx.src = "FAIL", "review replaced by the merge gates"
    pc.ledger(pr=pr, stage="gates", round=_rnd(ctx), head=ctx.head, items=gbad)
    return "dry" if _verdict(ctx) else "fails"


def ci_pending_review(ctx, args):
    pc = ctx.pc
    if pc.DRY or not pc.ci_pending(ctx.pr, _head(ctx)):
        return "settled"
    h = _head(ctx)  # ci_wait_at: when this head started waiting; resume hands it to a fixer after the limit
    pc.set_state(ctx.pr, ci_wait="review", head=h, ci_wait_at={h: (ctx.st0.get("ci_wait_at") or {}).get(h) or pc.time.time()},
                 ci_wait_round=_rnd(ctx), ci_wait_key=ctx.prev_key)  # the next run goes on from this round
    ctx.why = "required checks still running; the review starts once none is pending"
    return "pending"


def carry_gate(ctx, args):
    pc = ctx.pc
    if ctx.carried or pc.DRY or ctx.extra:
        return "none"
    old = pc.carried_pass(ctx.pr, _head(ctx))  # an earlier run audited `old` as PASS and only main moved since
    if not old:
        return "none"
    if old == _head(ctx):  # this very head was audited as PASS in an earlier run: nothing to review again
        ctx.verdict, ctx.vp, ctx.head, ctx.src, ctx.carried, ctx.covered = "PASS", None, old, f"earlier audit of this head {old[:8]} stands", True, True
        _verdict(ctx)
        return "same_head"
    ctx.carry_old = old
    return "moved"


def agent_cover(ctx, args):
    pc = ctx.pc  # the merge-only addendum covers the moved head; cover_moved_head falls back to the full audit by itself
    g, gp, h, how = pc.cover_moved_head(ctx.pr, ctx.a, ctx.carry_old, f"{_rnd(ctx)}c", ctx.first_head)
    ctx.verdict, ctx.vp, ctx.head = ("BEHIND" if g == "CONFLICT" else g), gp, h
    ctx.src = f"earlier audit of {ctx.carry_old[:8]} carried by {how}"
    ctx.carried, ctx.covered = g not in ("BEHIND", "CONFLICT"), g == "PASS"
    _verdict(ctx)
    return _route(ctx)


def carry_review(ctx, args):
    pc = ctx.pc
    if ctx.carried or pc.DRY or ctx.extra:
        return "no"
    rold = pc.carried_pass(ctx.pr, _head(ctx), review=True)
    if not rold:
        return "no"
    # an earlier run's review passed `rold`, no gate ran on it, only main moved since: no second review, the gate audits it
    ctx.head = _head(ctx)
    ctx.verdict, ctx.vp, ctx.src, ctx.carried, ctx.covered = "PASS", None, f"earlier review PASS of {rold[:8]} stands", True, False
    _verdict(ctx)
    return "yes"


def agent_review(ctx, args):
    pc, rnd = ctx.pc, _rnd(ctx)
    model = _model(ctx, args)
    ctx.verdict, ctx.vp, ctx.head = pc.review(ctx.pr, rnd, "review", model, not ctx.a.no_post,
                                              extra=(ctx.class_note + "\n\n" + ctx.extra).strip(), fix_too=True)
    ctx.src = f"{model} review"
    ctx.fixed_in = pc.WORK / "prs" / str(ctx.pr) / f"r{rnd}-review"  # the reviewer fixed its own FAIL there (review-fix)
    ctx.covered = False  # a full review ran: its PASS gets the normal audit
    if _verdict(ctx):
        return "dry"
    return _route(ctx)


def after_pass(ctx, args):
    ctx.pc.file_followups(ctx.pr, ctx.vp)
    return "skip" if ctx.a.no_gate or ctx.covered else "audit"  # covered: the addendum (or its audit) already judged this head


def agent_audit(ctx, args):
    pc, pr, n = ctx.pc, ctx.pr, ctx.counts["audits"]
    g, gp, ghead = pc.audit_current(pr, ctx.a, f"{_rnd(ctx)}a{n}", ctx.first_head)
    pc.log(f"#{pr} audit {n} ({_model(ctx, args)}): {g}")
    pc.ledger(pr=pr, stage="audit", n=n, verdict=g, head=ghead, model=_model(ctx, args))
    if g == "PASS":
        ctx.head = ghead
        return "pass"
    if g == "FAIL":
        ctx.vp, ctx.verdict, ctx.head, ctx.fixed_in = gp, "FAIL", ghead, None  # the audit's findings go to the next fixer
        return "fail"
    if g == "CONFLICT":  # main moved and now conflicts: no audit ran; the next round's fold hands it to the fixer
        return "conflict"
    if g == "BEHIND":
        ctx.why = f"main moved {pc.DRIFT_TRIES} times before the audit could start; rerun the chain"
        return "behind"
    ctx.why = f"audit wrote no PASS/FAIL line; see {gp}"
    return "no_verdict"


# ---------------------------------------------------------------- UI PRs
def ui_needed(ctx, args):
    if not ctx.need_ui:
        return "no"
    ctx.audited, ctx.ui_tag = ctx.head, _rnd(ctx)
    return "yes"


def agent_ui_review(ctx, args):
    pc = ctx.pc  # a vision model runs ui-slop-review on screenshots and assumes the UI is wrong
    ctx.uv, ctx.uvp, ctx.uhead = pc.ui_review(ctx.pr, ctx.ui_tag, ctx.head, ctx.a)
    pc.log(f"#{ctx.pr} UI review ({ctx.a.ui_model}): {ctx.uv} at {ctx.uhead[:8] if ctx.uhead else '?'}")
    pc.ledger(pr=ctx.pr, stage="ui-review", n=ctx.ui_tag, verdict=ctx.uv, head=ctx.uhead, model=ctx.a.ui_model)
    if ctx.uv in ("PASS", "FAIL"):
        return ctx.uv.lower()
    ctx.why = f"UI review gave {ctx.uv}; see {ctx.uvp}"
    return "stack_down" if ctx.uv == "STACK-DOWN" else "no_verdict"


def agent_ui_fix(ctx, args):
    pc, pr, n = ctx.pc, ctx.pr, ctx.counts["ui_fails"]
    tag = f"{_rnd(ctx)}u{n}"  # no same-files stop here: UI laps often name one file; max_ui caps them
    new_head, note = pc.fix(pr, tag, _model(ctx, args), ctx.uvp, ctx.uhead or ctx.head, None)
    pc.log(f"#{pr} UI fix {n}: {new_head or 'none'} ({note})")
    pc.ledger(pr=pr, stage="ui-fix", n=n, head=new_head, note=note[:160])
    if new_head is None or new_head == "DISPUTE":
        ctx.why = "UI fixer " + ("disputed the UI findings; see " + str(ctx.uvp) if new_head else "produced nothing usable: " + note)
        return "nothing"
    ctx.head, ctx.ui_tag = new_head, tag
    pc.set_state(pr, head=new_head, last_verdict="UI-FIXED")
    return "fixed"


def ui_pass(ctx, args):
    ctx.pc.file_followups(ctx.pr, ctx.uvp)
    ctx.head, ctx.ui_vp = ctx.uhead or ctx.head, ctx.uvp
    if ctx.head == ctx.audited or ctx.a.no_gate:
        ctx.need_ui = False
        return "done"
    return "moved"  # the UI fixes moved the head past the audited one: one audit of the final head


def agent_audit_after_ui(ctx, args):
    pc, pr = ctx.pc, ctx.pr
    tag = f"{ctx.counts['audits']}u{ctx.counts['ui_fails']}"
    g, gp, ghead = pc.audit_current(pr, ctx.a, f"{_rnd(ctx)}a{tag}", ctx.first_head)
    pc.log(f"#{pr} audit after the UI fixes ({_model(ctx, args)}): {g}")
    pc.ledger(pr=pr, stage="audit", n=tag, verdict=g, head=ghead, model=_model(ctx, args), after="ui-fix")
    if g == "PASS":
        ctx.need_ui = ghead != ctx.head  # main was folded in under the audit: the landing step reshoots that head
        ctx.head = ghead
        return "pass"
    if g != "FAIL":
        ctx.why = f"audit after the UI fixes gave {g}; see {gp}"
        return "behind" if g in ("BEHIND", "CONFLICT") else "no_verdict"
    ctx.vp, ctx.verdict, ctx.head = gp, "FAIL", ghead  # a UI fix broke code: findings go to the fixer, then a full lap
    return "fail"


# ---------------------------------------------------------------- the fixer
def same_findings(ctx, args):
    key = ctx.pc.finding_key(ctx.vp.read_text())
    if key == ctx.prev_key:
        ctx.why = "the same findings came back two rounds in a row"
        return "same"
    ctx.prev_key = key
    return "new"


def agent_fix(ctx, args):
    pc, pr, rnd, model = ctx.pc, ctx.pr, _rnd(ctx), _model(ctx, args)
    if ctx.fixed_in:
        new_head, note = pc.fix(pr, rnd, model, ctx.vp, ctx.head, None, done_in=ctx.fixed_in)
        if new_head is None and not (note or "").startswith("fixer said BLOCKED"):
            pc.log(f"#{pr} round {rnd} review-fix gave nothing usable ({note}); the plain fixer takes it")
            new_head, note = pc.fix(pr, rnd, model, ctx.vp, ctx.head, None)
    else:
        new_head, note = pc.fix(pr, rnd, model, ctx.vp, ctx.head, None)
    pc.log(f"#{pr} round {rnd} fix: {new_head or 'none'} ({note})")
    if new_head == "DISPUTE":
        ctx.extra = "A previous fixer disputed the earlier findings with this evidence; judge it on the merits:\n" + note
        return "disputed"
    ctx.extra = ""
    if new_head is None and ctx.src == "review replaced by red CI" and "BLOCKED" in (note or "") and pc.ci_rerun(pr, ctx.head, ctx.red_bad):
        ctx.prev_key = None  # a flaky red test: one rerun of the failed jobs per head, not a round
        return "flaky_rerun"
    if new_head is None and (note or "").startswith("fixer said BLOCKED POLICY"):
        ctx.why = note  # the only fix breaks a project rule, or the PR declares its own hold
        return "blocked_policy"
    if new_head is None and (note or "").startswith("fixer said BLOCKED") and not ctx.src.startswith("review replaced"):
        # The fixer says a model's finding cannot be fixed here (#1012). The next reviewer weighs that evidence under review.md rule 5.
        rep = (ctx.fixed_in or pc.WORK / "prs" / str(pr) / f"r{rnd}-fix") / "fix-report.md"
        ctx.extra = ("A previous fixer could not fix the earlier findings at this same head and gave the evidence below. "
                     "Weigh it with rule 5's Follow-up test; a finding that still fits FAIL stays FAIL:\n"
                     + (rep.read_text()[-3000:] if rep.exists() else note))
        return "evidence"
    if new_head is None:
        ctx.why = f"fixer produced nothing usable: {note}"
        return "nothing"
    return "fixed"


# ---------------------------------------------------------------- landing: fold, cover a moved head, UI final, CI, merge
def agent_land_audit(ctx, args):
    pc, pr = ctx.pc, ctx.pr
    if not (ctx.from_gate and not ctx.a.no_gate and not ctx.approved):
        return "skip"
    if pc.fold(pr, pc.pr_info(pr)) is None:
        ctx.why = "conflict with main"
        return "fold_conflict"
    g, gp, ctx.head = pc.audit_current(pr, ctx.a, "land", ctx.first_head)
    pc.ledger(pr=pr, stage="audit", n="land", verdict=g, head=ctx.head, model=_model(ctx, args))
    if g == "PASS":
        return "pass"
    ctx.why = f"audit verdict {g}; see {gp}"
    return "fail" if g == "FAIL" else "behind" if g in ("BEHIND", "CONFLICT") else "no_verdict"


def land_try(ctx, args):
    pc, pr = ctx.pc, ctx.pr
    if ctx.reviewer is None:
        ctx.reviewer = f"{ctx.a.review_model} review, {ctx.a.gate_model} audit" + (f", {ctx.a.ui_model} UI review" if ctx.ui_vp else "")
    if pc.STOPFILE.exists():
        ctx.why = "STOP file"
        return "stop"
    if pc.DRY:
        return "same"
    if pc.fold(pr, pc.pr_info(pr)) is None:
        ctx.why = "conflict with main after the " + ("human approval" if ctx.approved else "audit")
        return "conflict"
    ctx.cur = pc.branch_head(pc.pr_info(pr))
    return "moved" if ctx.cur != ctx.head else "same"  # moved: main folded in, or a push; the audit does not cover it


def agent_land_cover(ctx, args):
    pc, pr, tries = ctx.pc, ctx.pr, ctx.counts["land_laps"]
    g, gp, covered, how = pc.cover_moved_head(pr, ctx.a, ctx.head, f"land{tries}", ctx.first_head)
    ctx.how, ctx.gp = how, gp
    if g in ("BEHIND", "CONFLICT") and tries < pc.DRIFT_TRIES:
        return "retry"  # main moved again before the reviewer could start: fold again; head stays the audited one
    if g == "PASS":
        ctx.shot_head, ctx.head = ctx.head, covered
        return "pass"
    if g == "FAIL" and ctx.approved:
        return "fail_approved"  # the approved path has no review loop: a landing fixer takes the findings (#1214)
    if not pc.DRY:  # rest and retry counters key on the head the PR has now (#1055)
        pc.set_state(pr, head=ctx.cur)
    ctx.why = f"{how} of the moved head: {g}; see {gp}"
    return "fail" if g == "FAIL" else "behind" if g in ("BEHIND", "CONFLICT") else "no_verdict"


def agent_land_fix(ctx, args):
    pc, pr, cur, tries = ctx.pc, ctx.pr, ctx.cur, ctx.counts["land_laps"]
    st = json.loads(pc.state_path(pr).read_text())
    nfix = (st.get("land_fixes") or {}).get(cur, 0)
    if nfix >= pc.LAND_FIX_CAP:  # a whack-a-mole audit at one head rests instead of burning fixers
        pc.set_state(pr, head=cur)
        ctx.why = f"{ctx.how} of the moved head failed after {nfix} landing fixes at {cur[:8]}; see {ctx.gp}"
        return "cap"
    pc.set_state(pr, land_fixes={cur: nfix + 1}, head=cur)
    new_head, note = pc.fix(pr, f"land{tries}", _model(ctx, args), ctx.gp, cur, None)
    pc.log(f"#{pr} landing fix {nfix + 1} of {pc.LAND_FIX_CAP}: {new_head or 'none'} ({note})")
    pc.ledger(pr=pr, stage="land-fix", n=nfix + 1, head=new_head, note=(note or "")[:160])
    if new_head is None or new_head == "DISPUTE":
        ctx.why = (f"{ctx.how} of the moved head: FAIL; the landing fixer "
                   + (f"disputed the findings; see {ctx.gp}" if new_head else f"produced nothing usable: {note}"))
        return "nothing"
    if new_head != cur and not pc.main_only_move(cur, new_head):  # code pushed: the approval stands for no head but its own
        if not pc.DRY:  # the push is a fresh attempt on a PR that had converged: earlier rests must not start the redesign
            pc.set_state(pr, rest_total=0, rests={})
        ctx.why = (f"the landing fixer pushed code ({cur[:8]} -> {new_head[:8]}): the approval does not stand for the new head; "
                   "the next run reviews it from the top")
        return "pushed_code"
    if new_head != cur:  # #1101: the head was behind main when the fixer started, so its push was only a merge of main
        pc.log(f"#{pr} the landing fixer's push only merged main ({cur[:8]} -> {new_head[:8]}): the approval stands")
    pc.retire_head_verdicts(pr, cur)  # a text-only fix: the FAIL just fixed must not be replayed at this head
    return "again"


def ui_carry(ctx, args):
    pc = ctx.pc
    if ctx.how == "addendum" and "addendum" not in ctx.reviewer:
        ctx.reviewer += f", {ctx.a.gate_model} merge-only addendum"
    if ctx.ui and ctx.ui_vp and not ctx.approved and not ctx.a.vision_ok:
        # The screenshots belong to an older head. They still hold when the head moved only by merging main and main brought in
        # no UI file (#1057). Anything else, or a diff git cannot compute: shoot the new head.
        dm = pc.sh("git", "-C", str(pc.CHECKOUT), "diff", "--name-only", ctx.shot_head, ctx.head)
        ctx.need_ui = ctx.how != "addendum" or dm.returncode != 0 or any(pc.UI_RE.search(f) for f in dm.stdout.split())
        if not ctx.need_ui:
            pc.log(f"#{ctx.pr} UI verdict of {ctx.shot_head[:8]} carries to {ctx.head[:8]}: main brought in no UI file")
            pc.ledger(pr=ctx.pr, stage="ui-carried", old=ctx.shot_head, head=ctx.head)
    return "ok"


def ui_final_needed(ctx, args):
    return "yes" if ctx.need_ui else "no"


def agent_ui_final(ctx, args):
    pc, pr = ctx.pc, ctx.pr
    uv, uvp, uhead = pc.ui_review(pr, "final", ctx.head, ctx.a)
    pc.ledger(pr=pr, stage="ui-review", n="final", verdict=uv, head=uhead, model=ctx.a.ui_model)
    if uv != "PASS":
        if uv == "FAIL":  # the next run's fixer takes these UI findings before any review (#1012)
            pc.set_state(pr, ui_seed=str(uvp), head=uhead or ctx.head)
        ctx.why = f"UI review {uv}; see {uvp}"
        return "fail" if uv == "FAIL" else "no_verdict"
    pc.file_followups(pr, uvp)
    ctx.head, ctx.ui_vp, ctx.need_ui = uhead or ctx.head, uvp, False
    return "pass"


def mark_ready(ctx, args):
    pc, a = ctx.pc, ctx.a
    pc.set_state(ctx.pr, status="READY", head=ctx.head, why="")
    if ctx.ui and not a.vision_ok and not ctx.approved and not a.no_human:
        return "human_gate"
    if not a.land:
        ctx.why = f"review PASS + audit PASS at {ctx.head[:8]}; rerun with --land to approve and merge"
        return "no_land"
    return "land"


def human_gate(ctx, args):
    ctx.ended = ctx.pc.human_gate(ctx.pr, ctx.head, ctx.ui_vp, ctx.a)  # screenshots on the PR; it parks the PR itself
    return "ended"


def ci_pending_land(ctx, args):
    pc = ctx.pc
    if not pc.DRY and pc.ci_pending(ctx.pr, ctx.head):  # watching CI is no work for a slot: resume lands it once CI finishes
        pc.set_state(ctx.pr, gate_head=ctx.head, reviewer=ctx.reviewer)
        ctx.why = f"required checks still running at {ctx.head[:8]}; the slot is free, resume lands it after CI"
        return "pending"
    return "settled"


def wait_ci(ctx, args):
    pc, pr, tries = ctx.pc, ctx.pr, ctx.counts["land_laps"]
    ci = pc.wait_ci(pr, ctx.head)
    pc.log(f"#{pr} CI: {ci}")
    if ci.startswith("RED") and (bad := pc.ci_red(pr, ctx.head)) and pc.main_flake_rerun(pr, ctx.head, bad):
        ci = pc.wait_ci(pr, ctx.head)  # a test main also fails, rerun once and green now
        pc.log(f"#{pr} CI after the rerun: {ci}")
    if ci == "HEAD-MOVED" and tries < pc.DRIFT_TRIES:
        pc.ledger(pr=pr, stage="drift", where="ci", head=ctx.head)
        return "head_moved_retry"
    if ci != "GREEN":
        ctx.why = f"required checks: {ci}"
        return {"TIMEOUT": "timeout", "HEAD-MOVED": "head_moved"}.get(ci.split()[0], "red")
    return "green"


def land(ctx, args):
    pc, pr, tries = ctx.pc, ctx.pr, ctx.counts["land_laps"]
    r = pc.land(pr, ctx.head, ctx.reviewer)
    if r.startswith("LAND-FAILED") and re.search(r"is (BEHIND|DIRTY)|not up to date with the base branch", r) and tries < pc.DRIFT_TRIES:
        pc.log(f"#{pr} main moved while CI ran (try {tries} of {pc.DRIFT_TRIES}): folding main in again")
        pc.ledger(pr=pr, stage="drift", where="land", head=ctx.head)
        return "retry"
    if r.startswith("LAND-FAILED") and "not green" in r and "FAILURE" in r:  # a red check: the fixer's job, not a person's
        ctx.why = r[:200]
        return "check_red"
    ctx.why = r
    return "merged" if r == "MERGED" else "refused"


STEPS = {name: fn for name, fn in globals().items() if callable(fn) and not name.startswith("_") and name not in ("init", "json", "re", "Path")}
STEPS["pass"] = noop
