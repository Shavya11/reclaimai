"""Render the Part 3 code stills (C08-C14) at 1920x1080.

Every excerpt is sliced out of the real file at render time, so a still can
never drift from the code it claims to show. Change a line range here, re-run,
and the picture updates.
"""
import html, pathlib, subprocess
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).parent / "stills"
OUT.mkdir(exist_ok=True)


def slice_(rel, a, b):
    lines = (ROOT / rel).read_text(encoding="utf8").splitlines()
    return "\n".join(lines[a - 1:b])


def tree():
    return subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True
    ).stdout


REPO_TREE = """reclaim/
├── detectors/          failed_payments · abandoned_carts
│                       failed_mandates · overdue_invoices
├── brain/
│   ├── rules.py        THE single rule loader
│   ├── diagnosis/      deterministic · cohort · llm · receivables
│   ├── policy/         policies.yaml + engine.py
│   └── guardrails/     base.py + rules/ (one file per rule)
├── executor/           razorpay_client · actions · channels
├── webhooks/           signature · attribution
├── audit/              append-only decision log
├── experiments/        ablation.py
└── api/                FastAPI routes

tests/                  342 tests, 3 property invariants
ui/                     Next.js dashboard, 8 screens"""

CARDS = [
    ("C08", "reclaim/", "The layout — one plugin per leak type",
     REPO_TREE, "plaintext"),

    ("C09", "reclaim/diagnose/deterministic.py",
     "Layer 1 — free, instant, confidence 1.0",
     slice_("reclaim/diagnose/deterministic.py", 1, 12) + "\n\n" +
     slice_("reclaim/diagnose/deterministic.py", 18, 27), "python"),

    ("C10", "reclaim/diagnose/cohort.py",
     "Layer 1.5 — computed over the batch, before diagnosis",
     slice_("reclaim/diagnose/cohort.py", 1, 12) + "\n\n" +
     slice_("reclaim/diagnose/cohort.py", 17, 23), "python"),

    ("C11", "reclaim/diagnose/llm_diagnoser.py",
     "Layer 2 — the model, on the ambiguous rest only",
     slice_("reclaim/diagnose/llm_diagnoser.py", 1, 14) + "\n\n" +
     slice_("reclaim/diagnose/llm_diagnoser.py", 249, 258), "python"),

    ("C12", "reclaim/decide/policies.yaml",
     "Rules are data — this row is REC_5042's silent retry",
     slice_("reclaim/decide/policies.yaml", 1, 10) + "\n\n" +
     slice_("reclaim/decide/policies.yaml", 12, 22), "yaml"),

    ("C13", "reclaim/guardrails/base.py",
     "14 rules, one file each — and two non-negotiables",
     slice_("reclaim/guardrails/base.py", 1, 17), "python"),

    ("C14", "reclaim/models.py  ·  reclaim/execute/actions.py",
     "Derived, never passed in — and claimed BEFORE the charge",
     slice_("reclaim/models.py", 84, 88) + "\n\n\n" +
     slice_("reclaim/execute/actions.py", 1, 15), "python"),
]

STYLE = """
:root{--bg:#0b0d10;--panel:#14181d;--line:#262d36;--ink:#e8edf3;
      --muted:#a4b0be;--dim:#8b97a6;--green:#34d399;--amber:#fbbf24;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:#333;font-family:'Plus Jakarta Sans',system-ui,sans-serif}
.card{width:1920px;height:1080px;background:var(--bg);color:var(--ink);
      padding:64px 80px;display:flex;flex-direction:column;overflow:hidden}
.hdr{display:flex;align-items:baseline;gap:24px;
     border-bottom:1px solid var(--line);padding-bottom:22px;margin-bottom:30px}
.path{font-family:'JetBrains Mono',monospace;font-size:26px;font-weight:600;
      color:var(--green)}
.note{font-size:22px;color:var(--muted);margin-left:auto;font-weight:500}
pre{flex:1;overflow:hidden}
code{font-family:'JetBrains Mono',monospace;font-size:23px;line-height:1.62;
     white-space:pre;display:block;background:none!important;padding:0!important}
.hljs-comment,.hljs-quote{color:#7d8b9a;font-style:italic}
.hljs-string,.hljs-attr{color:#7fd6a8}
.hljs-keyword,.hljs-built_in{color:#c9a5f5}
.hljs-number,.hljs-literal{color:#fbbf24}
.hljs-title,.hljs-class .hljs-title,.hljs-function .hljs-title{color:#7cc4ff}
"""

doc = ["<meta charset='utf-8'>",
       "<link href='https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:"
       "wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap'"
       " rel='stylesheet'>",
       "<link rel='stylesheet' href='https://cdnjs.cloudflare.com/ajax/libs/"
       "highlight.js/11.9.0/styles/base16/tomorrow-night.min.css'>",
       "<script src='https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/"
       "highlight.min.js'></script>",
       f"<style>{STYLE}</style>"]

for cid, path, note, code, lang in CARDS:
    doc.append(
        f"<div class='card' id='{cid}'>"
        f"<div class='hdr'><span class='path'>{html.escape(path)}</span>"
        f"<span class='note'>{html.escape(note)}</span></div>"
        f"<pre><code class='language-{lang}'>{html.escape(code)}</code></pre>"
        f"</div>")
doc.append("<script>hljs.highlightAll();</script>")

page = pathlib.Path(__file__).parent / "code_cards.html"
page.write_text("\n".join(doc), encoding="utf8")

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    pg.goto(page.as_uri())
    pg.wait_for_timeout(3000)
    for cid, *_ in CARDS:
        pg.locator(f"#{cid}").screenshot(path=str(OUT / f"code-{cid}.png"))
        print("wrote code-%s.png" % cid)
    b.close()
