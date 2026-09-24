"""Record the Part 4 dashboard clips (C15-C22) as real 1920x1080 video.

  python video/dash_clips.py            # all
  python video/dash_clips.py C18 C21    # just these

Each clip opens its own browser context so one failure cannot poison the rest.
Clips are recorded ~20% longer than the narration target; build.py trims them
to the exact voice length, so a slightly slow page never truncates a sentence.
"""
import pathlib, shutil, sys, time
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = pathlib.Path(__file__).parent / "clips"
OUT.mkdir(exist_ok=True)
RAW = OUT / "_raw"

TARGET = {"C15": 38, "C16": 40, "C17": 28, "C18": 56,
          "C19": 62, "C20": 23, "C21": 56, "C22": 39}
OVERSHOOT = 1.2


def glide(pg, total_px, seconds, step=18):
    """Scroll total_px over `seconds`, in small steps so it reads as a glide."""
    n = max(1, abs(total_px) // step)
    per = max(0.012, seconds / n)
    sign = 1 if total_px >= 0 else -1
    for _ in range(n):
        pg.mouse.wheel(0, sign * step)
        pg.wait_for_timeout(int(per * 1000))


def nav(pg, label):
    pg.get_by_role("button", name=label, exact=False).first.click()
    pg.wait_for_timeout(900)


# --------------------------------------------------------------- the clips --
def c15(pg):                                    # Dashboard, top to bottom
    pg.wait_for_timeout(3200)
    glide(pg, 1500, 26)
    pg.wait_for_timeout(2500)


def c16(pg):                                    # Try it - preview writes nothing
    nav(pg, "Try it")
    pg.wait_for_timeout(3500)
    glide(pg, 500, 8)
    pg.wait_for_timeout(1200)
    for sel in ("button:has-text('Preview')", "button:has-text('preview')"):
        b = pg.locator(sel).first
        if b.count():
            b.click(); break
    pg.wait_for_timeout(4000)
    glide(pg, 900, 14)
    pg.wait_for_timeout(3000)


def c17(pg):                                    # Recovery queue
    nav(pg, "Recovery queue")
    pg.wait_for_timeout(2600)
    glide(pg, 1400, 22)
    pg.wait_for_timeout(2000)


def c18(pg):                                    # Audit trail for REC_5001
    pg.wait_for_timeout(1500)
    box = pg.get_by_placeholder("Search record").first
    box.click()
    box.type("REC_5001", delay=110)
    pg.wait_for_timeout(2200)
    row = pg.get_by_text("REC_5001", exact=False).last
    row.click()
    pg.wait_for_timeout(4000)
    glide(pg, 1100, 30)
    pg.wait_for_timeout(6000)


def c19(pg):                                    # Promises & replies
    nav(pg, "Promises")
    pg.wait_for_timeout(3000)
    glide(pg, 1500, 34)
    pg.wait_for_timeout(6000)


def c20(pg):                                    # Human queue
    nav(pg, "Human queue")
    pg.wait_for_timeout(2600)
    glide(pg, 1100, 17)
    pg.wait_for_timeout(2000)


def c21(pg):                                    # Rules studio: edit -> replay -> refused
    # The studio keeps a server-side draft, so a previous take would leave the
    # ceiling already at 75,000 and the "raise it from fifty thousand" beat
    # would be a lie on screen. Restore the snapshot before recording this one
    # (see record_c21_prep) rather than hitting /api/admin/reset, which writes
    # its own row into the change history the clip is about to show.
    nav(pg, "Rules studio")
    pg.wait_for_timeout(2800)

    # fill() not type(): typing char-by-char leaves React's state unset, so the
    # Replay button stays disabled and the click times out.
    def edit_then_replay(idx, value, settle):
        pg.mouse.wheel(0, -2000)             # come back up to the rule table
        pg.wait_for_timeout(900)
        box = pg.locator("input[type=number]").nth(idx)
        box.scroll_into_view_if_needed()
        box.click()
        pg.wait_for_timeout(700)
        box.fill(value)
        box.press("Tab")                     # blur so React commits the change
        btn = pg.get_by_role("button", name="Replay this batch")
        btn.scroll_into_view_if_needed()
        for _ in range(20):                  # the panel re-renders after a replay
            if not btn.is_disabled():
                break
            pg.wait_for_timeout(250)
        if btn.is_disabled():
            print("    (replay still disabled after editing input %d)" % idx)
            return
        pg.wait_for_timeout(900)
        btn.click()
        pg.wait_for_timeout(settle)

    edit_then_replay(0, "75000", 11000)     # ceiling 50k -> 75k, the real trade
    glide(pg, 700, 12)
    pg.wait_for_timeout(3000)

    # The refusal beat is "Apply for real", not Replay: replay is a sandbox and
    # takes anything, while apply is validated by the same code that consumes
    # the rule. 99 contacts comes back 422 with the reason.
    pg.mouse.wheel(0, -2000)
    pg.wait_for_timeout(900)
    box = pg.locator("input[type=number]").nth(1)
    box.scroll_into_view_if_needed()
    box.click()
    pg.wait_for_timeout(600)
    box.fill("99")
    box.press("Tab")
    pg.wait_for_timeout(1500)
    pg.get_by_role("button", name="Apply for real").click()
    pg.wait_for_timeout(6000)               # linger on the refusal message


def c22(pg):                                    # Evidence
    nav(pg, "Evidence")
    pg.wait_for_timeout(3000)
    glide(pg, 1600, 28)
    pg.wait_for_timeout(3000)


CLIPS = {"C15": c15, "C16": c16, "C17": c17, "C18": c18,
         "C19": c19, "C20": c20, "C21": c21, "C22": c22}


def record(name, fn):
    if RAW.exists():
        shutil.rmtree(RAW, ignore_errors=True)
    RAW.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1920, "height": 1080},
                            record_video_dir=str(RAW),
                            record_video_size={"width": 1920, "height": 1080})
        pg = ctx.new_page()
        pg.goto(BASE, wait_until="networkidle")
        try:
            fn(pg)
            ok = True
        except Exception as e:
            print(f"  {name}: interaction failed - {str(e)[:120]}")
            ok = False
        ctx.close()
        b.close()
    vids = list(RAW.glob("*.webm"))
    if not vids:
        print(f"  {name}: no video produced"); return
    dest = OUT / f"{name}.webm"
    if dest.exists():
        dest.unlink()
    shutil.move(str(vids[0]), str(dest))
    shutil.rmtree(RAW, ignore_errors=True)
    print(f"  {name}: {time.time()-t0:.1f}s recorded "
          f"(target {TARGET[name]}s){'' if ok else '  [PARTIAL]'} -> {dest.name}")


if __name__ == "__main__":
    want = [a.upper() for a in sys.argv[1:]] or list(CLIPS)
    for n in want:
        if n in CLIPS:
            record(n, CLIPS[n])
