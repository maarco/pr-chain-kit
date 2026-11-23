#!/usr/bin/env python3
"""pr-chain-loop: a chain's output lands in its own run log, and `ended` reports only that run's RESULT.
No agents, no gh: the chain is a stub script in a scratch folder."""
import atexit, importlib.machinery, importlib.util, json, pathlib, shutil, tempfile
import os as _os, pathlib as _pl, tempfile as _tf
_os.environ.setdefault("PR_CHAIN_CONFIG", str(_pl.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml"))
_os.environ.setdefault("KIT_WORK", _tf.mkdtemp(prefix="kitwork--prc"))

tmp = pathlib.Path(tempfile.mkdtemp(prefix="loop--prc"))
atexit.register(shutil.rmtree, tmp, True)

src = pathlib.Path(__file__).resolve().parents[2] / "bin/pr-chain-loop"
loader = importlib.machinery.SourceFileLoader("prloop", str(src))
loop = importlib.util.module_from_spec(importlib.util.spec_from_loader("prloop", loader))
loader.exec_module(loop)
loop.WORK = tmp
stub = tmp / "stub-chain"
stub.write_text("import sys\nprint('[00:00:01] #5 review PASS', flush=True)\n"
                "if sys.argv[1] == 'run':\n    print('RESULT: READY')\nsys.exit(0 if sys.argv[1] == 'run' else 3)\n")
loop.CHAIN = str(stub)

log = tmp / "logs" / "run-5.log"
log.parent.mkdir(parents=True)
log.write_text("[23:59:00] #5 an earlier run\nRESULT: NEEDS-HUMAN\n")

p, pos = loop.spawn_logged(log, "run", "5", "--land")
p.wait()
got = loop.ended(5, p, log, pos)
assert got == "READY", got  # the earlier run's NEEDS-HUMAN in the same file is not this run's result
text = log.read_text()
assert "an earlier run" in text and "#5 review PASS" in text, text  # appended, not overwritten

(tmp / "prs" / "5").mkdir(parents=True)
(tmp / "prs" / "5" / "STATE.json").write_text(json.dumps({"status": "READY", "why": "review PASS + audit PASS at abc12345"}))
got = loop.ended(5, p, log, pos)
assert got == "READY: review PASS + audit PASS at abc12345", got

p, pos = loop.spawn_logged(log, "crash", "5")  # a chain that dies without a RESULT line
p.wait()
got = loop.ended(5, p, log, pos)
assert got.startswith("with no result line (exit 3;"), got

# one host, several repos: a loop counts only its own repo's chains (and untagged hand runs, which may be its own)
me = loop.CONF.slug
assert loop.ours(f"python3 -B /k/bin/pr-chain run 5 --land --repo {me}")
assert not loop.ours("python3 -B /k/bin/pr-chain run 5 --land --repo someone/else")
assert loop.ours("python3 -B /k/bin/pr-chain run 5 --land")
assert not loop.ours("python3 -B /k/bin/pr-chain pick --repo " + me)
assert loop.TAG == ("--repo", me)

print("all loop checks passed")
