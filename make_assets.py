#!/usr/bin/env python3
"""
Generate static social/branding assets for the Prague Events site:
  - og-image.png  (1200x630)  link-preview banner for Facebook/Telegram/etc.
  - favicon.png   (180x180)   apple-touch-icon / fallback favicon

Re-run any time the branding changes:  python make_assets.py
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

# ─ Palette (matches the site) ─
BG_TOP   = (26, 14, 46)     # #1a0e2e
BG_MID   = (13, 31, 62)     # #0d1f3e
BG_BOT   = (25, 14, 0)      # #190e00
GOLD     = (201, 168, 76)   # #c9a84c
GOLD_LT  = (245, 224, 144)  # #f5e090
TEXT     = (240, 240, 250)
MUTED    = (150, 150, 190)


def _font(size, bold=False):
    """Load a TrueType font, trying a few common Windows/Linux faces."""
    candidates = (
        ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf"]
        if bold else
        ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf"]
    ) + [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _vgrad(w, h):
    """Vertical 3-stop gradient (top→mid→bottom) as an RGB image."""
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        if t < 0.5:
            u = t / 0.5
            a, b = BG_TOP, BG_MID
        else:
            u = (t - 0.5) / 0.5
            a, b = BG_MID, BG_BOT
        r = int(a[0] + (b[0] - a[0]) * u)
        g = int(a[1] + (b[1] - a[1]) * u)
        bl = int(a[2] + (b[2] - a[2]) * u)
        for x in range(w):
            px[x, y] = (r, g, bl)
    return img


def _skyline(draw, x0, y0, scale):
    """Draw a simple gold Prague skyline silhouette anchored at baseline y0."""
    g = GOLD

    def s(v):
        return v * scale

    # cathedral twin spires + castle block
    draw.polygon([(x0+s(0), y0), (x0+s(0), y0-s(20)), (x0+s(3), y0-s(28)),
                  (x0+s(6), y0-s(20)), (x0+s(6), y0)], fill=g)
    draw.polygon([(x0+s(8), y0), (x0+s(8), y0-s(20)), (x0+s(11), y0-s(28)),
                  (x0+s(14), y0-s(20)), (x0+s(14), y0)], fill=g)
    draw.rectangle([x0+s(0), y0-s(10), x0+s(14), y0], fill=g)
    # bridge tower
    bx = x0 + s(20)
    draw.polygon([(bx, y0), (bx, y0-s(34)), (bx+s(5), y0-s(42)),
                  (bx+s(10), y0-s(34)), (bx+s(10), y0)], fill=g)
    draw.rectangle([bx-s(1), y0-s(22), bx+s(11), y0], fill=g)
    # dome + finial
    dx = x0 + s(36)
    draw.pieslice([dx, y0-s(20), dx+s(16), y0-s(4)], 180, 360, fill=g)
    draw.rectangle([dx, y0-s(12), dx+s(16), y0], fill=g)
    draw.rectangle([dx+s(7), y0-s(30), dx+s(9), y0-s(20)], fill=g)
    # rooftops
    rx = x0 + s(56)
    draw.polygon([(rx, y0), (rx, y0-s(14)), (rx+s(6), y0-s(20)),
                  (rx+s(12), y0-s(14)), (rx+s(12), y0)], fill=g)


def make_og():
    W, H = 1200, 630
    img = _vgrad(W, H)
    d = ImageDraw.Draw(img)

    # subtle gold bottom edge
    d.rectangle([0, H-6, W, H], fill=GOLD)

    # skyline on the lower-right, clear of the text column
    _skyline(d, 760, 548, scale=6.0)

    # title
    f_title = _font(84, bold=True)
    f_sub   = _font(40)
    f_tag   = _font(30)

    d.text((90, 180), "Prague Cultural Events", font=f_title, fill=TEXT)
    d.text((92, 292), "What's on in Prague — updated daily", font=f_sub, fill=GOLD_LT)
    d.text((92, 364), "Cinema · Theatre · Concerts · Festivals · Exhibitions · Stand-up",
           font=f_tag, fill=MUTED)
    d.text((92, 410), "CZ · EN · RU · UA · VI", font=f_tag, fill=MUTED)

    out = HERE / "og-image.png"
    img.save(out, "PNG")
    print("wrote", out)


def make_favicon():
    S = 180
    img = _vgrad(S, S)
    # rounded corners via mask
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S-1, S-1], radius=38, fill=255)
    d = ImageDraw.Draw(img)
    _skyline(d, 28, 140, scale=2.4)
    img.putalpha(mask)
    out = HERE / "favicon.png"
    img.save(out, "PNG")
    print("wrote", out)


if __name__ == "__main__":
    make_og()
    make_favicon()
