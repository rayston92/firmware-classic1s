#!/usr/bin/env python3
"""
DIY build: replace the 24x24 icon on the wallet's home screen.

    python3 diy/set-home-icon.py my-icon.png

The picture must be 24x24 pixels. Light pixels (brightness 128 or more) are lit
on the screen, dark pixels stay off. Only the home-screen icon in
legacy/gen/bitmaps.c is changed; compile and reinstall afterwards.

Needs Pillow (pip install pillow). License: GNU GPL v3 or later.
"""

import os
import re
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BITMAPS_C = os.path.join(ROOT, "legacy", "gen", "bitmaps.c")
NAME = "onekey_logo"
SIZE = (24, 24)


def encode(img):
    # Same packing as legacy/gen/bitmaps/generate.py: rows of bits, 8 pixels per byte.
    bits = ["1" if v >= 128 else "0" for v in img.tobytes()]
    return "".join("0x%02x, " % int("".join(bits[i : i + 8]), 2) for i in range(0, len(bits), 8))


def main():
    if len(sys.argv) != 2:
        sys.exit("usage: set-home-icon.py my-icon.png")
    img = Image.open(sys.argv[1]).convert("L")
    if img.size != SIZE:
        sys.exit(f"The picture is {img.size[0]}x{img.size[1]} pixels; it must be 24x24.")

    src = open(BITMAPS_C).read()
    pattern = re.compile(r"(const uint8_t bmp_%s_data\[\] = \{ )[^}]*(\};)" % NAME)
    if not pattern.search(src):
        sys.exit("Could not find the home-screen icon in legacy/gen/bitmaps.c.")
    new = pattern.sub(lambda m: m.group(1) + encode(img) + m.group(2), src, count=1)
    open(BITMAPS_C, "w").write(new)

    preview = "\n".join(
        "".join("#" if img.getpixel((x, y)) >= 128 else "." for x in range(SIZE[0])) for y in range(SIZE[1])
    )
    print(preview)
    print("\nHome-screen icon replaced. Now compile and reinstall.")


if __name__ == "__main__":
    main()
