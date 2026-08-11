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

  const sea = $('seaState');
  MODEL.sea_state_premium.table.forEach((row) => {
    const o = document.createElement('option');
    o.value = String(row.wmo);
    o.textContent = `${row.wmo} — ${row.label} (${row.hs_m} m)`;
    o.dataset.premium = String(row.premium);
    sea.appendChild(o);
  });
  sea.value = '2';

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

  sea.addEventListener('change', onSeaChange);
  cap.addEventListener('change', () => {
    if (cap.value !== 'custom') $('capacity').value = Number(cap.value).toFixed(1);
    refreshDerived();
  });
  ['capacity', 'reserve', 'startLevel', 'surLines', 'surRange'].forEach((id) =>
    $(id).addEventListener('input', refreshDerived));
  ['windSpeed', 'windFrom', 'outCourse', 'surCourse', 'homeCourse'].forEach((id) =>
    $(id).addEventListener('input', drawRose));

  $('waypointUnit').addEventListener('change', onWaypointUnitChange);
  wireLoiter();

  $('planForm').addEventListener('submit', (e) => { e.preventDefault(); doPlan(); });
  $('maxBtn').addEventListener('click', doMaxSurvey);

  onSeaChange();
  refreshDerived();
  refreshHolding();
  drawRose();
}

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
  const opt = $('seaState').selectedOptions[0];
  const p = Number(opt.dataset.premium);
  $('seaPremium').value = signedPct(p) + ' RPM';
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
    }));
  LOITER_LEGS.forEach((p) => {
    $(p + 'Loiter').addEventListener('input', refreshHolding);
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
    environment: {
      wmo_sea_state: Number($('seaState').value),
      wind_speed_kt: Number($('windSpeed').value),
      wind_from_deg: Number($('windFrom').value),
    },
    vessel: {
      capacity_l: Number($('capacity').value),
      reserve_fraction: Number($('reserve').value) / 100,
      start_level_fraction: Number($('startLevel').value) / 100,
      gondola: $('gondola').value,
    },
    legs: [
      { name: 'Transit out', kind: 'transit', distance_nm: Number($('outRange').value),
        speed_kt: Number($('outSpeed').value), course_deg: Number($('outCourse').value),
        loiter_hours: loiterHours('out') },
      { name: 'Survey', kind: 'survey',
        // distance is derived server-side from lines x line length
        distance_nm: 0,
        lines: Number($('surLines').value),
        line_length_nm: Number($('surRange').value),
        speed_kt: Number($('surSpeed').value), course_deg: Number($('surCourse').value),
        loiter_hours: loiterHours('sur') },
      { name: 'Transit home', kind: 'transit', distance_nm: Number($('homeRange').value),
        speed_kt: Number($('homeSpeed').value), course_deg: Number($('homeCourse').value),
        loiter_hours: loiterHours('home') },
    ],
    // Blank start time is sent as null: elapsed hours only, no clock.
    start_time: $('startTime').value || null,
    // Comma- or space-separated distances, e.g. "13, 26", in the selected
    // unit. Blank falls back to the model defaults server-side rather than
    // silently meaning "none".
    waypoints: ($('waypoints').value.match(/[\d.]+/g) || []).map(Number),
    waypoint_unit: $('waypointUnit').value,
  };
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
      const head = L.lines === 0
        ? '<strong>Not even one line fits</strong> — the transits alone reach the floor.'
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
    tile('Distance', fmt(p.total_distance_nm, 0), 'NM'),
    tile('Overall', fmt(p.total_distance_nm / p.total_litres, 2), 'NM/L'),
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

  $('finishOut').textContent = p.finish_clock
    ? p.finish_clock
    : `T+${fmt(p.total_hours, 2)} h`;

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
}

const tile = (k, v, u, cls = '') =>
  `<div class="tile ${cls}"><div class="k">${k}</div><div class="v">${v}</div>` +
  `<div class="u">${u}</div></div>`;

// -------------------------------------------------------------------- rose
function drawRose() {
  const cx = 100, cy = 100, R = 74;
  const windFrom = Number($('windFrom').value) || 0;
  const windKt = Number($('windSpeed').value) || 0;
  const legs = [
    { c: Number($('outCourse').value) || 0, label: 'out' },
    { c: Number($('surCourse').value) || 0, label: 'survey' },
    { c: Number($('homeCourse').value) || 0, label: 'home' },
  ];
  const pt = (deg, r) => {
    const a = (deg - 90) * Math.PI / 180;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  };

  let s = `<circle cx="${cx}" cy="${cy}" r="${R}" fill="none"
             stroke="var(--line)" stroke-width="1"/>`;
  ['N', 'E', 'S', 'W'].forEach((lab, i) => {
    const [x, y] = pt(i * 90, R + 11);
    s += `<text x="${x}" y="${y}" text-anchor="middle" dominant-baseline="middle"
            font-size="10" fill="var(--ink-soft)">${lab}</text>`;
  });

  legs.forEach(({ c, label }) => {
    const [x, y] = pt(c, R - 6);
    // colour by how much this course works with or against the wind
    const rel = Math.cos((c - windFrom) * Math.PI / 180);
    const col = windKt <= 0 ? 'var(--ink-soft)'
      : rel > 0.25 ? 'var(--bad)' : rel < -0.25 ? 'var(--ok)' : 'var(--ink-soft)';
    s += `<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="${col}"
            stroke-width="2.5" stroke-linecap="round"/>`;
    const [tx, ty] = pt(c, R - 20);
    s += `<text x="${tx}" y="${ty}" text-anchor="middle" font-size="8"
            fill="${col}">${label}</text>`;
  });

  if (windKt > 0) {
    // wind blows TOWARDS windFrom + 180
    const [sx, sy] = pt(windFrom, R - 2);
    const [ex, ey] = pt(windFrom + 180, R - 34);
    s += `<line x1="${sx}" y1="${sy}" x2="${ex}" y2="${ey}" stroke="var(--wind)"
            stroke-width="3" stroke-linecap="round"
            marker-end="url(#arrow)"/>`;
    s += `<text x="${cx}" y="${cy + 4}" text-anchor="middle" font-size="9"
            fill="var(--wind)">${windKt} kt</text>`;
  }

  $('rose').innerHTML = `<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--wind)"/></marker></defs>` + s;
}

boot();
