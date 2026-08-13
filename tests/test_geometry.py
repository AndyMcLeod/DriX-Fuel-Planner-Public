"""Mission geometry — tracklines, survey patterns, and the turns between them.

Every numeric expectation here is computed by hand or by an identity that
holds independently of the code under test (a lawnmower turn is exactly the
line spacing; splitting a run preserves its length). A test that merely
re-states the implementation would pass a mirrored bearing or a pattern whose
lines all start at the same end, which are precisely the mistakes this file
exists to catch.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geometry import (Point, Run, SurveyPattern, TurnModel,  # noqa: E402
                      course_deg, distance_nm, move, runs_from, track_runs,
                      turn_angle)

LEWES = Point(38.7828, -75.1394)


class TestRhumbArithmetic(unittest.TestCase):

    def test_sixty_miles_north_is_one_degree_of_latitude(self):
        p = move(Point(38.0, -75.0), 0.0, 60.0)
        self.assertAlmostEqual(p.lat, 39.0, places=6)
        self.assertAlmostEqual(p.lon, -75.0, places=6)

    def test_east_at_sixty_north_covers_two_degrees_of_longitude(self):
        p = move(Point(60.0, 0.0), 90.0, 60.0)
        self.assertAlmostEqual(p.lon, 2.0, places=3)
        self.assertAlmostEqual(p.lat, 60.0, places=6)

    def test_distance_and_course_invert_a_move(self):
        for brg in (0, 45, 90, 135, 180, 225, 270, 315):
            p = move(LEWES, brg, 12.5)
            self.assertAlmostEqual(distance_nm(LEWES, p), 12.5, places=3, msg=str(brg))
            self.assertAlmostEqual(course_deg(LEWES, p), brg, places=2, msg=str(brg))

    def test_course_is_measured_from_north_through_east(self):
        self.assertAlmostEqual(course_deg(Point(38, -75), Point(39, -75)), 0.0, places=6)
        self.assertAlmostEqual(course_deg(Point(38, -75), Point(38, -74)), 90.0, places=6)
        self.assertAlmostEqual(course_deg(Point(38, -75), Point(37, -75)), 180.0, places=6)
        self.assertAlmostEqual(course_deg(Point(38, -75), Point(38, -76)), 270.0, places=6)

    def test_a_zero_length_step_has_no_course_rather_than_a_random_one(self):
        self.assertEqual(course_deg(LEWES, LEWES), 0.0)

    def test_turn_angle_takes_the_short_way_round(self):
        self.assertAlmostEqual(turn_angle(350, 10), 20.0, places=9)
        self.assertAlmostEqual(turn_angle(10, 350), -20.0, places=9)

    def test_an_exact_reversal_is_half_a_circle_either_way_round(self):
        """At exactly 180 the SIZE of the turn is defined and its direction is
        not — port and starboard are the same manoeuvre. Asserting a sign here
        would be pinning an arbitrary choice."""
        for a, b in ((0, 180), (180, 0), (90, 270)):
            self.assertAlmostEqual(abs(turn_angle(a, b)), 180.0, places=9)

    def test_longitude_scaling_uses_the_mid_latitude_of_the_step(self):
        """A long north-east leg stretched by the START latitude comes out
        measurably wrong; the identity that catches it is that distance() must
        invert move() at any bearing."""
        p = move(Point(38.0, -75.0), 45.0, 300.0)
        self.assertAlmostEqual(distance_nm(Point(38.0, -75.0), p), 300.0, places=1)


class TestRunSplitting(unittest.TestCase):

    def setUp(self):
        self.run = Run(10.0, 90.0, Point(38.0, -75.0), move(Point(38.0, -75.0), 90.0, 10.0))

    def test_a_short_run_is_returned_untouched(self):
        self.assertEqual(self.run.split(20.0), [self.run])

    def test_splitting_preserves_the_total_distance(self):
        parts = self.run.split(1.0)
        self.assertEqual(len(parts), 10)
        self.assertAlmostEqual(sum(p.distance_nm for p in parts), 10.0, places=9)

    def test_the_pieces_join_end_to_end_and_span_the_original(self):
        parts = self.run.split(3.0)
        for a, b in zip(parts, parts[1:]):
            self.assertAlmostEqual(a.end.lat, b.start.lat, places=12)
            self.assertAlmostEqual(a.end.lon, b.start.lon, places=12)
        self.assertAlmostEqual(parts[0].start.lat, self.run.start.lat, places=12)
        self.assertAlmostEqual(parts[-1].end.lon, self.run.end.lon, places=12)

    def test_the_kind_survives_the_split(self):
        r = Run(5.0, 0.0, Point(38, -75), move(Point(38, -75), 0, 5), 'line', 'L1')
        self.assertTrue(all(p.kind == 'line' for p in r.split(1.0)))


class TestTrackRuns(unittest.TestCase):

    def test_a_polyline_becomes_its_segments(self):
        pts = [LEWES, move(LEWES, 90, 10), move(move(LEWES, 90, 10), 0, 5)]
        runs = track_runs(pts)
        self.assertEqual(len(runs), 2)
        self.assertAlmostEqual(runs[0].distance_nm, 10.0, places=3)
        self.assertAlmostEqual(runs[0].course_deg, 90.0, places=2)
        self.assertAlmostEqual(runs[1].distance_nm, 5.0, places=3)
        self.assertAlmostEqual(runs[1].course_deg, 0.0, places=2)

    def test_a_repeated_waypoint_is_dropped_not_given_a_course_of_zero(self):
        """A duplicated point is a typo. Keeping it would inject a
        zero-length run whose course is meaningless and whose environment
        lookup is wasted."""
        runs = track_runs([LEWES, LEWES, move(LEWES, 45, 3)])
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0].course_deg, 45.0, places=2)


class TestSurveyPattern(unittest.TestCase):

    def pattern(self, **kw):
        kw.setdefault('anchor', LEWES)
        kw.setdefault('bearing_deg', 20.0)
        kw.setdefault('length_nm', 5.0)
        kw.setdefault('spacing_nm', 0.1)
        kw.setdefault('lines', 4)
        return SurveyPattern(**kw)

    def test_alternate_lines_run_the_reciprocal(self):
        ends = self.pattern().line_endpoints()
        c = [course_deg(a, b) for a, b in ends]
        self.assertAlmostEqual(c[0], 20.0, places=2)
        self.assertAlmostEqual(c[1], 200.0, places=2)
        self.assertAlmostEqual(c[2], 20.0, places=2)

    def test_each_line_starts_where_the_last_one_finished_one_spacing_across(self):
        """The identity that catches a pattern whose lines all start at the
        same end: the gap between the end of a line and the start of the next
        is the line SPACING, not the line length.

        Tolerance is 0.001 NM (under 2 m), not machine precision, because the
        rhumb arithmetic scales longitude by the mid-latitude of each step and
        two steps composed in different orders differ by about 0.2 m over a
        5 NM line. That is the flat-earth approximation this module documents,
        and it is four orders of magnitude below the failure being guarded
        against — lines all starting at the same end would put this gap at the
        line length, 5 NM out.
        """
        p = self.pattern()
        ends = p.line_endpoints()
        for (a, b), (c, d) in zip(ends, ends[1:]):
            self.assertAlmostEqual(distance_nm(b, c), p.spacing_nm, places=3)

    def test_every_line_is_the_stated_length(self):
        p = self.pattern(length_nm=3.25)
        for a, b in p.line_endpoints():
            self.assertAlmostEqual(distance_nm(a, b), 3.25, places=4)

    def test_lines_step_perpendicular_to_the_line_bearing_by_default(self):
        p = self.pattern(lines=2, spacing_nm=1.0)
        first_start = p.line_endpoints()[0][0]
        second_end = p.line_endpoints()[1][1]
        # the second line's far end sits one spacing off the first line's start
        self.assertAlmostEqual(distance_nm(first_start, second_end), 1.0, places=3)
        self.assertAlmostEqual(course_deg(first_start, second_end), 110.0, places=1)

    def test_total_line_distance_is_lines_times_length(self):
        self.assertAlmostEqual(self.pattern(lines=7, length_nm=2.5).total_line_nm(),
                               17.5, places=9)

    def test_a_zero_line_pattern_produces_nothing_rather_than_raising(self):
        self.assertEqual(self.pattern(lines=0).line_endpoints(), [])


class TestTurnModel(unittest.TestCase):

    def test_a_wide_spacing_turns_in_a_half_circle(self):
        t = TurnModel(radius_nm=0.02)
        self.assertAlmostEqual(t.path_nm(0.10), math.pi * 0.02, places=9)

    def test_the_boundary_case_is_exactly_the_half_circle(self):
        t = TurnModel(radius_nm=0.02)
        self.assertAlmostEqual(t.path_nm(0.04), math.pi * 0.02, places=9)

    def test_close_spacing_costs_more_than_a_half_circle(self):
        """Lines closer than twice the turn radius do not admit a simple
        180 — the vehicle runs out and comes back, and pretending otherwise
        under-counts every close-spaced survey."""
        t = TurnModel(radius_nm=0.02)
        tight = t.path_nm(0.01)
        self.assertGreater(tight, math.pi * 0.02)
        self.assertAlmostEqual(tight, math.pi * 0.02 + 2 * (0.04 - 0.01), places=9)

    def test_a_zero_radius_turns_instantly_which_is_the_old_behaviour(self):
        self.assertEqual(TurnModel(radius_nm=0.0).path_nm(0.5), 0.0)

    def test_turn_path_grows_as_spacing_tightens(self):
        t = TurnModel(radius_nm=0.03)
        paths = [t.path_nm(s) for s in (0.10, 0.06, 0.04, 0.02, 0.01)]
        self.assertEqual(paths, sorted(paths))


class TestRunsFrom(unittest.TestCase):

    def test_a_pattern_yields_lines_and_turns_in_running_order(self):
        p = SurveyPattern(LEWES, 20.0, 2.0, 0.1, 3, turn=TurnModel(radius_nm=0.02))
        runs = runs_from(p, max_run_nm=99)
        self.assertEqual([r.kind for r in runs],
                         ['line', 'turn', 'line', 'turn', 'line'])

    def test_turns_can_be_left_out_which_is_the_pre_geometry_behaviour(self):
        p = SurveyPattern(LEWES, 20.0, 2.0, 0.1, 3)
        runs = runs_from(p, max_run_nm=99, include_turns=False)
        self.assertTrue(all(r.kind == 'line' for r in runs))
        self.assertAlmostEqual(sum(r.distance_nm for r in runs), 6.0, places=6)

    def test_runs_are_split_fine_enough_to_resolve_an_hourly_forecast(self):
        runs = runs_from([LEWES, move(LEWES, 90, 20)], max_run_nm=1.0)
        self.assertEqual(len(runs), 20)
        self.assertTrue(all(r.distance_nm <= 1.0 + 1e-9 for r in runs))

    def test_splitting_does_not_change_the_distance_flown(self):
        p = SurveyPattern(LEWES, 20.0, 5.0, 0.1, 4, turn=TurnModel(radius_nm=0.02))
        coarse = sum(r.distance_nm for r in runs_from(p, max_run_nm=99))
        fine = sum(r.distance_nm for r in runs_from(p, max_run_nm=0.25))
        self.assertAlmostEqual(coarse, fine, places=6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
