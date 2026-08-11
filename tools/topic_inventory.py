"""Inventory every ROS 2 topic in the DriX MCAP bags and classify it for the
topic-reference document.

Usage:
    python tools/topic_inventory.py [BAG_ROOT]        # -> tools/topic_inventory.json
    node tools/build_topic_doc.js                     # -> D:\Claude\ROS2\...docx
    powershell -File tools/bake_toc.ps1               # fill in the contents page

BAG_ROOT defaults to E:/fuel/D8_2040 — the same root `extract_bags.py` uses —
and is expected to hold day folders (e.g. 2026-08-04T00/) of connectivity-box
.mcap segments.

Extra dependencies (the planner itself is stdlib-only, this pipeline is not):
    pip install pyyaml mcap

This is a *summary-only* pass: it reads each bag's metadata.yaml and the MCAP
summary/schema sections, never the message stream, so it runs in seconds over
77 hours of recording where `extract_bags.py` takes tens of minutes.

Hard-won notes (Aug 2026 run):
  * The 2026-08-07 folder has no metadata.yaml (the bag was still open when it
    was copied) and is missing segment _4. Its counts come from the MCAP
    summary sections instead; the gap is called out in the document.
  * A topic with zero messages is still a topic — the recorder registers
    subscriptions that never fire. Those are kept and marked silent, because
    "this capability existed but was never used" is itself an answer.
  * Descriptions live in DESC below, not in the document builder. The builder
    is presentation only; this file is the content.
"""
import json
import re
import sys
from pathlib import Path

import yaml
from mcap.reader import make_reader

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'E:/fuel/D8_2040')
OUT = Path(__file__).parent / 'topic_inventory.json'

# --------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------


def scan(root: Path):
    """Return (day_meta, topics, schemas) from bag metadata + MCAP summaries."""
    topics, day_meta, schemas = {}, {}, {}

    for yml in sorted(root.glob('*/metadata.yaml')):
        info = yaml.safe_load(yml.read_text())['rosbag2_bagfile_information']
        day_meta[yml.parent.name] = {
            'duration_s': info['duration']['nanoseconds'] / 1e9,
            'message_count': info['message_count'],
            'files': len(info.get('relative_file_paths', [])),
        }
        for t in info['topics_with_message_count']:
            meta = t['topic_metadata']
            rec = topics.setdefault(meta['name'], {'type': meta['type'], 'days': {}})
            rec['days'][yml.parent.name] = t['message_count']

    # Days whose metadata.yaml is absent: rebuild counts from MCAP summaries.
    for day_dir in sorted(d for d in root.iterdir() if d.is_dir()):
        if day_dir.name in day_meta:
            continue
        counts, t_min, t_max = {}, None, None
        for f in sorted(day_dir.glob('*.mcap')):
            with open(f, 'rb') as fh:
                summ = make_reader(fh).get_summary()
            if summ is None or summ.statistics is None:
                continue
            st = summ.statistics
            t_min = st.message_start_time if t_min is None else min(t_min, st.message_start_time)
            t_max = st.message_end_time if t_max is None else max(t_max, st.message_end_time)
            for cid, n in st.channel_message_counts.items():
                ch = summ.channels[cid]
                typ = summ.schemas[ch.schema_id].name if ch.schema_id in summ.schemas else '?'
                counts[(ch.topic, typ)] = counts.get((ch.topic, typ), 0) + n
        day_meta[day_dir.name] = {
            'duration_s': (t_max - t_min) / 1e9 if t_min is not None else 0.0,
            'message_count': sum(counts.values()),
            'files': len(list(day_dir.glob('*.mcap'))),
            'no_metadata_yaml': True,
        }
        for (name, typ), n in counts.items():
            topics.setdefault(name, {'type': typ, 'days': {}})['days'][day_dir.name] = n

    # Schema text, first definition wins (identical across days on one firmware).
    for day in sorted(day_meta):
        segments = sorted((root / day).glob('*.mcap'))
        if not segments:
            continue
        with open(segments[0], 'rb') as fh:
            summ = make_reader(fh).get_summary()
        if summ is None:
            continue
        for sch in summ.schemas.values():
            schemas.setdefault(sch.name, sch.data.decode('utf-8', 'replace'))

    for rec in topics.values():
        dur = sum(day_meta[d]['duration_s'] for d in rec['days'])
        rec['total'] = sum(rec['days'].values())
        rec['rate_hz'] = round(rec['total'] / dur, 3) if dur > 0 else None

    return dict(sorted(day_meta.items())), dict(sorted(topics.items())), schemas


# --------------------------------------------------------------------------
# Descriptions — the content of the reference document
# --------------------------------------------------------------------------

DESC = {
    # --- root / system
    '/diagnostics': 'Raw ROS 2 diagnostics firehose from every node and driver; highest-volume topic in the recording. Aggregated downstream into /diagnostics_agg.',
    '/diagnostics_agg': 'Aggregated diagnostics tree (diagnostic_aggregator output) at ~1 Hz — the organised view of /diagnostics.',
    '/diagnostics_toplevel_state': 'Single roll-up status (OK / WARN / ERROR) of the whole aggregated diagnostics tree.',
    '/journal': 'ROS 2 log stream (rosout equivalent) from all nodes at ~14 Hz. The primary event-forensics topic: mode changes, faults and driver messages appear here with timestamps.',
    '/rosmon_robobox/state': 'rosmon process-monitor state for the robobox launch group: per-node running / crashed / restart counts.',
    '/launcher/launchers_manager/list': 'Catalogue of launchable process groups known to the launcher manager and their current run states.',
    '/launcher/DDS12/manual_action_ping': 'Manual keep-alive ping slot for the DDS12 launcher entry. No traffic in these logs.',
    '/launcher/DDS13/manual_action_ping': 'Manual keep-alive ping slot for the DDS13 launcher entry. No traffic in these logs.',
    '/mothership/size': 'Mothership principal dimensions broadcast (for relative-position geometry). No traffic in these logs.',
    # --- control-centre nodes
    '/cortix/communications/bridge_comm_masters/network_info': 'Fleet communication-bridge master status at ~1 Hz: per-vehicle link visibility as seen from the control-centre side of the bridge.',
    '/cortix/communications/cortix_software_version': 'Cortix software version string for the control-centre stack (latched).',
    '/cortix/communications/d_iridium/msg_status': 'Iridium SBD message send/receive accounting from the control-centre modem driver.',
    '/cortix/communications/d_iridium/rec_iridium_data': 'Raw received Iridium SBD payloads (vehicle snapshots arriving over satellite).',
    '/cortix/communications/hmi_heartbeat': 'Operator HMI alive-tick received by the connectivity box (~3 Hz).',
    '/cortix/communications/iridium/d_iridium/iridium_direct_ip_status': 'Iridium DirectIP server session status (the ground-side path Iridium messages arrive through).',
    '/cortix/communications/iridium/iridium_events': 'Iridium link event stream on the control-centre side (session open/close, failures).',
    '/cortix/communications/rosbridge_server_api/client_count': 'Number of WebSocket clients on the API rosbridge server.',
    '/cortix/communications/rosbridge_server_api/connected_clients': 'Identity list of WebSocket clients connected to the API rosbridge server.',
    '/cortix/communications/rosbridge_server_api/service_call_events': 'Audit stream of ROS service calls made through the API rosbridge. No traffic in these logs.',
    '/cortix/communications/rosbridge_server_cpp/client_count': 'Number of WebSocket clients on the main rosbridge server (HMI connections).',
    '/cortix/communications/rosbridge_server_cpp/connected_clients': 'Identity list of WebSocket clients on the main rosbridge server.',
    '/cortix/communications/rosbridge_server_cpp/service_call_events': 'Audit stream of every ROS service call made through the main rosbridge (~2 Hz) — a record of operator HMI actions.',
    '/cortix/navigation/mothership/gps': 'Mothership GPS feed on the control-centre side. No traffic in these logs.',
    '/cortix/safety/diagnostics/ntp_sync_diagnostics_chrony/sources': 'Chrony NTP source list with reachability and offsets — clock-sync health of the box.',
    '/cortix/safety/diagnostics/ntp_sync_diagnostics_chrony/sourcestats': 'Per-NTP-source statistics (jitter, drift) from chrony.',
    '/cortix/safety/diagnostics/ntp_sync_diagnostics_chrony/time_synced': 'Boolean: box clock currently NTP-synchronised.',
    '/cortix/safety/diagnostics/ntp_sync_diagnostics_chrony/tracking': 'Chrony tracking summary: reference, stratum, offset, skew.',
    '/cortix/safety/diagnostics/ping_diagnostics/host_array': 'Ping reachability results for monitored network hosts. No traffic in these logs.',
    '/cortix/safety/diagnostics_cleaner/diagnostics_out': 'Diagnostics stream with stale/expired entries removed, republished at ~1 Hz.',
    # --- vehicle comms
    '/drix_8/cortix/communications/d_starlink/status': "Starlink dish status mirrored from the dish's local gRPC API (the same API the Starlink Dashboard reads): downlink/uplink throughput, POP ping latency and drop rate, boresight azimuth/elevation, obstruction and outage stats, GPS stats, alerts, ready states, mobility class, Ethernet speed, software-update state.",
    '/drix_8/cortix/communications/d_wifi_modem/bandwidth_info': 'Measured throughput/capacity over the Wi-Fi (near-field broadband) modem link.',
    '/drix_8/cortix/communications/d_wifi_modem/modem_id': 'Wi-Fi modem identity string (latched).',
    '/drix_8/cortix/communications/d_mbr/modem_id': 'Kongsberg Maritime Broadband Radio modem identity. No traffic — MBR link inactive in these logs.',
    '/drix_8/cortix/communications/d_iridium/modem_imei': 'Vehicle Iridium modem IMEI (latched).',
    '/drix_8/cortix/communications/iridium/iridium_status': 'Iridium link watchdog at ~10 Hz: connection-OK flag plus last/next expected message reception times. Second-highest-volume topic in the recording.',
    '/drix_8/cortix/communications/iridium/iridium_events': 'Vehicle-side Iridium link events (sessions, send attempts, failures).',
    '/drix_8/cortix/communications/iridium/pending_order': 'Command pending acknowledgement over the Iridium fallback C2 path. No traffic — Iridium C2 never engaged.',
    '/drix_8/cortix/communications/iridium/order_timeout_countdown': 'Countdown to expiry of a pending Iridium order. No traffic in these logs.',
    '/drix_8/cortix/communications/iridium/sensor_state': 'Sensor-state events relayed over the Iridium snapshot path.',
    '/drix_8/cortix/communications/iridium/digital_outputs/state': 'PLC digital-output snapshot relayed over Iridium (~121 snapshots across the four days).',
    '/drix_8/cortix/communications/iridium/drix_status': 'Core vehicle-status (DrixOutput) snapshot relayed over Iridium.',
    '/drix_8/cortix/communications/iridium/electronic_breakers/state': 'Electronic-breaker state snapshot relayed over Iridium.',
    '/drix_8/cortix/communications/iridium/gps': 'Position snapshot relayed over Iridium.',
    '/drix_8/cortix/communications/iridium/guidance_status': 'Guidance-state snapshot relayed over Iridium.',
    '/drix_8/cortix/communications/iridium/supervisor/report': 'Safety-supervisor report snapshot relayed over Iridium.',
    '/drix_8/cortix/communications/iridium/telemetry2': 'Legacy PLC telemetry snapshot over Iridium. No traffic in these logs.',
    '/drix_8/cortix/communications/iridium/telemetry3': 'PLC telemetry snapshot over Iridium. No traffic in these logs (broadband links stayed up).',
    '/drix_8/cortix/communications/vehicle_heartbeat': 'Vehicle alive-tick (timestamp) sent to the control centre.',
    '/drix_8/cortix/communications/cortix_software_version': 'Cortix software version string of the vehicle stack (latched).',
    '/drix_8/cortix/communications/firmware_status': 'Status of managed firmware components (update state per unit).',
    '/drix_8/cortix/communications/firmware_versions': 'Version inventory of managed firmware components.',
    '/drix_8/cortix/communications/audio_from_hmi/packets': 'Chunked audio transfer from the operator HMI toward the vehicle (VHF/loudhailer voice path).',
    '/drix_8/cortix/communications/audio_from_hmi/missed_packets': 'Missing-chunk NACK stream for the HMI audio transfer.',
    '/drix_8/cortix/communications/garmin_cortex/status': 'Garmin Cortex VHF unit status.',
    '/drix_8/cortix/communications/garmin_cortex/dsc/status': 'Digital Selective Calling status from the Cortex VHF. No traffic in these logs.',
    '/drix_8/cortix/communications/garmin_cortex/vhf_channels': 'VHF channel table reported by the Cortex unit.',
    '/drix_8/cortix/communications/garmin_cortex/is_new_message_available': 'Flag: new VHF message/recording available. No traffic in these logs.',
    '/drix_8/cortix/communications/garmin_cortex_hmi_sender/is_transfer_audio_hmi_ready': 'Handshake: HMI-bound audio transfer ready.',
    '/drix_8/cortix/communications/garmin_cortex_recordings/packets': 'Chunked transfer of VHF audio recordings from the vehicle to the control centre.',
    '/drix_8/cortix/communications/garmin_cortex_recordings/missed_packets': 'Missing-chunk NACK stream for the VHF-recording transfer.',
    '/drix_8/cortix/communications/garmin_cortex_recordings_receiver/vhf_recordings': 'Reassembled VHF audio recordings on the control-centre side.',
    '/drix_8/cortix/communications/garmin_cortex_recordings_receiver/audio_data_to_ihm_debug': 'Debug tap of decoded VHF audio bytes forwarded to the HMI.',
    '/drix_8/cortix/communications/garmin_cortex_recordings_sender/is_transfer_ready': 'Handshake: VHF-recording transfer ready to start.',
    # --- topic simplifier (light feeds)
    '/drix_8/cortix/communications/topic_simplifier/light_gps': 'Bandwidth-trimmed position feed: latitude, longitude, altitude, COG, SOG (m/s), heading. The recommended position/speed source for analysis — this was the speed input to the fuel-curve refit.',
    '/drix_8/cortix/communications/topic_simplifier/light_ins': 'Trimmed INS feed from the PHINS: roll, pitch, heading, heave plus alignment/quality statuses and per-receiver GPS fix quality.',
    '/drix_8/cortix/communications/topic_simplifier/light_geo_tracks': 'Trimmed tracked-target list (fused AIS/radar/lidar tracks) for the control-centre display.',
    '/drix_8/cortix/communications/topic_simplifier/light_bare_tracks': 'Companion raw/unsmoothed variant of the tracked-target list.',
    '/drix_8/cortix/communications/topic_simplifier/light_coms_info': 'Summary of communication-link states (which links are up, quality) for the HMI.',
    '/drix_8/cortix/communications/topic_simplifier/light_auv_gps': 'Position feed slot for a companion AUV. No traffic in these logs.',
    '/drix_8/cortix/communications/topic_simplifier/light_lars_gps': 'Position feed slot for the launch-and-recovery system. No traffic in these logs.',
    # --- mission
    '/drix_8/cortix/mission/guidance_manager/guidance_state': 'Active guidance mode and state (STARTED / STOPPED / PAUSED / FAILED / SELECTED) with description, obstacle-avoidance-active flag and Iridium-mode flag.',
    '/drix_8/cortix/mission/guidance_manager/guidance_zone': 'Geographic containment zone currently enforced by guidance.',
    '/drix_8/cortix/mission/guidance_manager/initial_geo_path': 'Mission path as originally loaded (latched per mission).',
    '/drix_8/cortix/mission/guidance_manager/light_safe_geo_path': 'Trimmed copy of the deconflicted path the vehicle is actually following.',
    '/drix_8/cortix/mission/guidance_manager/light_preview_geo_path': 'Trimmed preview of the upcoming path legs.',
    '/drix_8/cortix/mission/guidance_manager/light_escape_geo_path': 'Trimmed standing escape route (where the vehicle goes on abort).',
    '/drix_8/cortix/mission/guidance_manager/lars/dist_to_lars_m': 'Distance to the launch-and-recovery system during recovery approach. No traffic — no LARS operations in these logs.',
    '/drix_8/cortix/mission/guidance_manager/lars/lars_relative_speed': 'Closing speed relative to the LARS during recovery. No traffic in these logs.',
    # --- navigation / launcher
    '/drix_8/cortix/navigation/jamming_data': 'GNSS anti-jamming detections (d_tualaj). No traffic in these logs.',
    '/drix_8/cortix/navigation/mothership/gps': 'Mothership position as received aboard the vehicle. No traffic in these logs.',
    '/drix_8/launcher/raw_gps': 'GPS feed from the launcher (davit) unit. No traffic in these logs.',
    '/drix_8/launcher/orientation': 'Orientation of the launcher unit. No traffic in these logs.',
    '/drix_8/launcher/status': 'Launcher controller status. No traffic in these logs.',
    '/drix_8/launcher/launchers_manager/list': 'Vehicle-side launcher-manager process list. No traffic in these logs.',
    '/drix_8/launcher/launchers_manager/simplified_gps_diags': 'Condensed GPS diagnostics string from the vehicle launcher manager. No traffic in these logs.',
    '/drix_8/launcher/launchers_manager/simplified_plc_diags': 'Condensed PLC diagnostics string. No traffic in these logs.',
    '/drix_8/launcher/launchers_manager/simplified_quadrans_diags': 'Condensed Quadrans (FOG compass) diagnostics string. No traffic in these logs.',
    # --- PLC
    '/drix_8/cortix/plc/telemetry/telemetry3': 'Full PLC telemetry at ~1.3 Hz: engine (oil pressure, coolant/exhaust/gearbox temperatures, hours, load, accelerator), fuel rate (L/h) and total fuel used, compartment temperature/humidity/smoke/water alarms, bilge pumps, battery/alternator electrics, engine diagnostics, sensor-fault flags, sea-water valve, nav light, fog horn. Primary fuel-burn source. Note: shaft_sensor_rpm was faulted throughout these logs — use thruster_rpm from vehicle_status instead.',
    '/drix_8/cortix/plc/telemetry/telemetry2': 'Legacy PLC telemetry format. No traffic in these logs.',
    '/drix_8/cortix/plc/telemetry/spare_descriptions': 'Name/scaling map for the spare analog telemetry channels (latched).',
    '/drix_8/cortix/plc/vehicle_status': 'Core vehicle state at ~1.6 Hz: thruster RPM (the working RPM source), rudder angle, fuel gauge percentage, DriX mode (docking/manual/auto), clutch position, keel state, e-stop flags (RC / cable / HMI), low-power and emergency modes, PLC error code, shutdown status.',
    '/drix_8/cortix/plc/batteries/status': 'Battery 1/2 voltage, current and charge/discharge flags plus alternator 1/2 current and fault flags.',
    '/drix_8/cortix/plc/digital_outputs/state': 'States of the PLC digital output channels (pumps, fans, lights...).',
    '/drix_8/cortix/plc/digital_outputs/description': 'Channel-name map for the digital outputs (latched).',
    '/drix_8/cortix/plc/electronic_breakers/state': 'State of each electronic breaker channel (powered equipment switching).',
    '/drix_8/cortix/plc/electronic_breakers/description': 'Channel-name map for the electronic breakers (latched).',
    '/drix_8/cortix/plc/extinguisher/status': 'Fire-extinguisher system status per bottle.',
    '/drix_8/cortix/plc/extinguisher/pin': 'Extinguisher arming-pin state string.',
    '/drix_8/cortix/plc/keel/keep_alive': 'Keep-alive that must be published while commanding keel motion. No traffic in these logs.',
    '/drix_8/cortix/plc/low_power_mode': 'Low-power (electrical economy) mode flag.',
    '/drix_8/cortix/plc/rc/status': 'Remote-controller pendant status: link, buttons, e-stop.',
    '/drix_8/cortix/plc/winch/status': 'Payload winch status array (position, load, limits).',
    '/drix_8/cortix/plc/winch/manual_command': 'Manual winch up/down commands issued by the operator.',
    '/drix_8/cortix/plc/offsets/status': 'Installation/sensor offsets currently applied by the PLC (latched).',
    # --- fuel
    '/drix_8/hardware/average_fuel_rate': 'Smoothed fuel burn (L/h), published about every 15 s.',
    '/drix_8/hardware/average_speed': 'Smoothed speed over ground, the companion to average_fuel_rate.',
    '/drix_8/hardware/fuel_cons_manager/fuel_consumption': 'Onboard endurance projection at ~1 Hz: current burn, remaining distance/time to reserve at current speed and at fixed 4/8/12 kt, burn rate at 4/8/12 kt, and whether the dynamic or static model produced it.',
    '/drix_8/hardware/fuel_consumption_model': 'Coefficients of the onboard SOG-quadratic burn model (a·v² + b·v + c), tank capacity and reserve floor percentage. Note: this static model reads roughly 1.9× the measured burn for the EM2040 configuration — treat its predictions as very conservative.',
    '/hardware/fuel_cons_manager/drix_8/status': 'Fuel-consumption node health: OK / ERROR / FUEL_LIMIT (raised when only the 20% reserve remains).',
    # --- vehicle hardware
    '/drix_8/hardware/trimmer/status': 'Trim-tab (trimmer) actuator status.',
    '/drix_8/hardware/drix_manager/active_licenses': 'Licensed Cortix feature set active on the vehicle (latched).',
    # --- safety
    '/drix_8/cortix/safety/supervisor/report': 'Master safety-supervisor verdict with the list of currently active faults/checks. The first place to look when asking "was anything wrong?".',
    '/drix_8/cortix/safety/supervisor/description': 'Catalogue of everything the safety supervisor monitors (latched).',
    '/drix_8/cortix/safety/supervisor/distress_active': 'Distress state flag. No traffic in these logs (never triggered).',
    '/drix_8/cortix/safety/supervisor/manual_distress': 'Operator-raised distress flag. No traffic in these logs.',
    '/drix_8/cortix/safety/estop_ros_driver/captain_status': 'Emergency-stop chain / captain-authority status from the e-stop driver.',
    '/drix_8/cortix/safety/supervisor_database/cc/history_entries': 'Supervisor event-history records synchronised to the control centre.',
    '/drix_8/cortix/safety/supervisor_database/cc/history_uuid': 'Identity of the current supervisor history stream (sync bookkeeping).',
    '/drix_8/cortix/safety/supervisor_database/cc/next_available_history_id': 'Next history record id (sync bookkeeping).',
    '/drix_8/cortix/safety/supervisor_database/cc/missed_entries': 'History record ids the control centre still lacks (retransmit requests).',
    '/drix_8/cortix/safety/diagnostics/compressed_diagnostics': 'Vehicle diagnostics compressed for the comm link.',
    '/drix_8/cortix/safety/diagnostics/decompressed_diagnostics': 'The same diagnostics re-expanded on the receiving side.',
    # --- sense
    '/drix_8/cortix/sense/ros_interface/common/sensor_state': 'Per-sensor state/health event stream (~3 Hz) covering the perception sensor suite.',
    '/drix_8/cortix/sense/ros_interface/common/disabled_sensors': 'List of sensors deliberately disabled by the operator. No traffic in these logs.',
    '/drix_8/cortix/sense/ros_interface/costmap/costmap_raytracer/status': 'Costmap raytracer alive/OK flag.',
    '/drix_8/cortix/sense/ros_interface/costmap/costmap_raytracer/contours': 'Obstacle contour polygons from the costmap raytracer. No traffic on the recorded side.',
    '/drix_8/cortix/sense/ros_interface/costmap/exclusion_zones': 'Operator-defined keep-out zones feeding the costmap (latched per session).',
    '/drix_8/cortix/sense/ros_interface/costmap/inclusion_zones': 'Operator-defined keep-in (operating area) zones (latched per session).',
    '/drix_8/cortix/sense/ros_interface/costmap/geo_points': 'Operator-added geographic points/marks feeding the costmap (latched).',
    '/drix_8/cortix/sense/ros_interface/planning/local_planner_status': 'Reactive local-planner state at ~1.6 Hz (mode, activity).',
    '/drix_8/cortix/sense/ros_interface/planning/local_planner_collisions': 'Predicted conflicts/collisions the local planner is reasoning about — ~30k messages here means genuine avoidance activity in these logs.',
    '/drix_8/cortix/sense/ros_interface/record/record_info_options': 'Recording metadata options offered for log annotation (latched).',
    '/drix_8/cortix/sense/ros_interface/record/set_weather_infos': 'Operator-entered weather annotation for the log record.',
    '/drix_8/cortix/sense/sensors/ais_operational_mode': 'AIS transponder operating mode. No traffic in these logs.',
    '/drix_8/cortix/sense/sensors/ais_voyage_data': 'AIS voyage configuration (destination, draught...). No traffic in these logs.',
    '/drix_8/cortix/sense/sensors/lidar/cc/bandwidth_status': 'Bandwidth budget/usage of the lidar feed toward the control centre.',
    '/drix_8/cortix/sense/sensors/radar/long_radar/cc/bandwidth_status': 'Bandwidth budget/usage of the long-range radar feed.',
    '/drix_8/cortix/sense/sensors/radar/long_radar/cc/radar_overlay': 'Georeferenced long-range radar overlay images for the chart display (~every 17 s).',
    '/drix_8/cortix/sense/sensors/radar/short_radar/cc/bandwidth_status': 'Bandwidth budget/usage of the short-range radar feed.',
    '/drix_8/cortix/sense/sensors/radar/short_radar/cc/radar_overlay': 'Georeferenced short-range radar overlay images for the chart display.',
    '/drix_8/cortix/sense/visualization/lidar/cc/overlay_image': 'Georeferenced lidar overlay snapshots for the chart display.',
    '/drix_8/cortix/sense/tools/circular_rosbag/infos': 'Status of the onboard circular rosbag recorder.',
    # --- fleet bridge
    '/drix_8/bridge_comm_link/params': 'Vehicle-side communication-bridge link parameters (latched).',
    '/drix_8/bridge_comm_link/params_description': 'Parameter descriptions for the vehicle bridge link.',
    '/drix_8/bridge_comm_link/params_file': 'Source parameter YAML for the vehicle bridge link.',
}

CAMERA_NAMES = {
    'rafal_back_left': 'aft-port hull camera',
    'rafal_back_right': 'aft-starboard hull camera',
    'rafal_front_left': 'forward-port hull camera',
    'rafal_front_right': 'forward-starboard hull camera',
    'rgb_cam': 'main RGB camera',
    'ir_cam': 'fixed infrared camera',
    'mast_ptz_vis': 'mast pan-tilt-zoom camera (visible)',
    'mast_ptz_ir': 'mast pan-tilt-zoom camera (infrared)',
    'underwater': 'underwater camera',
    'winch': 'winch camera',
    'engine': 'engine-compartment camera',
    'elec': 'electronics-compartment camera',
    'survey_pc': 'survey PC screen capture',
}

# What each node whose /params* topics we fold into the matrix actually configures.
PARAM_NODE_HINTS = {
    'd_phins': 'iXblue PHINS INS driver',
    'd_radio_modem': 'UHF radio-modem driver',
    'd_wifi_modem': 'Wi-Fi broadband modem driver',
    'd_wifi_modem_roaming': 'Wi-Fi modem roaming controller',
    'garmin_cortex': 'Garmin Cortex VHF driver',
    'guidance_manager': 'guidance manager',
    'cameras_manager': 'camera manager',
    'd_flipix': 'Flipix retractable camera driver (+ installation pose entries)',
    'd_gaps': 'GAPS USBL positioning driver',
    'd_navipac': 'EIVA NaviPac interface',
    'd_qinsy': 'QPS QINSy survey-software interface',
    'd_sams': 'SAMS interface',
    'echoes_controller': 'ECHOES sub-bottom profiler controller',
    'p_fls_grpc': 'forward-looking-sonar gRPC bridge',
    'payloads_manager': 'payloads manager',
    'd_mbes_norbit': 'Norbit multibeam driver',
    'kongsberg_em_controller': 'Kongsberg EM-series (EM2040) sonar controller',
    'sonar_processor': 'sonar processor (incl. antigrounding look-ahead)',
    'antigrounding_alert_fault': 'antigrounding alert fault monitor',
    'antigrounding_warning_fault': 'antigrounding warning fault monitor',
    'captain_timeout_supervisor': 'captain-timeout supervisor',
    'iridium_comm_supervisor': 'Iridium-comms-loss supervisor',
    'rc_comm_supervisor': 'RC-link-loss supervisor',
    'planning_strategy': 'local-planner strategy',
    'ais_parser': 'AIS sentence parser',
    'ais_receiver': 'AIS receiver driver',
    'ais_sender': 'AIS transmit driver',
    'enc_processor': 'ENC chart-to-costmap processor',
    'long_radar': 'long-range radar driver',
    'short_radar': 'short-range radar driver',
    'detection_filter': 'track detection filter',
    'tracker': 'target tracker',
    'environment_presets_manager': 'environment presets manager',
    'p_nav_params_manager': 'navigation parameter-set manager',
    'sea_condition_presets_manager': 'sea-condition presets manager',
    'd_drix': 'PLC installation offsets',
    'winch': 'winch controller',
    'drix_manager': 'DriX manager',
    'mdt_manager': 'MDT manager',
    'pwd_manager': 'password manager',
    'fuel_cons_manager': 'fuel-consumption manager',
    'robobox_manager': 'robobox manager',
    'gps_manager': 'robobox GPS manager',
    'bridge_comm_masters': 'fleet comm-bridge master',
    'iridium_bridge_clients': 'Iridium bridge clients',
    'garmin_cortex_recordings_receiver': 'VHF-recordings receiver',
    'p_nmea_drixpos_sender': 'NMEA DriX-position sender (feeds ship systems, e.g. ECDIS)',
    'p_relative_lars_pos': 'relative LARS position estimator',
    'static_tf_bow': 'static transform: bow reference',
    'static_tf_cam_ir': 'static transform: IR camera',
    'static_tf_gps_antenna': 'static transform: GPS antenna',
    'static_tf_keel': 'static transform: keel',
    'static_tf_lidar': 'static transform: lidar',
    'static_tf_radar': 'static transform: radar',
    'installation': 'sonar installation geometry',
    'installation_alt': 'Flipix installation altitude',
    'installation_depth': 'Flipix installation depth',
    'installation_pitch': 'Flipix installation pitch',
    'installation_roll': 'Flipix installation roll',
    'offsets': 'installation offsets',
}

# (title, common prefix stripped in the table, intro line)
GROUP_DEFS = [
    ('Position & attitude (light feeds)', '/drix_8/cortix/communications/topic_simplifier/',
     'Bandwidth-trimmed navigation feeds produced by the topic simplifier for the comm links. '
     'These are the cleanest position/attitude sources in the recording.'),
    ('Vehicle status, engine & PLC', '/drix_8/cortix/plc/',
     'Vehicle-machinery truth from the programmable logic controller.'),
    ('Fuel & endurance', None,
     'The onboard fuel accounting chain.'),
    ('Communication links (vehicle side)', '/drix_8/cortix/communications/',
     'Drivers and supervisors for every link the DriX carries: Starlink, Iridium, Wi-Fi, MBR, VHF.'),
    ('Mission & guidance', '/drix_8/cortix/mission/guidance_manager/',
     'What the vehicle was tasked to do and the paths it planned.'),
    ('Safety & supervision', '/drix_8/cortix/safety/',
     'Fault monitors, the master supervisor and its event history.'),
    ('Situational awareness (sense)', '/drix_8/cortix/sense/',
     'Perception, costmap, local planning, AIS, radar and lidar products.'),
    ('Video snapshots', '/drix_8/cortix/videostreams/screenshots/',
     'Still frames logged periodically from each onboard video stream.'),
    ('Navigation & launcher', None,
     'INS/GNSS support topics, mothership feeds and the launcher (davit) unit.'),
    ('Vehicle hardware & management', None,
     'Trim, licensing and vehicle-manager topics.'),
    ('Control-centre nodes', '/cortix/',
     'Nodes running on the control-centre side of the bridge: fleet comm masters, '
     'control-centre Iridium terminus, rosbridge servers for the HMI, and box health (NTP, ping).'),
    ('System, diagnostics & logging', None,
     'ROS-level health, logs and process management for the whole recorded graph.'),
    ('Fleet & mothership placeholders', None,
     'Topic slots for other hulls and the mothership on the shared fleet bridge — all silent here.'),
]

PARAMS_RE = re.compile(r'/params(_description|_file)?$')


def group_of(name: str) -> str:
    if PARAMS_RE.search(name):
        return 'PARAMS'
    if name.startswith(('/drix_21/', '/drix_22/')) or name == '/mothership/size':
        return 'Fleet & mothership placeholders'
    if name.startswith('/drix_8/cortix/communications/topic_simplifier/'):
        return 'Position & attitude (light feeds)'
    if name.startswith('/drix_8/cortix/plc/'):
        return 'Vehicle status, engine & PLC'
    if ('fuel' in name and '/hardware/' in name) or name.startswith('/drix_8/hardware/average_'):
        return 'Fuel & endurance'
    if name.startswith('/drix_8/cortix/communications/'):
        return 'Communication links (vehicle side)'
    if name.startswith('/drix_8/cortix/mission/'):
        return 'Mission & guidance'
    if name.startswith('/drix_8/cortix/safety/'):
        return 'Safety & supervision'
    if name.startswith(('/drix_8/cortix/sense/', '/drix_8/cortix/payloads/')):
        return 'Situational awareness (sense)'
    if name.startswith('/drix_8/cortix/videostreams/'):
        return 'Video snapshots'
    if name.startswith(('/drix_8/cortix/navigation/', '/drix_8/launcher/', '/drix_8/lever_arms/')):
        return 'Navigation & launcher'
    if name.startswith(('/drix_8/hardware/', '/hardware/')):
        return 'Vehicle hardware & management'
    if name.startswith('/cortix/'):
        return 'Control-centre nodes'
    return 'System, diagnostics & logging'


def describe(name: str, rec: dict):
    """Return a description, or None if the topic needs one adding to DESC."""
    if name in DESC:
        return DESC[name]
    m = re.match(r'/drix_8/cortix/videostreams/screenshots/([^/]+)/image_log', name)
    if m:
        cam = CAMERA_NAMES.get(m.group(1), m.group(1) + ' camera')
        s = f'Periodic still frame from the {cam}, logged by the screenshot manager.'
        return s + (' No traffic in these logs.' if rec['total'] == 0 else '')
    if name.startswith(('/drix_21/', '/drix_22/')):
        hull = name.split('/')[1].replace('drix_', 'DriX ')
        return f'Fleet-bridge slot for {hull}. Defined but silent in these logs.'
    return None


def node_hint(node: str) -> str:
    parts = node.strip('/').split('/')
    for p in reversed(parts):
        if p in PARAM_NODE_HINTS:
            return PARAM_NODE_HINTS[p]
    return parts[-1].replace('_', ' ')


def rate_str(rec: dict) -> str:
    if rec['total'] == 0:
        return '—'
    r = rec['rate_hz'] or 0
    if r >= 10:
        return f'{r:.1f}'
    return f'{r:.2f}' if r >= 0.01 else '<0.01'


def classify(day_meta: dict, topics: dict) -> dict:
    groups = {title: [] for title, _, _ in GROUP_DEFS}
    params_nodes, missing = {}, []

    for name, rec in topics.items():
        grp = group_of(name)
        if grp == 'PARAMS':
            node = PARAMS_RE.sub('', name)
            kind = ('file' if name.endswith('_file')
                    else 'desc' if name.endswith('_description') else 'values')
            e = params_nodes.setdefault(node, {'kinds': set(), 'total': 0})
            e['kinds'].add(kind)
            e['total'] += rec['total']
            continue
        desc = describe(name, rec)
        if desc is None:
            missing.append(name)
            desc = ''
        groups[grp].append({
            'topic': name,
            'type': rec['type'].replace('/msg/', '/'),
            'rate': rate_str(rec),
            'rate_hz': rec['rate_hz'] or 0,
            'total': rec['total'],
            'desc': desc,
        })

    if missing:
        raise SystemExit('Topics with no description — add them to DESC:\n  '
                         + '\n  '.join(missing))

    # Loudest first inside a group; silent topics fall to the bottom alphabetically.
    for rows in groups.values():
        rows.sort(key=lambda r: (-r['rate_hz'], r['topic']))

    n_params = sum(1 for n in topics if PARAMS_RE.search(n))
    return {
        'days': day_meta,
        'n_topics': len(topics),
        'n_params_topics': n_params,
        'total_msgs': sum(r['total'] for r in topics.values()),
        'groups': [
            {'title': t, 'prefix': p, 'intro': intro, 'rows': groups[t]}
            for t, p, intro in GROUP_DEFS if groups[t]
        ],
        'params_matrix': [
            {
                'node': node,
                'hint': node_hint(node),
                'values': 'values' in e['kinds'],
                'desc': 'desc' in e['kinds'],
                'file': 'file' in e['kinds'],
                'total': e['total'],
            }
            for node, e in sorted(params_nodes.items())
        ],
    }


if __name__ == '__main__':
    print(f'scanning {ROOT}')
    day_meta, topics, schemas = scan(ROOT)
    out = classify(day_meta, topics)
    OUT.write_text(json.dumps(out, indent=1), encoding='utf-8')

    n_rows = sum(len(g['rows']) for g in out['groups'])
    covered = n_rows + out['n_params_topics']
    assert covered == out['n_topics'], f'{covered} classified of {out["n_topics"]}'
    for day, m in out['days'].items():
        flag = '  [no metadata.yaml — counts from MCAP summaries]' if m.get('no_metadata_yaml') else ''
        print(f"  {day}: {m['message_count']:>10,} msgs  {m['duration_s'] / 3600:5.2f} h  "
              f"{m['files']:2d} files{flag}")
    print(f'{out["n_topics"]} topics ({n_rows} described + {out["n_params_topics"]} params), '
          f'{len(schemas)} schemas, {out["total_msgs"]:,} messages')
    print(f'wrote {OUT.name} — now run: node tools/build_topic_doc.js')
