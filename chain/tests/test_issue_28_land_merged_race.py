#!/usr/bin/env python3
"""Issue #28: a PR that the merge bot merges while land_pr.py waits for CLEAN is landed, not a 300 s STOP.

GitHub reports such a PR as state MERGED with mergeStateStatus UNKNOWN. The first wait loop in
agent-kit/land/land_pr.py looked only at mergeStateStatus, so it polled to its 300 s limit and stopped with
"STOP: UNKNOWN ...". The fake gh here is OPEN/BLOCKED until the approval, then MERGED/UNKNOWN. land_pr.py runs
under a virtual clock (a fake sleep advances time at once), so the limit costs no real time.
Scratch dir, fake gh first on PATH, no network."""
import atexit, json, os, pathlib, shutil, subprocess, sys, tempfile

root = pathlib.Path(__file__).resolve().parents[2]
tmp = pathlib.Path(tempfile.mkdtemp(prefix="issue-28--prc"))
atexit.register(shutil.rmtree, tmp, True)
CHECKS = ("ci/tests", "ci/standards-check")
fails = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok:
        fails.append(name)


# land_pr.py polls every 15 s and gives up 300 s after it starts waiting. This shim runs it on a virtual clock:
# sleep() only advances the clock; the total slept seconds are written to $CLOCK_OUT.
CLOCK_SHIM = """
import json, os, runpy, sys, time
now, slept = [1_800_000_000.0], [0.0]
def fake_sleep(seconds):
    now[0] += seconds
    slept[0] += seconds
time.time = lambda: now[0]
time.sleep = fake_sleep
sys.argv = sys.argv[1:]  # land_pr.py reads its arguments from sys.argv[1:]
try:
    runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    open(os.environ["CLOCK_OUT"], "w").write(json.dumps(slept[0]))
"""

# A fake gh: OPEN/BLOCKED until the approval, then MERGED/UNKNOWN, as GitHub reports a PR the bot merged at once.
FAKE_GH = "#!" + sys.executable + "\n" + r'''
import json, os, sys
d = os.environ["FAKE_GH"]; a = sys.argv[1:]
w = json.load(open(d + "/world.json"))
open(d + "/calls.jsonl", "a").write(json.dumps(a) + "\n")
approved = os.path.exists(d + "/approved")
if a[:2] == ["auth", "token"]:
    print("tok")
elif a[:2] == ["pr", "view"]:
    pr = dict(w["pr"])
    if approved:
        pr.update(state="MERGED", mergeStateStatus="UNKNOWN", reviewDecision="APPROVED",
                  mergedBy={"login": "bot"}, mergeCommit={"oid": "e" * 40})
    print(json.dumps(pr))
elif a[:2] == ["pr", "review"]:
    open(d + "/approved", "w").close()
elif a[0] == "api" and "POST" in a and any(x.endswith("/reviews") for x in a):
    open(d + "/approved", "w").close(); print("{}")
elif a[0] == "api" and any(x.endswith("/reviews") for x in a):
    print(w["reviews"])
'''


def land_when_the_bot_merges():
    """Run land_pr.py for one PR that is OPEN/BLOCKED until approved, then merged by the bot."""
    fake = tmp / "fakegh"
    fake.mkdir()
    (fake / "gh").write_text(FAKE_GH)
    (fake / "gh").chmod(0o755)
    head = "a" * 40
    pr = dict(state="OPEN", isDraft=True, author={"login": "owner"}, headRefOid=head, baseRefOid="b" * 40,
              mergeStateStatus="BLOCKED", reviewDecision="REVIEW_REQUIRED", mergedBy=None, mergeCommit=None,
              statusCheckRollup=[{"name": c, "conclusion": "SUCCESS"} for c in CHECKS])
    (fake / "world.json").write_text(json.dumps(dict(pr=pr, reviews="")))
    work, clock_out = tmp / "work", tmp / "clock.json"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GH_", "GITHUB_", "KIT_", "PR_CHAIN_", "LAND_"))}
    env.update(PATH=f"{fake}{os.pathsep}{os.environ['PATH']}", FAKE_GH=str(fake), CLOCK_OUT=str(clock_out),
               KIT_REPO="owner/name", KIT_MERGER="bot", KIT_CHECKS=",".join(CHECKS), KIT_WORK=str(work),
               PR_CHAIN_CONFIG=str(root / "chain/tests/test-config.toml"), LAND_PAUSE="0")
    land = str(root / "agent-kit/land/land_pr.py")
    try:
        p = subprocess.run([sys.executable, "-B", "-c", CLOCK_SHIM, land, "1", head, "bot", head, "rev"],
                           capture_output=True, text=True, env=env, timeout=60)
        rc, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        rc, out = "timeout", ""
    calls_file, log = fake / "calls.jsonl", work / "landings.jsonl"
    return dict(
        rc=rc, out=out,
        slept=json.loads(clock_out.read_text()) if clock_out.exists() else None,
        calls=[json.loads(x) for x in calls_file.read_text().splitlines()] if calls_file.exists() else [],
        rows=[json.loads(x) for x in log.read_text().splitlines()] if log.exists() else [],
    )


r = land_when_the_bot_merges()
subcommands = [c[1] for c in r["calls"] if c[:1] == ["pr"]]
check("issue #28: land_pr exits 0 when the bot merged the PR while it waited for CLEAN", r["rc"], 0)
check("issue #28: it reports the PR merged, with the bot's merge commit",
      f"#1 MERGED by bot merge commit {'e' * 40}" in r["out"], True)
check("issue #28: it does not poll toward its 300 s limit (at most two 15 s polls)",
      r["slept"] is not None and r["slept"] <= 30, True)
check("issue #28: it does not merge a PR that is already merged", "merge" in subcommands, False)
check("issue #28: the landings row records MERGED", [(x.get("state"), x.get("merge_commit")) for x in r["rows"]],
      [("MERGED", "e" * 40)])

print("\nFAILED: " + ", ".join(fails) if fails else "\nall issue #28 checks passed")
sys.exit(1 if fails else 0)
