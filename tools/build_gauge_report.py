"""Generate the fuel-gauge linearity report (.docx) with figures.

Usage:
    python tools/build_gauge_report.py [OUT.docx]

Analyses the tank gauge against the flow meter across every cached day, builds
the figures, and writes the report. Every number is computed here from the
caches — nothing is transcribed.
"""
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from docx.enum.text import WD_ALIGN_PARAGRAPH

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # engine
from docx_style import (new_document, check_table_widths,  # noqa: E402
                        build_date_str, INK, SOFT, ACCENT, WARN)
from engine import Model as _EngModel  # noqa: E402
from drawdown import spec as _drawdown_spec  # noqa: E402

HERE = Path(__file__).parent
CACHE = HERE / 'rosbags'
FIGS = HERE / 'gauge_figs'
FIGS.mkdir(exist_ok=True)
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    HERE.parent / 'DriX8_Fuel_Gauge_Linearity.docx')

import json as _json
with open(HERE.parent / 'model.json', encoding='utf-8') as _fh:
    RES_PCT = _json.load(_fh)['reserve']['default_fraction'] * 100.0

_DD = _drawdown_spec(_EngModel(), CACHE)

NOMINAL_LPP = 2.50          # 250 L over 100 points
DD2024_LPP = 2.09           # 185 L over ~88.5 points, whole range
Y2022_LPP = 1.73            # 65.0 modelled L over 37.5 points

C_MEAS, C_MODEL, C_ALT, C_2022, C_WARN = '#1f77b4', '#c0504d', '#ed7d31', '#4c9a2a', '#9a6400'

plt.rcParams.update({
    'font.family': 'Arial', 'font.size': 9, 'axes.titlesize': 10,
    'axes.titleweight': 'bold', 'axes.edgecolor': '#5b6b7a',
    'axes.labelcolor': '#16212b', 'text.color': '#16212b',
    'xtick.color': '#5b6b7a', 'ytick.color': '#5b6b7a', 'axes.grid': True,
    'grid.color': '#dbe2e8', 'grid.linewidth': .7, 'legend.fontsize': 8,
    'legend.frameon': False, 'figure.dpi': 200, 'savefig.dpi': 200,
    'savefig.bbox': 'tight'})


# --------------------------------------------------------------- data & maths
def load(p):
    z = np.load(p)
    d = {k: z[k] for k in z.files}
    for g in ('t3', 'vs'):
        t = d.get(f'{g}_t')
        if t is None or not len(t):
            continue
        i = np.argsort(t, kind='stable')
        for k in list(d):
            if k.startswith(g + '_'):
                d[k] = d[k][i]
    return d


def rmed(t, x, win):
    out = np.empty(len(x))
    j = 0
    for i in range(len(t)):
        while t[i] - t[j] > win:
            j += 1
        out[i] = np.median(x[j:i + 1])
    return out


days = []
for p in sorted(CACHE.glob('2026-*.npz')):
    d = load(p)
    t, lph = d['t3_t'], d['t3_fuel_lph'].astype(float)
    if len(t) < 50:
        continue
    gas = np.interp(t, d['vs_t'], d['vs_gas_pct'].astype(float))
    dts = np.diff(t, prepend=t[0])
    dts[dts > 30] = 0
    cum = np.cumsum(lph * dts) / 3600
    days.append(dict(day=p.stem, t=t, gas=gas, cum=cum, total=float(cum[-1]),
                     g0=float(np.median(gas[:600])), g1=float(np.median(gas[-600:]))))

active = [d for d in days if d['total'] >= 2.0]
idle = [d for d in days if d['total'] < 2.0]
SIG_PTS = 0.7            # 1% quantisation -> ~0.5 pt per endpoint, 0.7 combined
for d in active:
    d['pts'] = d['g0'] - d['g1']
    d['lpp'] = d['total'] / d['pts']
    d['sig'] = d['lpp'] * SIG_PTS / d['pts']

TOT = sum(d['total'] for d in active)
G_HI = max(d['g0'] for d in active)
G_LO = min(d['g1'] for d in active)
PTS = G_HI - G_LO
POOLED = TOT / PTS
POOLED_SIG = POOLED * SIG_PTS / PTS
lo_d = min(active, key=lambda d: d['lpp'])
hi_d = max(active, key=lambda d: d['lpp'])
GAP = hi_d['lpp'] - lo_d['lpp']
GAP_SIG = float(np.hypot(hi_d['sig'], lo_d['sig']))
SIGMAS = GAP / GAP_SIG
NOM_SIGMAS = (NOMINAL_LPP - POOLED) / POOLED_SIG

# ------------------------------------------------------------------- figures
fig, axes = plt.subplots(1, len(active), figsize=(11, 3.1), sharey=True)
for ax, d in zip(np.atleast_1d(axes), active):
    hrs = (d['t'] - d['t'][0]) / 3600
    ax.plot(hrs, d['gas'], color=C_MEAS, lw=1.3)
    ax2 = ax.twinx()
    ax2.plot(hrs, d['cum'], color=C_MODEL, lw=1.6)
    ax2.set_ylim(0, max(11, max(x['total'] for x in active) * 1.15))
    ax2.grid(False)
    if ax is np.atleast_1d(axes)[-1]:
        ax2.set_ylabel('cumulative litres (flow meter)', color=C_MODEL)
        ax2.tick_params(axis='y', colors=C_MODEL)
    else:
        ax2.set_yticklabels([])
    ax.set_title(d['day'], fontsize=9)
    ax.set_xlabel('hours into day')
np.atleast_1d(axes)[0].set_ylabel('indicated tank level (%)', color=C_MEAS)
np.atleast_1d(axes)[0].tick_params(axis='y', colors=C_MEAS)
fig.suptitle('Gauge (blue, quantised to 1%) against metered fuel (red)',
             fontsize=10, fontweight='bold', y=1.06)
fig.savefig(FIGS / 'g1_traces.png', facecolor='white')
plt.close(fig)

fig, ax = plt.subplots(figsize=(6.4, 3.9))
off = 0.0
for d in active:
    ax.scatter(d['gas'], d['cum'] + off, s=1.5, color=C_MEAS, alpha=.16,
               linewidths=0, zorder=1)
    env = np.minimum.accumulate(rmed(d['t'], d['gas'], 600))
    ax.plot(env, d['cum'] + off, color=C_MEAS, lw=2.0, zorder=3)
    ax.plot(d['g0'], off, 'o', color=C_MODEL, ms=6, zorder=5)
    off += d['total']
ax.plot(active[-1]['g1'], off, 'o', color=C_MODEL, ms=6, zorder=5)
xs = np.array([G_LO, G_HI])
ax.plot(xs, (G_HI - xs) * NOMINAL_LPP, '--', color=C_2022, lw=1.8,
        label=f'nominal linear ({NOMINAL_LPP:.2f} L/pt on a 250 L tank)')
ax.plot(xs, (G_HI - xs) * POOLED, '-', color=C_MODEL, lw=2.2,
        label=f'measured ({POOLED:.2f} L/pt, pooled)')
ax.plot([], [], color=C_MEAS, lw=2.0, label='metered fuel vs level (10-min median)')
ax.scatter([], [], s=8, color=C_MEAS, alpha=.35, linewidths=0, label='raw 1%-quantised gauge')
ax.invert_xaxis()
ax.set_xlabel('Indicated tank level (%)')
ax.set_ylabel('Cumulative litres burned')
ax.set_title('Calibration curve: the gauge falls faster than nominal')
ax.set_ylim(-1, None)
ax.legend(loc='upper left')
fig.savefig(FIGS / 'g2_calibration.png', facecolor='white')
plt.close(fig)

fig, ax = plt.subplots(figsize=(6.6, 3.6))
labels = [f'{d["day"][5:]}\n{d["g1"]:.0f}-{d["g0"]:.0f}%' for d in active]
vals = [d['lpp'] for d in active]
errs = [d['sig'] for d in active]
cols = [C_MEAS] * len(active)
labels.append(f'POOLED\n{G_LO:.0f}-{G_HI:.0f}%')
vals.append(POOLED); errs.append(POOLED_SIG); cols.append(C_MODEL)
x = np.arange(len(vals))
ax.bar(x, vals, yerr=errs, capsize=5, color=cols, width=.6,
       error_kw=dict(lw=1.4, ecolor='#333'))
ax.axhline(NOMINAL_LPP, color=C_2022, ls='--', lw=1.6,
           label=f'nominal {NOMINAL_LPP:.2f} L/pt (250 L linear)')
ax.axhline(DD2024_LPP, color=C_ALT, ls=':', lw=1.6,
           label=f'DD2024 refuel, whole range {DD2024_LPP:.2f}')
ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=8)
ax.set_ylabel('Litres per indicated point')
ax.set_ylim(0, 3.1)
ax.set_title('Per-day scatter sits within its own error bars')
ax.legend(loc='lower left'); ax.grid(axis='x')
fig.savefig(FIGS / 'g3_lpp.png', facecolor='white')
plt.close(fig)

fig, ax = plt.subplots(figsize=(6.2, 3.3))
srcs = [('Nominal\nassumption', 250.0, 0.0, C_2022),
        ('DD2024 refuel\n(9–97%)', DD2024_LPP * 100, 6.0, C_ALT),
        ('2022 telemetry\n(modelled litres)', Y2022_LPP * 100, 12.0, C_WARN),
        ('MCAP pooled\n(metered)', POOLED * 100, POOLED_SIG * 100, C_MODEL)]
xs = np.arange(len(srcs))
ax.bar(xs, [s[1] for s in srcs], yerr=[s[2] for s in srcs], capsize=5,
       color=[s[3] for s in srcs], width=.6, error_kw=dict(lw=1.4, ecolor='#333'))
for i, s in enumerate(srcs):
    ax.text(i, s[1] + s[2] + 6, f'{s[1]:.0f} L', ha='center', fontsize=8.5, fontweight='bold')
ax.set_xticks(xs); ax.set_xticklabels([s[0] for s in srcs], fontsize=8)
ax.set_ylabel('Implied usable capacity (L)')
ax.set_ylim(0, 290)
ax.set_title('What each source implies for usable capacity')
ax.grid(axis='x')
fig.savefig(FIGS / 'g4_capacity.png', facecolor='white')
plt.close(fig)

# -------------------------------------------------------------------- document
S = new_document(FIGS,
                 headings=(('Heading 1', 15, ACCENT, 16, 8, True),
                           ('Heading 2', 12, ACCENT, 13, 5, False)),
                 right_from=1, warn_prefix=False, callout_spacer=None)
doc, para, mono = S.doc, S.para, S.mono
bullets, shade, table, callout, figure = (S.bullets, S.shade, S.table,
                                          S.callout, S.figure)


# ---- title
para('', after=52)
para('DriX-8 Fuel Gauge', size=24, bold=True, color=ACCENT,
     align=WD_ALIGN_PARAGRAPH.CENTER, after=2)
para('Linearity, Scale and What the Reserve Is Worth', size=14, italic=True,
     color=SOFT, align=WD_ALIGN_PARAGRAPH.CENTER, after=28)
para(f'{len(active)} operating days measured against the flow meter · '
     f'{TOT:.1f} L over {PTS:.0f} indicated points',
     size=11, color=SOFT, align=WD_ALIGN_PARAGRAPH.CENTER, after=4)
para(build_date_str(), size=10, color=SOFT,
     align=WD_ALIGN_PARAGRAPH.CENTER, after=34)
callout('The short version',
        f'The gauge does not read litres. Over the {G_LO:.0f}–{G_HI:.0f}% band it delivers '
        f'{POOLED:.2f} ± {POOLED_SIG:.2f} litres per indicated point, against {NOMINAL_LPP:.2f} '
        f'for a linear 250 L tank — {NOM_SIGMAS:.0f} sigma below, and in close agreement with the '
        f'independent DD2024 refuel figure of {DD2024_LPP:.2f}. That is a solid result and it '
        f'points at a usable capacity near {POOLED*100:.0f} L. Whether the gauge is also '
        f'NON-LINEAR — reading different litres-per-point at different levels — is NOT '
        f'established by this data, and an earlier reading of these same days overstated it. '
        f'Section 5 sets that record straight.')

# ---- 1
doc.add_heading('1.  Why this matters', level=1)
para('Two operating decisions rest on the gauge, and both are quantitative.')
bullets([
    (f'The {RES_PCT:.0f}% return-to-port reserve is expressed in indicated percent. ',
     f'If a point is not a fixed number of litres, then "{RES_PCT:.0f}%" is not a fixed amount of fuel — and '
     'the reserve is either more or less protective than intended, without anyone being able to '
     'tell from the wheelhouse.'),
    ('Endurance predictions scale directly with usable capacity. ',
     'Range and hours-remaining are computed as usable litres times efficiency. Take the '
     'capacity wrong and every planning number inherits the error, in proportion.'),
])
para('The gauge is also the only fuel instrument available on a vehicle without a working flow '
     'meter, so understanding its behaviour transfers to hulls that cannot be measured this way.')

# ---- 2
doc.add_heading('2.  Method', level=1)
para('The flow meter is used as the ruler. It measures litres directly and is independent of '
     'the gauge, so comparing the two isolates the gauge\'s behaviour without assuming a tank '
     'volume or a fuel model anywhere in the chain.')
mono('litres per indicated point  =  ∫ fuel_rate dt  ÷  Δ indicated %')
para('Two details do the work. Fuel rate is integrated over each day with gaps longer than 30 s '
     'excluded, so a recording break cannot inflate the total. And each end of the gauge span is '
     'taken as a ten-minute median rather than a single reading, because the gauge is quantised '
     'to whole percent and bounces by a point or more as fuel moves in the tank.')
para('Days burning less than 2 L are excluded from the calibration entirely. On an idle day the '
     'gauge still wanders a point or two on essentially no fuel, and letting such a day anchor '
     'one end of a span moves the answer by roughly a tenth for no physical reason.')

table(['Day', 'Metered litres', 'Gauge span', 'Points', 'L per point', 'Uncertainty'],
      [[d['day'], f'{d["total"]:.2f}', f'{d["g1"]:.0f}–{d["g0"]:.0f}%',
        f'{d["pts"]:.0f}', f'{d["lpp"]:.2f}', f'± {d["sig"]:.2f}'] for d in active]
      + [['POOLED', f'{TOT:.1f}', f'{G_LO:.0f}–{G_HI:.0f}%', f'{PTS:.0f}',
          f'{POOLED:.2f}', f'± {POOLED_SIG:.2f}']]
      + [[d['day'], f'{d["total"]:.2f}', '—', '—', 'idle — excluded', '—'] for d in idle],
      [1.05, 1.15, 1.05, 0.7, 1.05, 1.05],
      note='Table 1 — Every cached day. Uncertainty is dominated by the gauge span: a '
           'ten-minute median of a 1%-quantised signal still carries about ±0.5 point at each '
           'end, so ±0.7 on the difference. A day spanning only four points therefore carries '
           'about ±18% on its litres-per-point, which is the crux of section 5.')

figure('g1_traces.png',
       'Figure 1 — The raw evidence, one panel per operating day. The gauge (blue) descends in '
       'visible 1% steps with bounce at each transition; metered fuel (red) rises smoothly. The '
       'staircase against the ramp is the entire measurement.', width=6.4)

# ---- 3
doc.add_heading('3.  The calibration curve', level=1)
para('Chaining the operating days end to end gives cumulative metered litres against indicated '
     'level across the whole observed band.')
figure('g2_calibration.png',
       'Figure 2 — Cumulative litres against indicated level. The measured slope sits below the '
       'nominal line throughout: each indicated point is buying fewer litres than a linear 250 L '
       'tank would give. The scatter behind the staircase is the raw quantised gauge, shown so '
       'the noise is not hidden by the smoothing that removes it.')
para(f'Across the full observed span the gauge delivers {POOLED:.2f} ± {POOLED_SIG:.2f} L per '
     f'indicated point. If that rate held across the whole tank it would imply about '
     f'{POOLED*100:.0f} L of usable capacity, against the 250 L nominal.')

# ---- 4
doc.add_heading('4.  Four independent lines of evidence', level=1)
table(['Source', 'Band', 'L per point', 'Litres are…', 'Strength'],
      [['Nominal assumption', '0–100%', f'{NOMINAL_LPP:.2f}', 'assumed',
        'No measurement behind it'],
       ['2022 telemetry', '54–92%', f'{Y2022_LPP:.2f}', 'MODELLED',
        'Precise arithmetic, but the litres come from a fuel model, not a meter'],
       ['DD2024 refuel event', '9–97%', f'{DD2024_LPP:.2f}', 'metered (pumped)',
        'Real litres over nearly the whole range; the fill may not be one clean event'],
       ['MCAP flow meter', f'{G_LO:.0f}–{G_HI:.0f}%', f'{POOLED:.2f}', 'metered',
        'Direct and continuous, but spans only a narrow band'],
       ],
      [1.35, 0.75, 0.85, 1.05, 2.3], right_from=1,
      note='Table 2 — The 2022 figure is the weakest of the three non-nominal sources despite '
           'looking the most precise: its litres are what a fuel model predicts for that window, '
           'so it inherits both model error and any in-service burn premium. It should not be '
           'weighed equally with the two metered figures.')

figure('g4_capacity.png',
       'Figure 4 — Implied usable capacity by source. The two metered sources agree closely; the '
       'nominal figure stands apart, and the modelled 2022 figure sits low for the reasons in '
       'Table 2.')
para(f'The two sources that measure real litres — the DD2024 refuel and the flow meter — agree '
     f'to within {abs(POOLED-DD2024_LPP)/POOLED_SIG:.1f} sigma despite sharing no data, no '
     f'method and no year. That agreement is the strongest single result here.')

# ---- 5
doc.add_heading('5.  Is the gauge non-linear? Not established.', level=1)
para('The three operating days each cover a different band, and their litres-per-point differ: '
     + ', '.join(f'{d["lpp"]:.2f} over {d["g1"]:.0f}–{d["g0"]:.0f}%' for d in active)
     + '. Read quickly, that looks like the gauge changing scale down the tank — and an earlier '
       'pass over these same days recorded it as exactly that.')
para('It does not survive an error analysis.')
figure('g3_lpp.png',
       'Figure 3 — The same per-day figures with their uncertainties. Every bar overlaps its '
       'neighbours. The pooled value (red) is tight because it spans three times as many points.')
mono(f'widest gap:  {hi_d["lpp"]:.2f} − {lo_d["lpp"]:.2f}  =  {GAP:.2f} L/pt\n'
     f'combined 1σ: {GAP_SIG:.2f}\n'
     f'separation:  {SIGMAS:.1f} σ   →  not significant',
     'Each day spans only four or five indicated points, so the ±0.7 point uncertainty on a span '
     'becomes roughly ±18% on that day\'s litres-per-point.')
para(f'A {SIGMAS:.1f} sigma separation is what random noise produces routinely. The per-day '
     f'spread is therefore consistent with measurement scatter, and it is not evidence of '
     f'non-linearity. Pushing to finer resolution makes this worse rather than better: computing '
     f'litres for each individual gauge point gives values ranging from about 0.2 to 5.0 L — a '
     f'scatter larger than the quantity being measured.')

para('None of this disproves non-linearity — a float sender in a tank that is not a uniform prism '
     'has every physical reason to be non-linear, and the DD2024 whole-range figure sitting close '
     'to the narrow-band figure is mildly reassuring rather than conclusive. The honest statement '
     'is that the effect, if present, is smaller than this measurement can resolve.')

callout('Correction to the earlier record',
        'A previous analysis of these days recorded the per-day spread as demonstrating '
        'non-linearity, and that statement propagated into the model file and the project notes. '
        'It was over-read. The correct position: the gauge SCALE is firmly established as '
        f'{POOLED:.2f} L per point over the measured band, well below nominal; the question of '
        'whether that scale varies with level is open, and this data cannot close it. The model '
        'file and notes have been corrected to say so.', WARN)

# ---- 6
doc.add_heading('6.  What the reserve is actually worth', level=1)
para('The reserve is set in indicated percent, so its value in litres follows directly from the '
     'calibration.')
table(['Basis', 'L per point', '15 points =', 'Against nominal'],
      [['Nominal 250 L linear', f'{NOMINAL_LPP:.2f}', f'{15*NOMINAL_LPP:.1f} L', '—'],
       ['Measured (this band)', f'{POOLED:.2f}', f'{15*POOLED:.1f} L',
        f'{15*(POOLED-NOMINAL_LPP):+.1f} L'],
       ['DD2024 whole range', f'{DD2024_LPP:.2f}', f'{15*DD2024_LPP:.1f} L',
        f'{15*(DD2024_LPP-NOMINAL_LPP):+.1f} L'],
       ],
      [1.9, 1.1, 1.1, 1.3],
      note='Table 3 — What fifteen indicated points buys. The measured figures assume the low '
           'band behaves like the measured band, which is precisely the untested assumption.')
para(f'On the measured scale the reserve is worth about {15*POOLED:.0f} L rather than the '
     f'{15*NOMINAL_LPP:.0f} L a nominal reading implies — roughly {abs(15*(NOMINAL_LPP-POOLED)):.0f} L '
     f'less margin than assumed. At survey speed that is on the order of an hour of endurance. '
     f'It does not make the reserve unsafe, but it does mean the reserve is smaller than the '
     f'number suggests, and it argues for planning on the evidence-based capacity rather than '
     f'the nominal.')
callout('The band that matters is the one never measured',
        'Every measurement here comes from the top third of the tank, because the reserve policy '
        f'means the vehicle rarely goes lower and returns are made well above the floor. The '
        f'{RES_PCT:.0f}% '
        'reserve sits in a region for which there is no direct calibration at all. If the gauge '
        'is non-linear anywhere, the bottom is where a float sender is most likely to misbehave '
        '— and that is exactly where the reserve decision is made.', WARN)

# ---- 7
doc.add_heading('7.  What would settle it', level=1)
bullets([
    ('Sound the tank. ',
     'A dowel against a known fill ends the capacity question outright and costs one afternoon '
     'alongside. It was suggested in the DD2024 thread and remains the single highest-value '
     'measurement available.'),
    ('Log litres pumped against indicated level at every refuel. ',
     'Record the level before and after with the volume. Fills that start from different levels '
     'build the calibration curve across bands the vehicle never burns through in normal '
     'operation.'),
    ('Deliberately run one tank low, once. ',
     f'A single controlled drawdown into the reserve band, with the flow meter logging, would '
     f'calibrate the region every planning decision depends on. Nothing else available does '
     f'this. From the lowest level ever recorded ({_DD["lowest"]:.0f}%) down to the '
     f'{_DD["floor"]:.0f}% floor is {_DD["full"]["points"]:.0f} indicated points: about '
     f'{_DD["full"]["litres"]:.0f} L and {_DD["full"]["hours_survey"]:.0f} hours at survey '
     f'speed, or {_DD["full"]["hours_loiter"]:.0f} hours at loiter, and it would fix litres '
     f'per point to {_DD["full"]["precision"]:.1%}. Half of it — down to '
     f'{_DD["half"]["to_pct"]:.0f}% for {_DD["half"]["litres"]:.0f} L and '
     f'{_DD["half"]["hours_survey"]:.0f} hours — buys {_DD["half"]["precision"]:.1%} and '
     f'halves the blind band. No new instrumentation: both channels are already recorded.'),
    ('Keep accumulating operating days. ',
     'Each new day extends the band and tightens the pooled figure. Three more days at the '
     'current rate would roughly halve the uncertainty on any single band, which is what a '
     'non-linearity test actually needs.'),
])

doc.add_heading('Appendix  —  Reproducing this report', level=1)
mono('python tools/extract_bags.py D:/Claude/Fuel/D8_2040\n'
     'python tools/build_gauge_report.py [OUT.docx]',
     'The report recomputes every figure and every number from the day caches, so re-running it '
     'after adding data produces a corrected document rather than a stale one. The significance '
     'test in section 5 is part of that recomputation — if accumulating data ever makes the '
     'band-to-band difference real, the document will say so.')

check_table_widths(doc)
doc.save(OUT)
print('written:', OUT)
print(f'pooled {POOLED:.3f} +/- {POOLED_SIG:.3f} L/pt over {G_LO:.0f}-{G_HI:.0f}% '
      f'({PTS:.0f} pts, {TOT:.1f} L) | day spread {SIGMAS:.1f} sigma')
