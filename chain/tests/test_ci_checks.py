"""Every check pr-chain.toml requires must be a job name this repo's CI reports, or the chain waits for it forever.
Every CI job must also carry a timeout-minutes below the chain's CI wait (flow.json), or a stuck job outlasts the wait."""
import json
import re
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[2]
required = tomllib.loads((root / "pr-chain.toml").read_text())["accounts"]["checks"]
ci = (root / ".github/workflows/ci.yml").read_text()
jobs = re.findall(r"^    name: (.+?)\s*$", ci, re.MULTILINE)
for check in required:
    assert check in jobs, f"pr-chain.toml requires {check!r}, but no job in .github/workflows/ci.yml is named {check!r}"
wait = json.loads((root / "chain/flow.json").read_text())["limits"]["ci_wait_max_minutes"]["value"]
for block in re.split(r"^  (?=[\w-]+:[ \t]*$)", ci.split("\njobs:\n", 1)[1], flags=re.MULTILINE)[1:]:
    job, bound = block.split(":", 1)[0], re.search(r"^    timeout-minutes: (\d+)[ \t]*$", block, re.MULTILINE)
    assert bound and int(bound.group(1)) < wait, (
        f"job {job!r} in .github/workflows/ci.yml has no timeout-minutes under the chain's {wait}-minute CI wait: "
        "a stuck run outlasts the wait and its required check never finishes")
print("PASS")
