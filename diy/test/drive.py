"""Press a sequence of keys on the running wallet and build one contact sheet of the screens."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ctl import send
from PIL import Image, ImageDraw

out, keys = sys.argv[1], sys.argv[2:]
paths = []
for k in keys:
    if ":" in k:
        name, n = k.split(":")
        paths.append(send(out, name, n).strip())
    else:
        paths.append(send(out, k).strip())
ims = [Image.open(p) for p in paths]
W, H = 256, 128
cols = 2
rows = (len(ims) + cols - 1) // cols
sheet = Image.new("RGB", (cols * (W + 10), rows * (H + 24)), (60, 60, 60))
d = ImageDraw.Draw(sheet)
for i, (im, p) in enumerate(zip(ims, paths)):
    x, y = (i % cols) * (W + 10), (i // cols) * (H + 24)
    d.text((x + 2, y + 2), os.path.basename(p), fill=(255, 255, 0))
    sheet.paste(im, (x, y + 18))
sheet.save(os.path.join(out, "sheet.png"))
print("\n".join(paths))
