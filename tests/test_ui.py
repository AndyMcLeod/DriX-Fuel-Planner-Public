"""The form's LAYOUT and the request's LEG ORDER are different things.

The survey block sits below both transits in the form (Andy, 2026-08-11), while
the mission it plans is still out -> survey -> home. Nothing enforced that
before: `buildBody()` reads element ids, so document order and mission order
drifted apart silently and either could be "tidied" into agreement by someone
who assumed they were the same thing.

These are static-source assertions rather than a browser test. That is a real
limit — they check what the files say, not what a browser renders — but the
failure they exist to catch is a source edit, and they need no dependencies.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UI = Path(__file__).resolve().parent.parent / 'ui'

# Mission order. The survey is flown BETWEEN the transits; a request in any
# other order is a different mission, not a different layout.
MISSION_ORDER = ['Transit out', 'Survey', 'Transit home']
# Form order, top to bottom. Deliberately not the mission order.
FORM_ORDER = ['Transit out', 'Transit home', 'Survey']


class TestLegOrder(unittest.TestCase):

    def setUp(self):
        self.html = (UI / 'index.html').read_text(encoding='utf-8')
        self.js = (UI / 'app.js').read_text(encoding='utf-8')

    def _leg_headings(self) -> list[str]:
        legs = re.findall(r'<div class="leg">(.*?)</div>\s*</div>',
                          self.html, re.DOTALL)
        return [re.search(r'<h3>(.*?)</h3>', block).group(1).strip()
                for block in legs]

    def _request_leg_names(self) -> list[str]:
        body = re.search(r'legs:\s*\[(.*?)\n    \],', self.js, re.DOTALL)
        self.assertIsNotNone(body, 'could not find the legs array in buildBody()')
        return re.findall(r"name:\s*'([^']+)'", body.group(1))

    def test_the_form_shows_survey_below_both_transits(self):
        self.assertEqual(self._leg_headings(), FORM_ORDER)

    def test_the_request_is_built_in_mission_order_whatever_the_form_shows(self):
        """The one that matters. If this ever equals the form order, a plan is
        being sent that surveys after coming home."""
        self.assertEqual(self._request_leg_names(), MISSION_ORDER)

    def test_the_two_orders_are_known_to_differ(self):
        """Guards the pair above from being 'fixed' into agreement: they are
        different on purpose, and a change that aligns them should have to say
        so here rather than pass quietly."""
        self.assertNotEqual(FORM_ORDER, MISSION_ORDER)
        self.assertEqual(sorted(FORM_ORDER), sorted(MISSION_ORDER))

    def test_every_form_leg_is_in_the_request(self):
        """A block moved out of the form entirely would otherwise be invisible
        here — the layout test would still pass on whatever remained."""
        self.assertEqual(sorted(self._leg_headings()),
                         sorted(self._request_leg_names()))


if __name__ == '__main__':
    unittest.main()
