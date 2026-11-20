"""`pr-chain` entry point: init, board, loop, janitor, path; anything else goes to bin/pr-chain."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE / "kit" if (HERE / "kit" / "bin").is_dir() else HERE.parent  # installed wheel, else a clone
SCRIPTS = {"board": "pr-chain-board", "loop": "pr-chain-loop", "janitor": "worktree-janitor"}
USAGE = """usage: pr-chain <command> [args]

  init [--force]       write ~/.config/pr-chain/pr-chain.toml and check gh, git and an agent CLI
  pick                 read-only: which PR would run next
  run <pr> [--land] [--dry-run]   walk one PR through the chain
  board [port]         the read-only board (default http://127.0.0.1:8790)
  loop                 run continuously
  janitor              remove worktrees of finished PRs
  path                 print where the kit's files live
  <other>              any bin/pr-chain subcommand (pr-chain help lists them)
"""


def ask(label, default=""):
    v = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return v or default


def init(args):
    sys.path.insert(0, str(KIT / "chain"))
    from config import USER_CFG  # noqa: E402
    if USER_CFG.exists() and "--force" not in args:
        sys.exit(f"{USER_CFG} already exists; edit it, or rerun with --force to replace it")
    print("pr-chain needs two GitHub accounts: one opens PRs, the other approves and merges them.")
    slug = ask("GitHub repo (owner/name)")
    checkout = ask("Local checkout of that repo", f"~/dev/{slug.split('/')[-1]}" if "/" in slug else "")
    author = ask("Account that opens PRs (you)")
    merger = ask("Account that approves and merges (a bot or second account)")
    checks = ask("Required CI checks, comma separated", "ci/tests")
    if not (slug.count("/") == 1 and checkout and author and merger):
        sys.exit("repo, checkout and both accounts are required")
    text = (KIT / "pr-chain.toml").read_text()
    q = lambda xs: "[" + ", ".join(f'"{x.strip()}"' for x in xs if x.strip()) + "]"
    for a, b in (('slug = "owner/name"', f'slug = "{slug}"'), ('checkout = "~/dev/name"', f'checkout = "{checkout}"'),
                 ('authors = ["you", "your-bot"]', f"authors = {q([author, merger])}"), ('trusted = ["you"]', f"trusted = {q([author])}"),
                 ('merger = "your-bot"', f'merger = "{merger}"'),
                 ('approver_of = { "your-bot" = "you" }', f'approver_of = {{ "{merger}" = "{author}", "{author}" = "{merger}" }}'),
                 ('checks = ["ci/tests"]', f"checks = {q(checks.split(','))}"),
                 ('models_file = ""  ', f'models_file = "{USER_CFG.parent / "models.json"}"  ')):
        assert a in text, a
        text = text.replace(a, b, 1)
    USER_CFG.parent.mkdir(parents=True, exist_ok=True)
    for f in ("models.json", "commands.json", "profiles.json"):  # editable copies the board writes; they survive upgrades
        if not (USER_CFG.parent / f).exists():
            shutil.copy(KIT / "chain" / f, USER_CFG.parent / f)
    USER_CFG.write_text(text)
    print(f"wrote {USER_CFG} (and models.json, commands.json, profiles.json beside it)")
    problems = [t for t in ("git", "gh") if not shutil.which(t)]
    if not (shutil.which("claude") or shutil.which("codex")):
        problems.append("claude or codex CLI")
    if shutil.which("gh") and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode:
        problems.append("gh is not logged in (gh auth login)")
    if not Path(checkout).expanduser().is_dir():
        problems.append(f"no checkout at {checkout} (git clone it there)")
    print("missing: " + "; ".join(problems) if problems else "gh, git and an agent CLI found")
    print("next: pr-chain pick")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd in ("", "-h", "--help"):
        print(USAGE)
        return
    if cmd == "init":
        return init(args[1:])
    if cmd == "path":
        print(KIT)
        return
    script = KIT / "bin" / SCRIPTS.get(cmd, "pr-chain")
    rest = args[1:] if cmd in SCRIPTS else args
    os.execv(sys.executable, [sys.executable, "-B", str(script), *rest])


if __name__ == "__main__":
    main()
