"""Local HTTP server for the DriX fuel planner.

Standard library only — no pip install, nothing to vendor, works offline.

    python server.py            # serves on http://127.0.0.1:8765
    python server.py --port 9000
    python server.py --no-open  # don't launch a browser

Endpoints
    GET  /                  the UI
    GET  /static/<file>     UI assets
    GET  /api/model         model.json, for populating defaults in the UI
    POST /api/plan          {environment, vessel, legs} -> full plan
    POST /api/max-survey    same body -> longest survey that holds the reserve

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

from engine import (DEFAULT_WAYPOINT_UNIT, Environment, Leg, Model, Vessel,
                    default_waypoints, load_model,
                    max_survey_length, max_survey_lines, plan)

ROOT = Path(__file__).resolve().parent
UI = ROOT / 'ui'
MAX_BODY = 256 * 1024

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


def _parse_waypoints(raw, unit: str) -> tuple[float, ...]:
    """Mission waypoints: a list, a single number, or "13, 26".

    Absent or empty falls back to the defaults rather than meaning "no
    waypoints" — an operator who clears the box wants the standard callouts
    back, not silence. Passing an explicit empty list is the way to ask for
    none.

    The defaults come from `default_waypoints(unit)`, so clearing the box in NM
    restores the same physical radii the km defaults describe, not the same
    numbers read as NM.
    """
    fallback = default_waypoints(unit)
    if raw is None:
        return fallback
    if isinstance(raw, (int, float)):
        return (float(raw),)
    if isinstance(raw, str):
        parts = raw.replace(',', ' ').split()
        return tuple(float(p) for p in parts) or fallback
    return tuple(float(x) for x in raw) or fallback


def _parse_request(body: dict) -> tuple[list[Leg], Environment, Vessel,
                                        dict | None, dt.datetime | None,
                                        tuple[float, ...], str]:
    env_in = body.get('environment') or {}
    env = Environment(
        wmo_sea_state=int(env_in.get('wmo_sea_state', 2)),
        wind_speed_kt=float(env_in.get('wind_speed_kt', 0.0)),
        wind_from_deg=float(env_in.get('wind_from_deg', 0.0)),
    )
    ves_in = body.get('vessel') or {}
    vessel = Vessel(
        capacity_l=float(ves_in.get('capacity_l', 250.0)),
        reserve_fraction=float(ves_in.get('reserve_fraction', 0.25)),
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
        return Leg(name=str(l.get('name', f'Leg {i + 1}')),
                   kind=str(l.get('kind', 'transit')),
                   distance_nm=float(l.get('distance_nm', 0.0)),
                   speed_kt=float(l.get('speed_kt', 6.0)),
                   course_deg=float(l.get('course_deg', 0.0)),
                   lines=int(lines) if lines not in (None, '') else None,
                   line_length_nm=float(length) if length not in (None, '') else None)

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
    if raw is None and 'home_marks_km' in body:
        raw, unit = body['home_marks_km'], 'km'
    waypoints = _parse_waypoints(raw, unit)
    return legs, env, vessel, override, start_time, waypoints, unit


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
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._error(400, f'malformed JSON: {exc}')
            return None

    # -- routes ------------------------------------------------------------ #

    def do_GET(self):                                    # noqa: N802
        route = self.path.split('?', 1)[0]
        if route in ('/', '/index.html'):
            return self._serve_file(UI / 'index.html')
        if route.startswith('/static/'):
            return self._serve_file(UI / route[len('/static/'):])
        if route == '/api/model':
            return self._json(200, load_model())
        if route == '/api/health':
            return self._json(200, {'ok': True})
        self._error(404, 'not found')

    do_HEAD = do_GET                                     # noqa: N815

    def do_POST(self):                                   # noqa: N802
        route = self.path.split('?', 1)[0]
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
                result = plan(legs, env, vessel, model, override,
                              start_time=start_time, waypoints=waypoints,
                              waypoint_unit=unit)
                return self._json(200, result.to_dict())

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
    ap = argparse.ArgumentParser(description='DriX fuel planner (local UI)')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--no-open', action='store_true',
                    help='do not open a browser window')
    args = ap.parse_args()

    if not (UI / 'index.html').is_file():
        raise SystemExit(f'UI files missing — expected {UI / "index.html"}')
    get_model()          # fail fast on a broken model.json

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f'http://{args.host}:{args.port}/'
    print(f'DriX fuel planner  ->  {url}')
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
