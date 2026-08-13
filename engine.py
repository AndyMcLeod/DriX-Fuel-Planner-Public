"""DriX mission fuel planning engine.

Pure computation, no I/O and no web framework — importable from anything, and
directly testable. The models come from model.json; every coefficient there is
traceable back to DriX_fuel_efficiency_analysis.xlsx.

The chain for one leg is:

    required SOG  ->  RPM in benign water        (per-gondola speed law, inverted)
                  ->  RPM premium                (sea state + heading effect)
                  ->  actual RPM                 (benign RPM * (1 + premium))
                  ->  fuel rate L/h              (fuel_vs_rpm — shared, engine-side)
                  ->  litres                     (rate * hours)

Gondolas: the hull has carried two — the EM2040 (currently fitted) and the
larger, heavier EM712 (2024 trials). Each gondola carries its own speed law,
and since the Aug 2026 MCAP refit the EM2040 also carries its own MEASURED
quadratic fuel law (valid 1400-3100 rpm); the EM712 uses the shared linear
law from the 2024 trials. The pre-refit transfer estimate agreed with the
measurement to ~6% at survey speed.

Three honesty rails run through the whole thing:

  * Each gondola's fuel law has a fitted RPM window; anything outside it is
    flagged as an extrapolation, per leg. At 8 kt the EM712 needs ~2616 rpm
    (outside its 1020-2500 window) while the EM2040 needs ~2172 rpm (inside
    its 1400-3100 window).
  * The sea-state premium is an assumption, not a fit. Every result carries
    a sensitivity band so a plan is never read as a single number.
  * The reserve floor is a GAUGE READING, not a number of litres. Since the
    Aug 2026 calibration the litres-per-indicated-point scale is measured
    (2.06 +/- 0.11, four sigma below the 2.50 a linear 250 L tank implies), so
    every plan also reports what the needle will actually do — the gauge_*
    fields. Those figures need no capacity assumption at all, which is the
    point: choosing a larger tank does not buy back endurance the measured
    scale has already taken away.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Literal

import geometry

MODEL_PATH = Path(__file__).with_name('model.json')

KM_PER_NM = 1.852
# Distances from home at which a mission gets a time mark — the outer callout on
# the way out and the approach callout coming back, at each radius. Distance is
# measured ALONG THE PLANNED TRACK (run made good on the first leg, distance
# still to run on the last), because the planner has no geometry: legs are
# distances and courses, never positions. A leg shorter than a radius is
# reported as already inside it rather than silently skipped.
WAYPOINTS_KM = (13.0, 26.0)

# NAUTICAL MILES PER ONE UNIT — so converting a waypoint to NM MULTIPLIES by
# this and never divides. The engine is NM-native everywhere else (legs are
# distance_nm), so a waypoint is converted once, on the way in, and the unit is
# carried only to label the result.
NM_PER_WAYPOINT_UNIT = {'km': 1.0 / KM_PER_NM, 'nm': 1.0}
DEFAULT_WAYPOINT_UNIT = 'km'

# Engine idle, in rpm. A DRIVETRAIN fact rather than a gondola one — direct
# drive, the engine idles at ~1000 rpm whatever gondola is fitted — so it stands
# in when a gondola carries no measured loiter block of its own.
IDLE_RPM = 1005.0

# Deprecated alias. The feature is "mission waypoints" now; this name is kept
# because it is the one the published API documented.
HOME_MARKS_KM = WAYPOINTS_KM


def default_waypoints(unit: str = DEFAULT_WAYPOINT_UNIT) -> tuple[float, ...]:
    """The default radii, expressed in `unit`.

    The defaults are the SAME PHYSICAL DISTANCES whatever unit is asked for —
    13 km and 26 km, which read as 7.02 and 14.04 in NM. Choosing a unit is a
    display decision; it must never quietly move a mark to a different place on
    the track.
    """
    nm_per_unit = _nm_per_unit(unit)
    return tuple(km / KM_PER_NM / nm_per_unit for km in WAYPOINTS_KM)


def _nm_per_unit(unit: str) -> float:
    """Nautical miles per one unit of `unit`. Multiply a waypoint by this."""
    try:
        return NM_PER_WAYPOINT_UNIT[str(unit).strip().lower()]
    except KeyError:
        raise ValueError(
            f'unknown waypoint unit "{unit}" — choose from '
            f'{", ".join(sorted(NM_PER_WAYPOINT_UNIT))}') from None


def load_model(path: Path | str = MODEL_PATH) -> dict[str, Any]:
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


class GaugeProfile:
    """Litres per indicated point as a function of level.

    A scalar sufficed while the gauge was assumed linear. It is not enough once
    the drawings are honoured: reading 'A' says the tank holds 250 L and the
    measured band holds 2.06 L/point, which forces the rest of the range to be
    richer. So the needle moves at different rates in different bands, and both
    "how much may I spend" and "where will the needle be" become integrals.

    Segments are (low_pct, high_pct, litres_per_point), contiguous and ordered.
    A linear gauge is simply one segment, which is how reading 'B' is expressed —
    same code path, no special case.
    """

    def __init__(self, segments: list[tuple[float, float, float]]):
        if not segments:
            raise ValueError('a gauge profile needs at least one segment')
        self.segments = sorted(segments, key=lambda s: s[0])
        for (_, hi, _), (lo2, _, _) in zip(self.segments, self.segments[1:]):
            if abs(hi - lo2) > 1e-9:
                raise ValueError(f'gauge profile has a gap or overlap at {hi}%')

    @property
    def uniform_l_per_point(self) -> float | None:
        """The single rate, when there is only one — else None."""
        return self.segments[0][2] if len(self.segments) == 1 else None

    def litres_between(self, lo_pct: float, hi_pct: float) -> float:
        """Litres held between two indicated levels. Negative if lo > hi."""
        if hi_pct < lo_pct:
            return -self.litres_between(hi_pct, lo_pct)
        total = 0.0
        for s_lo, s_hi, lpp in self.segments:
            overlap = min(hi_pct, s_hi) - max(lo_pct, s_lo)
            if overlap > 0:
                total += overlap * lpp
        return total

    def level_after(self, start_pct: float, litres: float) -> float:
        """Where the needle sits after burning `litres` from `start_pct`.

        Walks the segments downward so a burn that crosses a band boundary is
        split at it. Returns a negative level if the burn exceeds what the
        gauge represents, which the caller should surface rather than clamp.
        """
        level, left = start_pct, litres
        for s_lo, s_hi, lpp in reversed(self.segments):
            if level <= s_lo or left <= 0:
                continue
            available = (min(level, s_hi) - s_lo) * lpp
            if left < available:
                return level - left / lpp
            left -= available
            level = s_lo
        return level - (left / self.segments[0][2] if left > 0 else 0.0)


# --------------------------------------------------------------------------- #
#  Inputs
# --------------------------------------------------------------------------- #

LegKind = Literal['transit', 'survey']


@dataclass
class Leg:
    """One phase of a mission.

    A survey is flown as a lawnmower: alternate lines run the reciprocal
    course. Give `lines` and `line_length_nm` to model that explicitly;
    `distance_nm` is then derived and `course_deg` is the bearing of line 1.

    **Why the line count matters, and why averaging the premium is wrong.**
    The heading effect is a cosine on required RPM, so over a reciprocal pair
    the PREMIUM averages to zero — which is what this leg used to assume. But
    fuel is a convex (quadratic) function of RPM, so the average of the two
    fuel RATES is strictly greater than the rate at the average premium. The
    line into the weather costs more than the line downwind saves. Cancelling
    the premium therefore understates survey fuel, and the error grows with the
    square of the wind: about +0.9% at 12 kt, +7% at 20 kt, +17% at 25 kt.

    An ODD number of lines cannot cancel even in principle — one direction gets
    an extra line — which the old single-distance input could not express at
    all. Three lines into a 20 kt wind costs about +21% against the cancelled
    figure.

    A survey without an explicit line count is treated as one reciprocal PAIR,
    which is what this leg has always meant, now with the convexity honoured.
    """
    name: str
    kind: LegKind
    distance_nm: float
    speed_kt: float
    course_deg: float = 0.0
    lines: int | None = None
    line_length_nm: float | None = None
    # Time held on station on this leg, making no way — a launch or recovery
    # delay, a hold for traffic, a sensor problem. Charged at the gondola's
    # IDLE burn, and taken at the START of the leg: the vehicle waits, then
    # runs the distance. That placement is what makes a hold on the return leg
    # delay the arrival home — the reason the end-of-leg convention this
    # replaced was wrong — and it is still a convention, not a measurement:
    # a real delay lands somewhere inside the leg, and this model puts the
    # whole lump before the first mile.
    loiter_hours: float = 0.0
    # -- Weather on THIS leg. A mission runs for two days at survey speed, so
    # the wind that was on the bow going out is not the wind coming home, and a
    # tide turns twice in between. Each is None to mean "use the mission-wide
    # Environment", so a plan that does not set them behaves exactly as before.
    # Speed and direction are independent: setting only a speed keeps the
    # mission's direction, which is what an operator changing strength alone
    # expects.
    wind_speed_kt: float | None = None
    wind_from_deg: float | None = None
    current_speed_kt: float | None = None
    current_set_deg: float | None = None
    # Sea state is per-leg too (Andy, 2026-08-11). It was held back on the
    # grounds that the premium rests on a single measured anchor — but that is
    # an argument about how much the DIAL can be trusted, not about whether it
    # should be one number for two days. A survey flown into a building sea is
    # an ordinary mission, and averaging it away helped nobody.
    wmo_sea_state: int | None = None
    # -- GEOMETRY (2026-08-13). Optional, and absent means every figure below
    # is computed exactly as it always was — pinned by a test.
    #
    # `track` is a polyline of (lat, lon) for a transit; `pattern` is a survey
    # lawnmower as a dict of the SurveyPattern fields. Give either and the
    # leg's distance and course are DERIVED from it, and each straight piece
    # of the path carries its own position and time.
    #
    # WHY: without geometry a leg is a distance and a course, which cannot say
    # where the vehicle is at 14:20 — so a tide could only ever be applied as
    # one averaged number per leg. Over a survey held on one ground through a
    # tidal cycle that average is not just coarse, it is false: the vector
    # mean of a reversing current is near zero, so the plan reads slack while
    # the hull works against 2 kt all day.
    track: list | None = None
    pattern: dict | None = None
    # Explicit survey lines, as IMPORTED from a line plan rather than
    # generated from a pattern. A real plan is whatever the surveyor drew —
    # lines of differing length, a gap where a shoal is, a run-in at one end —
    # and forcing that through a regular pattern would quietly straighten it.
    survey_lines: list | None = None
    turn_radius_nm: float = 0.0

    def environment(self, env: 'Environment') -> 'Environment':
        """The mission environment with this leg's own weather laid over it."""
        pick = lambda mine, theirs: theirs if mine is None else mine  # noqa: E731
        return Environment(
            wmo_sea_state=pick(self.wmo_sea_state, env.wmo_sea_state),
            wind_speed_kt=pick(self.wind_speed_kt, env.wind_speed_kt),
            wind_from_deg=pick(self.wind_from_deg, env.wind_from_deg),
            current_speed_kt=pick(self.current_speed_kt, env.current_speed_kt),
            current_set_deg=pick(self.current_set_deg, env.current_set_deg))

    def geometry_runs(self, max_run_nm: float = 1.0,
                      include_turns: bool = True) -> list | None:
        """The path as straight pieces with positions, or None when this leg
        carries no geometry. One seam: everything geometric reads this."""
        if self.pattern:
            spec = dict(self.pattern)
            turn = geometry.TurnModel(
                radius_nm=float(spec.pop('turn_radius_nm', 0.0) or 0.0),
                speed_kt=float(spec.pop('turn_speed_kt', 0.0) or 0.0))
            pattern = geometry.SurveyPattern(
                anchor=geometry.Point(float(spec['anchor'][0]), float(spec['anchor'][1])),
                bearing_deg=float(spec['bearing_deg']),
                length_nm=float(spec['length_nm']),
                spacing_nm=float(spec['spacing_nm']),
                lines=int(spec['lines']),
                step_bearing_deg=(None if spec.get('step_bearing_deg') is None
                                  else float(spec['step_bearing_deg'])),
                turn=turn, name=self.name)
            return geometry.runs_from(pattern, max_run_nm, include_turns)
        if self.survey_lines:
            turn = geometry.TurnModel(radius_nm=float(self.turn_radius_nm or 0.0))
            runs = geometry.imported_lines_runs(
                [[geometry.Point(float(a), float(b)) for a, b in line]
                 for line in self.survey_lines],
                turn if include_turns else geometry.TurnModel(radius_nm=0.0),
                self.name)
            out = []
            for r in runs:
                out.extend(r.split(max_run_nm))
            return out
        if self.track and len(self.track) >= 2:
            pts = [geometry.Point(float(a), float(b)) for a, b in self.track]
            return geometry.runs_from(pts, max_run_nm)
        return None

    def resolved_distance_nm(self) -> float:
        """Ground covered. Derived from the geometry when there is any, then
        from the lines, then the plain distance."""
        runs = self.geometry_runs()
        if runs is not None:
            return sum(r.distance_nm for r in runs)
        if self.kind == 'survey' and self.lines:
            return self.lines * float(self.line_length_nm or 0.0)
        return self.distance_nm

    def line_plan(self) -> list[tuple[float, float]]:
        """(distance_nm, course_deg) for each run this leg is flown as."""
        runs = self.geometry_runs()
        if runs is not None:
            return [(r.distance_nm, r.course_deg) for r in runs]
        if self.kind != 'survey':
            return [(self.distance_nm, self.course_deg)]
        if self.lines:
            length = float(self.line_length_nm or 0.0)
            return [(length, self.course_deg + (180.0 if i % 2 else 0.0))
                    for i in range(self.lines)]
        # No line count: the historical meaning, one reciprocal pair.
        half = self.distance_nm / 2.0
        return [(half, self.course_deg), (half, self.course_deg + 180.0)]

    def validate(self) -> list[str]:
        errs = []
        if self.distance_nm < 0:
            errs.append(f'{self.name}: distance must not be negative')
        # `not (x > 0)` rather than `x <= 0`: both comparisons are False for
        # NaN, so the latter waves NaN through and it poisons every figure
        # downstream, dying only at response serialization with a message that
        # names no input (found in review). The server also rejects NaN at the
        # JSON layer; this is the engine's own guard for direct callers.
        if not self.speed_kt > 0:
            errs.append(f'{self.name}: speed must be greater than zero')
        if self.loiter_hours < 0:
            errs.append(f'{self.name}: loiter must not be negative')
        if self.wind_speed_kt is not None and self.wind_speed_kt < 0:
            errs.append(f'{self.name}: wind speed must not be negative')
        if self.current_speed_kt is not None and self.current_speed_kt < 0:
            errs.append(f'{self.name}: current speed must not be negative')
        if self.wmo_sea_state is not None and self.wmo_sea_state < 0:
            errs.append(f'{self.name}: sea state must not be negative')
        if self.kind not in ('transit', 'survey'):
            errs.append(f'{self.name}: kind must be "transit" or "survey"')
        if self.lines is not None:
            if self.kind != 'survey':
                errs.append(f'{self.name}: only a survey leg can have lines')
            if int(self.lines) != self.lines or self.lines < 1:
                errs.append(f'{self.name}: lines must be a whole number, 1 or more')
            if self.line_length_nm is None or self.line_length_nm <= 0:
                errs.append(f'{self.name}: give a line length with the line count')
        elif self.line_length_nm is not None:
            errs.append(f'{self.name}: give a line count with the line length')
        return errs


@dataclass
class Environment:
    wmo_sea_state: int = 2
    wind_speed_kt: float = 0.0
    wind_from_deg: float = 0.0          # direction the wind blows FROM
    # CURRENT USES THE OPPOSITE CONVENTION TO WIND, as at sea: a wind is named
    # for where it comes FROM, a current for where it SETS TOWARD. Getting
    # these the same way round is the classic way to plan a mission backwards,
    # so the field name says which it is and the UI labels it.
    current_speed_kt: float = 0.0       # drift
    current_set_deg: float = 0.0        # direction the current flows TOWARD

    def validate(self) -> list[str]:
        errs = []
        if self.wind_speed_kt < 0:
            errs.append('wind speed must not be negative')
        if self.current_speed_kt < 0:
            errs.append('current speed must not be negative')
        # Mirrors Leg.validate. Without this a wmo of -1 through the API sailed
        # past every check and silently planned the whole mission at the TOP
        # premium, because sea_state_premium's "above the table, hold the top
        # value" fallback cannot tell above from below (found in review).
        if self.wmo_sea_state < 0:
            errs.append('sea state must not be negative')
        return errs


@dataclass
class Vessel:
    capacity_l: float = 250.0
    reserve_fraction: float = 0.25
    start_level_fraction: float = 1.0
    # Which gondola the hull carries. 'em712' is the measured 2024 trial
    # configuration and stays the API default; the UI defaults to the model
    # file's 'default' key (currently em2040, the gondola now fitted).
    gondola: str = 'em712'

    def validate(self) -> list[str]:
        errs = []
        if self.capacity_l <= 0:
            errs.append('tank capacity must be greater than zero')
        if not 0 <= self.reserve_fraction < 1:
            errs.append('reserve must be between 0 and 1')
        if not 0 < self.start_level_fraction <= 1:
            errs.append('start level must be between 0 and 1')
        if self.reserve_fraction >= self.start_level_fraction:
            errs.append('start level must be above the reserve floor')
        return errs


# --------------------------------------------------------------------------- #
#  Model primitives
# --------------------------------------------------------------------------- #

class Model:
    """Thin typed wrapper over model.json."""

    def __init__(self, data: dict[str, Any] | None = None):
        self.data = data if data is not None else load_model()
        f = self.data['fuel_vs_rpm']
        self.f0, self.f1 = f['f0'], f['f1']
        self.rpm_min, self.rpm_max = f['valid_rpm_min'], f['valid_rpm_max']
        s = self.data['speed_vs_rpm_2024']
        self.sb, self.sm = s['b'], s['m']          # em712 shorthand (back-compat)
        # Per-gondola speed laws. Fall back to the legacy blocks so an old
        # model.json still loads.
        gon = self.data.get('gondolas', {})
        opts = gon.get('options') or {
            'em712': {'label': 'EM712', 'status': 'measured',
                      'speed_vs_rpm': self.data['speed_vs_rpm_2024']},
            'em2040': {'label': 'EM2040', 'status': 'derived',
                       'speed_vs_rpm': self.data['speed_vs_rpm_2022']},
        }
        self.gondolas = {}
        for k, v in opts.items():
            g = {'label': v.get('label', k),
                 'status': v.get('status', 'measured'),
                 'status_note': v.get('status_note', ''),
                 'b': v['speed_vs_rpm']['b'],
                 'm': v['speed_vs_rpm']['m'],
                 'fuel': None,                       # None -> shared linear law
                 'rpm_min': self.rpm_min, 'rpm_max': self.rpm_max}
            fv = v.get('fuel_vs_rpm')
            if fv:                                   # per-gondola quadratic law
                g['fuel'] = (fv['q0'], fv['q1'], fv.get('q2', 0.0))
                g['rpm_min'] = fv.get('valid_rpm_min', self.rpm_min)
                g['rpm_max'] = fv.get('valid_rpm_max', self.rpm_max)
            self.gondolas[k] = g
        self.default_gondola = gon.get('default', 'em712')
        h = self.data['heading_effect']
        self.head_amp = h['amplitude_at_reference']
        self.head_ref_wind = h['reference_wind_kt']
        self.head_exp = h['wind_exponent']
        self.sea_table = {row['wmo']: row for row in
                          self.data['sea_state_premium']['table']}
        # Gauge scale — litres of real fuel per indicated point, measured
        # against the flow meter. This is what lets an indicated percentage be
        # converted to litres WITHOUT assuming the tank is linear at
        # capacity/100. Absent from pre-2.4.0 model files, hence the .get().
        gc = self.data.get('gauge_calibration') or {}
        self.gauge_l_per_point = gc.get('l_per_point')
        self.gauge_l_per_point_sigma = gc.get('l_per_point_sigma')
        # PHYSICAL tank volume from the engineering drawings — a different
        # quantity from the gauge span and from mission fuel. Absent in
        # pre-2.5.0 model files.
        self.tank_volume_l = (self.data.get('tank_volume') or {}).get('litres')
        self.gauge_reading = (self.data.get('gauge_profile') or {}).get('reading', 'B')
        self.gauge_profile = self._build_gauge_profile(gc)

    def _build_gauge_profile(self, gc: dict[str, Any]) -> GaugeProfile | None:
        """Derive the profile from the facts, never from a stored copy of it.

        Reading 'A': the drawings volume is spread over the points outside the
        measured band, uniformly — neutral, since only the integral and the one
        band are pinned by evidence. Reading 'B': one segment at the measured
        rate, i.e. the gauge speaks only for its own span.
        """
        lpp = self.gauge_l_per_point
        if not lpp:
            return None
        band = gc.get('band_pct')
        if self.gauge_reading == 'A' and self.tank_volume_l and band:
            lo, hi = float(band[0]), float(band[1])
            rest_pts = 100.0 - (hi - lo)
            if rest_pts <= 0:
                return GaugeProfile([(0.0, 100.0, lpp)])
            rest = (self.tank_volume_l - (hi - lo) * lpp) / rest_pts
            if rest <= 0:
                raise ValueError(
                    f'reading A is impossible: the measured band alone holds more '
                    f'than the {self.tank_volume_l} L drawing volume')
            return GaugeProfile([(0.0, lo, rest), (lo, hi, lpp), (hi, 100.0, rest)])
        return GaugeProfile([(0.0, 100.0, lpp)])

    # -- speed <-> rpm ----------------------------------------------------- #

    def _law(self, gondola: str) -> dict[str, Any]:
        try:
            return self.gondolas[gondola]
        except KeyError:
            raise ValueError(
                f'unknown gondola "{gondola}" — choose from '
                f'{", ".join(sorted(self.gondolas))}') from None

    def rpm_for_speed(self, kt: float, gondola: str = 'em712') -> float:
        """RPM this gondola configuration needs for a given SOG in benign water."""
        g = self._law(gondola)
        return (kt - g['b']) / g['m']

    def speed_for_rpm(self, rpm: float, gondola: str = 'em712') -> float:
        g = self._law(gondola)
        return g['b'] + g['m'] * rpm

    # -- fuel -------------------------------------------------------------- #

    def fuel_rate_lph(self, rpm: float, gondola: str = 'em712') -> float:
        """Fuel rate at an engine speed, using the gondola's own fuel law when
        it has one (EM2040: quadratic, measured from MCAP logs) and the shared
        linear law otherwise. Clamped at zero — neither form may be
        extrapolated down to idle."""
        g = self._law(gondola)
        if g['fuel'] is not None:
            q0, q1, q2 = g['fuel']
            return max(0.0, q0 + q1 * rpm + q2 * rpm * rpm)
        return max(0.0, self.f0 + self.f1 * rpm)

    def loiter_rate_lph(self, gondola: str = 'em712') -> tuple[float, bool, float]:
        """Idle burn while holding station: (litres/hour, measured?, rpm).

        The EM2040's figure is MEASURED — 0.95 L/h at ~1005 rpm over 20.8 h of
        observed idle. Where a gondola has no such block the rate is read off
        that gondola's OWN fuel law at idle rpm rather than borrowed from a
        gondola that was measured: a different gondola is a different drag
        state, and the whole point of carrying two is not to mix them. The
        caller is told which it got, because the derived one is an
        extrapolation below the fuel law's fitted floor.

        Idle rpm is a DRIVETRAIN fact, not a gondola one — the engine idles at
        about the same speed whatever is slung underneath — so it carries over
        when a gondola supplies no figure of its own.
        """
        g = self.data.get('gondolas', {}).get('options', {}).get(gondola, {})
        block = g.get('loiter') or {}
        rpm = float(block.get('rpm') or IDLE_RPM)
        lph = block.get('lph')
        if lph is not None:
            return float(lph), True, rpm
        return max(0.0, self.fuel_rate_lph(rpm, gondola)), False, rpm

    def in_fit_window(self, rpm: float, gondola: str = 'em712') -> bool:
        g = self._law(gondola)
        return g['rpm_min'] <= rpm <= g['rpm_max']

    # -- environment ------------------------------------------------------- #

    def sea_state_premium(self, wmo: int, override: dict[int, float] | None = None
                          ) -> float:
        if override and wmo in override:
            return float(override[wmo])
        row = self.sea_table.get(wmo)
        if row is None:                     # above the table: hold the top value
            top = max(self.sea_table)
            return float(self.sea_table[top]['premium'])
        return float(row['premium'])

    def heading_premium(self, course_deg: float, wind_from_deg: float,
                        wind_speed_kt: float) -> float:
        """Cosine modulation on required RPM.

        theta is the angle between the leg course and the direction the wind
        comes FROM, so theta = 0 is a dead headwind (full penalty) and 180 is
        a following wind (full credit).
        """
        if wind_speed_kt <= 0 or self.head_ref_wind <= 0:
            return 0.0
        theta = math.radians(course_deg - wind_from_deg)
        scale = (wind_speed_kt / self.head_ref_wind) ** self.head_exp
        return self.head_amp * scale * math.cos(theta)


# --------------------------------------------------------------------------- #
#  Results
# --------------------------------------------------------------------------- #

@dataclass
class LegResult:
    name: str
    kind: str
    distance_nm: float
    speed_kt: float
    course_deg: float
    hours: float
    rpm_benign: float
    rpm_required: float
    sea_premium: float
    heading_premium: float
    total_premium: float
    fuel_rate_lph: float
    litres: float
    nm_per_l: float
    extrapolated: bool
    notes: list[str] = field(default_factory=list)
    # -- Survey geometry, and the spread it produces. rpm_required,
    # heading_premium and total_premium above are distance-weighted MEANS;
    # these are the extremes actually flown, which is what the fuel law sees.
    lines: int | None = None
    line_length_nm: float | None = None
    # Through-water speed actually required, distance-weighted. Equals speed_kt
    # when there is no current; the spread across a reciprocal pair is what a
    # current costs that a following one does not give back.
    stw_kt: float = 0.0
    stw_min: float = 0.0
    stw_max: float = 0.0
    rpm_min: float = 0.0
    rpm_max: float = 0.0
    premium_min: float = 0.0
    premium_max: float = 0.0
    # -- The weather this leg was actually computed under, after the leg's own
    # values were laid over the mission's. Reported so a surface never has to
    # re-derive the fallback, and so a plan can be read back without the Legs
    # that produced it.
    wind_speed_kt: float = 0.0
    wind_from_deg: float = 0.0
    current_speed_kt: float = 0.0
    current_set_deg: float = 0.0
    wmo_sea_state: int = 0
    # -- Loiter: time held on station at the START of this leg, making no way.
    # `hours`, `litres`, `fuel_rate_lph` and `nm_per_l` above all remain
    # UNDERWAY quantities, so `litres == fuel_rate_lph * hours` still holds and
    # nm_per_l still describes how far this gondola goes on a litre. The leg's
    # real cost and duration are total_litres and (end_hours - start_hours).
    loiter_hours: float = 0.0
    loiter_litres: float = 0.0
    loiter_lph: float = 0.0
    loiter_measured: bool = True
    total_litres: float = 0.0
    # Position on the mission timeline. Elapsed hours are always present;
    # the clock strings only when a start time was supplied. end_hours includes
    # the loiter, because the clock is what the mission actually takes.
    start_hours: float = 0.0
    end_hours: float = 0.0
    start_clock: str | None = None
    end_clock: str | None = None
    start_iso: str | None = None
    end_iso: str | None = None


@dataclass
class PlanResult:
    legs: list[LegResult]
    total_hours: float
    total_distance_nm: float
    total_litres: float
    capacity_l: float
    start_litres: float
    reserve_litres: float
    usable_litres: float
    remaining_litres: float
    remaining_fraction: float
    margin_litres: float
    margin_nm: float
    margin_hours: float
    within_reserve: bool
    runs_dry: bool
    gondola: str
    gondola_status: str
    extrapolated_legs: list[str]
    warnings: list[str]
    sensitivity: list[dict[str, Any]]
    capacity_scenarios: list[dict[str, Any]]
    # -- Gauge-denominated reading (None when the model file has no gauge
    # calibration). The reserve floor is a NEEDLE POSITION, so the fuel a
    # mission may spend before reaching it is fixed by the gauge scale alone
    # and no tank-capacity assumption enters it. Reported alongside the
    # capacity-based figures, never instead of them.
    # -- Loiter, summed over the legs. `total_hours` and `total_litres` above
    # already INCLUDE these; they are broken out so a surface can say how much
    # of a mission was spent holding rather than making way.
    total_loiter_hours: float = 0.0
    total_loiter_litres: float = 0.0
    # -- The MISSION-WIDE FALLBACK current, i.e. Environment's values — NOT
    # necessarily what any leg was flown in, now that weather is per-leg and
    # the shipped UI sends an empty environment (these then read 0/slack while
    # the legs fight a real current). The truth per leg is legs[].current_*;
    # the UI's Current tile summarises those, never these. Kept because they
    # are published API; do not point a new surface at them (found in review —
    # the original comment here promised the opposite).
    current_speed_kt: float = 0.0
    current_set_deg: float = 0.0
    gauge_l_per_point: float | None = None
    gauge_l_per_point_sigma: float | None = None
    indicated_start_pct: float = 100.0
    indicated_return_pct: float | None = None
    gauge_usable_litres: float | None = None
    gauge_margin_litres: float | None = None
    gauge_within_reserve: bool | None = None
    # 100 points x L/point — the capacity the gauge itself implies, and how
    # many sigma the assumed capacity_l sits from it.
    gauge_capacity_l: float | None = None
    gauge_capacity_delta_l: float | None = None
    gauge_capacity_sigma: float | None = None
    # -- The constraint that actually binds. When the capacity basis and the
    # needle disagree the needle wins, because it is what the operator will
    # see. `verdict` is the single answer every surface renders, so the UI and
    # the API cannot drift apart: 'ok' | 'gauge_breach' | 'breach' | 'dry'.
    verdict: str = 'ok'
    binding_margin_litres: float = 0.0
    binding_margin_nm: float = 0.0
    binding_margin_hours: float = 0.0
    # Physical tank volume (drawings) and the litres it holds that the gauge's
    # indicated range does not account for. That remainder is real fuel, but it
    # cannot be planned on while the reserve floor is a needle position — it is
    # either non-linearity in unmeasured bands or volume outside the sender's
    # travel, and top-third data cannot say which.
    tank_volume_l: float | None = None
    gauge_unlocated_l: float | None = None
    # -- Mission clock. `marks` are the timed callouts, chronological: the
    # distance-from-home crossings (see WAYPOINTS_KM for what that means here)
    # and the mission-phase changes. Each carries a 'kind' of 'range'|'phase'.
    start_iso: str | None = None
    start_clock: str | None = None
    finish_iso: str | None = None
    finish_clock: str | None = None
    marks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d['legs'] = [asdict(l) if not isinstance(l, dict) else l for l in self.legs]
        return d


# --------------------------------------------------------------------------- #
#  The planner
# --------------------------------------------------------------------------- #

def plan_leg(leg: Leg, env: Environment, model: Model,
             sea_override: dict[int, float] | None = None,
             premium_delta: float = 0.0, gondola: str = 'em712',
             env_at=None, start_hours: float = 0.0,
             max_run_nm: float = 1.0) -> LegResult:
    """Fuel for one leg. `premium_delta` shifts the total premium, for
    sensitivity runs. `gondola` selects which speed-vs-RPM law converts the
    required SOG into engine RPM; the fuel-vs-RPM law is shared.

    Fuel is summed LINE BY LINE, never from an averaged premium. The fuel law
    is convex in RPM, so f(mean premium) < mean of f(premium) — the line into
    the weather costs more than the reciprocal saves. See Leg for the size of
    that error, which is what this leg used to make.
    """
    # This leg's own weather, falling back to the mission's. Resolved ONCE,
    # here, so every reference below — premiums, current, notes — sees the same
    # thing and no path can accidentally read the mission-wide value.
    env = leg.environment(env)
    distance = leg.resolved_distance_nm()
    # Duration follows SOG: the ground still has to be covered. A current moves
    # the FUEL, never the clock.
    hours = distance / leg.speed_kt if leg.speed_kt > 0 else 0.0
    sea_p = model.sea_state_premium(env.wmo_sea_state, sea_override)
    # rpm_benign and stw_kt are resolved per line below and averaged, because a
    # current is directional: a reciprocal pair does not see the same water.
    rpm_benign = stw_kt = 0.0

    # Each run carries its own environment when a field was supplied AND this
    # leg has geometry to locate it with. `env_at(lat, lon, hours)` is INJECTED
    # rather than fetched, so the engine keeps its one useful property: pure
    # computation, no I/O, testable offline against a field made of arithmetic.
    geo = leg.geometry_runs(max_run_nm) if env_at is not None else None
    if geo is not None:
        runs = [(r.distance_nm, r.course_deg, r) for r in geo]
    else:
        runs = [(nm, course, None) for nm, course in leg.line_plan()]

    litres = 0.0
    w_head = w_rpm = w_prem = w_stw = w_benign = 0.0   # distance-weighted means
    rpms: list[float] = []
    prems: list[float] = []
    stws: list[float] = []
    run_hours = start_hours + leg.loiter_hours       # the hold comes first
    for nm, course, run in runs:
        # The current is resolved PER RUN, before any premium: it changes the
        # through-water speed the hull must make to hold this SOG, which is a
        # different thing from the empirical premium and enters the chain one
        # step earlier. Over a reciprocal pair it helps one way and hurts the
        # other, and the two do not cancel, for the same reason the wind
        # premium does not: fuel is convex in RPM.
        #
        # With geometry the run also knows WHERE and WHEN it is, so the field
        # is sampled at the midpoint of the piece rather than one value being
        # stretched across the whole leg. The same convexity argument that
        # makes a line-by-line sum necessary across HEADINGS makes this
        # necessary across TIME.
        env_i = env
        if run is not None:
            mid = geometry.interpolate(run.start, run.end, 0.5)
            half = (nm / leg.speed_kt / 2.0) if leg.speed_kt > 0 else 0.0
            env_i = env_at(mid.lat, mid.lon, run_hours + half) or env
            run_hours += (nm / leg.speed_kt) if leg.speed_kt > 0 else 0.0
        sea_i = (sea_p if env_i is env
                 else model.sea_state_premium(env_i.wmo_sea_state, sea_override))
        stw_i = required_stw_kt(leg.speed_kt, course,
                                env_i.current_speed_kt, env_i.current_set_deg)
        benign_i = max(0.0, model.rpm_for_speed(stw_i, gondola))
        head_i = model.heading_premium(course, env_i.wind_from_deg, env_i.wind_speed_kt)
        prem_i = sea_i + head_i + premium_delta
        rpm_i = benign_i * (1.0 + prem_i)
        rate_i = model.fuel_rate_lph(rpm_i, gondola)
        litres += rate_i * (nm / leg.speed_kt if leg.speed_kt > 0 else 0.0)
        w_head += head_i * nm
        w_rpm += rpm_i * nm
        w_prem += prem_i * nm
        w_stw += stw_i * nm
        w_benign += benign_i * nm
        rpms.append(rpm_i)
        prems.append(prem_i)
        stws.append(stw_i)

    if distance > 0:
        head_p, rpm_req, total_p = w_head / distance, w_rpm / distance, w_prem / distance
        stw_kt = w_stw / distance
        rpm_benign = w_benign / distance
    else:
        head_p = model.heading_premium(leg.course_deg, env.wind_from_deg,
                                       env.wind_speed_kt)
        total_p = sea_p + head_p + premium_delta
        stw_kt = required_stw_kt(leg.speed_kt, leg.course_deg,
                                 env.current_speed_kt, env.current_set_deg)
        rpm_benign = max(0.0, model.rpm_for_speed(stw_kt, gondola))
        rpm_req = rpm_benign * (1.0 + total_p)
    # The effective rate, so `litres = rate x hours` still holds for readers.
    rate = litres / hours if hours > 0 else model.fuel_rate_lph(rpm_req, gondola)

    notes: list[str] = []
    g = model._law(gondola)
    # Flagged if ANY line falls outside the window, not the mean: a big heading
    # premium can put the into-wind line outside it while the mean sits inside.
    out = [r for r in rpms if not model.in_fit_window(r, gondola)]
    extrapolated = bool(out)
    if extrapolated:
        worst = max(out, key=lambda r: abs(r - rpm_req))
        where = 'above' if worst > g['rpm_max'] else 'below'
        scope = ('every line' if len(out) == len(rpms) else
                 f'{len(out)} of {len(rpms)} lines')
        notes.append(
            f'{worst:.0f} rpm is {where} the {g["rpm_min"]:.0f}-{g["rpm_max"]:.0f} rpm '
            f'window this gondola fuel law was fitted over ({scope}) — this leg is '
            f'an extrapolation.')
    if leg.kind == 'survey' and env.wind_speed_kt > 0 and len(runs) > 1:
        spread = max(prems) - min(prems)
        flat = model.fuel_rate_lph(rpm_benign * (1.0 + sea_p + premium_delta), gondola)
        # `flat` can clamp to 0.0 — the EM712's linear law goes non-positive
        # below ~712 rpm, which a 2 kt survey reaches — and a slow leg's own
        # `rate` can clamp too. Either way the convexity comparison is
        # meaningless there, and dividing by it crashed plan() outright
        # (found in review, reproduced: em712, 10 kt wind, 2 kt survey → 500).
        # The clamp note two lines down is the honest message for that regime.
        if flat > 0 and rate > 0:
            notes.append(
                f'{len(runs)} lines, alternating reciprocals: the RPM premium swings '
                f'{spread:.1%} across them. Summing line by line costs '
                f'{rate / flat - 1:+.1%} against a cancelled premium, because fuel is '
                f'convex in RPM.')
    if rate <= 0:
        notes.append('Fuel model clamped at zero; the leg is below its valid range.')

    if env.current_speed_kt > 0 and stws:
        lo, hi = min(stws), max(stws)
        span = (f'{lo:.2f}–{hi:.2f}' if hi - lo > 0.005 else f'{lo:.2f}')
        notes.append(
            f'{env.current_speed_kt:.1f} kt current setting {env.current_set_deg:.0f}°: '
            f'holding {leg.speed_kt:.1f} kt over the ground needs {span} kt through '
            f'the water. This is KINEMATIC, not fitted — but the speed law was fitted '
            f'on SOG in an unrecorded tide and the heading premium already mixes wind, '
            f'sea and current, so a large current is partly counted twice.')
        if lo <= 0.05:
            notes.append(
                f'The current nearly matches this leg\'s ground speed, so the required '
                f'through-water speed falls to {lo:.2f} kt — the hull is being carried. '
                f'The fuel law says nothing at that speed; treat the figure as a floor.')

    # -- Loiter, held at the START of the leg -------------------------------- #
    # It was originally taken at the end, and the return leg is where that
    # collapsed: a hold "at the end" of transit home is a hold AFTER arriving,
    # so 4 h of delay moved the finish clock and not one mark — reported as
    # "I do not get a 4 hours later arrival", and reproduced exactly. At the
    # start, every leg reads right: a launch delay outbound, a hold before the
    # first line on survey, an offshore hold before running in on the way home.
    # One rule falls out: a hold delays the leg's own crossings and everything
    # after it, and the arrival-home mark always equals total_hours.
    loiter_h = max(0.0, float(leg.loiter_hours or 0.0))
    loiter_lph, loiter_measured, loiter_rpm = model.loiter_rate_lph(gondola)
    loiter_l = loiter_lph * loiter_h
    if loiter_h > 0:
        if loiter_measured:
            notes.append(
                f'Holding {_hm(loiter_h)} at the start of this leg: '
                f'{loiter_l:.1f} L at the measured idle burn of {loiter_lph:.2f} L/h. '
                f'That figure is calm-water and carries NO sea-state premium — '
                f'station-keeping in a seaway has never been measured.')
        else:
            notes.append(
                f'Holding {_hm(loiter_h)} at the start of this leg: '
                f'{loiter_l:.1f} L at {loiter_lph:.2f} L/h, which is this '
                f'gondola\'s fuel law read at {loiter_rpm:.0f} rpm — it has no '
                f'measured idle burn, and that rpm is below the window the law '
                f'was fitted over, so the figure is an EXTRAPOLATION.')

    return LegResult(
        name=leg.name, kind=leg.kind, distance_nm=distance,
        speed_kt=leg.speed_kt, course_deg=leg.course_deg, hours=hours,
        rpm_benign=rpm_benign, rpm_required=rpm_req, sea_premium=sea_p,
        heading_premium=head_p, total_premium=total_p, fuel_rate_lph=rate,
        litres=litres, nm_per_l=(distance / litres) if litres > 0 else 0.0,
        extrapolated=extrapolated, notes=notes,
        wind_speed_kt=env.wind_speed_kt, wind_from_deg=env.wind_from_deg,
        current_speed_kt=env.current_speed_kt, current_set_deg=env.current_set_deg,
        wmo_sea_state=env.wmo_sea_state,
        loiter_hours=loiter_h, loiter_litres=loiter_l, loiter_lph=loiter_lph,
        loiter_measured=loiter_measured, total_litres=litres + loiter_l,
        lines=leg.lines, line_length_nm=leg.line_length_nm,
        stw_kt=stw_kt,
        stw_min=min(stws) if stws else stw_kt,
        stw_max=max(stws) if stws else stw_kt,
        rpm_min=min(rpms) if rpms else rpm_req,
        rpm_max=max(rpms) if rpms else rpm_req,
        premium_min=min(prems) if prems else total_p,
        premium_max=max(prems) if prems else total_p)


def required_stw_kt(sog_kt: float, course_deg: float,
                    current_speed_kt: float, current_set_deg: float) -> float:
    """Speed through the water needed to hold `sog_kt` over the ground.

    Pure kinematics, not a fitted premium: the water velocity a hull must make
    is the ground velocity minus the current, so

        STW = |Vg - Vc| = sqrt(Vg^2 + Vc^2 - 2*Vg*Vc*cos(set - course))

    A current dead on the nose adds its drift to the required through-water
    speed; dead astern it subtracts; on the beam the hull must crab, which
    costs a little either way. Fuel follows STW because that is what the hull
    pushes against, while the LEG'S DURATION follows SOG because that is what
    covers the ground — so a current changes the fuel bill without changing
    the clock.
    """
    if current_speed_kt <= 0 or sog_kt <= 0:
        return max(0.0, sog_kt)
    d = math.radians(current_set_deg - course_deg)
    stw_sq = (sog_kt * sog_kt + current_speed_kt * current_speed_kt
              - 2.0 * sog_kt * current_speed_kt * math.cos(d))
    return math.sqrt(max(0.0, stw_sq))


def _hm(hours: float) -> str:
    """A duration as an operator would say it: '20 min', '1 h', '2 h 30 min'."""
    total_min = int(round(hours * 60.0))
    h, m = divmod(total_min, 60)
    if h and m:
        return f'{h} h {m} min'
    return f'{h} h' if h else f'{m} min'


def _clock(start: dt.datetime | None, hours: float) -> tuple[str | None, str | None]:
    """(ISO, display) for a point `hours` into the mission.

    The display carries the DATE, and a `(+Nd)` suffix once it rolls over. These
    missions routinely run past midnight — endurance at survey speed is over two
    days — so a bare HH:MM two days out is a trap, not a convenience.
    """
    if start is None:
        return None, None
    t = start + dt.timedelta(hours=hours)
    day = (t.date() - start.date()).days
    return (t.isoformat(timespec='minutes'),
            t.strftime('%d %b %H:%M') + (f' (+{day}d)' if day else ''))


def _mission_marks(results: list[LegResult], cum_l: list[float],
                   waypoints: tuple[float, ...], start: dt.datetime | None,
                   prof: GaugeProfile | None, ind_start: float,
                   unit: str = DEFAULT_WAYPOINT_UNIT
                   ) -> tuple[list[dict[str, Any]], list[str]]:
    """The mission's timed callouts, in chronological order.

    Two kinds:

      'range'  — a fixed distance from home, one outbound and one inbound per
                 radius, measured ALONG THE PLANNED TRACK: run made good on the
                 first leg and distance still to run on the last. There is no
                 position model here, so those are the only two honest readings
                 of "distance from home".
      'phase'  — a change of mission phase: leaving home (the moment the first
                 leg starts MAKING WAY, after any launch hold), arrival on the
                 survey area, departure from it, and back alongside — which is
                 the end of the mission, so its time always equals total_hours.

    Every mark reports elapsed time, clock time, fuel burned by then and what
    the gauge will read.
    """
    marks: list[dict[str, Any]] = []
    notes: list[str] = []
    if not results:
        return marks, notes

    def add(idx: int, into_nm: float, kind: str, phase: str, label: str,
            extra: dict[str, Any] | None = None, after_hold: bool = True) -> None:
        r = results[idx]
        if r.speed_kt <= 0:
            return
        hrs = into_nm / r.speed_kt
        # The hold sits at the START of the leg, so a mark N miles into the
        # leg happens AFTER it — in time and in fuel. Leaving the hold out of
        # either line is exactly the reported bug: a 4 h hold on the way home
        # that moved no mark. `after_hold=False` is for the one event that
        # happens at mile 0 BEFORE the leg's own hold: arriving somewhere and
        # then holding there.
        hold_h = r.loiter_hours if after_hold else 0.0
        hold_l = r.loiter_litres if after_hold else 0.0
        elapsed = r.start_hours + hold_h + hrs
        burned = cum_l[idx] + hold_l + r.fuel_rate_lph * hrs
        iso, clock = _clock(start, elapsed)
        m: dict[str, Any] = {
            'kind': kind, 'phase': phase, 'label': label,
            'leg': r.name, 'elapsed_hours': elapsed,
            'iso': iso, 'clock': clock, 'litres_burned': burned,
        }
        if extra:
            m.update(extra)
        if prof is not None:
            m['indicated_pct'] = prof.level_after(ind_start, burned)
        marks.append(m)

    # -- home, both ways ----------------------------------------------------- #
    # Departure is the moment the first leg starts MAKING WAY — after any
    # launch hold, because "departing" names the vehicle leaving, not the
    # mission clock starting. Arrival is the end of the last leg, holds and
    # all, so its elapsed time always equals the plan's total_hours; a test
    # pins that identity because it is what makes the marks table and the
    # "Back alongside" readout agree.
    add(0, 0.0, 'phase', 'home_departure', 'Departing home — mission starts')

    # -- on and off the survey area ----------------------------------------- #
    # Arrival is the start of the FIRST survey leg, departure the end of the
    # LAST: the vehicle is on task from the moment it starts the first line
    # until it leaves for good, and any repositioning between patches sits
    # inside that. A plan with no survey leg simply has neither mark; that is a
    # legitimate mission shape, not a near-miss, so it produces no warning.
    # Arrival is stamped BEFORE the survey leg's own hold — the vehicle gets
    # there, then waits — which is the one place the hold offset must not
    # apply.
    survey = [i for i, r in enumerate(results) if r.kind == 'survey']
    if survey:
        add(survey[0], 0.0, 'phase', 'survey_arrival',
            'Arriving survey area — survey starts', after_hold=False)
        i = survey[-1]
        add(i, results[i].distance_nm, 'phase', 'survey_departure',
            'Survey complete — departing survey area')

    last = len(results) - 1
    add(last, results[last].distance_nm, 'phase', 'home_arrival',
        'Back alongside — mission complete')

    # -- fixed distances from home, along the planned track ----------------- #
    # Deduplicated so a repeated radius cannot produce a doubled mark; the
    # chronological sort at the end puts each pair where it belongs.
    #
    # `waypoints` are in `unit`; everything downstream is NM. Each mark carries
    # BOTH km and NM regardless of the unit chosen, because the unit is a
    # display choice and a consumer should not have to convert to compare two
    # plans made in different units.
    nm_per_unit = _nm_per_unit(unit)
    label_unit = 'km' if unit.strip().lower() == 'km' else 'NM'
    for value in sorted({float(w) for w in waypoints}):
        mark_nm = value * nm_per_unit
        if mark_nm <= 0:
            continue
        extra = {'km_from_home': mark_nm * KM_PER_NM, 'nm_from_home': mark_nm,
                 'from_home': value, 'unit': label_unit}
        first = results[0]
        if first.distance_nm >= mark_nm:
            add(0, mark_nm, 'range', 'outbound',
                f'{value:g} {label_unit} from home (outbound)', extra)
        else:
            notes.append(
                f'No outbound {value:g} {label_unit} waypoint: the first leg is '
                f'{first.distance_nm:.1f} NM, shorter than the {mark_nm:.2f} NM '
                f'the waypoint sits at.')

        last = len(results) - 1
        if results[last].distance_nm >= mark_nm:
            add(last, results[last].distance_nm - mark_nm, 'range', 'inbound',
                f'{value:g} {label_unit} from home (inbound)', extra)
        else:
            notes.append(
                f'No inbound {value:g} {label_unit} waypoint: the return leg is '
                f'only {results[last].distance_nm:.1f} NM, so the vehicle is '
                f'already inside {value:g} {label_unit} of home when it turns '
                f'for home.')

    # Chronological, so the table reads as the mission runs. Sorting here means
    # a new mark can be added anywhere above without minding the order.
    marks.sort(key=lambda m: m['elapsed_hours'])
    return marks, notes


def _total_litres(legs: list[Leg], env: Environment, model: Model,
                  sea_override: dict[int, float] | None, premium_delta: float,
                  gondola: str = 'em712') -> float:
    # total_litres, not litres: the sensitivity rows have to carry the loiter
    # too, or shifting the premium would appear to change a mission's whole
    # burn while a two-hour hold quietly sat outside every row.
    return sum(plan_leg(l, env, model, sea_override, premium_delta,
                        gondola).total_litres
               for l in legs)


def plan(legs: list[Leg], env: Environment, vessel: Vessel,
         model: Model | None = None,
         sea_override: dict[int, float] | None = None,
         sensitivity_deltas: tuple[float, ...] = (-0.05, 0.0, 0.05, 0.10, 0.20),
         capacity_scenarios: list[dict[str, Any]] | None = None,
         start_time: dt.datetime | None = None,
         waypoints: float | tuple[float, ...] | None = None,
         waypoint_unit: str = DEFAULT_WAYPOINT_UNIT,
         home_marks_km: float | tuple[float, ...] | None = None,
         env_at=None, max_run_nm: float = 1.0) -> PlanResult:
    """Run a full mission plan.

    Raises ValueError with all input problems collected, rather than failing on
    the first one.

    `waypoints` are distances from home in `waypoint_unit` ('km' or 'nm').
    Omitting them uses `default_waypoints(waypoint_unit)` — the same physical
    radii whichever unit is asked for, because a unit is a display choice and
    must not move a mark along the track.

    `home_marks_km` is the deprecated spelling, always in km. It is honoured
    when `waypoints` is not given, so the published API keeps working.
    """
    model = model or Model()

    errs = list(env.validate()) + list(vessel.validate())
    for leg in legs:
        errs.extend(leg.validate())
    if not legs:
        errs.append('a plan needs at least one leg')
    if vessel.gondola not in model.gondolas:
        errs.append(f'unknown gondola "{vessel.gondola}" — choose from '
                    f'{", ".join(sorted(model.gondolas))}')
    if start_time is not None and not isinstance(start_time, dt.datetime):
        errs.append('start_time must be a datetime, or None for no mission clock')
    # The deprecated km-only spelling still works, but only when the current
    # one is absent — passing both is a contradiction, not something to resolve
    # by precedence, so it is an error.
    unit = waypoint_unit
    if home_marks_km is not None:
        if waypoints is not None:
            errs.append('pass waypoints or home_marks_km, not both')
        else:
            waypoints, unit = home_marks_km, 'km'
    try:
        _nm_per_unit(unit)
    except ValueError as exc:
        errs.append(str(exc))
        unit = DEFAULT_WAYPOINT_UNIT
    if waypoints is None:
        waypoints = default_waypoints(unit)
    # A bare number is accepted as a single radius, so callers with one waypoint
    # need not wrap it in a tuple.
    if isinstance(waypoints, (int, float)):
        marks: tuple[float, ...] = (float(waypoints),)
    else:
        try:
            marks = tuple(float(w) for w in waypoints)
        except (TypeError, ValueError):
            marks = ()
            errs.append('waypoints must be a number or a sequence of numbers')
    if any(w < 0 for w in marks):
        errs.append('mission waypoints must not be negative')
    if errs:
        raise ValueError('; '.join(errs))

    gondola = vessel.gondola
    gond = model.gondolas[gondola]
    # Elapsed hours are accumulated as we go, so a leg's geometry can be
    # located in TIME as well as in space: leg three of a two-day mission does
    # not see the tide leg one saw. Without `env_at` this is exactly the old
    # comprehension — every leg planned against the mission environment.
    results, elapsed = [], 0.0
    for l in legs:
        r = plan_leg(l, env, model, sea_override, gondola=gondola,
                     env_at=env_at, start_hours=elapsed, max_run_nm=max_run_nm)
        results.append(r)
        elapsed += r.hours

    # Lay the legs out on a mission timeline. Elapsed hours always; clock times
    # only when a start was given. cum_l[i] is the fuel burned BEFORE leg i,
    # which is what the range marks interpolate from.
    # Loiter is held at the START of a leg: the leg spans [start_hours,
    # end_hours] with the hold occupying the first loiter_hours of it, and the
    # distance running after. _mission_marks adds the hold before any mark on
    # the leg — that offset is the fix for the reported "4 h hold on the way
    # home moved nothing" bug.
    cum_l: list[float] = []
    elapsed = burned = 0.0
    for r in results:
        cum_l.append(burned)
        r.start_hours = elapsed
        r.end_hours = elapsed + r.hours + r.loiter_hours
        r.start_iso, r.start_clock = _clock(start_time, r.start_hours)
        r.end_iso, r.end_clock = _clock(start_time, r.end_hours)
        elapsed, burned = r.end_hours, burned + r.total_litres

    total_l = sum(r.total_litres for r in results)
    total_h = sum(r.hours + r.loiter_hours for r in results)
    total_d = sum(r.distance_nm for r in results)

    start_l = vessel.capacity_l * vessel.start_level_fraction
    reserve_l = vessel.capacity_l * vessel.reserve_fraction
    usable_l = start_l - reserve_l
    remaining_l = start_l - total_l
    margin_l = remaining_l - reserve_l

    # Margin expressed the two ways a supervisor actually thinks about it,
    # both at the return leg's burn rate — that being the one still to fly.
    ret = results[-1]
    margin_nm = margin_l * (ret.nm_per_l if ret.nm_per_l > 0 else 0.0)
    margin_h = margin_l / ret.fuel_rate_lph if ret.fuel_rate_lph > 0 else 0.0

    # -- What the needle will actually do ---------------------------------- #
    # The reserve floor is a GAUGE READING, not a number of litres, so the fuel
    # a mission may spend before reaching it follows from the measured gauge
    # scale alone — capacity_l never enters it. That is why believing a 250 L
    # tank does not buy back the endurance the measured scale takes away.
    lpp = model.gauge_l_per_point
    lpp_sigma = model.gauge_l_per_point_sigma
    ind_start = vessel.start_level_fraction * 100.0
    ind_return = g_usable = g_margin = g_ok = None
    g_cap = g_cap_delta = g_cap_sigma = unlocated = None
    prof = model.gauge_profile
    if lpp and prof is not None:
        # Integrals over the profile, so a burn crossing a band boundary is
        # split at it. Under a linear gauge these reduce to the old arithmetic.
        ind_floor = vessel.reserve_fraction * 100.0
        ind_return = prof.level_after(ind_start, total_l)
        g_usable = prof.litres_between(ind_floor, ind_start)
        g_margin = g_usable - total_l
        g_ok = g_margin >= 0
        g_cap = prof.litres_between(0.0, 100.0)
        g_cap_delta = vessel.capacity_l - g_cap
        if model.tank_volume_l:
            unlocated = model.tank_volume_l - g_cap
        if lpp_sigma:
            g_cap_sigma = abs(vessel.capacity_l / 100.0 - lpp) / lpp_sigma

    # Whichever floor the mission reaches first is the one that constrains it.
    # Spare range and time are quoted against this, not against the capacity
    # row alone — otherwise a red verdict would sit beside a generous spare.
    binding_l = margin_l if g_margin is None else min(margin_l, g_margin)
    binding_nm = binding_l * (ret.nm_per_l if ret.nm_per_l > 0 else 0.0)
    binding_h = binding_l / ret.fuel_rate_lph if ret.fuel_rate_lph > 0 else 0.0

    marks, mark_notes = _mission_marks(results, cum_l, marks,
                                       start_time, prof, ind_start, unit)
    start_iso, start_clock = _clock(start_time, 0.0)
    finish_iso, finish_clock = _clock(start_time, total_h)

    warnings: list[str] = list(mark_notes)
    extrap = [r.name for r in results if r.extrapolated]
    if extrap:
        warnings.append(
            'Extrapolated beyond the fitted fuel model on: ' + ', '.join(extrap)
            + f'. The {gond["label"]} fuel law was fitted over {gond["rpm_min"]:.0f}-'
              f'{gond["rpm_max"]:.0f} rpm and the flagged legs fall outside it — treat '
              f'their burn figures as indicative.')
    if gond['status'] == 'derived':
        warnings.append(
            f'The {gond["label"]} fuel curve is derived, not measured — the EM712 fuel-vs-RPM '
            f'law transferred through this gondola\'s speed curve, validated to about 2% '
            f'against the one in-service check available. Pending refit from ROS bag data.')
    runs_dry = remaining_l < 0
    if runs_dry:
        warnings.append(
            f'PLAN RUNS THE TANK DRY. It needs {total_l:.1f} L against {start_l:.1f} L '
            f'aboard — {abs(remaining_l):.1f} L more than the vehicle carries. This is not '
            f'a reserve problem; the mission does not close.')
    elif margin_l < 0:
        warnings.append(
            f'PLAN BREACHES THE RESERVE by {abs(margin_l):.1f} L. The vehicle would '
            f'return with {remaining_l / vessel.capacity_l:.1%} indicated, below the '
            f'{vessel.reserve_fraction:.0%} floor.')
    if g_margin is not None and g_margin < 0 <= margin_l and not runs_dry:
        detail = (f', which the gauge measurement rules out at {g_cap_sigma:.0f} sigma'
                  if g_cap_sigma else '')
        warnings.append(
            f'GAUGE DISAGREES WITH CAPACITY. The {vessel.reserve_fraction:.0%} floor is '
            f'a needle position: at the measured {lpp:.2f} L per indicated point this '
            f'mission may spend {g_usable:.1f} L before the gauge reaches it, and it '
            f'spends {total_l:.1f} L — {abs(g_margin):.1f} L over. The capacity row '
            f'passes only by assuming {vessel.capacity_l:.0f} L behaves as '
            f'{vessel.capacity_l / 100.0:.2f} L per indicated point{detail}.')
    if margin_l < 0.05 * vessel.capacity_l and margin_l >= 0 and not runs_dry:
        warnings.append(
            f'Thin margin: only {margin_l:.1f} L above the reserve floor '
            f'({margin_nm:.0f} NM at the return leg burn rate).')
    # Per LEG, not the mission Environment: since weather went per-leg the UI
    # sends environment {}, so a mission-level check was dead for the primary
    # client and blind to a leg that overrides the fallback (found in review).
    # Legs are grouped into one warning so three windy legs do not nag thrice.
    odd = [r.name for r in results
           if r.wind_speed_kt > 0 and r.wmo_sea_state <= 1]
    if odd:
        worst = max((r for r in results if r.name in odd),
                    key=lambda r: r.wind_speed_kt)
        warnings.append(
            f'{worst.wind_speed_kt:.0f} kt of wind with a WMO {worst.wmo_sea_state} '
            f'sea ({", ".join(odd)}) is an unusual pairing — check the sea state '
            f'is right for the fetch.')

    if runs_dry:
        verdict = 'dry'
    elif margin_l < 0:
        verdict = 'breach'
    elif g_ok is False:
        verdict = 'gauge_breach'
    else:
        verdict = 'ok'

    sens = []
    for d in sensitivity_deltas:
        lit = _total_litres(legs, env, model, sea_override, d, gondola)
        rem = start_l - lit
        sens.append({
            'premium_delta': d,
            'total_litres': lit,
            'remaining_litres': rem,
            'remaining_fraction': rem / vessel.capacity_l,
            'margin_litres': rem - reserve_l,
            'within_reserve': (rem - reserve_l) >= 0,
            'runs_dry': rem < 0,
            # Same row judged against the needle rather than the tank.
            'gauge_margin_litres': (g_usable - lit) if g_usable is not None else None,
            'gauge_within_reserve': ((g_usable - lit) >= 0) if g_usable is not None
                                    else None,
        })

    scen = []
    for opt in (capacity_scenarios or model.data['capacity_options']['options']):
        cap = float(opt['litres'])
        s_start = cap * vessel.start_level_fraction
        s_res = cap * vessel.reserve_fraction
        rem = s_start - total_l
        scen.append({
            'key': opt.get('key', ''), 'label': opt.get('label', ''),
            'capacity_l': cap,
            'remaining_litres': rem,
            'remaining_fraction': rem / cap,
            'margin_litres': rem - s_res,
            'within_reserve': (rem - s_res) >= 0,
            'runs_dry': rem < 0,
        })

    return PlanResult(
        legs=results, total_hours=total_h, total_distance_nm=total_d,
        total_loiter_hours=sum(r.loiter_hours for r in results),
        total_loiter_litres=sum(r.loiter_litres for r in results),
        current_speed_kt=env.current_speed_kt,
        current_set_deg=env.current_set_deg,
        total_litres=total_l, capacity_l=vessel.capacity_l, start_litres=start_l,
        reserve_litres=reserve_l, usable_litres=usable_l,
        remaining_litres=remaining_l,
        remaining_fraction=remaining_l / vessel.capacity_l,
        margin_litres=margin_l, margin_nm=margin_nm, margin_hours=margin_h,
        within_reserve=margin_l >= 0, runs_dry=runs_dry,
        gondola=gondola, gondola_status=gond['status'], extrapolated_legs=extrap,
        warnings=warnings, sensitivity=sens, capacity_scenarios=scen,
        gauge_l_per_point=lpp, gauge_l_per_point_sigma=lpp_sigma,
        indicated_start_pct=ind_start, indicated_return_pct=ind_return,
        gauge_usable_litres=g_usable, gauge_margin_litres=g_margin,
        gauge_within_reserve=g_ok, gauge_capacity_l=g_cap,
        gauge_capacity_delta_l=g_cap_delta, gauge_capacity_sigma=g_cap_sigma,
        verdict=verdict, binding_margin_litres=binding_l,
        binding_margin_nm=binding_nm, binding_margin_hours=binding_h,
        tank_volume_l=model.tank_volume_l, gauge_unlocated_l=unlocated,
        start_iso=start_iso, start_clock=start_clock,
        finish_iso=finish_iso, finish_clock=finish_clock, marks=marks)


def max_survey_length(legs: list[Leg], env: Environment, vessel: Vessel,
                      model: Model | None = None,
                      sea_override: dict[int, float] | None = None,
                      tol_nm: float = 0.01, max_nm: float = 5000.0) -> float:
    """Longest survey leg that still returns at or above the reserve floor.

    Bisection on the survey distance, holding everything else fixed. Returns 0.0
    if even a zero-length survey breaches the reserve.

    Solves against the BINDING floor — capacity or needle, whichever the mission
    reaches first. Anything else would hand back a distance the planner then
    flags red.

    When the survey is defined by lines, the LINE COUNT is held and the line
    length is scaled. Scaling the count instead would change the odd/even parity
    as it searched, so the objective would jump rather than vary smoothly — and
    with it the heading asymmetry the line model exists to capture.
    """
    model = model or Model()
    idx = [i for i, l in enumerate(legs) if l.kind == 'survey']
    if not idx:
        raise ValueError('no survey leg to solve for')

    def scaled(leg: Leg, nm: float) -> Leg:
        d = asdict(leg)
        d['distance_nm'] = nm
        if leg.lines:
            if nm <= 0:
                # The zero-length probe: a survey of no distance has no lines,
                # and a zero-length line is rejected as meaningless input.
                d['lines'] = d['line_length_nm'] = None
            else:
                d['line_length_nm'] = nm / leg.lines
        return Leg(**d)

    def margin_for(nm: float) -> float:
        trial = [scaled(l, nm) if i in idx else l for i, l in enumerate(legs)]
        return plan(trial, env, vessel, model, sea_override).binding_margin_litres

    if margin_for(0.0) < 0:
        return 0.0
    lo, hi = 0.0, max_nm
    if margin_for(hi) >= 0:
        return hi
    while hi - lo > tol_nm:
        mid = (lo + hi) / 2
        if margin_for(mid) >= 0:
            lo = mid
        else:
            hi = mid
    return lo


def max_survey_lines(legs: list[Leg], env: Environment, vessel: Vessel,
                     model: Model | None = None,
                     sea_override: dict[int, float] | None = None,
                     cap: int = 10_000) -> dict[str, Any]:
    """How many survey lines the fuel actually allows, at the planned geometry.

    The operationally useful question when a survey will not fit in one run:
    the line length and bearing are set by the area, so what gives is the
    number of lines you get through before you must turn for home.

    Lines are DISCRETE, so this searches integers rather than bisecting a
    distance. That is not fussiness — the marginal cost alternates sharply.
    At 25 kt an into-wind line costs about 3.5x the downwind line that follows
    it, so "fits" and "does not fit" can turn on which direction the next line
    happens to run.

    Total fuel is strictly increasing in the line count (every line adds a
    positive burn), so the margin decreases monotonically and the answer is
    well defined. Returns a dict rather than a bare int because the count is
    only half the answer — the caller needs the distance and the shortfall too.
    """
    model = model or Model()
    idx = [i for i, l in enumerate(legs) if l.kind == 'survey' and l.lines]
    if not idx:
        raise ValueError('no line-based survey leg to solve for — give the '
                         'survey a line count and length, or use '
                         'max_survey_length for a distance-based survey')

    length = legs[idx[0]].line_length_nm or 0.0
    requested = max(legs[i].lines or 0 for i in idx)

    def with_lines(n: int) -> list[Leg]:
        out = []
        for i, l in enumerate(legs):
            if i not in idx:
                out.append(l)
                continue
            d = asdict(l)
            if n <= 0:                      # no survey at all
                d['lines'] = d['line_length_nm'] = None
                d['distance_nm'] = 0.0
            else:
                d['lines'] = n
                d['distance_nm'] = n * (l.line_length_nm or 0.0)
            out.append(Leg(**d))
        return out

    def fits(n: int) -> bool:
        return plan(with_lines(n), env, vessel, model,
                    sea_override).binding_margin_litres >= 0

    if not fits(1):
        return {'lines': 0, 'requested_lines': requested, 'line_length_nm': length,
                'distance_nm': 0.0, 'shortfall_lines': requested,
                'completes': False,
                'note': 'Not even one line fits: the transits alone reach the floor.'}

    # Exponential probe then bisect, so a generous cap costs a few plans, not
    # thousands. lo always fits, hi never does.
    lo = 1
    hi = 2
    while hi <= cap and fits(hi):
        lo, hi = hi, hi * 2
    if hi > cap:
        # The probe has only ever PROVEN lo (a power of two) fits; hi doubling
        # past the cap says nothing about the counts between lo and cap. The
        # old exit returned lines=cap, completes=True here without checking —
        # review reproduced a mission whose true max was 20 being told 30 fit,
        # a plan 52 L past the floor. Ask the question before answering it.
        if fits(cap):
            return {'lines': cap, 'requested_lines': requested,
                    'line_length_nm': length, 'distance_nm': cap * length,
                    'shortfall_lines': 0, 'completes': True,
                    'note': f'At least {cap} lines fit; the search stops there.'}
        hi = cap                      # cap does not fit: bisect lo..cap honestly
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if fits(mid):
            lo = mid
        else:
            hi = mid

    short = max(0, requested - lo)
    spare = max(0, lo - requested)
    return {'lines': lo, 'requested_lines': requested, 'line_length_nm': length,
            'distance_nm': lo * length, 'shortfall_lines': short,
            'spare_lines': spare, 'completes': short == 0,
            'note': (f'{short} of the {requested} planned lines will not fit in '
                     f'this run.' if short else
                     f'The planned {requested} lines fit, with room for '
                     f'{spare} more.' if spare else
                     'The planned survey fits exactly.')}
