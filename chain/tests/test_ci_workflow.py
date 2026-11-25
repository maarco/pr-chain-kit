"""The CI workflow reports each check the chain requires, and the gate behind that check fails when a test shard fails.

The chain waits for the checks named in pr-chain.toml [accounts] checks (the template says ci/tests) and reads GREEN only
when each one is SUCCESS. A required name that no job reports never finishes: the chain waits out its limit and then hands
the PR to a fixer. The gate that reports the name must run after a red shard too (if: always()), or it is skipped, and a
skipped check never finishes either. The gate fails unless the test job succeeded.
"""

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
required = tomllib.loads((ROOT / "pr-chain.toml").read_text())["accounts"]["checks"]

# job id -> the job's text: every two-space key under `jobs:` starts a new job
jobs = {}
for chunk in re.split(r"(?m)^  (?=[\w-]+:[ \t]*$)", workflow.split("\njobs:\n", 1)[1]):
    if chunk.strip():
        jobs[chunk.split(":", 1)[0]] = chunk

assert "tests" in jobs, f"ci.yml has no tests job: {sorted(jobs)}"
for name in required:
    owners = [job for job, body in jobs.items() if re.search(rf"(?m)^    name: {re.escape(name)}$", body)]
    assert owners, f"no ci.yml job reports the required check {name!r}: the chain waits for it and it never finishes"
    gate = jobs[owners[0]]
    assert re.search(r"(?m)^    needs: \[?tests\]?$", gate), f"the {name!r} job must need the tests job"
    assert re.search(r"(?m)^    if: always\(\)$", gate), f"the {name!r} job must run after a red shard, or it is skipped"
    script = gate.split("        run: |\n", 1)[1]
    body = "\n".join(line[10:] for line in script.splitlines() if line.startswith(" " * 10))
    for result in ("success", "failure", "cancelled"):
        rc = subprocess.run(["bash", "-e", "-c", body.replace("${{ needs.tests.result }}", result)],
                            capture_output=True, text=True).returncode
        assert (rc == 0) == (result == "success"), f"the {name!r} gate with tests {result} exits {rc}"
print("PASS")
