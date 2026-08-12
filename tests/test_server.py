"""The HTTP seam: parsing, coercion, and the contracts the wire depends on.

Review (2026-08-12) found this seam completely untested — the None-vs-0
semantics of per-leg weather, the deprecated waypoint spelling, and the
fallback rules lived only in `_parse_request`'s lambdas, guarded by nothing.
Every test here drives the parser functions directly; no sockets involved.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402
from engine import WAYPOINTS_KM, default_waypoints  # noqa: E402


def parse(body):
    return server._parse_request(body)


LEGS = [{'name': 'out', 'kind': 'transit', 'distance_nm': 20, 'speed_kt': 8,
         'course_deg': 0}]


class TestPerLegWeatherParsing(unittest.TestCase):
    """Absent means "use the mission Environment". Zero means zero.

    Coercing a missing value to 0.0 would silently becalm a leg; coercing a
    present zero to None would hand a glassy leg the mission's weather. The
    difference is the whole contract.
    """

    def test_absent_weather_fields_parse_as_none(self):
        legs, *_ = parse({'legs': LEGS})
        leg = legs[0]
        for f in ('wind_speed_kt', 'wind_from_deg', 'current_speed_kt',
                  'current_set_deg', 'wmo_sea_state'):
            self.assertIsNone(getattr(leg, f), f)

    def test_present_zeros_parse_as_zeros_not_none(self):
        body = {'legs': [dict(LEGS[0], wind_speed_kt=0, wind_from_deg=0,
                              current_speed_kt=0, current_set_deg=0,
                              wmo_sea_state=0)]}
        legs, *_ = parse(body)
        leg = legs[0]
        self.assertEqual(leg.wind_speed_kt, 0.0)
        self.assertEqual(leg.wmo_sea_state, 0)
        self.assertIsNotNone(leg.current_set_deg)

    def test_empty_string_means_absent(self):
        """The UI sends '' for a cleared box; that is absent, not zero."""
        body = {'legs': [dict(LEGS[0], wind_speed_kt='')]}
        legs, *_ = parse(body)
        self.assertIsNone(legs[0].wind_speed_kt)


class TestLineCountParsing(unittest.TestCase):

    def test_an_integral_count_becomes_an_int(self):
        body = {'legs': [{'name': 's', 'kind': 'survey', 'distance_nm': 0,
                          'speed_kt': 8, 'lines': 12.0, 'line_length_nm': 10}]}
        legs, *_ = parse(body)
        self.assertEqual(legs[0].lines, 12)
        self.assertIsInstance(legs[0].lines, int)

    def test_a_fractional_count_reaches_the_engines_guard(self):
        """int() used to truncate 12.5 to 12 silently, which both altered the
        survey and made the engine's whole-number rejection unreachable over
        HTTP — found in review. The parser now passes the fraction through so
        Leg.validate can refuse it by name."""
        body = {'legs': [{'name': 's', 'kind': 'survey', 'distance_nm': 0,
                          'speed_kt': 8, 'lines': 12.5, 'line_length_nm': 10}]}
        legs, *_ = parse(body)
        self.assertEqual(legs[0].lines, 12.5)
        self.assertTrue(any('whole number' in e for e in legs[0].validate()))


class TestWaypointParsing(unittest.TestCase):

    def test_absent_and_blank_fall_back_to_the_defaults(self):
        self.assertEqual(server._parse_waypoints(None, 'km'), WAYPOINTS_KM)
        self.assertEqual(server._parse_waypoints('', 'km'), WAYPOINTS_KM)
        self.assertEqual(server._parse_waypoints(None, 'nm'),
                         default_waypoints('nm'))

    def test_an_explicit_empty_list_means_no_waypoints(self):
        """The docstring promised this escape hatch; `... or fallback` had
        quietly closed it, so no HTTP client could ask for a plan without
        range marks — found in review."""
        self.assertEqual(server._parse_waypoints([], 'km'), ())

    def test_both_spellings_at_once_is_refused_not_resolved(self):
        """The engine's rule; the server used to silently prefer `waypoints`
        so the engine never saw the clash."""
        body = {'legs': LEGS, 'waypoints': [5.0], 'home_marks_km': [13.0]}
        with self.assertRaises(ValueError) as cm:
            parse(body)
        self.assertIn('not both', str(cm.exception))

    def test_the_deprecated_spelling_still_works_alone(self):
        *_, waypoints, unit = parse({'legs': LEGS, 'home_marks_km': [13.0]})
        self.assertEqual(waypoints, (13.0,))
        self.assertEqual(unit, 'km')


class TestReserveDefaultComesFromTheModel(unittest.TestCase):

    def test_a_body_without_a_reserve_gets_the_model_floor(self):
        """server.py carried the repo's THIRD hard-coded copy of the reserve
        floor, which the reserve-agreement test never read — found in review.
        It now asks the model, so a policy change cannot strand API callers on
        the old floor."""
        _, _, vessel, *_ = parse({'legs': LEGS})
        want = server.get_model().data['reserve']['default_fraction']
        self.assertEqual(vessel.reserve_fraction, want)

    def test_the_literal_is_gone_from_the_parser(self):
        """The whole of _parse_request, not one line: a first version checked
        only the `reserve_fraction=` line, and a mutant that hardcoded 0.25
        into the `reserve_default =` assignment above it SURVIVED — equivalent
        today because the model's value happens to be 0.25, and exactly the
        drift this exists to prevent the day the policy moves."""
        import inspect
        src = inspect.getsource(server._parse_request)
        self.assertNotIn('0.25', src)
        self.assertIn("get_model().data['reserve']['default_fraction']", src)


class TestBodyHygiene(unittest.TestCase):

    def test_nan_and_infinity_literals_are_refused(self):
        """json.loads ADMITS the non-standard NaN/Infinity literals by
        default, and a NaN speed passes `<= 0` validation (both comparisons
        are False for NaN), poisoning every figure and dying only at response
        serialization — found in review. The parse hook refuses them at the
        door with the token named."""
        for literal in ('NaN', 'Infinity', '-Infinity'):
            with self.assertRaises(ValueError, msg=literal):
                json.loads(f'{{"speed_kt": {literal}}}',
                           parse_constant=server._reject_nonfinite)

    def test_engine_speed_check_is_nan_proof_regardless(self):
        """Belt for direct engine callers: `not (x > 0)` catches NaN where
        `x <= 0` waved it through."""
        from engine import Leg
        errs = Leg('x', 'transit', 10.0, float('nan'), 0.0).validate()
        self.assertTrue(any('speed must be greater than zero' in e for e in errs))


if __name__ == '__main__':
    unittest.main()
