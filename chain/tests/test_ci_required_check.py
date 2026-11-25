"""Every check the chain requires is the exact name of a job in ci.yml, and that gate job fails when a job it needs fails.

The chain reads pr-chain.toml's `checks` by exact name and keeps waiting while one is missing. ci.yml named its jobs
`tests` and `package`, so no run ever reported `ci/tests`. A gate job that is skipped after a failed need is not
SUCCESS to the chain either, so the gate runs always and checks each need's result itself.
"""
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
lines = (ROOT / ".github/workflows/ci.yml").read_text().splitlines()
required = tomllib.loads((ROOT / "pr-chain.toml").read_text())["accounts"]["checks"]

jobs, cur, in_jobs = {}, None, False
for ln in lines:
    if ln == "jobs:":
        in_jobs = True
    elif ln and not ln.startswith(" "):
        in_jobs = False
    elif in_jobs and re.fullmatch(r"  [A-Za-z_][\w-]*:", ln):
        cur = ln.strip()[:-1]
        jobs[cur] = []
    elif in_jobs and cur is not None:
        jobs[cur].append(ln)


def display_name(jid):
    """The check name GitHub reports for a job: its `name:` when it has one, else its id."""
    for ln in jobs[jid]:
        m = re.fullmatch(r"    name: (.+)", ln)
        if m:
            return m.group(1)
    return jid


names = {display_name(jid): jid for jid in jobs}
for check in required:
    assert check in names, f"{check!r} is not the name of any job in ci.yml, so no run reports it: {sorted(names)}"
    gate = names[check]
    body = "\n".join(jobs[gate])
    needs = re.search(r"^    needs: \[(.*)\]$", body, re.MULTILINE)
    assert needs, f"the {check!r} job has no `needs: [...]` list"
    others = [j for j in jobs if j != gate]
    listed = {x.strip() for x in needs.group(1).split(",")}
    assert set(others) <= listed, f"the {check!r} job must need every other job: {others}"
    assert re.search(r"^    if: always\(\)$", body, re.MULTILINE), f"the {check!r} job must run with `if: always()`"
    for j in others:
        assert f"needs.{j}.result" in body, f"the {check!r} job must check needs.{j}.result, or a failed {j} passes"
print("PASS")
