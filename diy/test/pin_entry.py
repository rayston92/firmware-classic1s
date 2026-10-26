"""Enter a PIN on the wallet's own screen (digit wheel), for tests.

  from pin_entry import enter_pin; enter_pin(outdir, "2903")

The wheel starts at a random digit; the digit under the cursor is read from
the screenshot by matching against the glyphs in pin_digits/ (learned from
real screens). 'up' lowers the digit by one, 'right' accepts it."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BAND = (0, 56, 256, 88)  # rows that hold the wheel digit


def _bits(im, box):
    return tuple(1 if p > 60 else 0 for p in im.convert("L").crop(box).getdata())


def learn(paths_digits):
    os.makedirs(os.path.join(HERE, "pin_digits"), exist_ok=True)
    for path, d in paths_digits:
        im = Image.open(path).convert("L").crop(BAND)
        im.save(os.path.join(HERE, "pin_digits", f"{d}.png"))


def _templates():
    t = {}
    for d in range(10):
        p = os.path.join(HERE, "pin_digits", f"{d}.png")
        if os.path.exists(p):
            t[d] = Image.open(p).convert("L")
    return t


def read_digit(path):
    """Return (digit, x) of the wheel digit, comparing glyph columns."""
    im = Image.open(path).convert("L").crop(BAND)
    w, h = im.size
    px = im.load()
    best = None
    for d, tim in _templates().items():
        tp = tim.load()
        # glyph = columns of the template with ink, near its arrow column
        cols = [x for x in range(w) if any(tp[x, y] > 60 for y in range(h))]
        if not cols:
            continue
        x0, x1 = min(cols), min(cols) + 12
        glyph = [[1 if tp[x, y] > 60 else 0 for y in range(h)] for x in range(x0, min(x1, w))]
        for off in range(0, w - len(glyph)):
            ok = True
            for i, col in enumerate(glyph):
                for y in range(h):
                    if (1 if px[off + i, y] > 60 else 0) != col[y]:
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return d, off
    return None, None


def enter_pin(out, pin):
    path = send(out, "right").strip()  # open the PIN screen
    time.sleep(0.3)
    for ch in pin:
        target = int(ch)
        for _ in range(12):
            d, _x = read_digit(path)
            if d is None:
                raise RuntimeError(f"cannot read the PIN wheel in {path}")
            if d == target:
                break
            n = (d - target) % 10
            path = send(out, "up", str(n)).strip()
        else:
            raise RuntimeError("could not reach the digit")
        path = send(out, "right").strip()
    # After the digits the wheel offers a check mark (no digit) to submit.
    for _ in range(12):
        d, _x = read_digit(path)
        if d is None:
            return send(out, "right").strip()
        path = send(out, "up").strip()
    raise RuntimeError("no submit mark on the PIN wheel")
