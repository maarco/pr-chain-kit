"""Every check pr-chain.toml requires must be a job name this repo's CI reports, or the chain waits for it forever."""
import re
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[2]
required = tomllib.loads((root / "pr-chain.toml").read_text())["accounts"]["checks"]
jobs = re.findall(r"^    name: (.+?)\s*$", (root / ".github/workflows/ci.yml").read_text(), re.MULTILINE)
for check in required:
    assert check in jobs, f"pr-chain.toml requires {check!r}, but no job in .github/workflows/ci.yml is named {check!r}"
print("PASS")
