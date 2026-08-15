"""The reserve-band drawdown numbers, in one place.

`reserve_band.py` reports these to the console and both Word reports quote
them, so they live here rather than being computed three times. Nothing in this
module prints or writes; it is arithmetic over the caches and `model.json`.

Everything is computed under the ADOPTED gauge reading. That matters: under
reading (A) a drawdown span costs the profile integral, not span x L/point,
because the band below the calibrated one is richer. Quoting the flat figure
would understate the fuel and the time the experiment needs.
"""
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CACHE_DEFAULT = HERE / 'rosbags'

# 1% gauge quantisation -> ~0.5 pt at each endpoint, ~0.7 pt over a span. This
# sets every precision claim about a drawdown.
SIG_PTS = 0.7
# A day burning less than this says nothing about the gauge scale.
MATERIAL_L = 2.0


def load(path):
    """Cache loader with the pipeline's time-sort.

    The sort is not optional: segments can land out of lexical order, and
    np.interp on an unsorted x silently returns nonsense rather than failing.
    """
    z = np.load(path)
    d = {k: z[k] for k in z.files}
    for grp in ('t3', 'vs'):
        t = d.get(f'{grp}_t')
        if t is None or not len(t):
            continue
        order = np.argsort(t, kind='stable')
        for k in list(d):
            if k.startswith(grp + '_'):
                d[k] = d[k][order]
    return d


# A rise this big in the smoothed gauge is a refill, not sensor noise.
GAUGE_RISE = 1.0
# Below these a span says nothing: an idle stretch's gauge wanders a point or
# two on essentially no fuel.
GAUGE_MIN_L = 2.0
GAUGE_MIN_PTS = 1.0


def minute_medians(t, x, bucket_s=60):
    """Robust 1-minute series — the raw gauge is quantised and noisy."""
    t = np.asarray(t, float)
    if len(t) == 0:
        return np.array([]), np.array([])
    k = ((t - t[0]) // bucket_s).astype(int)
    keys = np.unique(k)
    return (np.array([t[k == q][0] for q in keys]),
            np.array([np.median(np.asarray(x, float)[k == q]) for q in keys]))


def drawdown_spans(t, gas):
    """(t0, t1, gas0, gas1) for each FALLING stretch, split at every refill.

    Litres per indicated point is a slope measured down a drawdown, so it is
    only defined while the level falls. Before 2026-08-15 the data happened to
    be one long fall and every consumer took a day's first and last reading. The
    moment it contained refuels — 08-10T11 rose 15 points, 08-14 rose 39 — that
    assumption produced a NEGATIVE litres-per-point for the refuel day, a pooled
    figure of 59 L/point against a true ~2.06 in the fit, and 5.58 L/point in
    the gauge report. Hence this: cut at every refill, never span one, never
    span two sessions.
    """
    bt, bg = minute_medians(t, gas)
    if len(bt) < 3:
        return []
    spans, s = [], 0
    for i in range(1, len(bt)):
        if bg[i] - bg[s:i].min() > GAUGE_RISE:          # the level rose: refill
            if bg[s] - bg[i - 1] > 0:
                spans.append((bt[s], bt[i - 1], bg[s], bg[i - 1]))
            s = i
    if bg[s] - bg[-1] > 0:
        spans.append((bt[s], bt[-1], bg[s], bg[-1]))
    return spans


def litres_between(t, lph, t0, t1):
    """Metered litres over a window, bridging recording gaps rather than
    integrating across them."""
    t = np.asarray(t, float)
    m = (t >= t0) & (t <= t1)
    tt, ll = t[m], np.asarray(lph, float)[m]
    if len(tt) < 2:
        return 0.0
    dt = np.diff(tt)
    good = (dt > 0) & (dt < 30)
    return float(np.sum(ll[:-1][good] * dt[good]) / 3600.0)


def usable_spans(cache=CACHE_DEFAULT):
    """Every drawdown in the cache worth calibrating on, with its own slope.

    This is the one place the gauge scale is derived from. `fit_em2040.py`,
    `build_gauge_report.py` and `reserve_band.py` all read it, so they cannot
    disagree about what a point is worth.
    """
    out = []
    for p in sorted(Path(cache).glob('2026-*.npz')):
        d = load(p)
        t = d['t3_t']
        if len(t) < 50 or not len(d.get('vs_t', [])):
            continue
        lph = d['t3_fuel_lph'].astype(float)
        gas = np.interp(t, d['vs_t'], d['vs_gas_pct'].astype(float))
        for t0, t1, g0, g1 in drawdown_spans(t, gas):
            litres, pts = litres_between(t, lph, t0, t1), g0 - g1
            if litres < GAUGE_MIN_L or pts < GAUGE_MIN_PTS:
                continue
            out.append(dict(day=p.stem, litres=litres, points=pts,
                            from_pct=g0, to_pct=g1, l_per_point=litres / pts,
                            sig=(litres / pts) * SIG_PTS / pts))
    return out


def pooled_scale(spans=None, cache=CACHE_DEFAULT):
    """(L/point, weighted scatter, litres, points, band_lo, band_hi, n).

    The denominator is the TOTAL DRAWDOWN TRAVELLED, not band_hi - band_lo: the
    spans are separate falls that may revisit the same part of the gauge, so the
    band only says where it looked.
    """
    spans = usable_spans(cache) if spans is None else spans
    if not spans:
        return None
    L = sum(s['litres'] for s in spans)
    P = sum(s['points'] for s in spans)
    rate = L / P
    var = sum(s['points'] * (s['l_per_point'] - rate) ** 2 for s in spans) / P
    return (rate, float(np.sqrt(var)), L, P,
            min(s['to_pct'] for s in spans), max(s['from_pct'] for s in spans),
            len(spans))


def days(cache=CACHE_DEFAULT):
    """Per-day gauge and fuel summary, oldest first."""
    out = []
    for p in sorted(Path(cache).glob('2026-*.npz')):
        d = load(p)
        t, lph = d['t3_t'], d['t3_fuel_lph'].astype(float)
        if len(t) < 50 or not len(d.get('vs_t', [])):
            continue
        gas = np.interp(t, d['vs_t'], d['vs_gas_pct'].astype(float))
        dts = np.diff(t, prepend=t[0])
        dts[dts > 30] = 0                      # bridge recording gaps
        cum = np.cumsum(lph * dts) / 3600.0
        out.append(dict(day=p.stem, gas=gas, cum=cum, total=float(cum[-1]),
                        g0=float(np.median(gas[:600])),
                        g1=float(np.median(gas[-600:])),
                        lowest=float(gas.min())))
    if not out:
        raise SystemExit(f'no usable caches in {cache} — run extract_bags.py first')
    return out


def spec(model, cache=CACHE_DEFAULT):
    """Everything the reports quote about the drawdown, computed once.

    `model` is an engine.Model. Returns plain floats and ints so a document
    builder can format them without knowing any of this.
    """
    d = model.data
    prof = model.gauge_profile
    gc = d['gauge_calibration']
    lpp = gc['l_per_point']
    band_lo, band_hi = gc['band_pct']
    floor = d['reserve']['default_fraction'] * 100.0
    gondola = d['gondolas'].get('default', 'em2040')
    kt = d['references']['survey_speed_kt']
    lph = model.fuel_rate_lph(model.rpm_for_speed(kt, gondola), gondola)
    loiter = d['gondolas']['options'][gondola]['loiter']['lph']
    vol = model.tank_volume_l

    dd = days(cache)
    lowest = min(x['lowest'] for x in dd)
    meas_pts = band_hi - band_lo

    def run(span_pts):
        """Fuel, time and precision for a drawdown of `span_pts` from `lowest`."""
        to = lowest - span_pts
        litres = prof.litres_between(max(0.0, to), lowest)
        return dict(points=span_pts, to_pct=to, litres=litres,
                    hours_survey=litres / lph if lph > 0 else 0.0,
                    hours_loiter=litres / loiter if loiter > 0 else 0.0,
                    precision=SIG_PTS / span_pts if span_pts > 0 else float('inf'),
                    leaves_uncalibrated=max(0.0, to - floor))

    full_span = lowest - floor
    mission = prof.litres_between(floor, 100.0)

    # Exposure. Under reading (A) the drawings pin the whole-gauge integral, so
    # the unmeasured LEVEL is fixed and only the SHAPE is free; redistributing
    # between the bands above and below the floor still moves mission fuel, and
    # a richer bottom means LESS of it. Under (B) the level itself is free.
    if vol and model.gauge_reading == 'A':
        below = [s for s in prof.segments if s[1] <= band_lo]
        above = [s for s in prof.segments if s[0] >= band_hi]
        lo_pts = sum(hi - lo for lo, hi, _ in below)
        hi_pts = sum(hi - lo for lo, hi, _ in above)
        lo_rate = below[0][2] if below else 0.0
        unmeasured_l = vol - meas_pts * lpp

        def shape(err):
            lam_lo = lo_rate * (1 + err)
            lam_hi = (unmeasured_l - lo_pts * lam_lo) / hi_pts if hi_pts else 0.0
            whole = lo_pts * lam_lo + meas_pts * lpp + hi_pts * lam_hi
            if abs(whole - vol) > 1e-6:            # the invariant that makes it mean anything
                raise ValueError(f'shape {err:+.0%} holds {whole:.3f} L, not {vol}')
            return ((lo_pts - floor) * lam_lo + meas_pts * lpp + hi_pts * lam_hi,
                    lam_lo, lam_hi)

        swing = abs(shape(0.10)[0] - mission)
        exposure = dict(kind='shape', swing_l=swing, swing_h=swing / lph,
                        swing_nm=swing * (kt / lph), shape=shape,
                        lo_pts=lo_pts, hi_pts=hi_pts, lo_rate=lo_rate,
                        unmeasured_l=unmeasured_l)
    else:
        uncal = (100.0 - floor) - meas_pts
        swing = uncal * lpp * 0.10
        exposure = dict(kind='level', swing_l=swing, swing_h=swing / lph,
                        swing_nm=swing * (kt / lph))

    return dict(
        days=dd, lowest=lowest, floor=floor, band=(band_lo, band_hi),
        blind_points=lowest - floor, meas_pts=meas_pts,
        uncalibrated_planning_points=(100.0 - floor) - meas_pts,
        mission_litres=mission, survey_kt=kt, survey_lph=lph, loiter_lph=loiter,
        full=run(full_span), half=run(full_span / 2.0),
        targets={t: run(SIG_PTS / t) for t in (0.05, 0.03, 0.02, 0.01)},
        exposure=exposure, reading=model.gauge_reading, tank_volume_l=vol,
    )
