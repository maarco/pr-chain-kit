#!/usr/bin/env python3
"""Reviewer writes are blocked from the source trees and cannot yield an accepted verdict."""
import importlib.machinery
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types


tmp = pathlib.Path(tempfile.mkdtemp(prefix="pr-chain-readonly-review-"))
os.environ["KIT_WORK"] = str(tmp / "work")
os.environ["PR_CHAIN_CONFIG"] = str(pathlib.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml")
os.environ["PR_CHAIN_WT_ROOT"] = str(tmp / "wt-root")
sys.dont_write_bytecode = True
source = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain"
loader = importlib.machinery.SourceFileLoader("prchain_readonly", str(source))
pc = importlib.util.module_from_spec(importlib.util.spec_from_loader("prchain_readonly", loader))
loader.exec_module(pc)

fails = []


def check(name, got, expected):
    ok = got == expected
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, wanted {expected!r}"))
    if not ok:
        fails.append(name)


def make_repo(path, symlink_target=None):
    path = pathlib.Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True, capture_output=True)
    (path / "tracked.txt").write_text("source\n")
    if symlink_target is not None:
        (path / "external-link").symlink_to(symlink_target, target_is_directory=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.name=test", "-c", "user.email=test@local",
                    "commit", "-qm", "fixture"], check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def copy_repo(source_repo, destination):
    pathlib.Path(destination).parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_repo, destination, symlinks=True)


try:
    pc.log = lambda _: None
    pc.DRY = False
    pc.WORK.mkdir(parents=True, exist_ok=True)
    pc.WT_ROOT.mkdir(parents=True, exist_ok=True)
    checkout = tmp / "checkout"
    external = tmp / "external-target"
    external.mkdir()
    head = make_repo(checkout, external)
    pc.CHECKOUT = checkout
    output = pc.WORK / "prs" / "1" / "r1-gate"
    stage_wt = pc.wt_for(output)
    stage_head = make_repo(stage_wt)
    output.mkdir(parents=True)

    check("review and gate roles are read-only", all(r in pc.READ_ONLY_AGENT_ROLES for r in ("review", "gate", "addendum", "ui")), True)
    check("review-fix remains writable", pc.readonly_agent_command("review-fix", ["agent"], output), ["agent"])

    write_probe = (
        "import pathlib,sys\n"
        "for raw in sys.argv[1:4]:\n"
        " try: pathlib.Path(raw).write_text('blocked')\n"
        " except OSError: pass\n"
        "pathlib.Path(sys.argv[4]).write_text('PASS\\n')\n"
    )
    protected_paths = [checkout / "blocked.txt", stage_wt / "blocked.txt", checkout / "external-link" / "escaped.txt"]
    verdict = output / "verdict.md"
    fake_cmd = [sys.executable, "-c", write_probe, *(str(p) for p in protected_paths), str(verdict)]
    pc.agent_cmd = lambda model, prompt, brief: fake_cmd
    pc.cfg_snapshot = lambda: {}
    pc.cfg_damage = lambda before, after: ([], [])
    pc.ledger = lambda **row: None
    result = pc.run_agent("gate", "test-model", output / "brief.md", stage_wt, output, 1)
    check("review runner completes its bounded probe", result["rc"], 0)
    check("reviewer cannot write to the main checkout", protected_paths[0].exists(), False)
    check("reviewer cannot write to its pinned worktree", protected_paths[1].exists(), False)
    check("tracked symlink cannot escape the source write deny", (external / "escaped.txt").exists(), False)
    check("reviewer can write its verdict outside source trees", verdict.read_text(), "PASS\n")
    check("read-only worktree starts clean at its head", pc.read_only_worktree_changed(stage_wt, stage_head), False)
    (stage_wt / "untracked-review-write.py").write_text("unexpected\n")
    check("untracked reviewer edits are detected", pc.read_only_worktree_changed(stage_wt, stage_head), True)
    check("review-fix detects untracked edits", pc.self_changed(stage_wt, stage_head), True)
    (stage_wt / "untracked-review-write.py").unlink()
    check("clean review-fix worktree passes its guard", pc.self_changed(stage_wt, stage_head), False)

    proof_dir = output / "proof"
    proof_dir.mkdir()
    check("old verdict without read-only proof is not reusable", pc.has_readonly_proof(proof_dir, head), False)
    pc.write_readonly_proof(proof_dir, head)
    check("proof is pinned to its reviewed head", pc.has_readonly_proof(proof_dir, head), True)
    check("proof does not carry to another head", pc.has_readonly_proof(proof_dir, "other-head"), False)

    # Simulate a buggy adapter that ignores the OS sandbox. The script must reject its PASS and keep the worktree.
    review_fixture = tmp / "review-fixture"
    review_head = make_repo(review_fixture)
    info = dict(title="fixture", body="", author={"login": "owner"}, files=[], headRefName="fixture",
                closingIssuesReferences=[])
    pc.pr_info = lambda pr: info
    pc.fetch = lambda _: "main"
    pc.branch_head = lambda _: review_head
    pc.contains_main = lambda current, main: True
    pc.make_wt = lambda path, ref: copy_repo(review_fixture, path)
    pc.issues_text = lambda _: "none\n"
    pc.render = lambda template, **values: "brief\n"
    pc.src_path = lambda wt: str(wt)
    posts, followups, dropped, ledger_rows = [], [], [], []
    pc.post_verdict = lambda *args: posts.append(args)
    pc.file_followups = lambda *args, **kwargs: followups.append(args)
    pc.drop_wt = lambda path: dropped.append(path)
    pc.ledger = lambda **row: ledger_rows.append(row)

    def dirty_review_agent(role, model, brief, cwd, outdir, pr):
        (pathlib.Path(cwd) / "unauthorized.py").write_text("change\n")
        (pathlib.Path(outdir) / "verdict.md").write_text(f"PASS\nhead {review_head}\n")
        return dict(rc=0, secs=1, turns=1, cost=0)

    pc.run_agent = dirty_review_agent
    review_result = pc.review(41, 1, "gate", "test-model", True)
    review_dir = pc.WORK / "prs" / "41" / "r1-gate"
    check("dirty gate verdict is rejected", review_result[0], None)
    check("dirty gate verdict is not posted", posts, [])
    check("dirty gate follow-ups are not filed", followups, [])
    check("dirty gate worktree is preserved", (pc.wt_for(review_dir) / "unauthorized.py").exists(), True)
    check("dirty gate worktree is not dropped", dropped, [])
    check("rejected gate verdict is preserved under an invalidated name", bool(list(review_dir.glob("verdict.rejected-*.md"))), True)
    check("rejected gate verdict has no reusable proof", pc.has_readonly_proof(review_dir, review_head), False)

    # Repeat the same post-run guard for the vision reviewer, whose output folder differs from its worktree.
    ui_fixture = tmp / "ui-fixture"
    ui_head = make_repo(ui_fixture)
    ui_info = dict(info, files=[{"path": "packages/kollabor-webui/frontend/src/App.tsx"}])
    pc.pr_info = lambda pr: ui_info
    pc.fetch = lambda _: "main"
    pc.make_wt = lambda path, ref: copy_repo(ui_fixture, path)
    pc.term_shots = lambda *args: "none"
    pc.term_cmd = lambda *args: ""
    pc.ui_focus = lambda *args: "fixture focus"
    pc.clean_stack = lambda stack: None
    ui_posts = []
    real_sh = pc.sh

    def ui_sh(*args, **kwargs):
        if len(args) > 2 and args[0] == sys.executable and args[1] == str(pc.UI_STACK):
            ui_posts.append(args)
            return types.SimpleNamespace(returncode=0,
                stdout="http://127.0.0.1:1\n" if args[2] == "up" else "", stderr="")
        return real_sh(*args, **kwargs)

    pc.sh = ui_sh

    def dirty_ui_agent(role, model, brief, cwd, outdir, pr):
        (pc.wt_for(outdir) / "unauthorized-ui.py").write_text("change\n")
        (pathlib.Path(outdir) / "ui-verdict.md").write_text(f"PASS\nhead {ui_head}\n")
        return dict(rc=0, secs=1, turns=1, cost=0)

    pc.run_agent = dirty_ui_agent
    ui_result = pc.ui_review(42, 1, ui_head, types.SimpleNamespace(ui_model="vision", no_post=False))
    ui_dir = pc.WORK / "prs" / "42" / "r1-ui"
    check("dirty UI verdict is rejected", ui_result[0], None)
    check("dirty UI verdict is not posted", not any(c and c[0] == "gh" for c in ui_posts), True)
    check("dirty UI worktree is preserved", (pc.wt_for(ui_dir) / "unauthorized-ui.py").exists(), True)
    check("dirty UI verdict is preserved under an invalidated name", bool(list(ui_dir.glob("ui-verdict.rejected-*.md"))), True)
    check("dirty UI verdict has no reusable proof", pc.has_readonly_proof(ui_dir, ui_head), False)

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall read-only reviewer checks passed")
sys.exit(1 if fails else 0)
