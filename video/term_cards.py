"""Render the Part 5 terminal stills (C23-C25) from real captured output.

The text comes from video/cap_*.txt, written by actually running the commands.
ANSI colour is translated to spans rather than stripped, so the PASS/FAIL
colouring the CLI produces survives onto the screen.
"""
import html, pathlib, re
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).parent
OUT = HERE / "stills"
OUT.mkdir(exist_ok=True)

ANSI = {"31": "r", "32": "g", "33": "y", "36": "c", "1": "b", "2": "d"}


def render(text):
    """ANSI -> spans. Handles the subset the CLI emits."""
    out, open_n = [], 0
    for chunk in re.split(r"(\x1b\[[0-9;]*m)", text):
        m = re.fullmatch(r"\x1b\[([0-9;]*)m", chunk)
        if not m:
            out.append(html.escape(chunk))
            continue
        codes = [c for c in m.group(1).split(";") if c]
        if not codes or codes == ["0"]:
            out.append("</span>" * open_n)
            open_n = 0
            continue
        cls = " ".join(ANSI[c] for c in codes if c in ANSI)
        if cls:
            out.append(f'<span class="{cls}">')
            open_n += 1
    return "".join(out) + "</span>" * open_n


def cap(name):
    p = HERE / f"cap_{name}.txt"
    return p.read_text(encoding="utf8", errors="replace") if p.exists() else ""


CARDS = [
    ("C23", "The receipts",
     [("pytest -q", cap("pytest")),
      ("reclaim verify", cap("verify"))]),
    ("C24", "Razorpay unreachable, and a crash mid-batch",
     [("reclaim run-batch --kill-razorpay", cap("kill")),
      ("reclaim prove-idempotency", cap("idem"))]),
    ("C25", "The baseline, including where we lose",
     [("reclaim baseline", cap("baseline"))]),
]

STYLE = """
:root{--bg:#0b0d10;--ink:#d8e0e8;--dim:#8b97a6;--green:#34d399;--amber:#fbbf24;--red:#f87171}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#333;font-family:'JetBrains Mono',ui-monospace,monospace}
.card{width:1920px;height:1080px;background:var(--bg);color:var(--ink);
      padding:52px 66px;display:flex;flex-direction:column;overflow:hidden}
.hdr{display:flex;align-items:baseline;border-bottom:1px solid #262d36;
     padding-bottom:18px;margin-bottom:26px}
.hdr .t{font-family:'Plus Jakarta Sans',system-ui,sans-serif;font-size:25px;
        font-weight:600;color:var(--dim);margin-left:auto}
.hdr .dot{width:13px;height:13px;border-radius:50%;margin-right:9px;display:inline-block}
.prompt{color:var(--green);font-size:24px;font-weight:600;margin:22px 0 10px}
.prompt .sig{color:var(--dim)}
pre{font-size:20.5px;line-height:1.52;white-space:pre-wrap;word-break:break-word}
.g{color:var(--green)} .r{color:var(--red)} .y{color:var(--amber)}
.c{color:#7cc4ff} .b{font-weight:700;color:#fff} .d{color:var(--dim)}
"""

doc = ["<meta charset='utf-8'>",
       "<link href='https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:"
       "wght@500;600&family=JetBrains+Mono:wght@400;600;700&display=swap' rel='stylesheet'>",
       f"<style>{STYLE}</style>"]

for cid, title, blocks in CARDS:
    body = "".join(
        f"<div class='prompt'><span class='sig'>$</span> {html.escape(cmd)}</div>"
        f"<pre>{render(text)}</pre>"
        for cmd, text in blocks)
    doc.append(
        f"<div class='card' id='{cid}'><div class='hdr'>"
        f"<span class='dot' style='background:#f87171'></span>"
        f"<span class='dot' style='background:#fbbf24'></span>"
        f"<span class='dot' style='background:#34d399'></span>"
        f"<span class='t'>{html.escape(title)}</span></div>{body}</div>")

page = HERE / "term_cards.html"
page.write_text("\n".join(doc), encoding="utf8")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto(page.as_uri())
    pg.wait_for_timeout(2600)
    for cid, *_ in CARDS:
        pg.locator(f"#{cid}").screenshot(path=str(OUT / f"term-{cid}.png"))
        print("wrote term-%s.png" % cid)
    b.close()
