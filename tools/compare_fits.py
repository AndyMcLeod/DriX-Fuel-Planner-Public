"""Compare a fresh fit against the coefficients currently adopted in model.json.

Usage:
    python tools/fit_em2040.py            # writes tools/rosbags/em2040_fit.json
    python tools/compare_fits.py          # this: new vs adopted, with a verdict

Prints the operational deltas that decide whether to adopt (8 kt burn, survey
range, the endurance-sheet rows) rather than raw coefficient diffs, which are
not interpretable on their own — a large q2 change can be almost invisible in
band, and a small one can matter at the edges.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
NEW = HERE / 'rosbags' / 'em2040_fit.json'
MODEL = HERE.parent / 'model.json'

new = json.load(open(NEW))
m = json.load(open(MODEL))
cur = m['gondolas']['options']['em2040']

nS, nF = new['speed_vs_rpm'], new['fuel_vs_rpm_quadratic']
cS, cF = cur['speed_vs_rpm'], cur['fuel_vs_rpm']


def kt_of(S, rpm):
    return S['b'] + S['m'] * rpm


def lph_of(F, rpm):
    q0 = F.get('q0'); q1 = F.get('q1'); q2 = F.get('q2', 0.0)
    return q0 + q1 * rpm + q2 * rpm * rpm


def rpm_for(S, kt):
    return (kt - S['b']) / S['m']


print('=' * 78)
print('COEFFICIENTS')
print('=' * 78)
print(f'  speed b : {cS["b"]:+.6f} -> {nS["b"]:+.6f}')
print(f'  speed m : {cS["m"]:.8f} -> {nS["m"]:.8f}')
print(f'  R2      : {cS.get("r2", float("nan")):.4f} -> {nS["r2"]:.4f}')
print(f'  fuel q0 : {cF["q0"]:+.6f} -> {nF["q0"]:+.6f}')
print(f'  fuel q1 : {cF["q1"]:+.8f} -> {nF["q1"]:+.8f}')
print(f'  fuel q2 : {cF["q2"]:.6e} -> {nF["q2"]:.6e}')
print(f'  R2      : {cF.get("r2", float("nan")):.4f} -> {nF["r2"]:.4f}')
print(f'  cruise hours: {new["cruise_hours"]:.2f} (this fit)')

print()
print('=' * 78)
print('OPERATIONAL DELTAS — what actually changes for a planner or a sheet')
print('=' * 78)
print(f'  {"speed":>6} {"rpm now":>9} {"rpm new":>9} {"L/h now":>9} {"L/h new":>9} '
      f'{"NM/L now":>9} {"NM/L new":>9} {"delta":>8}')
worst = 0.0
for v in (5, 6, 7, 8, 9, 10):
    rc, rn = rpm_for(cS, v), rpm_for(nS, v)
    lc, ln = lph_of(cF, rc), lph_of(nF, rn)
    ec, en = v / lc, v / ln
    d = en / ec - 1
    worst = max(worst, abs(d))
    print(f'  {v:>6} {rc:>9.0f} {rn:>9.0f} {lc:>9.3f} {ln:>9.3f} '
          f'{ec:>9.3f} {en:>9.3f} {d:>+8.1%}')

print()
print(f'  worst efficiency shift across 5-10 kt: {worst:.1%}')
# Capacity and reserve from model.json — this script used to carry its own
# copies, which is exactly how a policy change gets missed in one place.
import json as _json
with open(Path(__file__).resolve().parent.parent / 'model.json', encoding='utf-8') as _fh:
    _MJ = _json.load(_fh)
CAP = next(o['litres'] for o in _MJ['capacity_options']['options'] if o['key'] == 'dd2024')
RES = _MJ['reserve']['default_fraction']
usable = CAP * (1 - RES)
r8c, r8n = rpm_for(cS, 8), rpm_for(nS, 8)
e8c, e8n = 8 / lph_of(cF, r8c), 8 / lph_of(nF, r8n)
print(f'  planning range at 8 kt on {CAP:.0f} L to the {RES:.0%} reserve: '
      f'{usable*e8c:.0f} NM -> {usable*e8n:.0f} NM ({usable*(e8n-e8c):+.0f} NM)')
print(f'  endurance at 8 kt: {usable/lph_of(cF, r8c):.1f} h -> '
      f'{usable/lph_of(nF, r8n):.1f} h')

print()
if worst < 0.02:
    print('  VERDICT: <2% everywhere — the new data confirms the adopted curve.')
    print('  Adopting is optional; the honest reason to adopt is the wider')
    print('  evidence base, not a changed answer.')
elif worst < 0.10:
    print('  VERDICT: material (2-10%). Adopt, and say what moved and why.')
else:
    print('  VERDICT: LARGE (>10%). Do not adopt blind — find out which days or')
    print('  bins drive it first (see the per-day agreement table in fit_em2040).')
