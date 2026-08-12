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
        """(heading, class attribute) for each leg block, in document order.

        The class pattern anchors on `leg` as a whole word — `leg(?: ...)?` —
        because review found the previous `leg[^"]*` also matched the `.legs`
        CONTAINER, so the first "leg" this returned was the wrapper div and
        Transit out's own class attribute was never inspected. It produced the
        right answer by coincidence, which is worse than being wrong. Pairing
        each class to the NEXT <h3> avoids depending on `</div>` nesting, which
        the old regex truncated mid-grid.
        """
        pairs = []
        for m in re.finditer(r'<div class="(leg(?: [^"]*)?)">', self.html):
            h3 = re.search(r'<h3>(.*?)</h3>', self.html[m.end():])
            self.assertIsNotNone(h3, f'no heading after leg div at {m.start()}')
            pairs.append((h3.group(1).strip(), m.group(1)))
        return pairs

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


class TestMaxSurveyAutofill(unittest.TestCase):
    """The Max-survey button writes its answer into the line-count field.

    Andy, 2026-08-11: generate the count from the button when the line length is
    filled in, and STILL ALLOW MANUAL EDITING afterwards. The second half is the
    part a future change could quietly take away — locking the field would look
    like tidiness — so it is asserted rather than left to good intentions.
    """

    def setUp(self):
        self.html = (UI / 'index.html').read_text(encoding='utf-8')
        self.js = (UI / 'app.js').read_text(encoding='utf-8')

    def _max_survey_body(self) -> str:
        body = re.search(r'async function doMaxSurvey\(\) \{(.*?)\n\}',
                         self.js, re.DOTALL)
        self.assertIsNotNone(body, 'could not find doMaxSurvey()')
        return body.group(1)

    def test_the_line_count_field_stays_editable(self):
        """The explicit requirement. An auto-filled value the operator cannot
        type over would be a worse tool than one that never filled it."""
        tag = re.search(r'<input[^>]*id="surLines"[^>]*>', self.html)
        self.assertIsNotNone(tag, 'surLines input not found')
        self.assertNotRegex(tag.group(0), r'\breadonly\b')
        self.assertNotRegex(tag.group(0), r'\bdisabled\b')

    def test_the_button_writes_the_count_back_and_refreshes_the_readout(self):
        body = self._max_survey_body()
        self.assertRegex(body, r"\$\('surLines'\)\.value\s*=")
        # Survey distance is derived from the count, so it must be recomputed
        # or the form shows a total that no longer matches its own inputs.
        self.assertIn('refreshDerived()', body)

    def test_a_blank_line_count_is_probed_rather_than_rejected(self):
        """A line length with no count is the case the button exists for, and
        the engine rejects that pair outright — so the request carries a probe
        count. Without it the button 422s exactly when it is most wanted.

        This pins the GUARD, not just the assignment. A first version asserted
        only that `leg.lines = 1` appeared somewhere, and survived a mutation to
        `if (false) leg.lines = 1;` — the text was still there while the probe
        was dead. Matching a bare fragment tests the source, not the behaviour.
        """
        body = self._max_survey_body()
        self.assertRegex(body, r'if\s*\(probed\)\s*leg\.lines\s*=\s*1')
        # ...and that `probed` means what its name says: a length with no count.
        self.assertRegex(body, r'probed\s*=\s*haveLength\s*&&\s*!\(leg\.lines\s*>\s*0\)')
        self.assertRegex(body, r'haveLength\s*=.*line_length_nm\s*>\s*0')


class TestQuickStart(unittest.TestCase):
    """The help panel renders QUICKSTART.md, so the document IS the help.

    That removes the drift this repo keeps paying for — but it hands the
    document a constraint: `renderMarkdownSubset()` in app.js understands
    headings, lists, paragraphs, bold and inline code, and nothing else. An
    edit reaching for a table, a link or a fenced block would render as literal
    punctuation in the panel while looking perfect on GitHub, which is exactly
    the kind of defect nobody notices. So the subset is asserted here.
    """

    ROOT = Path(__file__).resolve().parent.parent
    DOC = ROOT / 'QUICKSTART.md'

    def setUp(self):
        self.text = self.DOC.read_text(encoding='utf-8')
        self.lines = self.text.split('\n')

    def test_the_document_exists_and_leads_with_a_title(self):
        self.assertTrue(self.DOC.is_file())
        self.assertTrue(self.lines[0].startswith('# '), self.lines[0])

    def test_it_uses_only_constructs_the_panel_can_render(self):
        banned = {
            '|': 'tables',
            '```': 'fenced code blocks',
            '![': 'images',
            '> ': 'block quotes',
        }
        for n, line in enumerate(self.lines, 1):
            for token, what in banned.items():
                self.assertNotIn(token, line,
                                 f'{what} are not rendered by the help panel '
                                 f'(QUICKSTART.md line {n})')
        self.assertNotRegex(self.text, r'\[[^\]]+\]\([^)]+\)',
                            'links are not rendered by the help panel')

    def test_no_bold_or_code_span_spans_a_line_break(self):
        """The renderer works line by line, so a mark opened on one line and
        closed on the next renders as literal asterisks. Caught exactly this in
        the first draft of the document."""
        for n, line in enumerate(self.lines, 1):
            self.assertEqual(line.count('**') % 2, 0,
                             f'unbalanced bold on QUICKSTART.md line {n}: {line!r}')
            self.assertEqual(line.count('`') % 2, 0,
                             f'unbalanced code span on QUICKSTART.md line {n}: {line!r}')

    def test_the_server_serves_it_from_the_repo_root(self):
        """Served, not copied into ui/ — a copy is a second thing to update."""
        server = (self.ROOT / 'server.py').read_text(encoding='utf-8')
        self.assertIn("'/quickstart.md'", server)
        self.assertIn("ROOT / 'QUICKSTART.md'", server)

    def test_the_panel_drops_the_documents_own_title(self):
        """The dialog header already reads "Quick start"; rendering the file's
        H1 as well put the title on screen twice. Caught by looking at a
        screenshot — every DOM check passed, because both titles were correct
        elements, just one too many."""
        js = (UI / 'app.js').read_text(encoding='utf-8')
        self.assertRegex(js, r"replace\(/\^#\[\^#\\n\]\[\^\\n\]\*\\n/, ''\)")
        # ...and the file still opens with one, for a standalone reader.
        self.assertTrue(self.DOC.read_text(encoding='utf-8').startswith('# '))

    def test_the_ui_has_a_help_button_that_fetches_it(self):
        html = (UI / 'index.html').read_text(encoding='utf-8')
        js = (UI / 'app.js').read_text(encoding='utf-8')
        self.assertIn('id="helpBtn"', html)
        self.assertIn('id="helpPanel"', html)
        self.assertIn("fetch('/quickstart.md')", js)
        # No prose duplicated into the markup — that would be the drift again.
        self.assertNotIn('Quick start</h1>', html)


if __name__ == '__main__':
    unittest.main()
