"""Local HTTP server for the DriX fuel planner.

Standard library only — no pip install, nothing to vendor, works offline.

    python server.py                     # serves on http://127.0.0.1:8765
    python server.py --port 9000
    python server.py --no-open           # don't launch a browser
    python server.py --no-reports        # plan without writing mission reports
    python server.py --report-dir PATH   # write them somewhere else

Endpoints
    GET  /                  the UI
    GET  /static/<file>     UI assets
    GET  /quickstart.md     docs/QUICKSTART.md, rendered by the help panel
    GET  /api/model         model.json, for populating defaults in the UI
    POST /api/plan          {environment, vessel, legs} -> full plan
    POST /api/max-survey    same body -> longest survey that holds the reserve
    POST /api/currents      {lat, lon, departure_utc, legs} -> per-leg set/drift
                            read off the NOAA OFS surface forecast

/api/currents IS THE ONE ENDPOINT THAT REACHES THE NETWORK. Everything else
here works on a boat with no signal, and that stays true: the currents call is
made only when an operator presses the button, a failure is reported as a
failure, and the per-leg fields it fills can always be typed by hand instead.
Times in that request are UTC INSTANTS, not the mission clock's local wall time
— the browser converts, because only it knows the operator's offset.

EVERY SUCCESSFUL PLAN WRITES A MARKDOWN REPORT to `docs/missions/`, and the
response carries its path. That is a side effect of a GET-shaped operation, so
it is worth being plain about: one file per press of Plan mission, timestamped
to the second, never overwritten. `--no-reports` turns it off; `--report-dir`
moves it. A solve (`/api/max-survey`) writes nothing — it is not a mission.

Bound to loopback deliberately: this is a planning aid on a laptop, not a
service. Nothing here is authenticated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import currents as ofs
import lineplan
import mission_report
from engine import (DEFAULT_WAYPOINT_UNIT, Environment, Leg, Model, Vessel,
                    default_waypoints, load_model,
                    max_survey_length, max_survey_lines, plan)

ROOT = Path(__file__).resolve().parent
UI = ROOT / 'ui'
DOCS = ROOT / 'docs'
MAX_BODY = 256 * 1024

# Where mission reports land, and whether they are written at all. Module-level
# rather than baked in so `main()` can point them elsewhere and, more
# importantly, so a TEST can point them at a temp directory — a suite that
# exercised the real handler would otherwise litter the operator's own docs/.
REPORT_DIR: Path | None = DOCS / 'missions'

CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
}

_MODEL: Model | None = None
_MODEL_LOCK = threading.Lock()


def get_model() -> Model:
    """Loaded once and reused; the file is small but this is hit per request."""
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = Model()
        return _MODEL


def _parse_start_time(raw) -> dt.datetime | None:
    """Mission start, as ISO 8601 or a bare clock time.

    The UI sends what `datetime-local` produces ("2026-08-11T06:30"). A bare
    "HH:MM" is accepted for convenience and dated today — but the engine always
    works in real datetimes, never wall-clock alone, because these missions run
    past midnight and an undated 02:00 is ambiguous by a day or two.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ('%H:%M', '%H:%M:%S', '%H%M'):
        try:
            return dt.datetime.combine(dt.date.today(),
                                       dt.datetime.strptime(s, fmt).time())
        except ValueError:
            continue
    raise ValueError(f'could not read start_time {raw!r} — use ISO 8601 '
                     f'(2026-08-11T06:30) or HH:MM')


def _reject_nonfinite(token: str):
    """json.loads parse_constant hook: refuse NaN/Infinity/-Infinity."""
    raise ValueError(f'{token} is not a valid number')


def _parse_waypoints(raw, unit: str) -> tuple[float, ...]:
    """Mission waypoints: a list, a single number, or "13, 26".

    Absent (null) or a BLANK STRING falls back to the defaults — an operator
    who clears the box wants the standard callouts back, not silence, and the
    UI sends null for a blank box. An explicit empty LIST asks for no
    waypoints at all, and is honoured: review found the old `... or fallback`
    coercion quietly turned [] back into the defaults, so the escape hatch
    this docstring promised did not exist.

    The defaults come from `default_waypoints(unit)`, so clearing the box in NM
    restores the same physical radii the km defaults describe, not the same
    numbers read as NM.
    """
    if raw is None:
        return default_waypoints(unit)
    if isinstance(raw, (int, float)):
        return (float(raw),)
    if isinstance(raw, str):
        parts = raw.replace(',', ' ').split()
        return tuple(float(p) for p in parts) or default_waypoints(unit)
    return tuple(float(x) for x in raw)


MAX_TRACK_POINTS = 2000
MAX_SURVEY_LINES = 2000


def _parse_track(raw):
    """[[lat, lon], ...] for a transit, or None.

    Bounded, because each point becomes runs and each run becomes a field
    lookup: an unbounded track is an unbounded amount of work for one request
    on a loopback server with no auth in front of it.
    """
    if raw in (None, ''):
        return None
    if not isinstance(raw, list):
        raise ValueError('track must be a list of [lat, lon] pairs')
    if len(raw) > MAX_TRACK_POINTS:
        raise ValueError(f'track has more than {MAX_TRACK_POINTS} points')
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise ValueError('each track point must be [lat, lon]')
        lat, lon = float(p[0]), float(p[1])
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError(f'track point {lat}, {lon} is not on the earth')
        out.append([lat, lon])
    if len(out) < 2:
        raise ValueError('a track needs at least two points')
    return out


def _parse_survey_lines(raw):
    """Explicit survey lines as imported from a line plan: [[[lat, lon], ...], ...]

    Bounded on both axes — a plan with thousands of lines, or one line with
    thousands of vertices, is the same unbounded work as an unbounded track.
    """
    if raw in (None, ''):
        return None
    if not isinstance(raw, list):
        raise ValueError('survey_lines must be a list of lines')
    if len(raw) > MAX_SURVEY_LINES:
        raise ValueError(f'more than {MAX_SURVEY_LINES} survey lines')
    out = []
    for line in raw:
        pts = _parse_track(line)          # same point rules, same bounds
        out.append(pts)
    if not out:
        return None
    return out


def _parse_pattern(raw):
    """A survey lawnmower, or None.

    The TURN RADIUS defaults from `model.json`, not from a literal here: it is
    an assumption with a documented standing, and a second copy in this file
    would be free to drift from the one the documents describe.
    """
    if raw in (None, ''):
        return None
    if not isinstance(raw, dict):
        raise ValueError('pattern must be an object')
    anchor = raw.get('anchor')
    if not isinstance(anchor, (list, tuple)) or len(anchor) != 2:
        raise ValueError('pattern needs an anchor of [lat, lon]')
    lat, lon = float(anchor[0]), float(anchor[1])
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f'pattern anchor {lat}, {lon} is not on the earth')
    lines = int(float(raw.get('lines', 0)))
    if lines < 1 or lines > MAX_SURVEY_LINES:
        raise ValueError(f'pattern needs 1 to {MAX_SURVEY_LINES} lines')
    length = float(raw.get('length_nm', 0.0))
    spacing = float(raw.get('spacing_nm', 0.0))
    if length <= 0:
        raise ValueError('pattern line length must be greater than zero')
    if spacing <= 0:
        raise ValueError('pattern line spacing must be greater than zero')
    turn = get_model().data.get('turn_model', {})
    radius = raw.get('turn_radius_nm')
    return {
        'anchor': [lat, lon],
        'bearing_deg': float(raw.get('bearing_deg', 0.0)),
        'length_nm': length,
        'spacing_nm': spacing,
        'lines': lines,
        'step_bearing_deg': (None if raw.get('step_bearing_deg') in (None, '')
                             else float(raw['step_bearing_deg'])),
        'turn_radius_nm': (float(turn.get('radius_nm', 0.0))
                           if radius in (None, '') else float(radius)),
        'turn_speed_kt': float(raw.get('turn_speed_kt',
                                       turn.get('speed_kt', 0.0)) or 0.0),
    }


def _parse_request(body: dict) -> tuple[list[Leg], Environment, Vessel,
                                        dict | None, dt.datetime | None,
                                        tuple[float, ...], str]:
    env_in = body.get('environment') or {}
    env = Environment(
        wmo_sea_state=int(env_in.get('wmo_sea_state', 2)),
        wind_speed_kt=float(env_in.get('wind_speed_kt', 0.0)),
        wind_from_deg=float(env_in.get('wind_from_deg', 0.0)),
        # Wind is named for where it comes FROM, current for where it SETS
        # TOWARD. Opposite conventions, deliberately, because that is what the
        # bridge says.
        current_speed_kt=float(env_in.get('current_speed_kt', 0.0)),
        current_set_deg=float(env_in.get('current_set_deg', 0.0)),
    )
    ves_in = body.get('vessel') or {}
    # The reserve default comes from the MODEL, not a literal: review found
    # this line carrying the repo's third hard-coded copy of the floor, which
    # the reserve-agreement test never read. Two copies are pinned together by
    # a test; a third that drifts plans API callers to a different floor.
    reserve_default = get_model().data['reserve']['default_fraction']
    vessel = Vessel(
        capacity_l=float(ves_in.get('capacity_l', 250.0)),
        reserve_fraction=float(ves_in.get('reserve_fraction', reserve_default)),
        start_level_fraction=float(ves_in.get('start_level_fraction', 1.0)),
        gondola=str(ves_in.get('gondola', 'em712')),
    )
    legs_in = body.get('legs') or []

    def _leg(i, l):
        # lines/line_length are optional and travel together; absent means the
        # historical single-distance survey, which the engine reads as one
        # reciprocal pair.
        lines = l.get('lines')
        length = l.get('line_length_nm')
        # Loiter arrives in HOURS. Minutes are a display unit and the UI
        # converts; keeping one unit on the wire means the engine never has to
        # guess which it was handed.
        loiter = l.get('loiter_hours')
        # Per-leg weather is OPTIONAL and each field is independent: absent
        # means "use the mission Environment", which is not the same as zero.
        # Coercing a missing value to 0.0 here would silently becalm a leg.
        opt = lambda k: (float(l[k]) if l.get(k) not in (None, '') else None)  # noqa: E731
        return Leg(name=str(l.get('name', f'Leg {i + 1}')),
                   track=_parse_track(l.get('track')),
                   pattern=_parse_pattern(l.get('pattern')),
                   survey_lines=_parse_survey_lines(l.get('survey_lines')),
                   turn_radius_nm=float(
                       l['turn_radius_nm'] if l.get('turn_radius_nm') not in (None, '')
                       else get_model().data.get('turn_model', {}).get('radius_nm', 0.0)),
                   kind=str(l.get('kind', 'transit')),
                   distance_nm=float(l.get('distance_nm', 0.0)),
                   speed_kt=float(l.get('speed_kt', 6.0)),
                   course_deg=float(l.get('course_deg', 0.0)),
                   # Not int(): that silently truncated 12.5 lines to 12 and
                   # made the engine's own whole-number guard unreachable over
                   # HTTP (found in review). An integral float becomes the int
                   # the engine wants; anything fractional is passed through so
                   # Leg.validate rejects it with its own message.
                   lines=(int(float(lines)) if float(lines).is_integer()
                          else float(lines)) if lines not in (None, '') else None,
                   line_length_nm=float(length) if length not in (None, '') else None,
                   loiter_hours=float(loiter) if loiter not in (None, '') else 0.0,
                   wind_speed_kt=opt('wind_speed_kt'),
                   wind_from_deg=opt('wind_from_deg'),
                   current_speed_kt=opt('current_speed_kt'),
                   current_set_deg=opt('current_set_deg'),
                   wmo_sea_state=(int(l['wmo_sea_state'])
                                  if l.get('wmo_sea_state') not in (None, '') else None))

    legs = [_leg(i, l) for i, l in enumerate(legs_in)]

    override = None
    if body.get('sea_state_override'):
        override = {int(k): float(v) for k, v in body['sea_state_override'].items()}

    start_time = _parse_start_time(body.get('start_time'))
    # `home_marks_km` is the deprecated body key and is always km, so it pins
    # the unit when it is the one supplied. The engine rejects an unknown unit;
    # it is not this shim's job to second-guess it.
    unit = str(body.get('waypoint_unit') or DEFAULT_WAYPOINT_UNIT)
    raw = body.get('waypoints')
    if 'home_marks_km' in body:
        # Both spellings at once is a contradiction, and the ENGINE'S rule is
        # that it raises rather than resolving by precedence — this shim was
        # quietly preferring `waypoints` and never letting the engine see the
        # clash (found in review). Same words as the engine's error, so the
        # client reads one message whichever layer catches it.
        if raw is not None:
            raise ValueError('pass waypoints or home_marks_km, not both')
        raw, unit = body['home_marks_km'], 'km'
    waypoints = _parse_waypoints(raw, unit)
    return legs, env, vessel, override, start_time, waypoints, unit


def _write_report(result, unit: str, title: str, currents_source: str = '') -> dict:
    """Write the mission report and describe what happened.

    Returns `{'written': bool, 'path': str|None, 'error': str|None}` — the UI
    shows the path so an operator knows a file appeared, and the error so a
    silent failure cannot masquerade as a saved report.
    """
    if REPORT_DIR is None:
        return {'written': False, 'path': None, 'error': None}
    try:
        when = dt.datetime.now()
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORT_DIR / mission_report.filename(result, when, title)
        # Never clobber: two plans inside one second are two missions, and the
        # earlier one is not scratch.
        n = 2
        while path.exists():
            path = path.with_name(f'{path.stem}-{n}{path.suffix}')
            n += 1
        text = mission_report.render(result, generated=when,
                                     waypoint_unit=unit, title=title,
                                     currents_source=currents_source)
        path.write_text(text, encoding='utf-8', newline='\n')
        try:
            shown = str(path.relative_to(ROOT))
        except ValueError:
            shown = str(path)
        return {'written': True, 'path': shown, 'error': None}
    except OSError as exc:
        return {'written': False, 'path': None, 'error': str(exc)}


class Handler(BaseHTTPRequestHandler):
    server_version = 'DriXPlanner/1.0'

    # -- plumbing ---------------------------------------------------------- #

    def log_message(self, fmt, *args):          # quieter than the default
        print(f'  {self.address_string()}  {fmt % args}')

    def _send(self, code: int, payload: bytes, ctype: str):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(payload)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, allow_nan=False).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _error(self, code: int, message: str) -> None:
        self._json(code, {'error': message})

    def _serve_file(self, path: Path) -> None:
        # Containment check: never serve outside the ui directory.
        try:
            resolved = path.resolve()
            resolved.relative_to(UI.resolve())
        except (ValueError, OSError):
            return self._error(403, 'forbidden')
        if not resolved.is_file():
            return self._error(404, 'not found')
        ctype = CONTENT_TYPES.get(resolved.suffix, 'application/octet-stream')
        self._send(200, resolved.read_bytes(), ctype)

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get('Content-Length', 0))
        except ValueError:
            self._error(400, 'bad Content-Length')
            return None
        if length <= 0:
            self._error(400, 'empty request body')
            return None
        if length > MAX_BODY:
            self._error(413, 'request body too large')
            return None
        try:
            # parse_constant fires only for the non-standard NaN/Infinity
            # literals, which json.loads otherwise ADMITS. A NaN speed sails
            # past every validator (NaN <= 0 is False) and dies at response
            # serialization with an error naming no input — found in review.
            body = json.loads(self.rfile.read(length).decode('utf-8'),
                              parse_constant=_reject_nonfinite)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._error(400, f'malformed JSON: {exc}')
            return None
        # A valid-JSON body whose top level is a list or string raised
        # AttributeError past do_POST's (TypeError, ValueError) net and closed
        # the connection with no response at all — found in review.
        if not isinstance(body, dict):
            self._error(400, 'request body must be a JSON object')
            return None
        return body

    # -- routes ------------------------------------------------------------ #

    def do_GET(self):                                    # noqa: N802
        route = self.path.split('?', 1)[0]
        if route in ('/', '/index.html'):
            return self._serve_file(UI / 'index.html')
        if route.startswith('/static/'):
            return self._serve_file(UI / route[len('/static/'):])
        # The help panel renders THIS file, so the document an operator reads in
        # the app and the one in the repo cannot drift apart. Served from the
        # repo root rather than copied into ui/, for the same reason.
        if route == '/quickstart.md':
            path = ROOT / 'docs' / 'QUICKSTART.md'
            if not path.is_file():
                return self._error(404, 'quick start not found')
            return self._send(200, path.read_bytes(), 'text/markdown; charset=utf-8')
        if route == '/api/model':
            return self._json(200, load_model())
        if route == '/api/health':
            return self._json(200, {'ok': True})
        self._error(404, 'not found')

    do_HEAD = do_GET                                     # noqa: N815

    def _currents(self, body: dict) -> None:
        """Per-leg set and drift from the OFS surface forecast.

        The mission is dead-reckoned from (lat, lon) at departure_utc and each
        leg sampled along its own track and time window. What comes back is
        exactly what an operator would otherwise type into the per-leg current
        boxes — the model is not involved and no coefficient moves.
        """
        try:
            lat = float(body['lat'])
            lon = float(body['lon'])
        except (KeyError, TypeError, ValueError):
            return self._error(400, 'give a departure position as lat and lon')
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return self._error(400, f'position {lat}, {lon} is not on the earth')

        raw = str(body.get('departure_utc') or '').strip()
        if not raw:
            return self._error(400, 'give departure_utc — the mission start as a UTC '
                                    'instant, e.g. 2026-08-13T14:00:00Z')
        try:
            departure = ofs.parse_time(raw)
        except ValueError:
            return self._error(400, f'could not read departure_utc {raw!r}')

        legs = body.get('legs')
        if not isinstance(legs, list) or not legs:
            return self._error(400, 'give the legs to resolve')
        if len(legs) > 32:
            return self._error(400, 'too many legs')
        for leg in legs:
            if not isinstance(leg, dict):
                return self._error(400, 'each leg must be a JSON object')

        # How long the mission runs, so a cycle can be judged before it is used.
        hours = 0.0
        for leg in legs:
            try:
                speed = float(leg.get('speed_kt') or 0.0)
                dist = float(leg.get('distance_nm') or 0.0)
                if leg.get('kind') == 'survey' and leg.get('lines'):
                    dist = float(leg['lines']) * float(leg.get('line_length_nm') or 0.0)
                hours += float(leg.get('loiter_hours') or 0.0)
                hours += (dist / speed) if speed > 0 else 0.0
            except (TypeError, ValueError):
                return self._error(400, 'legs need numeric distance, speed and loiter')
        if hours > 240:
            return self._error(400, 'that mission is longer than any forecast')
        end = departure + dt.timedelta(hours=hours)

        bbox = ofs.mission_bbox(lat, lon, legs)
        tag = ofs.covering_cycle(departure, end, bbox)
        fetched = False
        if tag is None:
            if body.get('offline'):
                return self._error(503, 'no cached forecast covers this mission and '
                                        'fetching is off — enter currents by hand')
            try:
                tag = ofs.fetch_cycle(bbox=bbox, quiet=True)
                fetched = True
            except RuntimeError as exc:
                # Offline, or NOAA down, or the mission is outside the model.
                # Each is the operator's to act on, so say which.
                return self._error(503, f'could not get the forecast: {exc}')

        try:
            rows, prov = ofs.resolve_legs(lat, lon, departure, legs, tag=tag)
        except (RuntimeError, ValueError) as exc:
            return self._error(503, f'could not read the forecast: {exc}')

        out = {'legs': rows, 'source': prov, 'fetched': fetched,
               'mission_hours': round(hours, 2)}
        span_end = ofs.parse_time(prov['span'][1])
        if end > span_end:
            over = (end - span_end).total_seconds() / 3600.0
            out['warning'] = (f'the mission runs {over:.1f} h past the end of this '
                              f'forecast — the last legs have no data')
        if all('current_speed_kt' not in r for r in rows):
            out['warning'] = ('no leg fell on model water — check the departure '
                              'position is inside the forecast domain')
        return self._json(200, out)

    def _lineplan(self, body: dict) -> None:
        """Parse an uploaded line plan and describe what came out.

        The file arrives base64 so one path carries both text formats and the
        zipped ones. Nothing is planned here and nothing is stored: this
        returns the geometry and a summary for the operator to CHECK before it
        is used, because a line plan that read cleanly into the wrong place
        looks exactly like one that read correctly.
        """
        import base64
        raw = body.get('data')
        if not isinstance(raw, str) or not raw:
            return self._error(400, 'send the file contents as base64 in "data"')
        try:
            blob = base64.b64decode(raw, validate=True)
        except (ValueError, TypeError):
            return self._error(400, 'that was not valid base64')
        if len(blob) > MAX_BODY:
            return self._error(413, 'that line plan is too large')

        zone = body.get('utm_zone')
        try:
            plan = lineplan.sniff_and_read(
                blob, str(body.get('filename') or ''),
                zone=(None if zone in (None, '') else int(zone)),
                northern=bool(body.get('northern', True)))
        except lineplan.LinePlanError as exc:
            # The operator's problem to fix, and the message says how.
            return self._error(422, str(exc))
        except Exception as exc:                          # noqa: BLE001
            self.log_message('lineplan: %r', exc)
            return self._error(422, 'could not read that line plan')

        if len(plan.lines) > MAX_SURVEY_LINES:
            return self._error(422, f'that plan has {len(plan.lines)} lines, '
                                    f'more than the {MAX_SURVEY_LINES} allowed')
        return self._json(200, {'summary': lineplan.describe(plan),
                                'lines': plan.as_tracks()})

    def do_POST(self):                                   # noqa: N802
        route = self.path.split('?', 1)[0]
        if route == '/api/lineplan':
            body = self._read_body()
            if body is None:
                return
            try:
                return self._lineplan(body)
            except Exception as exc:                      # noqa: BLE001
                self.log_message('lineplan: %r', exc)
                return self._error(500, 'internal error reading the line plan')
        if route == '/api/currents':
            body = self._read_body()
            if body is None:
                return
            try:
                return self._currents(body)
            except Exception as exc:                      # noqa: BLE001
                self.log_message('currents: %r', exc)
                return self._error(500, 'internal error while reading the forecast')
        if route not in ('/api/plan', '/api/max-survey'):
            return self._error(404, 'not found')

        body = self._read_body()
        if body is None:
            return
        try:
            (legs, env, vessel, override, start_time,
             waypoints, unit) = _parse_request(body)
        except (TypeError, ValueError) as exc:
            return self._error(400, f'could not read the request: {exc}')

        model = get_model()
        try:
            if route == '/api/plan':
                # A forecast FIELD, not per-leg numbers, when the caller asks
                # for one and the legs carry geometry to locate it with. This
                # is the only place in planning that can touch the network,
                # and a failure degrades to the typed currents rather than
                # costing the operator their plan.
                env_at, field_note = None, None
                if body.get('use_forecast_currents') and any(
                        l.track or l.pattern or l.survey_lines for l in legs):
                    try:
                        env_at = ofs.env_factory(
                            env, ofs.parse_time(str(body.get('departure_utc') or '')))
                    except (ValueError, RuntimeError) as exc:
                        field_note = f'planned without the forecast: {exc}'
                result = plan(legs, env, vessel, model, override,
                              start_time=start_time, waypoints=waypoints,
                              waypoint_unit=unit, env_at=env_at)
                payload = result.to_dict()
                if env_at is not None:
                    payload['currents_field'] = {
                        'label': env_at.label, 'tag': env_at.tag,
                        'asked': env_at.asked, 'covered': env_at.covered}
                if field_note:
                    payload['currents_field'] = {'error': field_note}
                # The report is a CONVENIENCE, not part of the answer: a full
                # disk or a read-only docs/ must not cost the operator their
                # plan. Failures are reported in the payload and logged, never
                # raised.
                payload['report'] = _write_report(
                    result, unit, str(body.get('title') or ''),
                    str(body.get('currents_source') or ''))
                return self._json(200, payload)

            out = {'max_survey_nm': max_survey_length(legs, env, vessel, model,
                                                      override)}
            # A line-based survey also gets the count, which is the answer an
            # operator can act on when the area will not fit in one run.
            if any(l.kind == 'survey' and l.lines for l in legs):
                out['lines'] = max_survey_lines(legs, env, vessel, model, override)
            return self._json(200, out)
        except ValueError as exc:
            return self._error(422, str(exc))
        except Exception as exc:                          # noqa: BLE001
            self.log_message('unhandled: %r', exc)
            return self._error(500, 'internal error while planning')


def main() -> None:
    global REPORT_DIR
    ap = argparse.ArgumentParser(description='DriX fuel planner (local UI)')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-open', action='store_true',
                    help='do not open a browser window')
    ap.add_argument('--report-dir', default=None,
                    help='where mission reports are written '
                         f'(default {REPORT_DIR.relative_to(ROOT)})')
    ap.add_argument('--no-reports', action='store_true',
                    help='plan without writing a mission report')
    args = ap.parse_args()

    if args.no_reports:
        REPORT_DIR = None
    elif args.report_dir:
        REPORT_DIR = Path(args.report_dir).resolve()

    if not (UI / 'index.html').is_file():
        raise SystemExit(f'UI files missing — expected {UI / "index.html"}')
    get_model()          # fail fast on a broken model.json

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f'http://{args.host}:{args.port}/'
    print(f'DriX fuel planner  ->  {url}')
    print('mission reports  ->  '
          + ('off' if REPORT_DIR is None else str(REPORT_DIR)))
    print('Ctrl-C to stop.')
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print('\nstopped.')
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
