"""Does the extraction reproduce NOAA's own picture?

Usage:
    python tools/dbofs_plotcheck.py --time 2026-08-13T14:00Z --out check.png

`currents.py` (repo root) reads the model output that the OFS map-plot animation was
drawn from. This draws the extracted field back onto that published PNG, in
NOAA's own arrow convention and colour bins, so the two can be compared as
pictures rather than as claims about pictures.

Two things are checked before anything is drawn, because an overlay that is
merely plausible is worse than no overlay:

    1. The plot frame is FOUND in the image, not assumed — the black axis
       rectangle is detected by scanning for its long runs.
    2. The georeference is PROVEN against the dotted gridlines. Each labelled
       meridian and parallel must land within a pixel or two of where the
       model box says it should. If that check fails the script refuses to
       draw, because arrows in the wrong place would look like a disagreement
       in the data.

Needs matplotlib and PIL, which the analysis tools already use. The planner
itself stays stdlib-only and never imports this.
"""
import argparse
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                       # noqa: E402
from PIL import Image                    # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from currents import (Currents, fmt_lat, fmt_lon, fmt_span,  # noqa: E402
                      parse_time)

PLOT_URL = ('https://cdn.tidesandcurrents.noaa.gov/ofs/dbofs/wwwgraphics/'
            'DBOFS_db_all_cu_fore_{stamp}.png')

# NOAA's legend, read off the published plot. Same bins, same colours, so a
# colour disagreement in the overlay is a speed disagreement in the data.
BINS = [(0.3, '#0000ff'), (0.6, '#00ffff'), (1.0, '#00ff00'), (1.3, '#ff8000'),
        (1.6, '#ff00ff'), (2.0, '#ff0000'), (99., '#000000')]

# The meridians and parallels NOAA labels, used to prove the georeference.
MERIDIANS = [-75.60, -74.80, -74.00, -73.20]
PARALLELS = [38.00, 38.80, 39.60]


def colour_for(kt):
    for edge, col in BINS:
        if kt < edge:
            return col
    return BINS[-1][1]


def find_frame(img):
    """Locate the black axis rectangle. Returns (left, top, right, bottom)."""
    a = np.asarray(img.convert('L'), dtype=np.int16)
    dark = a < 100
    h, w = dark.shape
    rows = [y for y in range(h) if dark[y].sum() > 0.6 * w]
    cols = [x for x in range(w) if dark[:, x].sum() > 0.6 * h]
    if len(rows) < 2 or len(cols) < 2:
        raise RuntimeError('could not find the plot frame in the image')
    return min(cols), min(rows), max(cols), max(rows)


def _cluster(idx, gap):
    out, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= gap:
            cur.append(i)
        else:
            out.append(sum(cur) / len(cur))
            cur = [i]
    out.append(sum(cur) / len(cur))
    return out


def find_gridlines(img, frame):
    """The dotted graticule, found by its NEUTRAL grey — the current arrows are
    saturated colours, so hue alone separates the two."""
    left, top, right, bottom = frame
    a = np.asarray(img.convert('RGB'), dtype=np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    neutral = (abs(r - g) < 12) & (abs(g - b) < 12) & (r < 140)
    inner = neutral[top + 6:bottom - 6, left + 6:right - 6]
    ox, oy = left + 6, top + 6

    colcount, rowcount = inner.sum(axis=0), inner.sum(axis=1)
    cthr = max(12, 0.45 * float(np.percentile(colcount, 99.5)))
    rthr = max(12, 0.45 * float(np.percentile(rowcount, 99.5)))
    cols = _cluster([ox + i for i, v in enumerate(colcount) if v >= cthr], 3)
    rows = _cluster([oy + i for i, v in enumerate(rowcount) if v >= rthr], 3)
    if len(cols) < 3 or len(rows) < 3:
        raise RuntimeError(f'found only {len(cols)} meridians and {len(rows)} parallels')
    return cols, rows


def _labelled_subset(lines, wanted, text_centres):
    """Labels sit on every OTHER graticule line. Pick the alternating subset
    that matches the label count, and when both do, the one the tick text
    actually sits under."""
    subsets = [lines[0::2], lines[1::2]]
    fits = [s for s in subsets if len(s) == wanted]
    if len(fits) == 1:
        return fits[0]
    if not fits:
        raise RuntimeError(f'{len(lines)} graticule lines give no alternating '
                           f'subset of {wanted}')
    if not text_centres:
        raise RuntimeError('two candidate subsets and no tick text to choose between')
    score = [sum(min(abs(c - t) for t in text_centres) for c in s) for s in fits]
    return fits[0] if score[0] <= score[1] else fits[1]


def _fit(pixels, values, name, tol=2.0):
    """Least-squares px = m*value + c, with the residuals reported. A graticule
    is exactly linear in a plate carree plot, so a residual above a pixel or
    two means the lines were misidentified, not that the plot is imprecise."""
    n = len(pixels)
    mv = sum(values) / n
    mp = sum(pixels) / n
    num = sum((v - mv) * (p - mp) for v, p in zip(values, pixels))
    den = sum((v - mv) ** 2 for v in values)
    m = num / den
    c = mp - m * mv
    res = [abs(m * v + c - p) for v, p in zip(values, pixels)]
    show = fmt_lon if name == 'meridian' else fmt_lat
    for v, p, e in zip(values, pixels, res):
        print(f'  {name} {show(v, 2):>11} -> px {p:7.1f}  residual {e:4.2f}')
    if max(res) > tol:
        raise RuntimeError(f'{name} graticule fit residual {max(res):.2f} px exceeds '
                           f'{tol}. Refusing to draw arrows in the wrong place.')
    return m, c


def georeference(img, frame):
    """Derive lon(x) and lat(y) from the plot's own graticule and tick labels.
    Returns (to_px, px_per_deg_lon, px_per_deg_lat)."""
    left, top, right, bottom = frame
    cols, rows = find_gridlines(img, frame)
    a = np.asarray(img.convert('L'), dtype=np.int16)

    # tick text: left margin for the parallels (no caption there to confuse it)
    band = a[:, 8:left - 2] < 128
    ytext = _cluster([y for y in range(band.shape[0]) if band[y].any()], 6)
    band2 = a[bottom + 3:bottom + 21, :] < 128
    xtext = _cluster([x for x in range(band2.shape[1]) if band2[:, x].any()], 10)

    lon_px = _labelled_subset(cols, len(MERIDIANS), xtext)
    lat_px = _labelled_subset(rows, len(PARALLELS), ytext)
    print(f'  graticule: {len(cols)} meridians, {len(rows)} parallels; '
          f'labels on {len(lon_px)} and {len(lat_px)}')

    # Pixel rows increase DOWNWARD while latitude increases upward, so the
    # northernmost parallel is the smallest y. Pairing both ascending mirrors
    # the map, and a mirrored axis still fits a straight line with sub-pixel
    # residuals — the fit cannot catch it, so the pairing is explicit here and
    # the sign is asserted below.
    mx, cx = _fit(lon_px, sorted(MERIDIANS), 'meridian')
    my, cy = _fit(lat_px, sorted(PARALLELS, reverse=True), 'parallel')
    if mx <= 0 or my >= 0:
        raise RuntimeError(f'axes came out the wrong way round: {mx:.1f} px/deg lon, '
                           f'{my:.1f} px/deg lat (want +ve east, -ve north)')

    # An independent check on the projection itself: a plate carree plot has
    # px/deg_lat = px/deg_lon / cos(mid latitude). If that fails, the axes were
    # read wrongly even though each fit looked clean.
    mid = sum(PARALLELS) / len(PARALLELS)
    want = mx / math.cos(math.radians(mid))
    if abs(abs(my) - want) / want > 0.03:
        raise RuntimeError(f'aspect check failed: {abs(my):.1f} px/deg lat vs '
                           f'{want:.1f} expected at {mid:.1f}N')
    print(f'  {mx:.1f} px/deg lon, {abs(my):.1f} px/deg lat — aspect matches '
          f'plate carree at {mid:.1f}N (expected {want:.1f})')
    print(f'  plot covers {fmt_span((left - cx) / mx, (right - cx) / mx, "lon")}, '
          f'{fmt_span((bottom - cy) / my, (top - cy) / my, "lat")}')

    def to_px(lat, lon):
        return mx * lon + cx, my * lat + cy

    return to_px, mx, abs(my), ((bottom - cy) / my, (left - cx) / mx,
                                (top - cy) / my, (right - cx) / mx)


def check_contains_model(plot_box, model_box, margin=0.05):
    """The plot is a map WITH the model domain inside it — the published frame
    is padded beyond the grid. If the derived plot box does not contain the
    model box, the georeference is wrong however clean the fit looked."""
    plat0, plon0, plat1, plon1 = plot_box
    mlat0, mlon0, mlat1, mlon1 = model_box
    ok = (plat0 <= mlat0 + margin and plat1 >= mlat1 - margin
          and plon0 <= mlon0 + margin and plon1 >= mlon1 - margin)
    print(f'  plot {fmt_span(plat0, plat1, "lat")} {fmt_span(plon0, plon1, "lon")} '
          f'{"contains" if ok else "DOES NOT CONTAIN"} the model box')
    if not ok:
        raise RuntimeError('derived plot box does not contain the model domain')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--time', required=True, help='ISO UTC, on the hour')
    p.add_argument('--png', type=Path, help='published plot (downloaded if absent)')
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--step', type=int, default=15, help='node stride between arrows')
    a = p.parse_args(argv)

    when = parse_time(a.time)
    cur = Currents()

    if a.png and a.png.exists():
        img = Image.open(a.png)
    else:
        import urllib.request
        # Plot filenames are stamped in LOCAL time (EDT), not UTC — the 14:00Z
        # frame is the file named 1000. Getting this wrong compares two
        # different hours and looks like a model error.
        local = when.astimezone(__import__('zoneinfo').ZoneInfo('America/New_York'))
        url = PLOT_URL.format(stamp=local.strftime('%Y%m%d%H%M'))
        print(f'fetching {url}')
        dest = a.out.with_name('noaa_frame.png')
        urllib.request.urlretrieve(url, dest)
        img = Image.open(dest)

    frame = find_frame(img)
    box = cur.box()
    print(f'frame px {frame}, model box {fmt_span(box[0], box[2], "lat")} '
          f'{fmt_span(box[1], box[3], "lon")}')
    to_px, _, _, plot_box = georeference(img, frame)
    check_contains_model(plot_box, box)

    left, top, right, bottom = frame

    fig, axes = plt.subplots(1, 2, figsize=(19, 8.5))
    for ax, title in zip(axes, ('NOAA published plot', 'extracted field, same instant')):
        ax.imshow(img)
        ax.set_title(title, fontsize=13)
        ax.set_xlim(left - 5, right + 5)
        ax.set_ylim(bottom + 5, top - 5)
        ax.axis('off')

    # blank the right panel's arrows so only ours are visible over the coastline
    axes[1].imshow(Image.new('RGBA', img.size, (255, 255, 255, 190)))

    n = 0
    for iy in range(0, cur.ny, a.step):
        lat = cur.lat0 + iy * cur.dlat
        for ix in range(0, cur.nx, a.step):
            lon = cur.lon0 + ix * cur.dlon
            got = cur.at(lat, lon, when)
            if got is None or got[0] < 0.01:
                continue
            kt, _, u, v = got
            x, y = to_px(lat, lon)
            norm = (u * u + v * v) ** 0.5
            dx, dy = 11 * u / norm, -11 * v / norm      # image y grows downward
            axes[1].arrow(x - dx / 2, y - dy / 2, dx, dy, head_width=4.0,
                          head_length=4.0, fc=colour_for(kt), ec=colour_for(kt),
                          length_includes_head=True, linewidth=0.9)
            n += 1

    fig.suptitle(f'DBOFS surface current — {when:%Y-%m-%d %H:%MZ} — '
                 f'{n} extracted vectors, NOAA colour bins', fontsize=14)
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(f'wrote {a.out} ({n} arrows drawn)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
