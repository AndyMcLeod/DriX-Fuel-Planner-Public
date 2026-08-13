"""Surface currents from the NOAA OFS forecast, and how a mission is put on it.

NOTHING HERE TOUCHES THE NETWORK OR THE OPERATOR'S CACHE. Every test builds a
synthetic cycle in a temp directory with a field whose answer is known by hand,
so a failure means the code is wrong rather than that NOAA is down, the tide
turned, or the laptop is at sea. The one thing that cannot be tested this way —
that the numbers match the real ocean — is covered by `currents.py`'s own
`crosscheck` (against the native ROMS grid) and `station` (against CO-OPS
harmonic predictions), which do reach the network and are run by hand.

The conventions pinned here are the ones that fail silently when wrong: a set
is where the water GOES, a missing value is not zero, and a survey leg does not
walk down its first line's course.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import currents as ofs  # noqa: E402

UTC = dt.timezone.utc


def build_cache(directory, frames, *, ny=5, nx=5, lat0=38.0, lon0=-75.0,
                step=0.1, mask=None, tag='dbofs_20260813_t00z',
                start=dt.datetime(2026, 8, 13, 0, 0, tzinfo=UTC)):
    """Write a synthetic cycle. `frames` is a list of (u, v) callables taking
    (iy, ix) and returning metres per second."""
    times = [(start + dt.timedelta(hours=k) - ofs.EPOCH).total_seconds()
             for k in range(len(frames))]
    mask = mask if mask is not None else [1] * (ny * nx)
    meta = {
        'ofs': 'dbofs', 'date': '20260813', 'cycle': '00z',
        'source': 'synthetic://test/', 'product': 'regulargrid', 'depth_m': 0.0,
        'fetched_utc': '2026-08-13T00:05:00Z',
        'ny': ny, 'nx': nx, 'lat0': lat0, 'lon0': lon0,
        'dlat': step, 'dlon': step,
        'files': [f'f{k:03d}' for k in range(1, len(frames) + 1)],
        'times': times, 'mask': mask,
    }
    directory = Path(directory)
    (directory / f'{tag}_meta.json').write_text(json.dumps(meta), encoding='utf8')
    blob = bytearray()
    for ufn, vfn in frames:
        for fn in (ufn, vfn):
            vals = [fn(iy, ix) for iy in range(ny) for ix in range(nx)]
            blob += struct.pack(f'>{len(vals)}f', *vals)
    with gzip.open(directory / f'{tag}_uv.bin.gz', 'wb') as fh:
        fh.write(bytes(blob))
    return tag


def uniform(u, v):
    return (lambda iy, ix: u, lambda iy, ix: v)


class TestConventions(unittest.TestCase):
    """Set is the direction the water flows TOWARD, in degrees true.

    The planner's wind is named for where it comes FROM and its current for
    where it goes TO. Mixing them plans a mission backwards, and the failure is
    invisible in every total — the fuel is simply wrong.
    """

    def test_cardinal_directions(self):
        for u, v, want in ((1, 0, 90), (0, 1, 0), (-1, 0, 270), (0, -1, 180)):
            self.assertAlmostEqual(ofs.uv_to_set(u, v)[1], want, places=9,
                                   msg=f'u={u} v={v}')

    def test_northeast_flow_reads_045(self):
        self.assertAlmostEqual(ofs.uv_to_set(1, 1)[1], 45.0, places=9)

    def test_metres_per_second_convert_to_knots(self):
        self.assertAlmostEqual(ofs.uv_to_set(1, 0)[0], 1.9438444924406, places=9)

    def test_a_set_of_zero_speed_still_returns_a_bearing_not_an_error(self):
        kt, deg, _, _ = ofs.uv_to_set(0.0, 0.0)
        self.assertEqual(kt, 0.0)
        self.assertTrue(0 <= deg < 360)


class TestPositionFormatting(unittest.TestCase):
    """A displayed position carries its hemisphere. `-75.1394 E` is a sign
    waiting to be misread; `075.1394 W` is not, and this whole domain is west.
    """

    def test_west_longitude_reads_w_not_negative_east(self):
        self.assertEqual(ofs.fmt_lon(-75.1394), '075.1394 W')
        self.assertNotIn('-', ofs.fmt_lon(-75.1394))
        self.assertNotIn('E', ofs.fmt_lon(-75.1394))

    def test_east_longitude_reads_e(self):
        self.assertEqual(ofs.fmt_lon(7.5), '007.5000 E')

    def test_longitude_is_padded_to_three_degrees_as_charts_write_it(self):
        self.assertTrue(ofs.fmt_lon(-9.0).startswith('009.'))
        self.assertTrue(ofs.fmt_lon(-175.0).startswith('175.'))

    def test_latitude_takes_n_and_s_and_two_digits(self):
        self.assertEqual(ofs.fmt_lat(38.7828), '38.7828 N')
        self.assertEqual(ofs.fmt_lat(-38.7828), '38.7828 S')
        self.assertEqual(ofs.fmt_lat(7.5), '07.5000 N')

    def test_zero_is_not_shown_as_a_negative_hemisphere(self):
        self.assertTrue(ofs.fmt_lon(0.0).endswith('E'))
        self.assertTrue(ofs.fmt_lat(0.0).endswith('N'))

    def test_a_span_carries_a_hemisphere_on_each_end(self):
        """A box straddling the meridian must not read as one signed range."""
        got = ofs.fmt_span(-0.5, 0.5, 'lon')
        self.assertIn('W', got)
        self.assertIn('E', got)

    def test_the_model_box_reads_as_west(self):
        got = ofs.fmt_span(-75.89, -73.25, 'lon')
        self.assertEqual(got, '075.89 W to 073.25 W')

    def test_the_wire_format_stays_signed(self):
        """Formatting is for humans. The API, the CSVs and the cache metadata
        all stay signed decimal degrees — every consumer parses those."""
        source = (Path(__file__).resolve().parent.parent / 'currents.py').read_text(
            encoding='utf8')
        # the leg rows the API returns carry raw rounded numbers
        self.assertIn("'start': [round(start_lat, 5), round(start_lon, 5)]", source)
        self.assertIn("w.writerow(['lat', 'lon', 'speed_kt'", source)


class CacheCase(unittest.TestCase):
    """Base: a temp cache that is torn down, so no test writes app state."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestInterpolation(CacheCase):

    def test_a_uniform_field_reads_back_exactly(self):
        build_cache(self.cache, [uniform(1.0, 0.0), uniform(1.0, 0.0)])
        cur = ofs.Currents(cache=self.cache)
        kt, deg, u, v = cur.at(38.21, -74.83, dt.datetime(2026, 8, 13, 0, 30, tzinfo=UTC))
        self.assertAlmostEqual(kt, 1.9438444924406, places=6)
        self.assertAlmostEqual(deg, 90.0, places=6)

    def test_bilinear_halfway_between_two_nodes(self):
        # u ramps 0,1,2,... along the x index; halfway between ix 1 and 2 must
        # be 1.5 exactly, computed independently of the engine.
        build_cache(self.cache, [(lambda iy, ix: float(ix), lambda iy, ix: 0.0)] * 2)
        cur = ofs.Currents(cache=self.cache)
        got = cur._at_frame(0, 38.0, -75.0 + 1.5 * 0.1)
        self.assertAlmostEqual(got[0], 1.5, places=5)

    def test_time_interpolation_is_linear_between_frames(self):
        build_cache(self.cache, [uniform(0.0, 0.0), uniform(2.0, 0.0)])
        cur = ofs.Currents(cache=self.cache)
        got = cur.at(38.2, -74.8, dt.datetime(2026, 8, 13, 0, 15, tzinfo=UTC))
        self.assertAlmostEqual(got[2], 0.5, places=5)

    def test_a_time_outside_the_span_raises_rather_than_extrapolating(self):
        build_cache(self.cache, [uniform(1.0, 0.0), uniform(1.0, 0.0)])
        cur = ofs.Currents(cache=self.cache)
        with self.assertRaises(ValueError):
            cur.at(38.2, -74.8, dt.datetime(2026, 8, 13, 9, 0, tzinfo=UTC))

    def test_a_position_outside_the_box_returns_none(self):
        build_cache(self.cache, [uniform(1.0, 0.0), uniform(1.0, 0.0)])
        cur = ofs.Currents(cache=self.cache)
        self.assertIsNone(cur.at(50.0, -74.8, dt.datetime(2026, 8, 13, 0, 30, tzinfo=UTC)))


class TestMissingIsNotZero(CacheCase):
    """A place the forecast cannot see and a place with no current are
    different answers. Only one of them belongs in a plan, and a zero typed
    into the current box is a claim of slack water."""

    def test_an_all_land_neighbourhood_returns_none(self):
        build_cache(self.cache, [uniform(1.0, 0.0)] * 2, mask=[0] * 25)
        cur = ofs.Currents(cache=self.cache)
        self.assertIsNone(cur.at(38.2, -74.8, dt.datetime(2026, 8, 13, 0, 30, tzinfo=UTC)))

    def test_land_nodes_are_dropped_and_the_weights_renormalised(self):
        """One water node in the corner, running 2 m/s; the land around it
        carries a plain zero rather than the fill value.

        The answer must be 2.0 — the water node's own value — not 1.28, which
        is what bilinear weighting returns if the three land corners are
        averaged in. The land HAS to hold a different value from the water for
        this test to mean anything: a uniform field gives 2.0 either way, and
        an earlier version of this test passed against a mutant that averaged
        the bank straight into the channel.
        """
        mask = [0] * 25
        mask[0] = 1
        build_cache(self.cache,
                    [(lambda iy, ix: 2.0 if (iy, ix) == (0, 0) else 0.0,
                      lambda iy, ix: 0.0)] * 2, mask=mask)
        cur = ofs.Currents(cache=self.cache)
        got = cur._at_frame(0, 38.02, -74.98)
        self.assertIsNotNone(got)
        self.assertAlmostEqual(got[0], 2.0, places=5)
        # and the value a bank-averaging version would have produced
        self.assertNotAlmostEqual(got[0], 1.28, places=2)

    def test_the_fill_value_never_reaches_a_result(self):
        build_cache(self.cache, [(lambda iy, ix: -99999.0, lambda iy, ix: -99999.0)] * 2)
        cur = ofs.Currents(cache=self.cache)
        self.assertIsNone(cur.at(38.2, -74.8, dt.datetime(2026, 8, 13, 0, 30, tzinfo=UTC)))


class TestDeadReckoning(unittest.TestCase):
    """Checked against hand-computed positions, not against the function."""

    def test_sixty_miles_north_is_one_degree_of_latitude(self):
        lat, lon = ofs.dead_reckon(38.0, -75.0, 0.0, 60.0)
        self.assertAlmostEqual(lat, 39.0, places=6)
        self.assertAlmostEqual(lon, -75.0, places=6)

    def test_east_at_sixty_north_covers_two_degrees_of_longitude(self):
        lat, lon = ofs.dead_reckon(60.0, 0.0, 90.0, 60.0)
        self.assertAlmostEqual(lat, 60.0, places=6)
        self.assertAlmostEqual(lon, 2.0, places=3)

    def test_south_and_west_go_the_other_way(self):
        lat, _ = ofs.dead_reckon(38.0, -75.0, 180.0, 60.0)
        _, lon = ofs.dead_reckon(0.0, 0.0, 270.0, 60.0)
        self.assertAlmostEqual(lat, 37.0, places=6)
        self.assertAlmostEqual(lon, -1.0, places=6)


class TestResolveLegs(CacheCase):

    def setUp(self):
        super().setUp()
        # 24 hourly frames of a steady 1 kt (0.5144 m/s) setting due east,
        # over a box big enough for a 20 NM transit.
        build_cache(self.cache, [uniform(0.5144447, 0.0)] * 24,
                    ny=40, nx=40, lat0=38.0, lon0=-75.0, step=0.05)
        self.dep = dt.datetime(2026, 8, 13, 1, 0, tzinfo=UTC)

    def legs(self):
        return [
            {'name': 'Transit out', 'kind': 'transit', 'distance_nm': 20,
             'speed_kt': 8, 'course_deg': 90},
            {'name': 'Survey', 'kind': 'survey', 'distance_nm': 0, 'lines': 4,
             'line_length_nm': 5, 'speed_kt': 4, 'course_deg': 0},
            {'name': 'Transit home', 'kind': 'transit', 'distance_nm': 20,
             'speed_kt': 8, 'course_deg': 270},
        ]

    def test_a_steady_field_gives_that_current_on_every_leg(self):
        rows, prov = ofs.resolve_legs(38.5, -74.6, self.dep, self.legs(),
                                      cache=self.cache)
        for row in rows:
            self.assertAlmostEqual(row['current_speed_kt'], 1.0, places=2, msg=row['name'])
            self.assertAlmostEqual(row['current_set_deg'], 90.0, places=1)
        self.assertEqual(prov['cycle'], '20260813 t00z')
        self.assertIn('DBOFS', prov['label'])

    def test_along_track_is_positive_outbound_and_negative_coming_home(self):
        rows, _ = ofs.resolve_legs(38.5, -74.6, self.dep, self.legs(), cache=self.cache)
        self.assertGreater(rows[0]['along_kt'], 0.9)      # east, with the set
        self.assertLess(rows[2]['along_kt'], -0.9)        # west, against it

    def test_a_survey_leg_does_not_walk_down_its_first_line(self):
        """A lawnmower ends about where it began. Advancing the position by the
        survey's total distance would put the run home tens of miles out."""
        rows, _ = ofs.resolve_legs(38.5, -74.6, self.dep, self.legs(), cache=self.cache)
        survey = rows[1]
        self.assertEqual(survey['start'], survey['end'])
        # and the leg after it starts where the survey did
        self.assertEqual(rows[2]['start'], survey['end'])

    def test_a_transit_advances_the_position_by_its_distance(self):
        rows, _ = ofs.resolve_legs(38.5, -74.6, self.dep, self.legs(), cache=self.cache)
        start_lat, start_lon = rows[0]['start']
        end_lat, end_lon = rows[0]['end']
        east_nm = (end_lon - start_lon) * 60 * math.cos(math.radians(start_lat))
        self.assertAlmostEqual(east_nm, 20.0, places=1)
        self.assertAlmostEqual(end_lat, start_lat, places=4)

    def test_a_loiter_delays_everything_after_it(self):
        legs = self.legs()
        legs[0]['loiter_hours'] = 3.0
        rows, _ = ofs.resolve_legs(38.5, -74.6, self.dep, legs, cache=self.cache)
        # the hold is taken at the START, so the leg itself begins 3 h late
        self.assertEqual(rows[0]['start_utc'], '2026-08-13T04:00:00Z')
        self.assertAlmostEqual(rows[0]['hours'], 3 + 20 / 8, places=3)

    def test_a_leg_off_the_grid_reports_no_data_rather_than_slack(self):
        rows, _ = ofs.resolve_legs(10.0, 10.0, self.dep, self.legs(), cache=self.cache)
        for row in rows:
            self.assertNotIn('current_speed_kt', row)
            self.assertIn('note', row)

    def test_a_mission_running_past_the_forecast_is_reported_per_leg(self):
        legs = self.legs()
        legs[1]['lines'] = 200          # 1000 NM of survey at 4 kt: days
        rows, _ = ofs.resolve_legs(38.5, -74.6, self.dep, legs, cache=self.cache)
        self.assertTrue(any('error' in r for r in rows))


class TestCycleSelection(CacheCase):

    def test_a_cycle_that_does_not_span_the_mission_is_not_used(self):
        build_cache(self.cache, [uniform(1.0, 0.0)] * 3)     # 00:00..02:00Z
        start = dt.datetime(2026, 8, 13, 1, 0, tzinfo=UTC)
        self.assertIsNotNone(ofs.covering_cycle(start, start + dt.timedelta(hours=1),
                                                cache=self.cache))
        self.assertIsNone(ofs.covering_cycle(start, start + dt.timedelta(hours=6),
                                             cache=self.cache))

    def test_a_cycle_fetched_for_another_box_is_not_used(self):
        """Without the box check every leg would answer "no water here", which
        reads as a forecast of slack rather than as the wrong file."""
        build_cache(self.cache, [uniform(1.0, 0.0)] * 3,
                    lat0=38.0, lon0=-75.0, ny=5, nx=5, step=0.1)
        start = dt.datetime(2026, 8, 13, 0, 30, tzinfo=UTC)
        end = start + dt.timedelta(hours=1)
        inside = (38.1, -74.9, 38.3, -74.7)
        outside = (40.0, -70.0, 40.2, -69.8)
        self.assertIsNotNone(ofs.covering_cycle(start, end, inside, cache=self.cache))
        self.assertIsNone(ofs.covering_cycle(start, end, outside, cache=self.cache))

    def test_the_mission_box_contains_the_whole_track(self):
        legs = [{'kind': 'transit', 'distance_nm': 60, 'course_deg': 90, 'speed_kt': 8},
                {'kind': 'transit', 'distance_nm': 60, 'course_deg': 0, 'speed_kt': 8}]
        lat0, lon0, lat1, lon1 = ofs.mission_bbox(38.0, -75.0, legs, margin_deg=0.0)
        self.assertLessEqual(lat0, 38.0)
        self.assertGreaterEqual(lat1, 39.0 - 1e-6)      # 60 NM north
        self.assertLessEqual(lon0, -75.0)
        self.assertGreaterEqual(lon1, -75.0 + 1.26)     # 60 NM east at 38N


class TestReportProvenance(unittest.TestCase):
    """A forecast is perishable. The report must name the cycle, or the same
    mission planned off tomorrow's run looks identical on paper."""

    def _plan(self):
        from engine import Environment, Leg, Vessel, plan
        return plan([Leg('Transit out', 'transit', 20, 8)], Environment(), Vessel())

    def test_the_cycle_is_named_when_the_currents_came_from_one(self):
        import mission_report
        text = mission_report.render(self._plan(), generated=dt.datetime(2026, 8, 13),
                                     currents_source='DBOFS 20260813 t00z surface forecast')
        self.assertIn('DBOFS 20260813 t00z', text)

    def test_hand_typed_currents_leave_no_forecast_line(self):
        import mission_report
        text = mission_report.render(self._plan(), generated=dt.datetime(2026, 8, 13))
        self.assertNotIn('Currents:', text)


class TestServerSeam(CacheCase):
    """The endpoint's contract, driven through the handler's own method with a
    fake request object — no sockets, no network, no writes outside the temp
    cache."""

    def setUp(self):
        super().setUp()
        import server
        self.server = server
        build_cache(self.cache, [uniform(0.5144447, 0.0)] * 24,
                    ny=40, nx=40, lat0=38.0, lon0=-75.0, step=0.05)
        self._real_cache = ofs.CACHE
        ofs.CACHE = self.cache

    def tearDown(self):
        ofs.CACHE = self._real_cache
        super().tearDown()

    def call(self, body):
        """Invoke the handler method with the JSON plumbing stubbed out."""
        handler = self.server.Handler.__new__(self.server.Handler)
        captured = {}
        handler._json = lambda code, obj: captured.update(code=code, body=obj)
        handler._error = lambda code, msg: captured.update(code=code,
                                                           body={'error': msg})
        handler.log_message = lambda *a: None
        handler._currents(body)
        return captured

    GOOD = {'lat': 38.5, 'lon': -74.6, 'departure_utc': '2026-08-13T01:00:00Z',
            'legs': [{'name': 'Transit out', 'kind': 'transit', 'distance_nm': 20,
                      'speed_kt': 8, 'course_deg': 90}]}

    def test_a_good_request_returns_per_leg_currents_and_provenance(self):
        got = self.call(dict(self.GOOD))
        self.assertEqual(got['code'], 200)
        self.assertAlmostEqual(got['body']['legs'][0]['current_speed_kt'], 1.0, places=2)
        self.assertIn('DBOFS', got['body']['source']['label'])

    def test_a_missing_position_is_refused_before_any_fetch(self):
        body = dict(self.GOOD)
        body.pop('lat')
        got = self.call(body)
        self.assertEqual(got['code'], 400)

    def test_a_position_off_the_earth_is_refused(self):
        got = self.call(dict(self.GOOD, lat=910))
        self.assertEqual(got['code'], 400)

    def test_a_missing_departure_time_is_refused(self):
        body = dict(self.GOOD)
        body.pop('departure_utc')
        self.assertEqual(self.call(body)['code'], 400)

    def test_offline_with_no_covering_cycle_says_so_instead_of_reaching_out(self):
        """The planner is used at sea. A request that cannot be served must
        fail as a message, never as a hang or a stack trace."""
        got = self.call(dict(self.GOOD, lat=10.0, lon=10.0, offline=True))
        self.assertEqual(got['code'], 503)
        self.assertIn('by hand', got['body']['error'])

    def test_a_mission_longer_than_any_forecast_is_refused(self):
        got = self.call(dict(self.GOOD, legs=[
            {'kind': 'transit', 'distance_nm': 100000, 'speed_kt': 8, 'course_deg': 90}]))
        self.assertEqual(got['code'], 400)

    def test_legs_must_be_objects(self):
        self.assertEqual(self.call(dict(self.GOOD, legs=['nope']))['code'], 400)


class TestGeometryParsing(unittest.TestCase):
    """The wire shape of a trackline and a survey pattern. Every rejection
    here exists because the value would otherwise reach the geometry and turn
    into either nonsense or an unbounded amount of work."""

    def setUp(self):
        import server
        self.server = server

    def test_a_track_needs_two_points_on_the_earth(self):
        self.assertIsNone(self.server._parse_track(None))
        good = self.server._parse_track([[38.7, -75.1], [38.8, -75.0]])
        self.assertEqual(len(good), 2)
        for bad in ([[38.7, -75.1]], [[38.7]], [[91.0, -75.0], [38.8, -75.0]],
                    'nope', [[38.7, -75.1], [38.8, -999.0]]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.server._parse_track(bad)

    def test_an_unbounded_track_is_refused(self):
        """Each point becomes runs and each run a field lookup. This server has
        no auth in front of it."""
        with self.assertRaises(ValueError):
            self.server._parse_track([[38.0, -75.0]] * 5000)

    def test_a_pattern_needs_a_positive_length_and_spacing(self):
        base = {'anchor': [38.85, -75.08], 'bearing_deg': 20,
                'length_nm': 2, 'spacing_nm': 0.1, 'lines': 10}
        self.assertEqual(self.server._parse_pattern(base)['lines'], 10)
        for key in ('length_nm', 'spacing_nm'):
            with self.assertRaises(ValueError, msg=key):
                self.server._parse_pattern(dict(base, **{key: 0}))
        for lines in (0, -1, 99999):
            with self.assertRaises(ValueError, msg=str(lines)):
                self.server._parse_pattern(dict(base, lines=lines))

    def test_the_turn_radius_defaults_from_model_json_not_a_literal(self):
        """It is an assumption with a documented standing; a second copy here
        would be free to drift from the one the documents describe."""
        import json
        from pathlib import Path as _P
        model = json.loads((_P(__file__).resolve().parent.parent / 'model.json')
                           .read_text(encoding='utf8'))
        want = model['turn_model']['radius_nm']
        got = self.server._parse_pattern({'anchor': [38.85, -75.08],
                                          'bearing_deg': 20, 'length_nm': 2,
                                          'spacing_nm': 0.1, 'lines': 4})
        self.assertEqual(got['turn_radius_nm'], want)
        self.assertFalse(model['turn_model']['fitted'],
                         'the turn model is an assumption and must say so')

    def test_an_explicit_radius_overrides_the_default(self):
        got = self.server._parse_pattern({'anchor': [38.85, -75.08],
                                          'bearing_deg': 20, 'length_nm': 2,
                                          'spacing_nm': 0.1, 'lines': 4,
                                          'turn_radius_nm': 0.05})
        self.assertEqual(got['turn_radius_nm'], 0.05)


class TestUiContract(unittest.TestCase):
    """Read the shipped files as text. The UI has no test runner, and the thing
    guarded against is a source edit."""

    ROOT = Path(__file__).resolve().parent.parent

    def setUp(self):
        self.html = (self.ROOT / 'ui' / 'index.html').read_text(encoding='utf8')
        self.js = (self.ROOT / 'ui' / 'app.js').read_text(encoding='utf8')
        self.css = (self.ROOT / 'ui' / 'styles.css').read_text(encoding='utf8')

    def test_the_departure_position_boxes_exist(self):
        self.assertIn('id="originLat"', self.html)
        self.assertIn('id="originLon"', self.html)
        self.assertIn('id="currentsBtn"', self.html)

    def test_the_start_time_is_converted_to_a_utc_instant(self):
        """datetime-local is wall time with no zone. Sending it raw would be
        four hours out in EDT — most of the way from slack to peak flood."""
        self.assertIn('toISOString()', self.js)
        self.assertIn('departure_utc', self.js)

    def test_the_forecast_label_is_dropped_when_a_current_is_typed_over(self):
        self.assertIn("currentsSource = ''", self.js)
        self.assertIn('currents_source', self.js)

    def test_a_leg_with_no_forecast_value_is_not_filled_with_zero(self):
        self.assertIn('row.current_speed_kt === undefined', self.js)

    def test_no_input_is_labelled_degrees_east(self):
        """The boxes take a signed value; labelling one "°E" and typing a
        negative into it is how a position gets read into the wrong ocean."""
        self.assertNotIn('&deg;E</em>', self.html)
        self.assertIn('+E / &minus;W', self.html)

    def test_the_position_is_echoed_back_with_its_hemisphere(self):
        self.assertIn('id="originOut"', self.html)
        self.assertIn("'W' : 'E'", self.js)
        self.assertIn("'S' : 'N'", self.js)

    def test_the_javascript_and_python_formatters_agree(self):
        """Two implementations of one convention drift. This pins the pair on
        the padding widths, which is the part that silently differs."""
        # python: 075.1394 W  /  38.7828 N
        self.assertEqual(ofs.fmt_lon(-75.1394).split()[0], '075.1394')
        self.assertEqual(ofs.fmt_lat(38.7828).split()[0], '38.7828')
        # javascript: padStart to the same widths
        self.assertIn("padStart(8, '0')", self.js)     # longitude, 3 + . + 4
        self.assertIn("padStart(7, '0')", self.js)     # latitude,  2 + . + 4

    # -- reading the forecast: the one state the operator waits through ----- #

    def test_the_reading_state_is_announced_as_busy(self):
        """The fetch is the only thing here that reaches the network, so its
        start gets its own kind rather than sharing the plain note."""
        self.assertIn("setCurrentsNote('Reading the forecast…', 'busy')", self.js)

    def test_the_flash_cannot_get_stuck_on(self):
        """`busy` is toggled on EVERY call, like warn and bad, so the note that
        replaces it clears it — success, failure and a hand edit alike. A
        one-way `add` here would leave a flashing red note on a finished read."""
        self.assertIn("out.classList.toggle('busy', kind === 'busy')", self.js)

    def test_the_reading_state_is_bold_and_red_and_flashes(self):
        self.assertIn('#currentsOut.busy', self.css)
        self.assertIn('@keyframes currents-reading', self.css)
        busy = self.css.split('#currentsOut.busy')[1].split('}')[0]
        self.assertIn('var(--bad)', busy)
        self.assertIn('font-weight: 700', busy)
        self.assertIn('animation: currents-reading', busy)

    def test_the_flash_stays_under_three_a_second(self):
        """THE ONE CHECK HERE THAT GUARDS SOMETHING RATHER THAN RESTATING IT.

        Anything flashing faster than 3 Hz is a seizure risk for photosensitive
        readers (WCAG 2.3.1), and the obvious way to make a warning feel more
        urgent is to speed it up. One on/off cycle is one flash, so the period
        must stay at or above a third of a second."""
        m = re.search(r'animation:\s*currents-reading\s+([\d.]+)s', self.css)
        self.assertIsNotNone(m, 'no currents-reading animation duration found')
        period_s = float(m.group(1))
        self.assertGreaterEqual(
            round(1.0 / period_s, 3), 0.0)          # sanity: a real period
        self.assertLessEqual(
            1.0 / period_s, 3.0,
            f'flashes at {1.0 / period_s:.2f} Hz — over the 3 Hz limit')

    def test_a_reader_who_asked_for_less_motion_keeps_the_colour(self):
        """The pulse is the discriminator against a failed read, but it is
        still motion. Reduced-motion drops the animation only — the bold red
        and the text carry the state on their own."""
        self.assertIn('prefers-reduced-motion: reduce', self.css)
        reduced = self.css.split('prefers-reduced-motion: reduce')[1]
        self.assertIn('#currentsOut.busy', reduced)
        self.assertIn('animation: none', reduced)


if __name__ == '__main__':
    unittest.main(verbosity=2)
