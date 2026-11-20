"""Checks for the term_shots helper without tmux or a browser: the harness patch still applies to
main's tmux harness, harness rows are trimmed, and identity text is scrubbed at the same length."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import os as _os, pathlib as _pl, tempfile as _tf
_os.environ.setdefault("PR_CHAIN_CONFIG", str(_pl.Path(__file__).resolve().parents[2] / "chain/tests/test-config.toml"))
_os.environ.setdefault("KIT_WORK", _tf.mkdtemp(prefix="kitwork--prc"))

src = pathlib.Path(__file__).resolve().parents[2] / "examples/kollab/term_shots.py"
spec = importlib.util.spec_from_file_location("term_shots", src)
ts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ts)
fails = []


def check(name, got, want):
    ok = got == want
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": got {got!r}, want {want!r}"))
    if not ok:
        fails.append(name)


# 1. The patch applies to the harness on origin/main (a moved anchor must fail here, not in a live chain).
real = pathlib.Path(_os.environ.get("KOLLAB_CHECKOUT") or pathlib.Path.home() / "dev/kollab")  # optional: a real kollab checkout
hr = subprocess.run(["git", "-C", str(real), "show", "origin/main:tests/tmux/lib/test_runner.sh"], capture_output=True, text=True)
if hr.returncode == 0:
    h = hr.stdout
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "tests/tmux/lib/test_runner.sh"
        f.parent.mkdir(parents=True)
        f.write_text(h)
        code = ts.patched_runner(pathlib.Path(d))
    check("patch: helper defined once", code.count("def _term_shot("), 1)
    check("patch: called at capture and at a passing wait_for", code.count("_term_shot("), 3)
    compile(code, "runner.py", "exec")
    check("patch: the patched runner compiles", True, True)
    try:
        with tempfile.TemporaryDirectory() as d:
            f = pathlib.Path(d) / "tests/tmux/lib/test_runner.sh"
            f.parent.mkdir(parents=True)
            f.write_text(h.replace("def _capture(", "def _capture_moved("))
            ts.patched_runner(pathlib.Path(d))
        check("patch: a moved anchor raises", "no error", "RuntimeError")
    except RuntimeError:
        check("patch: a moved anchor raises", "RuntimeError", "RuntimeError")
else:
    print("skip patch checks: no kollab checkout (set KOLLAB_CHECKOUT)")

# 2. The harness's own typed `env -u KOLLAB_...` line is dropped, wrapped rows included; app output stays.
w = 20
rows = ["env -u KOLLAB_WEBUI_H", "OSTS HOME=/x bash --", "norc", "bash-5.3$ python3 x", "Installed hello"]
check("trim: env rows dropped up to the prompt", ts.trim_harness_rows("\n".join(rows), w), "bash-5.3$ python3 x\nInstalled hello")
rows = ["$ env -u KOLLAB_A x", "Plugin: Hello", "Install? [yes/no]"]  # 19 chars: shorter than w
check("trim: a short env row ends at once", ts.trim_harness_rows("\n".join(rows), w), "Plugin: Hello\nInstall? [yes/no]")
check("trim: no env row, nothing changes", ts.trim_harness_rows("a\nb", w), "a\nb")

# 3. Scrub: same length, nothing left.
toks = {"someone@mail.test": "dev@example.test-", "realuser": "dev-user"}
out = ts.scrub("cwd /Users/realuser/x  someone@mail.test  REALUSER", toks)
check("scrub: same length", len(out), len("cwd /Users/realuser/x  someone@mail.test  REALUSER"))
check("scrub: no token left", any(t.lower() in out.lower() for t in toks), False)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall term_shots checks passed")
sys.exit(1 if fails else 0)
