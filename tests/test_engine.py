"""Tests for the DriX fuel planning engine.

Run:  python -m unittest discover -s tests -v      (from the project root)
  or: python tests/test_engine.py

The point of these is not coverage, it is teeth. Each numeric test is checked
against a value computed independently of the engine, and the suite includes a
mutation guard (test_mutation_sensitivity) that fails if the coefficients are
perturbed — so a silently broken model.json cannot pass.
"""

import dataclasses
import datetime as dt
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import (HOME_MARKS_KM, KM_PER_NM, WAYPOINTS_KM,  # noqa: E402
                    Environment, Leg, Model, Vessel, default_waypoints,
                    load_model, max_survey_length, max_survey_lines, plan,
                    plan_leg, required_stw_kt)

# Coefficients as published in the analysis workbook, retyped here on purpose:
# if model.json drifts from the workbook, these tests break.
F0 = -1.7761355709825288
F1 = 0.0024949428685311897
SB = 1.1347810132138756
SM = 0.0026240783000814768
HEAD_AMP = 0.06913210895349385
# Measured gauge scale (litres of real fuel per indicated point) and its sigma,
# retyped from the Aug 2026 calibration for the same reason as the above.
LPP = 2.06
LPP_SIGMA = 0.11
# One radius, so the single-mark tests stay deterministic while the shipped
# default carries two (13 and 26 km). Set to the inner default so these tests
# exercise a radius that actually ships.
ONE_MARK_KM = 13.0
ONE = (ONE_MARK_KM,)
# The reserve floor is POLICY, not a measurement, so it is pinned here once
# and referenced everywhere rather than retyped. A test asserts model.json
# and the Vessel default both agree with it, which is the only thing that
# should break when the policy changes.
RESERVE_PCT = 25.0
RESERVE = RESERVE_PCT / 100.0


def rpm_for(kt):
    return (kt - SB) / SM


def lph_for(rpm):
    return F0 + F1 * rpm


class TestModelPrimitives(unittest.TestCase):
    def setUp(self):
        self.m = Model()

    def test_coefficients_match_the_workbook(self):
        self.assertAlmostEqual(self.m.f0, F0, places=12)
        self.assertAlmostEqual(self.m.f1, F1, places=12)
        self.assertAlmostEqual(self.m.sb, SB, places=12)
        self.assertAlmostEqual(self.m.sm, SM, places=12)

    def test_speed_rpm_roundtrip(self):
        for kt in (4.0, 6.0, 7.5, 8.0):
            self.assertAlmostEqual(self.m.speed_for_rpm(self.m.rpm_for_speed(kt)),
                                   kt, places=9)

    def test_known_points_from_the_workbook(self):
        # 8 kt needs ~2616 rpm; verified in the workbook and in the source data.
        self.assertAlmostEqual(self.m.rpm_for_speed(8.0), 2616.0, delta=1.0)
        # Fit-window edges map to 3.811 and 7.695 kt.
        self.assertAlmostEqual(self.m.speed_for_rpm(1020), 3.811, places=3)
        self.assertAlmostEqual(self.m.speed_for_rpm(2500), 7.695, places=3)

    def test_fuel_rate_matches_independent_arithmetic(self):
        for rpm in (1200, 1800, 2400):
            self.assertAlmostEqual(self.m.fuel_rate_lph(rpm), lph_for(rpm), places=12)

    def test_fuel_rate_never_negative(self):
        # The fitted line crosses zero at ~712 rpm; below that it must clamp,
        # not return negative fuel.
        self.assertEqual(self.m.fuel_rate_lph(100), 0.0)
        self.assertGreater(self.m.fuel_rate_lph(1020), 0.0)

    def test_fit_window_flagging(self):
        self.assertTrue(self.m.in_fit_window(1020))
        self.assertTrue(self.m.in_fit_window(2500))
        self.assertFalse(self.m.in_fit_window(1019.9))
        self.assertFalse(self.m.in_fit_window(2616))


class TestHeadingEffect(unittest.TestCase):
    def setUp(self):
        self.m = Model()

    def test_headwind_penalises_and_tailwind_credits(self):
        ref = self.m.head_ref_wind
        head = self.m.heading_premium(0.0, 0.0, ref)     # wind from dead ahead
        tail = self.m.heading_premium(180.0, 0.0, ref)   # wind from astern
        self.assertAlmostEqual(head, HEAD_AMP, places=9)
        self.assertAlmostEqual(tail, -HEAD_AMP, places=9)

    def test_beam_wind_is_neutral(self):
        for course in (90.0, 270.0):
            self.assertAlmostEqual(
                self.m.heading_premium(course, 0.0, self.m.head_ref_wind),
                0.0, places=12)

    def test_zero_wind_is_neutral(self):
        self.assertEqual(self.m.heading_premium(0.0, 0.0, 0.0), 0.0)

    def test_scales_with_wind_by_the_declared_exponent(self):
        ref = self.m.head_ref_wind
        doubled = self.m.heading_premium(0.0, 0.0, 2 * ref)
        self.assertAlmostEqual(doubled, HEAD_AMP * 2 ** self.m.head_exp, places=9)

    def test_symmetry_about_the_wind_axis(self):
        a = self.m.heading_premium(45.0, 0.0, 12.0)
        b = self.m.heading_premium(-45.0, 0.0, 12.0)
        self.assertAlmostEqual(a, b, places=12)


class TestLegArithmetic(unittest.TestCase):
    def setUp(self):
        self.m = Model()

    def test_transit_leg_against_hand_calculation(self):
        leg = Leg('t', 'transit', distance_nm=50.0, speed_kt=6.0, course_deg=0.0)
        env = Environment(wmo_sea_state=0, wind_speed_kt=0.0)
        r = plan_leg(leg, env, self.m)
        want_rpm = rpm_for(6.0)
        want_lph = lph_for(want_rpm)
        want_hours = 50.0 / 6.0
        self.assertAlmostEqual(r.rpm_required, want_rpm, places=9)
        self.assertAlmostEqual(r.fuel_rate_lph, want_lph, places=9)
        self.assertAlmostEqual(r.hours, want_hours, places=9)
        self.assertAlmostEqual(r.litres, want_lph * want_hours, places=9)

    def test_sea_state_raises_rpm_and_fuel(self):
        leg = Leg('t', 'transit', 50.0, 6.0)
        calm = plan_leg(leg, Environment(wmo_sea_state=0), self.m)
        rough = plan_leg(leg, Environment(wmo_sea_state=4), self.m)
        self.assertGreater(rough.rpm_required, calm.rpm_required)
        self.assertGreater(rough.litres, calm.litres)
        # and by exactly the declared premium
        p = self.m.sea_state_premium(4)
        self.assertAlmostEqual(rough.rpm_required, calm.rpm_required * (1 + p),
                               places=9)

    def test_survey_cancels_the_heading_term(self):
        env = Environment(wmo_sea_state=2, wind_speed_kt=20.0, wind_from_deg=0.0)
        for course in (0.0, 37.0, 90.0, 213.0):
            s = plan_leg(Leg('s', 'survey', 100.0, 6.0, course), env, self.m)
            self.assertAlmostEqual(s.heading_premium, 0.0, places=12,
                                   msg=f'course {course}')

    def test_transit_does_not_cancel_the_heading_term(self):
        env = Environment(wmo_sea_state=2, wind_speed_kt=20.0, wind_from_deg=0.0)
        t = plan_leg(Leg('t', 'transit', 100.0, 6.0, 0.0), env, self.m)
        self.assertGreater(t.heading_premium, 0.0)

    def test_extrapolation_is_flagged_at_survey_speed(self):
        r = plan_leg(Leg('s', 'survey', 100.0, 8.0), Environment(wmo_sea_state=0),
                     self.m)
        self.assertTrue(r.extrapolated)
        self.assertTrue(any('extrapolation' in n for n in r.notes))

    def test_no_extrapolation_flag_inside_the_window(self):
        r = plan_leg(Leg('t', 'transit', 100.0, 6.0), Environment(wmo_sea_state=0),
                     self.m)
        self.assertFalse(r.extrapolated)


class TestPlan(unittest.TestCase):
    def setUp(self):
        self.m = Model()
        self.legs = [
            Leg('Transit out', 'transit', 30.0, 6.0, 90.0),
            Leg('Survey', 'survey', 120.0, 6.0, 0.0),
            Leg('Transit home', 'transit', 30.0, 6.0, 270.0),
        ]
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=12.0, wind_from_deg=90.0)
        self.vessel = Vessel(capacity_l=250.0, reserve_fraction=0.15)

    def test_totals_are_the_sum_of_legs(self):
        p = plan(self.legs, self.env, self.vessel, self.m)
        self.assertAlmostEqual(p.total_litres, sum(l.litres for l in p.legs), places=9)
        self.assertAlmostEqual(p.total_hours, sum(l.hours for l in p.legs), places=9)
        self.assertAlmostEqual(p.total_distance_nm, 180.0, places=9)

    def test_reserve_arithmetic(self):
        p = plan(self.legs, self.env, self.vessel, self.m)
        self.assertAlmostEqual(p.reserve_litres, 37.5, places=9)
        self.assertAlmostEqual(p.remaining_litres, 250.0 - p.total_litres, places=9)
        self.assertAlmostEqual(p.margin_litres,
                               p.remaining_litres - p.reserve_litres, places=9)
        self.assertEqual(p.within_reserve, p.margin_litres >= 0)

    def test_headwind_out_and_tailwind_home_partly_offset(self):
        """Opposite transits in the same wind should roughly cancel — but not
        exactly, because time on each leg differs only if speeds differ."""
        env = Environment(wmo_sea_state=0, wind_speed_kt=12.0, wind_from_deg=0.0)
        out = plan_leg(Leg('o', 'transit', 30.0, 6.0, 0.0), env, self.m)
        home = plan_leg(Leg('h', 'transit', 30.0, 6.0, 180.0), env, self.m)
        self.assertGreater(out.litres, home.litres)
        mean = (out.litres + home.litres) / 2
        neutral = plan_leg(Leg('n', 'transit', 30.0, 6.0, 90.0), env, self.m)
        self.assertAlmostEqual(mean, neutral.litres, places=9)

    def test_breaching_the_reserve_is_reported(self):
        """A mission that eats into the reserve but still gets home. Sized to
        land between the floor and empty — see TestRunsDry for the other side."""
        legs = [Leg('Transit out', 'transit', 40.0, 7.0, 0.0),
                Leg('Survey', 'survey', 150.0, 7.0, 0.0),
                Leg('Transit home', 'transit', 40.0, 7.0, 180.0)]
        # ~130 L needed; a 145 L tank lands between the reserve floor and empty
        vessel = Vessel(capacity_l=145.0, reserve_fraction=0.15)
        p = plan(legs, self.env, vessel, self.m)
        self.assertFalse(p.within_reserve)
        self.assertFalse(p.runs_dry, 'this case must breach without running dry')
        self.assertTrue(any('BREACHES' in w for w in p.warnings))

    def test_an_overrunning_mission_reports_dry_instead(self):
        long_legs = [Leg('Transit out', 'transit', 200.0, 7.0, 0.0),
                     Leg('Survey', 'survey', 400.0, 7.0, 0.0),
                     Leg('Transit home', 'transit', 200.0, 7.0, 180.0)]
        p = plan(long_legs, self.env, self.vessel, self.m)
        self.assertFalse(p.within_reserve)
        self.assertTrue(p.runs_dry)
        self.assertTrue(any('RUNS THE TANK DRY' in w for w in p.warnings))

    def test_sensitivity_is_monotonic_in_the_premium(self):
        p = plan(self.legs, self.env, self.vessel, self.m)
        lits = [s['total_litres'] for s in p.sensitivity]
        self.assertEqual(lits, sorted(lits),
                         'more premium must never mean less fuel')

    def test_capacity_scenarios_shrink_the_margin(self):
        p = plan(self.legs, self.env, self.vessel, self.m)
        by_cap = {s['key']: s for s in p.capacity_scenarios}
        self.assertGreater(by_cap['nominal']['margin_litres'],
                           by_cap['dd2024']['margin_litres'])
        self.assertGreater(by_cap['dd2024']['margin_litres'],
                           by_cap['worstcase']['margin_litres'])

    def test_rejects_bad_input_and_collects_every_problem(self):
        with self.assertRaises(ValueError) as ctx:
            plan([Leg('bad', 'transit', -5.0, 0.0)], self.env,
                 Vessel(capacity_l=-1), self.m)
        msg = str(ctx.exception)
        self.assertIn('distance', msg)
        self.assertIn('speed', msg)
        self.assertIn('capacity', msg)

    def test_rejects_reserve_above_start_level(self):
        with self.assertRaises(ValueError):
            plan(self.legs, self.env,
                 Vessel(capacity_l=250, reserve_fraction=0.5,
                        start_level_fraction=0.4), self.m)

    def test_empty_plan_rejected(self):
        with self.assertRaises(ValueError):
            plan([], self.env, self.vessel, self.m)


class TestMaxSurveyLength(unittest.TestCase):
    def setUp(self):
        self.m = Model()
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=10.0)
        self.vessel = Vessel(250.0, 0.15)
        self.legs = [Leg('out', 'transit', 25.0, 6.0, 0.0),
                     Leg('survey', 'survey', 100.0, 6.0, 0.0),
                     Leg('home', 'transit', 25.0, 6.0, 180.0)]

    def test_solution_sits_exactly_on_the_reserve_floor(self):
        nm = max_survey_length(self.legs, self.env, self.vessel, self.m)
        self.assertGreater(nm, 0)
        at = [Leg('out', 'transit', 25.0, 6.0, 0.0),
              Leg('survey', 'survey', nm, 6.0, 0.0),
              Leg('home', 'transit', 25.0, 6.0, 180.0)]
        p = plan(at, self.env, self.vessel, self.m)
        # The solver works to the BINDING floor, so that is the one sitting on
        # zero — and the verdict, which is what the operator reads, must pass.
        self.assertEqual(p.verdict, 'ok')
        self.assertLess(abs(p.binding_margin_litres), 0.2)
        # Under (A) the needle and a 250 L tank nearly coincide, so the two
        # floors sit within a couple of litres. Assert the invariant that always
        # holds instead of a gap that is an artefact of the reading.
        self.assertAlmostEqual(p.binding_margin_litres,
                               min(p.margin_litres, p.gauge_margin_litres),
                               places=9)
        self.assertGreaterEqual(p.margin_litres, p.binding_margin_litres - 1e-9)

    def test_just_beyond_the_solution_breaches(self):
        nm = max_survey_length(self.legs, self.env, self.vessel, self.m)
        over = [Leg('out', 'transit', 25.0, 6.0, 0.0),
                Leg('survey', 'survey', nm + 2.0, 6.0, 0.0),
                Leg('home', 'transit', 25.0, 6.0, 180.0)]
        p = plan(over, self.env, self.vessel, self.m)
        self.assertNotEqual(p.verdict, 'ok')
        self.assertLess(p.binding_margin_litres, 0.0)

    def test_impossible_mission_returns_zero(self):
        far = [Leg('out', 'transit', 900.0, 7.0, 0.0),
               Leg('survey', 'survey', 10.0, 6.0, 0.0),
               Leg('home', 'transit', 900.0, 7.0, 180.0)]
        self.assertEqual(max_survey_length(far, self.env, self.vessel, self.m), 0.0)

    def test_rough_water_shortens_the_achievable_survey(self):
        calm = max_survey_length(self.legs, Environment(wmo_sea_state=0),
                                 self.vessel, self.m)
        rough = max_survey_length(self.legs, Environment(wmo_sea_state=4),
                                  self.vessel, self.m)
        self.assertLess(rough, calm)


class TestGondolas(unittest.TestCase):
    """Per-gondola curves: EM712 measured, EM2040 derived by transfer."""

    B2040 = -0.04343717952015602
    M2040 = 0.004033265611920235

    def setUp(self):
        self.m = Model()
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=10.0)
        self.legs = [Leg('out', 'transit', 25.0, 7.0, 0.0),
                     Leg('survey', 'survey', 120.0, 8.0, 0.0),
                     Leg('home', 'transit', 25.0, 7.0, 180.0)]

    # Measured MCAP-refit coefficients (6-day fit, 04-09 Aug 2026).
    # These mirror model.json deliberately: if a refit changes the model and
    # nobody updates them, this class fails and says so.
    B2040M = 0.48606839462590734
    M2040M = 0.003447858749954489
    Q0, Q1, Q2 = 2.9364356362594624, -0.002970433167208108, 1.458113245924223e-06

    def test_em2040_known_points(self):
        # 8 kt on the measured speed law is ~2172 rpm, inside the 1400-3100 window
        r8 = self.m.rpm_for_speed(8.0, 'em2040')
        self.assertAlmostEqual(r8, 2179.3, delta=0.5)
        self.assertAlmostEqual(self.m.speed_for_rpm(2000, 'em2040'),
                               self.B2040M + self.M2040M * 2000, places=9)
        # the per-gondola quadratic fuel law
        want = self.Q0 + self.Q1 * r8 + self.Q2 * r8 * r8
        self.assertAlmostEqual(self.m.fuel_rate_lph(r8, 'em2040'), want, places=9)
        self.assertAlmostEqual(want, 3.388, delta=0.002)
        # em712 still uses the shared linear law
        r8e = self.m.rpm_for_speed(8.0, 'em712')
        self.assertAlmostEqual(self.m.fuel_rate_lph(r8e, 'em712'),
                               F0 + F1 * r8e, places=9)

    def test_em2040_window_is_its_own(self):
        self.assertTrue(self.m.in_fit_window(2179, 'em2040'))
        self.assertFalse(self.m.in_fit_window(1300, 'em2040'))
        self.assertTrue(self.m.in_fit_window(1300, 'em712'))
        self.assertFalse(self.m.in_fit_window(2616, 'em712'))

    def test_em2040_is_faster_at_equal_rpm(self):
        for rpm in (1200, 1600, 2000, 2400):
            self.assertGreater(self.m.speed_for_rpm(rpm, 'em2040'),
                               self.m.speed_for_rpm(rpm, 'em712'))

    def test_survey_speed_is_inside_the_window_for_em2040_only(self):
        env = Environment(wmo_sea_state=0)
        r40 = plan_leg(Leg('s', 'survey', 100.0, 8.0), env, self.m, gondola='em2040')
        r12 = plan_leg(Leg('s', 'survey', 100.0, 8.0), env, self.m, gondola='em712')
        self.assertFalse(r40.extrapolated,
                         '8 kt on the EM2040 is ~1994 rpm — inside 1020-2500')
        self.assertTrue(r12.extrapolated)
        self.assertLess(r40.litres, r12.litres)

    def test_same_mission_is_cheaper_on_the_em2040(self):
        p40 = plan(self.legs, self.env, Vessel(gondola='em2040'), self.m)
        p12 = plan(self.legs, self.env, Vessel(gondola='em712'), self.m)
        self.assertLess(p40.total_litres, p12.total_litres)
        self.assertGreater(p40.margin_litres, p12.margin_litres)

    def test_plan_reports_gondola_status(self):
        # Both gondolas are measured since the Aug 2026 MCAP refit
        p = plan(self.legs, self.env, Vessel(gondola='em2040'), self.m)
        self.assertEqual(p.gondola, 'em2040')
        self.assertEqual(p.gondola_status, 'measured')
        self.assertFalse(any('derived, not measured' in w for w in p.warnings))
        p12 = plan(self.legs, self.env, Vessel(gondola='em712'), self.m)
        self.assertEqual(p12.gondola_status, 'measured')

    def test_a_derived_gondola_still_warns(self):
        # The derived-status warning path must survive for future transfers
        data = load_model()
        data['gondolas']['options']['em2040']['status'] = 'derived'
        p = plan(self.legs, self.env, Vessel(gondola='em2040'), Model(data))
        self.assertEqual(p.gondola_status, 'derived')
        self.assertTrue(any('derived, not measured' in w for w in p.warnings))

    def test_perturbing_the_em2040_fuel_law_moves_only_em2040_plans(self):
        data = load_model()
        data['gondolas']['options']['em2040']['fuel_vs_rpm']['q2'] = self.Q2 * 1.3
        bumped = Model(data)
        base = Model()
        p40b = plan(self.legs, self.env, Vessel(gondola='em2040'), base).total_litres
        p40m = plan(self.legs, self.env, Vessel(gondola='em2040'), bumped).total_litres
        p12b = plan(self.legs, self.env, Vessel(gondola='em712'), base).total_litres
        p12m = plan(self.legs, self.env, Vessel(gondola='em712'), bumped).total_litres
        self.assertGreater(abs(p40m - p40b) / p40b, 0.05,
                           'the quadratic term must drive em2040 fuel')
        self.assertAlmostEqual(p12m, p12b, places=9)

    def test_unknown_gondola_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            plan(self.legs, self.env, Vessel(gondola='em9999'), self.m)
        self.assertIn('unknown gondola', str(ctx.exception))

    def test_max_survey_is_longer_on_the_em2040(self):
        nm40 = max_survey_length(self.legs, self.env, Vessel(gondola='em2040'), self.m)
        nm12 = max_survey_length(self.legs, self.env, Vessel(gondola='em712'), self.m)
        self.assertGreater(nm40, nm12)

    def test_perturbing_the_em2040_law_moves_only_em2040_plans(self):
        data = load_model()
        data['gondolas']['options']['em2040']['speed_vs_rpm']['m'] = self.M2040 * 1.10
        bumped = Model(data)
        base = Model()
        p40b = plan(self.legs, self.env, Vessel(gondola='em2040'), base).total_litres
        p40m = plan(self.legs, self.env, Vessel(gondola='em2040'), bumped).total_litres
        p12b = plan(self.legs, self.env, Vessel(gondola='em712'), base).total_litres
        p12m = plan(self.legs, self.env, Vessel(gondola='em712'), bumped).total_litres
        self.assertGreater(abs(p40m - p40b) / p40b, 0.02,
                           'the em2040 speed law must drive em2040 plans')
        self.assertAlmostEqual(p12m, p12b, places=9,
                               msg='the em712 plan must not move')


class TestMutationSensitivity(unittest.TestCase):
    """A test suite that passes against a broken model is worthless. These
    perturb the coefficients and assert the results actually move."""

    def _plan_with(self, mutate):
        data = load_model()
        mutate(data)
        m = Model(data)
        legs = [Leg('out', 'transit', 30.0, 6.0, 0.0),
                Leg('survey', 'survey', 100.0, 6.0, 0.0),
                Leg('home', 'transit', 30.0, 6.0, 180.0)]
        return plan(legs, Environment(wmo_sea_state=2, wind_speed_kt=12.0),
                    Vessel(250.0, 0.15), m).total_litres

    def test_baseline(self):
        self.base = self._plan_with(lambda d: None)
        self.assertGreater(self.base, 0)

    def test_perturbing_the_fuel_slope_changes_the_answer(self):
        base = self._plan_with(lambda d: None)
        bumped = self._plan_with(
            lambda d: d['fuel_vs_rpm'].__setitem__('f1', F1 * 1.10))
        self.assertGreater(abs(bumped - base) / base, 0.05,
                           'a 10% change in the fuel slope must move total fuel')

    def test_perturbing_the_speed_curve_changes_the_answer(self):
        # Planning reads the gondolas block, not the legacy speed_vs_rpm_2024
        # block — this perturbs the path the engine actually uses.
        base = self._plan_with(lambda d: None)
        bumped = self._plan_with(
            lambda d: d['gondolas']['options']['em712']['speed_vs_rpm']
            .__setitem__('m', SM * 1.10))
        self.assertGreater(abs(bumped - base) / base, 0.02)

    def test_perturbing_the_sea_state_table_changes_the_answer(self):
        base = self._plan_with(lambda d: None)

        def bump(d):
            for row in d['sea_state_premium']['table']:
                if row['wmo'] == 2:
                    row['premium'] = 0.25
        self.assertGreater(abs(self._plan_with(bump) - base) / base, 0.05)

    def test_perturbing_the_heading_amplitude_changes_transit_legs(self):
        m_base, m_bump = Model(), Model(load_model())
        m_bump.head_amp = HEAD_AMP * 3
        env = Environment(wmo_sea_state=2, wind_speed_kt=12.0, wind_from_deg=0.0)
        leg = Leg('t', 'transit', 30.0, 6.0, 0.0)
        self.assertGreater(plan_leg(leg, env, m_bump).litres,
                           plan_leg(leg, env, m_base).litres * 1.02)


class TestRunsDry(unittest.TestCase):
    """A mission that overruns the tank is a different failure from one that
    merely eats into the reserve, and must be reported as such."""

    def setUp(self):
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=10.0)
        self.vessel = Vessel(250.0, 0.15)

    def test_dry_plan_is_flagged_dry_not_merely_breached(self):
        legs = [Leg('out', 'transit', 400.0, 7.0, 0.0),
                Leg('survey', 'survey', 600.0, 8.0, 0.0),
                Leg('home', 'transit', 400.0, 7.0, 180.0)]
        p = plan(legs, self.env, self.vessel)
        self.assertTrue(p.runs_dry)
        self.assertFalse(p.within_reserve)
        self.assertLess(p.remaining_litres, 0)
        self.assertTrue(any('RUNS THE TANK DRY' in w for w in p.warnings))
        self.assertFalse(any('BREACHES THE RESERVE' in w for w in p.warnings),
                         'a dry plan must not also claim to be a reserve breach')

    def test_reserve_breach_that_is_not_dry(self):
        """Sits between the reserve floor and empty — breach, but not dry."""
        legs = [Leg('out', 'transit', 25.0, 7.0, 0.0),
                Leg('survey', 'survey', 120.0, 8.0, 0.0),
                Leg('home', 'transit', 25.0, 7.0, 180.0)]
        # a small tank makes the same mission a breach without running dry
        v = Vessel(capacity_l=115.0, reserve_fraction=0.15)
        p = plan(legs, self.env, v)
        self.assertFalse(p.runs_dry)
        self.assertFalse(p.within_reserve)
        self.assertGreater(p.remaining_litres, 0)
        self.assertTrue(any('BREACHES THE RESERVE' in w for w in p.warnings))

    def test_healthy_plan_is_neither(self):
        legs = [Leg('out', 'transit', 20.0, 6.0, 0.0),
                Leg('survey', 'survey', 60.0, 6.0, 0.0),
                Leg('home', 'transit', 20.0, 6.0, 180.0)]
        p = plan(legs, self.env, self.vessel)
        self.assertFalse(p.runs_dry)
        self.assertTrue(p.within_reserve)

    def test_scenario_rows_carry_the_dry_flag(self):
        legs = [Leg('out', 'transit', 300.0, 7.0, 0.0),
                Leg('survey', 'survey', 500.0, 8.0, 0.0),
                Leg('home', 'transit', 300.0, 7.0, 180.0)]
        p = plan(legs, self.env, self.vessel)
        self.assertTrue(all('runs_dry' in s for s in p.sensitivity))
        self.assertTrue(all('runs_dry' in c for c in p.capacity_scenarios))
        self.assertTrue(any(c['runs_dry'] for c in p.capacity_scenarios))


class TestAcceptanceCases(unittest.TestCase):
    """Every refusal above is paired with a case that must be accepted."""

    def test_a_realistic_mission_is_accepted_and_sane(self):
        legs = [Leg('Transit out', 'transit', 20.0, 6.0, 45.0),
                Leg('Survey', 'survey', 80.0, 6.0, 0.0),
                Leg('Transit home', 'transit', 20.0, 6.0, 225.0)]
        p = plan(legs, Environment(wmo_sea_state=2, wind_speed_kt=12.0,
                                   wind_from_deg=45.0),
                 Vessel(250.0, 0.15))
        self.assertTrue(p.within_reserve)
        self.assertGreater(p.total_litres, 0)
        self.assertLess(p.total_litres, 250.0)
        self.assertEqual(len(p.legs), 3)
        self.assertAlmostEqual(p.total_distance_nm, 120.0)
        # efficiency should land in the ballpark the workbook establishes
        overall = p.total_distance_nm / p.total_litres
        self.assertGreater(overall, 1.0)
        self.assertLess(overall, 4.0)

    def test_zero_length_legs_are_allowed(self):
        legs = [Leg('out', 'transit', 0.0, 6.0), Leg('survey', 'survey', 50.0, 6.0),
                Leg('home', 'transit', 0.0, 6.0)]
        p = plan(legs, Environment(), Vessel())
        self.assertGreater(p.total_litres, 0)


class TestGaugeDenominatedReserve(unittest.TestCase):
    """The reserve floor is a needle position, not a number of litres.

    Everything here follows from one measured quantity — litres per indicated
    point — and none of it may depend on the assumed tank capacity. That
    independence is the whole finding: choosing a bigger tank cannot buy back
    endurance the measured gauge scale has already taken away.
    """

    def setUp(self):
        self.m = Model()
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=0.0)
        # ~100 NM: comfortably inside both bases.
        self.short = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                      Leg('survey', 'survey', 60.0, 8.0, 90.0),
                      Leg('home', 'transit', 20.0, 8.0, 180.0)]
        # ~450 NM: burns ~203 L, past the 185.7 L the profile allows to the
        # 25% floor and past a 250 L tank's 187.5 L — both bases breach.
        self.long = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                     Leg('survey', 'survey', 410.0, 8.0, 90.0),
                     Leg('home', 'transit', 20.0, 8.0, 180.0)]
        # ~540 NM: burns ~244 L, past the profile's 185.7 L to the 25% floor
        # but inside a 340 L tank's 255 L. This is the disagreement window,
        # and it only reopens above ~325 L now that the floor is 25%.
        self.longer = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                       Leg('survey', 'survey', 500.0, 8.0, 90.0),
                       Leg('home', 'transit', 20.0, 8.0, 180.0)]

    def _v(self, cap=250.0):
        return Vessel(capacity_l=cap, gondola='em2040')

    def test_the_reserve_policy_agrees_everywhere_it_is_written(self):
        """The floor lives in model.json AND as the Vessel default, so the two
        can drift apart. They are pinned together here because nothing else
        would notice: a mismatch would quietly plan the UI and the API to
        different floors."""
        self.assertAlmostEqual(self.m.data['reserve']['default_fraction'],
                               RESERVE, places=12)
        self.assertAlmostEqual(Vessel().reserve_fraction, RESERVE, places=12)

    def test_gauge_scale_matches_the_calibration(self):
        self.assertAlmostEqual(self.m.gauge_l_per_point, LPP, places=9)
        self.assertAlmostEqual(self.m.gauge_l_per_point_sigma, LPP_SIGMA, places=9)

    def test_the_measured_band_is_still_four_sigma_from_a_linear_tank(self):
        # The tension that forced reading (A): a linear 250 L tank needs 2.50
        # L/point and the measured band says 2.06. Stated independently.
        self.assertAlmostEqual((2.50 - LPP) / LPP_SIGMA, 4.0, places=1)
        # Under (A) the profile is built to hold the drawings volume, so the
        # gauge and a 250 L tank agree by construction and nothing is unlocated.
        p = plan(self.short, self.env, self._v(250.0), self.m)
        self.assertAlmostEqual(p.gauge_capacity_l, 250.0, places=6)
        self.assertAlmostEqual(p.gauge_capacity_delta_l, 0.0, places=6)
        self.assertAlmostEqual(p.gauge_unlocated_l, 0.0, places=6)
        # but the measured band itself is untouched by the reading
        self.assertAlmostEqual(self.m.gauge_profile.litres_between(72.0, 86.0) / 14.0,
                               LPP, places=9)

    def test_needle_on_return_is_independent_arithmetic(self):
        p = plan(self.short, self.env, self._v(), self.m)
        # Hand-computed without touching the profile code, and deliberately
        # across a band boundary: the 86-100% segment holds only ~36 L, so a
        # 45 L burn finishes inside the measured band. Getting this wrong by
        # assuming a single rate is exactly the error the profile exists to fix.
        rest = (250.0 - 14.0 * LPP) / 86.0
        top_segment = 14.0 * rest
        self.assertGreater(p.total_litres, top_segment, 'must cross the boundary')
        expected = 86.0 - (p.total_litres - top_segment) / LPP
        self.assertAlmostEqual(p.indicated_return_pct, expected, places=9)
        # the litres between the predicted needle and full must equal the burn
        self.assertAlmostEqual(
            self.m.gauge_profile.litres_between(p.indicated_return_pct, 100.0),
            p.total_litres, places=9)

    def test_mission_fuel_is_the_profile_integral_to_the_floor(self):
        p = plan(self.short, self.env, self._v(), self.m)
        prof = self.m.gauge_profile
        self.assertAlmostEqual(p.gauge_usable_litres,
                               prof.litres_between(RESERVE_PCT, 100.0), places=9)
        # and burning exactly that must land the needle exactly on the floor
        self.assertAlmostEqual(prof.level_after(100.0, p.gauge_usable_litres),
                               RESERVE_PCT, places=9)
        self.assertAlmostEqual(p.gauge_margin_litres,
                               p.gauge_usable_litres - p.total_litres, places=9)

    def test_mission_fuel_does_not_depend_on_assumed_capacity(self):
        """The structural claim. Believing a larger tank must not move it."""
        vals = [plan(self.short, self.env, self._v(c), self.m).gauge_usable_litres
                for c in (173.4, 206.0, 250.0, 400.0)]
        for v in vals[1:]:
            self.assertAlmostEqual(v, vals[0], places=9)
        # while the capacity-based figure moves freely, as it always did
        caps = [plan(self.short, self.env, self._v(c), self.m).usable_litres
                for c in (173.4, 250.0)]
        self.assertAlmostEqual(caps[1] - caps[0], (250.0 - 173.4) * (1 - RESERVE),
                               places=9)

    def test_capacity_passes_while_the_needle_breaches(self):
        # Under reading (A) the gauge and a 250 L tank almost coincide, so the
        # disagreement window is only ~1 L wide there. A larger assumed tank
        # reopens it, and the machinery must still work.
        p = plan(self.longer, self.env, self._v(340.0), self.m)
        self.assertTrue(p.within_reserve, 'the 340 L basis should still pass')
        self.assertFalse(p.gauge_within_reserve, 'the needle should not')
        self.assertLess(p.indicated_return_pct, RESERVE_PCT)
        self.assertTrue(any('GAUGE DISAGREES' in w for w in p.warnings))

    def test_no_false_alarm_on_a_mission_both_bases_clear(self):
        # Refusal paired with acceptance: the warning must stay silent here.
        p = plan(self.short, self.env, self._v(250.0), self.m)
        self.assertTrue(p.within_reserve)
        self.assertTrue(p.gauge_within_reserve)
        self.assertFalse(any('GAUGE DISAGREES' in w for w in p.warnings))

    def test_the_gauge_capacity_scenario_is_offered(self):
        p = plan(self.short, self.env, self._v(), self.m)
        by_key = {c['key']: c for c in p.capacity_scenarios}
        self.assertIn('gauge', by_key)
        self.assertAlmostEqual(by_key['gauge']['capacity_l'], 100.0 * LPP, places=9)
        # the 206 L row is the conservative reading (B) kept visible beside the
        # 250 L drawings row, so it must still be the tighter of the two
        self.assertLess(by_key['gauge']['margin_litres'],
                        by_key['nominal']['margin_litres'])

    def test_sensitivity_rows_carry_the_needle_verdict(self):
        p = plan(self.long, self.env, self._v(), self.m)
        self.assertTrue(all('gauge_margin_litres' in s for s in p.sensitivity))
        # a heavier premium can only make the needle verdict worse, never better
        margins = [s['gauge_margin_litres'] for s in p.sensitivity]
        self.assertEqual(margins, sorted(margins, reverse=True))

    def test_verdict_goes_red_when_only_the_needle_breaches(self):
        p = plan(self.longer, self.env, self._v(340.0), self.m)
        self.assertTrue(p.within_reserve, 'the capacity basis still passes')
        self.assertEqual(p.verdict, 'gauge_breach')

    def test_all_four_verdict_states_are_reachable_and_distinct(self):
        # long burns ~203 L, which lets one mission exercise three of them.
        self.assertEqual(plan(self.short, self.env, self._v(250.0), self.m).verdict,
                         'ok')
        self.assertEqual(plan(self.longer, self.env, self._v(340.0), self.m).verdict,
                         'gauge_breach')
        self.assertEqual(plan(self.long, self.env, self._v(206.0), self.m).verdict,
                         'breach')
        self.assertEqual(plan(self.long, self.env, self._v(120.0), self.m).verdict,
                         'dry')

    def test_spare_range_is_quoted_against_the_binding_floor(self):
        p = plan(self.longer, self.env, self._v(340.0), self.m)
        self.assertLess(p.binding_margin_litres, p.margin_litres)
        self.assertAlmostEqual(p.binding_margin_litres, p.gauge_margin_litres,
                               places=12)
        # past the needle floor, so spare range must not read as positive
        self.assertLess(p.binding_margin_nm, 0.0)
        self.assertLess(p.binding_margin_hours, 0.0)

    def test_capacity_binds_when_the_tank_is_smaller_than_the_gauge_implies(self):
        """The binding floor must work in BOTH directions. Below 100 × L/point
        the tank runs out before the needle reaches the floor, so capacity is
        the constraint — which is the case for the 205 L and 173 L scenarios.
        """
        p = plan(self.short, self.env, self._v(173.4), self.m)
        self.assertLess(p.margin_litres, p.gauge_margin_litres)
        self.assertAlmostEqual(p.binding_margin_litres, p.margin_litres, places=12)
        self.assertEqual(p.verdict, 'ok')
        # Under (A) the crossover is at the drawings volume, not at 206 L.
        hi = plan(self.short, self.env, self._v(300.0), self.m)
        self.assertLess(hi.gauge_margin_litres, hi.margin_litres)
        # ...and the mirror of gauge_breach: capacity fails while the needle
        # is still comfortable. ~380 NM burns between the two floors.
        mid = [Leg('out', 'transit', 20.0, 8.0, 0.0),
               Leg('survey', 'survey', 340.0, 8.0, 90.0),
               Leg('home', 'transit', 20.0, 8.0, 180.0)]
        q = plan(mid, self.env, self._v(173.4), self.m)
        self.assertLess(q.margin_litres, 0.0)
        self.assertGreater(q.gauge_margin_litres, 0.0)
        self.assertEqual(q.verdict, 'breach')

    def test_max_survey_answer_always_passes_its_own_verdict(self):
        """The property that forced the solver change: an answer the planner
        then flags red is a bug, not a plan."""
        nm = max_survey_length(self.short, self.env, self._v(250.0), self.m)
        at = [Leg('out', 'transit', 20.0, 8.0, 0.0),
              Leg('survey', 'survey', nm, 8.0, 90.0),
              Leg('home', 'transit', 20.0, 8.0, 180.0)]
        self.assertEqual(plan(at, self.env, self._v(250.0), self.m).verdict, 'ok')

    def test_without_gauge_calibration_the_binding_floor_is_capacity(self):
        data = load_model()
        del data['gauge_calibration']
        p = plan(self.short, self.env, self._v(250.0), Model(data))
        self.assertAlmostEqual(p.binding_margin_litres, p.margin_litres, places=12)
        self.assertEqual(p.verdict, 'ok')

    def test_under_reading_A_planning_follows_the_drawings(self):
        """Adopting (A) moves the dependency from the measurement to the drawing.

        Worth asserting because it is the least obvious consequence: the six
        days of gauge calibration now barely touch mission fuel, while the tank
        volume sets it almost entirely.
        """
        p = plan(self.short, self.env, self._v(250.0), self.m)
        self.assertAlmostEqual(p.tank_volume_l, 250.0, places=9)
        self.assertEqual(self.m.gauge_reading, 'A')
        # mission fuel is the tank less the fuel below the floor
        below = self.m.gauge_profile.litres_between(0.0, RESERVE_PCT)
        self.assertAlmostEqual(p.gauge_usable_litres, 250.0 - below, places=6)
        # +20% on the measured scale moves it by ~1 L, because the profile
        # re-normalises to the drawings volume...
        d = load_model()
        d['gauge_calibration']['l_per_point'] = LPP * 1.20
        q = plan(self.short, self.env, self._v(250.0), Model(d))
        self.assertLess(abs(q.gauge_usable_litres - p.gauge_usable_litres), 2.0)
        # ...while +20% on the tank volume moves it by tens of litres
        d2 = load_model()
        d2['tank_volume']['litres'] = 300.0
        r = plan(self.short, self.env, self._v(250.0), Model(d2))
        self.assertGreater(r.gauge_usable_litres - p.gauge_usable_litres, 30.0)

    def test_unlocated_volume_is_the_difference_between_the_readings(self):
        # Under (A) the profile accounts for the whole tank, so nothing is
        # unlocated, whatever the vessel is told its capacity is.
        for cap in (173.4, 206.0, 250.0, 400.0):
            p = plan(self.short, self.env, self._v(cap), self.m)
            self.assertAlmostEqual(p.gauge_unlocated_l, 0.0, places=6)
        # Under (B) the gauge speaks only for its own span and the 44 L reappears.
        data = load_model()
        data['gauge_profile']['reading'] = 'B'
        q = plan(self.short, self.env, self._v(), Model(data))
        self.assertAlmostEqual(q.gauge_capacity_l, 100.0 * LPP, places=9)
        self.assertAlmostEqual(q.gauge_unlocated_l, 250.0 - 100.0 * LPP, places=9)
        # and that is the 36 L of planning fuel the choice of reading is worth
        p = plan(self.short, self.env, self._v(), self.m)
        self.assertGreater(p.gauge_usable_litres - q.gauge_usable_litres, 30.0)

    def test_a_model_without_tank_volume_still_plans(self):
        data = load_model()
        del data['tank_volume']
        p = plan(self.short, self.env, self._v(), Model(data))
        self.assertIsNone(p.tank_volume_l)
        self.assertIsNone(p.gauge_unlocated_l)
        self.assertGreater(p.total_litres, 0.0)

    def test_perturbing_the_gauge_scale_moves_endurance_but_not_burn(self):
        """Mutation guard, and an invariant: the gauge calibration must not
        leak into the flow-meter-derived burn model."""
        data = load_model()
        data['tank_volume']['litres'] = 300.0
        bumped, base = Model(data), Model()
        pb = plan(self.short, self.env, self._v(), base)
        pm = plan(self.short, self.env, self._v(), bumped)
        self.assertGreater(abs(pm.gauge_usable_litres - pb.gauge_usable_litres), 30.0,
                           'under (A) the tank volume must drive mission fuel')
        self.assertGreater(abs(pm.indicated_return_pct - pb.indicated_return_pct), 1.0,
                           'and must drive the predicted needle')
        # Burn is measured by the flow meter and must not move at all.
        self.assertAlmostEqual(pm.total_litres, pb.total_litres, places=12)
        self.assertAlmostEqual(pm.margin_litres, pb.margin_litres, places=12)
        for a, b in zip(pm.legs, pb.legs):
            self.assertAlmostEqual(a.nm_per_l, b.nm_per_l, places=12)

    def test_a_model_without_gauge_calibration_still_plans(self):
        # Pre-2.4.0 model files carry no gauge block; nothing may crash.
        data = load_model()
        del data['gauge_calibration']
        p = plan(self.short, self.env, self._v(), Model(data))
        self.assertIsNone(p.gauge_l_per_point)
        self.assertIsNone(p.indicated_return_pct)
        self.assertIsNone(p.gauge_within_reserve)
        self.assertGreater(p.total_litres, 0.0)
        self.assertFalse(any('GAUGE DISAGREES' in w for w in p.warnings))


class TestCurrent(unittest.TestCase):
    """Set and drift. Kinematics, not an empirical premium.

    A current changes the through-water speed the hull must make to hold a
    given SOG. Fuel follows STW; the clock follows SOG.
    """

    def setUp(self):
        self.m = Model()
        self.legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                     Leg('survey', 'survey', 120.0, 8.0, 90.0),
                     Leg('home', 'transit', 20.0, 8.0, 180.0)]

    def _v(self):
        return Vessel(gondola='em2040')

    def _env(self, kt=0.0, set_deg=0.0):
        return Environment(wmo_sea_state=2, wind_speed_kt=0.0,
                           current_speed_kt=kt, current_set_deg=set_deg)

    # -- the vector triangle, against arithmetic done outside the engine ---- #

    def test_a_head_current_adds_its_drift_to_the_required_water_speed(self):
        # Course 000, current setting 180 = straight onto the bow.
        self.assertAlmostEqual(required_stw_kt(8.0, 0.0, 2.0, 180.0), 10.0, places=12)

    def test_a_following_current_subtracts_it(self):
        self.assertAlmostEqual(required_stw_kt(8.0, 0.0, 2.0, 0.0), 6.0, places=12)

    def test_a_beam_current_costs_a_little_either_way(self):
        """Pythagoras: the hull must crab, so STW exceeds SOG whichever beam."""
        want = math.sqrt(8.0 ** 2 + 2.0 ** 2)
        for set_deg in (90.0, 270.0):
            self.assertAlmostEqual(required_stw_kt(8.0, 0.0, 2.0, set_deg),
                                   want, places=12)
        self.assertGreater(want, 8.0)

    def test_no_current_is_the_ground_speed_exactly(self):
        for course in (0.0, 37.0, 180.0, 359.0):
            self.assertEqual(required_stw_kt(8.0, course, 0.0, 123.0), 8.0)

    # -- what it does to a plan -------------------------------------------- #

    def test_no_current_changes_nothing(self):
        """Absent means old behaviour, to twelve places."""
        a = plan(self.legs, Environment(wmo_sea_state=2, wind_speed_kt=0.0),
                 self._v(), self.m)
        b = plan(self.legs, self._env(0.0, 250.0), self._v(), self.m)
        self.assertAlmostEqual(a.total_litres, b.total_litres, places=12)
        self.assertAlmostEqual(a.total_hours, b.total_hours, places=12)

    def test_a_current_moves_the_fuel_but_never_the_clock(self):
        """The ground still has to be covered — duration follows SOG."""
        base = plan(self.legs, self._env(), self._v(), self.m)
        cur = plan(self.legs, self._env(2.0, 180.0), self._v(), self.m)
        self.assertAlmostEqual(cur.total_hours, base.total_hours, places=12)
        self.assertAlmostEqual(cur.total_distance_nm, base.total_distance_nm,
                               places=12)
        self.assertNotAlmostEqual(cur.total_litres, base.total_litres, places=3)

    def test_the_leg_reports_the_water_speed_it_actually_needs(self):
        p = plan(self.legs, self._env(2.0, 180.0), self._v(), self.m)
        out = p.legs[0]      # course 000, current onto the bow
        home = p.legs[2]     # course 180, same current now astern
        self.assertAlmostEqual(out.stw_kt, 10.0, places=9)
        self.assertAlmostEqual(home.stw_kt, 6.0, places=9)
        self.assertAlmostEqual(p.legs[0].hours, 20.0 / 8.0, places=12)

    def test_a_head_current_costs_more_than_the_reciprocal_gives_back(self):
        """The convexity argument, now for current rather than wind. Out and
        home are equal distances at equal SOG on reciprocal courses, so a
        current that cancelled would leave the pair unchanged."""
        base = plan(self.legs, self._env(), self._v(), self.m)
        cur = plan(self.legs, self._env(2.5, 180.0), self._v(), self.m)
        b_pair = base.legs[0].litres + base.legs[2].litres
        c_pair = cur.legs[0].litres + cur.legs[2].litres
        self.assertGreater(c_pair, b_pair)

    def test_a_survey_sees_the_current_line_by_line(self):
        """Alternate lines run reciprocal courses, so the current helps one and
        hinders the next; the spread must show up in the reported STW."""
        p = plan(self.legs, self._env(2.0, 90.0), self._v(), self.m)
        survey = p.legs[1]        # lines on 090, alternating to 270
        self.assertAlmostEqual(survey.stw_min, 6.0, places=9)
        self.assertAlmostEqual(survey.stw_max, 10.0, places=9)
        self.assertAlmostEqual(survey.stw_kt, 8.0, places=9)   # mean cancels
        # ...but the FUEL does not cancel, which is the whole point.
        base = plan(self.legs, self._env(), self._v(), self.m)
        self.assertGreater(survey.litres, base.legs[1].litres)

    def test_the_note_admits_the_double_count(self):
        """The speed law is SOG-based and the heading premium already mixes
        current in. Saying so is the honest part."""
        p = plan(self.legs, self._env(2.0, 180.0), self._v(), self.m)
        note = ' '.join(p.legs[0].notes)
        self.assertIn('KINEMATIC', note)
        self.assertIn('counted twice', note)

    def test_a_current_that_carries_the_hull_is_flagged(self):
        p = plan(self.legs, self._env(8.0, 0.0), self._v(), self.m)
        note = ' '.join(p.legs[0].notes)
        self.assertIn('being carried', note)

    def test_the_plan_carries_the_current_it_was_run_under(self):
        """The summary tile reads these. They must come from the plan, so a
        form edited after planning cannot make the tile describe conditions the
        numbers were never computed under."""
        p = plan(self.legs, self._env(1.7, 235.0), self._v(), self.m)
        self.assertAlmostEqual(p.current_speed_kt, 1.7, places=12)
        self.assertAlmostEqual(p.current_set_deg, 235.0, places=12)
        slack = plan(self.legs, self._env(), self._v(), self.m)
        self.assertEqual(slack.current_speed_kt, 0.0)

    def test_a_negative_current_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            plan(self.legs, self._env(-1.0, 0.0), self._v(), self.m)
        self.assertIn('current speed must not be negative', str(cm.exception))
        # ...and the acceptance case, so the refusal is not vacuous.
        p = plan(self.legs, self._env(1.0, 0.0), self._v(), self.m)
        self.assertGreater(p.total_litres, 0.0)


class TestPerLegWeather(unittest.TestCase):
    """Wind and current belong to the leg, not the mission.

    A mission runs two days at survey speed, so the wind on the bow going out
    is not the wind coming home. Each field is None to mean "use the mission
    Environment", which is NOT the same as zero.
    """

    def setUp(self):
        self.m = Model()
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=20.0,
                               wind_from_deg=0.0, current_speed_kt=2.0,
                               current_set_deg=90.0)

    def _v(self):
        return Vessel(gondola='em2040')

    def _legs(self, **out_kw):
        return [Leg('out', 'transit', 30.0, 8.0, 0.0, **out_kw),
                Leg('survey', 'survey', 60.0, 8.0, 90.0),
                Leg('home', 'transit', 30.0, 8.0, 180.0)]

    def test_a_leg_with_no_weather_of_its_own_uses_the_mission(self):
        p = plan(self._legs(), self.env, self._v(), self.m)
        for leg in p.legs:
            self.assertAlmostEqual(leg.wind_speed_kt, 20.0, places=12)
            self.assertAlmostEqual(leg.wind_from_deg, 0.0, places=12)
            self.assertAlmostEqual(leg.current_speed_kt, 2.0, places=12)
            self.assertAlmostEqual(leg.current_set_deg, 90.0, places=12)

    def test_a_leg_overrides_only_what_it_sets(self):
        """Speed and direction are independent: changing strength alone must
        keep the mission's direction, not silently reset it to north."""
        p = plan(self._legs(wind_speed_kt=5.0), self.env, self._v(), self.m)
        self.assertAlmostEqual(p.legs[0].wind_speed_kt, 5.0, places=12)
        self.assertAlmostEqual(p.legs[0].wind_from_deg, 0.0, places=12)   # inherited
        self.assertAlmostEqual(p.legs[1].wind_speed_kt, 20.0, places=12)  # untouched

    def test_zero_is_an_override_not_an_absence(self):
        """A becalmed leg must be plannable. If 0 were read as 'unset' the leg
        would silently inherit 20 kt."""
        calm = plan(self._legs(wind_speed_kt=0.0, current_speed_kt=0.0),
                    self.env, self._v(), self.m)
        self.assertEqual(calm.legs[0].wind_speed_kt, 0.0)
        self.assertEqual(calm.legs[0].current_speed_kt, 0.0)
        windy = plan(self._legs(), self.env, self._v(), self.m)
        self.assertLess(calm.legs[0].litres, windy.legs[0].litres)

    def test_each_leg_is_costed_under_its_own_weather(self):
        """The whole point: a head current out and a fair one home must not be
        computed from one mission-wide vector."""
        legs = [Leg('out', 'transit', 30.0, 8.0, 0.0,
                    current_speed_kt=2.0, current_set_deg=180.0),
                Leg('survey', 'survey', 60.0, 8.0, 90.0,
                    current_speed_kt=0.0),
                Leg('home', 'transit', 30.0, 8.0, 180.0,
                    current_speed_kt=2.0, current_set_deg=180.0)]
        p = plan(legs, Environment(wmo_sea_state=2), self._v(), self.m)
        # Out is on 000 into a current setting 180 — dead on the bow.
        self.assertAlmostEqual(p.legs[0].stw_kt, 10.0, places=9)
        # Home is on 180 with the same current now astern.
        self.assertAlmostEqual(p.legs[2].stw_kt, 6.0, places=9)
        # The survey said 0 explicitly, so it gets no current at all.
        self.assertAlmostEqual(p.legs[1].stw_kt, 8.0, places=9)
        self.assertGreater(p.legs[0].litres, p.legs[2].litres)

    def test_the_mission_environment_still_works_alone(self):
        """The API's old shape must keep planning identically."""
        legs = [Leg('out', 'transit', 30.0, 8.0, 0.0),
                Leg('survey', 'survey', 60.0, 8.0, 90.0),
                Leg('home', 'transit', 30.0, 8.0, 180.0)]
        a = plan(legs, self.env, self._v(), self.m)
        b = plan([Leg('out', 'transit', 30.0, 8.0, 0.0, wind_speed_kt=20.0,
                      wind_from_deg=0.0, current_speed_kt=2.0, current_set_deg=90.0),
                  Leg('survey', 'survey', 60.0, 8.0, 90.0, wind_speed_kt=20.0,
                      wind_from_deg=0.0, current_speed_kt=2.0, current_set_deg=90.0),
                  Leg('home', 'transit', 30.0, 8.0, 180.0, wind_speed_kt=20.0,
                      wind_from_deg=0.0, current_speed_kt=2.0, current_set_deg=90.0)],
                 Environment(wmo_sea_state=2), self._v(), self.m)
        self.assertAlmostEqual(a.total_litres, b.total_litres, places=9)

    def test_sea_state_is_per_leg_too(self):
        """Held back at first on the grounds that the premium rests on a single
        anchor — but that is about how far the dial can be trusted, not about
        whether it should be one number for two days. A survey flown into a
        building sea is an ordinary mission."""
        legs = [Leg('out', 'transit', 30.0, 8.0, 0.0, wmo_sea_state=1),
                Leg('survey', 'survey', 60.0, 8.0, 90.0, wmo_sea_state=4),
                Leg('home', 'transit', 30.0, 8.0, 180.0)]
        p = plan(legs, Environment(wmo_sea_state=2), self._v(), self.m)
        self.assertEqual([l.wmo_sea_state for l in p.legs], [1, 4, 2])
        # A rising sea costs more RPM, and each leg carries its own premium.
        self.assertLess(p.legs[0].sea_premium, p.legs[2].sea_premium)
        self.assertLess(p.legs[2].sea_premium, p.legs[1].sea_premium)
        self.assertEqual(len({l.sea_premium for l in p.legs}), 3)

    def test_a_calm_sea_state_of_zero_is_an_override(self):
        """0 is a real WMO code, so it must not be read as "unset"."""
        legs = [Leg('out', 'transit', 30.0, 8.0, 0.0, wmo_sea_state=0),
                Leg('survey', 'survey', 60.0, 8.0, 90.0),
                Leg('home', 'transit', 30.0, 8.0, 180.0)]
        p = plan(legs, Environment(wmo_sea_state=5), self._v(), self.m)
        self.assertEqual(p.legs[0].wmo_sea_state, 0)
        self.assertEqual(p.legs[1].wmo_sea_state, 5)
        self.assertLess(p.legs[0].sea_premium, p.legs[1].sea_premium)

    def test_the_sensitivity_rows_see_per_leg_weather(self):
        legs = self._legs(wind_speed_kt=0.0, current_speed_kt=0.0)
        p = plan(legs, self.env, self._v(), self.m)
        as_planned = next(s for s in p.sensitivity if s['premium_delta'] == 0)
        self.assertAlmostEqual(as_planned['total_litres'], p.total_litres, places=9)

    def test_negative_per_leg_weather_is_refused(self):
        for kw in ({'wind_speed_kt': -1.0}, {'current_speed_kt': -1.0}):
            with self.assertRaises(ValueError) as cm:
                plan(self._legs(**kw), self.env, self._v(), self.m)
            self.assertIn('must not be negative', str(cm.exception))
        # ...and the acceptance case, so the refusal is not vacuous.
        p = plan(self._legs(wind_speed_kt=1.0, current_speed_kt=1.0),
                 self.env, self._v(), self.m)
        self.assertAlmostEqual(p.legs[0].wind_speed_kt, 1.0, places=12)


class TestLoiter(unittest.TestCase):
    """Delays imposed on a leg, charged at the gondola's idle burn.

    Things happen at sea; the model accommodates them rather than pretending
    the plan ran clean. Loiter is held at the END of its leg.
    """

    def setUp(self):
        self.m = Model()
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=0.0)
        self.legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                     Leg('survey', 'survey', 120.0, 8.0, 90.0),
                     Leg('home', 'transit', 20.0, 8.0, 180.0)]

    def _v(self):
        return Vessel(gondola='em2040')

    def _with_loiter(self, idx, hours):
        legs = []
        for i, l in enumerate(self.legs):
            d = {f.name: getattr(l, f.name) for f in dataclasses.fields(l)}
            if i == idx:
                d['loiter_hours'] = hours
            legs.append(Leg(**d))
        return legs

    def test_no_loiter_changes_nothing(self):
        """The safety property behind the whole feature: an absent delay must
        reproduce the old plan exactly, not approximately."""
        a = plan(self.legs, self.env, self._v(), self.m)
        b = plan(self._with_loiter(0, 0.0), self.env, self._v(), self.m)
        self.assertAlmostEqual(a.total_litres, b.total_litres, places=12)
        self.assertAlmostEqual(a.total_hours, b.total_hours, places=12)
        self.assertEqual(a.verdict, b.verdict)

    def test_loiter_burns_the_measured_idle_rate(self):
        """Against arithmetic done outside the engine, from model.json."""
        lph = self.m.data['gondolas']['options']['em2040']['loiter']['lph']
        self.assertEqual(lph, 0.95)
        base = plan(self.legs, self.env, self._v(), self.m)
        held = plan(self._with_loiter(1, 3.0), self.env, self._v(), self.m)
        self.assertAlmostEqual(held.total_litres - base.total_litres,
                               3.0 * lph, places=9)
        self.assertAlmostEqual(held.total_hours - base.total_hours, 3.0, places=12)

    def test_the_leg_keeps_its_underway_figures_separate(self):
        """`litres == fuel_rate_lph * hours` has to keep holding, or every
        reader of the leg table is quietly reading a blended rate."""
        p = plan(self._with_loiter(1, 2.0), self.env, self._v(), self.m)
        leg = p.legs[1]
        self.assertAlmostEqual(leg.litres, leg.fuel_rate_lph * leg.hours, places=9)
        self.assertAlmostEqual(leg.loiter_litres, 2.0 * leg.loiter_lph, places=12)
        self.assertAlmostEqual(leg.total_litres, leg.litres + leg.loiter_litres,
                               places=12)
        # The timeline carries the hold; the underway hours do not.
        self.assertAlmostEqual(leg.end_hours - leg.start_hours,
                               leg.hours + 2.0, places=12)

    def test_loiter_burns_no_distance_and_does_not_move_nm_per_l(self):
        base = plan(self.legs, self.env, self._v(), self.m)
        held = plan(self._with_loiter(1, 5.0), self.env, self._v(), self.m)
        self.assertAlmostEqual(held.total_distance_nm, base.total_distance_nm,
                               places=12)
        self.assertAlmostEqual(held.legs[1].nm_per_l, base.legs[1].nm_per_l,
                               places=12)

    def test_a_hold_shifts_everything_after_it_and_nothing_before(self):
        """Loiter sits at the END of its leg, so the first leg's own outbound
        waypoint is untouched while the inbound one moves out by the delay."""
        base = plan(self.legs, self.env, self._v(), self.m, waypoints=(13.0,))
        held = plan(self._with_loiter(0, 4.0), self.env, self._v(), self.m,
                    waypoints=(13.0,))
        b_out = next(m for m in base.marks if m['phase'] == 'outbound')
        h_out = next(m for m in held.marks if m['phase'] == 'outbound')
        self.assertAlmostEqual(b_out['elapsed_hours'], h_out['elapsed_hours'],
                               places=12)
        b_in = next(m for m in base.marks if m['phase'] == 'inbound')
        h_in = next(m for m in held.marks if m['phase'] == 'inbound')
        self.assertAlmostEqual(h_in['elapsed_hours'] - b_in['elapsed_hours'],
                               4.0, places=12)

    def test_the_fuel_at_a_later_mark_includes_the_hold(self):
        """An ordering assertion would pass with the hold's fuel dropped."""
        lph = self.m.data['gondolas']['options']['em2040']['loiter']['lph']
        base = plan(self.legs, self.env, self._v(), self.m, waypoints=(13.0,))
        held = plan(self._with_loiter(0, 4.0), self.env, self._v(), self.m,
                    waypoints=(13.0,))
        b_in = next(m for m in base.marks if m['phase'] == 'inbound')
        h_in = next(m for m in held.marks if m['phase'] == 'inbound')
        self.assertAlmostEqual(h_in['litres_burned'] - b_in['litres_burned'],
                               4.0 * lph, places=9)

    def test_a_long_enough_hold_breaches_the_reserve(self):
        """The point of the feature: a delay must be able to change the answer,
        not just the arithmetic."""
        ok = plan(self.legs, self.env, self._v(), self.m)
        self.assertEqual(ok.verdict, 'ok')
        held = plan(self._with_loiter(1, 400.0), self.env, self._v(), self.m)
        self.assertNotEqual(held.verdict, 'ok')
        self.assertLess(held.binding_margin_litres, 0.0)

    def test_the_sensitivity_rows_carry_the_hold(self):
        """Otherwise shifting the premium appears to change the whole burn
        while the hold sits outside every row."""
        lph = self.m.data['gondolas']['options']['em2040']['loiter']['lph']
        base = plan(self.legs, self.env, self._v(), self.m)
        held = plan(self._with_loiter(1, 6.0), self.env, self._v(), self.m)
        for b, h in zip(base.sensitivity, held.sensitivity):
            self.assertAlmostEqual(h['total_litres'] - b['total_litres'],
                                   6.0 * lph, places=9)

    def test_a_gondola_without_a_measured_idle_burn_says_so(self):
        """EM712 has no loiter block. The rate must come from ITS OWN fuel law,
        never borrowed from the gondola that was measured, and be flagged."""
        lph_2040, measured_2040, _ = self.m.loiter_rate_lph('em2040')
        lph_712, measured_712, rpm = self.m.loiter_rate_lph('em712')
        self.assertTrue(measured_2040)
        self.assertFalse(measured_712)
        self.assertNotAlmostEqual(lph_712, lph_2040, places=6)
        self.assertAlmostEqual(lph_712, self.m.fuel_rate_lph(rpm, 'em712'),
                               places=12)
        p = plan(self._with_loiter(1, 2.0), self.env,
                 Vessel(gondola='em712'), self.m)
        self.assertFalse(p.legs[1].loiter_measured)
        self.assertTrue(any('EXTRAPOLATION' in n for n in p.legs[1].notes),
                        p.legs[1].notes)

    def test_the_hold_note_names_the_sea_state_gap(self):
        """The idle figure is calm-water. Saying so is the honest part."""
        p = plan(self._with_loiter(1, 2.0), self.env, self._v(), self.m)
        note = ' '.join(p.legs[1].notes)
        self.assertIn('NO sea-state premium', note)

    def test_the_plan_totals_the_holds_across_legs(self):
        """The summary tile reads these, so they must be the sum of the legs and
        must already be inside total_hours/total_litres, not additional to them."""
        legs = []
        for i, l in enumerate(self.legs):
            d = {f.name: getattr(l, f.name) for f in dataclasses.fields(l)}
            d['loiter_hours'] = [0.5, 1.5, 0.25][i]
            legs.append(Leg(**d))
        p = plan(legs, self.env, self._v(), self.m)
        self.assertAlmostEqual(p.total_loiter_hours, 2.25, places=12)
        self.assertAlmostEqual(p.total_loiter_hours,
                               sum(l.loiter_hours for l in p.legs), places=12)
        self.assertAlmostEqual(p.total_loiter_litres,
                               sum(l.loiter_litres for l in p.legs), places=12)
        # Already counted in the mission totals — a tile that double-counted
        # would read plausibly and be wrong.
        base = plan(self.legs, self.env, self._v(), self.m)
        self.assertAlmostEqual(p.total_hours - base.total_hours, 2.25, places=12)
        self.assertAlmostEqual(p.total_litres - base.total_litres,
                               p.total_loiter_litres, places=9)

    def test_a_plan_with_no_holds_totals_zero(self):
        p = plan(self.legs, self.env, self._v(), self.m)
        self.assertEqual(p.total_loiter_hours, 0.0)
        self.assertEqual(p.total_loiter_litres, 0.0)

    def test_a_negative_hold_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            plan(self._with_loiter(0, -1.0), self.env, self._v(), self.m)
        self.assertIn('loiter must not be negative', str(cm.exception))
        # ...and the acceptance case, so the refusal is not vacuous.
        p = plan(self._with_loiter(0, 0.25), self.env, self._v(), self.m)
        self.assertAlmostEqual(p.legs[0].loiter_hours, 0.25, places=12)


class TestMissionClock(unittest.TestCase):
    """Elapsed time, clock times, and the mission waypoints."""

    def setUp(self):
        self.m = Model()
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=0.0)
        self.start = dt.datetime(2026, 8, 11, 6, 30)
        # 20 out / 120 survey / 20 home, all at 8 kt -> 2.5 + 15 + 2.5 = 20 h.
        self.legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                     Leg('survey', 'survey', 120.0, 8.0, 90.0),
                     Leg('home', 'transit', 20.0, 8.0, 180.0)]

    def _v(self):
        return Vessel(gondola='em2040')

    def test_leg_timeline_is_cumulative_and_matches_hours(self):
        p = plan(self.legs, self.env, self._v(), self.m)
        self.assertAlmostEqual(p.legs[0].start_hours, 0.0, places=12)
        run = 0.0
        for leg in p.legs:
            self.assertAlmostEqual(leg.start_hours, run, places=12)
            run += leg.hours
            self.assertAlmostEqual(leg.end_hours, run, places=12)
        self.assertAlmostEqual(p.legs[-1].end_hours, p.total_hours, places=12)
        self.assertAlmostEqual(p.total_hours, 20.0, places=9)

    def test_no_start_time_means_no_clock_but_still_elapsed(self):
        p = plan(self.legs, self.env, self._v(), self.m)
        self.assertIsNone(p.start_clock)
        self.assertIsNone(p.finish_clock)
        self.assertTrue(all(l.end_clock is None for l in p.legs))
        self.assertGreater(p.legs[-1].end_hours, 0.0)
        # marks still exist, timed in elapsed hours only
        self.assertEqual(len(p.marks), 6)      # survey in/out + two radii, in and out
        self.assertTrue(all(m['clock'] is None for m in p.marks))

    def test_clock_times_are_start_plus_elapsed(self):
        p = plan(self.legs, self.env, self._v(), self.m, start_time=self.start)
        self.assertEqual(p.start_iso, '2026-08-11T06:30')
        want = self.start + dt.timedelta(hours=p.total_hours)
        self.assertEqual(p.finish_iso, want.isoformat(timespec='minutes'))
        for leg in p.legs:
            expect = self.start + dt.timedelta(hours=leg.end_hours)
            self.assertEqual(leg.end_iso, expect.isoformat(timespec='minutes'))

    def test_display_carries_the_date_and_flags_day_rollover(self):
        p = plan(self.legs, self.env, self._v(), self.m, start_time=self.start)
        self.assertEqual(p.start_clock, '11 Aug 06:30')       # no suffix on day 0
        self.assertIn('(+1d)', p.finish_clock)                # 20 h from 06:30
        self.assertIn('12 Aug', p.finish_clock)

    def test_marks_sit_at_the_right_distance_from_home(self):
        p = plan(self.legs, self.env, self._v(), self.m, start_time=self.start,
                 home_marks_km=ONE)
        by = {m['phase']: m for m in p.marks if m['kind'] == 'range'}
        self.assertEqual(set(by), {'outbound', 'inbound'})
        nm = ONE_MARK_KM / KM_PER_NM
        # outbound: that far along the first leg
        self.assertAlmostEqual(by['outbound']['elapsed_hours'], nm / 8.0, places=12)
        # inbound: that far short of the end of the last
        self.assertAlmostEqual(by['inbound']['elapsed_hours'],
                               p.total_hours - nm / 8.0, places=12)
        self.assertAlmostEqual(by['inbound']['nm_from_home'], nm, places=12)

    def test_mark_distance_is_configurable_and_moves_the_marks(self):
        a = plan(self.legs, self.env, self._v(), self.m, home_marks_km=(7.0,))
        b = plan(self.legs, self.env, self._v(), self.m, home_marks_km=(14.0,))
        ain = next(m for m in a.marks if m['phase'] == 'inbound')
        bin_ = next(m for m in b.marks if m['phase'] == 'inbound')
        self.assertLess(bin_['elapsed_hours'], ain['elapsed_hours'])
        self.assertAlmostEqual(bin_['nm_from_home'], 2 * ain['nm_from_home'],
                               places=12)

    def test_fuel_at_the_mark_is_the_fuel_actually_burned_by_then(self):
        """Checked against arithmetic done outside the engine.

        An ordering assertion is not enough here: the inbound mark still sits
        16 NM into its own leg, so 'outbound < inbound' holds even if the fuel
        burned on every previous leg is dropped. A mutation run found that.
        """
        p = plan(self.legs, self.env, self._v(), self.m, home_marks_km=ONE)
        by = {m['phase']: m for m in p.marks if m['kind'] == 'range'}
        nm = ONE_MARK_KM / KM_PER_NM
        first, last = p.legs[0], p.legs[-1]

        # outbound: only the run made good on the first leg
        self.assertAlmostEqual(by['outbound']['litres_burned'],
                               first.fuel_rate_lph * (nm / first.speed_kt),
                               places=9)
        # inbound: everything except the last `nm` still to run
        self.assertAlmostEqual(by['inbound']['litres_burned'],
                               p.total_litres
                               - last.fuel_rate_lph * (nm / last.speed_kt),
                               places=9)
        # and it must exceed the two legs already behind it
        self.assertGreater(by['inbound']['litres_burned'],
                           p.legs[0].litres + p.legs[1].litres)
        self.assertLess(by['inbound']['litres_burned'], p.total_litres)
        # the needle at the inbound mark must read above the return figure
        self.assertGreater(by['inbound']['indicated_pct'], p.indicated_return_pct)

    def test_a_short_return_leg_reports_no_inbound_crossing(self):
        """Refusal paired with the acceptance above: inside the radius already."""
        nm = ONE_MARK_KM / KM_PER_NM
        legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                Leg('survey', 'survey', 40.0, 8.0, 90.0),
                Leg('home', 'transit', nm / 2, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m, home_marks_km=ONE)
        self.assertEqual([m['phase'] for m in p.marks if m['kind'] == 'range'],
                         ['outbound'])
        self.assertTrue(any('already inside' in w for w in p.warnings))

    def test_a_short_first_leg_reports_no_outbound_crossing(self):
        nm = ONE_MARK_KM / KM_PER_NM
        legs = [Leg('out', 'transit', nm / 2, 8.0, 0.0),
                Leg('survey', 'survey', 40.0, 8.0, 90.0),
                Leg('home', 'transit', 20.0, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m, home_marks_km=ONE)
        self.assertEqual([m['phase'] for m in p.marks if m['kind'] == 'range'],
                         ['inbound'])
        self.assertTrue(any('No outbound' in w for w in p.warnings))

    def test_the_default_carries_both_radii_in_and_out(self):
        p = plan(self.legs, self.env, self._v(), self.m)
        rng = [m for m in p.marks if m['kind'] == 'range']
        self.assertEqual(len(rng), 2 * len(HOME_MARKS_KM))
        for km in HOME_MARKS_KM:
            pair = [m for m in rng if m['km_from_home'] == km]
            self.assertEqual(sorted(m['phase'] for m in pair),
                             ['inbound', 'outbound'], msg=f'{km} km')
            for m in pair:
                self.assertAlmostEqual(m['nm_from_home'], km / KM_PER_NM, places=12)

    def test_the_outer_radius_is_passed_later_out_and_earlier_back(self):
        """The order is the physical one: 13 km out, 26 km out, … 26 km in,
        13 km in. Getting that backwards is invisible to a single-radius
        test."""
        p = plan(self.legs, self.env, self._v(), self.m)
        seq = [(m['phase'], m['km_from_home'])
               for m in p.marks if m['kind'] == 'range']
        self.assertEqual(seq, [('outbound', 13.0), ('outbound', 26.0),
                               ('inbound', 26.0), ('inbound', 13.0)])

    # -- units ------------------------------------------------------------- #

    def test_a_waypoint_in_nm_lands_at_that_many_nautical_miles(self):
        """The conversion is a MULTIPLY, and this is the test that says so.

        Written after an inverted factor put a 13 km waypoint at 24.08 NM
        instead of 7.02 — dividing by NM-per-unit instead of multiplying. The
        assertion is against arithmetic done outside the engine, not against
        another of its outputs.
        """
        p = plan(self.legs, self.env, self._v(), self.m, waypoints=(9.0,),
                 waypoint_unit='nm')
        out = next(m for m in p.marks if m['phase'] == 'outbound')
        self.assertAlmostEqual(out['nm_from_home'], 9.0, places=12)
        self.assertAlmostEqual(out['km_from_home'], 9.0 * 1.852, places=12)
        self.assertAlmostEqual(out['elapsed_hours'], 9.0 / 8.0, places=12)

    def test_the_same_distance_in_either_unit_gives_the_same_mark(self):
        """A unit is a display choice. 18.52 km and 10 NM are the same place,
        so every physical quantity about the mark must agree exactly."""
        km = plan(self.legs, self.env, self._v(), self.m,
                  waypoints=(18.52,), waypoint_unit='km')
        nm = plan(self.legs, self.env, self._v(), self.m,
                  waypoints=(10.0,), waypoint_unit='nm')
        for a, b in zip([m for m in km.marks if m['kind'] == 'range'],
                        [m for m in nm.marks if m['kind'] == 'range']):
            self.assertEqual(a['phase'], b['phase'])
            for key in ('nm_from_home', 'km_from_home', 'elapsed_hours',
                        'litres_burned'):
                self.assertAlmostEqual(a[key], b[key], places=9, msg=key)

    def test_switching_units_does_not_move_the_default_waypoints(self):
        """Choosing NM must not silently relocate a mark — the defaults are the
        same radii either way, just spelled differently."""
        km = plan(self.legs, self.env, self._v(), self.m, waypoint_unit='km')
        nm = plan(self.legs, self.env, self._v(), self.m, waypoint_unit='nm')
        self.assertEqual([m['nm_from_home'] for m in km.marks if m['kind'] == 'range'],
                         [m['nm_from_home'] for m in nm.marks if m['kind'] == 'range'])
        self.assertEqual(default_waypoints('km'), WAYPOINTS_KM)

    def test_the_label_and_unit_follow_the_chosen_unit(self):
        km = plan(self.legs, self.env, self._v(), self.m, waypoints=(13.0,),
                  waypoint_unit='km')
        nm = plan(self.legs, self.env, self._v(), self.m, waypoints=(7.0,),
                  waypoint_unit='nm')
        kmm = next(m for m in km.marks if m['phase'] == 'outbound')
        nmm = next(m for m in nm.marks if m['phase'] == 'outbound')
        self.assertEqual(kmm['unit'], 'km')
        self.assertEqual(nmm['unit'], 'NM')
        self.assertIn('13 km from home', kmm['label'])
        self.assertIn('7 NM from home', nmm['label'])
        self.assertNotIn('km', nmm['label'])

    def test_an_unreachable_waypoint_warns_in_the_chosen_unit(self):
        """The refusal path has to speak the user's unit too, or the warning
        names a distance they never typed."""
        legs = [Leg('out', 'transit', 2.0, 8.0, 0.0),
                Leg('survey', 'survey', 40.0, 8.0, 90.0),
                Leg('home', 'transit', 2.0, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m, waypoints=(20.0,),
                 waypoint_unit='nm')
        self.assertTrue(any('20 NM' in w for w in p.warnings), p.warnings)
        self.assertFalse(any(' km' in w for w in p.warnings), p.warnings)

    def test_an_unknown_unit_is_refused(self):
        with self.assertRaises(ValueError) as cm:
            plan(self.legs, self.env, self._v(), self.m, waypoints=(5.0,),
                 waypoint_unit='furlongs')
        self.assertIn('furlongs', str(cm.exception))
        # ... and the acceptance case, so the refusal is not vacuous.
        for unit in ('km', 'nm', 'NM', ' Km '):
            p = plan(self.legs, self.env, self._v(), self.m, waypoints=(5.0,),
                     waypoint_unit=unit)
            self.assertEqual(len([m for m in p.marks if m['kind'] == 'range']), 2)

    def test_the_deprecated_spelling_still_works_and_is_km(self):
        old = plan(self.legs, self.env, self._v(), self.m, home_marks_km=(13.0,))
        new = plan(self.legs, self.env, self._v(), self.m, waypoints=(13.0,),
                   waypoint_unit='km')
        self.assertEqual([m['nm_from_home'] for m in old.marks if m['kind'] == 'range'],
                         [m['nm_from_home'] for m in new.marks if m['kind'] == 'range'])

    def test_passing_both_spellings_is_an_error(self):
        """Silently preferring one would plan marks the caller did not ask for."""
        with self.assertRaises(ValueError) as cm:
            plan(self.legs, self.env, self._v(), self.m, waypoints=(5.0,),
                 home_marks_km=(13.0,))
        self.assertIn('not both', str(cm.exception))

    def test_a_repeated_radius_does_not_double_the_marks(self):
        p = plan(self.legs, self.env, self._v(), self.m,
                 home_marks_km=(13.0, 13.0, 26.0))
        self.assertEqual(len([m for m in p.marks if m['kind'] == 'range']), 4)

    def test_a_bare_number_is_accepted_as_one_radius(self):
        p = plan(self.legs, self.env, self._v(), self.m, home_marks_km=26.0)
        rng = [m for m in p.marks if m['kind'] == 'range']
        self.assertEqual(len(rng), 2)
        self.assertTrue(all(m['km_from_home'] == 26.0 for m in rng))

    def test_one_radius_can_be_reachable_while_the_other_is_not(self):
        """A 10 NM (18.5 km) transit clears 13 km but never reaches 26."""
        legs = [Leg('out', 'transit', 10.0, 8.0, 0.0),
                Leg('survey', 'survey', 40.0, 8.0, 90.0),
                Leg('home', 'transit', 10.0, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m)
        km = sorted(m['km_from_home'] for m in p.marks if m['kind'] == 'range')
        self.assertEqual(km, [13.0, 13.0])
        self.assertTrue(any('No outbound 26 km' in w for w in p.warnings))
        self.assertTrue(any('No inbound 26 km' in w for w in p.warnings))
        self.assertFalse(any('13 km mark' in w for w in p.warnings))

    def test_survey_arrival_mark_sits_at_the_start_of_the_survey(self):
        p = plan(self.legs, self.env, self._v(), self.m, start_time=self.start)
        arr = [m for m in p.marks if m['phase'] == 'survey_arrival']
        self.assertEqual(len(arr), 1)
        arr, survey = arr[0], p.legs[1]
        self.assertEqual(arr['kind'], 'phase')
        self.assertEqual(arr['leg'], survey.name)
        self.assertAlmostEqual(arr['elapsed_hours'], survey.start_hours, places=12)
        self.assertEqual(arr['clock'], survey.start_clock)
        # nothing of the survey leg is burned yet — only the transit before it
        self.assertAlmostEqual(arr['litres_burned'], p.legs[0].litres, places=9)

    def test_survey_arrival_uses_the_first_survey_leg(self):
        legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                Leg('survey A', 'survey', 40.0, 8.0, 90.0),
                Leg('reposition', 'transit', 10.0, 8.0, 45.0),
                Leg('survey B', 'survey', 40.0, 8.0, 90.0),
                Leg('home', 'transit', 20.0, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m)
        arr = next(m for m in p.marks if m['phase'] == 'survey_arrival')
        dep = next(m for m in p.marks if m['phase'] == 'survey_departure')
        self.assertEqual(arr['leg'], 'survey A')
        self.assertEqual(dep['leg'], 'survey B')
        # on task from the first line to the last: the reposition sits inside
        self.assertAlmostEqual(arr['elapsed_hours'], p.legs[1].start_hours, places=12)
        self.assertLess(arr['elapsed_hours'], p.legs[2].start_hours)
        self.assertGreater(dep['elapsed_hours'], p.legs[2].end_hours)

    def test_time_on_task_is_arrival_to_departure(self):
        p = plan(self.legs, self.env, self._v(), self.m)
        arr = next(m for m in p.marks if m['phase'] == 'survey_arrival')
        dep = next(m for m in p.marks if m['phase'] == 'survey_departure')
        self.assertAlmostEqual(dep['elapsed_hours'] - arr['elapsed_hours'],
                               p.legs[1].hours, places=12)
        self.assertAlmostEqual(dep['litres_burned'] - arr['litres_burned'],
                               p.legs[1].litres, places=9)

    def test_survey_departure_mark_sits_at_the_end_of_the_survey(self):
        p = plan(self.legs, self.env, self._v(), self.m, start_time=self.start)
        dep = [m for m in p.marks if m['phase'] == 'survey_departure']
        self.assertEqual(len(dep), 1)
        dep, survey = dep[0], p.legs[1]
        self.assertEqual(dep['kind'], 'phase')
        self.assertEqual(dep['leg'], survey.name)
        self.assertAlmostEqual(dep['elapsed_hours'], survey.end_hours, places=12)
        self.assertEqual(dep['clock'], survey.end_clock)
        # fuel burned by then, against arithmetic done outside the engine
        self.assertAlmostEqual(dep['litres_burned'],
                               p.legs[0].litres + p.legs[1].litres, places=9)

    def test_survey_departure_uses_the_last_survey_leg(self):
        legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                Leg('survey A', 'survey', 40.0, 8.0, 90.0),
                Leg('reposition', 'transit', 10.0, 8.0, 45.0),
                Leg('survey B', 'survey', 40.0, 8.0, 90.0),
                Leg('home', 'transit', 20.0, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m)
        dep = next(m for m in p.marks if m['phase'] == 'survey_departure')
        self.assertEqual(dep['leg'], 'survey B')
        self.assertAlmostEqual(dep['elapsed_hours'], p.legs[3].end_hours, places=12)

    def test_no_survey_leg_means_no_phase_marks_and_no_warning(self):
        """A transit-only plan is a legitimate shape, not a near-miss."""
        legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                Leg('home', 'transit', 20.0, 8.0, 180.0)]
        p = plan(legs, self.env, self._v(), self.m)
        self.assertFalse([m for m in p.marks if m['kind'] == 'phase'])
        self.assertFalse(any('survey' in w.lower() for w in p.warnings))

    def test_marks_are_chronological(self):
        p = plan(self.legs, self.env, self._v(), self.m)
        times = [m['elapsed_hours'] for m in p.marks]
        self.assertEqual(times, sorted(times))
        self.assertEqual([m['phase'] for m in p.marks],
                         ['outbound', 'outbound', 'survey_arrival',
                          'survey_departure', 'inbound', 'inbound'])

    def test_a_bad_start_time_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            plan(self.legs, self.env, self._v(), self.m, start_time='06:30')
        self.assertIn('start_time must be a datetime', str(ctx.exception))

    def test_the_clock_does_not_touch_fuel(self):
        """Adding a start time must not perturb a single burn figure."""
        a = plan(self.legs, self.env, self._v(), self.m)
        b = plan(self.legs, self.env, self._v(), self.m, start_time=self.start)
        self.assertAlmostEqual(a.total_litres, b.total_litres, places=12)
        self.assertAlmostEqual(a.binding_margin_litres, b.binding_margin_litres,
                               places=12)
        self.assertEqual(a.verdict, b.verdict)

    def test_result_survives_json_round_trip(self):
        """to_dict() feeds the HTTP API, so datetimes must not leak into it."""
        import json
        p = plan(self.legs, self.env, self._v(), self.m, start_time=self.start)
        text = json.dumps(p.to_dict(), allow_nan=False)
        back = json.loads(text)
        self.assertEqual(back['start_clock'], '11 Aug 06:30')
        self.assertEqual(len(back['marks']), 6)


class TestSurveyLines(unittest.TestCase):
    """A survey is a lawnmower, not a straight line.

    Two effects the single-distance input could not express, both of which make
    weather cost MORE than the old model said:

      * CONVEXITY. The heading premium averages to zero over a reciprocal pair,
        but fuel is convex in RPM, so the mean of the two RATES exceeds the rate
        at the mean premium. The line into the weather costs more than the
        reciprocal saves.
      * PARITY. An odd number of lines cannot balance at all — one direction
        gets an extra line.
    """

    def setUp(self):
        self.m = Model()
        self.v = Vessel(gondola='em2040')

    def _leg(self, **kw):
        base = dict(name='survey', kind='survey', distance_nm=120.0, speed_kt=8.0,
                    course_deg=0.0)
        base.update(kw)
        return Leg(**base)

    def _env(self, wind=20.0):
        return Environment(wmo_sea_state=2, wind_speed_kt=wind, wind_from_deg=0.0)

    # -- geometry ---------------------------------------------------------- #

    def test_distance_comes_from_the_lines_when_they_are_given(self):
        leg = self._leg(distance_nm=0.0, lines=12, line_length_nm=10.0)
        self.assertAlmostEqual(leg.resolved_distance_nm(), 120.0, places=12)
        r = plan_leg(leg, self._env(), self.m, gondola='em2040')
        self.assertAlmostEqual(r.distance_nm, 120.0, places=12)
        self.assertAlmostEqual(r.hours, 15.0, places=12)

    def test_a_stale_distance_does_not_override_the_lines(self):
        """distance_nm is ignored once lines are given, so a leftover value
        cannot quietly plan a different survey than the one described."""
        leg = self._leg(distance_nm=999.0, lines=4, line_length_nm=10.0)
        self.assertAlmostEqual(leg.resolved_distance_nm(), 40.0, places=12)

    def test_alternate_lines_run_the_reciprocal(self):
        courses = [c for _, c in self._leg(lines=4, line_length_nm=10.0).line_plan()]
        self.assertEqual(courses, [0.0, 180.0, 0.0, 180.0])

    def test_without_a_line_count_a_survey_is_one_reciprocal_pair(self):
        runs = self._leg().line_plan()
        self.assertEqual([c for _, c in runs], [0.0, 180.0])
        self.assertEqual([nm for nm, _ in runs], [60.0, 60.0])

    # -- the physics ------------------------------------------------------- #

    def test_a_linear_fuel_law_shows_no_convexity_penalty(self):
        """The EM712 law is affine in RPM, so a balanced pair costs EXACTLY the
        cancelled-premium figure. This is the control: it proves the correction
        below comes from curvature, not from the loop."""
        r = plan_leg(self._leg(lines=12, line_length_nm=10.0), self._env(20.0),
                     self.m, gondola='em712')
        rpm_b = self.m.rpm_for_speed(8.0, 'em712')
        flat = self.m.fuel_rate_lph(rpm_b * (1 + self.m.sea_state_premium(2)), 'em712')
        self.assertAlmostEqual(r.fuel_rate_lph, flat, places=9)

    def test_the_quadratic_penalty_is_exactly_q2_r_squared_p_squared(self):
        """Independent algebra, not a recomputation of the loop.

        Writing R for the RPM at the sea-state premium and dp for the heading
        swing in RPM, the two lines burn f(R±dp); their mean is
        q0 + q1·R + q2·(R² + dp²), so the excess over f(R) is exactly q2·dp².
        """
        r = plan_leg(self._leg(lines=12, line_length_nm=10.0), self._env(20.0),
                     self.m, gondola='em2040')
        q = self.m.data['gondolas']['options']['em2040']['fuel_vs_rpm']
        rpm_b = self.m.rpm_for_speed(8.0, 'em2040')
        R = rpm_b * (1 + self.m.sea_state_premium(2))
        dp = rpm_b * self.m.heading_premium(0.0, 0.0, 20.0)
        flat = self.m.fuel_rate_lph(R, 'em2040')
        self.assertAlmostEqual(r.fuel_rate_lph, flat + q['q2'] * dp * dp, places=9)
        self.assertGreater(r.fuel_rate_lph, flat, 'convexity must cost, not save')

    def test_no_wind_means_no_penalty_and_no_spread(self):
        r = plan_leg(self._leg(lines=7, line_length_nm=10.0), self._env(0.0),
                     self.m, gondola='em2040')
        self.assertAlmostEqual(r.premium_min, r.premium_max, places=12)
        self.assertAlmostEqual(r.rpm_min, r.rpm_max, places=9)

    def test_even_line_counts_match_the_balanced_pair(self):
        env = self._env(20.0)
        pair = plan_leg(self._leg(), env, self.m, gondola='em2040')
        for n in (2, 4, 12, 40):
            r = plan_leg(self._leg(distance_nm=0.0, lines=n, line_length_nm=120.0 / n),
                         env, self.m, gondola='em2040')
            self.assertAlmostEqual(r.litres, pair.litres, places=9, msg=f'{n} lines')

    def test_an_odd_line_count_costs_more_than_an_even_one(self):
        env = self._env(20.0)
        even = plan_leg(self._leg(distance_nm=0.0, lines=4, line_length_nm=30.0),
                        env, self.m, gondola='em2040')
        odd = plan_leg(self._leg(distance_nm=0.0, lines=3, line_length_nm=40.0),
                       env, self.m, gondola='em2040')
        self.assertGreater(odd.litres, even.litres)
        self.assertAlmostEqual(even.heading_premium, 0.0, places=12)
        # the residual is one line's worth of premium spread over all of them
        self.assertAlmostEqual(odd.heading_premium,
                               self.m.heading_premium(0.0, 0.0, 20.0) / 3.0, places=9)

    def test_the_penalty_grows_with_wind(self):
        flat = self.m.fuel_rate_lph(
            self.m.rpm_for_speed(8.0, 'em2040') * (1 + self.m.sea_state_premium(2)),
            'em2040')
        prev = -1.0
        for wind in (0.0, 10.0, 20.0, 25.0):
            pair = plan_leg(self._leg(), self._env(wind), self.m, gondola='em2040')
            excess = pair.fuel_rate_lph / flat - 1
            self.assertGreater(excess, prev)
            prev = excess
        self.assertGreater(prev, 0.15, '25 kt should cost well over 15%')

    # -- honesty rails ----------------------------------------------------- #

    def test_extrapolation_is_flagged_when_only_some_lines_leave_the_window(self):
        """The mean RPM can sit inside the fitted window while the into-wind
        line sits outside it. Flagging on the mean alone would hide that.

        9 kt in 25 kt of wind is the case: the mean sits near 2540 rpm, well
        inside the 1400-3100 window, while the into-wind lines need over 3280.
        """
        r = plan_leg(self._leg(speed_kt=9.0, lines=12, line_length_nm=10.0),
                     self._env(25.0), self.m, gondola='em2040')
        self.assertTrue(self.m.in_fit_window(r.rpm_required, 'em2040'),
                        'the mean must be inside, or this test proves nothing')
        self.assertFalse(self.m.in_fit_window(r.rpm_max, 'em2040'))
        self.assertTrue(r.extrapolated)
        self.assertTrue(any('of 12 lines' in n for n in r.notes))

    def test_the_extremes_bracket_the_mean(self):
        r = plan_leg(self._leg(lines=5, line_length_nm=10.0), self._env(20.0),
                     self.m, gondola='em2040')
        self.assertLessEqual(r.rpm_min, r.rpm_required)
        self.assertLessEqual(r.rpm_required, r.rpm_max)
        self.assertLessEqual(r.premium_min, r.total_premium)
        self.assertLessEqual(r.total_premium, r.premium_max)

    def test_bad_line_inputs_are_rejected(self):
        cases = [
            (dict(kind='transit', lines=4, line_length_nm=10.0), 'only a survey'),
            (dict(lines=4), 'give a line length'),
            (dict(line_length_nm=10.0), 'give a line count'),
            (dict(lines=0, line_length_nm=10.0), 'whole number'),
            (dict(lines=-2, line_length_nm=10.0), 'whole number'),
        ]
        for kw, fragment in cases:
            errs = self._leg(**kw).validate()
            self.assertTrue(any(fragment in e for e in errs), msg=f'{kw} -> {errs}')

    def test_max_survey_holds_the_line_count_and_scales_the_length(self):
        legs = [Leg('out', 'transit', 20.0, 8.0, 90.0),
                self._leg(distance_nm=0.0, lines=9, line_length_nm=10.0),
                Leg('home', 'transit', 20.0, 8.0, 270.0)]
        env = self._env(15.0)
        nm = max_survey_length(legs, env, self.v, self.m)
        self.assertGreater(nm, 0.0)
        solved = [legs[0],
                  self._leg(distance_nm=0.0, lines=9, line_length_nm=nm / 9),
                  legs[2]]
        p = plan(solved, env, self.v, self.m)
        self.assertEqual(p.verdict, 'ok')
        self.assertEqual(p.legs[1].lines, 9, 'the parity must not drift as it solves')
        self.assertAlmostEqual(p.legs[1].distance_nm, nm, places=6)


class TestMaxSurveyLines(unittest.TestCase):
    """How many lines the fuel allows, when the area will not fit in one run."""

    def setUp(self):
        self.m = Model()
        self.v = Vessel(gondola='em2040')
        self.env = Environment(wmo_sea_state=2, wind_speed_kt=25.0,
                               wind_from_deg=0.0)

    def _legs(self, lines=50, length=10.0):
        return [Leg('out', 'transit', 20.0, 8.0, 90.0),
                Leg('survey', 'survey', 0.0, 8.0, 0.0,
                    lines=lines, line_length_nm=length),
                Leg('home', 'transit', 20.0, 8.0, 270.0)]

    def _plan_with(self, n, length=10.0):
        legs = self._legs(lines=n, length=length)
        return plan(legs, self.env, self.v, self.m)

    def test_the_answer_fits_and_one_more_line_does_not(self):
        """The only property that really matters: it is the LARGEST count that
        still passes."""
        r = max_survey_lines(self._legs(), self.env, self.v, self.m)
        n = r['lines']
        self.assertGreater(n, 0)
        self.assertEqual(self._plan_with(n).verdict, 'ok')
        self.assertNotEqual(self._plan_with(n + 1).verdict, 'ok')

    def test_fuel_is_monotonic_in_the_line_count(self):
        """The search assumes this. If it ever stopped holding, bisection would
        silently return a wrong count rather than fail."""
        prev = -1.0
        for n in range(1, 15):
            litres = self._plan_with(n).total_litres
            self.assertGreater(litres, prev, msg=f'{n} lines')
            prev = litres

    def test_distance_and_shortfall_are_consistent(self):
        r = max_survey_lines(self._legs(lines=50), self.env, self.v, self.m)
        self.assertAlmostEqual(r['distance_nm'],
                               r['lines'] * r['line_length_nm'], places=9)
        self.assertEqual(r['requested_lines'], 50)
        self.assertEqual(r['shortfall_lines'], 50 - r['lines'])
        self.assertFalse(r['completes'])
        self.assertIn('will not fit', r['note'])

    def test_a_survey_that_fits_reports_headroom_not_a_shortfall(self):
        r = max_survey_lines(self._legs(lines=4), self.env, self.v, self.m)
        self.assertTrue(r['completes'])
        self.assertEqual(r['shortfall_lines'], 0)
        self.assertGreater(r['spare_lines'], 0)
        self.assertIn('room for', r['note'])

    def test_more_wind_allows_fewer_lines(self):
        counts = []
        for wind in (0.0, 20.0, 25.0):
            env = Environment(wmo_sea_state=2, wind_speed_kt=wind, wind_from_deg=0.0)
            counts.append(max_survey_lines(self._legs(), env, self.v, self.m)['lines'])
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertLess(counts[-1], counts[0])

    def test_zero_when_not_even_one_line_fits(self):
        legs = [Leg('out', 'transit', 700.0, 8.0, 90.0),
                Leg('survey', 'survey', 0.0, 8.0, 0.0, lines=10,
                    line_length_nm=10.0),
                Leg('home', 'transit', 700.0, 8.0, 270.0)]
        r = max_survey_lines(legs, self.env, self.v, self.m)
        self.assertEqual(r['lines'], 0)
        self.assertFalse(r['completes'])
        self.assertIn('Not even one line', r['note'])

    def test_it_agrees_with_the_continuous_solver(self):
        """The two answer different questions but must not contradict: the line
        count times the line length cannot exceed the continuous maximum, and
        must be within one line of it."""
        legs = self._legs()
        n = max_survey_lines(legs, self.env, self.v, self.m)['lines']
        nm = max_survey_length(legs, self.env, self.v, self.m)
        length = 10.0
        self.assertLessEqual(n * length, nm + 1e-6)
        self.assertGreater((n + 1) * length, nm)

    def test_a_distance_based_survey_is_rejected_with_a_useful_message(self):
        legs = [Leg('out', 'transit', 20.0, 8.0, 90.0),
                Leg('survey', 'survey', 120.0, 8.0, 0.0),
                Leg('home', 'transit', 20.0, 8.0, 270.0)]
        with self.assertRaises(ValueError) as ctx:
            max_survey_lines(legs, self.env, self.v, self.m)
        self.assertIn('max_survey_length', str(ctx.exception))

    def test_parity_decides_the_last_line(self):
        """The case the whole line model exists for, and the one a
        distance-equivalent search gets wrong.

        At 20 kt with 20 NM lines and 20 NM transits the answer is 16. The 17th
        line runs INTO the wind and is the expensive one, so it breaches — even
        though the same 340 NM flown as an even set of lines fits comfortably.
        A solver that scaled the line length while holding an even count would
        answer 17 here, and be wrong by a whole line.
        """
        env = Environment(wmo_sea_state=2, wind_speed_kt=20.0, wind_from_deg=0.0)
        r = max_survey_lines(self._legs(lines=50, length=20.0), env, self.v, self.m)
        self.assertEqual(r['lines'], 16)

        def verdict(n, length=20.0):
            return plan(self._legs(lines=n, length=length), env, self.v,
                        self.m).verdict

        self.assertEqual(verdict(16), 'ok')
        self.assertNotEqual(verdict(17), 'ok')
        # the reason is the DIRECTION of the 17th line, not the distance: the
        # same 340 NM as an even set of lines still fits.
        even = plan(self._legs(lines=34, length=10.0), env, self.v, self.m)
        self.assertAlmostEqual(even.legs[1].distance_nm, 340.0, places=9)
        self.assertEqual(even.verdict, 'ok')

    def test_the_line_length_is_not_changed_by_the_search(self):
        """This solver varies the COUNT; the geometry of a line is fixed by the
        survey area and must come back untouched."""
        r = max_survey_lines(self._legs(length=7.5), self.env, self.v, self.m)
        self.assertAlmostEqual(r['line_length_nm'], 7.5, places=12)


if __name__ == '__main__':
    unittest.main(verbosity=2)
