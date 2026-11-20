"""Screenshot routes at 1440x900, 820x1180 and 390x844, dark and light, and measure horizontal overflow.
Run through: uv run -q --no-project --with playwright==1.62.0 python -I ui_shoot.py --url U --routes /,/x --out DIR
Copy this file next to your shots and edit it when a state needs clicks (open a menu, type, scroll): the page object is `pg`.
Writes <slug>-<size>-<theme>.png and overflow.json ({"<slug>-<size>-<theme>": true when the page scrolls sideways})."""
import argparse
import json
import re
from pathlib import Path
from playwright.sync_api import sync_playwright

SIZES = {"desktop": (1440, 900), "tablet": (820, 1180), "phone": (390, 844)}

ap = argparse.ArgumentParser()
ap.add_argument("--url", required=True)
ap.add_argument("--routes", default="/")
ap.add_argument("--out", required=True)
a = ap.parse_args()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
overflow = {}
with sync_playwright() as p:
    br = p.chromium.launch()
    for route in [r for r in a.routes.split(",") if r]:
        slug = re.sub(r"[^a-z0-9]+", "-", route.lower()).strip("-") or "home"
        for size, (w, h) in SIZES.items():
            for theme in ("dark", "light"):
                ctx = br.new_context(viewport={"width": w, "height": h}, color_scheme=theme, has_touch=(size == "phone"))
                pg = ctx.new_page()
                pg.goto(a.url + route, wait_until="load")
                pg.wait_for_timeout(1500)
                pg.evaluate(f"document.documentElement.classList.toggle('dark', {str(theme == 'dark').lower()})")
                pg.wait_for_timeout(300)
                key = f"{slug}-{size}-{theme}"
                overflow[key] = pg.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                pg.screenshot(path=str(out / f"{key}.png"))
                ctx.close()
    br.close()
(out / "overflow.json").write_text(json.dumps(overflow, indent=1))
print(json.dumps({"shots": len(overflow), "overflowing": [k for k, v in overflow.items() if v]}))
