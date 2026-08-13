"""Mission geometry: tracklines, survey line patterns, and the turns between.

Standard library only, like the rest of the planner.

WHY THIS EXISTS
    A leg used to be a distance, a course and a speed. That cannot answer
    "where is the vehicle at 14:20", so a time-varying field — a tide — could
    only ever be applied to a leg as one averaged number. Over a survey held
    on one ground through a tidal cycle that average is not merely imprecise,
    it is untrue: the vector mean of a reversing current is near zero, so the
    plan reads as slack water while the hull spends the day pushing against up
    to 2 kt.

    With geometry, every point of the mission has a position and a time, so
    the current can be read where and when the vehicle is actually there.

WHAT IS APPROXIMATED, AND WHY IT IS ENOUGH
    Rhumb lines on a mid-latitude parallel, not geodesics. Over a survey line
    or a transit leg at these latitudes the difference is metres, and the
    forecast being sampled is on a ~500 m mesh published hourly. Carrying a
    full geodesic solution would be precision the inputs cannot support.

    Positions are signed decimal degrees throughout — the wire format. Use
    `currents.fmt_pos` when showing one to a person.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

NM_PER_DEG = 60.0
M_PER_NM = 1852.0


# --------------------------------------------------------------------------- #
#  Points and rhumb-line arithmetic
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Point:
    lat: float
    lon: float

    def __iter__(self):
        yield self.lat
        yield self.lon


def _clamp_cos(lat_deg: float) -> float:
    """cos(latitude), floored so arithmetic near the poles cannot divide by
    zero. The planner never operates there, but a NaN escaping into a fuel
    figure would be far harder to spot than a nonsense position."""
    return max(math.cos(math.radians(lat_deg)), 1e-6)


def move(p: Point, course_deg: float, distance_nm: float) -> Point:
    """Rhumb-line step. Longitude is stretched by the cosine of the MID
    latitude of the step, which is what makes an east-west leg come out the
    right length instead of short by the latitude change."""
    c = math.radians(course_deg)
    dlat = distance_nm * math.cos(c) / NM_PER_DEG
    dlon = distance_nm * math.sin(c) / (NM_PER_DEG * _clamp_cos(p.lat + dlat / 2.0))
    return Point(p.lat + dlat, p.lon + dlon)


def distance_nm(a: Point, b: Point) -> float:
    dlat = (b.lat - a.lat) * NM_PER_DEG
    dlon = (b.lon - a.lon) * NM_PER_DEG * _clamp_cos((a.lat + b.lat) / 2.0)
    return math.hypot(dlat, dlon)


def course_deg(a: Point, b: Point) -> float:
    dlat = (b.lat - a.lat) * NM_PER_DEG
    dlon = (b.lon - a.lon) * NM_PER_DEG * _clamp_cos((a.lat + b.lat) / 2.0)
    if dlat == 0.0 and dlon == 0.0:
        return 0.0
    return math.degrees(math.atan2(dlon, dlat)) % 360.0


def interpolate(a: Point, b: Point, f: float) -> Point:
    return Point(a.lat + (b.lat - a.lat) * f, a.lon + (b.lon - a.lon) * f)


def turn_angle(from_course: float, to_course: float) -> float:
    """The signed change of heading, -180..180. A turn of 179 and one of -179
    are nearly the same manoeuvre and must not read as 358 degrees apart."""
    return (to_course - from_course + 180.0) % 360.0 - 180.0


# --------------------------------------------------------------------------- #
#  A run: one straight piece of the mission, with where and when
# --------------------------------------------------------------------------- #
@dataclass
class Run:
    """One straight piece of the path. `kind` distinguishes a survey line from
    a transit or a turn so the plan can report and cost them separately —
    line kilometres are the product, turn kilometres are overhead."""
    distance_nm: float
    course_deg: float
    start: Point
    end: Point
    kind: str = 'transit'          # 'transit' | 'line' | 'turn'
    name: str = ''

    def split(self, max_nm: float) -> list['Run']:
        """Chop into pieces no longer than `max_nm`, so a run cannot span more
        of the tide than the forecast resolves. Returns [self] when already
        short enough, so the common case allocates nothing."""
        # The epsilon is load-bearing: a 20 NM run against a 1 NM limit comes
        # back as 20.000000000000004 from the rhumb arithmetic and ceil() then
        # asks for 21 pieces, the last of them a few microns long.
        if max_nm <= 0 or self.distance_nm <= max_nm * (1 + 1e-9):
            return [self]
        n = int(math.ceil(self.distance_nm / max_nm - 1e-9))
        out = []
        for i in range(n):
            a = interpolate(self.start, self.end, i / n)
            b = interpolate(self.start, self.end, (i + 1) / n)
            out.append(Run(self.distance_nm / n, self.course_deg, a, b,
                           self.kind, self.name))
        return out


def track_runs(points: list[Point], name: str = '') -> list[Run]:
    """A polyline into its straight segments. Zero-length segments are dropped
    — a repeated waypoint is a typo, not a course change, and it would make
    `course_deg` meaningless for that piece."""
    out = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        d = distance_nm(a, b)
        if d <= 0:
            continue
        out.append(Run(d, course_deg(a, b), a, b, 'transit',
                       f'{name} {i + 1}' if name else ''))
    return out


# --------------------------------------------------------------------------- #
#  Survey line patterns
# --------------------------------------------------------------------------- #
@dataclass
class TurnModel:
    """How a vehicle gets from the end of one line to the start of the next.

    THIS IS AN ASSUMPTION, not a measurement — the same standing as the
    sea-state premium, and it is labelled as such wherever it reaches a
    surface. Turn time and fuel were simply absent before, which flattered
    every survey; a stated assumption is better than a silent zero, but it is
    still a dial to turn.

    Two geometries, chosen by the arithmetic rather than by preference:

      * spacing >= 2r — a simple 180 degree turn fits between the lines, arc
        length pi*r.
      * spacing < 2r  — it does not fit, and the vehicle must run out beyond
        the line end and come back: the omega or teardrop turn every surveyor
        has watched a boat make on close-spaced lines. Modelled as the same
        half-circle plus the run-out and run-back needed to make up the
        shortfall.
    """
    radius_nm: float = 0.0135          # ~25 m, DriX-8 scale. Assumption.
    speed_kt: float = 0.0              # 0 = hold the survey speed through it

    def path_nm(self, spacing_nm: float) -> float:
        r = max(self.radius_nm, 0.0)
        if r <= 0:
            return 0.0
        half_circle = math.pi * r
        if spacing_nm >= 2 * r:
            return half_circle
        # run out, turn, run back: the shortfall is covered twice
        return half_circle + 2 * (2 * r - spacing_nm)


@dataclass
class SurveyPattern:
    """A lawnmower, as a surveyor specifies one.

    `anchor` is the START of the first line. Lines run on `bearing`, each
    `length_nm` long, stepped `spacing_nm` apart on `step_bearing` (which
    defaults to 90 degrees right of the line bearing). Alternate lines run the
    reciprocal, which is what makes it a lawnmower and why the fuel cannot be
    taken from an averaged premium.
    """
    anchor: Point
    bearing_deg: float
    length_nm: float
    spacing_nm: float
    lines: int
    step_bearing_deg: float | None = None
    turn: TurnModel = field(default_factory=TurnModel)
    name: str = 'Survey'

    def step_bearing(self) -> float:
        if self.step_bearing_deg is not None:
            return self.step_bearing_deg % 360.0
        return (self.bearing_deg + 90.0) % 360.0

    def line_endpoints(self) -> list[tuple[Point, Point]]:
        out = []
        for i in range(max(int(self.lines), 0)):
            base = move(self.anchor, self.step_bearing(), i * self.spacing_nm)
            course = self.bearing_deg + (180.0 if i % 2 else 0.0)
            # Odd lines run the reciprocal, so they START at the far end —
            # which is where the previous line finished. Starting every line
            # at the same end would model a survey that teleports home after
            # each pass and would understate both time and fuel.
            start = base if i % 2 == 0 else move(base, self.bearing_deg,
                                                 self.length_nm)
            out.append((start, move(start, course, self.length_nm)))
        return out

    def runs(self, include_turns: bool = True) -> list[Run]:
        out: list[Run] = []
        ends = self.line_endpoints()
        for i, (a, b) in enumerate(ends):
            out.append(Run(distance_nm(a, b), course_deg(a, b), a, b, 'line',
                           f'{self.name} line {i + 1}'))
            if include_turns and i < len(ends) - 1:
                nxt = ends[i + 1][0]
                path = self.turn.path_nm(self.spacing_nm)
                if path > 0:
                    # Charged as a run of the turn's PATH length while
                    # displacing the vehicle only from b to the next start.
                    # The distance made good and the distance travelled are
                    # different things in a turn, and fuel follows the latter.
                    out.append(Run(path, course_deg(b, nxt), b, nxt, 'turn',
                                   f'{self.name} turn {i + 1}'))
        return out

    def total_line_nm(self) -> float:
        return max(int(self.lines), 0) * self.length_nm

    def total_turn_nm(self) -> float:
        n = max(int(self.lines), 0) - 1
        return max(n, 0) * self.turn.path_nm(self.spacing_nm)


def imported_lines_runs(lines, turn: TurnModel | None = None,
                        name: str = 'Survey') -> list[Run]:
    """Explicit survey lines — imported from a line plan rather than generated
    from a pattern — with the turns between them.

    The turn geometry is the same as the pattern's, but the spacing is not a
    parameter here: it is MEASURED between the end of one line and the start
    of the next, because an imported plan is whatever the planner drew. Lines
    that already run alternately give short gaps; a plan where every line
    starts at the same end gives a gap of a full line length, and the turn
    cost then reflects that honestly rather than being told otherwise.
    """
    turn = turn or TurnModel()
    out: list[Run] = []
    for i, pts in enumerate(lines):
        pts = [p if isinstance(p, Point) else Point(p[0], p[1]) for p in pts]
        for a, b in zip(pts, pts[1:]):
            d = distance_nm(a, b)
            if d > 0:
                out.append(Run(d, course_deg(a, b), a, b, 'line',
                               f'{name} line {i + 1}'))
        if i < len(lines) - 1:
            nxt = lines[i + 1][0]
            nxt = nxt if isinstance(nxt, Point) else Point(nxt[0], nxt[1])
            gap = distance_nm(pts[-1], nxt)
            path = max(turn.path_nm(gap), gap)
            if path > 0:
                out.append(Run(path, course_deg(pts[-1], nxt), pts[-1], nxt,
                               'turn', f'{name} turn {i + 1}'))
    return out


def runs_from(points_or_pattern, max_run_nm: float = 1.0,
              include_turns: bool = True) -> list[Run]:
    """Geometry -> runs, split fine enough for an hourly forecast on a ~500 m
    mesh. 1 NM is 7.5 minutes at 8 kt, which resolves both."""
    if isinstance(points_or_pattern, SurveyPattern):
        runs = points_or_pattern.runs(include_turns=include_turns)
    else:
        runs = track_runs(list(points_or_pattern))
    out: list[Run] = []
    for r in runs:
        out.extend(r.split(max_run_nm))
    return out
