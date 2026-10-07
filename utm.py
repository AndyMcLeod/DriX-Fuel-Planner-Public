# ============================================================================
# VENDORED FROM asv_core -- DO NOT EDIT THIS COPY.
#
#   source : asv_core/utm.py
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
# Carried in because lineplan.py imports it: the UTM series that used to sit
# inside that file now lives here, shared with the transit tool, and the
# zone guard lineplan already had is now the one definition of it.
#
# Edit the core file and re-run the sync. Everything below is verbatim.
# ============================================================================

"""UTM <-> geographic (WGS84) — one implementation for the estate.

Snyder's series (USGS Professional Paper 1395), accurate to well under a
millimetre inside a zone. Measured against an independent numerical integration
of the meridian arc: worst 0.95 mm at 72 N, 0.12 mm at Lewes. See
`tests/utm.py`, which checks it against that integration and against the
definitional anchors rather than against another copy of the same series.

WHY THIS IS ITS OWN MODULE AND NOT PART OF geodesy.py. `lineplan.py` needs the
inverse and is vendored into Fuel and WorldView; `geodesy.py` is vendored only
into Transit. Putting UTM in geodesy would drag the whole geodesy module — and
`contracts.py` behind it — into two repos that have not adopted it, which is a
much larger change riding along on this one. A small focused module can be
vendored to exactly the repos that need it.

THE ZONE GUARD IS THE POINT OF THE EXTRACTION, NOT A TIDY-UP. There were two
implementations of this arithmetic: `lineplan.utm_to_geographic`, which refused a
zone outside 1..60, and `Transit/geo.py`, which did not — in either direction,
and not in `utm_zone_for` either. Transit's export path takes `utm_zone` straight
out of a request body, so `{"utm_zone": 99}` wrote a complete, well-formed
shapefile with a .prj naming "WGS_1984_UTM_Zone_99N" at central meridian 411 and
a first vertex at easting 4,096,126,768 m — four million kilometres, and the file
opens. Measured, along with zone 61 (half a billion metres north), zone -3, and
zone 0, which is FALSY and so was silently ignored: the caller asked for UTM and
got a geographic shapefile with no complaint.

`utm_zone_for` had the same hole from the other end. A longitude of exactly
180.0 — an ordinary way to write the antimeridian — returned zone 61, and the
export path calls it on the caller's first point when the zone is "auto". The
longitude is normalised here before the zone is derived.

Both spellings the estate already used are kept, on one body:

    utm_to_geographic(e, n, zone, northern=True)   # lineplan.py's
    utm_to_ll(e, n, zone, hemisphere='N')          # Transit geo.py's

That is not indecision. Renaming either would touch call sites in three repos
for no behavioural gain, and the whole claim of this extraction is that nothing
moves except the guard.
"""

from __future__ import annotations

import math

__all__ = ['UtmError', 'utm_zone_for', 'll_to_utm', 'utm_to_ll',
           'utm_to_geographic', 'valid_zone', 'check_zone',
           'A', 'F', 'E2', 'K0']

# WGS84, and the UTM projection constants. F is the DEFINING flattening, not one
# derived from the axes -- the same choice geodesy.py makes, and the reason the
# two agree to the last bits rather than to a nanometre.
A = 6378137.0                     # semi-major axis, m
F = 1 / 298.257223563             # flattening (defining)
E2 = F * (2 - F)                  # first eccentricity squared
EP2 = E2 / (1 - E2)               # second eccentricity squared
K0 = 0.9996                       # scale on the central meridian
FALSE_EASTING = 500000.0
FALSE_NORTHING_N = 0.0
FALSE_NORTHING_S = 10000000.0

ZONE_MIN, ZONE_MAX = 1, 60


class UtmError(ValueError):
    """A UTM argument that does not describe a place on Earth.

    A ValueError subclass so a caller that already catches ValueError keeps
    working, and so `lineplan.LinePlanError` -- also a ValueError -- can carry it
    across that module's boundary without widening what it promises.
    """


def valid_zone(zone) -> bool:
    """True for a zone number that exists. There are sixty, and they are integers.

    A ZONE MUST BE INTEGRAL, and the first version of this did not say so: it went
    through `int()`, which TRUNCATES, so 60.5 read as zone 60 and 18.7 as zone 18 —
    a caller off by a fraction got a real zone and no complaint. `18.0` and `"18"`
    are still accepted, because those are zone 18 written differently rather than
    a different number.

    `True` is rejected even though it is 1 in Python. A boolean arriving where a
    zone is expected means a caller passed a flag, not a place.
    """
    if isinstance(zone, bool):
        return False
    try:
        z = float(zone)
    except (TypeError, ValueError):
        return False
    if z != int(z):
        return False
    return ZONE_MIN <= int(z) <= ZONE_MAX


def check_zone(zone) -> int:
    """The one place a zone is checked, and the int it is checked to.

    Public because `shapefile_io.utm_wkt` needs the same answer: it names the CRS
    in a .prj, and a guard on the arithmetic alone would let a wrong .prj be
    written beside right numbers.
    """
    if not valid_zone(zone):
        raise UtmError('UTM zone %r does not exist (there are %d..%d, whole numbers)'
                       % (zone, ZONE_MIN, ZONE_MAX))
    return int(float(zone))


def _central_meridian(zone: int) -> float:
    """Degrees. Zone 1 is centred on 177 W; each zone is 6 degrees wide."""
    return (zone - 1) * 6 - 180 + 3


def _norm_lon(lon: float) -> float:
    """Longitude into [-180, 180).

    NOT imported from geodesy.wrap_lon, deliberately: this module is vendored to
    repos that have not adopted geodesy, and pulling it in for four lines of
    arithmetic would defeat the reason this is a separate module. The two agree;
    tests/utm.py asserts that they do, so the copy cannot drift into a second
    answer.
    """
    return (float(lon) + 180.0) % 360.0 - 180.0


def utm_zone_for(lat, lon):
    """(zone, hemisphere) for a position, with the Norway and Svalbard
    exceptions — a track crossing into them would otherwise be given a zone that
    is not in the EPSG registry.

    THE LONGITUDE IS NORMALISED FIRST. Exactly 180.0 is an ordinary way to write
    the antimeridian and used to yield zone 61, which does not exist; anything
    beyond 180 yielded worse. Wrapping makes 180.0 read as zone 1, which is what
    -180.0 already gave.
    """
    lat = float(lat)
    lon = _norm_lon(lon)
    zone = int((lon + 180) / 6) + 1
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    elif 72 <= lat < 84:
        if 0 <= lon < 9:
            zone = 31
        elif 9 <= lon < 21:
            zone = 33
        elif 21 <= lon < 33:
            zone = 35
        elif 33 <= lon < 42:
            zone = 37
    return check_zone(zone), ('N' if lat >= 0 else 'S')


def ll_to_utm(lat, lon, zone=None, hemisphere=None):
    """(lat, lon) -> (easting, northing, zone, hemisphere), WGS84.

    `zone` may be FORCED so every vertex of one line lands in a single zone: a
    track crossing a zone boundary must not have half its vertices renumbered, or
    the exported shapefile is nonsense under its own .prj. A forced zone far from
    the position is therefore legal and gives a large easting on purpose — what is
    refused is a zone that does not exist, which is a different thing.
    """
    if zone is None:
        zone, auto_hemi = utm_zone_for(lat, lon)
        hemisphere = hemisphere or auto_hemi
    zone = check_zone(zone)
    hemisphere = (hemisphere or ('N' if float(lat) >= 0 else 'S')).upper()

    lon0 = math.radians(_central_meridian(zone))
    p, l = math.radians(float(lat)), math.radians(float(lon))
    n = A / math.sqrt(1 - E2 * math.sin(p) ** 2)
    t = math.tan(p) ** 2
    c = EP2 * math.cos(p) ** 2
    a = math.cos(p) * (l - lon0)
    m = A * ((1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256) * p
             - (3 * E2 / 8 + 3 * E2 ** 2 / 32 + 45 * E2 ** 3 / 1024) * math.sin(2 * p)
             + (15 * E2 ** 2 / 256 + 45 * E2 ** 3 / 1024) * math.sin(4 * p)
             - (35 * E2 ** 3 / 3072) * math.sin(6 * p))

    easting = K0 * n * (a + (1 - t + c) * a ** 3 / 6
                        + (5 - 18 * t + t ** 2 + 72 * c - 58 * EP2) * a ** 5 / 120) + FALSE_EASTING
    northing = K0 * (m + n * math.tan(p) * (
        a ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * a ** 4 / 24
        + (61 - 58 * t + t ** 2 + 600 * c - 330 * EP2) * a ** 6 / 720))
    if hemisphere == 'S':
        northing += FALSE_NORTHING_S
    return easting, northing, zone, hemisphere


def utm_to_geographic(easting, northing, zone, northern=True):
    """UTM (WGS84) -> (lat, lon). Standard series inverse, good to millimetres
    over a zone — far beyond what a line plan needs, but the arithmetic is no
    harder than an approximation would be.

    THIS BODY IS lineplan.py's, not Transit's. The two were measured side by side
    over seven points from the equator to 72 N and both hemispheres: identical to
    the last bit in the north, 1.3 nanometres apart in the south, where the two
    put the division by K0 at different steps and IEEE-754 multiplication is not
    associative. lineplan's was already the vendored body in three repos, so
    keeping it is the choice that moves the fewest consumers.
    """
    zone = check_zone(zone)
    x = float(easting) - FALSE_EASTING
    y = float(northing) - (FALSE_NORTHING_N if northern else FALSE_NORTHING_S)
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    m = y / K0
    mu = m / (A * (1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1 = EP2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = A / math.sqrt(1 - E2 * math.sin(phi1) ** 2)
    r1 = A * (1 - E2) / (1 - E2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * EP2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * EP2 - 3 * c1 ** 2)
        * d ** 6 / 720)
    lon = (d
           - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * EP2 + 24 * t1 ** 2)
           * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon) + _central_meridian(zone)


def utm_to_ll(easting, northing, zone, hemisphere='N'):
    """UTM -> (lat, lon) degrees, WGS84. Transit's spelling of the inverse; the
    same body, with the hemisphere given as a letter rather than a flag."""
    return utm_to_geographic(easting, northing, zone,
                             northern=str(hemisphere).upper() != 'S')
