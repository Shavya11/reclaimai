"""Assemble narrated clips into parts, and parts into the finished video.

  python video/build.py part1          # build one part
  python video/build.py C01            # build a single clip
  python video/build.py final          # concat every part built so far

Each clip is an image + your voice track. Dashboard shots pan down a tall
full-page capture so the motion matches the narration; stills get a slow push.
Audio duration drives clip length - the voice is never cut to fit the picture.
"""
import glob, json, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).parent
STILLS, VOICE, OUT = HERE / "stills", HERE / "voice", HERE / "out"
OUT.mkdir(exist_ok=True)

import imageio_ffmpeg
FF = imageio_ffmpeg.get_ffmpeg_exe()

W, H, FPS = 1920, 1080, 30

# kind: "push" = slow zoom on a 1920x1080 still
#       "pan"  = scroll a tall capture from y0 to y1
SHOTS = {
    "C01": dict(kind="push", img="01-problem.png"),
    "C02": dict(kind="pan",  img="dash-full.png", y0=0,  y1=90),
    "C03": dict(kind="pan",  img="dash-full.png", y0=90, y1=800),
    # The architecture card revealed in four stages, so 80s of narration is not
    # 80s of one static picture. Regenerate with video/arch_stages.py.
    "C04": dict(kind="push", img="02-architecture-1.png"),
    "C05": dict(kind="push", img="02-architecture-2.png"),
    "C06": dict(kind="push", img="02-architecture-3.png"),
    "C07": dict(kind="push", img="02-architecture-4.png"),
    # Part 3 - code stills, sliced from the real files by video/code_cards.py
    "C08": dict(kind="push", img="code-C08.png"),
    "C09": dict(kind="push", img="code-C09.png"),
    "C10": dict(kind="push", img="code-C10.png"),
    "C11": dict(kind="push", img="code-C11.png"),
    "C12": dict(kind="push", img="code-C12.png"),
    "C13": dict(kind="push", img="code-C13.png"),
    "C14": dict(kind="push", img="code-C14.png"),
    # Part 4 - real screen recordings of the live dashboard (video/dash_clips.py).
    # Several voice takes run longer than their recording, so these are stretched
    # to the narration rather than the narration being cut to the picture.
    "C15": dict(kind="clip", src="C15.webm"),
    "C16": dict(kind="clip", src="C16.webm"),
    "C17": dict(kind="clip", src="C17.webm"),
    "C18": dict(kind="clip", src="C18.webm"),
    "C19": dict(kind="clip", src="C19.webm"),
    "C20": dict(kind="clip", src="C20.webm", note="note-c20.png"),
    "C21": dict(kind="clip", src="C21.webm"),
    "C22": dict(kind="clip", src="C22.webm"),
    # Part 5 - terminal stills rendered from real captured output (term_cards.py)
    "C23": dict(kind="push", img="term-C23.png", note="note-c23.png"),
    "C24": dict(kind="push", img="term-C24.png"),
    "C25": dict(kind="push", img="term-C25.png", note="note-c25.png"),
    "C26": dict(kind="push", img="03-conclusion.png"),
}

PARTS = {
    "part1": ["C01", "C02", "C03"],
    "part2": ["C04", "C05", "C06", "C07"],
    "part3": ["C08", "C09", "C10", "C11", "C12", "C13", "C14"],
    "part4": ["C15", "C16", "C17", "C18", "C19", "C20", "C21", "C22"],
    "part5": ["C23", "C24", "C25"],
    "part6": ["C26"],
}


def audio_for(clip):
    hits = sorted(glob.glob(str(VOICE / f"{clip}.*")))
    return hits[0] if hits else None


def duration(path):
    out = subprocess.run([FF, "-i", str(path), "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def build_screen_clip(clip, shot, aud, dur):
    """A recorded dashboard clip, retimed to the narration.

    Video shorter than voice is the normal case here - the browser finished
    scrolling before the sentence did. Stretching with setpts keeps the motion
    smooth; freezing the tail would read as the page having hung.
    """
    src = HERE / "clips" / shot["src"]
    if not src.exists():
        print(f"  {clip}: missing {shot['src']} - skipping"); return None
    vdur = duration(src)
    total = round(dur + 0.35, 2)
    factor = total / vdur if vdur else 1.0

    chain = [f"setpts={factor:.5f}*PTS", f"fps={FPS}", f"scale={W}:{H}"]
    inputs = ["-i", str(src), "-i", str(aud)]
    note = shot.get("note")
    if note and (STILLS / note).exists():
        inputs = ["-i", str(src), "-i", str(aud), "-i", str(STILLS / note)]
        vf = (f"[0:v]{','.join(chain)},format=yuv420p[base];"
              f"[base][2:v]overlay=(W-w)/2:H-h-48[v];")
    else:
        vf = f"[0:v]{','.join(chain)},format=yuv420p[v];"
    vf += (f"[1:a]apad=pad_dur=0.35,aresample=48000,"
           f"aformat=sample_fmts=fltp:channel_layouts=stereo[a]")

    dest = OUT / f"{clip}.mp4"
    cmd = [FF, "-y", *inputs, "-filter_complex", vf,
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-t", str(total), str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(f"  {clip}: FAILED\n{r.stderr[-700:]}"); return None
    print(f"  {clip}: {total:.1f}s  (video {vdur:.1f}s x{factor:.2f})"
          f"{'  +note' if note else ''} -> {dest.name}")
    return dest


def build_clip(clip):
    shot, aud = SHOTS.get(clip), audio_for(clip)
    if not shot:
        print(f"  {clip}: no shot defined - skipping"); return None
    if not aud:
        print(f"  {clip}: no voice take yet - skipping"); return None

    dur = duration(aud)
    if shot["kind"] == "clip":
        return build_screen_clip(clip, shot, aud, dur)

    img = STILLS / shot["img"]
    if not img.exists():
        print(f"  {clip}: missing {img.name} - skipping"); return None
    pad = 0.35                                  # breath at the tail
    total = round(dur + pad, 2)

    if shot["kind"] == "pan":
        y0, y1 = shot["y0"], shot["y1"]
        vf = (f"crop={W}:{H}:0:'{y0}+({y1}-{y0})*min(t/{total},1)',"
              f"format=yuv420p,fps={FPS}")
    else:
        vf = (f"scale={W*2}:{H*2},"
              f"zoompan=z='1+0.045*(on/({FPS}*{total}))'"
              f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
              f":d=1:s={W}x{H}:fps={FPS},fps={FPS},format=yuv420p")

    # -framerate must be set on the image input: image2 defaults to 25fps, so
    # a 30fps output would silently come out short and the concat would then
    # start the next clip early, mid-sentence.
    dest = OUT / f"{clip}.mp4"
    extra, note = [], shot.get("note")
    if note and (STILLS / note).exists():
        extra = ["-i", str(STILLS / note)]
        chain = f"[0:v]{vf}[base];[base][2:v]overlay=(W-w)/2:H-h-48[v];"
    else:
        chain = f"[0:v]{vf}[v];"
    cmd = [FF, "-y", "-framerate", str(FPS), "-loop", "1",
           "-t", str(total), "-i", str(img),
           "-i", str(aud), *extra,
           "-filter_complex", chain + f"[1:a]apad=pad_dur={pad},"
                              f"aresample=48000,aformat=sample_fmts=fltp:"
                              f"channel_layouts=stereo[a]",
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-c:a", "aac", "-b:a", "192k", "-t", str(total), str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(f"  {clip}: FAILED\n{r.stderr[-700:]}"); return None
    print(f"  {clip}: {total:.1f}s -> {dest.name}")
    return dest


def concat(clips, name):
    made = [OUT / f"{c}.mp4" for c in clips if (OUT / f"{c}.mp4").exists()]
    if not made:
        print("nothing to concat"); return
    lst = OUT / f"{name}.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in made), encoding="utf8")
    dest = OUT / f"{name}.mp4"
    r = subprocess.run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-c", "copy", str(dest)], capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-700:]); return
    print(f"\n=> {dest}   ({len(made)} clips, {duration(dest):.1f}s)")


if __name__ == "__main__":
    what = (sys.argv[1] if len(sys.argv) > 1 else "part1").lower()
    if what == "final":
        order = [c for p in ("part1", "part2", "part3", "part4", "part5", "part6")
                 for c in PARTS[p]]
        concat(order, "final")
    elif what in PARTS:
        print(f"building {what}:")
        for c in PARTS[what]:
            build_clip(c)
        concat(PARTS[what], what)
    else:
        build_clip(what.upper())
