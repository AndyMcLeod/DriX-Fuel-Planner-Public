'use strict';

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 1) => (Number.isFinite(n) ? n.toFixed(d) : '—');
const pct = (n, d = 1) => (Number.isFinite(n) ? (n * 100).toFixed(d) + '%' : '—');
const signedPct = (n) => (n >= 0 ? '+' : '') + (n * 100).toFixed(1) + '%';
// A mission that overruns the tank has no meaningful "percent remaining" —
// showing -101% reads as a bug. Say dry and put the deficit in the unit line.
const remain = (frac, litres) => (litres < 0 ? 'dry' : pct(frac));

let MODEL = null;

// --------------------------------------------------------------- bootstrap
async function boot() {
  try {
    MODEL = await (await fetch('/api/model')).json();
  } catch (err) {
    $('placeholder').innerHTML =
      '<p>Could not load the model from the server. Is <code>server.py</code> running?</p>';
    return;
  }

  // One sea-state select per leg. Shorter option text than the old single
  // control carried — these sit in a compact row, and the significant wave
  // height is in the quick start rather than repeated three times.
  LEG_PREFIXES.forEach((p) => {
    const sel = $(p + 'Sea');
    MODEL.sea_state_premium.table.forEach((row) => {
      const o = document.createElement('option');
      o.value = String(row.wmo);
      o.textContent = `${row.wmo} — ${row.label}`;
      o.dataset.premium = String(row.premium);
      sel.appendChild(o);
    });
    sel.value = '2';
    sel.addEventListener('change', onSeaChange);
  });

  const gon = $('gondola');
  const gopts = MODEL.gondolas ? MODEL.gondolas.options : {};
  Object.entries(gopts).forEach(([key, g]) => {
    const o = document.createElement('option');
    o.value = key;
    o.textContent = g.label + (g.status === 'derived' ? ' — derived curve' : ' — measured');
    gon.appendChild(o);
  });
  gon.value = (MODEL.gondolas && MODEL.gondolas.default) || 'em712';

  const cap = $('capacityPreset');
  MODEL.capacity_options.options.forEach((opt) => {
    const o = document.createElement('option');
    o.value = String(opt.litres);
    o.textContent = opt.label;
    cap.appendChild(o);
  });
  const custom = document.createElement('option');
  custom.value = 'custom';
  custom.textContent = 'Custom…';
  cap.appendChild(custom);
  cap.value = String(MODEL.capacity_options.options[0].litres);

  $('reserve').value = String(MODEL.reserve.default_fraction * 100);

  cap.addEventListener('change', () => {
    if (cap.value !== 'custom') $('capacity').value = Number(cap.value).toFixed(1);
    refreshDerived();
  });
  ['capacity', 'reserve', 'startLevel', 'surLines', 'surRange'].forEach((id) =>
    $(id).addEventListener('input', refreshDerived));
  ['outCourse', 'surCourse', 'homeCourse'].concat(
    LEG_PREFIXES.flatMap((p) => [p + 'WindKt', p + 'WindFrom', p + 'CurKt', p + 'CurSet'])
  ).forEach((id) => $(id).addEventListener('input', drawRose));

  $('waypointUnit').addEventListener('change', onWaypointUnitChange);
  wireLoiter();

  $('helpBtn').addEventListener('click', openHelp);
  $('helpClose').addEventListener('click', closeHelp);
  // Click-outside and Escape both close it: a modal you can only leave by
  // finding one small button is a modal that gets left open.
  $('helpPanel').addEventListener('click', (e) => {
    if (e.target === $('helpPanel')) closeHelp();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !$('helpPanel').hidden) closeHelp();
  });

  $('planForm').addEventListener('submit', (e) => { e.preventDefault(); doPlan(); });
  $('maxBtn').addEventListener('click', doMaxSurvey);
  $('currentsBtn').addEventListener('click', doCurrents);
  $('planImportBtn').addEventListener('click', importLinePlan);
  $('clearGeomBtn').addEventListener('click', clearGeometry);
  ['patLat', 'patLon', 'patBearing', 'patSpacing', 'surLines', 'surRange']
    .forEach((id) => $(id).addEventListener('input', refreshPattern));
  ['originLat', 'originLon'].forEach((id) =>
    $(id).addEventListener('input', refreshOrigin));
  // Typing in a current box makes the forecast label a lie, so it is dropped
  // the moment one is touched. The report must never name a cycle for numbers
  // an operator overrode by hand.
  LEG_PREFIXES.flatMap((p) => [p + 'CurKt', p + 'CurSet']).forEach((id) =>
    $(id).addEventListener('input', () => {
      if (!currentsSource) return;
      currentsSource = '';
      setCurrentsNote('Edited by hand — the forecast label has been dropped.');
    }));

  onSeaChange();
  refreshDerived();
  refreshHolding();
  refreshOrigin();
  refreshPattern();
  drawRose();
  applyTips();
}

// --------------------------------------------------------------- hover tips
// Every control carries an explanation, shown in ONE floating layer anchored to
// the control rather than to the pointer.
//
// Ported from the ASV console's `#uiTip` (its `1035ef7`), for the reason that
// mechanism exists: a native `title` tooltip is drawn by the BROWSER, which on
// this platform parks it under the cursor — so the pointer sits on top of the
// text it just asked for, and the page cannot move it. On hover the title is
// STASHED off the element, which suppresses the native tip, and the text is
// drawn below the control's left edge instead.
//
// Two deliberate differences from the sibling:
//
//   * the text lives HERE, in TIPS, not as `title=` in the markup. The sibling
//     already had 118 hand-written titles and only needed re-positioning; this
//     page had none, so the copy is new — and one reviewable block beats it
//     scattered across 460 lines of form markup. `applyTips` writes it onto the
//     elements at boot, so from that moment the DOM looks exactly like the
//     sibling's and the layer below is the same code.
//   * FOCUS shows a tip too, not just hover. This page is a form — 39 inputs
//     and 11 selects — and a keyboard operator tabbing through it would
//     otherwise be the only one who never sees an explanation.
//
// A tip NEVER overwrites a title already on the element: `planTarget` and the
// loiter rows carry aria-labels and hand-set titles that say something the map
// does not know.
const TIPS = {
  // -- vessel -------------------------------------------------------------- //
  '#gondola':
    'Which gondola the hull carries. The EM2040 curve is measured from six days '
    + 'of flow-meter logs and is valid 1400–3100 rpm; the EM712 is the 2024 '
    + 'trials configuration and is extrapolated above 2500 rpm.',
  '#capacityPreset':
    'What 100 indicated gauge points are worth in litres. These are NOT tank '
    + 'volumes: the drawings put the physical tank at 250 L, while three '
    + 'independent measurements put the gauge span nearer 206 L.',
  '#capacity':
    'Litres behind a full gauge. Typing here switches the preset to Custom.',
  '#startLevel':
    'Gauge reading at departure, as an indicated percentage rather than a '
    + 'number of litres.',
  '#reserve':
    'Return-to-port floor, and a needle position rather than a volume. It is a '
    + 'floor, not a target — every plan is judged against it.',
  '#usableOut':
    'Fuel available between the start level and the reserve floor, integrated '
    + 'over the gauge profile rather than taken as a flat share of capacity.',

  // -- mission clock ------------------------------------------------------- //
  '#startTime':
    'Optional. Read in YOUR local time zone and converted to UTC before it '
    + 'reaches a forecast. Without it the plan still runs, on elapsed hours.',
  '#waypoints':
    'Distances from home to call out along the track, comma separated.',
  '#waypointUnit':
    'A display choice only. Switching it converts the numbers and never moves a '
    + 'waypoint along the track.',
  '#originLat':
    'Departure latitude in signed decimal degrees, + north. Needed only for the '
    + 'forecast currents.',
  '#originLon':
    'Departure longitude in signed decimal degrees, + east — so Delaware Bay is '
    + 'negative. Needed only for the forecast currents.',
  '#originOut':
    'The typed position echoed back with its hemisphere, so a mistyped sign is '
    + 'visible here rather than merely present in the plan.',
  '#currentsBtn':
    'Fill the per-leg current boxes from the NOAA Delaware Bay forecast, '
    + 'dead-reckoning the mission from this position and start time. The one '
    + 'thing in this planner that needs the network.',
  '#currentsOut':
    'Which forecast cycle filled the boxes and how much of the mission it '
    + 'answered for. Bold and blinking means the read is still running; a red '
    + 'note sitting still means it failed.',

  // -- geometry ------------------------------------------------------------ //
  '#planFile':
    'CSV, GeoJSON, KML/KMZ, GPX or Hypack LNW. Geographic degrees are read '
    + 'either decimal or degrees-minutes-seconds.',
  '#planZone':
    'UTM zone for eastings and northings, WGS84 only. Never guessed — the wrong '
    + 'zone puts the survey hundreds of miles away with nothing on screen to '
    + 'show for it. Delaware Bay is 18.',
  '#planTarget':
    'Which leg the imported lines become. Importing a multi-line file to a '
    + 'transit is refused rather than flying the first of them.',
  '#planHemi':
    'Hemisphere for UTM northings.',
  '#planImportBtn':
    'Read the file and fill the geometry below. Check the summary against what '
    + 'you actually drew before planning on it.',
  '#outTrack':
    'One waypoint per line, latitude then longitude. Leave empty to plan the '
    + 'leg on its range and course alone.',
  '#homeTrack':
    'One waypoint per line. Leave empty to reverse the outbound track.',
  '#patLat':
    'Latitude of the START of the first survey line.',
  '#patLon':
    'Longitude of the START of the first survey line.',
  '#patBearing':
    'Bearing of line 1 in degrees true. Alternate lines run the reciprocal, '
    + 'each starting where the last finished.',
  '#patSpacing':
    'Distance between adjacent survey lines, in METRES; it reaches the planner '
    + 'as nautical miles. The turn between lines is costed from it — below '
    + 'twice the modelled turn radius (50.0 m) a 180° will not fit, and the '
    + 'boat runs out past the line end and back, which costs more. Spacing '
    + 'makes no difference to the plan above that.',
  '#patOut':
    'The pattern this anchor produces, using the line count and length from the '
    + 'survey leg below — there is one place to change them.',
  '#useField':
    'Sample the forecast at every run — every survey line, every transit '
    + 'segment — instead of taking one current per leg. Needs geometry and a '
    + 'start time, and the network once to cache the cycle.',
  '#clearGeomBtn':
    'Drop all geometry. The plan falls back to plain ranges and courses, which '
    + 'is exactly how it behaves with none of this filled in.',

  // -- legs ---------------------------------------------------------------- //
  '#outRange, #homeRange':
    'Ground distance for this transit.',
  '#outSpeed, #homeSpeed, #surSpeed':
    'Speed over the ground. The through-water speed the engine actually has to '
    + 'make is derived from this and the current.',
  '#outCourse, #homeCourse':
    'Course made good, degrees true. This is what decides whether the wind is '
    + 'on the bow or astern.',
  '#surLines':
    'How many survey lines. An ODD count cannot balance the wind premium even '
    + 'in principle — one direction gets an extra line.',
  '#surRange':
    'Length of a single survey line, not the total.',
  '#surCourse':
    'Bearing of line 1, degrees true. Alternate lines run the reciprocal.',
  '#surTotal':
    'Lines × line length — the ground the survey covers.',
  '#outSea, #surSea, #homeSea':
    'WMO sea state for this leg. The premium it applies is an ASSUMPTION, not a '
    + 'fitted value: only its calm end has been measured, so treat it as a dial '
    + 'to turn rather than a number to trust.',
  '#outWindKt, #surWindKt, #homeWindKt':
    'Wind speed on this leg. The premium scales with the SQUARE of it against a '
    + '12 kt reference, so the high end runs away fast.',
  '#outWindFrom, #surWindFrom, #homeWindFrom':
    'The direction the wind blows FROM, as at sea. Dead on the bow costs the '
    + 'full premium and a following wind gives it back — but not evenly, '
    + 'because fuel is convex in RPM.',
  '#outCurKt, #surCurKt, #homeCurKt':
    'Drift in knots. Applied kinematically, so it moves the FUEL and never the '
    + 'clock. It partly double-counts against the speed law, which was fitted '
    + 'in an unrecorded tide.',
  '#outCurSet, #surCurSet, #homeCurSet':
    'The direction the current flows TOWARD — the opposite convention to wind, '
    + 'as at sea. Getting these the same way round is the classic way to plan a '
    + 'mission backwards.',
  '#outLoiter, #surLoiter, #homeLoiter':
    'Time held on station making no way, charged at the measured 0.95 L/h idle '
    + 'burn. Taken at the START of the leg, so a hold on the way home arrives '
    + 'home late.',
  '#outLoiterUnit, #surLoiterUnit, #homeLoiterUnit':
    'Whether the loiter box above is read as minutes or hours.',
  '[data-loiter-add]':
    'Add this much to the leg’s loiter hold.',
  '[data-loiter-clear]':
    'Clear this leg’s loiter hold.',

  // -- actions ------------------------------------------------------------- //
  '#planForm button[type="submit"]':
    'Run the mission and report fuel, endurance and margin over the reserve.',
  '#maxBtn':
    'Solve for the longest survey that still returns at or above the floor — '
    + 'against whichever floor binds first, capacity or needle, so the answer '
    + 'is never a distance the planner then flags red.',
  '#helpBtn':
    'Open the quick start. It renders the repo’s own QUICKSTART.md, so the '
    + 'document and the help cannot drift apart.',
  '#helpClose':
    'Close the quick start. Clicking outside it or pressing Escape does the '
    + 'same.',

  // -- results ------------------------------------------------------------- //
  '#legTable th:nth-child(4)':
    'Engine speed this leg needs, after the sea-state and heading premiums.',
  '#legTable th:nth-child(5)':
    'Combined sea-state and heading premium on required RPM.',
  '#legTable th:nth-child(8)':
    'Gauge reading at the end of this leg.',
  '#legTable th:nth-child(10)':
    'Nautical miles per litre on this leg — the efficiency the conditions '
    + 'actually allowed, not the flat-water figure.',
  '#marksTable th:nth-child(6)':
    'Indicated gauge percentage at this mark, which is what the operator will '
    + 'actually see.',
  '#sensTable th:nth-child(1)':
    'How far the sea-state premium is shifted from the table value.',
  '#legNotes':
    'Per-leg notes, including any leg whose required RPM sits outside the '
    + 'window the fuel law was fitted over.',
  '#maxOut':
    'The solved maximum survey, and whether the mission as entered fits inside '
    + 'it.',
  '#reportOut':
    'Where this plan’s Markdown report was written.',
};

/** Write the copy onto the controls, once, at boot.
 *
 *  Never clobbers a title already there, and never invents one for a selector
 *  that matches nothing — a tip pointing at a control that has been renamed is
 *  a silent no-op, which is what the coverage test exists to catch. */
function applyTips() {
  let applied = 0;
  Object.entries(TIPS).forEach(([sel, text]) => {
    document.querySelectorAll(sel).forEach((el) => {
      if (!el.getAttribute('title')) { el.setAttribute('title', text); applied += 1; }
    });
  });
  return applied;
}

const TIP_DELAY_MS = 400;
const TIP_GAP = 8;
const TIP_MARGIN = 4;

/** Where the tip goes: below the control's left edge, flipped above when the
 *  bottom would clip, clamped into the viewport either way. Pure, so it is
 *  testable without a layout. */
function tipPos(r, tw, th, ww, wh) {
  const left = Math.max(TIP_MARGIN, Math.min(ww - tw - TIP_MARGIN, r.left));
  let top = r.bottom + TIP_GAP;
  if (top + th > wh - TIP_MARGIN) top = r.top - TIP_GAP - th;   // flip above
  if (top < TIP_MARGIN) top = Math.min(wh - th - TIP_MARGIN, r.bottom + TIP_GAP);
  return { left, top };
}

let tipTimer = null;
let tipEl = null;
let tipStash = '';

function hideTip() {
  clearTimeout(tipTimer);
  tipTimer = null;
  const layer = $('uiTip');
  if (layer) layer.style.display = 'none';
  // Restore the title ONLY if it is still absent. #currentsOut, #maxOut and the
  // readouts are rewritten at runtime, and a value written mid-hover has to win
  // over the stash — the sibling learned this with five such readouts.
  if (tipEl) {
    if (!tipEl.getAttribute('title')) tipEl.setAttribute('title', tipStash);
    tipEl = null;
    tipStash = '';
  }
}

function showTip(el) {
  const text = el.getAttribute('title');
  if (!text) return;
  tipEl = el;
  tipStash = text;
  el.removeAttribute('title');            // suppress the browser's own tip
  tipTimer = setTimeout(() => {
    const layer = $('uiTip');
    if (!layer) return;
    layer.textContent = text;
    layer.style.display = 'block';
    layer.style.left = '0px';
    layer.style.top = '-9999px';          // measure before placing
    const p = tipPos(el.getBoundingClientRect(), layer.offsetWidth,
                     layer.offsetHeight, window.innerWidth, window.innerHeight);
    layer.style.left = `${p.left}px`;
    layer.style.top = `${p.top}px`;
  }, TIP_DELAY_MS);
}

document.addEventListener('mouseover', (e) => {
  if (tipEl && tipEl.contains(e.target)) return;     // still on the same control
  const el = e.target.closest ? e.target.closest('[title]') : null;
  hideTip();
  if (el) showTip(el);
});
document.addEventListener('mouseout', (e) => {
  if (tipEl && !tipEl.contains(e.relatedTarget)) hideTip();
});
// Keyboard: tabbing onto a control shows its tip, leaving hides it. focusin
// bubbles where focus does not, so one listener covers the whole form.
document.addEventListener('focusin', (e) => {
  const el = e.target.closest ? e.target.closest('[title]') : null;
  hideTip();
  if (el) showTip(el);
});
document.addEventListener('focusout', hideTip);
// A click means acting, not reading; Escape dismisses without moving the mouse.
document.addEventListener('mousedown', hideTip, true);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') hideTip(); });
window.addEventListener('blur', hideTip);

const KM_PER_NM = 1.852;
// What the waypoint box is currently written in. Needed because the <select>
// already holds the NEW unit by the time the change event fires, and the
// conversion needs the old one.
let waypointUnit = 'km';
// The waypoints in NM, authoritative, and the exact string last written to the
// box. Converting the DISPLAYED text on every toggle loses precision to the
// rounding each time — 13 km came back as 12.999 — so the unrounded values are
// kept here and re-rendered instead. `lastRendered` is how an operator's own
// edit is told apart from our own writing.
let waypointsNm = null;
let lastRendered = null;

function onWaypointUnitChange() {
  const next = $('waypointUnit').value;
  if (next === waypointUnit) return;
  // Switching units is a DISPLAY choice and must not move a waypoint: the
  // values are converted so they stay in the same place on the track. Leaving
  // the numbers alone would quietly push a 13 km callout out to 13 NM — very
  // nearly twice as far.
  if (waypointsNm === null || $('waypoints').value !== lastRendered) {
    const perUnit = waypointUnit === 'km' ? 1 / KM_PER_NM : 1;
    waypointsNm = ($('waypoints').value.match(/[\d.]+/g) || []).map((v) => Number(v) * perUnit);
  }
  waypointUnit = next;
  renderWaypoints();
}

function renderWaypoints() {
  const box = $('waypoints');
  const perNm = waypointUnit === 'km' ? KM_PER_NM : 1;
  if (waypointsNm && waypointsNm.length) {
    lastRendered = waypointsNm
      .map((nm) => String(Number((nm * perNm).toFixed(3))))
      .join(', ');
    box.value = lastRendered;
  }
  box.placeholder = waypointUnit === 'km' ? '13, 26' : '7.019, 14.039';
}

function onSeaChange() {
  // Each leg shows the premium its OWN sea state implies, beside the select,
  // so the cost of building weather on the survey is visible before planning.
  LEG_PREFIXES.forEach((p) => {
    const opt = $(p + 'Sea').selectedOptions[0];
    $(p + 'SeaPrem').textContent = opt ? signedPct(Number(opt.dataset.premium)) + ' RPM' : '';
  });
  refreshDerived();
}

// --------------------------------------------------------------------- loiter
// Delays imposed on a leg: launch and recovery hold-ups, traffic, a sensor
// problem. Entered in minutes or hours, always SENT in hours, and charged at
// the gondola's idle burn by the engine.
const LOITER_LEGS = ['out', 'sur', 'home'];

function loiterHours(prefix) {
  const v = Number($(prefix + 'Loiter').value) || 0;
  if (v <= 0) return 0;
  return $(prefix + 'LoiterUnit').value === 'h' ? v : v / 60;
}

/** Add minutes to a field, in whatever unit it is currently showing. */
function addLoiterMinutes(id, minutes) {
  const box = $(id);
  const unit = $(id + 'Unit').value;
  const current = Number(box.value) || 0;
  const next = unit === 'h' ? current + minutes / 60 : current + minutes;
  // Trim float noise from the hours case: 0.25 + 0.25 must read 0.5, not
  // 0.7500000000000001 on the third press.
  box.value = String(Number(next.toFixed(4)));
  refreshHolding();
  drawRose();
}

/** Mark legs that are holding, so a delay typed in and forgotten is visible. */
function refreshHolding() {
  LOITER_LEGS.forEach((p) => {
    const leg = $(p + 'Loiter').closest('.leg');
    if (leg) leg.classList.toggle('holding', loiterHours(p) > 0);
  });
}

function wireLoiter() {
  document.querySelectorAll('[data-loiter-add]').forEach((b) =>
    b.addEventListener('click', () =>
      addLoiterMinutes(b.dataset.loiterAdd, Number(b.dataset.addMin))));
  document.querySelectorAll('[data-loiter-clear]').forEach((b) =>
    b.addEventListener('click', () => {
      $(b.dataset.loiterClear).value = '0';
      refreshHolding();
      drawRose();
    }));
  LOITER_LEGS.forEach((p) => {
    $(p + 'Loiter').addEventListener('input', () => { refreshHolding(); drawRose(); });
    // Changing the unit CONVERTS what is typed, exactly as the waypoint unit
    // does: 90 min is 1.5 h, and a unit switch must not silently redefine a
    // delay as 24x longer.
    const sel = $(p + 'LoiterUnit');
    sel.addEventListener('change', () => {
      const box = $(p + 'Loiter');
      const v = Number(box.value) || 0;
      if (v > 0) {
        const toHours = sel.value === 'h';
        box.value = String(Number((toHours ? v / 60 : v * 60).toFixed(4)));
      }
      refreshHolding();
      drawRose();
    });
  });
}

const surveyDistance = () =>
  (Number($('surLines').value) || 0) * (Number($('surRange').value) || 0);

function refreshDerived() {
  const sd = surveyDistance();
  $('surTotal').textContent = sd > 0 ? `${sd.toFixed(1)} NM` : '—';
  const cap = Number($('capacity').value) || 0;
  const start = (Number($('startLevel').value) || 0) / 100;
  const res = (Number($('reserve').value) || 0) / 100;
  const usable = cap * start - cap * res;
  $('usableOut').textContent = usable > 0 ? `${usable.toFixed(1)} L` : '—';
}

// ------------------------------------------------------------------ request
function buildBody() {
  return {
    // Every environmental field is per leg now. What is sent here is only the
    // fallback for a caller that omits them; the UI never does.
    environment: {},
    vessel: {
      capacity_l: Number($('capacity').value),
      reserve_fraction: Number($('reserve').value) / 100,
      start_level_fraction: Number($('startLevel').value) / 100,
      gondola: $('gondola').value,
    },
    legs: [
      { name: 'Transit out', kind: 'transit', distance_nm: Number($('outRange').value),
        speed_kt: Number($('outSpeed').value), course_deg: Number($('outCourse').value),
        loiter_hours: loiterHours('out'), ...legWeather('out'),
        track: parseTrack($('outTrack').value) },
      { name: 'Survey', kind: 'survey',
        // distance is derived server-side from lines x line length
        distance_nm: 0,
        lines: Number($('surLines').value),
        line_length_nm: Number($('surRange').value),
        speed_kt: Number($('surSpeed').value), course_deg: Number($('surCourse').value),
        loiter_hours: loiterHours('sur'), ...legWeather('sur'),
        // Imported lines WIN over a generated pattern: they are what the
        // surveyor actually drew, and a pattern is only a way of describing
        // lines when you have none.
        survey_lines: importedLines,
        pattern: importedLines ? null : surveyPattern() },
      { name: 'Transit home', kind: 'transit', distance_nm: Number($('homeRange').value),
        speed_kt: Number($('homeSpeed').value), course_deg: Number($('homeCourse').value),
        loiter_hours: loiterHours('home'), ...legWeather('home'),
        track: homeTrack() },
    ],
    // Blank start time is sent as null: elapsed hours only, no clock.
    start_time: $('startTime').value || null,
    // Comma- or space-separated distances, e.g. "13, 26", in the selected
    // unit. A BLANK box is sent as null — "give me the defaults" — never as an
    // empty list: on the wire [] means "no waypoints at all", and sending it
    // for a cleared box would silence the standard callouts instead of
    // restoring them.
    waypoints: (() => {
      const v = ($('waypoints').value.match(/[\d.]+/g) || []).map(Number);
      return v.length ? v : null;
    })(),
    waypoint_unit: $('waypointUnit').value,
    // Names the forecast the current boxes were filled from, so the mission
    // report records WHICH cycle rather than presenting a perishable input as
    // a constant. Cleared the moment a current box is edited by hand.
    currents_source: currentsSource,
    // Sample the forecast along the geometry instead of using one current per
    // leg. Ignored server-side unless a leg actually carries geometry.
    use_forecast_currents: $('useField').checked,
    departure_utc: departureUtc(),
  };
}

// -------------------------------------------------------------------- geometry
// All optional. With none of it filled in, buildBody sends no track, no
// pattern and no survey lines, and the planner works on distances and courses
// exactly as it always did.
//
// Imported lines are held HERE rather than written into a textarea: a real
// plan is hundreds of vertices, and round-tripping those through a text box
// would lose precision and invite a half-edit that parses but is not what the
// surveyor drew.
let importedLines = null;
let importedSummary = null;

/** "38.78, -75.14" per line -> [[lat, lon], ...]; null when blank. */
function parseTrack(text) {
  const pts = [];
  for (const raw of String(text || '').split(/[\n;]+/)) {
    const line = raw.trim();
    if (!line) continue;
    const nums = line.split(/[,\s]+/).map(Number).filter((n) => !Number.isNaN(n));
    if (nums.length >= 2) pts.push([nums[0], nums[1]]);
  }
  return pts.length >= 2 ? pts : null;
}

/** The homebound track: what was typed, or the outbound one reversed.
 *
 *  A blank box meaning "and back the way I came" saves typing the same points
 *  twice, which is where a transposed digit gets in. Written as a function
 *  rather than an expression inline in the request because the fallback has
 *  three states — typed, derived, absent — and an `||` chain that reads
 *  cleanly is not the same as one that evaluates correctly. */
function homeTrack() {
  const typed = parseTrack($('homeTrack').value);
  if (typed) return typed;
  const out = parseTrack($('outTrack').value);
  return out ? out.slice().reverse() : null;
}

// The BOX is metres; the wire, the engine and every test stay in NM. This is
// the one seam that converts, so nothing downstream had to learn a second unit
// — the same arrangement the waypoint unit uses, where changing the display
// never moves a waypoint.
const M_PER_NM = KM_PER_NM * 1000;

function surveyPattern() {
  const lat = $('patLat').value;
  const lon = $('patLon').value;
  const spacingM = Number($('patSpacing').value);
  if (lat === '' || lon === '' || !(spacingM > 0)) return null;
  return {
    anchor: [Number(lat), Number(lon)],
    bearing_deg: Number($('patBearing').value) || Number($('surCourse').value) || 0,
    // Count and length come from the survey leg, so there is exactly one
    // place to change them and the two can never disagree.
    lines: Number($('surLines').value),
    length_nm: Number($('surRange').value),
    spacing_nm: spacingM / M_PER_NM,
  };
}

function refreshPattern() {
  const p = surveyPattern();
  if (!p) {
    $('patOut').textContent = importedLines
      ? `${importedLines.length} imported lines in use`
      : '—';
    return;
  }
  const total = p.lines * p.length_nm;
  // Echo the spacing back in the unit it was TYPED in. Reading the box rather
  // than converting p.spacing_nm back means a rounding cannot make the readout
  // disagree with what is on screen above it.
  const spacingM = Number($('patSpacing').value);
  $('patOut').textContent =
    `${p.lines} lines × ${p.length_nm} NM on ${fmtDeg(p.bearing_deg)}, `
    + `${spacingM} m apart — ${total.toFixed(1)} NM of line`;
}

function fmtDeg(d) {
  return `${String(Math.round(Number(d) || 0)).padStart(3, '0')}°T`;
}

function clearGeometry() {
  importedLines = null;
  importedSummary = null;
  ['outTrack', 'homeTrack', 'patLat', 'patLon', 'patBearing']
    .forEach((id) => { $(id).value = ''; });
  // Spacing goes back to its DEFAULT rather than blank, which is what makes 50 m
  // a default and not just a starting value. `defaultValue` is the markup's own
  // `value` attribute, so the number lives in exactly one place and this cannot
  // drift from it. Blanking it is safe but unhelpful: a cleared pattern still
  // needs an anchor before it plans anything, so the spacing sitting at 50 can
  // never produce a survey on its own.
  $('patSpacing').value = $('patSpacing').defaultValue;
  $('useField').checked = false;
  $('planOut').textContent = '';
  $('planOut').className = 'note';
  refreshPattern();
}

async function importLinePlan() {
  const file = $('planFile').files && $('planFile').files[0];
  if (!file) {
    $('planOut').className = 'note warn';
    $('planOut').textContent = 'Choose a file first.';
    return;
  }
  const btn = $('planImportBtn');
  btn.disabled = true;
  $('planOut').className = 'note';
  $('planOut').textContent = `Reading ${file.name}…`;
  try {
    const buf = await file.arrayBuffer();
    // base64 in chunks: a big line plan overflows the argument list of a
    // single String.fromCharCode(...) spread.
    const bytes = new Uint8Array(buf);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 8192) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    }
    const data = await post('/api/lineplan', {
      filename: file.name,
      data: btoa(bin),
      utm_zone: $('planZone').value || null,
      northern: $('planHemi').value === 'N',
    });

    const s = data.summary;
    const target = $('planTarget').value;
    if (target === 'survey') {
      importedLines = data.lines;
      importedSummary = s;
      // The imported lines replace the generated pattern, so clear the
      // pattern fields rather than leaving two sources of truth on screen.
      // Spacing returns to its default here for the same reason it does in
      // clearGeometry — without an anchor it generates nothing either way.
      ['patLat', 'patLon', 'patBearing'].forEach((id) => { $(id).value = ''; });
      $('patSpacing').value = $('patSpacing').defaultValue;
      $('surLines').value = s.lines;
      if (s.mean_line_nm) $('surRange').value = s.mean_line_nm.toFixed(3);
      // The FIRST line's heading, not an average of them. A lawnmower's
      // bearings average to the direction it never steers — 020 and 200 mean
      // 110 — and that number was going straight into the course box.
      if (s.first_bearing_deg !== null) $('surCourse').value = Math.round(s.first_bearing_deg);
    } else {
      // A transit takes ONE polyline. More than one line in the file is
      // almost certainly a survey plan pointed at the wrong target, so say so
      // rather than silently flying the first of them.
      if (data.lines.length > 1) {
        $('planOut').className = 'note warn';
        $('planOut').textContent =
          `That file holds ${data.lines.length} lines — a transit takes one `
          + `track. Import it as survey lines, or edit the file.`;
        return;
      }
      $(target === 'out' ? 'outTrack' : 'homeTrack').value =
        data.lines[0].map((p) => `${p[0].toFixed(6)}, ${p[1].toFixed(6)}`).join('\n');
    }

    const bits = [`${s.format}: ${s.lines} lines, ${s.points} points, `
                  + `${s.total_nm.toFixed(1)} NM`];
    if (s.line_axis_deg !== null) {
      bits.push(`lines on the ${fmtDeg(s.line_axis_deg)}/`
                + `${fmtDeg((s.line_axis_deg + 180) % 360)} axis`);
    }
    if (s.mean_gap_nm) bits.push(`mean gap ${s.mean_gap_nm.toFixed(3)} NM`);
    bits.push(`first point ${fmtLat(s.first_point[0])} ${fmtLon(s.first_point[1])}`);
    if (s.crs && s.crs !== 'WGS84 geographic') bits.push(s.crs);
    (s.notes || []).forEach((n) => bits.push(n));
    $('planOut').className = 'note';
    $('planOut').textContent = bits.join(' · ');
    refreshDerived();
    refreshPattern();
  } catch (err) {
    importedLines = null;
    $('planOut').className = 'note bad';
    $('planOut').textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

// ------------------------------------------------------------ forecast currents
// The one thing in this app that reaches the network. It fills the per-leg
// current boxes and nothing else: no coefficient moves, no plan is run, and an
// operator can ignore the button entirely and type the tide in by hand.
let currentsSource = '';

/** The mission start as a UTC INSTANT.
 *
 *  `datetime-local` gives wall time with no zone, and the mission clock is
 *  happy with that — but a tide is not. Sending "14:00" to a forecast indexed
 *  in UTC would be four hours out in EDT, which at the mouth of Delaware Bay
 *  is most of the way from slack to peak flood. The browser is the only party
 *  that knows the operator's offset, so it converts here. */
function departureUtc() {
  const raw = $('startTime').value;
  if (!raw) return null;
  const d = new Date(raw);           // parsed as LOCAL time, by definition
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

// A position is SHOWN with its hemisphere — 075.1394 W, never -75.1394 E. The
// boxes take signed degrees because that is what charts and the API use, so
// this echo is where a mistyped sign becomes visible. Mirrors fmt_lat/fmt_lon
// in currents.py; a test pins the two against each other.
function fmtLat(lat) {
  return `${Math.abs(lat).toFixed(4).padStart(7, '0')}° ${lat < 0 ? 'S' : 'N'}`;
}

function fmtLon(lon) {
  return `${Math.abs(lon).toFixed(4).padStart(8, '0')}° ${lon < 0 ? 'W' : 'E'}`;
}

function refreshOrigin() {
  const lat = $('originLat').value;
  const lon = $('originLon').value;
  $('originOut').textContent = (lat === '' || lon === '')
    ? '—' : `${fmtLat(Number(lat))}   ${fmtLon(Number(lon))}`;
}

// 'warn' | 'bad' | 'busy'. Every kind is toggled on every call, so a state
// cannot survive the note that replaces it — which is what clears the reading
// flash on success, on failure and on a hand edit alike, without any of those
// paths knowing the flash exists.
function setCurrentsNote(text, kind) {
  const out = $('currentsOut');
  out.textContent = text;
  out.classList.toggle('warn', kind === 'warn');
  out.classList.toggle('bad', kind === 'bad');
  out.classList.toggle('busy', kind === 'busy');
  // The fetch is the one thing here that can hang on a network the boat may
  // not have, so its start is announced to a screen reader rather than left to
  // the flash. Assertive because it is replacing what the operator was reading.
  out.setAttribute('aria-live', kind === 'busy' ? 'assertive' : 'polite');
}

async function doCurrents() {
  const btn = $('currentsBtn');
  const lat = $('originLat').value;
  const lon = $('originLon').value;
  if (lat === '' || lon === '') {
    return setCurrentsNote('Give a departure latitude and longitude first.', 'warn');
  }
  const departure = departureUtc();
  if (!departure) {
    return setCurrentsNote('Set a mission start time first — a current is a '
                           + 'time of day, not a place alone.', 'warn');
  }

  const body = buildBody();
  btn.disabled = true;
  setCurrentsNote('Reading the forecast…', 'busy');
  try {
    const data = await post('/api/currents', {
      lat: Number(lat), lon: Number(lon), departure_utc: departure,
      legs: body.legs.map((l) => ({
        name: l.name, kind: l.kind, distance_nm: l.distance_nm,
        speed_kt: l.speed_kt, course_deg: l.course_deg,
        loiter_hours: l.loiter_hours, lines: l.lines,
        line_length_nm: l.line_length_nm,
      })),
    });

    // Fill only the legs the forecast could answer for. A leg it could not see
    // keeps whatever was already in the box: overwriting it with zero would
    // turn "no data" into "slack water", which is a different plan.
    let filled = 0;
    const missed = [];
    data.legs.forEach((row, i) => {
      const p = LEG_PREFIXES[i];
      if (!p) return;
      if (row.current_speed_kt === undefined) {
        missed.push(row.name);
        return;
      }
      $(p + 'CurKt').value = row.current_speed_kt;
      $(p + 'CurSet').value = row.current_set_deg;
      filled += 1;
    });

    currentsSource = filled ? data.source.label : '';
    const along = data.legs
      .filter((r) => r.along_kt !== undefined)
      .map((r) => `${r.name.replace('Transit ', '')} ${r.along_kt > 0 ? '+' : ''}`
                  + `${r.along_kt.toFixed(2)} kt`)
      .join(' · ');
    let note = `${data.source.label} — ${filled} of ${data.legs.length} legs filled`;
    if (along) note += `. Along track: ${along}`;
    if (missed.length) note += `. No data: ${missed.join(', ')}`;
    // An ESTIMATE is named here as well as in the label and the warning. A
    // borrowed value that reads like a forecast is worse than no value at all,
    // because it gets acted on with the same confidence.
    const est = data.estimated_legs || [];
    if (est.length) note += `. ESTIMATED: ${est.join(', ')}`;
    const kind = (data.warning || missed.length || est.length) ? 'warn' : '';
    setCurrentsNote(note, kind);
    if (data.warning) setCurrentsNote(`${note}. ${data.warning}`, kind);
    refreshDerived();
  } catch (err) {
    // Offline is the ordinary case at sea, not an exception worth hiding.
    currentsSource = '';
    setCurrentsNote(err.message, 'bad');
  } finally {
    btn.disabled = false;
  }
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await r.json();
  if (!r.ok) throw new Error(data.error || `request failed (${r.status})`);
  return data;
}

async function doPlan() {
  try {
    render(await post('/api/plan', buildBody()));
  } catch (err) {
    showError(err.message);
  }
}

async function doMaxSurvey() {
  $('maxOut').textContent = 'Solving…';
  const body = buildBody();
  const leg = body.legs.find((l) => l.kind === 'survey');
  const haveLength = Boolean(leg) && leg.line_length_nm > 0;
  // A line length with no count is precisely what this button is for — the area
  // sets the line length, the fuel decides how many you get. The engine rejects
  // that pair outright (a length needs a count), so probe with a single line.
  // The ANSWER does not depend on the probe: the search runs upward from 1. Only
  // the "planned vs fits" wording would, which is why the probe is tracked and
  // that wording suppressed when it was used.
  const probed = haveLength && !(leg.lines > 0);
  if (probed) leg.lines = 1;
  try {
    const { max_survey_nm: nm, lines: L } = await post('/api/max-survey', body);
    if (!(nm > 0)) {
      $('maxOut').textContent =
        'Even a zero-length survey breaches the reserve on these transits.';
      return;
    }
    // The line count is the actionable answer when the area will not fit in
    // one run; the continuous distance is the backstop for a survey given as
    // a plain distance.
    if (L) {
      // Write the count back into the form. It stays an ordinary input — type
      // over it and the new value is what the next plan or solve uses; nothing
      // here locks it or re-imposes the solved number.
      let filled = '';
      if (haveLength && L.lines > 0 && String(L.lines) !== $('surLines').value) {
        $('surLines').value = String(L.lines);
        refreshDerived();
        filled = ' Lines set to <strong>' + L.lines + '</strong> — edit it to plan fewer.';
      }
      // L.lines can be 0 while nm > 0: some survey DISTANCE fits, just not one
      // whole line of the length asked for. The old message here blamed the
      // transits ("the transits alone reach the floor"), which in this state
      // is provably false — nm > 0 means the transits hold with room to spare.
      // Blame the line length, and say how much distance IS available.
      const head = L.lines === 0
        ? `<strong>Not even one ${fmt(L.line_length_nm, 1)} NM line fits</strong> — `
          + `the fuel allows only ${fmt(nm, 1)} NM of survey. Shorten the lines.`
        : `Fuel allows <strong>${L.lines} lines</strong> of ${fmt(L.line_length_nm, 1)} NM `
          + `— ${fmt(L.distance_nm, 1)} NM of survey.`;
      // The engine's note compares the answer against the count that was
      // REQUESTED. After a probe that count was invented here, so the note
      // would report fitting one line with room for more — true, and useless.
      const tail = probed ? ''
        : L.completes
          ? `<span class="ok">${escapeHtml(L.note)}</span>`
          : `<span class="bad">${escapeHtml(L.note)}</span>`;
      $('maxOut').innerHTML = `${head} ${tail}${filled}`;
    } else {
      $('maxOut').textContent =
        `Longest survey that still returns at or above the reserve: ${fmt(nm, 1)} NM.`;
    }
  } catch (err) {
    $('maxOut').textContent = err.message;
  }
}

function showError(msg) {
  $('placeholder').hidden = false;
  $('output').hidden = true;
  $('verdict').hidden = true;
  $('placeholder').innerHTML = `<p><strong>Could not plan:</strong> ${escapeHtml(msg)}</p>`;
}

const escapeHtml = (s) => String(s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// ------------------------------------------------------------------- render
function render(p) {
  $('placeholder').hidden = true;
  $('output').hidden = false;

  const v = $('verdict');
  v.hidden = false;
  // One field decides the headline, so the API and the UI cannot disagree.
  // 'gauge_breach' is the case where the capacity row passes and the needle
  // does not — red, because the needle is what the operator will see.
  v.className = 'verdict ' + (p.verdict === 'ok' ? 'ok' : 'bad');
  $('verdictLabel').textContent = ({
    dry: 'RUNS DRY',
    breach: 'BREACHES RESERVE',
    gauge_breach: 'BREACHES ON THE GAUGE',
    ok: 'WITHIN RESERVE',
  })[p.verdict] || 'WITHIN RESERVE';
  const detail = {
    dry: () => `needs ${fmt(Math.abs(p.remaining_litres))} L more than the tank holds`,
    breach: () => `${fmt(Math.abs(p.margin_litres))} L short of the floor`,
    gauge_breach: () =>
      `needle reads ${fmt(p.indicated_return_pct, 1)}% on return, `
      + `${fmt(Math.abs(p.gauge_margin_litres))} L past what the gauge allows — `
      + `the ${fmt(p.capacity_l, 0)} L row still shows ${fmt(p.margin_litres)} L spare`,
    ok: () => `${fmt(p.binding_margin_litres)} L spare — about `
      + `${fmt(p.binding_margin_nm, 0)} NM`,
  };
  $('verdictDetail').textContent = (detail[p.verdict] || detail.ok)();

  $('tiles').innerHTML = [
    tile('Fuel used', fmt(p.total_litres), 'L'),
    tile('On return', remain(p.remaining_fraction, p.remaining_litres),
         p.runs_dry ? `${fmt(Math.abs(p.remaining_litres))} L short`
                    : `${fmt(p.remaining_litres)} L`,
         p.within_reserve ? 'ok' : 'bad'),
    // What the needle actually reads — from the measured gauge scale, with no
    // tank-capacity assumption in it. Sits beside the capacity-based tile on
    // purpose: when the two disagree, this is the one the operator will see.
    tile('Needle on return',
         p.indicated_return_pct == null ? '—' : fmt(p.indicated_return_pct, 1) + '%',
         p.gauge_within_reserve == null ? 'no gauge calibration'
           : `${fmt(p.gauge_margin_litres)} L over the floor`,
         p.gauge_within_reserve == null ? ''
           : (p.gauge_within_reserve ? 'ok' : 'bad')),
    tile('Margin over reserve', fmt(p.margin_litres), 'L', p.within_reserve ? 'ok' : 'bad'),
    // Spare follows the binding floor — a generous figure beside a red
    // verdict would be the contradiction this whole change exists to remove.
    tile('Spare range', p.verdict === 'ok' ? fmt(p.binding_margin_nm, 0) : '—', 'NM'),
    tile('Spare time', p.verdict === 'ok' ? fmt(p.binding_margin_hours) : '—', 'h'),
    tile('Mission time', fmt(p.total_hours), 'h'),
    // Shown even at zero: "no holds" is the confirmation an operator reviewing
    // a plan wants, and a tile that vanished would leave them wondering whether
    // they had forgotten to set one. Marked when it is not zero, because time
    // spent holding is time not surveying.
    tile('Loiter', hm(p.total_loiter_hours || 0),
         p.total_loiter_hours > 0 ? `${fmt(p.total_loiter_litres, 1)} L held` : 'no holds',
         p.total_loiter_hours > 0 ? 'warn' : ''),
    tile('Distance', fmt(p.total_distance_nm, 0), 'NM'),
    tile('Overall', fmt(p.total_distance_nm / p.total_litres, 2), 'NM/L'),
    // Read from the PLAN's legs, not from the form: these are the conditions
    // the numbers above were computed under, which is not the same thing as
    // whatever the inputs say after someone has edited them without replanning.
    // Current is per leg now, so the tile reports one value only when every leg
    // agrees — claiming a single figure across three forecasts would be the
    // kind of quiet averaging this planner exists to avoid. "sets" is spelled
    // out because current takes the opposite convention to wind.
    currentTile(p.legs),
  ].join('');

  const warn = $('warnings');
  warn.innerHTML = p.warnings.map(
    (w) => `<li class="${/^(PLAN |GAUGE )/.test(w) ? 'bad' : ''}">${escapeHtml(w)}</li>`
  ).join('');
  $('warnCard').hidden = p.warnings.length === 0;

  const tb = $('legTable').querySelector('tbody');
  tb.innerHTML = p.legs.map((l) => `
    <tr class="${l.extrapolated ? 'flag' : ''}">
      <td>${escapeHtml(l.name)}${l.extrapolated ? ' ⚠' : ''}</td>
      <td>${fmt(l.distance_nm)}</td><td>${fmt(l.speed_kt)}</td>
      <td>${l.rpm_max - l.rpm_min > 1
            ? `${fmt(l.rpm_min, 0)}–${fmt(l.rpm_max, 0)}`
            : fmt(l.rpm_required, 0)}</td>
      <td>${signedPct(l.total_premium)}${l.premium_max - l.premium_min > 1e-6
            ? ` <span class="soft">±${fmt((l.premium_max - l.premium_min) * 50, 1)}</span>`
            : ''}</td>
      <td>${fmt(l.fuel_rate_lph, 2)}</td>
      <td>${fmt(l.hours, 2)}${l.loiter_hours > 0
            ? ` <span class="soft">+${fmt(l.loiter_hours, 2)} hold</span>` : ''}</td>
      <td>${l.end_clock ? escapeHtml(l.end_clock) : 'T+' + fmt(l.end_hours, 2) + ' h'}</td>
      <td>${fmt(l.litres, 1)}${l.loiter_litres > 0
            ? ` <span class="soft">+${fmt(l.loiter_litres, 1)} hold</span>` : ''}</td>
      <td>${fmt(l.nm_per_l, 2)}</td>
    </tr>`).join('');
  // Per-leg notes carry the reasoning behind a number — why a survey costs more
  // than its mean premium suggests, which lines left the fitted window. They
  // were being generated and thrown away.
  const legNotes = p.legs.flatMap((l) => l.notes.map((n) => [l.name, n]));
  $('legNotes').innerHTML = legNotes.length
    ? '<ul class="warnings">' + legNotes.map(
        ([name, n]) => `<li><strong>${escapeHtml(name)}:</strong> ${escapeHtml(n)}</li>`
      ).join('') + '</ul>'
    : '';

  const mk = $('marksTable').querySelector('tbody');
  mk.innerHTML = (p.marks || []).map((m) => `
    <tr class="${m.kind === 'phase' || m.phase === 'inbound' ? 'here' : ''}">
      <td>${escapeHtml(m.label)}</td>
      <td>${escapeHtml(m.leg)}</td>
      <td>T+${fmt(m.elapsed_hours, 2)} h</td>
      <td>${m.clock ? escapeHtml(m.clock) : '—'}</td>
      <td>${fmt(m.litres_burned, 1)} L</td>
      <td>${m.indicated_pct == null ? '—' : fmt(m.indicated_pct, 1) + '%'}</td>
    </tr>`).join('');
  $('marksCard').hidden = !(p.marks && p.marks.length);

  const st = $('sensTable').querySelector('tbody');
  st.innerHTML = p.sensitivity.map((s) => `
    <tr class="${s.premium_delta === 0 ? 'here' : ''}">
      <td>${s.premium_delta === 0 ? 'as planned' : signedPct(s.premium_delta)}</td>
      <td>${fmt(s.total_litres)} L</td>
      <td>${remain(s.remaining_fraction, s.remaining_litres)}</td>
      <td>${fmt(s.margin_litres)} L</td>
      <td><span class="pill ${s.within_reserve ? 'ok' : 'bad'}">${
        s.runs_dry ? 'dry' : s.within_reserve ? 'ok' : 'breach'}</span></td>
    </tr>`).join('');

  // The tank/gauge card is gone (Andy, 2026-08-11), so `gauge_*` and
  // `capacity_scenarios` are no longer rendered here. The needle itself is NOT
  // lost: it is the "Needle on return" tile, and `verdict` still drives the
  // banner — including `gauge_breach`, where the capacity row passes and the
  // needle does not. Verification rail 6 stays satisfied.

  // Every plan writes a report to docs/missions/. Say so — a file appearing on
  // the operator's disk should never be a surprise — and say it plainly when
  // one could NOT be written, because a silently missing report is worse than
  // no report at all.
  const r = p.report;
  const out = $('reportOut');
  if (!r || (!r.written && !r.error)) {
    out.textContent = '';
  } else if (r.written) {
    out.innerHTML = `Report saved to <code>${escapeHtml(r.path)}</code>`;
  } else {
    out.innerHTML = `<span class="bad">Report not saved: ${escapeHtml(r.error)}</span>`;
  }
}

/** A duration as an operator says it: '0 min', '45 min', '1 h', '2 h 30 min'.
 *  Mirrors _hm() in engine.py so the tile and the leg notes agree. */
function hm(hours) {
  const totalMin = Math.round((Number(hours) || 0) * 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h && m) return `${h} h ${m} min`;
  return h ? `${h} h` : `${m} min`;
}

// -------------------------------------------------------------- leg weather
// Wind and current belong to the leg, not the mission: two days at survey speed
// means the wind on the bow going out is not the wind coming home, and the tide
// turns twice in between.
const LEG_PREFIXES = ['out', 'sur', 'home'];

function legWeather(p) {
  return {
    wmo_sea_state: Number($(p + 'Sea').value),
    wind_speed_kt: Number($(p + 'WindKt').value) || 0,
    wind_from_deg: Number($(p + 'WindFrom').value) || 0,
    current_speed_kt: Number($(p + 'CurKt').value) || 0,
    current_set_deg: Number($(p + 'CurSet').value) || 0,
  };
}

// ----------------------------------------------------------------- quick start
// The help panel renders QUICKSTART.md itself, so the document in the repo IS
// the help in the app and neither can go stale against the other.
//
// The renderer below is a DELIBERATELY SMALL markdown subset — headings, lists,
// paragraphs, bold, inline code — because that is all the document uses and a
// full parser would be a dependency this project does not take. A test asserts
// the document stays inside the subset, so an edit that reaches for a table or
// a link fails the suite instead of rendering as literal asterisks.

function renderMarkdownSubset(md) {
  const esc = (s) => s.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  // Escape FIRST, then apply inline marks, so nothing in the document can
  // inject markup.
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  const out = [];
  let list = null;
  let para = [];
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  const flushPara = () => {
    if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para = []; }
  };

  md.split('\n').forEach((raw) => {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) { flushPara(); closeList(); return; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) {
      flushPara(); closeList();
      const h = m[1].length;
      out.push(`<h${h}>${inline(m[2])}</h${h}>`);
    } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
      flushPara();
      if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul'; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
      flushPara();
      if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol'; }
      out.push(`<li>${inline(m[1])}</li>`);
    } else if (list && /^\s{2,}\S/.test(raw)) {
      // A wrapped continuation of the item above, joined back onto it.
      out[out.length - 1] = out[out.length - 1]
        .replace(/<\/li>$/, ` ${inline(line.trim())}</li>`);
    } else {
      closeList();
      para.push(line.trim());
    }
  });
  flushPara(); closeList();
  return out.join('\n');
}

let quickstartHtml = null;      // fetched once, then reused
let helpLastFocus = null;

async function openHelp() {
  const panel = $('helpPanel');
  const body = $('helpBody');
  helpLastFocus = document.activeElement;
  panel.hidden = false;
  if (quickstartHtml === null) {
    body.innerHTML = '<p>Loading…</p>';
    try {
      const r = await fetch('/quickstart.md');
      if (!r.ok) throw new Error(`quick start unavailable (${r.status})`);
      // Drop the document's leading H1: the dialog header already says "Quick
      // start", and rendering both puts the title on screen twice. The heading
      // stays in the file, where a standalone document needs it.
      const md = (await r.text()).replace(/^#[^#\n][^\n]*\n/, '');
      quickstartHtml = renderMarkdownSubset(md);
    } catch (err) {
      // Left null so a later open retries rather than caching the failure.
      body.innerHTML = `<p>${escapeHtml(err.message)}</p>`;
      return;
    }
  }
  body.innerHTML = quickstartHtml;
  body.scrollTop = 0;
  body.focus();
}

function closeHelp() {
  $('helpPanel').hidden = true;
  if (helpLastFocus) helpLastFocus.focus();
}

/** The Current tile, summarising what each leg was actually planned under. */
function currentTile(legs) {
  // A slack current has no direction worth distinguishing: 0 kt setting 090
  // and 0 kt setting 270 are the same water. Keying on the set regardless made
  // an all-slack mission with a leftover set value read "varies by leg" in
  // amber — found in review.
  const vecs = [...new Set(legs.map((l) => {
    const kt = Number(l.current_speed_kt);
    return kt > 0
      ? `${kt.toFixed(2)}|${Number(l.current_set_deg).toFixed(0)}` : 'slack';
  }))];
  if (vecs.length === 1) {
    const kt = Number(legs[0].current_speed_kt);
    return tile('Current', `${fmt(kt, 1)} kt`,
                kt > 0 ? `sets ${fmt(legs[0].current_set_deg, 0)}°T` : 'slack');
  }
  const kts = legs.map((l) => Number(l.current_speed_kt));
  return tile('Current',
              `${fmt(Math.min(...kts), 1)}–${fmt(Math.max(...kts), 1)} kt`,
              'varies by leg', 'warn');
}

/** Compact duration for the rose, where '2 h 30 min' will not fit: '45m',
 *  '2h', '2h30'. The tile and the leg notes keep the long form. */
function hmShort(hours) {
  const totalMin = Math.round((Number(hours) || 0) * 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (!h) return `${m}m`;
  return m ? `${h}h${String(m).padStart(2, '0')}` : `${h}h`;
}

const tile = (k, v, u, cls = '') =>
  `<div class="tile ${cls}"><div class="k">${k}</div><div class="v">${v}</div>` +
  `<div class="u">${u}</div></div>`;

// -------------------------------------------------------------------- rose
function drawRose() {
  const cx = 100, cy = 100, R = 74;
  // Each leg carries its own weather now, so the rose draws one arrow per
  // DISTINCT vector rather than one for the mission. When the three legs share
  // a forecast — the common case — that collapses back to a single pair of
  // arrows and the picture is what it always was.
  const legs = ['out', 'sur', 'home'].map((p, i) => ({
    p,
    c: Number($(p + 'Course').value) || 0,
    label: ['out', 'survey', 'home'][i],
    hold: loiterHours(p),
    windKt: Number($(p + 'WindKt').value) || 0,
    windFrom: Number($(p + 'WindFrom').value) || 0,
    curKt: Number($(p + 'CurKt').value) || 0,
    curSet: Number($(p + 'CurSet').value) || 0,
  }));
  const distinct = (kt, deg) => {
    const seen = new Map();
    legs.forEach((l) => {
      if (l[kt] <= 0) return;
      const key = `${l[kt]}|${l[deg]}`;
      if (!seen.has(key)) seen.set(key, { kt: l[kt], deg: l[deg], legs: [] });
      seen.get(key).legs.push(l.label);
    });
    return [...seen.values()];
  };
  const winds = distinct('windKt', 'windFrom');
  const currents = distinct('curKt', 'curSet');
  const pt = (deg, r) => {
    const a = (deg - 90) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };

  // Leg labels sit on the leg's own bearing, so two legs on a similar course
  // put their labels in the same place — "out" over "survey" whenever a survey
  // runs near a transit, which is common rather than pathological.
  //
  // They are separated VERTICALLY, not radially, and the separation is decided
  // AFTER rendering from real getBBox() measurements — see nudgeRoseLabels().
  //
  // Two earlier attempts are worth not repeating. Radial stacking fails because
  // the text is horizontal: on an east-west leg two labels one ring apart are
  // 14 units apart while needing ~23 to clear, and a sweep of 9216 course
  // combinations found 1557 collisions. Estimating box sizes from character
  // counts then fails more quietly — 0.55em per character reads "home" as 17.6
  // wide when it renders 21.1, and font-size 8 as 8 tall when it renders 10, so
  // it under-nudges and leaves 240. Only measurement gets it to zero.
  legs.forEach((leg) => { [leg.lx, leg.ly] = pt(leg.c, R - 20); });

  let s = `<circle cx="${cx}" cy="${cy}" r="${R}" fill="none"
             stroke="var(--line)" stroke-width="1"/>`;
  ['N', 'E', 'S', 'W'].forEach((lab, i) => {
    const [x, y] = pt(i * 90, R + 11);
    s += `<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="middle"
            font-size="10" fill="var(--ink-soft)">${lab}</text>`;
  });

  legs.forEach(({ c, label, hold, lx, ly, windKt, windFrom }) => {
    const [x, y] = pt(c, R - 6);
    // Colour by how this course works with or against ITS OWN wind — which is
    // the point of per-leg weather: the return leg can be green while the
    // outbound is red under a wind that has backed in between.
    const rel = Math.cos((c - windFrom) * Math.PI / 180);
    const col = windKt <= 0 ? 'var(--ink-soft)'
      : rel > 0.25 ? 'var(--bad)' : rel < -0.25 ? 'var(--ok)' : 'var(--ink-soft)';
    s += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="${col}"
            stroke-width="2.5" stroke-linecap="round"/>`;
    // A hold has no bearing, so it is NOT drawn as a direction — that would be
    // inventing geometry the delay does not have. It is drawn where it happens:
    // a marker at the INBOARD end of the leg's own line, because the engine
    // takes the hold at the START of the leg (the 2026-08-12 arrival-time fix
    // moved it there from the end, and the dot moved with it). Radius 18 keeps
    // three dots on similar courses clear of the wind/current text at centre.
    // The duration rides the leg's OWN label rather than getting a text of its
    // own — a separate text collided with the label it belonged to on any
    // east-west leg; measured, not guessed.
    if (hold > 0) {
      const [hx, hy] = pt(c, 18);
      s += `<circle cx="${hx}" cy="${hy}" r="4" fill="var(--warn)"
              stroke="var(--panel)" stroke-width="1"/>`;
    }
    const held = hold > 0
      ? ` <tspan fill="var(--warn)">${hmShort(hold)}</tspan>` : '';
    // data-* carries what the post-render nudge needs: where this label's own
    // leg line ends, so a displaced label can be given a leader back to it.
    const [ex, ey] = pt(c, R - 12);
    s += `<text class="leg-label" data-ex="${ex.toFixed(2)}" data-ey="${ey.toFixed(2)}"
            data-col="${col}" x="${lx}" y="${ly}" text-anchor="middle" font-size="8"
            fill="${col}">${label}${held}</text>`;
  });

  // Wind arrows: one per distinct vector, blowing TOWARDS from + 180. Thinner
  // when the legs disagree, and tagged with which legs share it — an untagged
  // arrow in a mission with three forecasts would be a guess.
  //
  // Disagreement is judged PER FAMILY, never as an aggregate: the old
  // `winds + currents > 2` test let exactly two distinct winds (and no
  // current) pass untagged, with the centre readout presenting one leg's wind
  // as the mission's while the arrows showed two — found in review. Two winds
  // disagree the moment there are two of them, whatever the currents do.
  const windsVary = winds.length > 1;
  const currentsVary = currents.length > 1;
  winds.forEach((w) => {
    const [sx, sy] = pt(w.deg, R - 2);
    const [ex, ey] = pt(w.deg + 180, R - 34);
    s += `<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="var(--wind)"
            stroke-width="${windsVary ? 2 : 3}" stroke-linecap="round"
            marker-end="url(#arrow)"/>`;
    if (windsVary) {
      const [tx, ty] = pt(w.deg + 180, R - 40);
      s += `<text class="wx-label" x="${tx}" y="${ty}" text-anchor="middle"
              font-size="7" fill="var(--wind)">${w.kt} kt ${w.legs.join('/')}</text>`;
    }
  });

  // The current arrow points where the water GOES — the opposite convention to
  // the wind arrow beside it, which is exactly why it gets its own colour and
  // its own dashed line rather than sharing the wind's styling.
  currents.forEach((c) => {
    const [sx, sy] = pt(c.deg + 180, R - 2);
    const [ex, ey] = pt(c.deg, R - 34);
    s += `<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="var(--warn)"
            stroke-width="${currentsVary ? 2 : 2.5}" stroke-linecap="round"
            stroke-dasharray="5 3" marker-end="url(#arrowCur)"/>`;
    if (currentsVary) {
      const [tx, ty] = pt(c.deg, R - 40);
      s += `<text class="wx-label" x="${tx}" y="${ty}" text-anchor="middle"
              font-size="7" fill="var(--warn)">${c.kt} kt ${c.legs.join('/')}</text>`;
    }
  });

  // Each family's centre readout appears only when that family agrees; a
  // varying family's figures live on its arrow tags instead.
  const wAgree = !windsVary && winds[0];
  const cAgree = !currentsVary && currents[0];
  if (wAgree) s += `<text x="${cx}" y="${cy + (cAgree ? -2 : 4)}" text-anchor="middle"
          font-size="9" fill="var(--wind)">${winds[0].kt} kt</text>`;
  if (cAgree) s += `<text x="${cx}" y="${cy + (wAgree ? 12 : 4)}" text-anchor="middle"
          font-size="9" fill="var(--warn)">${currents[0].kt} kt set</text>`;

  $('rose').innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--wind)"/></marker>
      <marker id="arrowCur" viewBox="0 0 10 10" refX="8" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--warn)"/></marker></defs>` + s;
  nudgeRoseLabels();
}

/** Separate overlapping leg labels, measuring what the browser actually drew.
 *
 *  Runs after the SVG is in the document because getBBox() is the only thing
 *  that knows how wide "home" really is. Estimating from character counts was
 *  tried and left 240 collisions in a 9216-case sweep; measuring leaves none.
 *  Each label is nudged in y, alternating down and up so it stays near its own
 *  bearing, and anything displaced gets a leader back to its leg line.
 */
function nudgeRoseLabels() {
  const svg = $('rose');
  // Leg labels first, then the per-arrow weather tags: the leg names matter
  // more, so they get first claim on their own bearing and the wx tags move
  // around them. Both need nudging — with three forecasts a wind tag and a
  // current tag can land on the same bearing, which they did.
  const labels = [...svg.querySelectorAll('text.leg-label'),
                  ...svg.querySelectorAll('text.wx-label')];
  if (!labels.length) return;
  const pad = 1.5;
  const hits = (a, b) => a.x < b.x + b.width + pad && b.x < a.x + a.width + pad
                      && a.y < b.y + b.height + pad && b.y < a.y + a.height + pad;
  // Fixed furniture first: compass points and the wind/current readouts. A
  // nudged label must not land on those either.
  const taken = [...svg.querySelectorAll('text')]
    .filter((t) => !t.classList.contains('leg-label')
                && !t.classList.contains('wx-label'))
    .map((t) => t.getBBox());

  labels.forEach((t) => {
    const y0 = Number(t.getAttribute('y'));
    let best = null;
    // Step 13, not 11: a label renders 10 tall and clearance allows 1.5 either
    // side, so anything under 11.5 leaves the pair still touching. An 11-step
    // version left exactly the cases where two legs share a course.
    for (const dy of [0, 13, -13, 26, -26, 39, -39, 52, -52]) {
      t.setAttribute('y', String(y0 + dy));
      const b = t.getBBox();
      if (b.y < 1 || b.y + b.height > 199) continue;     // keep it on the canvas
      if (!taken.some((o) => hits(b, o))) { best = { dy, b }; break; }
    }
    if (!best) { t.setAttribute('y', String(y0)); best = { dy: 0, b: t.getBBox() }; }
    taken.push(best.b);
    // Only leg labels get a leader. A weather tag names the legs it applies to
    // in its own text, so it identifies itself wherever it ends up.
    if (best.dy !== 0 && t.classList.contains('leg-label')) {
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', t.getAttribute('x'));
      line.setAttribute('y1', String(Number(t.getAttribute('y')) - 2.5));
      line.setAttribute('x2', t.dataset.ex);
      line.setAttribute('y2', t.dataset.ey);
      line.setAttribute('stroke', t.dataset.col);
      line.setAttribute('stroke-width', '0.6');
      line.setAttribute('stroke-dasharray', '2 2');
      line.setAttribute('opacity', '0.75');
      svg.insertBefore(line, t);
    }
  });
}

boot();
