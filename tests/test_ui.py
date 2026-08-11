"""Leg order: what the keyboard follows, what the eye sees, what gets planned.

Three orders are in play and only two of them agree:

  document order   out -> survey -> home    (the mission; also the TAB ORDER)
  request order    out -> survey -> home    (what buildBody sends)
  visual order     out -> home -> survey    (CSS `order`, Andy 2026-08-11)

The survey block reads below both transits, but the form is still typed and
tabbed in the order the mission is flown, because tab order is document order
and nothing else. That is why the move is a flex `order` and not a tabindex: a
positive tabindex would pull these inputs ahead of every tabindex=0 element on
the page, so tabbing from the top would reach the legs before Environment and
Vessel.

These are static-source assertions, not a browser test — they check what the
files say, not what a browser renders. That is the right trade when the failure
being guarded against is a source edit, and it keeps the suite dependency-free.
The rendered geometry was checked by hand.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UI = Path(__file__).resolve().parent.parent / 'ui'

# The mission is flown in this order, so the form is authored and tabbed in it.
MISSION_ORDER = ['Transit out', 'Survey', 'Transit home']
# ... and read in this one. The difference is CSS, never markup.
VISUAL_ORDER = ['Transit out', 'Transit home', 'Survey']
# The block CSS moves, and the class that does it.
MOVED = 'Survey'
MOVED_CLASS = 'leg-visual-last'


class TestLegOrder(unittest.TestCase):

    def setUp(self):
        self.html = (UI / 'index.html').read_text(encoding='utf-8')
        self.js = (UI / 'app.js').read_text(encoding='utf-8')
        self.css = (UI / 'styles.css').read_text(encoding='utf-8')

    def _legs(self) -> list[tuple[str, str]]:
        """(heading, class attribute) for each leg block, in document order."""
        blocks = re.findall(r'<div class="(leg[^"]*)">(.*?)</div>\s*</div>',
                            self.html, re.DOTALL)
        return [(re.search(r'<h3>(.*?)</h3>', body).group(1).strip(), cls)
                for cls, body in blocks]

    def _request_leg_names(self) -> list[str]:
        body = re.search(r'legs:\s*\[(.*?)\n    \],', self.js, re.DOTALL)
        self.assertIsNotNone(body, 'could not find the legs array in buildBody()')
        return re.findall(r"name:\s*'([^']+)'", body.group(1))

    # -- the two that must agree ------------------------------------------- #

    def test_the_markup_is_in_mission_order_so_the_tab_order_is_too(self):
        """Tab order IS document order. If this drifts, an operator tabs
        through the form in an order the mission is not flown in."""
        self.assertEqual([name for name, _ in self._legs()], MISSION_ORDER)

    def test_the_request_is_built_in_mission_order(self):
        """If this ever equals VISUAL_ORDER, a plan is being sent that surveys
        after coming home."""
        self.assertEqual(self._request_leg_names(), MISSION_ORDER)

    # -- the one that deliberately differs --------------------------------- #

    def test_only_the_moved_block_is_reordered_and_css_is_what_moves_it(self):
        moved = [name for name, cls in self._legs() if MOVED_CLASS in cls.split()]
        self.assertEqual(moved, [MOVED],
                         f'exactly one leg should carry {MOVED_CLASS}')
        # The class has to actually do something, and to a flex parent.
        self.assertRegex(self.css, r'\.legs\s*\{[^}]*display:\s*flex')
        self.assertRegex(self.css, rf'\.{MOVED_CLASS}\s*\{{[^}}]*order:\s*[1-9]')

    def test_no_positive_tabindex_anywhere_in_the_form(self):
        """The reason this is CSS. A positive tabindex would jump these inputs
        ahead of every tabindex=0 element on the page."""
        found = re.findall(r'tabindex\s*=\s*"(-?\d+)"', self.html)
        self.assertTrue(all(int(v) <= 0 for v in found),
                        f'positive tabindex present: {found}')

    def test_the_visual_order_is_the_mission_order_with_the_block_moved_last(self):
        """Guards VISUAL_ORDER itself from drifting into something that is not
        simply MISSION_ORDER with one block relocated."""
        self.assertEqual([n for n in MISSION_ORDER if n != MOVED] + [MOVED],
                         VISUAL_ORDER)
        self.assertNotEqual(VISUAL_ORDER, MISSION_ORDER)

    # -- neither order notices a block that vanished ----------------------- #

    def test_every_form_leg_is_in_the_request(self):
        self.assertEqual(sorted(name for name, _ in self._legs()),
                         sorted(self._request_leg_names()))


if __name__ == '__main__':
    unittest.main()
