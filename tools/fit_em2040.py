"""Fit the EM2040 fuel and speed laws from extracted MCAP caches.

Usage:
    python tools/fit_em2040.py [CACHE_DIR]

CACHE_DIR defaults to tools/rosbags (the output of extract_bags.py). Writes
em2040_fit.json alongside the caches and prints the bin tables and fits.
This is the script whose Aug 2026 output (tools/em2040_fit_2026-08-07.json)
produced the gondolas.em2040 coefficients in model.json v2.0.0.

Extra dependency: pip install numpy

Method (details in report section 5.5):
  * Time-sort each channel on load — segment files can arrive out of order.
  * CRUISE = engine on, 1200<=thruster_rpm<3200, sog>1.5 m/s, and 60 s rolling
    std limits: rpm<40, sog<0.20 m/s, unwrapped cog<6 deg (straight running).
    LOITER = engine on, 800-1200 rpm (station-keeping — different prop loading,
    kept out of the cruise fit deliberately).
  * Median per 100-rpm bin (>=120 samples, i.e. >=2 min), weighted fits with
    weights capped at 3600 so one long bin cannot own the answer.
  * Gauge calibration: flow-meter litres per day against robust (10-min median)
    gauge endpoints — no capacity or fuel model assumed.
"""
import json
import sys
from pathlib import Path

import numpy as np

CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / 'rosbags'
MS_KT = 1.9438444924406046

# Reference laws, for comparison printouts only (not used in the fits):
F0T, F1T = -1.7761355709825288, 0.0024949428685311897    # EM712 fuel law
B22, M22 = -0.04343717952015602, 0.004033265611920235    # 2022 EM2040 speed law


def load_sorted(p):
    z = np.load(p)
    d = {k: z[k] for k in z.files}
    for grp in ('t3', 'vs', 'gps', 'ins', 'fc'):
        t = d.get(f'{grp}_t')
        if t is None or len(t) == 0:
            continue
        idx = np.argsort(t, kind='stable')
        for k in list(d):
            if k.startswith(grp + '_'):
                d[k] = d[k][idx]
    return d


def rolling_std_ok(t, x, win_s, max_std, min_n=20):
    ok = np.zeros(len(t), bool)
    csum = np.cumsum(np.insert(x.astype(float), 0, 0))
    csq = np.cumsum(np.insert(x.astype(float) ** 2, 0, 0))
    j = 0
    for i in range(len(t)):
        while t[i] - t[j] > win_s:
            j += 1
        n = i - j + 1
        if n < min_n:
            continue
        m = (csum[i + 1] - csum[j]) / n
        v = max(0.0, (csq[i + 1] - csq[j]) / n - m * m)
        ok[i] = v <= max_std ** 2
    return ok


def wpoly(x, y, w, deg):
    A = np.vstack([x ** d for d in range(deg + 1)]).T
    Wm = np.diag(w)
    beta = np.linalg.solve(A.T @ Wm @ A, A.T @ Wm @ y)
    pred = A @ beta
    r2 = 1 - np.sum(w * (y - pred) ** 2) / np.sum(w * (y - np.average(y, weights=w)) ** 2)
    return beta, r2


DAYS = []
for p in sorted(CACHE.glob('2026*.npz')):
    d = load_sorted(p)
    t = d['t3_t']
    if len(t) < 100 or not d['t3_engine_on'].any():
        continue
    on = d['t3_engine_on'].astype(bool)
    lph = d['t3_fuel_lph'].astype(float)
    sog = np.interp(t, d['gps_t'], d['gps_sog'])           # m/s
    cog = np.interp(t, d['gps_t'], np.degrees(np.unwrap(np.radians(d['gps_cog']))))
    rpm = np.interp(t, d['vs_t'], d['vs_thruster_rpm'].astype(float))
    gas = np.interp(t, d['vs_t'], d['vs_gas_pct'].astype(float))
    ok_r = rolling_std_ok(t, rpm, 60, 40.0)
    ok_s = rolling_std_ok(t, sog, 60, 0.20)
    ok_c = rolling_std_ok(t, cog, 60, 6.0)                 # straight running
    cruise = on & ok_r & ok_s & ok_c & (rpm >= 1200) & (rpm < 3200) & (sog > 1.5)
    loiter = on & ok_r & (rpm >= 800) & (rpm < 1200) & (lph > 0.3)
    DAYS.append(dict(day=p.stem, t=t, rpm=rpm, lph=lph, kt=sog * MS_KT,
                     gas=gas, cruise=cruise, loiter=loiter))
    print(f'{p.stem}: cruise {cruise.sum():,} ({cruise.sum()/3600:.2f} h)  '
          f'loiter {loiter.sum():,} ({loiter.sum()/3600:.2f} h)')

# ------------------------------------------------------- physics guard on fuel
# A flow meter can lie, and on 2026-08-14 one did: 6,112 samples reporting
# 12-14 L/h at 1889 rpm and 4.1 kt, where every other day burns 2.8 L/h at the
# same revs. Nothing in the cruise filter above could see it — the readings were
# steady, straight and fast enough to look like textbook cruise — so the fit
# swallowed them and moved the curve 11% at the edges.
#
# The guard is that a sample's fuel rate must agree with what the SAME RPM burns
# everywhere else. Median and MAD are taken over the POOLED data so that one bad
# day cannot define its own normal, and the threshold is deliberately loose:
# this is here to reject the physically impossible, not to tidy the scatter that
# sea state and loading legitimately produce.
GUARD_MAD = 6.0        # reject beyond this many MAD from the bin median
GUARD_BIN = 100        # rpm


def mad(x):
    """Median absolute deviation, scaled to be comparable with a std dev."""
    return 1.4826 * float(np.median(np.abs(x - np.median(x))))


_pr = np.concatenate([f['rpm'][f['cruise']] for f in DAYS])
_pl = np.concatenate([f['lph'][f['cruise']] for f in DAYS])
_edges = np.arange(1200, 3200 + GUARD_BIN, GUARD_BIN)
_lim = {}
for _lo in _edges[:-1]:
    _m = (_pr >= _lo) & (_pr < _lo + GUARD_BIN)
    if _m.sum() < 120:
        continue
    _med, _s = float(np.median(_pl[_m])), mad(_pl[_m])
    # A degenerate MAD (a bin sitting on one reported value) would reject every
    # neighbour; fall back to a fraction of the median so the band stays real.
    _s = max(_s, 0.15 * _med)
    _lim[_lo] = (_med - GUARD_MAD * _s, _med + GUARD_MAD * _s)

_rejected = []
for f in DAYS:
    keep = f['cruise'].copy()
    idx = np.where(f['cruise'])[0]
    if len(idx):
        b = (np.floor(f['rpm'][idx] / GUARD_BIN) * GUARD_BIN).astype(int)
        lo_hi = np.array([_lim.get(int(x), (-np.inf, np.inf)) for x in b])
        bad = (f['lph'][idx] < lo_hi[:, 0]) | (f['lph'][idx] > lo_hi[:, 1])
        keep[idx[bad]] = False
        if bad.any():
            _rejected.append((f['day'], int(bad.sum()), len(idx),
                              float(np.median(f['lph'][idx][bad]))))
    f['cruise'] = keep

if _rejected:
    print('\nPHYSICS GUARD — samples whose fuel rate disagrees with their own RPM')
    for day, n, of, med in _rejected:
        print(f'  {day}: rejected {n:,} of {of:,} cruise samples '
              f'({n / of:.1%}), median {med:.2f} L/h')
    print(f'  total rejected: {sum(r[1] for r in _rejected):,} samples '
          f'({sum(r[1] for r in _rejected) / 3600:.2f} h)')

rpm = np.concatenate([f['rpm'][f['cruise']] for f in DAYS])
kt = np.concatenate([f['kt'][f['cruise']] for f in DAYS])
lph = np.concatenate([f['lph'][f['cruise']] for f in DAYS])
print(f'\ncruise pool: {len(rpm):,} samples ({len(rpm)/3600:.2f} h)')

print('\nCRUISE BINS (100 rpm, medians)')
print(f'{"rpm bin":>9} {"n":>6} {"SOG kt":>7} {"L/h":>6} {"NM/L":>6} | '
      f'{"2022 kt":>8} {"EM712-law L/h":>13}')
rows = []
for lo in range(1200, 3200, 100):
    m = (rpm >= lo) & (rpm < lo + 100)
    if m.sum() < 120:
        continue
    r_, k_, l_ = np.median(rpm[m]), np.median(kt[m]), np.median(lph[m])
    rows.append((r_, k_, l_, int(m.sum())))
    print(f'{lo:>5}-{lo+99:<4} {m.sum():>6} {k_:>7.2f} {l_:>6.2f} {k_/l_:>6.2f} | '
          f'{B22+M22*r_:>8.2f} {F0T+F1T*r_:>13.2f}')

R = np.array([r[0] for r in rows])
K = np.array([r[1] for r in rows])
L = np.array([r[2] for r in rows])
W = np.array([min(r[3], 3600) for r in rows], float)

# ---- per-day agreement: with data arriving daily, a day that disagrees must
# ---- be visible rather than averaged into the pool.
print('\nPER-DAY AGREEMENT (bins with >=60 samples that day; % vs pooled median)')
print(f'{"day":>12} {"bins":>5} {"fuel dev":>10} {"speed dev":>10}   worst bin')
day_rpm = {f['day']: f['rpm'][f['cruise']] for f in DAYS}
day_kt = {f['day']: f['kt'][f['cruise']] for f in DAYS}
day_lph = {f['day']: f['lph'][f['cruise']] for f in DAYS}
for dname in sorted(day_rpm):
    dr, dk, dl = day_rpm[dname], day_kt[dname], day_lph[dname]
    if len(dr) == 0:
        continue
    fdev, sdev, worst, wbin = [], [], 0.0, ''
    for r_, k_, l_, _n in rows:
        lo = int(r_ // 100) * 100
        m = (dr >= lo) & (dr < lo + 100)
        if m.sum() < 60:
            continue
        df = np.median(dl[m]) / l_ - 1
        ds = np.median(dk[m]) / k_ - 1
        fdev.append(df)
        sdev.append(ds)
        if abs(df) > abs(worst):
            worst, wbin = df, f'{lo}-{lo+99}'
    if fdev:
        print(f'{dname:>12} {len(fdev):>5} {np.mean(np.abs(fdev)):>9.1%} '
              f'{np.mean(np.abs(sdev)):>10.1%}   {worst:+.1%} @ {wbin}')
    else:
        print(f'{dname:>12} {0:>5} {"—":>10} {"—":>10}   (no shared bins)')

print('\nFITS')
(b0, b1), r2s = wpoly(R, K, W, 1)
print(f'  speed: kt  = {b0:+.4f} + {b1:.6f}*RPM        R2={r2s:.4f}')
(q0, q1, q2), r2f = wpoly(R, L, W, 2)
print(f'  fuel : L/h = {q0:+.4f} + {q1:.6f}*RPM + {q2:.3e}*RPM^2   R2={r2f:.4f}')
rpm8 = (8 - b0) / b1
l8 = q0 + q1 * rpm8 + q2 * rpm8 * rpm8
print(f'  at 8 kt: {rpm8:.0f} rpm, {l8:.3f} L/h, {8/l8:.3f} NM/L')

print('\nLOITER (800-1200 rpm)')
# Collected as well as printed: model.json's idle burn is the median OF THE DAY
# MEDIANS, so the day figures are the evidence and belong in the fit output
# rather than being read off a console by hand.
LOITER_MEDIANS, LOITER_RPMS, LOITER_HOURS = [], [], 0.0
for f in DAYS:
    lo = f['loiter']
    if lo.sum() > 300:
        med, rpm_ = float(np.median(f['lph'][lo])), float(np.median(f['rpm'][lo]))
        LOITER_MEDIANS.append(med)
        LOITER_RPMS.append(rpm_)
        LOITER_HOURS += lo.sum() / 3600
        print(f'  {f["day"]}: median {med:.2f} L/h at {rpm_:.0f} rpm, '
              f'{lo.sum()/3600:.2f} h')
if LOITER_MEDIANS:
    print(f'  across {len(LOITER_MEDIANS)} days: median of day medians '
          f'{np.median(LOITER_MEDIANS):.2f} L/h, {LOITER_HOURS:.1f} h observed')

print('\nGAUGE CALIBRATION (flow-meter litres per indicated point)')
# Litres per point is a SLOPE measured down a drawdown, so it is only defined
# while the level is falling. The moment the data contained refuels — 08-10T11
# rose 15 points, 08-14 rose 39 — treating a day as one span produced a NEGATIVE
# L/pt for the refuel day and a combined figure of 59 L/point against a true
# ~2.06. So every day is now cut at each refill and calibrated within each
# drawdown, and the combined figure sums litres and points over those spans
# only. It never spans a refill, and never spans two days.
#
# Only spans with material burn count: an idle stretch's gauge wanders +/-1-2
# points on essentially zero fuel, and letting one anchor the total moves the
# figure ~10% for no physical reason.
# The segmentation itself lives in tools/drawdown.py, so this script, the gauge
# report and reserve_band.py cannot disagree about what a point is worth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from drawdown import (GAUGE_MIN_L, GAUGE_MIN_PTS,  # noqa: E402
                      drawdown_spans, litres_between)


tot_l = tot_pts = 0.0
band_lo, band_hi = 100.0, 0.0      # the gauge range the calibration actually saw
SPANS = []                         # every usable drawdown, for the sigma below
for f in DAYS:
    t, gas, lph_ = f['t'], f['gas'], f['lph']
    spans = drawdown_spans(t, gas)
    used = []
    for t0, t1, g0_, g1_ in spans:
        litres = litres_between(t, lph_, t0, t1)
        pts = g0_ - g1_
        if litres < GAUGE_MIN_L or pts < GAUGE_MIN_PTS:
            continue
        used.append((litres, g0_, g1_, litres / pts))
        tot_l += litres
        tot_pts += pts
        band_lo, band_hi = min(band_lo, g1_), max(band_hi, g0_)
        SPANS.append({'day': f['day'], 'litres': litres, 'points': pts,
                      'from_pct': g0_, 'to_pct': g1_, 'l_per_point': litres / pts})
    if not used:
        burned = litres_between(t, lph_, t[0], t[-1])
        print(f'  {f["day"]}: {burned:5.1f} L — no usable drawdown '
              f'({len(spans)} span(s)), excluded')
        continue
    for litres, g0_, g1_, rate in used:
        note = f'  {f["day"]}: {litres:5.1f} L over {g0_-g1_:4.1f} pts ' \
               f'({g0_:.0f}%->{g1_:.0f}%) = {rate:.2f} L/pt'
        print(note + ('   [1 of %d spans]' % len(used) if len(used) > 1 else ''))
    if len(spans) > len(used):
        print(f'      ({len(spans) - len(used)} span(s) too small to use)')

if tot_pts > GAUGE_MIN_PTS:
    print(f'  combined: {tot_l:.1f} L over {tot_pts:.1f} points of drawdown '
          f'= {tot_l/tot_pts:.2f} L/point  (band {band_lo:.0f}%-{band_hi:.0f}%)')

out = {
    'speed_vs_rpm': {'b': float(b0), 'm': float(b1), 'r2': float(r2s)},
    'fuel_vs_rpm_quadratic': {'q0': float(q0), 'q1': float(q1), 'q2': float(q2),
                              'r2': float(r2f)},
    'rpm_range': [float(R.min()), float(R.max())],
    'cruise_hours': float(len(rpm) / 3600),
    'bins': [{'rpm': float(r), 'kt': float(k), 'lph': float(l), 'n': int(n)}
             for r, k, l, n in rows],
    # `points` is the sum of drawdown covered, NOT band_hi - band_lo: the spans
    # are separate falls that may revisit the same part of the gauge, so the
    # total travelled is the denominator and the band only says where it looked.
    'gauge': {'litres': float(tot_l), 'points': float(tot_pts),
              'band_pct': [float(band_lo), float(band_hi)],
              'l_per_point': float(tot_l / tot_pts)
              if tot_pts > GAUGE_MIN_PTS else None,
              # Spread across the individual drawdowns, weighted by how much
              # gauge each one covered — a 1-point span is not evidence on the
              # same footing as a 9-point one. This is what model.json carries
              # as l_per_point_sigma.
              'l_per_point_sigma': (
                  float(np.sqrt(np.average(
                      [(s['l_per_point'] - tot_l / tot_pts) ** 2 for s in SPANS],
                      weights=[s['points'] for s in SPANS])))
                  if len(SPANS) > 1 else None),
              'spans': SPANS},
    'loiter': {
        'per_day_lph': [float(x) for x in LOITER_MEDIANS],
        'hours_observed': float(LOITER_HOURS),
        'median_lph': float(np.median(LOITER_MEDIANS)) if LOITER_MEDIANS else None,
        'rpm': float(np.median(LOITER_RPMS)) if LOITER_RPMS else None,
    },
}
with open(CACHE / 'em2040_fit.json', 'w') as fh:
    json.dump(out, fh, indent=1)
print(f'\nsaved {CACHE / "em2040_fit.json"}')
