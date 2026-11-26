"""Each check the chain requires is reported by a job that always finishes with a result, in the kit and in the starter pack.

The chain reads pr-chain.toml's `checks` by exact name and waits while one is missing or skipped. ci.yml named its jobs
`tests` and `package`, so no run ever reported `ci/tests`. A gate job that a failed need skips is not SUCCESS to the
chain either, so the gate runs always and reads each need's result itself. Every job in the kit's ci.yml has a timeout,
so a hung leg fails its check instead of holding it for six hours. The starter pack's ci/tests gate has the same rule.
"""
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
REQUIRED = tomllib.loads((ROOT / "pr-chain.toml").read_text())["accounts"]["checks"]


def parse_jobs(path):
    """Job id -> its lines, read from the `jobs:` map of a workflow file (the tests have no YAML library)."""
    jobs, cur, in_jobs = {}, None, False
    for ln in path.read_text().splitlines():
        if ln == "jobs:":
            in_jobs = True
        elif ln and not ln.startswith(" "):
            in_jobs = False
        elif in_jobs and re.fullmatch(r"  [A-Za-z_][\w-]*:", ln):
            cur = ln.strip()[:-1]
            jobs[cur] = []
        elif in_jobs and cur is not None:
            jobs[cur].append(ln)
    return jobs


def check_name(jid, lines):
    """The check name GitHub reports for a job: its `name:` when it has one, else its id."""
    for ln in lines:
        m = re.fullmatch(r"    name: (.+)", ln)
        if m:
            return m.group(1)
    return jid


def check_gate(jobs, check):
    """The job that reports `check` must need every other job, run always, and read each need's result."""
    names = {check_name(jid, lines): jid for jid, lines in jobs.items()}
    assert check in names, f"{check!r} is not the name of any job, so no run reports it: {sorted(names)}"
    gate = names[check]
    body = "\n".join(jobs[gate])
    needs = re.search(r"^    needs: \[(.*)\]$", body, re.M)
    assert needs, f"the {check!r} job has no `needs: [...]` list"
    others = [j for j in jobs if j != gate]
    listed = {x.strip() for x in needs.group(1).split(",")}
    assert set(others) <= listed, f"the {check!r} job must need every other job: {others}"
    assert re.search(r"^    if: always\(\)$", body, re.M), f"the {check!r} job must run with `if: always()`"
    for j in others:
        assert f"needs.{j}.result" in body, f"the {check!r} job must check needs.{j}.result, or a failed {j} passes"


def kit_ci():
    jobs = parse_jobs(ROOT / ".github" / "workflows" / "ci.yml")
    for check in REQUIRED:
        check_gate(jobs, check)
    for jid, lines in jobs.items():
        assert any(re.fullmatch(r"    timeout-minutes: \d+", ln) for ln in lines), (
            f"job {jid!r} needs timeout-minutes, or a hang holds the chain's check for six hours")


def starter_ci():
    check_gate(parse_jobs(ROOT / "starter-pack" / ".github" / "workflows" / "ci.yml"), "ci/tests")


failures = []
for check_fn in (kit_ci, starter_ci):
    try:
        check_fn()
    except AssertionError as exc:
        failures.append(f"{check_fn.__name__}: {exc}")
assert not failures, "\n".join(failures)
print("PASS")
