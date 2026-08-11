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
for f in DAYS:
    lo = f['loiter']
    if lo.sum() > 300:
        print(f'  {f["day"]}: median {np.median(f["lph"][lo]):.2f} L/h at '
              f'{np.median(f["rpm"][lo]):.0f} rpm, {lo.sum()/3600:.2f} h')

print('\nGAUGE CALIBRATION (flow-meter litres per indicated point)')
# Only days with material burn set the endpoints: an idle day's gauge wanders
# +/-1-2 points on essentially zero fuel, and letting it anchor the combined
# span moves the figure ~10% for no physical reason.
active = []
for f in DAYS:
    t, gas, lph_ = f['t'], f['gas'], f['lph']
    dt = np.diff(t)
    good = (dt > 0) & (dt < 30)
    litres = float(np.sum(lph_[:-1][good] * dt[good]) / 3600)
    g0_ = float(np.median(gas[t < t[0] + 600]))
    g1_ = float(np.median(gas[t > t[-1] - 600]))
    if litres < 2.0:
        print(f'  {f["day"]}: {litres:5.1f} L — idle day, excluded from the calibration')
        continue
    active.append((litres, g0_, g1_))
    print(f'  {f["day"]}: {litres:5.1f} L over {g0_-g1_:.1f} pts '
          f'({g0_:.0f}%->{g1_:.0f}%) = {litres/(g0_-g1_):.2f} L/pt')
tot_l = sum(a[0] for a in active)
g_start, g_end = (active[0][1], active[-1][2]) if active else (0.0, 0.0)
if g_start - g_end > 1:
    print(f'  combined: {tot_l:.1f} L over {g_start:.0f}%->{g_end:.0f}% '
          f'= {tot_l/(g_start-g_end):.2f} L/point')

out = {
    'speed_vs_rpm': {'b': float(b0), 'm': float(b1), 'r2': float(r2s)},
    'fuel_vs_rpm_quadratic': {'q0': float(q0), 'q1': float(q1), 'q2': float(q2),
                              'r2': float(r2f)},
    'rpm_range': [float(R.min()), float(R.max())],
    'cruise_hours': float(len(rpm) / 3600),
    'bins': [{'rpm': float(r), 'kt': float(k), 'lph': float(l), 'n': int(n)}
             for r, k, l, n in rows],
    'gauge': {'litres': float(tot_l), 'band_pct': [float(g_end), float(g_start)],
              'l_per_point': float(tot_l / (g_start - g_end))
              if g_start - g_end > 1 else None},
}
with open(CACHE / 'em2040_fit.json', 'w') as fh:
    json.dump(out, fh, indent=1)
print(f'\nsaved {CACHE / "em2040_fit.json"}')
