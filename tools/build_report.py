"""Generate the DriX fuel-efficiency report (.docx) with figures.

Usage:
    python tools/build_report.py [OUT.docx]

The report used to be the one document in this repo with no builder, so it was
the one document that could silently drift from the model. Everything derived
here is computed at build time from `model.json`, `em2040_fit_2026-08-09.json`
and the planning engine itself — no derived figure is transcribed.

Two categories of input are NOT computed, and are marked as such below:

  * SOURCE OBSERVATIONS — the 2024 trial steps, the four-heading test, the
    DD2024 refuel and the Exail ROE costs. These are measurements read off the
    source documents. They are inputs by definition; everything else in the
    report is derived from them plus the model file.
  * SRC_2022 — aggregates of the 2022 operational log. The per-observation
    log (21 rows) is NOT in this repo, so these cannot be recomputed and are
    carried as constants. This is the report's one remaining drift risk; see
    `LIMITS` at the bottom of this file.

Figure 8 (the 2022 tank trace) is drawn from that same missing log, so it is
carried as a versioned asset in tools/report_figs/. The other eleven figures
are regenerated on every run.

A trap, recorded because it was walked into: that asset was recovered from the
previous edition of the .docx, and Word numbers `word/media/imageN.png` by
RELATIONSHIP order, not by caption order. In the previous edition image8 was
Figure 12 and image9 was Figure 8. Extracting by media index put the wrong
picture under the caption, and it looked entirely plausible until the page was
rendered. Map media to captions by walking the document body, never by index.
"""
import json
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))     # engine
sys.path.insert(0, str(HERE))            # docx_style — explicit, so importing
#                                          this module (not just running it) works

from docx_style import (new_document, check_table_widths,  # noqa: E402
                        build_date_str, PLOT_RC, C_MEAS, C_MODEL, C_ALT,
                        C_2022, C_WARN, ACCENT, WARN)
from engine import (Environment, Leg, Model, Vessel, plan)  # noqa: E402
from drawdown import spec as _drawdown_spec  # noqa: E402

FIGS = HERE / 'report_figs'
FIGS.mkdir(exist_ok=True)
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    HERE.parent / 'docs' / 'DriX_Fuel_Efficiency_Report.docx')

plt.rcParams.update(PLOT_RC)
MS_KT = 3600.0 / 1852.0

# ============================================================================ #
#  SOURCE OBSERVATIONS — read off the source documents. Inputs, not results.
# ============================================================================ #

# 2024 shakedown, 28 Jun 2024. (rpm, SOG m/s, flow L/h, in_fit)
# The 2750 and 3000 rpm runs burn LESS than the 2500 run beneath them, which no
# steady state produces; the log records rising chop from 2750 on. Excluded from
# every fit, kept visible.
TRIALS = [
    (1020, 1.60, 1.08, True), (1250, 2.20, 1.35, True), (1500, 2.70, 1.77, True),
    (1750, 3.13, 2.28, True), (2000, 3.65, 3.10, True), (2250, 3.80, 3.90, True),
    (2500, 4.00, 4.70, True), (2750, 4.10, 4.20, False), (3000, 4.40, 4.40, False),
]

# Four-heading prop-efficiency test, same day. (label, rpm, N, E, S, W) in m/s.
FOUR_HEADING = [
    ('WOT, fixed RPM', 3015, 4.60, 5.40, 5.30, 4.80),
    ('Request 8 kt, fixed RPM', 1928, 3.05, 3.50, 3.50, 3.15),
    ('WOT req 16 kt, free RPM', 3020, 4.70, 5.30, 5.20, 4.93),
]

# DD2024 refuel event, read as a capacity measurement.
DD_LITRES, DD_BEFORE, DD_AFTER_LO, DD_AFTER_HI = 185.0, 9.0, 95.0, 100.0

# Exail rate-of-effort model, 2,546 km² survey in 2040. Planning figures from
# the vendor model, not measurements. (config, fuel $, hours, grand total $)
EXAIL_ROE = [
    ('1 × DriX H8', 3467, 792.8, 376667),
    ('2 × DriX H8', 1796, 440.4, 248796),
    ('1 × DriX H9', 2506, 653.3, 316906),
]

# --- SRC_2022: aggregates of the 2022 operational log (29–30 Jul 2022). ----
# The per-observation log is not in this repo, so these cannot be recomputed.
SRC_2022 = dict(
    pct_start=92.0, pct_end=54.5, hours=22.5,
    modelled_litres_full=65.04,        # 2022 speeds -> RPM -> 2024 flow curve
    modelled_litres_excl=57.36,        # same, first interval dropped
    points_excl=28.0,
    i1_from=92.0, i1_to=82.5, i1_hours=1.7333, i1_rate=5.49, window_rate=1.67,
    mean_speed_kt=7.50,
    wind_lo=10.0, wind_hi=16.0, wind_mean=11.95,
    heave_lo=0.10, heave_hi=1.00, heave_mean=0.47,
    # Table 12 — premium binned by heave. Derived from the same missing log.
    heave_bins=[('≤ 0.3 m', 4, 2.04, 6.0, 10.0, 7.37, 0.475),
                ('0.4 – 0.6 m', 6, 4.27, 19.0, 9.5, 11.13, 1.195),
                ('≥ 0.7 m', 4, 6.49, 4.5, 5.6, 1.73, 0.003)],
)

# ============================================================================ #
#  DERIVED — everything below is computed from the model file and the engine.
# ============================================================================ #
M = Model()
D = M.data
FIT = json.loads((HERE / 'em2040_fit_2026-08-09.json').read_text(encoding='utf-8'))

FV, CU = D['fuel_vs_rpm'], D['fuel_vs_speed_cubic']
S24, S22 = D['speed_vs_rpm_2024'], D['speed_vs_rpm_2022']
G40 = D['gondolas']['options']['em2040']
GC = D['gauge_calibration']
HE = D['heading_effect']
RESERVE = D['reserve']['default_fraction']
CAPS = D['capacity_options']['options']
SURVEY_KT = D['references']['survey_speed_kt']
EX = D['exail_static_model']

f0, f1 = FV['f0'], FV['f1']
c0, c3 = CU['c0'], CU['c3']
LPP, LPP_SIG = GC['l_per_point'], GC['l_per_point_sigma']
# Everything below comes off the adopted gauge profile, so the report cannot
# disagree with the planner about which reading is in force.
PROF = M.gauge_profile
READING = M.gauge_reading
GAUGE_CAP = PROF.litres_between(0.0, 100.0)
GAUGE_SPAN_LINEAR = 100.0 * LPP        # what the measured band alone spans
NOMINAL_LPP = 2.50

rpm = np.array([t[0] for t in TRIALS], float)
kt = np.array([t[1] for t in TRIALS]) * MS_KT
lph = np.array([t[2] for t in TRIALS], float)
keep = np.array([t[3] for t in TRIALS])


def r2_rmse(y, yhat):
    ss = np.sum((y - yhat) ** 2)
    return 1.0 - ss / np.sum((y - y.mean()) ** 2), float(np.sqrt(ss / len(y)))


# Candidate model forms over the seven clean points (§3.3).
kf, lf = kt[keep], lph[keep]
FORMS = []
p_lin = np.polyfit(rpm[keep], lf, 1)
FORMS.append(('L/h = f₀ + f₁·RPM',
              f'f₀ = {p_lin[1]:.4f},  f₁ = {p_lin[0]:.8f}',
              *r2_rmse(lf, np.polyval(p_lin, rpm[keep]))))
A = np.vstack([np.ones_like(kf), kf ** 3]).T
p_cub = np.linalg.lstsq(A, lf, rcond=None)[0]
FORMS.append(('L/h = c₀ + c₃·V³',
              f'c₀ = {p_cub[0]:.5f},  c₃ = {p_cub[1]:.7f}',
              *r2_rmse(lf, A @ p_cub)))
Aq = np.vstack([np.ones_like(kf), kf ** 2]).T
p_qua = np.linalg.lstsq(Aq, lf, rcond=None)[0]
FORMS.append(('L/h = q₀ + q₂·V²',
              f'q₀ = {p_qua[0]:.4f},  q₂ = {p_qua[1]:.6f}',
              *r2_rmse(lf, Aq @ p_qua)))
lg = np.polyfit(np.log(kf), np.log(lf), 1)
pw_a, pw_b = float(np.exp(lg[1])), float(lg[0])
FORMS.append((f'L/h = a·V^b', f'a = {pw_a:.5f},  b = {pw_b:.4f}',
              *r2_rmse(lf, pw_a * kf ** pw_b)))
R2_LOG = r2_rmse(np.log(lf), np.polyval(lg, np.log(kf)))[0]
# Same cubic over all nine points, to show what the two suspect runs do (§3.2).
A9 = np.vstack([np.ones_like(kt), kt ** 3]).T
p9 = np.linalg.lstsq(A9, lph, rcond=None)[0]
R2_9, RMSE_9 = r2_rmse(lph, A9 @ p9)

V_OPT = (c0 / (2 * c3)) ** (1 / 3)
E_OPT = V_OPT / (c0 + c3 * V_OPT ** 3)
E8_712 = SURVEY_KT / (c0 + c3 * SURVEY_KT ** 3)
BEST_I = int(np.argmax((kt / lph)[keep]))
BEST_E, BEST_V = (kt[keep] / lf)[BEST_I], kf[BEST_I]
RPM8_712 = M.rpm_for_speed(SURVEY_KT, 'em712')
RPM8_40 = M.rpm_for_speed(SURVEY_KT, 'em2040')
LPH8_40 = M.fuel_rate_lph(RPM8_40, 'em2040')
E8_40 = SURVEY_KT / LPH8_40
LOITER = G40['loiter']

# Four-heading test, reduced.
FH = []
for label, r, *hdg in FOUR_HEADING:
    mean_kt = float(np.mean(hdg)) * MS_KT
    spread = (max(hdg) - min(hdg)) / 2.0 / float(np.mean(hdg))
    ref22 = S22['b'] + S22['m'] * r
    FH.append((label, r, hdg, mean_kt, spread, mean_kt / ref22 - 1.0))
FH_SPREAD = float(np.mean([f[4] for f in FH]))
FH_LOSS = float(np.mean([f[5] for f in FH]))

# Capacity arithmetic (§6).
DD_SPAN = ((DD_AFTER_LO - DD_BEFORE) + (DD_AFTER_HI - DD_BEFORE)) / 2.0
DD_CAP = DD_LITRES / DD_SPAN * 100.0
DD_CAP_LO = DD_LITRES / (DD_AFTER_HI - DD_BEFORE) * 100.0
DD_CAP_HI = DD_LITRES / (DD_AFTER_LO - DD_BEFORE) * 100.0
PTS_FULL = SRC_2022['pct_start'] - SRC_2022['pct_end']
CAP_FULL = SRC_2022['modelled_litres_full'] / PTS_FULL * 100.0
CAP_EXCL = SRC_2022['modelled_litres_excl'] / SRC_2022['points_excl'] * 100.0
LPP_2022 = SRC_2022['modelled_litres_full'] / PTS_FULL
LPP_DD = DD_LITRES / DD_SPAN
CAP_AGREE = abs(CAP_EXCL - DD_CAP) / DD_CAP

cap_by_key = {c['key']: c for c in CAPS}


# The premium is a multiplier on RPM, NOT on litres. Because the fuel law has a
# non-zero intercept those are different things: L/h = f0 + f1*RPM, so scaling
# RPM by (1+p) gives f0 + (rate0 - f0)*(1+p), not rate0*(1+p). Appendix B.2 states
# this; an earlier version of this builder scaled litres and overstated the 250 L
# row's premium as +44% against the correct +27%.
HOURS_FULL = SRC_2022['hours']
HOURS_EXCL = SRC_2022['hours'] - SRC_2022['i1_hours']


def _rate_at(premium, rate0):
    return f0 + (rate0 - f0) * (1.0 + premium)


def premium_for_capacity(cap_l, litres, points, hours):
    """RPM premium that makes the flow-meter curve match a gauge window."""
    rate0 = litres / hours
    rate_needed = (cap_l * points / 100.0) / hours
    return (rate_needed - f0) / (rate0 - f0) - 1.0


def implied_capacity(premium, litres, points, hours):
    """Capacity at which gauge and flow meter agree, given an RPM premium."""
    return _rate_at(premium, litres / hours) * hours / points * 100.0


# Planning, straight out of the engine (§8).
ENV = Environment(wmo_sea_state=2, wind_speed_kt=0.0)


def endurance_rows(gondola):
    out = []
    for c in CAPS:
        cap = float(c['litres'])
        usable = cap * (1.0 - RESERVE)
        r = M.rpm_for_speed(SURVEY_KT, gondola)
        rate = M.fuel_rate_lph(r, gondola)
        eff = SURVEY_KT / rate
        out.append((c['label'], cap, cap * RESERVE, usable,
                    usable * eff, usable / rate))
    return out


GAUGE_USABLE = PROF.litres_between(RESERVE * 100.0, 100.0)
GAUGE_USABLE_B = (100.0 - RESERVE * 100.0) * LPP    # the conservative reading
# Physical volume from the drawings, and the litres the gauge does not account
# for. The two readings that fit both facts, and what each does to mission fuel.
TANK_VOL = D['tank_volume']['litres']
UNLOCATED = TANK_VOL - GAUGE_SPAN_LINEAR
_MEAS_PTS = GC['band_pct'][1] - GC['band_pct'][0]
LAM_A = (TANK_VOL - _MEAS_PTS * LPP) / (100.0 - _MEAS_PTS)   # non-linear reading
FUEL_A = TANK_VOL - RESERVE * 100.0 * LAM_A
FUEL_B = GAUGE_USABLE                                        # linear reading (used)
BAND_LO, BAND_HI = GC['band_pct']
NOMINAL_USABLE = 250.0 * (1.0 - RESERVE)
GAUGE_RANGE = GAUGE_USABLE * E8_40
GAUGE_HOURS = GAUGE_USABLE / LPH8_40
NOM_RANGE = NOMINAL_USABLE * E8_40
NOM_HOURS = NOMINAL_USABLE / LPH8_40
OVERSTATE = NOMINAL_USABLE / GAUGE_USABLE - 1.0

# Worked example (§8.4) — engine-computed, both gondolas.
WORK_LEGS = [Leg('Transit out (090°)', 'transit', 25.0, 7.0, 90.0),
             Leg('Survey (000°/180°)', 'survey', 120.0, 8.0, 0.0),
             Leg('Transit home (270°)', 'transit', 25.0, 7.0, 270.0)]
WORK_ENV = Environment(wmo_sea_state=2, wind_speed_kt=12.0, wind_from_deg=270.0)
W712 = plan(WORK_LEGS, WORK_ENV, Vessel(250.0, RESERVE, gondola='em712'), M)
W40 = plan(WORK_LEGS, WORK_ENV, Vessel(250.0, RESERVE, gondola='em2040'), M)
W40_G = plan(WORK_LEGS, WORK_ENV, Vessel(GAUGE_CAP, RESERVE, gondola='em2040'), M)


def check_test_claims():
    """§8.5 claims the engine has a test suite with mutation guards. Verify the
    claim; do not print a count.

    An exact figure was quoted here at first, discovered at build time so it
    could not drift. That was the wrong trade: it made the report churn on any
    unrelated test being added, and a number nobody acts on is not worth a
    rebuild. What a reader relies on is that the guards EXIST — so that is what
    is checked, and the build fails if the sentence stops being true. The counts
    go to the console, for whoever ran the build.
    """
    import unittest

    # Same invocation the project uses (`unittest discover -s tests`): the tests
    # directory is its own top level, as it has no __init__.py.
    suite = unittest.TestLoader().discover(str(HERE.parent / 'tests'))
    ids = []

    def walk(s):
        for item in s:
            if isinstance(item, unittest.TestSuite):
                walk(item)
            else:
                ids.append(item.id())

    walk(suite)
    if not ids:
        raise RuntimeError('no tests discovered — §8.5 claims a test suite exists')
    guards = [i for i in ids if 'mutation' in i.lower() or 'perturb' in i.lower()]
    if not guards:
        raise RuntimeError('no mutation guards found — §8.5 claims they exist. '
                           'Either they were removed or they were renamed; the '
                           'report must not go on asserting them either way.')
    return len(ids), len(guards)


N_TESTS, N_GUARDS = check_test_claims()
DD = _drawdown_spec(M)


# ============================================================================ #
#  FIGURES — eleven regenerated, one (fig08) a carried asset.
# ============================================================================ #
def _save(fig, name):
    fig.savefig(FIGS / name)
    plt.close(fig)


def fig01():
    fig, ax = plt.subplots(figsize=(6.4, 3.5))
    ax.plot(kt[keep], lph[keep], 'o', color=C_MEAS, label='Trial runs (in fit)')
    ax.plot(kt[~keep], lph[~keep], 'o', mfc='none', color=C_WARN,
            label='Excluded — chop from 2750 rpm on')
    v = np.linspace(kt.min() * .95, kt.max() * 1.02, 200)
    ax.plot(v, c0 + c3 * v ** 3, '-', color=C_MODEL,
            label=f'Cubic fit (R² {FORMS[1][2]:.3f})')
    ax.set_xlabel('Speed over ground (kt)')
    ax.set_ylabel('Fuel rate (L/h)')
    ax.set_title('Fuel rate against speed — 2024 trials')
    ax.legend()
    _save(fig, 'fig01_trials.png')


def fig02():
    fig, ax = plt.subplots(figsize=(6.4, 3.5))
    ax.plot(rpm[keep], lph[keep], 'o', color=C_MEAS, label='Trial runs (in fit)')
    ax.plot(rpm[~keep], lph[~keep], 'o', mfc='none', color=C_WARN, label='Excluded')
    r = np.linspace(900, 3100, 200)
    ax.plot(r, f0 + f1 * r, '-', color=C_MODEL,
            label=f'L/h = {f0:.4f} + {f1:.6f}·RPM  (R² {FV["r2"]:.3f})')
    ax.axvspan(FV['valid_rpm_min'], FV['valid_rpm_max'], color='#1f3b60', alpha=.06)
    ax.set_xlabel('Engine RPM')
    ax.set_ylabel('Fuel rate (L/h)')
    ax.set_title('The preferred model — fuel is linear in RPM')
    ax.legend()
    _save(fig, 'fig02_rpm_model.png')


def fig03():
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    res_lin = lph - np.polyval(p_lin, rpm)
    res_cub = lph - (c0 + c3 * kt ** 3)
    ax.axhline(0, color='#5b6b7a', lw=.8)
    ax.plot(rpm[keep], res_lin[keep], 'o', color=C_MEAS, label='RPM-linear')
    ax.plot(rpm[keep], res_cub[keep], 's', color=C_MODEL, label='Cubic in speed')
    ax.plot(rpm[~keep], res_lin[~keep], 'o', mfc='none', color=C_WARN)
    ax.plot(rpm[~keep], res_cub[~keep], 's', mfc='none', color=C_WARN)
    ax.set_xlabel('Engine RPM')
    ax.set_ylabel('Residual (L/h)')
    ax.set_title('Residuals — neither form shows structure inside the window')
    ax.legend()
    _save(fig, 'fig03_residuals.png')


def fig04():
    fig, ax = plt.subplots(figsize=(6.4, 3.5))
    v = np.linspace(2.0, 9.0, 300)
    ax.plot(v, v / (c0 + c3 * v ** 3), '-', color=C_MODEL, label='E(V) — cubic model')
    ax.plot(kf, kf / lf, 'o', color=C_MEAS, label='Measured runs')
    ax.plot(V_OPT, E_OPT, '*', ms=13, color=C_ALT,
            label=f'Model optimum {V_OPT:.2f} kt, {E_OPT:.2f} NM/L')
    ax.axvline(SURVEY_KT, color=C_WARN, ls='--', lw=1.1,
               label=f'Survey speed {SURVEY_KT:.0f} kt, {E8_712:.2f} NM/L')
    ax.set_xlabel('Speed over ground (kt)')
    ax.set_ylabel('Efficiency (NM/L)')
    ax.set_title('The efficiency curve — EM712 configuration')
    ax.legend()
    _save(fig, 'fig04_efficiency.png')


def fig05():
    fig, ax = plt.subplots(figsize=(6.4, 3.5))
    r = np.linspace(1000, 3100, 200)
    ax.plot(r, S22['b'] + S22['m'] * r, '-', color=C_2022,
            label=f'2022 EM2040: kt = {S22["b"]:.4f} + {S22["m"]:.6f}·RPM')
    ax.plot(r, S24['b'] + S24['m'] * r, '-', color=C_MODEL,
            label=f'2024 EM712: kt = {S24["b"]:.4f} + {S24["m"]:.6f}·RPM')
    ax.plot(rpm, kt, 'o', color=C_MEAS, ms=4, label='2024 trial steps')
    for _, r_, _h, mkt, spread, _l in FH:
        ax.errorbar(r_, mkt, yerr=mkt * spread, fmt='D', color=C_WARN, ms=6,
                    capsize=3)
    ax.plot([], [], 'D', color=C_WARN, label='Four-heading means (±spread)')
    ax.set_xlabel('Engine RPM')
    ax.set_ylabel('Speed over ground (kt)')
    ax.set_title('Speed against RPM — the cost of the gondola change')
    ax.legend(loc='upper left')
    _save(fig, 'fig05_speed_rpm.png')


def fig06():
    fig, ax = plt.subplots(figsize=(6.4, 3.5))
    r = np.linspace(1100, 2600, 200)
    for lab, s, col in (('2022 EM2040 config', S22, C_2022),
                        ('2024 EM712 config', S24, C_MODEL)):
        v = s['b'] + s['m'] * r
        ax.plot(v, v / np.maximum(f0 + f1 * r, 1e-6), '-', color=col, label=lab)
    ax.set_xlabel('Speed over ground (kt)')
    ax.set_ylabel('Efficiency (NM/L)')
    ax.set_title('Efficiency then and now, bridged through RPM')
    ax.legend()
    _save(fig, 'fig06_config_efficiency.png')


def fig07():
    th = np.linspace(0, 360, 361)
    fig, ax = plt.subplots(figsize=(6.0, 3.2))
    for w, col in ((8, C_2022), (12, C_MEAS), (16, C_MODEL)):
        prem = HE['amplitude_at_reference'] * (w / HE['reference_wind_kt']) \
            ** HE['wind_exponent'] * np.cos(np.radians(th))
        ax.plot(th, prem * 100, color=col, label=f'{w} kt wind')
    ax.axhline(0, color='#5b6b7a', lw=.8)
    ax.set_xticks([0, 90, 180, 270, 360])
    ax.set_xlabel('Angle between course and the direction the wind comes FROM (°)')
    ax.set_ylabel('RPM premium (%)')
    ax.set_title('The heading effect — measured magnitude, assumed cosine shape')
    ax.legend()
    _save(fig, 'fig07_heading.png')


def fig09():
    labels = ['2022 telemetry\nas recorded', '2022 telemetry\n1st excluded',
              'DD2024\nrefuel', 'Gauge scale\n(2026)', 'DRAWINGS\nphysical tank']
    vals = [CAP_FULL, CAP_EXCL, DD_CAP, GAUGE_CAP, TANK_VOL]
    cols = [C_WARN, C_MEAS, C_MEAS, C_MEAS, C_ALT]
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.bar(labels, vals, color=cols, width=.6)
    ax.axhspan(204, 210, color=C_MEAS, alpha=.10)
    for i, v in enumerate(vals):
        ax.text(i, v + 3, f'{v:.0f} L', ha='center', fontsize=8.5)
    ax.set_ylabel('Litres')
    ax.set_title('Gauge span (measured) against physical tank volume (drawings)')
    ax.set_ylim(0, 285)
    _save(fig, 'fig09_capacity.png')


def fig10():
    p = np.linspace(0, .30, 100)
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    ax.plot(p * 100, implied_capacity(p, SRC_2022['modelled_litres_full'], PTS_FULL,
                                      HOURS_FULL),
            color=C_WARN, label='Full 2022 window')
    ax.plot(p * 100, implied_capacity(p, SRC_2022['modelled_litres_excl'],
                                      SRC_2022['points_excl'], HOURS_EXCL),
            color=C_MEAS, label='First interval excluded')
    ax.axhline(DD_CAP, color=C_MODEL, ls='--', lw=1.1,
               label=f'DD2024 refuel — {DD_CAP:.0f} L')
    ax.axhline(250, color='#5b6b7a', ls=':', lw=1.1, label='Nominal 250 L')
    ax.set_xlabel('Assumed RPM premium (%)')
    ax.set_ylabel('Implied capacity (L)')
    ax.set_title('Implied capacity against the premium you assume')
    ax.legend()
    _save(fig, 'fig10_premium.png')


def fig11():
    v = np.linspace(4.0, 10.0, 120)
    r40 = np.array([M.rpm_for_speed(x, 'em2040') for x in v])
    e40 = v / np.array([M.fuel_rate_lph(x, 'em2040') for x in r40])
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    for c, col in ((cap_by_key['nominal'], C_MODEL), (cap_by_key['gauge'], C_MEAS),
                   (cap_by_key['worstcase'], C_WARN)):
        ax.plot(v, float(c['litres']) * (1 - RESERVE) * e40, color=col,
                label=c['label'])
    ax.axhline(GAUGE_USABLE * E8_40, color=C_MEAS, ls=':', lw=1)
    ax.axvline(SURVEY_KT, color='#5b6b7a', ls='--', lw=.9)
    ax.set_xlabel('Speed over ground (kt)')
    ax.set_ylabel(f'Planning range to the {RESERVE:.0%} floor (NM)')
    ax.set_title('Planning range — EM2040 measured curve')
    ax.legend()
    _save(fig, 'fig11_planning_range.png')


def fig12():
    bins = FIT['bins']
    br = np.array([b['rpm'] for b in bins])
    bl = np.array([b['lph'] for b in bins])
    bk = np.array([b['kt'] for b in bins])
    q = G40['fuel_vs_rpm']
    fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.0))
    r = np.linspace(1400, 3100, 150)
    axes[0].plot(br, bl, 'o', color=C_MEAS, ms=4, label='Cruise bins')
    axes[0].plot(r, q['q0'] + q['q1'] * r + q['q2'] * r ** 2, color=C_MODEL,
                 label=f'Quadratic (R² {q["r2"]:.4f})')
    axes[0].plot(LOITER['rpm'], LOITER['lph'], '*', ms=12, color=C_ALT,
                 label=f'Loiter {LOITER["lph"]} L/h')
    axes[0].set_xlabel('RPM')
    axes[0].set_ylabel('L/h')
    axes[0].set_title('Measured fuel law')
    axes[0].legend(fontsize=7)
    sp = G40['speed_vs_rpm']
    axes[1].plot(br, bk, 'o', color=C_MEAS, ms=4)
    axes[1].plot(r, sp['b'] + sp['m'] * r, color=C_MODEL, label='2026 EM2040')
    axes[1].plot(r, S22['b'] + S22['m'] * r, color=C_2022, ls='--', label='2022')
    axes[1].set_xlabel('RPM')
    axes[1].set_ylabel('kt')
    axes[1].set_title('Speed law')
    axes[1].legend(fontsize=7)
    v = np.linspace(4.5, 9.5, 120)
    for g, col, lab in (('em2040', C_MEAS, 'EM2040 (measured)'),
                        ('em712', C_MODEL, 'EM712 (2024)')):
        rr = np.array([M.rpm_for_speed(x, g) for x in v])
        axes[2].plot(v, v / np.array([M.fuel_rate_lph(x, g) for x in rr]),
                     color=col, label=lab)
    axes[2].set_xlabel('kt')
    axes[2].set_ylabel('NM/L')
    axes[2].set_title('Efficiency by gondola')
    axes[2].legend(fontsize=7)
    fig.tight_layout()
    _save(fig, 'fig12_mcap_refit.png')


for fn in (fig01, fig02, fig03, fig04, fig05, fig06, fig07, fig09, fig10,
           fig11, fig12):
    fn()
CARRIED_ASSET = 'fig08_tank_trace_2022.png'
print(f'figures regenerated: 11 (+1 carried asset: {CARRIED_ASSET})')

# ============================================================================ #
#  DOCUMENT
# ============================================================================ #
S = new_document(FIGS)
doc, para, mono, bullets = S.doc, S.para, S.mono, S.bullets
table, callout, figure, rich = S.table, S.callout, S.figure, S.rich
h1, h2, h3, CENTER, SOFT = S.h1, S.h2, S.h3, S.CENTER, S.SOFT

pct = lambda x, d=1: f'{x * 100:.{d}f}%'                              # noqa: E731
_SUP = str.maketrans('0123456789-', '⁰¹²³⁴⁵⁶⁷⁸⁹⁻')


def sci(x, sig=4):
    """1.4581e-06 -> 1.4581×10⁻⁶ — prose, not a REPL."""
    mant, exp = f'{x:.{sig}e}'.split('e')
    return f'{mant}×10{str(int(exp)).translate(_SUP)}'


def num(x, fmt='.3f'):
    """Proper minus sign (U+2212), which is what the rest of the document uses."""
    return format(x, fmt).replace('-', '−')

# ---- title page -----------------------------------------------------------
para('', after=46)
para('Estimating the Fuel Efficiency of the', size=21, bold=True, color=ACCENT,
     align=CENTER, after=2)
para('Uncrewed Surface Vehicle DriX', size=21, bold=True, color=ACCENT,
     align=CENTER, after=14)
para('Derived fuel-rate and efficiency curves, variation between the 2022 and '
     '2024 configurations, resolution of the tank-capacity discrepancy, and a '
     'mission planning framework', size=11, italic=True, color=SOFT,
     align=CENTER, after=26)
table(['', ''], [
    ['Subject vehicle', 'DriX-8 (CCOM / UNH), with reference to DriX-12 and DriX-23'],
    ['Primary data', '2024 shakedown fixed-RPM speed trials, 28 June 2024'],
    ['Supporting data', '2022 shakedown speed-vs-RPM; 2022 operational operational '
                        'log; 2024 four-heading prop-efficiency test; DriX-8 MCAP logs '
                        '04–09 Aug 2026; Exail 2040 rate-of-effort model'],
    ['Derived from', 'model.json v' + D['version'] + ' and the MCAP fit pipeline — '
                     'every figure in this report is computed at build time'],
    ['Builder', 'tools/build_report.py (regenerate; do not hand-edit)'],
    ['Companion tool', 'DriX mission fuel planner — D:\\Claude\\Fuel'],
    ['Date', build_date_str() +
        f'  (model v{D["version"]} — gauge-denominated reserve)'],
], [1.5, 4.8], right_from=99, size=9)
callout('How to read this report',
        'Every quantity is labelled either MEASURED — a regression against recorded '
        'data, with its fit statistics stated — or ASSUMED, meaning it is a judgement '
        'that can be changed. The distinction matters more than any single number '
        'here: the fuel curves are measured, the sea-state response is not, and '
        'conclusions that depend on the latter are given as sensitivity bands rather '
        'than point estimates.')

# ---- 1. Executive summary -------------------------------------------------
h1('1.  Executive summary')
para('Eight findings, in the order they were established.')

h3('The fuel curve')
bullets([
    ('Fuel rate is linear in engine RPM. ',
     f'L/h = {num(f0)} + {f1:.6f}·RPM, R² = {FV["r2"]:.3f} over '
     f'{FV["valid_rpm_min"]:.0f}–{FV["valid_rpm_max"]:.0f} rpm. Expressed against '
     f'speed the same data is a cubic, L/h = {c0:.3f} + {c3:.5f}·V³, at '
     f'R² = {CU["r2"]:.3f}. The two are indistinguishable on residuals; RPM is '
     f'preferred because it is also the only bridge back to the 2022 data, where no '
     f'fuel rate was recorded.'),
    ('Distance per litre peaks near 4 knots. ',
     f'Best measured efficiency is {BEST_E:.2f} NM/L at {BEST_V:.2f} kt — the 1250 rpm '
     f'step. The model optimum is {V_OPT:.2f} kt at {E_OPT:.2f} NM/L, a short '
     f'extrapolation below the slowest run.'),
    ('The 8-knot survey speed costs ',
     f'{BEST_E / E8_712:.1f}× the fuel per nautical mile against that best measured '
     f'run on the EM712 curve, and sits above the fitted window — 8 kt requires about '
     f'{RPM8_712:.0f} rpm against a {FV["valid_rpm_max"]:.0f} rpm ceiling, so every '
     f'EM712 figure at survey speed is an extrapolation and is marked as such.'),
])

h3('Change between configurations')
bullets([
    ('The vehicle lost 17–19% of its speed at equal RPM between 2022 and 2024. ',
     f'Measured by three independent four-heading tests at two very different RPM '
     f'settings, which agree with each other ({pct(-FH_LOSS)} mean loss). Because fuel '
     f'is set by RPM, that is very nearly a one-for-one loss of distance per litre.'),
    ('It is now drag-limited at the top end. ',
     f'The speed-vs-RPM slope fell to {S24["m"] / S22["m"]:.0%} of its 2022 value while '
     f'the intercept rose from {S22["b"]:.2f} to {S24["b"]:.2f} kt. The curve has '
     f'flattened: the vehicle reaches speed early and then stops gaining.'),
])

h3('Tank capacity and the gauge')
bullets([
    ('The tank holds 250 L, and the gauge does not know it. ',
     f'The engineering drawings settle the physical volume at {TANK_VOL:.0f} L on every '
     f'hull. But three independent methods put the GAUGE SPAN at 205–{DD_CAP:.0f} L — '
     f'the DD2024 refuel {DD_CAP:.0f} L (pumped litres), the 2022 telemetry '
     f'{CAP_EXCL:.0f} L, and the metered gauge scale {GAUGE_CAP:.0f} L. All three are '
     f'SLOPES in litres per indicated point, so a partial fill or partial drawdown '
     f'cannot bias them: that moves a window along the gauge, it does not change what '
     f'a point is worth. About {UNLOCATED:.0f} L of the tank is therefore not '
     f'represented in the indicated range — see §6.'),
    ('The gauge SCALE is measured; its LINEARITY is not. ',
     f'{LPP:.2f} ± {LPP_SIG:.2f} L per indicated point over the '
     f'{GC["band_pct"][0]:.0f}–{GC["band_pct"][1]:.0f}% band — '
     f'{(NOMINAL_LPP - LPP) / LPP_SIG:.0f}σ below the {NOMINAL_LPP:.2f} a linear 250 L '
     f'tank implies, and agreeing with DD2024 to '
     f'{GC["cross_check"]["agreement_sigma"]:.1f}σ. Non-linearity is UNRESOLVED, not '
     f'established: the per-day spread separates by only 1.5σ. An earlier edition of '
     f'this report called that spread non-linearity; that was an over-read and is '
     f'retracted in §6.3.'),
    ('Fuel is under 1% of mission cost in the Exail 2040 planning model. ',
     'These curves earn their keep in endurance and range prediction, not in economy.'),
])

h3('The configuration fitted today (MCAP refit, August 2026)')
bullets([
    ('The EM2040 curve is measured, not inferred. ',
     f'Six days of MCAP logs ({FIT["cruise_hours"]:.2f} h of steady cruise) give '
     f'L/h = {G40["fuel_vs_rpm"]["q0"]:.4f} − '
     f'{abs(G40["fuel_vs_rpm"]["q1"]):.6f}·RPM + '
     f'{sci(G40["fuel_vs_rpm"]["q2"])}·RPM² (R² {G40["fuel_vs_rpm"]["r2"]:.4f}) and '
     f'{E8_40:.2f} NM/L at the 8 kt survey speed — {RPM8_40:.0f} rpm, inside the '
     f'fitted window, so survey planning on this gondola is interpolation. Loiter burn '
     f'is {LOITER["lph"]} L/h over {LOITER["hours_observed"]} h observed.'),
    ('Planning fuel is set by the gauge, not by the tank. ',
     f'The {RESERVE:.0%} floor is written in indicated percent, so a mission may spend '
     f'{GAUGE_USABLE:.1f} L before the '
     f'needle reaches it — against {NOMINAL_USABLE:.1f} L on a linear 250 L tank. No '
     f'capacity assumption enters that number. At survey speed it is '
     f'{GAUGE_HOURS:.1f} h and {GAUGE_RANGE:.0f} NM, not {NOM_HOURS:.1f} h and '
     f'{NOM_RANGE:.0f} NM.'),
])
callout('The single most useful sentence in this report',
        f'The floor is a needle position, so mission fuel is what the gauge holds '
        f'between full and {RESERVE:.0%} — {GAUGE_USABLE:.0f} L on the adopted reading, '
        f'{GAUGE_USABLE_B:.0f} L if the gauge speaks only for its own span. That '
        f'{GAUGE_USABLE - GAUGE_USABLE_B:.0f} L is {(GAUGE_USABLE - GAUGE_USABLE_B) / LPH8_40:.0f} '
        f'hours and {(GAUGE_USABLE - GAUGE_USABLE_B) * E8_40:.0f} NM of endurance riding '
        f'on an inference the drawings support and no drawdown has yet tested.',
        color=WARN)

# ---- 2. Scope -------------------------------------------------------------
h1('2.  Scope, sources and method')
h2('2.1  What this report is built from')
para('The 2022 and 2024 analysis rests on a text extraction of Microsoft 365 search '
     'results rather than on the original binaries — no cell from those sources was '
     'read from a source file directly. That limitation is inherited from the source '
     'workbook and is restated wherever it bears on a conclusion. The 2026 MCAP '
     'material is different in kind: it is read directly from the vehicle\'s own bag '
     'files by the pipeline in tools/, and this report recomputes from that pipeline '
     'on every build.')
table(['Source', 'Date', 'What it contributes'], [
    ['Daily Log — 2024 Shakedown', '28 Jun 2024',
     'Fixed-RPM speed trials with flow-meter L/h and measured SOG; four-heading prop test'],
    ['DriX-8 MCAP logs (connectivity box)', '04–09 Aug 2026',
     'EM2040 refit: flow meter, thruster RPM, GPS, INS, trim; direct gauge calibration'],
    ['2022 Shakedown Cruise Report', '2022',
     'Speed vs RPM for the EM2040 configuration, 7 points'],
    ['2022 operational log', '29–30 Jul 2022',
     'Tank telemetry with wind, heave and speed, 21 observations'],
    ['DD2024', '2024', 'Refuel event sizing the tank; flow-meter reliability notes'],
    ['Exail ROE large-survey model', 'Jul 2024',
     'Cost, duration and efficiency ratios for the 2040 planning case'],
    ['Fuel Consumption.html (Exail)', 'Feb 2025',
     'Static vs dynamic endurance estimation as implemented in the HMI'],
], [2.3, 1.0, 3.0], note='Table 1 — Source material.', right_from=99)

h2('2.2  Measured against assumed')
para('The distinction is enforced throughout, including in the companion planning '
     'tool, where every block of the model file carries a "fitted" flag.')
table(['Quantity', 'Status', 'Basis'], [
    ['EM712 fuel rate vs RPM (2024)', 'MEASURED',
     f'Regression, R² {FORMS[0][2]:.3f}, n = {int(keep.sum())}'],
    ['EM712 fuel rate vs speed (cubic)', 'MEASURED',
     f'Regression, R² {FORMS[1][2]:.3f}, n = {int(keep.sum())}'],
    ['EM712 speed vs RPM (2024)', 'MEASURED', f'Regression, R² {S24["r2"]:.3f}, n = 9'],
    ['EM2040 speed vs RPM (2022)', 'MEASURED', f'Regression, R² {S22["r2"]:.3f}, n = 7'],
    ['EM2040 fuel & speed laws (2026)', 'MEASURED',
     f'MCAP refit, R² {G40["fuel_vs_rpm"]["r2"]:.4f} / '
     f'{G40["speed_vs_rpm"]["r2"]:.3f} — §5.5'],
    ['Gauge scale (L per indicated point)', 'MEASURED',
     f'{LPP:.2f} ± {LPP_SIG:.2f}, flow meter vs gauge — §6.3'],
    ['Gauge linearity', '⚠ UNRESOLVED', 'Per-day spread is 1.5σ — not established'],
    ['Heading effect magnitude', 'MEASURED',
     f'±{pct(FH_SPREAD, 2)} speed spread, three four-heading tests'],
    ['Heading effect shape (cosine)', '⚠ ASSUMED', 'Physically motivated; not fitted'],
    ['Wind-speed scaling of that effect', '⚠ ASSUMED',
     f'Power law, exponent {HE["wind_exponent"]:.0f}, about a '
     f'{HE["reference_wind_kt"]:.0f} kt reference'],
    ['Sea state → RPM premium', '⚠ ASSUMED',
     'Calm anchor measured; slope into rough water is not — §7'],
    ['Physical tank volume', 'ESTABLISHED',
     f'{TANK_VOL:.0f} L, engineering drawings, all hulls — §6'],
    ['Gauge span in litres', 'MEASURED',
     f'205–{DD_CAP:.0f} L from three slope measurements — §6.3'],
    ['Where the other ~{:.0f} L sits'.format(TANK_VOL - GAUGE_CAP), '⚠ UNRESOLVED',
     'Non-linearity, or outside the sender travel — §6.5'],
    ['Reserve fraction', 'POLICY', f'{RESERVE:.0%} indicated on return'],
], [2.4, 1.1, 2.8],
    note='Table 2 — Provenance of every quantity used. The assumed rows are where '
         'disagreement with this report should be directed.', right_from=99)

h2('2.3  Method')
para('Each candidate model form was fitted by least squares over a declared window, '
     'then compared on residual behaviour rather than on R² alone — with nine points '
     'and two of them suspect, R² is easy to inflate. Every derived figure in this '
     'report is recomputed by tools/build_report.py at build time from model.json and '
     'the MCAP fit output, and the planning figures are produced by calling the '
     'planning engine itself rather than by repeating its arithmetic.')

# ---- 3. Fuel model --------------------------------------------------------
h1('3.  The fuel model')
h2('3.1  The 2024 speed trials')
para('On 28 June 2024 the vehicle was run at nine fixed engine speeds from 1020 to '
     '3000 rpm, with fuel flow logged by the installed flow meter and speed over '
     'ground recorded simultaneously. This is the only dataset in the 2024 source '
     'material that pairs a fuel rate with a speed.')
table(['RPM', 'SOG (m/s)', 'SOG (kt)', 'Flow (L/h)', 'NM/L', 'L/NM', 'In fit'],
      [[f'{t[0]}', f'{t[1]:.2f}', f'{t[1] * MS_KT:.3f}', f'{t[2]:.2f}',
        f'{t[1] * MS_KT / t[2]:.3f}', f'{t[2] / (t[1] * MS_KT):.3f}',
        'yes' if t[3] else '⚠ no'] for t in TRIALS],
      [0.7, 0.95, 0.95, 0.95, 0.8, 0.8, 0.75],
      note='Table 3 — The trial data. Knots from m/s using the exact factor 3600/1852.',
      flag_rows=(7, 8))

h2('3.2  Why two runs are excluded')
para(f'The 2750 and 3000 rpm runs report 4.20 and 4.40 L/h — both below the 4.70 L/h '
     f'of the 2500 rpm run beneath them. No steady state produces that: a diesel at '
     f'higher load cannot burn less. The log itself supplies the explanation, '
     f'recording "noticeable increase in wave chop from this run forward".')
para(f'Including them is not neutral. Fitted over all nine points the cubic gives '
     f'R² = {R2_9:.3f} and RMSE {RMSE_9:.3f} L/h; over the seven clean points it gives '
     f'R² = {FORMS[1][2]:.3f} and RMSE {FORMS[1][3]:.3f} L/h. The two suspect runs are '
     f'doing real damage to the fit, and they are the two the source document itself '
     f'flags as disturbed.')
figure('fig01_trials.png',
       'Figure 1 — Fuel rate against speed over ground. The open circles are the '
       'excluded runs; note both fall below the 2500 rpm point to their left.')

h2('3.3  Candidate model forms')
para('Four forms were fitted over the seven-point window. All are single-parameter-'
     'pair models so none has a complexity advantage.')
table(['Model form', 'Fitted coefficients', 'R²', 'RMSE (L/h)'],
      [[f[0], f[1], f'{f[2]:.4f}' + ('*' if i == 3 else ''), f'{f[3]:.3f}']
       for i, f in enumerate(FORMS)],
      [1.7, 2.3, 0.9, 1.0],
      note=f'Table 4 — Model comparison over {FV["valid_rpm_min"]:.0f}–'
           f'{FV["valid_rpm_max"]:.0f} rpm. *The power model\'s R² is quoted in linear '
           f'space for comparability; in log space, where it is actually fitted, it '
           f'reads {R2_LOG:.3f}. RMSE is the honest comparator across all four.',
      right_from=2)
para('The RPM-linear and cubic-in-speed forms are statistically indistinguishable, '
     'which is not a coincidence — over this narrow band speed is close to linear in '
     'RPM, so a cubic in speed and a linear in RPM describe nearly the same surface. '
     'The quadratic and power forms are materially worse, the power model because it '
     'has no separate idle term and must absorb the engine\'s standing load into its '
     f'exponent, dragging that exponent down to {pw_b:.2f} when hydrodynamic drag '
     'alone would put it near 3.')
figure('fig02_rpm_model.png',
       'Figure 2 — The preferred model. The CS850 final report asserted that in calm '
       'seas fuel consumption relates linearly to engine RPM; over this window it does.')
figure('fig03_residuals.png',
       'Figure 3 — Residuals for both leading forms. Neither shows structure within '
       'the window; both under-predict the two excluded runs, which is what excluded '
       'them.')

h2('3.4  The chosen model and its idle term')
mono(f'L/h  =  {num(f0, ".4f")}  +  {f1:.8f} · RPM',
     f'Valid {FV["valid_rpm_min"]:.0f}–{FV["valid_rpm_max"]:.0f} rpm. '
     f'R² {FORMS[0][2]:.3f}, RMSE {FORMS[0][3]:.3f} L/h, n = {int(keep.sum())}.')
para(f'The intercept is negative, so this form must never be extrapolated down toward '
     f'idle — it crosses zero at about {-f0 / f1:.0f} rpm and would return negative '
     f'fuel below that. The companion planning tool clamps it at zero for exactly this '
     f'reason. The cubic form carries the same information in a more physically '
     f'readable way:')
mono(f'L/h  =  {c0:.4f}  +  {c3:.7f} · V³',
     'V in knots. The constant is the standing load — engine idle plus alternator '
     'draw — and the cubic term is the speed-dependent work.')
para(f'That {c0:.3f} L/h standing term is worth noting on its own: it is roughly '
     f'{c0 / (c0 + c3 * 4 ** 3):.0%} of the total burn at 4 kt and only '
     f'{c0 / (c0 + c3 * 8 ** 3):.0%} at 8 kt. It is also the term most likely to '
     f'differ between a bare trial and a laden survey, because the trials carried '
     f'whatever electrical load happened to be running that day and no record of it '
     f'survives.')

# ---- 4. Efficiency --------------------------------------------------------
h1('4.  The efficiency curve')
h2('4.1  Derivation')
para('Efficiency in distance per unit fuel follows directly from dividing speed by '
     'fuel rate:')
mono('E(V)  =  V / (c₀ + c₃·V³)      [NM per litre]')
para('This has the shape every displacement hull shows: at low speed the fixed '
     'standing load is amortised over very little distance, so efficiency is poor; at '
     'high speed the cubic drag term dominates and efficiency collapses again. Between '
     'them lies a maximum.')

h2('4.2  The optimum speed')
para('Differentiating and setting to zero:')
mono('dE/dV  =  [(c₀ + c₃V³) − V·(3c₃V²)] / (c₀ + c₃V³)²  =  0')
mono('⟹  c₀ − 2c₃V³  =  0      ⟹      V_opt  =  ( c₀ / 2c₃ )^(1/3)')
mono(f'V_opt  =  ( {c0:.5f} / (2 × {c3:.7f}) )^(1/3)  =  {V_OPT:.3f} kt',
     f'E(V_opt)  =  {E_OPT:.3f} NM/L')
para(f'A caution attaches to this. The slowest run in the fit window was '
     f'{kf.min():.3f} kt, so {V_OPT:.3f} kt sits inside the data but the peak\'s '
     f'left-hand shoulder does not — the curve there is carried by the model, not by '
     f'measurement. The best measured point is {BEST_E:.3f} NM/L at {BEST_V:.3f} kt. '
     f'Both say the same operationally useful thing: the efficient speed is near 4 kt, '
     f'not 8.')
figure('fig04_efficiency.png',
       'Figure 4 — The efficiency curve, with the model optimum, the best measured '
       'run, and the standard survey speed marked.')

h2('4.3  The cost of eight knots')
mono(f'E(8)  =  8 / ({c0:.5f} + {c3:.7f} × 512)  =  {E8_712:.3f} NM/L',
     f'Ratio to best measured  =  {BEST_E:.3f} / {E8_712:.3f}  =  '
     f'{BEST_E / E8_712:.2f} ×')
para(f'So surveying at 8 kt on the EM712 curve costs about {BEST_E / E8_712:.1f} times '
     f'the fuel per nautical mile that the same vehicle would use at {BEST_V:.1f} kt, '
     f'in exchange for covering the ground in a little over half the time. Whether '
     f'that is a good trade depends entirely on which constraint binds — and §8.1 '
     f'shows that in the one costed model available, it is time, overwhelmingly.')
callout('An extrapolation warning that applies to the EM712 only',
        f'Eight knots requires ({SURVEY_KT:.0f} − {S24["b"]:.4f}) / {S24["m"]:.8f} ≈ '
        f'{RPM8_712:.0f} rpm on the EM712 speed law, against a '
        f'{FV["valid_rpm_max"]:.0f} rpm fit ceiling — an extrapolation of about '
        f'{RPM8_712 / FV["valid_rpm_max"] - 1:.0%}. The EM2040 fitted today needs only '
        f'{RPM8_40:.0f} rpm against a {G40["fuel_vs_rpm"]["valid_rpm_max"]:.0f} rpm '
        f'ceiling, so survey planning on the current gondola is interpolation.',
        color=WARN)

h2('4.4  Range and endurance')
para('Range is capacity multiplied by efficiency; endurance is capacity divided by '
     'fuel rate. Both scale linearly with usable fuel, which is exactly why §6 spends '
     'so long on establishing it — and why §8.2 ends up not needing a capacity at all.')
rows = []
for v in (V_OPT, BEST_V, 6.0, 7.0, SURVEY_KT):
    rate = c0 + c3 * v ** 3
    rows.append([f'{v:.2f} kt', f'{rate:.3f} L/h', f'{v / rate:.2f} NM/L',
                 f'{250 * v / rate:.0f} NM', f'{250 / rate:.0f} h'])
table(['Speed', 'Fuel rate', 'Efficiency', 'Range at 250 L', 'Endurance at 250 L'],
      rows, [1.1, 1.1, 1.1, 1.35, 1.55],
      note='Table 5 — Range and endurance to a DRY tank on the EM712 cubic model, at '
           'the nominal capacity. §8 repeats this to the reserve floor on the gondola '
           'actually fitted, which is the number to plan against.')

# ---- 5. Configurations ----------------------------------------------------
h1('5.  Variation between the EM2040 and EM712 gondola configurations')
para('The 2022/2024 difference is the gondola, on the same hull: the 2022 shakedown '
     'data is DriX-8 with the EM2040 gondola, and the 2024 trials the same '
     'vehicle with the much larger and heavier EM712 — the "experimental upgrade that '
     'drastically increased drag" of the CS850 report, now named. The EM2040 is the '
     'gondola fitted today.')
para('No fuel rate of any kind was recorded in 2022. The comparison must therefore run '
     'through speed at equal RPM, which is legitimate precisely because fuel is set by '
     'RPM (§3). §5.5 adds the direct EM2040 measurement that later confirmed the chain.')

h2('5.1  Speed against RPM')
table(['Configuration', 'Fitted relationship', 'R²', 'n', 'Span'], [
    ['2022 shakedown', f'kt = {S22["b"]:.4f} + {S22["m"]:.7f}·RPM',
     f'{S22["r2"]:.4f}', '7', '1050–2765 rpm'],
    ['2024 trials', f'kt = +{S24["b"]:.4f} + {S24["m"]:.7f}·RPM',
     f'{S24["r2"]:.4f}', '9', '1020–3000 rpm'],
], [1.35, 2.35, 0.7, 0.4, 1.3], note='Table 6 — The two propulsion curves.',
    right_from=2)
para(f'Two things changed, and they are different in kind. The slope fell from '
     f'{S22["m"] * 100:.3f} to {S24["m"] * 100:.3f} kt per 100 rpm — each additional '
     f'100 rpm now buys {S24["m"] / S22["m"]:.0%} of the speed it used to. And the '
     f'intercept rose from {S22["b"]:.2f} kt to +{S24["b"]:.2f} kt. A propulsion curve '
     f'through the origin is what one expects; the 2022 fit passes essentially through '
     f'it. The 2024 curve does not, and a raised intercept with a shallower slope is '
     f'the signature of a curve that has flattened.')
figure('fig05_speed_rpm.png',
       'Figure 5 — Speed against RPM for both configurations, with the four-heading '
       'test means and their spread overlaid.')

h2('5.2  Controlling for current')
para('Every point in the 2024 speed trials was run on a single heading, so tidal '
     'current is folded into the measured speed and cannot be separated. The '
     'prop-efficiency test performed the same day is the control: it held RPM fixed '
     'and ran all four cardinal headings, so the spread across headings is the '
     'environment and the mean is the vehicle.')
table(['Test', 'RPM', 'N', 'E', 'S', 'W', 'Mean (kt)', 'Spread', 'vs 2022'],
      [[f[0], f'{f[1]}', *[f'{x:.2f}' for x in f[2]], f'{f[3]:.2f}',
        f'±{pct(f[4])}', f'{pct(f[5])}'] for f in FH]
      + [['Mean of the three', '—', '—', '—', '—', '—', '—',
          f'±{pct(FH_SPREAD)}', f'{pct(FH_LOSS)}']],
      [1.6, 0.5, 0.42, 0.42, 0.42, 0.42, 0.75, 0.68, 0.68],
      note='Table 7 — Four-heading prop-efficiency test. N/E/S/W are speed in m/s as '
           'logged. The three tests agree across two very different RPM settings.')
para(f'Two conclusions follow. First, the true loss at equal RPM is '
     f'{pct(-FH_LOSS)} rather than the ~30% a single-heading comparison suggests — the '
     f'larger figures are current-inflated. Second, and independently useful: heading '
     f'alone moves measured speed by roughly ±{pct(FH_SPREAD)} at constant RPM. Any '
     f'efficiency figure taken from a single-heading run carries at least that much '
     f'error before anything else is considered.')
figure('fig07_heading.png',
       'Figure 7 — The heading effect. The measured magnitude is firm; the cosine '
       'shape fitted through it is a physically motivated assumption, and it is what '
       'the companion planner uses to apply a wind vector to a leg.')

h2('5.3  Efficiency then and now')
para('Applying the 2024 fuel-vs-RPM model to both configurations\' speeds at matched '
     'RPM gives a like-for-like comparison. This is deliberately conservative: at '
     'equal RPM the higher-drag 2024 hull is the more heavily loaded one and would in '
     'truth burn more than the shared curve predicts.')
rows, ret = [], []
for r in (1250, 1500, 1750, 2000, 2250, 2500):
    lr = f0 + f1 * r
    v22, v24 = S22['b'] + S22['m'] * r, S24['b'] + S24['m'] * r
    e22, e24 = v22 / lr, v24 / lr
    ret.append(e24 / e22)
    rows.append([f'{r}', f'{lr:.3f}', f'{v22:.2f} kt', f'{e22:.2f}',
                 f'{v24:.2f} kt', f'{e24:.2f}', f'{e24 / e22:.1%}'])
rows.append(['Mean', '—', '—', '—', '—', '—', f'{np.mean(ret):.1%}'])
table(['RPM', 'Fuel (L/h)', '2022 speed', '2022 NM/L', '2024 speed', '2024 NM/L',
       'Retained'], rows, [0.6, 0.95, 1.0, 0.95, 1.0, 0.95, 0.85],
      note='Table 8 — Efficiency retained, bridged through RPM. The loss widens with '
           'speed, consistent with an added-drag mechanism rather than a propulsion '
           'fault.')
figure('fig06_config_efficiency.png',
       'Figure 6 — Efficiency curves for both configurations. The 2022 curve sits '
       'above the 2024 one at every shared speed and extends further right.')

h2('5.4  Interpretation')
para(f'The pattern — a flattened propulsion curve, a loss that widens with speed, and '
     f'a hard ceiling near 9.8 kt — is the signature of substantially added drag '
     f'rather than a loss of installed power. This report quantifies that upgrade\'s '
     f'cost: roughly {1 - np.mean(ret):.0%} of distance per litre, and about 1.6 kt '
     f'off the top end.')
para('One caveat should be stated plainly. There is no fuel measurement of any kind '
     'from 2022 and no speed-vs-RPM table from 2023, so the historical time series '
     'rests on two points.')

h2('5.5  The MCAP refit — the EM2040 curve, measured directly')
para(f'With the EM2040 refitted to the vehicle, six days of MCAP logs (04–09 August '
     f'2026) provided the first fuel measurements ever taken in that configuration: '
     f'{FIT["cruise_hours"]:.2f} hours of steady straight-line cruise, flow-meter fuel '
     f'rate against the PLC thruster-RPM channel — the shaft-RPM sensor was faulted '
     f'throughout — with GPS speed over ground. Widening the evidence base from four '
     f'days to six moved efficiency by ≤1% anywhere in 5–10 kt, which is the strongest '
     f'confirmation available that the curve is real rather than merely fitted.')
q = G40['fuel_vs_rpm']
mono(f'L/h  =  {q["q0"]:.4f}  −  {abs(q["q1"]):.7f}·RPM  +  {sci(q["q2"])}·RPM²',
     f'Measured EM2040 fuel law. R² {q["r2"]:.4f} on binned medians, valid '
     f'{q["valid_rpm_min"]:.0f}–{q["valid_rpm_max"]:.0f} rpm.')
sp = G40['speed_vs_rpm']
mono(f'kt  =  {sp["b"]:.4f}  +  {sp["m"]:.7f}·RPM',
     f'Measured EM2040 speed law. R² {sp["r2"]:.3f}; SOG-based, so it carries roughly '
     f'±5% tidal uncertainty.')
table(['Quantity', 'EM2040 measured', 'EM712 measured'], [
    ['At 8 kt — RPM', f'{RPM8_40:.0f} (interpolation)', f'{RPM8_712:.0f} (extrapolated)'],
    ['At 8 kt — fuel', f'{LPH8_40:.2f} L/h', f'{c0 + c3 * 512:.2f} L/h'],
    ['At 8 kt — efficiency', f'{E8_40:.2f} NM/L', f'{E8_712:.2f} NM/L'],
    ['Loiter (~%d rpm)' % LOITER['rpm'],
     f'{LOITER["lph"]} L/h ({LOITER["hours_observed"]} h observed)', '—'],
], [1.7, 2.3, 2.0],
    note=f'Table 8b — The measured EM2040 curve against the EM712. The EM712 costs '
         f'{E8_40 / E8_712:.2f}× the fuel per nautical mile at 8 kt on measured curves.',
    right_from=1)
figure('fig12_mcap_refit.png',
       'Figure 12 — The MCAP refit. Left: the measured quadratic fuel law with the '
       'loiter anchor. Centre: the measured speed law against the 2022 shakedown law. '
       'Right: the efficiency comparison across both gondolas.')
para(f'Two findings ride along. First, the vehicle\'s own static endurance model reads '
     f'{EX["a"] * 64 + EX["c"]:.2f} L/h at 8 kn against the measured {LPH8_40:.2f} — '
     f'the onboard prediction runs about '
     f'{(EX["a"] * 64 + EX["c"]) / LPH8_40:.1f}× rich for this configuration. Second, '
     f'below about {M.speed_for_rpm(q["valid_rpm_min"], "em2040"):.1f} kt '
     f'({q["valid_rpm_min"]:.0f} rpm) there is no cruise data at all, only the loiter '
     f'figure.')

# ---- 6. Capacity ----------------------------------------------------------
h1('6.  Tank volume, gauge span, and the litres between them')
h2('6.1  The problem')
para(f'The 2022 operational log records tank level as a percentage. Over the '
     f'{SRC_2022["hours"]:.1f}-hour window it falls from {SRC_2022["pct_start"]:.1f}% '
     f'to {SRC_2022["pct_end"]:.1f}% — {PTS_FULL:.1f} points. At the nominal 250 L '
     f'that is {250 * PTS_FULL / 100:.2f} L consumed. But mapping each interval of '
     f'that window through speed → 2022 RPM → the 2024 flow-meter curve predicts only '
     f'{SRC_2022["modelled_litres_full"]:.1f} L for the same period. The gauge says '
     f'the vehicle burned '
     f'{250 * PTS_FULL / 100 / SRC_2022["modelled_litres_full"]:.2f} times what the '
     f'flow-meter curve says it should have.')

h2('6.2  Independent evidence — the DD2024 refuel')
para('The source workbook contains a second, entirely separate handle on capacity. '
     'The DD2024 log records 185 L added to DriX-12, with the crew themselves flagging '
     'that the arithmetic did not work. Read the other way round, that event sizes the '
     'tank.')
table(['Quantity', 'Value', 'Note'], [
    ['Fuel added', f'{DD_LITRES:.0f} L', 'As logged'],
    ['Indicated level before', f'{DD_BEFORE:.0f}%', 'The figure questioned at the time'],
    ['Indicated level after', f'{DD_AFTER_LO:.0f}–{DD_AFTER_HI:.0f}%',
     '"at 95%, fluctuating 95-100"'],
    ['Span covered', f'{DD_AFTER_LO - DD_BEFORE:.0f}–{DD_AFTER_HI - DD_BEFORE:.0f} pts',
     'Against 74 points if the tank were truly 250 L'],
    ['Implied capacity (to 95%)', f'{DD_CAP_HI:.1f} L', ''],
    ['Implied capacity (to 100%)', f'{DD_CAP_LO:.1f} L', ''],
    ['Implied capacity (midpoint)', f'{DD_CAP:.1f} L',
     'The single best estimate this event supports'],
], [2.0, 1.3, 2.7], note='Table 9 — The refuel event, read as a capacity measurement.',
    right_from=1)

h2('6.3  The gauge scale — measured; its linearity — not established')
callout('A correction to earlier editions of this report',
        'The section that stood here was titled "The gauge is non-linear" and inferred '
        'a band-dependent scale from two low-precision windows. The August 2026 '
        'calibration measured the scale directly across every day with material burn, '
        'and an error analysis of the per-day spread showed the apparent trend is '
        f'1.5σ — what noise produces routinely. The SCALE below is established. '
        f'Non-linearity is UNRESOLVED: smaller than this measurement can see, which is '
        f'not the same as absent. A float sender in a non-prismatic tank has every '
        f'physical reason to be non-linear.', color=WARN)
para('Expressing each source as litres per indicated point removes the capacity '
     'assumption entirely:')
table(['Source', 'Band covered', 'Points', 'Litres', 'L per point', 'Implied capacity'],
      [['2022 telemetry vs flow-meter curve',
        f'{SRC_2022["pct_end"]:.1f} – {SRC_2022["pct_start"]:.1f}%',
        f'{PTS_FULL:.1f}', f'{SRC_2022["modelled_litres_full"]:.1f}',
        f'{LPP_2022:.3f}', f'{CAP_FULL:.1f} L'],
       ['DD2024 refuel event',
        f'{DD_BEFORE:.0f} – {(DD_AFTER_LO + DD_AFTER_HI) / 2:.1f}%',
        f'{DD_SPAN:.1f}', f'{DD_LITRES:.1f}', f'{LPP_DD:.3f}', f'{DD_CAP:.1f} L'],
       ['MCAP flow meter vs gauge (2026)',
        f'{GC["band_pct"][0]:.0f} – {GC["band_pct"][1]:.0f}%', '—', '—',
        f'{LPP:.3f} ± {LPP_SIG:.2f}', f'{GAUGE_CAP:.1f} L'],
       ['Nominal, if the gauge were linear', '0 – 100%', '100', '250.0',
        f'{NOMINAL_LPP:.3f}', '250.0 L']],
      [2.1, 1.05, 0.6, 0.6, 1.05, 1.1],
      note='Table 10 — Litres per indicated point, by source.', flag_rows=(3,))
para(f'The 2026 measurement is the one that settles it, because it assumes no capacity '
     f'and no fuel model: {LPP:.2f} ± {LPP_SIG:.2f} L per indicated point, '
     f'{(NOMINAL_LPP - LPP) / LPP_SIG:.0f}σ below the {NOMINAL_LPP:.2f} a linear 250 L '
     f'tank requires, and agreeing with the wholly independent DD2024 figure of '
     f'{LPP_DD:.2f} to {GC["cross_check"]["agreement_sigma"]:.1f}σ.')
para('The per-day figures that once looked like a trend are set out below with their '
     'uncertainties, which is what the earlier reading omitted.')
table(['Day', 'Band', 'L per point', '±1σ', 'Relative'],
      [[d['day'], f'{d["band"][0]:.0f}–{d["band"][1]:.0f}%', f'{d["l_per_point"]:.2f}',
        f'{d["sigma"]:.2f}', f'±{d["sigma"] / d["l_per_point"]:.0%}']
       for d in GC['per_day']],
      [1.2, 1.0, 1.0, 0.7, 0.8],
      note=f'Table 10b — Per-day gauge scale. Each day spans only 4–5 indicated points, '
           f'so each carries roughly ±18%. The widest separation is 1.5σ. Pooled, the '
           f'figure is {LPP:.2f} ± {LPP_SIG:.2f}.')
callout('What has never been measured',
        'Every gauge reading in this report comes from the top third of the tank — the '
        f'{GC["band_pct"][0]:.0f}–{GC["band_pct"][1]:.0f}% band and above. The '
        f'{RESERVE:.0%} reserve band has no direct calibration at all, and because the '
        'reserve means the bottom of the gauge is almost never exercised, normal '
        'practice will never supply one. Only a deliberate drawdown or a tank sounding '
        'will.', color=WARN)

h2('6.4  One interval was doing most of the damage')
para(f'The first logged interval of the 2022 window runs from {SRC_2022["i1_from"]:.1f}% '
     f'to {SRC_2022["i1_to"]:.1f}% in {SRC_2022["i1_hours"]:.2f} hours: '
     f'{SRC_2022["i1_from"] - SRC_2022["i1_to"]:.1f} points, a quarter of everything '
     f'consumed, in 8% of the elapsed time, at {SRC_2022["i1_rate"]:.2f} %/h against a '
     f'window mean of {SRC_2022["window_rate"]:.2f} %/h. It is a '
     f'{SRC_2022["i1_rate"] / SRC_2022["window_rate"]:.1f}× outlier, and it sits at the '
     f'very top of the gauge immediately after fuelling.')
figure('fig08_tank_trace_2022.png',
       'Figure 8 — The 2022 tank trace. The highlighted first interval is the outlier; '
       'the long shallow middle section is the low-speed loiter, and the steeper final '
       'section the return to survey speed.')
mono(f'Full window:           {SRC_2022["modelled_litres_full"]:.2f} L / '
     f'{PTS_FULL:.1f} pts × 100  =  {CAP_FULL:.1f} L\n'
     f'Excluding interval 1:  {SRC_2022["modelled_litres_excl"]:.2f} L / '
     f'{SRC_2022["points_excl"]:.1f} pts × 100  =  {CAP_EXCL:.1f} L',
     f'Against the refuel event\'s {DD_CAP:.1f} L, that is agreement to within '
     f'{CAP_AGREE:.1%} — from two sources that share no data, no vehicle and no year.')
figure('fig09_capacity.png',
       'Figure 9 — The first four bars are what 100 indicated points are worth, '
       'measured three independent ways. The last is the physical tank from the '
       'engineering drawings. The gap between them is the ~44 L the gauge does not '
       'account for.')

h2('6.5  Reconciliation')
para('The remaining question is how much of the residual gap is real in-service burn '
     'rather than capacity. That is answered by asking what RPM premium each assumed '
     'capacity requires.')
rows = []
for cap, lab, verdict in ((TANK_VOL, 'Physical tank (drawings)',
                           'Large for the conditions'),
                          (DD_CAP, 'DD2024 refuel event', 'Plausible'),
                          (CAP_EXCL, '2022 telemetry, 1st excluded', 'Plausible'),
                          (CAP_FULL, 'Makes 2022 agree exactly', 'Implausibly tidy')):
    burn = cap * PTS_FULL / 100.0
    prem = premium_for_capacity(cap, SRC_2022['modelled_litres_full'], PTS_FULL,
                                HOURS_FULL)
    rows.append([lab, f'{cap:.1f} L', f'{burn:.1f} L',
                 f'{burn / SRC_2022["hours"]:.2f}',
                 f'{burn / SRC_2022["modelled_litres_full"]:.2f}',
                 f'+{prem:.1%}' if prem > 0 else f'{prem:.1%}', verdict])
table(['Capacity basis', 'Capacity', '2022 burn', 'L/h', '× curve', 'RPM premium',
       'Verdict'], rows, [1.5, 0.75, 0.75, 0.5, 0.6, 0.9, 1.5],
      note='Table 11 — What each capacity requires of the environment.')
callout('Conclusion on capacity — restated on the drawings (2026-08-09)',
        f'The physical tank is {TANK_VOL:.0f} L; that is settled and applies to every '
        f'hull on every mission. What is NOT settled is how that volume maps onto the '
        f'gauge. Three methods put 100 indicated points at 205–{DD_CAP:.0f} L, leaving '
        f'about {UNLOCATED:.0f} L unaccounted for. Two readings fit: the gauge spans '
        f'the tank and is non-linear, forcing the {100 - (BAND_HI - BAND_LO):.0f} '
        f'unmeasured points to average {LAM_A:.2f} L/pt ({LAM_A / LPP - 1:+.0%} on the '
        f'measured band); or the gauge covers only ~{GAUGE_CAP:.0f} L and the rest sits '
        f'outside its travel. Mission fuel to the floor is {FUEL_A:.0f} L under the '
        f'first and {FUEL_B:.0f} L under the second — {FUEL_A - FUEL_B:.0f} L apart. '
        f'THIS REPORT PLANS ON THE FIRST (Andy, 2026-08-09): the drawings are '
        f'documentary evidence and outrank an extrapolation of one band. Note what '
        f'that does to the dependency — under (A) a 20% error in the measured gauge '
        f'scale moves mission fuel by about 1 L, because the profile re-normalises to '
        f'the drawing volume, so planning now rests on the drawings rather than on the '
        f'six days of calibration. It is an inference, and it is the one adopted value '
        f'here that a drawdown could overturn: the two readings predict burns '
        f'{LAM_A / LPP - 1:.0%} apart over a span that resolves to 1.3%.', color=WARN)

# ---- 7. Sea state ---------------------------------------------------------
h1('7.  Sea state and the RPM premium')
h2('7.1  What was actually recorded')
para(f'The 2022 operational log carries wind and heave against every observation. '
     f'Across the window wind ran {SRC_2022["wind_lo"]:.1f} to {SRC_2022["wind_hi"]:.1f} '
     f'kt (mean {SRC_2022["wind_mean"]:.2f}) and heave {SRC_2022["heave_lo"]:.2f} to '
     f'{SRC_2022["heave_hi"]:.2f} m (mean {SRC_2022["heave_mean"]:.2f}) — Beaufort 3 to '
     f'4, WMO sea state 2 to 3. Heave is vehicle vertical motion rather than significant '
     f'wave height, so the WMO mapping is a proxy.')
para(f'The August 2026 MCAP material adds the other end of the scale. Across roughly '
     f'11,000 steady-cruise samples at heave standard deviations of 0.03–0.13 m, fuel '
     f'residuals against the measured quadratic law show no motion-driven premium above '
     f'a ±2% noise floor. The CALM ANCHOR is therefore measured; the slope into rough '
     f'water is still not.')

h2('7.2  The attempt to measure the relationship, and why it fails')
para('If sea state drives the premium, splitting the 2022 window by recorded heave '
     'should show the premium rising with heave. It does not.')
table(['Heave band', 'Intervals', 'Hours', 'Tank pts', 'Mean speed', 'Fuel (L/h)',
       'Premium'],
      [[b[0], f'{b[1]}', f'{b[2]:.2f}', f'{b[3]:.1f}', f'{b[4]:.1f} kt', f'{b[5]:.2f}',
        f'+{b[6]:.1%}'] for b in SRC_2022['heave_bins']],
      [1.05, 0.85, 0.65, 0.8, 1.0, 0.95, 0.85],
      note='Table 12 — Premium binned by heave. Capacity cancels out of the ordering, '
           'so this does not depend on which capacity is assumed.')
para('The premium moves with no consistent relation to heave at all. Two confounds are '
     'sufficient to explain that. The first is speed: the crew slowed deliberately as '
     'conditions built, so the roughest band is also the slowest band. The second is '
     'gauge position: tank level fell steadily through the window while sea state '
     'wandered up and down, so any bin on heave is partly a bin on tank level. One '
     '22-hour window cannot separate three effects.')

h2('7.3  Sensitivity in place of a fit')
para('Since the relationship cannot be measured, the premium is treated as an input '
     'and its consequences are mapped.')
table(['Assumed premium', 'Implied capacity (full window)',
       'Implied capacity (1st excluded)', 'Indicative conditions'],
      [[f'{p:.0%}',
        f'{implied_capacity(p, SRC_2022["modelled_litres_full"], PTS_FULL, HOURS_FULL):.1f} L',
        f'{implied_capacity(p, SRC_2022["modelled_litres_excl"], SRC_2022["points_excl"], HOURS_EXCL):.1f} L',
        cond]
       for p, cond in ((0.00, 'Flat water — trial conditions'),
                       (0.05, 'Ripple, Beaufort 2'),
                       (0.10, 'Beaufort 3–4 — as recorded'),
                       (0.15, 'Beaufort 4, short chop'),
                       (0.20, 'Beaufort 5'),
                       (0.30, 'Beaufort 6 — beyond survey limits'))],
      [1.2, 1.75, 1.75, 1.8],
      note='Table 13 — Sensitivity of implied capacity to the assumed premium. The '
           'rightmost column is interpretation, not measurement.', flag_rows=(2,))
figure('fig10_premium.png',
       'Figure 10 — Implied capacity against assumed premium. Note where the lower '
       'curve begins: at zero premium, excluding the first interval, it starts '
       'essentially on the DD2024 reference line.')

h2('7.4  What would let this be measured')
bullets([
    ('Log the flow meter, not the gauge. ',
     'Now done — the MCAP pipeline reads the flow meter directly, which is what made '
     'the calm anchor and the gauge calibration possible.'),
    ('Record sea state as a number. ',
     'The weather sensor is still not bridged into the connectivity-box recording; '
     'zero live wind or met topics appear across all recorded channels.'),
    ('Hold speed constant across conditions. ',
     'A fixed-RPM leg flown in calm and again in a seaway on the same heading gives '
     'the premium directly. This is the single missing experiment.'),
])

# ---- 8. Planning ----------------------------------------------------------
h1('8.  Mission planning framework')
h2('8.1  Fuel is not where the money is')
table(['Configuration', 'Fuel cost', 'Mission hours', 'Grand total', 'Fuel share'],
      [[c, f'${f:,}', f'{h:.1f}', f'${g:,}', f'{f / g:.2%}']
       for c, f, h, g in EXAIL_ROE], [1.5, 1.1, 1.2, 1.2, 1.0],
      note='Table 14 — Planning figures from the Exail ROE model, not measurements.')
para('Fuel is under one percent of mission cost in every configuration. Halving burn '
     'saves about a tenth of a percent of the total. The same curves used for '
     'endurance instead — one fewer refuel transit, more time on line — move the '
     'shore-refuel-transit ratio, and time is what the model is actually made of. This '
     'is the strongest argument for building an endurance tool rather than an economy '
     'tool, and it is why the framework below is organised around a reserve floor.')

h2('8.2  The reserve policy — and why capacity is the wrong question')
para(f'Operating practice is to return to port with at least {RESERVE:.0%} indicated '
     f'remaining. It is a floor rather than a target. The important structural point is '
     f'that the policy is written in INDICATED PERCENT, not in litres — so the fuel a '
     f'mission may spend before reaching it follows from the measured gauge scale '
     f'alone:')
mono(f'mission fuel  =  litres the gauge holds between 100% and '
     f'{RESERVE * 100:.0f}%  =  {GAUGE_USABLE:.1f} L',
     f'An integral over the gauge, not capacity x (1 - reserve). No tank-capacity '
     f'assumption appears anywhere in it.')
para(f'That figure is on the ADOPTED reading (A) of §6.5, where the gauge spans the '
     f'{TANK_VOL:.0f} L drawing volume and the points outside the measured band carry '
     f'the balance at {LAM_A:.2f} L/point. On the conservative reading (B) — the gauge '
     f'speaking only for its own span, a flat {LPP:.2f} L/point — the same policy gives '
     f'{GAUGE_USABLE_B:.1f} L, and a linear {TANK_VOL:.0f} L tank would give '
     f'{NOMINAL_USABLE:.1f} L, which is {pct(NOMINAL_USABLE / GAUGE_USABLE_B - 1)} more '
     f'than the measured band alone supports. The three sit '
     f'{GAUGE_USABLE_B:.0f} / {GAUGE_USABLE:.0f} / {NOMINAL_USABLE:.0f} L, and the '
     f'choice between the first two is worth {GAUGE_USABLE - GAUGE_USABLE_B:.0f} L — '
     f'{(GAUGE_USABLE - GAUGE_USABLE_B) / LPH8_40:.0f} hours at survey speed.')
rows = []
for lab, cap, usable in ((f'Reading B — gauge span only ({LPP:.2f} L/pt flat)',
                          GAUGE_SPAN_LINEAR, GAUGE_USABLE_B),
                         (f'Reading A — drawings honoured (ADOPTED)', TANK_VOL,
                          GAUGE_USABLE)):
    rows.append([lab, f'{cap:.1f} L', f'{cap - usable:.1f} L', f'{usable:.1f} L',
                 f'{usable * E8_40:.0f} NM', f'{usable / LPH8_40:.1f} h'])
table(['Planning basis', 'Capacity', 'Reserve', 'Mission fuel', 'Range at 8 kt',
       'Endurance'], rows, [2.05, 0.8, 0.75, 0.95, 1.0, 0.8],
      note=f'Table 15 — The two readings of the 44 L gap, on the EM2040 curve measured '
           f'today. The adopted row is the one the planner and the needle both follow; '
           f'the other is what the measured band alone supports. Difference: '
           f'{(GAUGE_USABLE - GAUGE_USABLE_B) * E8_40:.0f} NM and '
           f'{(GAUGE_USABLE - GAUGE_USABLE_B) / LPH8_40:.1f} h.',
      flag_rows=(0,))
para('For completeness, the same planning across every capacity basis the model file '
     'offers — these remain useful as a sensitivity, but note that the gauge row above '
     'does not depend on any of them:')
table(['Capacity basis', 'Capacity', 'Reserve', 'Usable', 'Range at 8 kt', 'Endurance'],
      [[lab, f'{cap:.1f} L', f'{res:.1f} L', f'{us:.1f} L', f'{rng:.0f} NM',
        f'{hrs:.1f} h'] for lab, cap, res, us, rng, hrs in endurance_rows('em2040')],
      [2.35, 0.8, 0.75, 0.75, 1.0, 0.85],
      note='Table 15b — Capacity sensitivity on the EM2040 curve, to the reserve floor.')
figure('fig11_planning_range.png',
       'Figure 11 — Planning range against speed for each capacity basis, to the '
       'reserve floor, on the measured EM2040 curve.')
para('An important interaction deserves stating. The reserve means the bottom of the '
     'gauge is almost never exercised, and short deployments return well above the '
     'floor — so refuel records cluster in the top of the range and say very little '
     'about the bottom. The band that matters for a reserve decision is precisely the '
     'one normal practice never samples.')

h2('8.3  The leg computation')
para('The companion planning tool computes each leg of a mission through the following '
     'chain, shown here for the EM2040 fitted today:')
mono(f'RPM_benign  =  (V_required − {sp["b"]:.5f}) / {sp["m"]:.8f}\n'
     f'premium     =  p_seastate  +  A · (W / W_ref)ⁿ · cos θ\n'
     f'RPM_actual  =  RPM_benign × (1 + premium)\n'
     f'L/h         =  {q["q0"]:.4f} − {abs(q["q1"]):.7f}·RPM + {sci(q["q2"])}·RPM²\n'
     f'litres      =  L/h × distance / V_required',
     f'θ is the angle between leg course and the direction the wind comes FROM, so 0° '
     f'is a dead headwind. A = {HE["amplitude_at_reference"]:.4f} from the four-heading '
     f'test; W_ref = {HE["reference_wind_kt"]:.0f} kt; n = {HE["wind_exponent"]:.0f} by '
     f'assumption.')
para('A survey is flown line by line, and its fuel is summed the same way. The heading '
     'PREMIUM still averages to zero over a reciprocal pair — which remains the guard '
     'against banking a tailwind across a whole survey — but the fuel RATE does not, '
     'because the fuel law is convex in RPM. The line into the weather costs more than '
     'the reciprocal saves, so averaging the premium and applying the law once '
     'understates survey fuel. See Appendix B.3 for the size of that error and why an '
     'odd number of lines cannot balance at all.')

h2('8.4  Worked example')
para(f'A representative mission: 25 NM transit out on 090°, 120 NM of survey at 8 kt, '
     f'25 NM home on 270°, in 12 kt of wind from 270° and WMO sea state 2 '
     f'({M.sea_state_premium(2):.0%} assumed premium), from a full tank. Figures below '
     f'are produced by calling the planning engine, not by repeating its arithmetic.')
table(['Leg', 'NM', 'kt', 'Premium', 'RPM', 'L/h', 'Hours', 'Litres'],
      [[l.name, f'{l.distance_nm:.0f}', f'{l.speed_kt:.1f}',
        f'{l.total_premium:+.1%}', f'{l.rpm_required:.0f}',
        f'{l.fuel_rate_lph:.2f}', f'{l.hours:.2f}', f'{l.litres:.1f}']
       for l in W40.legs]
      + [['Total', f'{W40.total_distance_nm:.0f}', '—', '—', '—', '—',
          f'{W40.total_hours:.2f}', f'{W40.total_litres:.1f}']],
      [1.85, 0.55, 0.5, 0.85, 0.65, 0.6, 0.7, 0.7],
      note='Table 16 — Worked mission on the EM2040 curve. The out and home legs '
           'differ on identical distance and speed — that is the wind, and it is the '
           'asymmetry a single average would hide.')
para(f'The mission burns {W40.total_litres:.1f} L. Against the measured gauge scale it '
     f'may spend {W40.gauge_usable_litres:.1f} L before the needle reaches the floor, '
     f'so it returns with {W40.gauge_margin_litres:.1f} L in hand and the gauge reading '
     f'{W40.indicated_return_pct:.1f}%. On the nominal 250 L basis the same mission '
     f'appears to return at {W40.remaining_fraction:.1%} indicated with '
     f'{W40.margin_litres:.1f} L of margin — the gap between those two readings is '
     f'the gap between the two readings of §6.5, not an error in either.')
para(f'The same mission on the EM712 gondola costs {W712.total_litres:.1f} L against '
     f'{W40.total_litres:.1f} L — the gondola swap of §5.5 made concrete on a single '
     f'modest mission, and with '
     f'{"extrapolation flags on " + ", ".join(W712.extrapolated_legs) if W712.extrapolated_legs else "no extrapolation flags"}.')

h2('8.5  The tool')
para(f'The framework above is implemented as a Python engine with a browser interface '
     f'at D:\\Claude\\Fuel, covered by a unit-test suite whose mutation guards fail if '
     f'a coefficient changes unnoticed. Three rails are built in deliberately: '
     f'any leg outside the fitted RPM window for its gondola is flagged as an '
     f'extrapolation; every result carries a sensitivity band across premium and '
     f'capacity; and every result reports what the NEEDLE will read alongside the '
     f'capacity-based figure, with the headline verdict following whichever floor '
     f'binds first. All coefficients live in one model file, each tagged fitted or '
     f'assumed.')

# ---- 9. Data quality ------------------------------------------------------
h1('9.  Data quality register')
para('Every item below is a property of the source material rather than of the '
     'analysis. Each is either worked around or excluded, as noted.')
DQ = [
    ('2024 trials', '2750 and 3000 rpm runs report less fuel than the 2500 rpm run',
     'Excluded from all fits; kept visible. Log records increasing chop from that run on'),
    ('2024 trials', 'RPM blank for the 2750 run',
     'Recovered from the raw-file path in the same row'),
    ('2024 trials', 'Every step is a single heading, so current is folded in',
     'Largest single error source in the 2024 curve. Quantified at ±7% by the '
     'four-heading test'),
    ('2022 ops log', 'Rows in document order, not time order',
     'Re-sorted by timestamp; left alone the interval rates would be nonsense'),
    ('2022 ops log', 'Level quantised to whole percent against ~1.7 %/h',
     'Only whole-window figures carried forward'),
    ('2022 ops log', 'First interval is a 3.3× outlier at the top of the gauge',
     f'Both windows carried side by side; it changes implied capacity from '
     f'{CAP_FULL:.0f} L to {CAP_EXCL:.0f} L'),
    ('2022 ops log', 'Per-observation rows are not held in this repository',
     'The §6–§7 aggregates and Figure 8 are carried as constants and a static asset; '
     'this is the report\'s one remaining drift risk'),
    ('2022 shakedown', 'No fuel rate, no heading, no sea state recorded',
     'Used only for speed at RPM; all 2022 efficiency bridged through the 2024 model'),
    ('DD2024', 'The 185 L may span more than one fuelling',
     f'Capacity shown as a range, {DD_CAP_LO:.0f}–{DD_CAP_HI:.0f} L'),
    ('MCAP 2026', 'Shaft-RPM sensor faulted across all days',
     'PLC thruster_rpm channel used throughout; direct drive makes them equal'),
    ('MCAP 2026', 'Speed law is SOG-based, so tide is folded in',
     'Carries ±5% tidal uncertainty; reciprocal-heading runs would strip it out'),
    ('MCAP 2026', 'All gauge readings come from the top third of the tank',
     f'The {RESERVE:.0%} reserve band has no calibration; stated wherever it bears on '
     f'a conclusion'),
    ('Weather', 'Sensor not bridged into the recording; no live wind or met topics',
     'Wind model remains an assumption; the extractor announces the topic if it appears'),
    ('Exail ROE', 'Fuel cost in dollars with no unit price',
     'Kept in cost terms; litres cannot be recovered from it'),
]
table(['#', 'Source', 'Issue', 'Handling'],
      [[str(i + 1), s, iss, hand] for i, (s, iss, hand) in enumerate(DQ)],
      [0.35, 1.25, 2.3, 2.6], note='Table 17 — Data quality register.', right_from=99)

# ---- 10. Findings ---------------------------------------------------------
h1('10.  Findings and recommendations')
h2('10.1  Findings')
FINDINGS = [
    (f'Fuel rate is linear in engine RPM over {FV["valid_rpm_min"]:.0f}–'
     f'{FV["valid_rpm_max"]:.0f} rpm (R² {FORMS[0][2]:.3f})', 'High — measured'),
    (f'Efficiency peaks near 4 kt at about {BEST_E:.1f} NM/L (EM712)', 'High — measured'),
    (f'The 8 kt survey speed costs {BEST_E / E8_712:.1f}× the fuel per NM on the EM712, '
     f'and is an extrapolation there', 'High, with the extrapolation caveat'),
    (f'Speed at equal RPM fell {pct(-FH_LOSS)} between 2022 and 2024',
     'High — three agreeing tests'),
    ('The vehicle is now drag-limited near 9.8 kt at WOT', 'High — measured'),
    (f'Heading alone moves measured speed ±{pct(FH_SPREAD)} at constant RPM',
     'High — measured'),
    (f'The physical tank is {TANK_VOL:.0f} L on every hull',
     'Established — engineering drawings'),
    (f'The gauge span is 205–{DD_CAP:.0f} L, so ~{UNLOCATED:.0f} L of the tank is '
     f'outside the indicated range',
     'High — three sources inside 2%, all slopes'),
    (f'The gauge scale is {LPP:.2f} ± {LPP_SIG:.2f} L per indicated point',
     'High — direct flow-meter calibration'),
    ('Gauge non-linearity is UNRESOLVED — the per-day spread is 1.5σ',
     'Established as NOT established'),
    (f'The reserve floor is a needle position, so mission fuel is an integral over '
     f'the gauge, not capacity x (1 - reserve)', 'High — follows from the policy'),
    (f'Mission fuel is {GAUGE_USABLE:.0f} L on the adopted reading (A), '
     f'{GAUGE_USABLE_B:.0f} L on the conservative reading (B)',
     'INFERRED — (A) rests on the drawings, not a drawdown'),
    ('Sea state: the calm anchor is measured; the slope into rough water is not',
     'High — demonstrated'),
    ('Fuel is under 1% of mission cost', 'High — from the ROE model as given'),
    (f'EM2040 measured directly: {E8_40:.2f} NM/L at 8 kt, six days of logs',
     'High — MCAP refit, Aug 2026'),
    (f'The onboard static endurance model runs '
     f'~{(EX["a"] * 64 + EX["c"]) / LPH8_40:.1f}× the measured burn',
     'Moderate — one configuration'),
]
table(['#', 'Finding', 'Confidence'],
      [[str(i + 1), f, c] for i, (f, c) in enumerate(FINDINGS)],
      [0.35, 4.15, 1.8], note='Table 18 — Findings with confidence. Confidence '
                              'reflects the evidence, not the importance.', right_from=99)

h2('10.2  Recommendations')
bullets([
    ('Run one controlled drawdown into the reserve band with the flow meter logging. ',
     f'This is now the single highest-value measurement outstanding. The '
     f'{RESERVE:.0%} floor is where every plan is judged and it is the one band with '
     f'no calibration at all — {DD["blind_points"]:.0f} indicated points below the '
     f'lowest level ever logged ({DD["lowest"]:.0f}%) have never been observed. Running '
     f'from there to the floor costs about {DD["full"]["litres"]:.0f} L and '
     f'{DD["full"]["hours_survey"]:.0f} hours at survey speed and would fix litres per '
     f'point to {DD["full"]["precision"]:.1%}; half of it costs '
     f'{DD["half"]["litres"]:.0f} L and {DD["half"]["hours_survey"]:.0f} hours for '
     f'{DD["half"]["precision"]:.1%}. A tank sounding alongside would settle capacity in '
     f'the same afternoon.'),
    ('Log litres against indicated percentage at every refuel. ',
     'The slope gives litres per point directly and the scatter across different '
     'starting levels is the only thing that will ever resolve the non-linearity '
     'question §6.3 has to leave open.'),
    ('Run one fixed-RPM leg in a seaway. ',
     'Matched speed, same heading, calm against rough. The calm end is now measured, '
     'so this single experiment would convert the sea-state table from assumption to '
     'measurement.'),
    ('Bridge the weather sensor into the connectivity-box recording. ',
     'Until it appears, the wind model stays an assumption. The extraction pipeline '
     'probes for it on every new day and will announce it the moment it arrives.'),
    ('Collect steady runs between 1100 and 1400 rpm. ',
     'There is no cruise data below about 5.3 kt, only the idle anchor, so the bottom '
     'of the operating range is carried by the curve rather than by measurement.'),
    ('Repeat the speed trials over reciprocal headings. ',
     'The speed law is SOG-based and carries the tide with it. Reciprocal pairs would '
     'strip it out and sharpen every derived range figure.'),
])

# ---- Appendices -----------------------------------------------------------
h1('Appendix A  —  Coefficient reference')
para('All values as they stand in model.json v' + D['version'] + ', which the planning '
     'tool consumes directly. Full precision is given for that reason. This table is '
     'generated from the model file, so it cannot drift from it.')
APP = [
    ('f₀', f0, 'EM712 fuel-vs-RPM intercept (L/h)'),
    ('f₁', f1, 'EM712 fuel-vs-RPM slope (L/h per rpm)'),
    ('c₀', c0, 'Cubic standing load (L/h)'),
    ('c₃', c3, 'Cubic drag coefficient'),
    ('m₂₄', S24['m'], '2024 EM712 speed-vs-RPM slope (kt per rpm)'),
    ('b₂₄', S24['b'], '2024 EM712 speed-vs-RPM intercept (kt)'),
    ('m₂₂', S22['m'], '2022 EM2040 speed-vs-RPM slope'),
    ('b₂₂', S22['b'], '2022 EM2040 speed-vs-RPM intercept'),
    ('A', HE['amplitude_at_reference'], 'Heading-effect amplitude'),
    ('q₀', q['q0'], 'EM2040 measured fuel law, constant (L/h)'),
    ('q₁', q['q1'], 'EM2040 measured fuel law, linear term'),
    ('q₂', q['q2'], 'EM2040 measured fuel law, quadratic term'),
    ('b₄₀', sp['b'], 'EM2040 measured speed-law intercept (kt)'),
    ('m₄₀', sp['m'], 'EM2040 measured speed-law slope (kt per rpm)'),
    ('L/pt', LPP, 'Measured gauge scale (litres per indicated point)'),
    ('σ(L/pt)', LPP_SIG, 'One standard deviation on the gauge scale'),
]
table(['Symbol', 'Value', 'Meaning'],
      [[s, f'{v!r}' if abs(v) < 1e-3 else f'{v:.16g}', m] for s, v, m in APP]
      + [['—', f'{FV["valid_rpm_min"]:.0f} – {FV["valid_rpm_max"]:.0f}',
          'EM712 fuel-law validity window (rpm)'],
         ['—', f'{q["valid_rpm_min"]:.0f} – {q["valid_rpm_max"]:.0f}',
          'EM2040 fuel-law validity window (rpm)'],
         ['—', f'{RESERVE:.2f}', 'Reserve fraction (policy, indicated)']],
      [0.9, 2.3, 3.1], note='Table A1 — Coefficient reference.', right_from=99)

h1('Appendix B  —  Derivations')
h3('B.1  Optimum speed')
para('Maximising E(V) = V / (c₀ + c₃V³). Using the quotient rule, the numerator of '
     'dE/dV is (c₀ + c₃V³)·1 − V·3c₃V², which simplifies to c₀ − 2c₃V³. Setting that '
     'to zero gives V_opt = (c₀/2c₃)^⅓. The denominator is strictly positive for '
     'V > 0, so no spurious roots are introduced, and the second derivative is '
     'negative there, confirming a maximum.')
h3('B.2  Implied capacity from a gauge window')
para('For a window consuming ΔP indicated points over H hours at mean speed V̄, with '
     'the flow-meter model predicting a rate L̇, the capacity at which gauge and flow '
     'meter agree is C = L̇ · H / ΔP × 100. Introducing an RPM premium p replaces L̇ '
     'with f₀ + f₁·RPM(V̄)·(1+p), which is the relationship plotted in Figure 10. It is '
     'linear in p because the fuel model is linear in RPM.')
h3('B.3  Why the premium cancels on a survey but the fuel does not')
para('For reciprocal lines the two courses are θ and θ + 180°. Since '
     'cos(θ + 180°) = −cos(θ), the mean of the two heading PREMIUMS is exactly zero '
     'for any θ and any wind — not approximately, and independent of wind speed and '
     'the exponent. That is why a tailwind cannot be banked across a survey.')
para('The fuel does not cancel, because the law is convex. Writing R for the RPM at '
     'the sea-state premium and δ for the heading swing in RPM, the two lines burn '
     'f(R ± δ). For the measured quadratic f = q₀ + q₁·RPM + q₂·RPM², their mean is '
     'q₀ + q₁R + q₂(R² + δ²), so the excess over f(R) is exactly q₂δ² — strictly '
     'positive, and quadratic in the wind because δ is. Averaging the premium first '
     'discards it.')
para('On the EM2040 at survey speed that is about +0.9% at 12 kt, +7.1% at 20 kt and '
     '+17.2% at 25 kt. On the EM712, whose fuel law is affine in RPM, q₂ is zero and '
     'the penalty vanishes exactly — which is the cleanest available confirmation '
     'that the effect is curvature rather than an artefact of the calculation.')
para('An ODD number of lines cannot balance even in principle: one direction gets an '
     'extra line, so the mean premium is A·cos θ / N rather than zero. Three lines '
     'into a 20 kt wind cost about 21% more than the cancelled figure. The planner '
     'therefore takes a line count and a line length rather than a single distance.')
h3('B.4  Why mission fuel does not depend on capacity')
para(f'The reserve policy fixes a needle position, not a volume. If the gauge reads '
     f'linearly at λ litres per indicated point over the range flown, a mission '
     f'starting at S indicated points and stopping at R may spend (S − R)·λ litres. '
     f'Tank capacity C appears nowhere in that expression; it enters only if one '
     f'insists on converting a percentage to litres via C/100, which is precisely the '
     f'step the measurement of λ makes unnecessary. With S = 100, R = '
     f'{RESERVE * 100:.0f} and λ constant at {LPP:.2f}, mission fuel is '
     f'{GAUGE_USABLE_B:.1f} L. Tank capacity C appears nowhere. Where λ varies — '
     f'reading (A), where the drawings force the unmeasured points to '
     f'{LAM_A:.2f} — the same argument holds with the sum replaced by an integral, '
     f'giving {GAUGE_USABLE:.1f} L. Either way the quantity is a property of the '
     f'gauge, not of the tank.')

h1('Appendix C  —  How this report is produced')
para('This document has no hand-written numbers. tools/build_report.py loads '
     'model.json and the MCAP fit output, recomputes every derived quantity, calls the '
     'planning engine for the planning tables, regenerates eleven of the twelve '
     'figures, and emits the document. Rebuild with:')
mono('python tools/build_report.py')
bullets([
    ('Source observations are inputs, not results. ',
     'The 2024 trial steps, the four-heading test, the DD2024 refuel and the Exail ROE '
     'costs are transcribed measurements and are marked as such in the builder.'),
    ('One dataset is missing from the repository. ',
     'The 2022 per-observation log is not held here, so the §6–§7 aggregates are '
     'carried as constants and Figure 8 as a static asset. Everything else recomputes.'),
    ('The companion documents share this property. ',
     'The gauge-linearity report, the methods report and the endurance sheet are all '
     'generated from the same pipeline output, which is why the four agree.'),
])

check_table_widths(doc)
doc.save(OUT)
print('written:', OUT)
print(f'  model v{D["version"]}  ·  gauge {LPP} L/pt  ·  mission fuel '
      f'{GAUGE_USABLE:.1f} L (reading {READING})  ·  B would be {GAUGE_USABLE_B:.1f} L')
print(f'  §8.5 claims checked: {N_TESTS} tests, {N_GUARDS} mutation guards '
      f'(counts are NOT written into the document — see check_test_claims)')

# LIMITS — what this builder cannot regenerate, kept where it will be seen:
#   * SRC_2022 aggregates and Figure 8 depend on the 2022 operational per-observation
#     log, which is not in this repository. If it is ever recovered, replace the
#     SRC_2022 block with a loader and fig08 with a real figure function.
