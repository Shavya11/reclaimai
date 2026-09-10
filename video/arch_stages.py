"""Render the architecture card as four progressive reveals (C04-C07)."""
import pathlib
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
OUT = HERE / "stills"; OUT.mkdir(exist_ok=True)
DIM = "opacity:.16;filter:saturate(.2)"

STAGES = {
    1: f"#c2 .zoom{{{DIM}}} #c2 .foot{{{DIM}}}",
    2: f"#c2 .pipe{{opacity:.34}} #c2 .zoom .box:not(.model){{{DIM}}} "
       f"#c2 .arrow{{{DIM}}} #c2 .foot{{{DIM}}}",
    3: f"#c2 .pipe{{opacity:.34}} #c2 .box.model{{opacity:.34}} #c2 .foot{{{DIM}}}",
    4: "#c2 .pipe{opacity:.34} #c2 .zoom{opacity:.42}",
}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto((HERE / "cards.html").as_uri())
    pg.wait_for_timeout(2500)
    for n, css in STAGES.items():
        h = pg.add_style_tag(content=css)
        pg.wait_for_timeout(250)
        pg.locator("#c2").screenshot(path=str(OUT / f"02-architecture-{n}.png"))
        pg.evaluate("el => el.remove()", h)
        print("wrote 02-architecture-%d.png" % n)
    b.close()
