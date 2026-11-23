"""fold.py with [[merge_checks]]: configured commands replace the Python default, run once on matching changed files,
and a failing one stops the fold (exit 3). Scratch repo, stub gh, no network."""
import atexit, json, os, pathlib, shutil, subprocess, sys, tempfile

kit = pathlib.Path(__file__).resolve().parents[2]
tmp = pathlib.Path(tempfile.mkdtemp(prefix="mergechecks--prc"))
atexit.register(shutil.rmtree, tmp, True)
fails = []
def check(name, ok, detail=""):
    print(("ok   " if ok else "FAIL ") + name + ("" if ok else f": {detail}"))
    if not ok: fails.append(name)

env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp)}
up, seed, train = tmp / "up.git", tmp / "seed", tmp / "train"
def g(*a, cwd=seed):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout.strip()
subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(up)], check=True)
subprocess.run(["git", "clone", "-q", str(up), str(seed)], check=True, capture_output=True, env=env)
g("checkout", "-q", "-b", "main"); (seed / "README.md").write_text("x\n"); g("add", "-A"); g("commit", "-q", "-m", "base"); g("push", "-q", "origin", "main")

def branch(name, text):
    g("checkout", "-q", "-b", name, "main"); (seed / "src").mkdir(exist_ok=True)
    (seed / "src" / "lib.rs").write_text(text); (seed / "src" / "notes.txt").write_text("n\n")
    g("add", "-A"); g("commit", "-q", "-m", name); g("push", "-q", "origin", name); sha = g("rev-parse", "HEAD"); g("checkout", "-q", "main")
    return sha
heads = {"good": branch("good", "fn ok() {}\n"), "bad": branch("bad", "fn BAD() {}\n")}
subprocess.run(["git", "clone", "-q", str(up), str(train)], check=True, capture_output=True, env=env)

(tmp / "bin").mkdir()
gh = tmp / "bin" / "gh"
gh.write_text("#!/bin/sh\ncat \"$GH_STUB\"\n"); gh.chmod(0o755)
log = tmp / "ran.txt"
(tmp / "pr-chain.toml").write_text(f"""
[repo]
slug = "o/r"
default_branch = "main"
checkout = "{train}"
import_guard = ""
[changelog]
forbidden = ["CHANGELOG.md"]
fragment_dir = ""
[[merge_checks]]
name = "rs-lint"
match = ["*.rs"]
cmd = ["sh", "-c", "echo \\"$@\\" >> {log}; ! grep -l BAD \\"$@\\"", "x", "{{files}}"]
[[merge_checks]]
name = "md-never"
match = ["docs/*.md"]
cmd = ["false"]
""")

def fold(name):
    stub = tmp / f"{name}.json"
    stub.write_text(json.dumps(dict(headRefName=name, headRefOid=heads[name], state="OPEN", baseRefName="main", closingIssuesReferences=[])))
    subprocess.run(["git", "checkout", "-q", "--detach", "origin/main"], cwd=train, env=env, capture_output=True)
    e = {**env, "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}", "GH_STUB": str(stub), "PR_CHAIN_CONFIG": str(tmp / "pr-chain.toml"),
         "KIT_TRAIN": str(train), "KIT_REPO": "o/r", "KIT_PYTHON": sys.executable}
    return subprocess.run([sys.executable, "-B", str(kit / "agent-kit/land/fold.py"), "1"], env=e, capture_output=True, text=True)

r = fold("good")
check("a passing configured check: fold exits 0", r.returncode == 0, r.stdout[-400:] + r.stderr[-400:])
check("the summary names the configured checks", "checks rs-lint, md-never" in r.stdout, r.stdout[-300:])
check("the check got only the matching changed file", log.exists() and log.read_text().split() == ["src/lib.rs"], log.read_text() if log.exists() else "not run")
check("a check with no matching file does not run", "md-never:" not in r.stdout, r.stdout[-300:])
check("no Python default when checks are configured", "ruff" not in r.stdout and "pytest" not in r.stdout, r.stdout[-300:])
r = fold("bad")
check("a failing configured check stops the fold with exit 3", r.returncode == 3, f"rc={r.returncode} " + r.stdout[-300:] + r.stderr[-300:])
check("the failure names the check", "rs-lint: " in r.stdout, r.stdout[-300:])

print("\nall passed" if not fails else f"\n{len(fails)} failed")
sys.exit(1 if fails else 0)
