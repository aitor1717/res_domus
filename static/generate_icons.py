"""
Run once to generate PWA icons: python static/generate_icons.py
Requires Pillow: pip install pillow
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent

BG      = (20, 23, 28)       # --bg topbar
SURFACE = (30, 34, 48)       # --bg3
ORANGE  = (253, 151, 31)     # --orange


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    # Rounded background square
    pad = size // 8
    draw.rounded_rectangle([pad, pad, size - pad, size - pad],
                            radius=size // 6, fill=SURFACE)

    # "r_d" wordmark — scale font to ~35% of icon height
    fs = max(10, int(size * 0.35))
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", fs)
    except OSError:
        font = ImageFont.load_default()

    text = "r_d"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]

    # Shadow / accent dot
    dot_r = size // 14
    draw.ellipse([x + tw - dot_r, y - dot_r * 2,
                  x + tw + dot_r, y],
                 fill=ORANGE)

    draw.text((x, y), text, fill=(248, 248, 242), font=font)
    return img


for sz in (192, 512):
    path = OUT / f"icon-{sz}.png"
    make_icon(sz).save(path, "PNG")
    print(f"wrote {path}")
