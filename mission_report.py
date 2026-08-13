"""Render a planned mission as a Markdown report.

PURE FORMATTING, no I/O — same rail as `engine.py`. `render()` takes a
`PlanResult` and returns a string; `server.py` decides where it lands. That
split is what lets the tests exercise the whole report without writing a byte
into the operator's `docs/` folder.

Markdown rather than a Word document on purpose. These are generated on every
plan, so they have to be diffable, greppable, readable in a terminal on a boat,
and cost nothing to produce; the four `.docx` deliverables in `docs/` are a
different kind of artefact with builders of their own.

Everything here comes off the `PlanResult`. Nothing is recomputed — a report
that did its own arithmetic could disagree with the plan it claims to describe.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from engine import PlanResult

VERDICT_TEXT = {
    'ok': 'WITHIN RESERVE',
    'gauge_breach': 'BREACHES ON THE GAUGE',
    'breach': 'BREACHES RESERVE',
    'dry': 'RUNS DRY',
}


def _num(x: Any, places: int = 1, dash: str = '—') -> str:
    if x is None:
        return dash
    return f'{float(x):.{places}f}'


def _hm(hours: float | None) -> str:
    if hours is None:
        return '—'
    total = int(round(float(hours) * 60))
    h, m = divmod(total, 60)
    if h and m:
        return f'{h} h {m} min'
    return f'{h} h' if h else f'{m} min'


def slug(name: str) -> str:
    """A filename-safe stem. Falls back to 'mission' if nothing survives."""
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '-', str(name)).strip('-.')
    return cleaned[:48] or 'mission'


def filename(plan: PlanResult, when: dt.datetime, name: str = '') -> str:
    """`mission_<stamp>[_<name>].md` — sortable, unique to the second.

    The stamp is the moment the report was GENERATED, not the mission start:
    two plans of the same mission an hour apart are two reports, and the one
    you wrote last is the one at the bottom of the listing.
    """
    stem = f'mission_{when:%Y%m%d-%H%M%S}'
    if name:
        stem += f'_{slug(name)}'
    return stem + '.md'


def render(plan: PlanResult, *, generated: dt.datetime,
           waypoint_unit: str = 'km', title: str = '',
           currents_source: str = '') -> str:
    """The report, as Markdown.

    `currents_source` names the forecast the per-leg currents were read from,
    when they came from one. A forecast is a perishable input — the same
    mission planned off a different cycle is a different plan — so the report
    records WHICH cycle rather than leaving the numbers looking like constants.
    Typed-in currents leave it empty and the line does not appear.
    """
    p = plan
    out: list[str] = []
    w = out.append

    w(f'# Mission report — {VERDICT_TEXT.get(p.verdict, p.verdict.upper())}')
    w('')
    if title:
        w(f'**{title}**')
        w('')
    w(f'Generated {generated:%Y-%m-%d %H:%M} · '
      f'{p.gondola.upper()} gondola ({p.gondola_status}) · '
      f'planned by the DriX mission fuel planner.')
    w('')
    if currents_source:
        w(f'Currents: {currents_source}')
        w('')

    # -- headline ----------------------------------------------------------- #
    w('## Verdict')
    w('')
    w(f'**{VERDICT_TEXT.get(p.verdict, p.verdict.upper())}**')
    w('')
    rows = [
        ('Fuel used', f'{_num(p.total_litres)} L'),
        ('Mission time', f'{_num(p.total_hours, 2)} h'),
        ('Distance', f'{_num(p.total_distance_nm, 0)} NM'),
        ('Overall efficiency',
         f'{_num(p.total_distance_nm / p.total_litres, 2) if p.total_litres else "—"} NM/L'),
        ('On return (capacity)',
         f'{_num(p.remaining_fraction * 100)}% · {_num(p.remaining_litres)} L'),
        ('Needle on return (gauge)',
         '—' if p.indicated_return_pct is None else f'{_num(p.indicated_return_pct)}%'),
        ('Margin over reserve', f'{_num(p.margin_litres)} L'),
        ('Spare range / time',
         f'{_num(p.binding_margin_nm, 0)} NM · {_num(p.binding_margin_hours, 2)} h'
         if p.verdict == 'ok' else '— (breaching)'),
        ('Loiter total',
         f'{_hm(p.total_loiter_hours)} · {_num(p.total_loiter_litres)} L'),
    ]
    w('| | |')
    w('|---|---|')
    for k, v in rows:
        w(f'| {k} | {v} |')
    w('')

    # -- warnings ----------------------------------------------------------- #
    # Placed high on purpose: a report whose caveats are on page three is a
    # report whose caveats do not get read.
    if p.warnings:
        w('## Warnings')
        w('')
        for warn in p.warnings:
            w(f'- {warn}')
        w('')

    # -- vessel and reserve ------------------------------------------------- #
    w('## Vessel')
    w('')
    w(f'- Gondola: **{p.gondola}** ({p.gondola_status})')
    w(f'- Tank: {_num(p.capacity_l, 0)} L nominal, starting at '
      f'{_num(p.indicated_start_pct, 0)}%')
    w(f'- Return reserve: {_num(p.reserve_litres)} L '
      f'({_num(p.reserve_litres / p.capacity_l * 100, 0) if p.capacity_l else "—"}%)')
    if p.gauge_usable_litres is not None:
        w(f'- Mission fuel to the floor, by the gauge: '
          f'**{_num(p.gauge_usable_litres)} L** — the reserve floor is a needle '
          f'position, so no tank-capacity assumption enters it')
    w('')

    # -- legs --------------------------------------------------------------- #
    w('## By leg')
    w('')
    w('| Leg | NM | kt | Sea | Wind | Current | RPM | Premium | Hours | Litres | NM/L |')
    w('|---|---|---|---|---|---|---|---|---|---|---|')
    for l in p.legs:
        rpm = (f'{_num(l.rpm_min, 0)}–{_num(l.rpm_max, 0)}'
               if l.rpm_max - l.rpm_min > 1 else _num(l.rpm_required, 0))
        wind = (f'{_num(l.wind_speed_kt, 0)} kt from {_num(l.wind_from_deg, 0)}°'
                if l.wind_speed_kt else 'calm')
        cur = (f'{_num(l.current_speed_kt)} kt sets {_num(l.current_set_deg, 0)}°'
               if l.current_speed_kt else 'slack')
        hours = _num(l.hours, 2) + (f' +{_num(l.loiter_hours, 2)} hold'
                                    if l.loiter_hours else '')
        litres = _num(l.litres) + (f' +{_num(l.loiter_litres)} hold'
                                   if l.loiter_litres else '')
        flag = ' ⚠' if l.extrapolated else ''
        w(f'| {l.name}{flag} | {_num(l.distance_nm)} | {_num(l.speed_kt)} | '
          f'WMO {l.wmo_sea_state} | {wind} | {cur} | {rpm} | '
          f'{l.total_premium * 100:+.1f}% | {hours} | {litres} | {_num(l.nm_per_l, 2)} |')
    w('')
    # Only when something is actually flagged: a legend for a symbol that does
    # not appear trains the reader to skip it, which is the last thing an
    # extrapolation warning needs.
    if any(l.extrapolated for l in p.legs):
        w('⚠ marks a leg outside the RPM window its gondola fuel law was fitted '
          'over — an extrapolation, not a measurement.')
        w('')

    leg_notes = [(l.name, n) for l in p.legs for n in l.notes]
    if leg_notes:
        w('### Leg notes')
        w('')
        for name, note in leg_notes:
            w(f'- **{name}:** {note}')
        w('')

    # -- marks -------------------------------------------------------------- #
    if p.marks:
        w('## Mission marks')
        w('')
        w('| Mark | On leg | Elapsed | Time | Burned | Gauge |')
        w('|---|---|---|---|---|---|')
        for m in p.marks:
            clock = m.get('clock') or '—'
            gauge = ('—' if m.get('indicated_pct') is None
                     else f'{_num(m["indicated_pct"])}%')
            w(f'| {m["label"]} | {m["leg"]} | T+{_num(m["elapsed_hours"], 2)} h | '
              f'{clock} | {_num(m["litres_burned"])} L | {gauge} |')
        w('')
        w(f'Distances from home are along the planned track, in {waypoint_unit}. '
          f'A hold is taken at the START of its leg, so it delays that leg\'s '
          f'crossings and everything after it.')
        w('')

    # -- sensitivity -------------------------------------------------------- #
    if p.sensitivity:
        w('## If the sea-state premium is wrong')
        w('')
        w('The premium is an **assumption**, not a fitted value. This band '
          'matters more than the single number above.')
        w('')
        w('| Premium shift | Total fuel | On return | Margin | |')
        w('|---|---|---|---|---|')
        for s in p.sensitivity:
            shift = ('as planned' if s['premium_delta'] == 0
                     else f'{s["premium_delta"] * 100:+.0f}%')
            state = 'dry' if s['runs_dry'] else ('ok' if s['within_reserve'] else 'breach')
            w(f'| {shift} | {_num(s["total_litres"])} L | '
              f'{_num(s["remaining_fraction"] * 100)}% | '
              f'{_num(s["margin_litres"])} L | {state} |')
        w('')

    w('---')
    w('')
    w('Generated automatically on plan. The sea-state premium and the wind '
      'scaling are assumptions; the reserve band below 68% indicated has never '
      'been measured. See `docs/QUICKSTART.md` and the reports in `docs/`.')
    w('')
    return '\n'.join(out)
