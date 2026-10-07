# ============================================================================
# VENDORED FROM asv_core -- DO NOT EDIT THIS COPY.
#
#   source : asv_core/lineplan.py
#   sync   : python tools/vendor.py            (from the asv_core repo)
#   verify : python tools/vendor.py --check    (fails if this copy drifted)
#
# NO ABSOLUTE PATH APPEARS ABOVE, AND THAT IS DELIBERATE. Two of these repos
# publish scrubbed PUBLIC mirrors, and Transit's exporter ABORTS on anything
# matching [A-Z]:\Claude -- absolute paths name private sibling projects and
# point a cloner at a drive they do not have. A header naming a path would be
# publish-safe only for as long as somebody maintained a substitution rule for
# it in each exporter separately. Naming the repo instead is safe by
# construction, in every consumer, including ones that do not exist yet.
#
# A copy rather than an import because this repo has to stand on its own: it is
# a separate repository, and this file is opened by path rather than imported
# as a package. The old trade was drift -- a vendored file did not follow its
# source, which is how the estate grew three copies of currents.py. The --check
# above removes that trade: this copy cannot diverge without failing a suite.
#
# THIS CONSUMER, SPECIFICALLY:
# This repo WROTE lineplan.py. Two things arrive with the core body:
#
#   * SHAPEFILE SUPPORT. The docstring here used to explain why shapefiles
#     were NOT read -- "guessing a datum from a partial WKT string is exactly
#     the silent-failure class this module exists to avoid". WorldView went
#     and did it properly, guard and all, so the excuse is retired.
#   * NO MORE `import geometry`. describe() used this repo's geometry.py for
#     distance and course; the core carries the same arithmetic inline --
#     60 NM/deg, cosine floored at 1e-6, mid-latitude -- so the numbers are
#     unchanged and the module no longer needs a sibling that only exists here.
#
# Edit the core file and re-run the sync. Everything below is verbatim.
# ============================================================================

"""Reading a survey line plan or a trackline out of the files planners produce.

Standard library only.

  * `describe()` used the planner's `geometry` module for plane-sailing
    distance and course. Those two formulas are inlined below instead of
    dragging a mission-geometry module across for them.
  * the HTTP layer is this app's — `worldview/routers/lineplan.py` — because
    the planner speaks `http.server` and this speaks FastAPI. The reader itself
    is untouched.

Everything else is the fuel planner's file, and its tests came with it.

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
      * Shapefile   — zipped set, or a bare .shp. SEE THE NOTE BELOW: this was
                      refused for a long time, and what changed is that the
                      .prj is now READ rather than guessed at.

    SHAPEFILES, AND WHY THEY WERE REFUSED UNTIL 2026-08-18
        The geometry was never the problem. The CRS lives in a sidecar `.prj`
        as WKT, and a half-understood WKT that silently mis-places a survey is
        exactly the failure this module exists to prevent — so the honest
        answer, with no parser, was no.

        `shapefile_io.parse_prj` is that parser, vendored from the transit
        calculator, and it is honest in the same way: it recognises the two
        shapes that matter — WGS84 UTM with a zone, and plain geographic — and
        answers `unknown` for everything else rather than approximating. So:

          .prj says geographic      -> read as lon/lat
          .prj says WGS84 UTM zone  -> projected back, same maths as a zoned CSV
          .prj says something else  -> REFUSED, naming what it saw
          no .prj at all            -> refused UNLESS the coordinates are
                                       already inside +-180/+-90, which is not
                                       a guess about the datum but the
                                       observation that projected metres cannot
                                       be mistaken for degrees

        A BARE .shp CARRIES NO .prj, because a shapefile is five files and a
        browser file picker hands over one. It is accepted so that a plainly
        geographic file is not turned away on a technicality — and refused the
        moment its numbers are too big to be degrees. **Zip the set** and the
        CRS comes with it.

    NOT accepted, deliberately, each for a reason rather than for lack of time:
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
from struct import error as struct_error


def _utm():
    """The vendored `utm.py`, imported the way `shapefile_io` already is.

    Lazily, and by both spellings: inside the core `asv_core` is a package, and in
    an app repo this file sits at the root beside a flat `utm.py` with no package
    around either.
    """
    try:
        from . import utm as _m
    except ImportError:                     # pragma: no cover - the vendored copies
        import utm as _m
    return _m


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
    """UTM (WGS84) -> (lat, lon), delegated to `utm.py`.

    THE SERIES USED TO LIVE HERE, AND A SECOND COPY LIVED IN Transit/geo.py.
    They were measured against each other over seven points from the equator to
    72 N in both hemispheres and agreed to 1.3 nanometres, so consolidating them
    is a rename rather than a change of answer -- and it moved the ZONE GUARD
    below to the copy that did not have one, where an out-of-range zone was
    writing shapefiles four million kilometres out.

    This is a WRAPPER rather than a plain alias, which the estate normally avoids,
    because it converts an exception TYPE at a module boundary: this module
    promises `LinePlanError` and its callers in two repos catch exactly that.
    Adapting the error is a real contract difference, not a tidier line.
    """
    try:
        return _utm().utm_to_geographic(easting, northing, zone, northern)
    except _utm().UtmError as e:
        raise LinePlanError(str(e)) from e


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


def read_shapefile(blob: bytes, filename: str = '', zone=None,
                   northern: bool = True) -> LinePlan:
    """A zipped shapefile set, or a bare `.shp`.

    EVERY line reads, not just the longest. The transit calculator this reader
    came from takes the longest record because it plans ONE passage; a survey
    line plan is a set of parallel lines and dropping all but one of them would
    be the same file arriving as a different survey.

    A polygon reads as its boundary. Somebody exporting a survey AREA as a
    polygon and importing it as the area is doing something sensible, and a
    ring is a closed polyline as far as this is concerned.
    """
    # VENDORED FLAT OR AS A PACKAGE, and this file has to work either way: it
    # sits in `worldview/services/` (a package) in one consumer and at the repo
    # ROOT in the others, where a relative import is a fatal ImportError.
    try:
        from . import shapefile_io as sio
    except ImportError:
        import shapefile_io as sio

    try:
        r = sio.read_bytes(blob, filename)
    except ValueError as exc:
        raise LinePlanError(str(exc))
    except (OSError, KeyError, IndexError, struct_error) as exc:
        raise LinePlanError(f'could not read that shapefile: {exc}')

    if not r['records']:
        raise LinePlanError('that shapefile holds no geometry — every record is null')

    crs = r['crs'] or {'kind': 'none'}
    kind = crs.get('kind')
    # A zone the CALLER supplied overrides a .prj that did not say — the same
    # courtesy a projected CSV gets, and the only way to read a bare .shp of
    # eastings at all.
    if kind not in ('utm', 'geographic') and zone:
        crs = {'kind': 'utm', 'zone': int(zone), 'hemisphere': 'N' if northern else 'S'}
        kind = 'utm'

    plan = LinePlan(source_format='ESRI Shapefile')
    if r.get('member'):
        plan.notes.append(f"read {r['member']} from the zip")

    if kind == 'utm':
        plan.crs = (f"UTM zone {crs['zone']}{crs['hemisphere']} (WGS84) -> geographic")
        plan.notes.append(f"converted from UTM zone {crs['zone']}{crs['hemisphere']}")
    elif kind == 'geographic':
        plan.crs = 'WGS84 geographic (from .prj)'
    elif kind == 'none':
        # NOT A GUESS ABOUT THE DATUM — an observation that projected metres
        # cannot be mistaken for degrees. See the module header.
        flat = [pt for rec in r['records'] for part in rec.parts for pt in part]
        if not all(abs(x) <= 180 and abs(y) <= 90 for x, y in flat):
            raise LinePlanError(
                'that shapefile has no .prj and its coordinates are not degrees, so '
                'there is no way to know where it is. Zip the .shp with its .prj and '
                'load that, or give the UTM zone.')
        plan.crs = 'WGS84 geographic (assumed — no .prj)'
        plan.notes.append('no .prj: the coordinates are within degrees, so they were '
                          'taken as WGS84 lat/lon. Zip the set to carry the CRS.')
    else:
        # THE REASON, NOT THE WKT. `parse_prj` says why it refused — "it is
        # Transverse Mercator but not UTM", "its coordinates are in Foot Us,
        # not metres" — and an operator can act on that where a 70-character
        # slice of WKT only tells them the load failed.
        why = crs.get('why')
        name = (r['prj'] or '').split('"')[1] if '"' in (r['prj'] or '') else (r['prj'] or '?')
        raise LinePlanError(
            f'that shapefile is in a coordinate system this cannot read — {name}: '
            + (f'{why}. ' if why else '')
            + 'Re-export it as WGS84 geographic or WGS84 UTM.')

    for rec in r['records']:
        name = ''
        for key in ('NAME', 'Name', 'name', 'LINE', 'Line', 'LINE_NAME', 'ID'):
            if rec.attrs.get(key) not in (None, ''):
                name = str(rec.attrs[key]).strip()
                break
        for part in rec.parts:
            pts = []
            for x, y in part:
                if kind == 'utm':
                    pts.append(utm_to_geographic(x, y, crs['zone'],
                                                 crs['hemisphere'] == 'N'))
                else:
                    pts.append((y, x))          # shapefiles store x=lon, y=lat
            pts = _clean(pts)
            if len(pts) >= 2:
                plan.lines.append(Line(points=pts, name=name))

    if not plan.lines:
        raise LinePlanError('that shapefile holds only single points, not lines')
    if r['null_count']:
        plan.notes.append(f"{r['null_count']} null shape(s) skipped")
    return plan


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
    # A BARE .shp, BY ITS MAGIC NUMBER. The shapefile header opens with the file
    # code 9994 big-endian; nothing else this reader accepts begins with a NUL,
    # so this is a positive identification rather than a fallback on the
    # extension — and it reads a shapefile called `lines.dat` correctly.
    if blob[:4] == bytes((0x00, 0x00, 0x27, 0x0a)):
        return read_shapefile(blob, filename, zone, northern)
    if blob[:2] == b'PK':
        # BOTH KMZ AND A ZIPPED SHAPEFILE ARE ZIPS, so the container cannot
        # decide this — what is INSIDE it has to. Reading a shapefile set as a
        # KMZ finds no placemarks and reports an empty file, which is a wrong
        # answer that looks like a clean one.
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                members = [n.lower() for n in z.namelist()]
        except zipfile.BadZipFile:
            raise LinePlanError('that file starts like a zip but will not open as one')
        if any(n.endswith('.shp') for n in members):
            return read_shapefile(blob, filename, zone, northern)
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


#: Plane sailing, on (lat, lon) tuples. VENDORED IN rather than imported: the
#: fuel planner this module came from has a `geometry` module and this app does
#: not, and pulling a whole mission-geometry module across for two formulas
#: would be the tail wagging the dog. Same arithmetic, same results — the
#: summary's own tests came over with it and pass unchanged.
_NM_PER_DEG = 60.0


def _mid_cos(lat_a: float, lat_b: float) -> float:
    """cos of the mid latitude, floored so nothing near a pole divides by zero."""
    return max(math.cos(math.radians((lat_a + lat_b) / 2.0)), 1e-6)


def _distance_nm(a, b) -> float:
    dlat = (b[0] - a[0]) * _NM_PER_DEG
    dlon = (b[1] - a[1]) * _NM_PER_DEG * _mid_cos(a[0], b[0])
    return math.hypot(dlat, dlon)


def _course_deg(a, b) -> float:
    dlat = (b[0] - a[0]) * _NM_PER_DEG
    dlon = (b[1] - a[1]) * _NM_PER_DEG * _mid_cos(a[0], b[0])
    if dlat == 0.0 and dlon == 0.0:
        return 0.0
    return math.degrees(math.atan2(dlon, dlat)) % 360.0


def describe(plan: LinePlan) -> dict:
    """A summary an operator can check the import against BEFORE planning
    against it: how many lines, how long, which way they run. A line plan that
    read cleanly into the wrong place still looks fine as a row count."""
    lines = plan.lines
    total = 0.0
    bearings, lengths = [], []
    for ln in lines:
        pts = list(ln.points)
        d = sum(_distance_nm(a, b) for a, b in zip(pts, pts[1:]))
        total += d
        lengths.append(d)
        if len(pts) >= 2:
            bearings.append(_course_deg(pts[0], pts[-1]))
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
            gaps.append(_distance_nm(a.points[-1], b.points[0]))
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
