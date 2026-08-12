#!/usr/bin/env python3
"""Generate LearnLeader (导学吧) placeholder branding assets.

Produces a clean indigo/violet icon mark built around the glyph 导
("to guide / lead" — the lead character of 导学吧), plus a horizontal
wordmark banner and the favicon set. All output is PNG so it drops into
``web/public/`` without touching any code references.

Run:  python scripts/make_learnleader_branding.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("web/public")
CJK = "/System/Library/Fonts/STHeiti Medium.ttc"
LATIN = "/System/Library/Fonts/SFNS.ttf"

# Brand palette: indigo -> violet gradient (friendly, modern EdTech).
TOP = (99, 102, 241, 255)      # #6366F1 indigo-500
BOTTOM = (124, 58, 237, 255)   # #7C3AED violet-600
WHITE = (255, 255, 255, 255)
INK = (30, 27, 75, 255)        # near-black indigo for the "black" variant


def _font(path: str, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        return ImageFont.truetype(path, size)


def _rounded_gradient(size: int, radius_ratio: float = 0.22) -> Image.Image:
    """A square with a vertical indigo→violet gradient and rounded corners."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(TOP[0] + (BOTTOM[0] - TOP[0]) * t)
        g = int(TOP[1] + (BOTTOM[1] - TOP[1]) * t)
        b = int(TOP[2] + (BOTTOM[2] - TOP[2]) * t)
        for x in range(size):
            grad.putpixel((x, y), (r, g, b, 255))
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * radius_ratio), fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def _solid_rounded(size: int, color, radius_ratio: float = 0.22) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * radius_ratio), fill=color)
    return img


def _draw_glyph(img: Image.Image, glyph: str, size: int, color=WHITE, ratio: float = 0.60):
    """Center a single CJK glyph on the image."""
    font = _font(CJK, int(size * ratio))
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), glyph, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    # textbbox top is the font ascent origin; nudge so the glyph optical-centers.
    x = (size - w) / 2 - bbox[0]
    y = (size - h) / 2 - bbox[1] - int(size * 0.02)
    d.text((x, y), glyph, font=font, fill=color)


def make_icon(size: int, path: Path):
    img = _rounded_gradient(size)
    _draw_glyph(img, "导", size)
    img.save(path)
    print(f"  wrote {path} ({size}x{size})")


def make_touch_icon(size: int, path: Path):
    # Apple applies its own squircle mask — ship a full square.
    img = _solid_rounded(size, BOTTOM, radius_ratio=0.0)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, size - 1, size - 1), fill=BOTTOM)
    _draw_glyph(img, "导", size)
    img.save(path)
    print(f"  wrote {path} ({size}x{size})")


def make_black_icon(size: int, path: Path):
    """Flat ink-colored mark for light backgrounds (logo_black)."""
    img = _solid_rounded(size, INK, radius_ratio=0.22)
    _draw_glyph(img, "导", size)
    img.save(path)
    print(f"  wrote {path} ({size}x{size})")


def make_banner(path: Path):
    """Horizontal wordmark: [icon] 导学吧  LearnLeader."""
    icon_size = 220
    pad = 20
    gap = 26
    cjk_size = 120
    latin_size = 78

    cjk_font = _font(CJK, cjk_size)
    latin_font = _font(LATIN, latin_size)

    tmp = Image.new("RGBA", (1, 1))
    td = ImageDraw.Draw(tmp)
    cjk_bbox = td.textbbox((0, 0), "导学吧", font=cjk_font)
    latin_bbox = td.textbbox((0, 0), "LearnLeader", font=latin_font)
    cjk_w = cjk_bbox[2] - cjk_bbox[0]
    latin_w = latin_bbox[2] - latin_bbox[0]

    height = icon_size + pad * 2
    width = pad + icon_size + gap + cjk_w + 28 + latin_w + pad

    canvas = Image.new("RGBA", (int(width), height), (0, 0, 0, 0))
    # icon
    icon = _rounded_gradient(icon_size)
    _draw_glyph(icon, "导", icon_size)
    canvas.paste(icon, (pad, pad), icon)

    d = ImageDraw.Draw(canvas)
    cy = height / 2
    # 导学吧
    x = pad + icon_size + gap
    d.text((x - cjk_bbox[0], cy - (cjk_bbox[3] - cjk_bbox[1]) / 2 - cjk_bbox[1] - 4),
           "导学吧", font=cjk_font, fill=INK)
    # LearnLeader (lighter indigo)
    x2 = x + cjk_w + 28
    d.text((x2 - latin_bbox[0], cy - (latin_bbox[3] - latin_bbox[1]) / 2 - latin_bbox[1] + 2),
           "LearnLeader", font=latin_font, fill=(99, 102, 241, 255))

    canvas.save(path)
    print(f"  wrote {path} ({canvas.width}x{canvas.height})")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    print("Generating LearnLeader branding into web/public/ ...")
    make_icon(256, OUT / "logo.png")
    make_icon(512, OUT / "logo-ver2.png")
    make_black_icon(256, OUT / "logo_black.png")
    make_banner(OUT / "banner.png")
    make_icon(16, OUT / "favicon-16x16.png")
    make_icon(32, OUT / "favicon-32x32.png")
    make_touch_icon(180, OUT / "apple-touch-icon.png")
    print("Done.")


if __name__ == "__main__":
    main()
