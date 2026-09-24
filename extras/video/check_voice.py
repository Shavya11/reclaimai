"""Check recorded VO takes against their target durations.

Usage:  python video/check_voice.py [C01 C02 ...]      (default: all present)

Stdlib only. Reads duration straight out of the container:
  .m4a/.mp4/.mov -> the mvhd atom
  .wav           -> the wave module
There is no ffmpeg with audio decoders on this machine, so LEVEL/clipping
cannot be measured - duration and presence are what this checks.
"""
import glob, pathlib, struct, sys, wave

HERE = pathlib.Path(__file__).parent
VOICE = HERE / "voice"

TARGET = {  # seconds, from VIDEO.md
    "C01": 23, "C02": 17, "C03": 20, "C04": 17, "C05": 25, "C06": 23, "C07": 15,
    # C14 was rewritten to add the claim-before-charge ordering: 32s, not 22s.
    "C08": 15, "C09": 27, "C10": 17, "C11": 30, "C12": 16, "C13": 23, "C14": 32,
    "C15": 38, "C16": 40, "C17": 28, "C18": 56, "C19": 62, "C20": 23, "C21": 56,
    "C22": 39, "C23": 22, "C24": 33, "C25": 38, "C26": 60,
}


def _find_mvhd(f, end, depth=0):
    """Walk the MP4 atom tree for mvhd and return (timescale, duration)."""
    while f.tell() < end and depth < 6:
        head = f.read(8)
        if len(head) < 8:
            return None
        size, kind = struct.unpack(">I4s", head)
        body = f.tell()
        if size == 1:                       # 64-bit extended size
            size = struct.unpack(">Q", f.read(8))[0]
            body = f.tell()
            payload = size - 16
        else:
            payload = size - 8
        if size == 0 or payload < 0:
            return None
        if kind == b"mvhd":
            ver = f.read(1)[0]
            f.read(3)                        # flags
            if ver == 1:
                f.read(16)                   # creation + modification (8+8)
                ts = struct.unpack(">I", f.read(4))[0]
                dur = struct.unpack(">Q", f.read(8))[0]
            else:
                f.read(8)                    # creation + modification (4+4)
                ts, dur = struct.unpack(">II", f.read(8))
            return ts, dur
        if kind in (b"moov", b"trak", b"mdia"):
            got = _find_mvhd(f, body + payload, depth + 1)
            if got:
                return got
        f.seek(body + payload)
    return None


def duration(path):
    p = pathlib.Path(path)
    if p.suffix.lower() == ".wav":
        with wave.open(str(p)) as w:
            return w.getnframes() / float(w.getframerate())
    with open(p, "rb") as f:
        got = _find_mvhd(f, p.stat().st_size)
    if not got:
        return None
    ts, dur = got
    return dur / ts if ts else None


def main(which):
    rows = []
    for clip in which:
        hits = sorted(glob.glob(str(VOICE / f"{clip}.*")))
        if not hits:
            rows.append((clip, "--", "MISSING", "")); continue
        try:
            secs = duration(hits[0])
        except Exception as e:
            rows.append((clip, "--", "UNREADABLE", str(e)[:40])); continue
        if secs is None:
            rows.append((clip, "--", "NO DURATION", "container not parsed")); continue
        tgt = TARGET[clip]
        drift = (secs - tgt) / tgt * 100
        if abs(drift) <= 12:
            verdict = "good"
        elif abs(drift) <= 25:
            verdict = "usable"
        else:
            verdict = "RE-RECORD"
        rows.append((clip, f"{secs:.1f}s", verdict,
                     f"target {tgt}s  ({drift:+.0f}%)"))
    for c, d, v, n in rows:
        print(f"{c:<4} {d:>7}  {v:<12} {n}")


if __name__ == "__main__":
    main([a.upper() for a in sys.argv[1:]] or sorted(TARGET))
