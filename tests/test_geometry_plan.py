"""Planning against a current FIELD instead of one number per leg.

The field here is arithmetic, not a forecast — a synthetic tide that reverses
on a known period — so every expectation is checkable by hand and nothing
touches the network or the operator's cache.

The claim being tested is the one that motivated geometry at all: a survey
held on one ground through a reversing tide costs MORE than the same survey
planned against the vector mean of that tide, because fuel is convex. The
mean of a reversing current is near zero, which reads as slack water; the
hull is not doing slack water.
"""

from __future__ import annotations

import dataclasses
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import geometry  # noqa: E402
from engine import Environment, Leg, Model, Vessel, plan, plan_leg  # noqa: E402

MODEL = Model()
LEWES = (38.7828, -75.1394)


def reversing_tide(base_env, amplitude_kt=2.0, period_h=12.42, set_deg=0.0):
    """A rectilinear tide: `amplitude` on `set_deg` at hour 0, reversing on the
    semidiurnal period. Returns an `env_at` of the shape plan() wants."""
    def env_at(lat, lon, hours):
        v = amplitude_kt * math.cos(2 * math.pi * hours / period_h)
        return dataclasses.replace(base_env,
                                   current_speed_kt=abs(v),
                                   current_set_deg=set_deg if v >= 0
                                   else (set_deg + 180.0) % 360.0)
    return env_at


def survey_leg(lines=12, length=2.0, speed=6.0, bearing=20.0, spacing=0.1,
               turn_radius=0.0):
    return Leg('Survey', 'survey', 0.0, speed, bearing,
               pattern={'anchor': LEWES, 'bearing_deg': bearing,
                        'length_nm': length, 'spacing_nm': spacing,
                        'lines': lines, 'turn_radius_nm': turn_radius})


class TestGeometryIsOptional(unittest.TestCase):
    """The whole change has to be invisible to a leg that carries no geometry.
    This is the guard against a refactor that quietly moves every existing
    plan's numbers."""

    def test_a_leg_without_geometry_plans_exactly_as_before(self):
        leg = Leg('Transit out', 'transit', 25.0, 7.0, 90.0,
                  current_speed_kt=1.2, current_set_deg=200.0)
        env = Environment(wmo_sea_state=2, wind_speed_kt=12, wind_from_deg=270)
        a = plan_leg(leg, env, MODEL, gondola='em2040')
        b = plan_leg(leg, env, MODEL, gondola='em2040', env_at=None)
        self.assertEqual(a.litres, b.litres)
        self.assertEqual(a.hours, b.hours)

    def test_an_env_field_is_ignored_by_a_leg_with_no_geometry(self):
        """No geometry means no position, so there is nothing to sample a
        field at. The leg's own current stands."""
        leg = Leg('Transit', 'transit', 25.0, 7.0, 90.0, current_speed_kt=0.0)
        env = Environment()
        plain = plan_leg(leg, env, MODEL, gondola='em2040')
        fielded = plan_leg(leg, env, MODEL, gondola='em2040',
                           env_at=reversing_tide(env, 3.0))
        self.assertAlmostEqual(plain.litres, fielded.litres, places=9)


class TestGeometryDrivesDistance(unittest.TestCase):

    def test_survey_distance_comes_from_the_pattern(self):
        leg = survey_leg(lines=10, length=3.0)
        self.assertAlmostEqual(leg.resolved_distance_nm(), 30.0, places=3)

    def test_turns_add_distance_that_was_previously_free(self):
        without = survey_leg(lines=10, length=3.0, turn_radius=0.0)
        with_turns = survey_leg(lines=10, length=3.0, turn_radius=0.0135)
        extra = with_turns.resolved_distance_nm() - without.resolved_distance_nm()
        # nine turns, each at least a half circle of the given radius
        self.assertGreater(extra, 9 * math.pi * 0.0135 * 0.999)

    def test_a_transit_track_sets_its_own_distance_and_course(self):
        a = geometry.Point(*LEWES)
        b = geometry.move(a, 90.0, 12.0)
        leg = Leg('Transit', 'transit', 999.0, 8.0, 0.0, track=[tuple(a), tuple(b)])
        self.assertAlmostEqual(leg.resolved_distance_nm(), 12.0, places=2)
        runs = leg.geometry_runs(max_run_nm=99)
        self.assertAlmostEqual(runs[0].course_deg, 90.0, places=2)


class TestTideConvexity(unittest.TestCase):
    """The reason geometry exists."""

    def setUp(self):
        self.env = Environment(wmo_sea_state=2)
        self.vessel = Vessel(gondola='em2040')
        self.leg = survey_leg(lines=24, length=2.0, speed=6.0)
        self.field = reversing_tide(self.env, amplitude_kt=2.0)

    def test_the_mean_of_a_reversing_tide_understates_what_the_hull_sees(self):
        """Sanity: the number the old per-leg fill would have produced.

        This 48 NM survey at 6 kt runs 8 h against a 12.42 h tide, so it is
        NOT a whole cycle and the mean does not cancel to zero — it lands near
        0.37 kt. That is the point rather than a caveat: the Current tile
        would report a third of a knot while the water runs to 2 kt and
        reverses under the vehicle. A survey spanning a full cycle cancels
        harder still, which is worse, not better.
        """
        hours = self.leg.resolved_distance_nm() / 6.0
        us, vs = [], []
        for i in range(49):
            e = self.field(0, 0, hours * i / 48)
            ang = math.radians(e.current_set_deg)
            us.append(e.current_speed_kt * math.sin(ang))
            vs.append(e.current_speed_kt * math.cos(ang))
        mean = math.hypot(sum(us) / len(us), sum(vs) / len(vs))
        self.assertLess(mean, 0.5, 'the mean should read far weaker than the tide')
        self.assertLess(mean, 2.0 / 4, 'under a quarter of the amplitude')

    def test_the_field_costs_more_than_its_own_mean(self):
        hours = self.leg.resolved_distance_nm() / 6.0
        us, vs = [], []
        for i in range(49):
            e = self.field(0, 0, hours * i / 48)
            ang = math.radians(e.current_set_deg)
            us.append(e.current_speed_kt * math.sin(ang))
            vs.append(e.current_speed_kt * math.cos(ang))
        u, v = sum(us) / len(us), sum(vs) / len(vs)
        mean_leg = dataclasses.replace(
            self.leg, current_speed_kt=math.hypot(u, v),
            current_set_deg=math.degrees(math.atan2(u, v)) % 360.0)

        averaged = plan([mean_leg], self.env, self.vessel, MODEL).total_litres
        fielded = plan([self.leg], self.env, self.vessel, MODEL,
                       env_at=self.field).total_litres
        self.assertGreater(fielded, averaged,
                           'averaging a reversing tide understates the fuel')

    def test_a_stronger_tide_widens_the_gap(self):
        """Convexity: the penalty grows faster than the current does."""
        gaps = []
        for amp in (1.0, 2.0, 3.0):
            field = reversing_tide(self.env, amplitude_kt=amp)
            fielded = plan([self.leg], self.env, self.vessel, MODEL,
                           env_at=field).total_litres
            slack = plan([self.leg], self.env, self.vessel, MODEL).total_litres
            gaps.append(fielded - slack)
        self.assertEqual(gaps, sorted(gaps))
        self.assertGreater(gaps[2] - gaps[1], gaps[1] - gaps[0])

    def test_the_clock_is_untouched_by_the_field(self):
        """A current moves the fuel, never the clock — the field changes
        nothing about that."""
        a = plan([self.leg], self.env, self.vessel, MODEL)
        b = plan([self.leg], self.env, self.vessel, MODEL, env_at=self.field)
        self.assertAlmostEqual(a.total_hours, b.total_hours, places=9)

    def test_finer_runs_do_not_change_the_distance_flown(self):
        coarse = plan([self.leg], self.env, self.vessel, MODEL,
                      env_at=self.field, max_run_nm=2.0)
        fine = plan([self.leg], self.env, self.vessel, MODEL,
                    env_at=self.field, max_run_nm=0.25)
        self.assertAlmostEqual(coarse.total_distance_nm, fine.total_distance_nm,
                               places=6)


class TestFieldTiming(unittest.TestCase):

    def test_a_later_leg_sees_a_later_tide(self):
        """Leg three of a two-day mission must not see leg one's water. The
        field is asked for the hours elapsed since the mission started."""
        seen = []

        def env_at(lat, lon, hours):
            seen.append(hours)
            return None

        env = Environment()
        legs = [Leg('A', 'transit', 16.0, 8.0, 90.0,
                    track=[LEWES, tuple(geometry.move(geometry.Point(*LEWES), 90, 16))]),
                Leg('B', 'transit', 16.0, 8.0, 270.0,
                    track=[tuple(geometry.move(geometry.Point(*LEWES), 90, 16)), LEWES])]
        plan(legs, env, Vessel(gondola='em2040'), MODEL, env_at=env_at)
        self.assertGreater(max(seen), 2.0, 'the second leg must be asked about '
                                           'a time after the first one ended')
        self.assertEqual(seen, sorted(seen))

    def test_a_loiter_delays_the_tide_the_rest_of_the_leg_sees(self):
        seen = []

        def env_at(lat, lon, hours):
            seen.append(hours)
            return None

        track = [LEWES, tuple(geometry.move(geometry.Point(*LEWES), 90, 8))]
        leg = Leg('A', 'transit', 8.0, 8.0, 90.0, track=track, loiter_hours=3.0)
        plan_leg(leg, Environment(), MODEL, gondola='em2040', env_at=env_at)
        self.assertGreaterEqual(min(seen), 3.0)

    def test_a_field_that_declines_to_answer_falls_back_to_the_leg(self):
        """Past the end of a forecast, or over a shoal the model calls land:
        the plan must complete on the operator's own number rather than fail
        or silently plan slack water."""
        leg = Leg('A', 'transit', 10.0, 8.0, 90.0,
                  track=[LEWES, tuple(geometry.move(geometry.Point(*LEWES), 90, 10))],
                  current_speed_kt=1.5, current_set_deg=270.0)
        env = Environment()
        fielded = plan_leg(leg, env, MODEL, gondola='em2040',
                           env_at=lambda *a: None)
        plain = plan_leg(leg, env, MODEL, gondola='em2040')
        self.assertAlmostEqual(fielded.litres, plain.litres, places=9)


if __name__ == '__main__':
    unittest.main(verbosity=2)
