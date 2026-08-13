#!/usr/bin/env python3
"""Generate the LearnLeader (导学吧) brand asset set from the source icon.

The source icon is an imagegen-produced 1024+ square: indigo→violet gradient
background with a white open-book / wings emblem representing guidance and
learning. This script resizes it for every size ``web/public/`` expects,
makes the near-black rounded corners transparent, composites a horizontal
banner wordmark, and produces a dark-background variant.

Usage:
  # 1. Generate the base icon with imagegen (see SKILL.md), then:
  ICON_SRC=/path/to/icon.png python scripts/make_learnleader_branding.py

If ICON_SRC is unset, falls back to the glyph-based generator (legacy).
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path("web/public")
CJK = "/System/Library/Fonts/STHeiti Medium.ttc"
LATIN = "/System/Library/Fonts/SFNS.ttf"

import os

_icon_src_env = os.environ.get("ICON_SRC", "")
_bundled = Path(__file__).parent / "branding-source" / "learnleader-icon.png"
ICON_SRC = Path(_icon_src_env) if _icon_src_env else (_bundled if _bundled.exists() else None)

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


# ---------------------------------------------------------------------------
# imagegen-source path (ICON_SRC env var)
# ---------------------------------------------------------------------------

def _load_imagegen_icon():
    """Load the AI-generated icon and make near-black corner pixels transparent."""
    if ICON_SRC is None or not ICON_SRC.exists():
        return None
    img = Image.open(ICON_SRC).convert("RGBA")
    w, h = img.size
    alpha = Image.new("L", (w, h), 255)
    alpha_px = alpha.load()
    px = img.load()
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if r < 15 and g < 15 and b < 15:
                alpha_px[x, y] = 0
    img.putalpha(alpha)
    return img


def _make_icon_from(src: Image.Image, size: int, path: Path):
    resized = src.resize((size, size), Image.LANCZOS)
    resized.save(path)
    print(f"  wrote {path} ({size}x{size})")


def _make_black_icon_from(src: Image.Image, size: int, path: Path):
    """Dark-background variant: keep the white emblem, swap gradient for ink."""
    resized = src.resize((size, size), Image.LANCZOS).convert("RGBA")
    w, h = resized.size
    px = resized.load()
    out = Image.new("RGBA", (w, h), INK)
    out_px = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 128:
                continue
            brightness = (r + g + b) / 3
            out_px[x, y] = (255, 255, 255, 255) if brightness > 180 else INK
    out.save(path)
    print(f"  wrote {path} ({size}x{size})")


def _make_banner_from(src: Image.Image, path: Path):
    """Horizontal wordmark: [icon] 导学吧  LearnLeader."""
    icon_size = 220
    pad = 24
    gap = 28
    cjk_size = 110
    latin_size = 72
    cjk_font = _font(CJK, cjk_size)
    latin_font = _font(LATIN, latin_size)
    tmp = Image.new("RGBA", (1, 1))
    td = ImageDraw.Draw(tmp)
    cjk_bbox = td.textbbox((0, 0), "导学吧", font=cjk_font)
    latin_bbox = td.textbbox((0, 0), "LearnLeader", font=latin_font)
    cjk_w = cjk_bbox[2] - cjk_bbox[0]
    cjk_h = cjk_bbox[3] - cjk_bbox[1]
    latin_w = latin_bbox[2] - latin_bbox[0]
    latin_h = latin_bbox[3] - latin_bbox[1]
    height = icon_size + pad * 2
    width = int(pad + icon_size + gap + cjk_w + 24 + latin_w + pad)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    icon = src.resize((icon_size, icon_size), Image.LANCZOS)
    canvas.paste(icon, (pad, pad), icon)
    d = ImageDraw.Draw(canvas)
    cy = height / 2
    x = pad + icon_size + gap
    d.text((x - cjk_bbox[0], cy - cjk_h / 2 - cjk_bbox[1] - 4),
           "导学吧", font=cjk_font, fill=INK)
    x2 = x + cjk_w + 24
    d.text((x2 - latin_bbox[0], cy - latin_h / 2 - latin_bbox[1] + 2),
           "LearnLeader", font=latin_font, fill=TOP)
    canvas.save(path)
    print(f"  wrote {path} ({canvas.width}x{canvas.height})")


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
    icon_src = _load_imagegen_icon()
    if icon_src is not None:
        print(f"Generating LearnLeader branding from imagegen icon ({ICON_SRC}) ...")
        _make_icon_from(icon_src, 256, OUT / "logo.png")
        _make_icon_from(icon_src, 512, OUT / "logo-ver2.png")
        _make_black_icon_from(icon_src, 256, OUT / "logo_black.png")
        _make_banner_from(icon_src, OUT / "banner.png")
        _make_icon_from(icon_src, 16, OUT / "favicon-16x16.png")
        _make_icon_from(icon_src, 32, OUT / "favicon-32x32.png")
        _make_icon_from(icon_src, 180, OUT / "apple-touch-icon.png")
        print("Done.")
        return

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
