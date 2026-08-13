"""The mission report: what it says, and where it is allowed to write.

Every plan generates one (Andy, 2026-08-12), so two things have to hold. It
must describe the plan it was handed — never recompute, never contradict — and
the tests must not litter the operator's own `docs/` proving it. Every test
here that writes points `server.REPORT_DIR` at a temp directory and asserts
the real one was untouched.
"""

from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mission_report  # noqa: E402
import server  # noqa: E402
from engine import Environment, Leg, Model, Vessel, plan  # noqa: E402

WHEN = dt.datetime(2026, 8, 12, 14, 30, 5)


def a_plan(**kw):
    legs = [Leg('out', 'transit', 20.0, 8.0, 0.0, **kw),
            Leg('survey', 'survey', 0.0, 8.0, 90.0, lines=6,
                line_length_nm=10.0),
            Leg('home', 'transit', 20.0, 8.0, 180.0)]
    return plan(legs, Environment(wmo_sea_state=2, wind_speed_kt=12.0),
                Vessel(gondola='em2040'), Model())


class TestRender(unittest.TestCase):

    def setUp(self):
        self.p = a_plan()
        self.md = mission_report.render(self.p, generated=WHEN)

    def test_it_leads_with_the_verdict(self):
        self.assertTrue(self.md.startswith('# Mission report — WITHIN RESERVE'))

    def test_the_figures_are_the_plans_own(self):
        """Not recomputed: a report doing its own arithmetic could disagree
        with the plan it claims to describe."""
        self.assertIn(f'{self.p.total_litres:.1f} L', self.md)
        self.assertIn(f'{self.p.total_hours:.2f} h', self.md)
        self.assertIn(f'{self.p.margin_litres:.1f} L', self.md)

    def test_every_leg_and_mark_appears(self):
        for leg in self.p.legs:
            self.assertIn(leg.name, self.md)
        for mark in self.p.marks:
            self.assertIn(mark['label'], self.md)

    def test_warnings_and_leg_notes_survive(self):
        notes = [n for l in self.p.legs for n in l.notes]
        self.assertTrue(notes, 'expected this plan to produce leg notes')
        for n in notes:
            self.assertIn(n[:40], self.md)

    def test_it_carries_the_sensitivity_band(self):
        """The premium is an assumption; a report that quoted one number and
        dropped the band would be quoting the least trustworthy figure alone."""
        self.assertIn('If the sea-state premium is wrong', self.md)
        self.assertEqual(self.md.count('| as planned |'), 1)

    def test_the_extrapolation_legend_appears_only_when_a_leg_is_flagged(self):
        """A legend for a symbol that does not appear teaches the reader to
        skip it — the last thing an extrapolation warning needs."""
        self.assertFalse(any(l.extrapolated for l in self.p.legs))
        self.assertNotIn('⚠ marks a leg', self.md)
        # EM712 at survey speed is outside its fitted window, so it flags.
        legs = [Leg('out', 'transit', 20.0, 8.0, 0.0),
                Leg('survey', 'survey', 60.0, 8.0, 90.0),
                Leg('home', 'transit', 20.0, 8.0, 180.0)]
        flagged = plan(legs, Environment(wmo_sea_state=2),
                       Vessel(gondola='em712'), Model())
        self.assertTrue(any(l.extrapolated for l in flagged.legs))
        md = mission_report.render(flagged, generated=WHEN)
        self.assertIn('⚠ marks a leg', md)
        self.assertIn('⚠ |', md.replace(' ⚠ |', ' ⚠ |'))

    def test_a_hold_is_shown_against_its_leg(self):
        p = a_plan(loiter_hours=1.5)
        md = mission_report.render(p, generated=WHEN)
        self.assertIn('hold', md)
        self.assertIn('1 h 30 min', md)

    def test_the_filename_is_sortable_and_stamped(self):
        name = mission_report.filename(self.p, WHEN)
        self.assertEqual(name, 'mission_20260812-143005.md')

    def test_a_title_is_slugged_into_the_filename(self):
        name = mission_report.filename(self.p, WHEN, 'East bank / line 4')
        self.assertEqual(name, 'mission_20260812-143005_East-bank-line-4.md')
        self.assertNotIn('/', name)

    def test_a_hostile_title_cannot_escape_the_directory(self):
        """A title reaches this from a request body, so it is untrusted."""
        for bad in ('../../etc/passwd', 'C:\\Windows\\system32', '....//..//x'):
            name = mission_report.filename(self.p, WHEN, bad)
            self.assertNotIn('/', name)
            self.assertNotIn('\\', name)
            self.assertNotIn('..', name)


class TestWriting(unittest.TestCase):
    """`server._write_report` — the only thing here that touches a disk."""

    def setUp(self):
        self.p = a_plan()
        self.real = server.REPORT_DIR
        self.tmp = tempfile.TemporaryDirectory()
        server.REPORT_DIR = Path(self.tmp.name)

    def tearDown(self):
        server.REPORT_DIR = self.real
        self.tmp.cleanup()

    def test_it_writes_a_report_and_returns_its_path(self):
        res = server._write_report(self.p, 'km', '')
        self.assertTrue(res['written'])
        self.assertIsNone(res['error'])
        written = list(Path(self.tmp.name).glob('mission_*.md'))
        self.assertEqual(len(written), 1)
        self.assertIn('Mission report', written[0].read_text(encoding='utf-8'))

    def test_two_plans_in_the_same_second_do_not_clobber(self):
        """Two plans are two missions; the earlier one is not scratch."""
        for _ in range(3):
            self.assertTrue(server._write_report(self.p, 'km', '')['written'])
        self.assertEqual(len(list(Path(self.tmp.name).glob('*.md'))), 3)

    def test_reports_can_be_switched_off(self):
        server.REPORT_DIR = None
        res = server._write_report(self.p, 'km', '')
        self.assertFalse(res['written'])
        self.assertIsNone(res['error'])

    def test_a_write_failure_is_reported_not_raised(self):
        """The report is a convenience. A full disk must not cost a plan."""
        server.REPORT_DIR = Path(self.tmp.name) / 'a-file-not-a-dir'
        server.REPORT_DIR.parent.mkdir(parents=True, exist_ok=True)
        (Path(self.tmp.name) / 'a-file-not-a-dir').write_text('x')
        res = server._write_report(self.p, 'km', '')
        self.assertFalse(res['written'])
        self.assertIsNotNone(res['error'])

    def test_the_suite_never_writes_into_the_real_docs_folder(self):
        """The rail this whole class exists for. A harness that ran the real
        handler against the real default would drop files into the operator's
        docs/ every run."""
        real = self.real
        before = sorted(p.name for p in real.glob('*')) if real.exists() else []
        server.REPORT_DIR = Path(self.tmp.name)
        server._write_report(self.p, 'km', '')
        after = sorted(p.name for p in real.glob('*')) if real.exists() else []
        self.assertEqual(before, after)
        self.assertEqual(real, Path(server.ROOT) / 'docs' / 'missions')


class TestReportsAreNotCommitted(unittest.TestCase):

    def test_the_missions_folder_is_gitignored(self):
        root = Path(__file__).resolve().parent.parent
        ignored = (root / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('docs/missions/', ignored)


if __name__ == '__main__':
    unittest.main()
