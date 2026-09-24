"""Render lower-third note cards overlaid onto clips."""
import pathlib
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(__file__).parent / "stills"
NOTES = {
    "note-c20.png":
        "Note \u00b7 the sidebar reads 51 here. One settled record (INV_7059) had "
        "not yet left the queue \u2014 50 are genuinely escalated. "
        "<b>cli verify</b> catches it as a failing check.",
    "note-c23.png":
        "Correction · the suite is <b>402</b> tests, not 342, and "
        "<b>verify</b> runs <b>28</b> checks — 27 pass, 1 fails on the "
        "settled record above. Both numbers grew after the narration was written.",
    "note-c25.png":
        "Correction · this run shows <b>169</b> contacts, and the part where "
        "our strategy was simply worse is <b>₹2,47,747 across 7 records</b> — "
        "not the ₹1,710 quoted. The larger figure is the honest one.",
}
CSS = """
body{margin:0;background:transparent;font-family:'Plus Jakarta Sans',system-ui,sans-serif}
.note{display:inline-block;max-width:1500px;background:rgba(16,21,28,.93);
      color:#e8edf3;font-size:25px;line-height:1.5;font-weight:500;
      padding:20px 30px;border-radius:12px;border-left:5px solid #fbbf24}
.note b{color:#fbbf24;font-family:'JetBrains Mono',monospace;font-weight:600}
"""
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 300})
    for name, text in NOTES.items():
        pg.set_content(
            "<link href='https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:"
            "wght@400;500;600&family=JetBrains+Mono:wght@500;600&display=swap' rel='stylesheet'>"
            f"<style>{CSS}</style><div class='note'>{text}</div>")
        pg.wait_for_timeout(2200)
        pg.locator(".note").screenshot(path=str(OUT / name), omit_background=True)
        print("wrote", name)
    b.close()
