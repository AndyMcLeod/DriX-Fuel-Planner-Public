"""Reading a survey line plan or a trackline out of the files planners produce.

Standard library only.

WHAT IS ACCEPTED, AND WHY THAT IS THE SCOPE
    The formats below were chosen on one test: can the file be read WITHOUT
    guessing? A parser that half-works is worse than none here, because a
    line plan that loads cleanly and lands a mile off looks exactly like a
    line plan that loaded correctly.

    Accepted:
      * CSV / TXT   — endpoint-per-row or point-per-row, columns sniffed from
                      the header, or positional as a fallback.
      * GeoJSON     — LineString, MultiLineString, and Features of either.
      * KML / KMZ   — LineString placemarks; KMZ is unzipped in memory.
      * GPX         — routes (rte) and tracks (trk).
      * Hypack LNW  — the plain-text line file, LIN/PNT records.

    NOT accepted, deliberately, each for a reason rather than for lack of time:
      * Shapefile — the geometry is easy but the CRS lives in a sidecar .prj
        as WKT, and guessing a datum from a partial WKT string is exactly the
        silent-failure class this module exists to avoid.
      * UKOOA P1/90 and SEG-P1 — fixed-column formats where a one-character
        offset error still parses and yields plausible positions. Needs a real
        sample to pin the columns against.
      * QINSy, NaviPac and PDS native databases — proprietary containers, not
        interchange formats. Every one of them exports to something above.

COORDINATES — THE PART THAT ACTUALLY BITES
    Geographic degrees are taken as they come. Decimal degrees, and the
    degrees-minutes-seconds spellings surveyors actually type, are all read;
    a hemisphere letter wins over a sign, and a bare negative still means
    west/south.

    PROJECTED coordinates are accepted for UTM on WGS84 only, and only when
    the zone is stated — in the file, or by the caller. That covers the great
    majority of survey line plans while refusing to guess: an easting and a
    northing with no zone is not a position, and inventing one would put the
    survey hundreds of miles away with no outward sign.

    WGS84 is assumed throughout. NAD83 differs by 1-2 m in this region, which
    is two orders of magnitude below the 500 m mesh of the forecast these
    lines are read to sample.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

# WGS84
_A = 6378137.0
_F = 1.0 / 298.257223563
_E2 = _F * (2 - _F)
_K0 = 0.9996


class LinePlanError(ValueError):
    """Raised with a message meant for an operator, not a developer."""


@dataclass
class Line:
    points: list                       # [(lat, lon), ...]
    name: str = ''

    def is_straight_pair(self) -> bool:
        return len(self.points) == 2


@dataclass
class LinePlan:
    lines: list = field(default_factory=list)
    source_format: str = ''
    crs: str = 'WGS84 geographic'
    notes: list = field(default_factory=list)

    def as_tracks(self) -> list:
        return [[list(p) for p in ln.points] for ln in self.lines]


# --------------------------------------------------------------------------- #
#  Coordinates
# --------------------------------------------------------------------------- #
_DMS = re.compile(r"""^\s*(?P<sign>[-+])?\s*(?P<d>\d+(?:\.\d+)?)\s*
                      (?:[°d:\s]\s*(?P<m>\d+(?:\.\d+)?)\s*
                      (?:['m:\s]\s*(?P<s>\d+(?:\.\d+)?)\s*)?)?
                      ["s]?\s*(?P<hemi>[NSEWnsew])?\s*$""", re.X)


def parse_angle(text) -> float:
    """Decimal degrees from the spellings people actually write.

    A hemisphere letter WINS over a sign: "-75.5 W" is 75.5 west, not east.
    Someone who writes both means west twice, and reading it as a double
    negative would put the position in China."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    if not s:
        raise LinePlanError('empty coordinate')
    m = _DMS.match(s)
    if not m:
        raise LinePlanError(f'could not read the coordinate {s!r}')
    val = float(m.group('d'))
    if m.group('m'):
        val += float(m.group('m')) / 60.0
    if m.group('s'):
        val += float(m.group('s')) / 3600.0
    hemi = (m.group('hemi') or '').upper()
    if hemi in ('S', 'W'):
        return -val
    if hemi in ('N', 'E'):
        return val
    return -val if m.group('sign') == '-' else val


def utm_to_geographic(easting, northing, zone, northern=True):
    """UTM (WGS84) -> (lat, lon). Standard series inverse, good to millimetres
    over a zone — far beyond what a line plan needs, but the arithmetic is no
    harder than an approximation would be."""
    if not 1 <= int(zone) <= 60:
        raise LinePlanError(f'UTM zone {zone} does not exist')
    x = float(easting) - 500000.0
    y = float(northing) - (0.0 if northern else 10000000.0)
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    m = y / _K0
    mu = m / (_A * (1 - _E2 / 4 - 3 * _E2 ** 2 / 64 - 5 * _E2 ** 3 / 256))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    ep2 = _E2 / (1 - _E2)
    c1 = ep2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * _K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2)
        * d ** 6 / 720)
    lon = (d
           - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2)
           * d ** 5 / 120) / math.cos(phi1)
    lon0 = math.radians((int(zone) - 1) * 6 - 180 + 3)
    return math.degrees(lat), math.degrees(lon) + math.degrees(lon0)


def looks_projected(a, b) -> bool:
    """Eastings and northings are metres and run to seven figures; degrees
    cannot exceed 180. Anything outside the geographic range must be
    projected, and anything inside it must not be guessed at."""
    return abs(a) > 180.0 or abs(b) > 180.0


# --------------------------------------------------------------------------- #
#  Format readers
# --------------------------------------------------------------------------- #
def _clean(points):
    """Drop consecutive duplicates — a repeated vertex is a typo, and it makes
    a segment with no course."""
    out = []
    for p in points:
        if not out or (abs(p[0] - out[-1][0]) > 1e-12 or abs(p[1] - out[-1][1]) > 1e-12):
            out.append(p)
    return out


def read_geojson(text: str) -> LinePlan:
    data = json.loads(text)
    lines = []

    def take(geom, name):
        t = (geom or {}).get('type')
        if t == 'LineString':
            lines.append(Line(_clean([(c[1], c[0]) for c in geom['coordinates']]), name))
        elif t == 'MultiLineString':
            for i, part in enumerate(geom['coordinates']):
                lines.append(Line(_clean([(c[1], c[0]) for c in part]),
                                  f'{name} {i + 1}' if name else ''))
        elif t == 'GeometryCollection':
            for g in geom.get('geometries', []):
                take(g, name)

    if data.get('type') == 'FeatureCollection':
        for f in data.get('features', []):
            take(f.get('geometry'), str((f.get('properties') or {}).get('name', '')))
    elif data.get('type') == 'Feature':
        take(data.get('geometry'), str((data.get('properties') or {}).get('name', '')))
    else:
        take(data, '')
    if not lines:
        raise LinePlanError('no LineString geometry in that GeoJSON')
    return LinePlan(lines, 'GeoJSON')


def _strip_ns(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def read_kml(text: str) -> LinePlan:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise LinePlanError(f'that KML will not parse: {exc}')
    lines = []
    for pm in root.iter():
        if _strip_ns(pm.tag) != 'Placemark':
            continue
        name = ''
        for child in pm:
            if _strip_ns(child.tag) == 'name':
                name = (child.text or '').strip()
        for node in pm.iter():
            if _strip_ns(node.tag) != 'coordinates':
                continue
            pts = []
            for tok in (node.text or '').replace('\n', ' ').split():
                bits = tok.split(',')
                if len(bits) >= 2:
                    pts.append((float(bits[1]), float(bits[0])))
            if len(pts) >= 2:
                lines.append(Line(_clean(pts), name))
    if not lines:
        raise LinePlanError('no LineString placemarks in that KML')
    return LinePlan(lines, 'KML')


def read_kmz(blob: bytes) -> LinePlan:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = [n for n in z.namelist() if n.lower().endswith('.kml')]
        if not names:
            raise LinePlanError('that KMZ has no .kml inside it')
        # doc.kml by convention, else the first one
        pick = next((n for n in names if n.lower().endswith('doc.kml')), names[0])
        plan = read_kml(z.read(pick).decode('utf-8', 'replace'))
    plan.source_format = 'KMZ'
    return plan


def read_gpx(text: str) -> LinePlan:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise LinePlanError(f'that GPX will not parse: {exc}')
    lines = []
    for node in root.iter():
        tag = _strip_ns(node.tag)
        if tag not in ('rte', 'trkseg'):
            continue
        name = ''
        parent_name = node.find('./{*}name')
        if parent_name is not None:
            name = (parent_name.text or '').strip()
        pts = []
        for p in node.iter():
            if _strip_ns(p.tag) in ('rtept', 'trkpt'):
                pts.append((float(p.get('lat')), float(p.get('lon'))))
        if len(pts) >= 2:
            lines.append(Line(_clean(pts), name))
    if not lines:
        raise LinePlanError('no routes or tracks in that GPX')
    return LinePlan(lines, 'GPX')


def read_hypack_lnw(text: str) -> LinePlan:
    """Hypack's plain-text line file: LIN <n> then that many PNT records.

    Coordinates are usually PROJECTED (the survey's grid), which is why a zone
    has to come from the caller for these — see `convert_projected`.
    """
    lines, pts, want, name = [], [], 0, ''
    for raw in text.splitlines():
        parts = raw.split()
        if not parts:
            continue
        key = parts[0].upper()
        if key == 'LIN':
            if pts:
                lines.append(Line(_clean(pts), name))
            pts, name = [], ''
            want = int(parts[1]) if len(parts) > 1 else 0
        elif key == 'PNT' and len(parts) >= 3:
            pts.append((float(parts[2]), float(parts[1])))     # PNT x y
        elif key == 'LNN' and len(parts) > 1:
            name = ' '.join(parts[1:])
        elif key == 'EOL':
            if pts:
                lines.append(Line(_clean(pts), name))
            pts, name = [], ''
    if pts:
        lines.append(Line(_clean(pts), name))
    if not lines:
        raise LinePlanError('no LIN/PNT records in that file')
    return LinePlan(lines, 'Hypack LNW')


_LAT_KEYS = ('lat', 'latitude', 'y', 'northing', 'north')
_LON_KEYS = ('lon', 'lng', 'long', 'longitude', 'x', 'easting', 'east')


def read_delimited(text: str) -> LinePlan:
    """CSV or whitespace-delimited text, in either of the two shapes a line
    plan comes in: one row per LINE (both endpoints), or one row per POINT
    with a line name or number to group by.

    Columns are taken from the header when there is one. Without a header the
    columns are positional, and the order is assumed to be the one every
    example of these files uses: name first if present, then latitude before
    longitude. That assumption is REPORTED in `notes` rather than made
    silently, because a transposed pair is the classic way to put a survey in
    the wrong ocean.
    """
    # csv.Sniffer gives up on short files — a two-line plan is short — and its
    # failure mode is silent: falling straight through to whitespace splitting
    # turns "L1,38.8,-75.1" into ONE token, every row is skipped for having no
    # numbers, and the file reports as containing no coordinates. So the
    # delimiters are tried explicitly before the whitespace fallback.
    sample = text[:4096]
    rows = None
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
        rows = list(csv.reader(io.StringIO(text), dialect))
    except csv.Error:
        for delim in (',', ';', '\t', '|'):
            if delim in sample:
                rows = list(csv.reader(io.StringIO(text), delimiter=delim))
                break
    if rows is None:
        rows = [r.split() for r in text.splitlines()]
    rows = [r for r in rows if r and not str(r[0]).lstrip().startswith('#')]
    if not rows:
        raise LinePlanError('that file has no rows in it')

    # Endpoint-per-row files number their columns — lat1, lon1, lat2, lon2 —
    # so the trailing index is stripped before matching. Without this the
    # header goes unrecognised, the file falls through to the positional path,
    # and a two-endpoint row silently becomes a one-point line.
    raw_head = [str(c).strip().lower() for c in rows[0]]
    head = [re.sub(r'[\s_\-]*\d+$', '', h) for h in raw_head]
    has_header = any(h in _LAT_KEYS + _LON_KEYS for h in head)
    notes = []

    def idx(keys):
        for i, h in enumerate(head):
            if h in keys:
                return i
        return None

    if has_header:
        lat_cols = [i for i, h in enumerate(head) if h in _LAT_KEYS]
        lon_cols = [i for i, h in enumerate(head) if h in _LON_KEYS]
        name_i = idx(('name', 'line', 'line_name', 'id', 'lineno', 'line_no'))
        body = rows[1:]
    else:
        lat_cols, lon_cols, name_i, body = [], [], None, rows
        notes.append('no header row — columns read positionally as '
                     'name, latitude, longitude')

    lines = []
    if has_header and len(lat_cols) >= 2 and len(lon_cols) >= 2:
        for r in body:                       # one row per line: both endpoints
            try:
                a = (parse_angle(r[lat_cols[0]]), parse_angle(r[lon_cols[0]]))
                b = (parse_angle(r[lat_cols[1]]), parse_angle(r[lon_cols[1]]))
            except (IndexError, LinePlanError):
                continue
            lines.append(Line([a, b], str(r[name_i]) if name_i is not None else ''))
        return LinePlan(lines, 'CSV (endpoint per row)', notes=notes)

    # one row per point, grouped by whatever names the line
    groups, order = {}, []
    for r in body:
        try:
            if has_header:
                lat = parse_angle(r[lat_cols[0]])
                lon = parse_angle(r[lon_cols[0]])
                key = str(r[name_i]) if name_i is not None else ''
            else:
                nums = [c for c in r if _is_number(c)]
                if len(nums) < 2:
                    continue
                lat, lon = parse_angle(nums[0]), parse_angle(nums[1])
                key = str(r[0]) if not _is_number(r[0]) else ''
        except (IndexError, LinePlanError):
            continue
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((lat, lon))

    for key in order:
        pts = _clean(groups[key])
        if len(pts) >= 2:
            lines.append(Line(pts, key))
    if not lines:
        # every point in one ungrouped run is still a trackline
        allpts = _clean([p for key in order for p in groups[key]])
        if len(allpts) >= 2:
            lines = [Line(allpts, '')]
    if not lines:
        raise LinePlanError('found no pairs of coordinates in that file')
    return LinePlan(lines, 'CSV (point per row)', notes=notes)


def _is_number(tok) -> bool:
    try:
        float(str(tok).strip())
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
#  Front door
# --------------------------------------------------------------------------- #
def convert_projected(plan: LinePlan, zone=None, northern=True) -> LinePlan:
    """Turn eastings/northings into degrees, or refuse with a message that
    says what is missing. Never guesses a zone: an unknown zone is hundreds
    of miles of error with nothing on screen to show for it."""
    projected = any(looks_projected(p[0], p[1]) for ln in plan.lines for p in ln.points)
    if not projected:
        return plan
    if zone in (None, ''):
        raise LinePlanError(
            'those look like projected coordinates (eastings and northings, not '
            'degrees) and no UTM zone was given. Add the zone — Delaware Bay is '
            '18N — or export the plan in geographic coordinates.')
    for ln in plan.lines:
        ln.points = [utm_to_geographic(p[1], p[0], zone, northern) for p in ln.points]
    plan.crs = f'UTM zone {zone}{"N" if northern else "S"} (WGS84) -> geographic'
    plan.notes.append(f'converted from UTM zone {zone}{"N" if northern else "S"}')
    return plan


def sniff_and_read(blob: bytes, filename: str = '', zone=None,
                   northern: bool = True) -> LinePlan:
    """Read a line plan, choosing the reader by content first and name second.

    Content wins because an operator's file is as likely to be called
    `lines.txt` as anything else, and a `.csv` full of XML is still XML.
    """
    name = (filename or '').lower()
    if blob[:2] == b'PK':
        return convert_projected(read_kmz(blob), zone, northern)
    text = blob.decode('utf-8-sig', 'replace').strip()
    if not text:
        raise LinePlanError('that file is empty')

    head = text[:600].lstrip()
    try:
        if head.startswith('{'):
            plan = read_geojson(text)
        elif head.startswith('<'):
            low = head.lower()
            if '<gpx' in low:
                plan = read_gpx(text)
            elif '<kml' in low or 'placemark' in low:
                plan = read_kml(text)
            else:
                raise LinePlanError('that XML is neither KML nor GPX')
        elif re.search(r'^\s*LIN\s', text, re.M | re.I):
            plan = read_hypack_lnw(text)
        elif name.endswith('.json'):
            plan = read_geojson(text)
        else:
            plan = read_delimited(text)
    except LinePlanError:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LinePlanError(f'could not read that file: {exc}')

    plan = convert_projected(plan, zone, northern)
    for ln in plan.lines:
        for lat, lon in ln.points:
            if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                raise LinePlanError(
                    f'that file produced a position at {lat:.4f}, {lon:.4f}, which '
                    f'is not on the earth — check the column order and the zone.')
    return plan


def describe(plan: LinePlan) -> dict:
    """A summary an operator can check the import against BEFORE planning
    against it: how many lines, how long, which way they run. A line plan that
    read cleanly into the wrong place still looks fine as a row count."""
    import geometry as geo
    lines = plan.lines
    total = 0.0
    bearings, lengths = [], []
    for ln in lines:
        pts = [geo.Point(a, b) for a, b in ln.points]
        d = sum(geo.distance_nm(a, b) for a, b in zip(pts, pts[1:]))
        total += d
        lengths.append(d)
        if len(pts) >= 2:
            bearings.append(geo.course_deg(pts[0], pts[-1]))
    # A lawnmower alternates reciprocals, so the ARITHMETIC mean of its
    # bearings is meaningless: 020 and 200 average to 110, which is square
    # across the lines the vessel actually steers. Averaged as an AXIS instead
    # — the circular mean of the doubled angles, halved — which is what a line
    # direction is: 020 and 200 are the same line run two ways.
    axis = None
    if bearings:
        sx = sum(math.sin(2 * math.radians(b)) for b in bearings) / len(bearings)
        sy = sum(math.cos(2 * math.radians(b)) for b in bearings) / len(bearings)
        axis = (math.degrees(math.atan2(sx, sy)) / 2.0) % 180.0

    spacing = None
    if len(lines) > 1:
        gaps = []
        for a, b in zip(lines, lines[1:]):
            gaps.append(geo.distance_nm(geo.Point(*a.points[-1]),
                                        geo.Point(*b.points[0])))
        gaps = [g for g in gaps if g > 0]
        if gaps:
            spacing = sum(gaps) / len(gaps)
    first = lines[0].points[0] if lines else (0.0, 0.0)
    return {
        'format': plan.source_format,
        'crs': plan.crs,
        'lines': len(lines),
        'points': sum(len(ln.points) for ln in lines),
        'total_nm': round(total, 3),
        'mean_line_nm': round(sum(lengths) / len(lengths), 3) if lengths else 0.0,
        # The line AXIS, 0-180, and the first line's actual heading beside it.
        # Reporting one bearing for a lawnmower is what produced a summary
        # reading 110 for a survey running 020/200.
        'line_axis_deg': None if axis is None else round(axis, 1),
        'first_bearing_deg': round(bearings[0], 1) if bearings else None,
        'mean_gap_nm': round(spacing, 4) if spacing else None,
        'first_point': [round(first[0], 6), round(first[1], 6)],
        'notes': plan.notes,
    }
