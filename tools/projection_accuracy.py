"""Measure what a tidal projection is worth, against the model's own output.

    python tools/projection_accuracy.py

When the forecast cannot reach a requested time, `Currents.at_best` substitutes
the value a whole M2 period away. This is the measurement behind that choice and
behind `currents.MAX_PROJECT_CYCLES`: for every hour a cached cycle covers, it
compares the real value against what a projection from n cycles back would have
said, and against the two things a projection has to beat —

    PERSISTENCE   hold the last value in the span ("just reuse the end")
    SLACK         assume zero, the answer the planner refuses to invent

Prints the table that `currents.PROJECTION_ACCURACY` records. Re-run it when a
materially different cycle is cached and update that block if the numbers move;
it carries the cycle it came from so the two cannot silently disagree.

Needs a cached cycle (`python currents.py fetch`). Reads nothing over the
network itself.
"""
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import currents as ofs  # noqa: E402

# Spread across the operating area rather than one berth: the tide at the bay
# entrance is several times the tide at Lewes, and a single point would flatter
# or damn the method by where it was taken.
POINTS = [('DriX berth, Lewes', 38.78965, -75.16094),
          ('Bay entrance', 38.8536, -75.0770),
          ('Mid bay', 39.15, -75.25),
          ('Upper bay', 39.45, -75.45)]


def series(cur, lat, lon):
    out = []
    for t in cur.frame_times():
        got = cur.at(lat, lon, t)
        out.append(None if got is None else (got[2], got[3]))   # u, v in m/s
    return out


def err_kt(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1]) * ofs.MS_TO_KT


def rms(xs):
    return math.sqrt(statistics.fmean(xs)) if xs else float('nan')


def main():
    cur = ofs.Currents()
    span_h = (cur.end - cur.start).total_seconds() / 3600.0
    print(f'cycle {cur.tag}')
    print(f'span  {cur.start:%Y-%m-%d %H:%M}Z .. {cur.end:%Y-%m-%d %H:%M}Z '
          f'({span_h:.0f} h)\n')
    print(f'{"point":<20}{"n":>3}{"shift h":>9}{"projected":>11}'
          f'{"persistence":>13}{"slack":>8}{"pairs":>7}')
    print('-' * 71)

    pooled = {n: [] for n in range(1, ofs.MAX_PROJECT_CYCLES + 1)}
    pooled_p, pooled_s = [], []
    for name, lat, lon in POINTS:
        s = series(cur, lat, lon)
        if not any(v is not None for v in s):
            print(f'{name:<20}  (no model water here — skipped)')
            continue
        last = next(v for v in reversed(s) if v is not None)
        for n in range(1, ofs.MAX_PROJECT_CYCLES + 1):
            shift = n * ofs.M2_PERIOD_H
            proj, pers, slack = [], [], []
            for i, t in enumerate(cur.frame_times()):
                if s[i] is None:
                    continue
                src = (t - cur.start).total_seconds() / 3600.0 - shift
                if src < 0:
                    continue
                k = int(src)
                if k + 1 >= len(s) or s[k] is None or s[k + 1] is None:
                    continue
                f = src - k
                est = (s[k][0] + (s[k + 1][0] - s[k][0]) * f,
                       s[k][1] + (s[k + 1][1] - s[k][1]) * f)
                proj.append(err_kt(s[i], est) ** 2)
                pers.append(err_kt(s[i], last) ** 2)
                slack.append(err_kt(s[i], (0.0, 0.0)) ** 2)
            if not proj:
                continue
            pooled[n].extend(proj)
            pooled_p.extend(pers)
            pooled_s.extend(slack)
            print(f'{name:<20}{n:>3}{shift:>9.1f}{rms(proj):>11.3f}'
                  f'{rms(pers):>13.3f}{rms(slack):>8.3f}{len(proj):>7}')

    print('-' * 71)
    total = sum(len(v) for v in pooled.values())
    for n, errs in pooled.items():
        if errs:
            print(f'  pooled n={n}: projection {rms(errs):.3f} kt RMS '
                  f'over {len(errs)} samples')
    print(f'  pooled persistence {rms(pooled_p):.3f} kt · '
          f'slack {rms(pooled_s):.3f} kt')

    rec = ofs.PROJECTION_ACCURACY
    print(f'\ncurrents.PROJECTION_ACCURACY records {rec["projected_rms_kt"]} '
          f'from {rec["cycle"]} ({rec["samples"]} samples)')
    if rec['cycle'] != cur.tag:
        print('  NOTE: that was a different cycle. If these numbers have moved '
              'materially, update the block — it is what the documents quote.')
    print(f'  measured here: '
          f'{ {n: round(rms(v), 2) for n, v in pooled.items() if v} } '
          f'({total} samples)')


if __name__ == '__main__':
    main()
