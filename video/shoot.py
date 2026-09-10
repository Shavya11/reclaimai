"""Render the static video cards to 1920x1080 PNGs."""
import pathlib, sys
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
OUT = HERE / "stills"
OUT.mkdir(exist_ok=True)
CARDS = {"c1": "01-problem", "c2": "02-architecture", "c3": "03-conclusion"}

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    pg.goto((HERE / "cards.html").as_uri())
    pg.wait_for_timeout(2500)          # webfonts
    for sel, name in CARDS.items():
        pg.locator(f"#{sel}").screenshot(path=str(OUT / f"{name}.png"))
        print("wrote", name + ".png")
    b.close()
