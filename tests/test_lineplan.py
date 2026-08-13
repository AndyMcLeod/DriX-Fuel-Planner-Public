"""Reading line plans, and refusing to guess.

The parsers matter less than the refusals. A line plan that loads cleanly into
the wrong place looks exactly like one that loaded correctly — there is no
error, no warning, just a survey somewhere else — so the tests that carry the
weight here are the ones asserting that an ambiguous file is REJECTED with a
message rather than interpreted.
"""

from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import lineplan  # noqa: E402


def read(text, name='plan.txt', **kw):
    return lineplan.sniff_and_read(text.encode('utf-8'), name, **kw)


class TestAngles(unittest.TestCase):

    def test_decimal_degrees(self):
        self.assertAlmostEqual(lineplan.parse_angle('38.7828'), 38.7828, places=9)
        self.assertAlmostEqual(lineplan.parse_angle('-75.1394'), -75.1394, places=9)

    def test_degrees_minutes_seconds(self):
        self.assertAlmostEqual(lineplan.parse_angle('38 46 58.08 N'), 38.7828, places=6)
        self.assertAlmostEqual(lineplan.parse_angle('075 08 21.84 W'), -75.1394, places=6)

    def test_degrees_and_decimal_minutes(self):
        self.assertAlmostEqual(lineplan.parse_angle("38 46.968 N"), 38.7828, places=6)

    def test_a_hemisphere_letter_beats_a_sign(self):
        """"-75.5 W" is written by someone saying west twice. Reading it as a
        double negative puts the position in China."""
        self.assertAlmostEqual(lineplan.parse_angle('-75.5 W'), -75.5, places=9)
        self.assertAlmostEqual(lineplan.parse_angle('75.5 E'), 75.5, places=9)

    def test_nonsense_is_refused_not_coerced_to_zero(self):
        for bad in ('', 'north', '--3', 'x12'):
            with self.assertRaises(lineplan.LinePlanError, msg=repr(bad)):
                lineplan.parse_angle(bad)


class TestUtm(unittest.TestCase):

    def test_an_offset_from_the_central_meridian_matches_a_hand_calculation(self):
        """Zone 18's central meridian is 75 W. An easting of 406000 is 94 km
        west of it, and at this latitude a degree of longitude is
        111320·cos(lat) metres — so the answer is checkable without trusting
        the series at all."""
        import math
        lat, lon = lineplan.utm_to_geographic(406000.0, 4300000.0, 18, True)
        self.assertAlmostEqual(lat, 38.845, places=2)
        want = -75.0 - 94000.0 / (111320.0 * math.cos(math.radians(lat)))
        self.assertAlmostEqual(lon, want, places=2)

    def test_the_zone_central_meridian_lands_on_the_false_easting(self):
        lat, lon = lineplan.utm_to_geographic(500000.0, 4300000.0, 18, True)
        self.assertAlmostEqual(lon, -75.0, places=6)

    def test_a_zone_that_does_not_exist_is_refused(self):
        for zone in (0, 61, -3):
            with self.assertRaises(lineplan.LinePlanError):
                lineplan.utm_to_geographic(500000.0, 4300000.0, zone)

    def test_projected_is_told_from_geographic_by_range(self):
        self.assertTrue(lineplan.looks_projected(406000.0, 4300000.0))
        self.assertFalse(lineplan.looks_projected(38.8, -75.1))


class TestRefusals(unittest.TestCase):
    """The heart of it."""

    def test_projected_coordinates_without_a_zone_are_refused(self):
        text = 'name,lat,lon\nL1,4300000,406000\nL1,4302000,406000\n'
        with self.assertRaises(lineplan.LinePlanError) as ctx:
            read(text, 'lines.csv')
        self.assertIn('zone', str(ctx.exception).lower())

    def test_the_same_file_reads_once_the_zone_is_given(self):
        text = 'name,lat,lon\nL1,4300000,406000\nL1,4302000,406000\n'
        plan = read(text, 'lines.csv', zone=18)
        self.assertEqual(len(plan.lines), 1)
        lat, lon = plan.lines[0].points[0]
        self.assertAlmostEqual(lat, 38.845, places=2)
        self.assertTrue(-76.5 < lon < -75.0, f'{lon} should be west of the CM')
        self.assertIn('UTM zone 18N', plan.crs)

    def test_a_position_off_the_earth_is_refused(self):
        text = 'name,lat,lon\nL1,938.0,-75.0\nL1,939.0,-75.0\n'
        with self.assertRaises(lineplan.LinePlanError):
            read(text, 'lines.csv')

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(lineplan.LinePlanError):
            read('   ', 'lines.csv')

    def test_xml_that_is_neither_kml_nor_gpx_is_refused(self):
        with self.assertRaises(lineplan.LinePlanError):
            read('<?xml version="1.0"?><root><thing/></root>', 'x.xml')


class TestFormats(unittest.TestCase):

    def test_geojson_linestring(self):
        text = ('{"type":"FeatureCollection","features":[{"type":"Feature",'
                '"properties":{"name":"L1"},"geometry":{"type":"LineString",'
                '"coordinates":[[-75.1,38.8],[-75.0,38.9]]}}]}')
        plan = read(text, 'p.geojson')
        self.assertEqual(plan.source_format, 'GeoJSON')
        self.assertEqual(plan.lines[0].name, 'L1')
        self.assertAlmostEqual(plan.lines[0].points[0][0], 38.8, places=6)

    def test_geojson_puts_longitude_first_and_we_do_not(self):
        """GeoJSON is x,y — longitude then latitude. Reading it as lat,lon
        transposes every position, and around Delaware that lands in
        Antarctica or fails the on-the-earth check."""
        text = ('{"type":"LineString","coordinates":'
                '[[-75.1394,38.7828],[-75.0,38.9]]}')
        plan = read(text, 'p.geojson')
        self.assertAlmostEqual(plan.lines[0].points[0][0], 38.7828, places=6)
        self.assertAlmostEqual(plan.lines[0].points[0][1], -75.1394, places=6)

    def test_kml_placemark(self):
        text = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">'
                '<Document><Placemark><name>Line 1</name><LineString><coordinates>'
                '-75.1,38.8,0 -75.0,38.9,0</coordinates></LineString></Placemark>'
                '</Document></kml>')
        plan = read(text, 'p.kml')
        self.assertEqual(plan.source_format, 'KML')
        self.assertEqual(plan.lines[0].name, 'Line 1')
        self.assertEqual(len(plan.lines[0].points), 2)

    def test_kmz_is_unzipped_in_memory(self):
        import io
        import zipfile
        kml = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2">'
               '<Placemark><LineString><coordinates>-75.1,38.8 -75.0,38.9'
               '</coordinates></LineString></Placemark></kml>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as z:
            z.writestr('doc.kml', kml)
        plan = lineplan.sniff_and_read(buf.getvalue(), 'p.kmz')
        self.assertEqual(plan.source_format, 'KMZ')
        self.assertEqual(len(plan.lines), 1)

    def test_gpx_route(self):
        text = ('<?xml version="1.0"?><gpx version="1.1"><rte><name>R</name>'
                '<rtept lat="38.8" lon="-75.1"/><rtept lat="38.9" lon="-75.0"/>'
                '</rte></gpx>')
        plan = read(text, 'p.gpx')
        self.assertEqual(plan.source_format, 'GPX')
        self.assertEqual(len(plan.lines[0].points), 2)

    def test_hypack_lnw(self):
        text = 'LNS 2\nLIN 2\nPNT -75.10 38.80\nPNT -75.00 38.90\nEOL\n'
        plan = read(text, 'p.lnw')
        self.assertEqual(plan.source_format, 'Hypack LNW')
        self.assertAlmostEqual(plan.lines[0].points[0][0], 38.80, places=6)

    def test_csv_with_both_endpoints_on_one_row(self):
        text = ('name,lat1,lon1,lat2,lon2\n'
                'L1,38.80,-75.10,38.90,-75.00\n'
                'L2,38.81,-75.10,38.91,-75.00\n')
        plan = read(text, 'p.csv')
        self.assertEqual(len(plan.lines), 2)
        self.assertEqual(len(plan.lines[0].points), 2)

    def test_csv_with_a_point_per_row_grouped_by_line_name(self):
        text = ('line,lat,lon\n'
                'A,38.80,-75.10\nA,38.90,-75.00\n'
                'B,38.81,-75.10\nB,38.91,-75.00\n')
        plan = read(text, 'p.csv')
        self.assertEqual(len(plan.lines), 2)
        self.assertEqual([ln.name for ln in plan.lines], ['A', 'B'])

    def test_a_headerless_file_says_it_assumed_the_column_order(self):
        """Positional columns are a guess. It is an unavoidable one, so it is
        REPORTED rather than made quietly."""
        plan = read('38.80 -75.10\n38.90 -75.00\n', 'p.txt')
        self.assertTrue(any('positional' in n for n in plan.notes))

    def test_repeated_vertices_are_dropped(self):
        text = 'line,lat,lon\nA,38.8,-75.1\nA,38.8,-75.1\nA,38.9,-75.0\n'
        plan = read(text, 'p.csv')
        self.assertEqual(len(plan.lines[0].points), 2)


class TestDescribe(unittest.TestCase):

    def test_the_summary_reports_what_an_operator_can_check(self):
        text = ('name,lat1,lon1,lat2,lon2\n'
                'L1,38.800,-75.100,38.850,-75.100\n'
                'L2,38.850,-75.098,38.800,-75.098\n')
        got = lineplan.describe(read(text, 'p.csv'))
        self.assertEqual(got['lines'], 2)
        self.assertEqual(got['points'], 4)
        self.assertGreater(got['total_nm'], 5.0)
        self.assertEqual(got['first_point'], [38.8, -75.1])
        self.assertIsNotNone(got['line_axis_deg'])

    def test_a_lawnmower_reports_its_AXIS_not_the_mean_of_its_bearings(self):
        """Two lines running 000 and 180 are the same axis run both ways. The
        arithmetic mean of those bearings is 090 — square across the lines the
        vessel actually steers, and it was going straight into the survey
        course box until a live import showed 110 for a survey on 020/200."""
        text = ('name,lat1,lon1,lat2,lon2\n'
                'L1,38.800,-75.100,38.850,-75.100\n'      # runs 000
                'L2,38.850,-75.098,38.800,-75.098\n')     # runs 180
        got = lineplan.describe(read(text, 'p.csv'))
        self.assertAlmostEqual(got['line_axis_deg'] % 180.0, 0.0, delta=1.0)
        self.assertNotAlmostEqual(got['line_axis_deg'], 90.0, delta=10.0)
        self.assertAlmostEqual(got['first_bearing_deg'], 0.0, delta=1.0)

    def test_the_summary_names_the_format_and_the_crs(self):
        got = lineplan.describe(read('name,lat,lon\nA,38.8,-75.1\nA,38.9,-75.0\n',
                                     'p.csv'))
        self.assertIn('CSV', got['format'])
        self.assertIn('WGS84', got['crs'])


class TestServerEndpoint(unittest.TestCase):

    def setUp(self):
        import server
        self.server = server

    def call(self, body):
        handler = self.server.Handler.__new__(self.server.Handler)
        captured = {}
        handler._json = lambda code, obj: captured.update(code=code, body=obj)
        handler._error = lambda code, msg: captured.update(code=code,
                                                           body={'error': msg})
        handler.log_message = lambda *a: None
        handler._lineplan(body)
        return captured

    def b64(self, text):
        return base64.b64encode(text.encode('utf-8')).decode('ascii')

    def test_a_good_plan_returns_lines_and_a_summary(self):
        got = self.call({'filename': 'p.csv',
                         'data': self.b64('name,lat1,lon1,lat2,lon2\n'
                                          'L1,38.80,-75.10,38.85,-75.10\n')})
        self.assertEqual(got['code'], 200)
        self.assertEqual(got['body']['summary']['lines'], 1)
        self.assertEqual(len(got['body']['lines'][0]), 2)

    def test_a_projected_plan_without_a_zone_is_a_422_with_advice(self):
        got = self.call({'filename': 'p.csv',
                         'data': self.b64('name,lat,lon\nL1,4300000,406000\n'
                                          'L1,4302000,406000\n')})
        self.assertEqual(got['code'], 422)
        self.assertIn('zone', got['body']['error'].lower())

    def test_bad_base64_is_refused(self):
        self.assertEqual(self.call({'data': 'not base64!!'})['code'], 400)

    def test_a_missing_body_is_refused(self):
        self.assertEqual(self.call({})['code'], 400)


if __name__ == '__main__':
    unittest.main(verbosity=2)
