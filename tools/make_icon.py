"""Draw tools/fuel.ico — the desktop-shortcut icon for the planner.

    python tools/make_icon.py            # -> tools/fuel.ico

A fuel gauge with the needle just above a quarter tank: the 25% reserve floor
this whole tool is built around, with the needle sitting where a plan should
leave it. Deliberately drawn rather than downloaded so there is no licensing
question and no binary in the repo without a builder beside it.

Everything is drawn at 8x and downsampled per size, because Windows renders the
shortcut at 16 px in a list view and a hairline arc disappears there. The dial
marks are dropped below 32 px for the same reason — at that size they turn into
grey mush and read worse than nothing.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / 'fuel.ico'
SIZES = (16, 24, 32, 48, 64, 128, 256)
SS = 8                                    # supersampling factor

NAVY = (18, 32, 51, 255)                  # dial face
RIM = (86, 110, 140, 255)                 # dial rim
SCALE = (214, 224, 236, 255)              # the arc itself
AMBER = (245, 166, 35, 255)               # needle + reserve sector
RED = (208, 74, 62, 255)                  # below the floor

# The gauge sweeps 220 deg, E on the left, F on the right. PIL angles run
# clockwise from 3 o'clock, so the arc start/end are measured that way.
START, SWEEP = 160.0, 220.0


def _frac_to_deg(frac: float) -> float:
    """Tank fraction (0 = empty, 1 = full) -> PIL angle in degrees."""
    return START + SWEEP * frac


def _draw(px: int) -> Image.Image:
    """One frame at `px` logical pixels, rendered at px*SS and downsampled.

    Small frames are NOT the large one shrunk. Below 32 px the dial geometry is
    redrawn fatter — wider arc, less padding, a stubbier needle — because the
    faithful version renders as a dark blob at 16 px, which is the size Windows
    uses in Explorer list view. This was checked by rasterising, not assumed.
    """
    n = px * SS
    small = px < 32
    img = Image.new('RGBA', (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = n * (0.015 if small else 0.035)
    box = (pad, pad, n - pad, n - pad)
    cx = cy = n / 2.0

    # Dial face and rim.
    d.ellipse(box, fill=NAVY, outline=RIM, width=max(1, int(n * 0.035)))

    # The scale arc, inset from the rim.
    ai = n * (0.13 if small else 0.20)
    abox = (ai, ai, n - ai, n - ai)
    aw = max(1, int(n * (0.155 if small else 0.085)))
    d.arc(abox, _frac_to_deg(0.0), _frac_to_deg(1.0), fill=SCALE, width=aw)
    # The reserve band, coloured: red below the 25% floor, amber just above it.
    d.arc(abox, _frac_to_deg(0.0), _frac_to_deg(0.25), fill=RED, width=aw)
    d.arc(abox, _frac_to_deg(0.25), _frac_to_deg(0.40), fill=AMBER, width=aw)

    # Tick marks at E, 1/2 and F — only where they will survive downsampling.
    if px >= 32:
        r_out, r_in = n * 0.30, n * 0.225
        for frac in (0.0, 0.5, 1.0):
            a = math.radians(_frac_to_deg(frac))
            d.line([(cx + r_out * math.cos(a), cy + r_out * math.sin(a)),
                    (cx + r_in * math.cos(a), cy + r_in * math.sin(a))],
                   fill=SCALE, width=max(1, int(n * 0.028)))

    # Needle, pointing at a third of a tank — above the floor, not comfortably.
    a = math.radians(_frac_to_deg(0.33))
    reach = n * (0.24 if small else 0.30)
    tip = (cx + reach * math.cos(a), cy + reach * math.sin(a))
    # A tapered needle: tip plus two shoulders behind the hub.
    back = a + math.pi
    hub_r = n * (0.085 if small else 0.055)
    sh = [(cx + hub_r * math.cos(back + off), cy + hub_r * math.sin(back + off))
          for off in (-1.15, 1.15)]
    d.polygon([tip, sh[0], sh[1]], fill=AMBER)
    d.ellipse((cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r),
              fill=AMBER, outline=NAVY, width=max(1, int(n * 0.012)))

    return img.resize((px, px), Image.LANCZOS)


def main() -> None:
    frames = [_draw(s) for s in SIZES]
    # Pillow writes every appended image as its own ICO directory entry, so the
    # sizes above are what Windows can choose from. Saving one 256 px frame and
    # letting Windows scale it looks noticeably worse in the taskbar.
    frames[-1].save(OUT, format='ICO',
                    sizes=[(s, s) for s in SIZES],
                    append_images=frames[:-1])
    print(f'wrote {OUT}  ({OUT.stat().st_size:,} bytes, sizes {list(SIZES)})')


if __name__ == '__main__':
    main()
