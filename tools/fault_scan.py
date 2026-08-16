"""Scan every bag for PLC fault/alarm transitions, and cache the result.

    python tools/fault_scan.py [SESSION ...]      # all sessions by default

The extraction pipeline pulls only the six channels the fuel model needs, so the
PLC's fault and alarm booleans are not in the NPZ caches. This decodes
Telemetry3 directly and records EDGES rather than samples: a boolean true for
six hours is one event with a duration, not 28,000 rows.

Writes tools/rosbags/fault_events.json, which build_methods_doc.py reads for
§2.1 and §4.3 — so the sensor-health figures in that document cannot be typed by
hand or drift from the bags.

WHY EDGES AND NOT A COUNT. "How many samples were faulted" is a function of how
long the vehicle happened to run; "how many times did it assert" is a property of
the sensor. The second is what tells an intermittent fault from a dead channel,
and it is what §2.1 needs to justify using a different channel.

Two behaviours worth knowing:

  * CHECKPOINTED PER SESSION. The first version of this wrote only at the end,
    ran forty minutes, was killed at exit 255 with no traceback, and lost all
    304 events it had found. Each session is now durable as it completes and a
    re-run skips it.
  * DO NOT RUN IT ALONGSIDE ANOTHER DECODE. That kill happened while a second
    MCAP reader was working over the same ~10 GB. Run this on its own.
"""
import datetime as dt
import json
import sys
from pathlib import Path

from mcap_ros2.reader import read_ros2_messages

HERE = Path(__file__).resolve().parent
BAGS = Path(sys.argv[1]) if False else HERE.parent / 'D8_2040'
TOPIC = '/drix_8/cortix/plc/telemetry/telemetry3'
OUT = HERE / 'rosbags' / 'fault_events.json'

# Normal is False; every departure is an event.
NORMAL_FALSE = [
    'fuel_level_sensor_fault', 'fuel_level_is_low', 'fuel_level_is_very_low',
    'fuel_in_water1', 'fuel_in_water2', 'water_in_fuel',
    'sea_water_valve_close', 'sea_water_valve_cmd_state',
    'sea_water_valve_engine_shutdown', 'sea_water_valve_engine_start_block',
    'engine_alarm', 'engine_warning', 'engine_oil_pressure_fault',
    'engine_coolant_temp_fault', 'engine_exhaust_temp_sensor_fault',
    'engine_actuator_overcurrent', 'shaft_sensor_rpm_fault',
    'battery_voltage_sensor_fault', 'transmission_oil_temp_sensor_fault',
    'loss_speed_sensor', 'speed_request_invalid', 'invalid_checksum',
    'message_count_error', 'message_rate_error', 'user_shutdown',
    'smoke_in_elec_compartment', 'smoke_in_engine_compartment',
    'water_in_elec_compartment', 'water_in_engine_compartment',
    'gearbox_oil_temperature_alarm', 'gearbox_oil_temperature_warning',
    'elec_compartment_temperature_alarm', 'engine_compartment_temperature_alarm',
    'elec_compartment_humidity_alarm', 'engine_compartment_humidity_alarm',
    'service_due', 'unsuported_mode', 'eeprom_read',
]
# CONFIGURATION, not health. `has_sea_water_valve` says whether the hull is
# FITTED with one; on this DriX it reads False throughout, which makes every
# sea_water_valve_* field inert rather than failed. An earlier version treated
# it as healthy-when-true and reported absent optional hardware as a fault.
CONFIG = ['has_sea_water_valve', 'sea_water_valve_open']
FIELDS = NORMAL_FALSE + CONFIG


def main():
    sessions = sorted(p for p in BAGS.iterdir() if p.is_dir())
    if len(sys.argv) > 1:
        sessions = [p for p in sessions if p.name in sys.argv[1:]]
    ckpt = OUT.with_name(OUT.stem + '_by_session')
    ckpt.mkdir(parents=True, exist_ok=True)

    state, events, counts, unreadable = {}, [], {}, []
    for sess in sessions:
        cp = ckpt / f'{sess.name}.json'
        if cp.exists():
            prior = json.load(open(cp))
            events.extend(prior['events'])
            for k, v in prior['counts'].items():
                counts[k] = counts.get(k, 0) + v
            unreadable.extend(prior['unreadable'])
            state.update(prior['end_state'])
            print(f'  {sess.name}: checkpoint, {len(prior["events"])} events')
            continue
        n0, u0, c0 = len(events), len(unreadable), dict(counts)
        segs = sorted(sess.glob('*.mcap'),
                      key=lambda p: int(p.stem.rsplit('_', 1)[1]))
        for seg in segs:
            try:
                for msg in read_ros2_messages(str(seg), topics=[TOPIC]):
                    m, ts = msg.ros_msg, msg.log_time_ns
                    for f in FIELDS:
                        v = getattr(m, f, None)
                        if v is None:
                            continue
                        counts[f] = counts.get(f, 0) + 1
                        if state.get(f) is None:
                            state[f] = v
                            if v:
                                events.append(dict(field=f, to=True, ts=ts,
                                                   session=sess.name, first=True))
                        elif v != state[f]:
                            events.append(dict(field=f, to=bool(v), ts=ts,
                                               session=sess.name, first=False))
                            state[f] = v
            except Exception as exc:
                unreadable.append([sess.name, seg.name, type(exc).__name__])
            print(f'  {sess.name}/{seg.name}: {len(events)} events', flush=True)
        json.dump(dict(events=events[n0:],
                       counts={k: counts[k] - c0.get(k, 0) for k in counts},
                       unreadable=unreadable[u0:], end_state=state),
                  open(cp, 'w'), indent=1)
        print(f'  == {sess.name} checkpointed ==', flush=True)

    json.dump(dict(events=events, counts=counts, unreadable=unreadable,
                   fields=FIELDS, config=CONFIG,
                   sessions=[s.name for s in sessions],
                   generated=dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')),
              open(OUT, 'w'), indent=1)
    print(f'\nwrote {OUT} ({len(events)} transitions, '
          f'{len(unreadable)} unreadable segments)')


if __name__ == '__main__':
    main()
