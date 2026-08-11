"""What is known about the tank gauge BELOW the calibrated band — and what a
drawdown would have to cover to settle it.

Usage:
    python tools/reserve_band.py [CACHE_DIR]        # default tools/rosbags

The reserve floor is where every plan is judged, and since v2.4.0 it is the
constraint that actually binds: mission fuel is `(start_pct - reserve_pct) x
L/point`, so the gauge scale down there sets the endurance of every sortie.
Every gauge measurement to date comes from the top of the tank. This script
reports the size of that gap, what the remaining evidence still constrains, what
the gap is worth in litres and hours, and what an experiment would need to cover.

It re-answers itself as data arrives: the lowest observed level is READ FROM THE
CACHES, never assumed, so the first day that dips below the calibrated band
changes the verdict and the script computes the new calibration on the spot.

Read-only. Nothing here writes to model.json — if a drawdown lands, read the
output, then update the model deliberately.
"""
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from engine import Model  # noqa: E402
from drawdown import days, MATERIAL_L, SIG_PTS  # noqa: E402

CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / 'rosbags'

# --------------------------------------------------------------------------- #
#  SOURCE OBSERVATION — the DD2024 refuel, as logged. Transcribed, and
#  cross-checked against model.json below so the two cannot drift apart.
# --------------------------------------------------------------------------- #
DD_L, DD_FROM, DD_TO_LO, DD_TO_HI = 185.0, 9.0, 95.0, 100.0


M = Model()
D = M.data
GC = D['gauge_calibration']
LPP, SIG = GC['l_per_point'], GC['l_per_point_sigma']
BAND_LO, BAND_HI = GC['band_pct']
RES_PCT = D['reserve']['default_fraction'] * 100.0
GONDOLA = D['gondolas'].get('default', 'em2040')
KT = D['references']['survey_speed_kt']
LPH8 = M.fuel_rate_lph(M.rpm_for_speed(KT, GONDOLA), GONDOLA)
NMPL8 = KT / LPH8
LOITER = D['gondolas']['options'][GONDOLA]['loiter']['lph']


DAYS = days(CACHE)
ACTIVE = [d for d in DAYS if d['total'] >= MATERIAL_L]
LOWEST = min(d['lowest'] for d in DAYS)
GAP_PTS = LOWEST - RES_PCT

print('=' * 78)
print(f'RESERVE-BAND STATUS   ({len(DAYS)} cached days, {len(ACTIVE)} with material burn)')
print('=' * 78)
for d in ACTIVE:
    pts = d['g0'] - d['g1']
    lpp = d['total'] / pts if pts > 0.5 else float('nan')
    print(f'  {d["day"]}   {d["g0"]:5.1f} -> {d["g1"]:4.1f}%   '
          f'{d["total"]:5.2f} L   {lpp:5.2f} L/pt   (dipped to {d["lowest"]:.0f}%)')

# Consistency rail: this script must reproduce the adopted calibration.
#
# Pool ONLY the days lying inside the calibrated band. Pooling every active day
# would compare a wider span against a figure fitted to a narrower one, and the
# rail would then abort on the first genuine drawdown — precisely the day this
# script exists for. The check is that we agree about the SAME band.
IN_BAND = [d for d in ACTIVE
           if d['g1'] >= BAND_LO - 0.5 and d['g0'] <= BAND_HI + 0.5]
if IN_BAND:
    pooled_hi = max(d['g0'] for d in IN_BAND)
    pooled_lo = min(d['g1'] for d in IN_BAND)
    pooled = sum(d['total'] for d in IN_BAND) / (pooled_hi - pooled_lo)
    if abs(pooled - LPP) > 0.02:
        raise SystemExit(
            f'INCONSISTENT: caches give {pooled:.3f} L/pt over '
            f'{pooled_lo:.0f}-{pooled_hi:.0f}%, model.json says {LPP:.2f}. '
            f'One of them has moved — reconcile before trusting anything below.')
    print(f'\n  pooled {pooled:.3f} L/pt over {pooled_lo:.0f}-{pooled_hi:.0f}% '
          f'— agrees with model.json ({LPP:.2f} +/- {SIG:.2f})')
else:
    print(f'\n  no cached day lies inside the calibrated '
          f'{BAND_LO:.0f}-{BAND_HI:.0f}% band; cannot cross-check model.json here.')

print()
print(f'  Calibrated band      : {BAND_LO:.0f}-{BAND_HI:.0f}%  '
      f'({BAND_HI - BAND_LO:.0f} of 100 points)')
print(f'  Lowest level logged  : {LOWEST:.0f}%')
print(f'  Reserve floor        : {RES_PCT:.0f}%')

# --------------------------------------------------------------------------- #
#  Has a drawdown happened? Everything below branches on this.
# --------------------------------------------------------------------------- #
NEW = [d for d in ACTIVE if d['g1'] < BAND_LO - 1.0]
print()
if not NEW:
    print(f'  >>> NO DRAWDOWN DATA. {GAP_PTS:.0f} points below the lowest datum have')
    print(f'      never been observed, and the reserve band has no calibration at all.')
else:
    print(f'  >>> DRAWDOWN DATA PRESENT on {len(NEW)} day(s) — calibrating below '
          f'{BAND_LO:.0f}%:')
    for d in NEW:
        pts = d['g0'] - d['g1']
        lpp = d['total'] / pts
        s = lpp * SIG_PTS / pts
        dev = (lpp - LPP) / math.hypot(s, SIG)
        print(f'      {d["day"]}  {d["g0"]:.0f}->{d["g1"]:.0f}%  '
              f'{lpp:.3f} +/- {s:.3f} L/pt   {dev:+.1f} sigma vs the top band '
              f'-> {"DIFFERENT" if abs(dev) >= 2 else "consistent"}')
    print('      Update gauge_calibration in model.json deliberately, not from here.')

# --------------------------------------------------------------------------- #
#  What the DD2024 refuel still constrains about the unmeasured remainder.
# --------------------------------------------------------------------------- #
span_lo, span_hi = DD_TO_LO - DD_FROM, DD_TO_HI - DD_FROM
span = (span_lo + span_hi) / 2.0
lam_dd = DD_L / span
sig_dd = (DD_L / span_lo - DD_L / span_hi) / math.sqrt(12.0)   # level ambiguity
recorded = GC['cross_check']['dd2024_refuel_l_per_point']
if abs(lam_dd - recorded) > 0.01:
    raise SystemExit(f'DD2024 source constants give {lam_dd:.3f} L/pt but '
                     f'model.json records {recorded}. Reconcile.')

meas_pts = BAND_HI - BAND_LO
rest_pts = span - meas_pts
lam_rest = (DD_L - meas_pts * LPP) / rest_pts
sig_rest = math.hypot(meas_pts * SIG, 0.0) / rest_pts
sig_comb = math.sqrt(sig_rest ** 2 + SIG ** 2 + sig_dd ** 2)
dev = (lam_rest - LPP) / sig_comb

print()
print('-' * 78)
print('WHAT THE DD2024 REFUEL STILL CONSTRAINS')
print('-' * 78)
print(f'  {DD_L:.0f} L over {DD_FROM:.0f}% -> {DD_TO_LO:.0f}-{DD_TO_HI:.0f}% = '
      f'{span:.1f} pts  ->  {lam_dd:.3f} +/- {sig_dd:.3f} L/pt whole-range')
print(f'  Remove the {meas_pts:.0f} calibrated pts at {LPP:.2f}: the other '
      f'{rest_pts:.1f} pts average {lam_rest:.3f} L/pt')
print(f'  Difference from the measured band: {lam_rest - LPP:+.3f} = {dev:+.2f} sigma'
      f'  ->  {"RESOLVED" if abs(dev) >= 2 else "NOT resolved"}')
print('  This is CONSISTENCY, not calibration: it says the low band is not wildly')
print('  different. It cannot say what it is. Only a drawdown can.')

# --------------------------------------------------------------------------- #
#  Exposure — what the uncalibrated points are worth on every plan.
#
#  This section was rewritten when reading (A) was adopted. Under (B) the free
#  parameter was the LEVEL of the unmeasured rate, and mission fuel scaled with
#  it directly. Under (A) the drawings pin the integral at tank_volume, so the
#  level is not free at all — only the SHAPE is. Redistributing litres between
#  the band above the floor and the band below it still moves mission fuel, and
#  the sign is the counter-intuitive part.
# --------------------------------------------------------------------------- #
PROF = M.gauge_profile
VOL = M.tank_volume_l
plan_pts = 100.0 - RES_PCT
uncal = plan_pts - meas_pts
base = PROF.litres_between(RES_PCT, 100.0)

print()
print('-' * 78)
print(f'EXPOSURE — {uncal:.0f} of the {plan_pts:.0f} planning points are uncalibrated')
print('-' * 78)
if VOL and M.gauge_reading == 'A':
    below = [s for s in PROF.segments if s[1] <= BAND_LO]      # 0 .. band_lo
    above = [s for s in PROF.segments if s[0] >= BAND_HI]      # band_hi .. 100
    lo_pts = sum(hi - lo for lo, hi, _ in below)
    hi_pts = sum(hi - lo for lo, hi, _ in above)
    lo_rate = below[0][2] if below else 0.0
    unmeasured_l = VOL - meas_pts * LPP
    print(f'  Reading (A): the drawings pin the whole gauge at {VOL:.0f} L, so the')
    print(f'  unmeasured {lo_pts + hi_pts:.0f} points hold {unmeasured_l:.1f} L between them')
    print(f'  no matter how it is distributed. Only the SHAPE is free.')
    print()
    print(f'  {"bottom band is":>16} {"L/pt below":>11} {"L/pt above":>11} '
          f'{"mission L":>10} {"vs assumed":>11} {"NM":>7}')
    for err in (-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20):
        lam_lo = lo_rate * (1 + err)
        lam_hi = (unmeasured_l - lo_pts * lam_lo) / hi_pts if hi_pts else 0.0
        if lam_hi <= 0:
            continue
        # Invariant: every row must still hold the drawing volume across the
        # whole gauge. If it does not, the redistribution above is wrong and
        # the whole table is meaningless.
        whole = lo_pts * lam_lo + meas_pts * LPP + hi_pts * lam_hi
        if abs(whole - VOL) > 1e-6:
            raise SystemExit(f'shape row {err:+.0%} holds {whole:.3f} L, not '
                             f'{VOL:.1f} — the redistribution is broken')
        fuel = (lo_pts - RES_PCT) * lam_lo + meas_pts * LPP + hi_pts * lam_hi
        mark = '   <- as assumed' if err == 0 else ''
        print(f'  {err:>15.0%} {lam_lo:11.2f} {lam_hi:11.2f} {fuel:10.1f} '
              f'{fuel / base - 1:>+11.1%} {fuel * NMPL8:7.0f}{mark}')
    lam_lo = lo_rate * 1.10
    lam_hi = (unmeasured_l - lo_pts * lam_lo) / hi_pts
    swing = abs(((lo_pts - RES_PCT) * lam_lo + meas_pts * LPP + hi_pts * lam_hi) - base)
    print(f'\n  A +/-10% shape error is +/-{swing:.0f} L, +/-{swing / LPH8:.1f} h, '
          f'+/-{swing * NMPL8:.0f} NM at {KT:.0f} kt.')
    print(f'  Note the SIGN: a RICHER bottom band means LESS mission fuel, because')
    print(f'  more of the fixed {VOL:.0f} L sits below the floor where you cannot reach it.')
else:
    print(f'  Reading (B): the unmeasured rate is a free level, not a shape.')
    print(f'  {"if lower band is":>17} {"mission L":>10} {"vs assumed":>11} '
          f'{"hours":>7} {"NM":>7}')
    for err in (-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20):
        fuel = meas_pts * LPP + uncal * LPP * (1 + err)
        mark = '   <- as assumed' if err == 0 else ''
        print(f'  {err:>16.0%} {fuel:10.1f} {fuel / base - 1:>+11.1%} '
              f'{fuel / LPH8:7.1f} {fuel * NMPL8:7.0f}{mark}')
    swing = uncal * LPP * 0.10
    print(f'\n  A +/-10% error down there is +/-{swing:.0f} L, '
          f'+/-{swing / LPH8:.1f} h, +/-{swing * NMPL8:.0f} NM at {KT:.0f} kt.')

# --------------------------------------------------------------------------- #
#  The experiment.
# --------------------------------------------------------------------------- #
print()
print('-' * 78)
print('WHAT A DRAWDOWN WOULD HAVE TO COVER')
print('-' * 78)
print(f'  Gauge quantises to 1 point, so a span carries ~{SIG_PTS} pt of '
      f'endpoint error;')
print(f'  precision on L/pt is {SIG_PTS}/span. From {LOWEST:.0f}% downward:')
print()
print(f'  {"target":>7} {"span":>7} {"down to":>8} {"fuel":>7} '
      f'{"h @{:.0f}kt".format(KT):>8} {"h @loiter":>10}')
# Fuel for a span is the PROFILE INTEGRAL, not span x L/point: under reading (A)
# the band below the calibrated one is richer, so a drawdown costs more fuel and
# more time than the flat scale suggests.
for target in (0.05, 0.03, 0.02, 0.01):
    sp = SIG_PTS / target
    to = LOWEST - sp
    fuel = PROF.litres_between(max(0.0, to), LOWEST)
    note = '' if to >= 0 else '   (below empty — unreachable in one run)'
    print(f'  {target:>6.0%} {sp:7.1f} {to:8.1f} {fuel:7.1f} '
          f'{fuel / LPH8:8.1f} {fuel / LOITER:10.1f}{note}')

for label, sp in (('FULL', LOWEST - RES_PCT), ('HALF', (LOWEST - RES_PCT) / 2)):
    fuel = PROF.litres_between(LOWEST - sp, LOWEST)
    print(f'\n  {label} drawdown {LOWEST:.0f}% -> {LOWEST - sp:.0f}%  '
          f'({sp:.0f} pts, ~{fuel:.0f} L)')
    print(f'    precision {SIG_PTS / sp:.1%} on L/pt · {fuel / LPH8:.0f} h at '
          f'{KT:.0f} kt · {fuel / LOITER:.0f} h at loiter')
    print(f'    leaves {max(0.0, LOWEST - sp - RES_PCT):.0f} points still uncalibrated')

print()
print('  No new instrumentation: t3_fuel_lph and vs_gas_pct are already recorded')
print('  every day. This is an operational decision, not an engineering one.')
