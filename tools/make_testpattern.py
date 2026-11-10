"""Create a 1040x1560 calibration image: colored edge bars + pixel ruler.

Usage: python3 tools/make_testpattern.py testpattern.png && mizink-enqueue testpattern.png
Print it and check which bars are fully visible and which ruler marks are missing/white.
"""
import sys
from PIL import Image, ImageDraw

W, H, B = 1040, 1560, 24
im = Image.new("RGB", (W, H), "white")
d = ImageDraw.Draw(im)
d.rectangle([0, 0, W, B], fill=(220, 30, 30))            # top    = red
d.rectangle([0, H - B, W, H], fill=(30, 60, 220))        # bottom = blue
d.rectangle([0, 0, B, H], fill=(30, 160, 60))            # left   = green
d.rectangle([W - B, 0, W, H], fill=(0, 0, 0))            # right  = black
for y in range(0, H, 50):                                 # ruler from the bottom edge
    n = (H - y)
    long_tick = n % 100 == 0
    d.line([W // 2 - (60 if long_tick else 30), y, W // 2 + (60 if long_tick else 30), y], fill="black", width=2)
    if long_tick:
        d.text((W // 2 + 70, y - 6), f"{n} px from bottom", fill="black")
d.text((60, 60), "TOP (red)", fill="black")
d.text((60, H - 80), "BOTTOM (blue)", fill="black")
im.save(sys.argv[1] if len(sys.argv) > 1 else "testpattern.png")
