"""Extract fuel/nav channels from DriX MCAP bags into per-day NPZ caches.

Usage:
    python tools/extract_bags.py [BAG_ROOT]

BAG_ROOT defaults to E:/fuel/D8_2040 and is expected to hold day folders
(e.g. 2026-08-04T00/) of connectivity-box .mcap segments. Caches land in
tools/rosbags/<day>.npz and are skipped if already present.

Extra dependencies (the planner itself is stdlib-only, this pipeline is not):
    pip install numpy mcap mcap-ros2-support

Topics kept deliberately lean — decoding is the cost driver:
    telemetry3        fuel_rate_lph, shaft_sensor_rpm, engine_on, alternators,
                      total_fuel_used_l  (~1 Hz)
    vehicle_status    gasoline_level_percent (the tank gauge!), thruster_rpm
    light_gps         sog (m/s), cog, lat, lon
    light_ins         heading, heave, roll, pitch   (subsampled 1-in-3)
    fuel_consumption  Exail's static-model outputs   (subsampled 1-in-10)

Hard-won notes (Aug 2026 run):
  * Segment files sort LEXICALLY (_1, _10, _11, _2 …). We natural-sort here,
    and the fit script additionally time-sorts on load — belt and braces.
  * The bags gained a per-configuration folder (D8_2040) after the original
    refit, so the default root is one level deeper than it first was. A root
    pointing at the wrong level yields day folders holding no .mcap at all,
    which is why extract_day() refuses to write an empty cache.
  * The shaft-RPM sensor was faulted throughout (0 / 65527); thruster_rpm from
    vehicle_status is the working RPM channel.
  * SOG is metres per second.
"""
import re
import sys
import time
from pathlib import Path

import numpy as np
from mcap_ros2.reader import read_ros2_messages

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'E:/fuel/D8_2040')
OUT = Path(__file__).parent / 'rosbags'
OUT.mkdir(exist_ok=True)

T3 = '/drix_8/cortix/plc/telemetry/telemetry3'
VS = '/drix_8/cortix/plc/vehicle_status'
GPS = '/drix_8/cortix/communications/topic_simplifier/light_gps'
INS = '/drix_8/cortix/communications/topic_simplifier/light_ins'
FC = '/drix_8/hardware/fuel_cons_manager/fuel_consumption'
TRIM = '/drix_8/hardware/trimmer/status'
TOPICS = [T3, VS, GPS, INS, FC, TRIM]

# Topics we WANT but which were absent from the Aug 2026 bags. Each day's first
# segment is probed and any arrival is announced loudly, so the moment the
# weather sensor or raw PHINS stream is bridged into the recording, the
# extraction says so instead of silently ignoring it.
WISHLIST_TERMS = ('weather', 'wind', 'meteo', 'airmar', 'gill', 'maximet',
                  'anemo', 'true_wind', 'apparent', 'phins_data', 'imu',
                  'ins_raw', 'attitude')


def natural_key(p: Path):
    """Sort …_2.mcap before …_10.mcap."""
    m = re.search(r'_(\d+)\.mcap$', p.name)
    return (p.name.rsplit('_', 1)[0], int(m.group(1)) if m else 0)


def extract_day(day_dir: Path) -> None:
    out_file = OUT / f"{day_dir.name.replace('T00', '')}.npz"
    if out_file.exists():
        print(f'  {out_file.name} exists — skipping')
        return
    cols = {
        't3': {k: [] for k in ('t', 'fuel_lph', 'rpm', 'engine_on', 'alt1', 'alt2',
                               'total_l', 'engine_hours', 'engine_load')},
        'vs': {k: [] for k in ('t', 'gas_pct', 'thruster_rpm')},
        'gps': {k: [] for k in ('t', 'sog', 'cog', 'lat', 'lon')},
        'ins': {k: [] for k in ('t', 'heading', 'heave', 'roll', 'pitch')},
        'fc': {k: [] for k in ('t', 'c4', 'c8', 'c12', 'cur', 'dynamic')},
        'trim': {k: [] for k in ('t', 'pos', 'zero')},
    }
    n_ins = n_fc = 0
    files = sorted(day_dir.glob('*.mcap'), key=natural_key)
    if not files:
        raise SystemExit(
            f'{day_dir} holds no .mcap segments — is BAG_ROOT pointing one '
            f'level too high? (bags live in <root>/<day>T00/*.mcap)')
    # wishlist probe on the first segment's channel index (cheap summary read)
    from mcap.reader import make_reader
    with open(files[0], 'rb') as fh:
        summ = make_reader(fh).get_summary()
        counts = summ.statistics.channel_message_counts
        for cid, ch in summ.channels.items():
            tl = ch.topic.lower()
            if any(k in tl for k in WISHLIST_TERMS) and counts.get(cid, 0) > 0                     and 'light_ins' not in tl and '/params' not in tl:
                print(f'  *** NEW SENSOR DATA: {ch.topic} '
                      f'({counts[cid]:,} msgs) — extend the extraction! ***')
    skipped = []
    for i, f in enumerate(files, 1):
        t0 = time.time()
        n = 0
        try:
            stream = list(read_ros2_messages(str(f), topics=TOPICS))
        except Exception as exc:                       # noqa: BLE001
            # A segment still being written has no valid footer/index yet, and
            # the reader raises rather than returning what it has. With data
            # arriving daily the newest day routinely has one open segment, so
            # skip it and keep the rest of the day instead of losing everything.
            skipped.append(f.name)
            print(f'  [{i:2d}/{len(files)}] {f.name}: UNREADABLE, skipped '
                  f'({type(exc).__name__}) — still recording?', flush=True)
            continue
        for m in stream:
            n += 1
            t = m.log_time_ns / 1e9
            r = m.ros_msg
            topic = m.channel.topic
            if topic == T3:
                c = cols['t3']
                c['t'].append(t)
                c['fuel_lph'].append(r.fuel_rate_lph)
                c['rpm'].append(r.shaft_sensor_rpm)
                c['engine_on'].append(r.engine_on)
                c['alt1'].append(r.alternator_1_current)
                c['alt2'].append(r.alternator_2_current)
                c['total_l'].append(r.total_fuel_used_l)
                c['engine_hours'].append(r.engine_hours)
                c['engine_load'].append(r.engine_load)
            elif topic == VS:
                c = cols['vs']
                c['t'].append(t)
                c['gas_pct'].append(r.gasoline_level_percent)
                c['thruster_rpm'].append(r.thruster_rpm)
            elif topic == GPS:
                c = cols['gps']
                c['t'].append(t)
                c['sog'].append(r.sog)
                c['cog'].append(r.cog)
                c['lat'].append(r.latitude)
                c['lon'].append(r.longitude)
            elif topic == INS:
                n_ins += 1
                if n_ins % 3:
                    continue
                c = cols['ins']
                c['t'].append(t)
                c['heading'].append(r.heading)
                c['heave'].append(r.heave)
                c['roll'].append(r.roll)
                c['pitch'].append(r.pitch)
            elif topic == TRIM:
                c = cols['trim']
                c['t'].append(t)
                c['pos'].append(r.position_deg)
                c['zero'].append(r.zero_position_deg)
            elif topic == FC:
                n_fc += 1
                if n_fc % 10:
                    continue
                c = cols['fc']
                c['t'].append(t)
                c['c4'].append(r.consumption_4kn)
                c['c8'].append(r.consumption_8kn)
                c['c12'].append(r.consumption_12kn)
                c['cur'].append(r.current_consumption)
                c['dynamic'].append(r.dynamic_model_used)
        print(f'  [{i:2d}/{len(files)}] {f.name}: {n:,} msgs in {time.time()-t0:.1f}s',
              flush=True)
    arrays = {}
    for grp, fields in cols.items():
        for k, v in fields.items():
            arrays[f'{grp}_{k}'] = np.asarray(v)
    if not len(cols['t3']['t']):
        print(f'  no telemetry decoded for {day_dir.name} — cache NOT written '
              f'(all segments unreadable?)', flush=True)
        return
    np.savez_compressed(out_file, **arrays)
    print(f'  wrote {out_file.name}: '
          + ', '.join(f'{g}={len(cols[g]["t"]):,}' for g in cols), flush=True)
    if skipped:
        # Do not leave a partial day looking complete: the cache is skipped on
        # the next run, so record what is missing from it.
        (out_file.with_suffix('.partial')).write_text(
            'segments skipped as unreadable (delete this file and the .npz to '
            'retry once recording has finished):\n' + '\n'.join(skipped) + '\n',
            encoding='utf-8')
        print(f'  ⚠ {len(skipped)} segment(s) skipped — see '
              f'{out_file.with_suffix(".partial").name}', flush=True)


if __name__ == '__main__':
    days = sorted(d for d in ROOT.iterdir() if d.is_dir())
    print(f'{len(days)} day folders under {ROOT}')
    for d in days:
        print(f'== {d.name}', flush=True)
        extract_day(d)
    print('DONE')
