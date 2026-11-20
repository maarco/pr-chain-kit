"""Terminal screenshots for the PR chain: run kollab tmux specs with color captures and render every capture as a PNG.

Run through: uv run -q --no-project --with playwright==1.62.0 python -I term_shots.py --wt WT --out SHOTS --work DIR
  [--spec SPEC.json ...] [--main SHA --head SHA] [--python PY]
With no --spec it runs the tmux specs the PR adds or changes (git diff MAIN...HEAD -- tests/tmux/specs/*.json).
A screen is saved at every `capture` step and every `wait_for` that passes, as <out>/term-<spec>-<NN>-<label>.png.
The spec runs on a clean copy of WT (git archive), through the PR's own harness with its throwaway HOME, plus a hub-solo
app, no keyring, stub gh/codex/claude and a plain /bin/sh prompt. Captures are scrubbed (user, host and git identity,
replaced at the same length so columns stay put) and a PNG is written only when the scrubbed text holds none of them.
Exit 0: at least one PNG. 3: no spec to run. 4: specs ran, no PNG. 2: the tool itself failed (harness anchors moved).
Writes <work>/term-shots.json: per spec the harness exit code, its last lines, the PNGs and any screen dropped."""
import argparse
import getpass
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# --- the harness patch: three anchors in tests/tmux/lib/test_runner.sh's embedded Python --------------------------
HELPER = '''def _term_shot(label: str) -> None:
    out = os.environ.get("TERM_SHOT_DIR")
    if not out or not STARTED:
        return
    r = _tmux("capture-pane", "-e", "-t", SESSION_NAME, "-p", check=False)
    n = len(list(Path(out).glob("*.ansi"))) + 1
    slug = re.sub(r"[^a-z0-9]+", "-", str(label).lower()).strip("-")[:40] or "screen"
    Path(out, f"{n:02d}-{slug}.ansi").write_text(r.stdout or "", encoding="utf-8")


'''
A_DEF = "def _capture(step: dict[str, Any] | None = None) -> str:"
A_CAP = '    LAST_OUTPUT = result.stdout or ""\n    show = bool((step or {}).get("show", False)) or SHOW_CAPTURES'
A_WAIT = '        if re.search(pattern, LAST_OUTPUT, flags=re.IGNORECASE | re.MULTILINE):\n            print(f"[PASS] {label}")'
# tmux starts each pane as a LOGIN shell, and macOS's /etc/profile (path_helper) put /usr/bin in front of the shim, so a
# typed `python3` ran the system Python and the spec failed. The pane shell is a wrapper that execs a non-login bash.
TSH = '#!/bin/sh\nexec /bin/bash --noprofile --norc "$@"\n'  # the harness also runs `shell` steps as `$SHELL -c ...`
PROMPT = re.compile(r"^(bash-[\d.]+\$|\$)( |$)")


def patched_runner(src: Path) -> str:
    lines = (src / "tests/tmux/lib/test_runner.sh").read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, l in enumerate(lines) if "<<'PY'" in l)
    end = max(i for i, l in enumerate(lines) if l.strip() == "PY")
    code = "".join(lines[start + 1:end])
    for anchor in (A_DEF, A_CAP, A_WAIT):
        if code.count(anchor) != 1:
            raise RuntimeError(f"harness anchor not found once, the harness changed: {anchor.splitlines()[0]!r}")
    code = code.replace(A_DEF, HELPER + A_DEF)
    code = code.replace(A_CAP, A_CAP.replace("\n", '\n    _term_shot("capture")\n', 1))
    return code.replace(A_WAIT, A_WAIT.replace("\n", "\n            _term_shot(label)\n", 1))


# --- privacy: same-length replacements, then a hard check on the plain text ---------------------------------------
def identity_tokens():
    toks = {}
    def add(tok, word):
        if tok and len(tok) >= 4:
            toks[tok] = (word + "-" * len(tok))[:len(tok)]
    for k, word in (("user.email", "dev@example.test"), ("user.name", "Dev User")):
        r = subprocess.run(["git", "config", "--global", k], capture_output=True, text=True)
        add(r.stdout.strip(), word)
        if k == "user.name" and r.stdout.strip():
            add(r.stdout.split()[0], "Dev")
    for k in ("ComputerName", "LocalHostName"):
        r = subprocess.run(["scutil", "--get", k], capture_output=True, text=True)
        add(r.stdout.strip(), "studio-mac-mini-devbox-lab")
    host = socket.gethostname()
    add(host, "studio-mac-mini-devbox-lab")
    add(host.split(".")[0], "studio-mac-mini-devbox-lab")
    add(getpass.getuser(), "dev-user")
    return dict(sorted(toks.items(), key=lambda kv: -len(kv[0])))


def scrub(text, toks):
    for tok, rep in toks.items():
        text = re.sub(re.escape(tok), rep, text, flags=re.IGNORECASE)
    return text


def trim_harness_rows(text, width):
    """Drop the rows of the harness's own typed `env -u KOLLAB_... <command>` line. It wraps over several rows: a row
    belongs to it while the row before was full width (a wrap), and the first shorter row ends it."""
    keep, skipping, prev = [], False, ""
    for row in text.split("\n"):
        plain = SGR.sub("", row)
        if "env -u KOLLAB_" in plain or (skipping and len(prev) >= width and not PROMPT.match(plain)):
            skipping, prev = True, plain
            continue
        skipping, prev = False, plain
        keep.append(row)
    return "\n".join(keep)


# --- ANSI to HTML -------------------------------------------------------------------------------------------------
FG, BG = "#d4d7dc", "#0f1114"
BASE = ["#1c1f24", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd", "#56b6c2", "#d4d7dc",
        "#5c6370", "#ff7a85", "#b5e890", "#ffd88a", "#82c4ff", "#dd99ff", "#7fd6e0", "#ffffff"]
SGR = re.compile(r"(\x1b\[[0-9;]*m)")


def c256(n):
    if n < 16:
        return BASE[n]
    if n < 232:
        n -= 16
        v = [0, 95, 135, 175, 215, 255]
        return "#%02x%02x%02x" % (v[n // 36], v[n // 6 % 6], v[n % 6])
    g = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (g, g, g)


def to_html(text):
    off = {"fg": None, "bg": None, "b": False, "d": False, "i": False, "u": False, "r": False}
    st, out = dict(off), []
    for part in SGR.split(text):
        m = re.fullmatch(r"\x1b\[([0-9;]*)m", part)
        if m:
            codes = [int(c) if c else 0 for c in m.group(1).split(";")]
            k = 0
            while k < len(codes):
                c = codes[k]
                if c == 0: st = dict(off)
                elif c == 1: st["b"] = True
                elif c == 2: st["d"] = True
                elif c == 22: st["b"] = st["d"] = False
                elif c == 3: st["i"] = True
                elif c == 23: st["i"] = False
                elif c == 4: st["u"] = True
                elif c == 24: st["u"] = False
                elif c == 7: st["r"] = True
                elif c == 27: st["r"] = False
                elif 30 <= c <= 37: st["fg"] = BASE[c - 30]
                elif 90 <= c <= 97: st["fg"] = BASE[c - 82]
                elif c == 39: st["fg"] = None
                elif 40 <= c <= 47: st["bg"] = BASE[c - 40]
                elif 100 <= c <= 107: st["bg"] = BASE[c - 92]
                elif c == 49: st["bg"] = None
                elif c in (38, 48) and k + 1 < len(codes):
                    key = "fg" if c == 38 else "bg"
                    if codes[k + 1] == 5 and k + 2 < len(codes):
                        st[key] = c256(codes[k + 2]); k += 2
                    elif codes[k + 1] == 2 and k + 4 < len(codes):
                        st[key] = "#%02x%02x%02x" % tuple(codes[k + 2:k + 5]); k += 4
                k += 1
            continue
        if not part:
            continue
        fg, bg = st["fg"] or FG, st["bg"]
        if st["r"]:
            fg, bg = (st["bg"] or BG), (st["fg"] or FG)
        css = [f"color:{fg}"] + ([f"background:{bg}"] if bg else [])
        css += ["font-weight:700"] * st["b"] + ["opacity:.6"] * st["d"] + ["font-style:italic"] * st["i"]
        css += ["text-decoration:underline"] * st["u"]
        out.append(f'<span style="{";".join(css)}">{html.escape(part)}</span>')
    return "".join(out)


def page(title, text):
    return (f'<!doctype html><meta charset="utf-8"><style>body{{margin:0;background:#0b0c0e}}'
            f'.term{{display:inline-block;font:13px/1.3 Menlo,Monaco,monospace}}'
            f'.bar{{background:#1a1d21;color:#9aa1ab;padding:7px 14px;font:12px -apple-system,sans-serif}}'
            f'pre{{margin:0;padding:10px 14px;background:{BG};color:{FG};font:inherit;white-space:pre}}</style>'
            f'<div class="term"><div class="bar">{html.escape(title)}</div><pre>{to_html(text)}</pre></div>')


# --- run -------------------------------------------------------------------------------------------------------------
def run_spec(spec, wt, work, py, toks, browser, out):
    stem = re.sub(r"[^a-z0-9]+", "-", spec.stem.lower()).strip("-")
    width = int(json.loads(spec.read_text(encoding="utf-8")).get("config", {}).get("term_width", 120))
    run = work / stem
    shutil.rmtree(run, ignore_errors=True)
    src, bin_, ansi = run / "src", run / "bin", run / "ansi"
    for p in (src, bin_, ansi):
        p.mkdir(parents=True)
    arch = subprocess.run(["git", "-C", str(wt), "archive", "HEAD"], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(src)], input=arch.stdout, check=True)
    (run / "runner.py").write_text(patched_runner(src), encoding="utf-8")
    pp = ":".join([str(src)] + sorted(str(p) for p in (src / "packages").glob("*/src")))
    for name in ("python", "python3"):
        (bin_ / name).write_text(f'#!/bin/sh\nexec env PYTHONSAFEPATH=1 PYTHONPATH="{pp}" "{py}" "$@"\n')
    (bin_ / "tsh").write_text(TSH)
    for name in ("gh", "codex", "claude"):
        (bin_ / name).write_text(f'#!/bin/sh\necho "{name} is blocked in terminal screenshots" >&2\nexit 1\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    (run / "home").mkdir()
    env = {"PATH": f"{bin_}:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(run / "home"),
           "SHELL": str(bin_ / "tsh"), "PS1": "$ ", "TERM": "xterm-256color", "COLORTERM": "truecolor", "LANG": "en_US.UTF-8",
           "LC_ALL": "en_US.UTF-8", "USER": "dev-user", "LOGNAME": "dev-user", "TMPDIR": "/private/tmp",
           "KOLLAB_NO_KEYRING": "1", "KOLLAB_HUB_SOLO": "1", "BASH_SILENCE_DEPRECATION_WARNING": "1", "PYTHONDONTWRITEBYTECODE": "1", "TERM_SHOT_DIR": str(ansi),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        r = subprocess.run([str(bin_ / "python3"), str(run / "runner.py"), str(spec), str(src)], cwd=src, env=env,
                           capture_output=True, text=True, timeout=900)
        rc, log = r.returncode, (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        rc, log = 124, f"timed out after 900 s\n{(e.stdout or b'')[-2000:]!r}"
    (run / "harness.log").write_text(log, encoding="utf-8")
    shutil.rmtree(src, ignore_errors=True)
    pngs, dropped, last = [], [], None
    for f in sorted(ansi.glob("*.ansi")):
        text = trim_harness_rows(scrub(f.read_text(encoding="utf-8", errors="replace").rstrip("\n"), toks), width)
        plain = SGR.sub("", text)
        if plain == last:
            continue
        last = plain
        if any(t.lower() in plain.lower() for t in toks):
            dropped.append(f"{f.name}: identity text left after scrubbing")
            continue
        name = f"term-{stem}-{f.stem}.png"
        label = f.stem.split("-", 1)[1] if "-" in f.stem else f.stem
        (run / f"{f.stem}.html").write_text(page(f"kollab terminal · {spec.stem} · {label}", text), encoding="utf-8")
        pg = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        pg.goto((run / f"{f.stem}.html").as_uri())
        pg.locator(".term").screenshot(path=str(out / name))
        pg.close()
        pngs.append(name)
    return {"rc": rc, "tail": log.strip().splitlines()[-4:], "pngs": pngs, "dropped": dropped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--spec", action="append", default=[])
    ap.add_argument("--main")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--python", default=str(Path.home() / "dev/kollab/.venv/bin/python"))
    a = ap.parse_args()
    wt, out, work = Path(a.wt).resolve(), Path(a.out).resolve(), Path(a.work).resolve()
    specs = [Path(s).resolve() for s in a.spec]
    if not specs and a.main:
        r = subprocess.run(["git", "-C", str(wt), "diff", "--name-only", "--diff-filter=AM", f"{a.main}...{a.head}",
                            "--", "tests/tmux/specs/*.json"], capture_output=True, text=True, check=True)
        specs = [wt / p for p in r.stdout.split()]
    work.mkdir(parents=True, exist_ok=True)
    if not specs:
        (work / "term-shots.json").write_text(json.dumps({"specs": {}, "note": "no tmux spec to run"}, indent=1))
        print("term_shots: no tmux spec to run")
        return 3
    out.mkdir(parents=True, exist_ok=True)
    toks = identity_tokens()
    from playwright.sync_api import sync_playwright
    res = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for spec in specs:
            res[spec.name] = run_spec(spec, wt, work, a.python, toks, browser, out)
        browser.close()
    (work / "term-shots.json").write_text(json.dumps({"specs": res}, indent=1))
    n = sum(len(v["pngs"]) for v in res.values())
    for k, v in res.items():
        print(f"term_shots: {k}: harness exit {v['rc']}, {len(v['pngs'])} PNGs, {len(v['dropped'])} dropped")
    return 0 if n else 4


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # the tool, not the PR: the chain treats exit 2 like a broken stack
        print(f"term_shots: tool failure: {type(e).__name__}: {str(e)[:300]}")
        sys.exit(2)
