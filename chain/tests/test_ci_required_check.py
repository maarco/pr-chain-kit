#!/usr/bin/env python3
"""ci_required_check: every check the chain waits for (pr-chain.toml [accounts] checks) is the name of a job in
.github/workflows/ci.yml, and that job runs even when a test job fails (a skipped check reads as pending to the chain,
so the head waits out the whole CI wait limit). Reads repo files only: no git, no gh, no agents."""
import pathlib, re, sys, tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
fails = []


def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}"))
    if not ok:
        fails.append(name)


def display(job_id, body):
    m = re.search(r"^    name: (.+?)\s*$", body, re.M)
    return m.group(1).strip("'\"") if m else job_id


required = tomllib.loads((ROOT / "pr-chain.toml").read_text())["accounts"]["checks"]
wf = (ROOT / ".github/workflows/ci.yml").read_text()
jobs = {display(i, b): (i, b) for i, b in re.findall(r"^  ([\w-]+):\n((?:    .*\n|\n)*)", wf.split("\njobs:\n", 1)[1], re.M)}

for name in required:
    check(f"ci.yml has a job reporting the required check {name!r}", name in jobs,
          f"jobs report {sorted(jobs)}; the chain waits for {name!r}, which never appears, so the head stays pending")
    if name in jobs:
        _, body = jobs[name]
        check(f"{name!r} runs when a test job fails (if: always())", re.search(r"^    if: always\(\)\s*$", body, re.M) is not None,
              "without it a failed test job skips this one, and a skipped check reads as pending")
        check(f"{name!r} needs the test job", re.search(r"^    needs: .*\btests\b", body, re.M) is not None,
              "without needs it reports success before the tests finish")

sys.exit(1 if fails else 0)
