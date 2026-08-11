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
    o.dataset.note = g.status_note || '';
    gon.appendChild(o);
  });
  gon.value = (MODEL.gondolas && MODEL.gondolas.default) || 'em712';
  const onGondola = () => {
    const o = gon.selectedOptions[0];
    $('gondolaNote').textContent = o ? o.dataset.note : '';
  };
  gon.addEventListener('change', onGondola);
  onGondola();

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

  $('planForm').addEventListener('submit', (e) => { e.preventDefault(); doPlan(); });
  $('maxBtn').addEventListener('click', doMaxSurvey);

  onSeaChange();
  refreshDerived();
  drawRose();
}

function onSeaChange() {
  const opt = $('seaState').selectedOptions[0];
  const p = Number(opt.dataset.premium);
  $('seaPremium').value = signedPct(p) + ' RPM';
  $('seaNote').textContent =
    'Assumption, not a fitted value — the source data supports one anchor only ' +
    '(WMO 2–3, somewhere between 0% and 13%). Everything else is judgement.';
  refreshDerived();
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
        speed_kt: Number($('outSpeed').value), course_deg: Number($('outCourse').value) },
      { name: 'Survey', kind: 'survey',
        // distance is derived server-side from lines x line length
        distance_nm: 0,
        lines: Number($('surLines').value),
        line_length_nm: Number($('surRange').value),
        speed_kt: Number($('surSpeed').value), course_deg: Number($('surCourse').value) },
      { name: 'Transit home', kind: 'transit', distance_nm: Number($('homeRange').value),
        speed_kt: Number($('homeSpeed').value), course_deg: Number($('homeCourse').value) },
    ],
    // Blank start time is sent as null: elapsed hours only, no clock.
    start_time: $('startTime').value || null,
    // Comma- or space-separated radii, e.g. "13, 26". Blank falls back to
    // the model defaults server-side rather than silently meaning "none".
    home_marks_km: ($('homeMarkKm').value.match(/[\d.]+/g) || []).map(Number),
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
  try {
    const { max_survey_nm: nm, lines: L } = await post('/api/max-survey', buildBody());
    if (!(nm > 0)) {
      $('maxOut').textContent =
        'Even a zero-length survey breaches the reserve on these transits.';
      return;
    }
    // The line count is the actionable answer when the area will not fit in
    // one run; the continuous distance is the backstop for a survey given as
    // a plain distance.
    if (L) {
      const head = L.lines === 0
        ? '<strong>Not even one line fits</strong> — the transits alone reach the floor.'
        : `Fuel allows <strong>${L.lines} lines</strong> of ${fmt(L.line_length_nm, 1)} NM `
          + `— ${fmt(L.distance_nm, 1)} NM of survey.`;
      const tail = L.completes
        ? `<span class="ok">${escapeHtml(L.note)}</span>`
        : `<span class="bad">${escapeHtml(L.note)}</span>`;
      $('maxOut').innerHTML = `${head} ${tail}`;
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
      <td>${fmt(l.fuel_rate_lph, 2)}</td><td>${fmt(l.hours, 2)}</td>
      <td>${l.end_clock ? escapeHtml(l.end_clock) : 'T+' + fmt(l.end_hours, 2) + ' h'}</td>
      <td>${fmt(l.litres, 1)}</td><td>${fmt(l.nm_per_l, 2)}</td>
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

  const gn = $('gaugeNote');
  gn.innerHTML = p.gauge_l_per_point == null ? '' :
    `The reserve floor is a <strong>needle position</strong>, not a number of litres, so ` +
    `mission fuel is what the gauge holds between full and the floor: ` +
    `<strong>${fmt(p.gauge_usable_litres)} L</strong>. No capacity assumption enters it. ` +
    `The measured 72–86% band is ${fmt(p.gauge_l_per_point, 2)} L per indicated point; ` +
    (p.gauge_unlocated_l != null && Math.abs(p.gauge_unlocated_l) < 1
      ? `outside it the profile carries the balance of the ${fmt(p.tank_volume_l, 0)} L ` +
        `drawing volume, which is an <strong>inference</strong> the drawings support and ` +
        `no drawdown has tested.`
      : `the gauge is taken to speak only for its own span, leaving about ` +
        `<strong>${fmt(p.gauge_unlocated_l, 0)} L</strong> of the ` +
        `${fmt(p.tank_volume_l, 0)} L tank outside the indicated range.`);

  const ct = $('capTable').querySelector('tbody');
  ct.innerHTML = p.capacity_scenarios.map((c) => `
    <tr class="${Math.abs(c.capacity_l - p.capacity_l) < 0.01 ? 'here' : ''}">
      <td>${escapeHtml(c.label)}</td>
      <td>${remain(c.remaining_fraction, c.remaining_litres)}</td>
      <td>${fmt(c.margin_litres)} L</td>
      <td><span class="pill ${c.within_reserve ? 'ok' : 'bad'}">${
        c.runs_dry ? 'dry' : c.within_reserve ? 'ok' : 'breach'}</span></td>
    </tr>`).join('');
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
