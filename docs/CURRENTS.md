# Currents: acquisition and model integration

How a tidal current gets from a NOAA model into a fuel plan, what it is allowed
to change on the way, and what it is not.

This is the **only part of the planner that touches the network**. Everything
else — the fuel law, the gauge profile, the reserve arithmetic, the whole
`model.json` — runs offline and is unaffected by anything in this document. If
the button is never pressed, the planner behaves exactly as it did before any of
this existed.

---

## 1. Acquisition

### 1.1 The source, and the one decision that mattered

NOAA runs the **Delaware Bay Operational Forecast System (DBOFS)** and publishes
it over OPeNDAP. It publishes each cycle in two forms, and they are not
interchangeable:

| product | grid | velocities | what using it costs |
|---|---|---|---|
| `fields` | curvilinear, staggered u/v | **grid axes** | must average u and v onto rho points, then rotate every cell by `angle` before a bearing means anything |
| `regulargrid` | rectilinear 0.005° | `u_eastward` / `v_northward`, already true-referenced | nothing |

**This reads `regulargrid`**, because the two operations the native product
needs are exactly the two that fail *silently*: get the rotation wrong and every
set is out by tens of degrees while every speed still looks entirely reasonable.

That is a shortcut, so it is evidenced rather than assumed —
`python currents.py crosscheck` does the native path by hand, rotation and all,
and compares. The two agree to a **median 0.07 kt and 1.0°**. Keep that
subcommand; it is the only thing standing behind the choice.

### 1.2 What a cycle is

A *cycle* is one model run, named by date and hour (`dbofs_20260813_t00z`):

- **6 nowcast hours** running up to the cycle time, then **48 forecast hours**
  after it — a **53 h span** in 54 hourly frames.
- Domain **37.79–40.22 N, 75.89–73.25 W** on a 0.005° mesh (487 × 529 nodes).
- Surface layer only (`Depth[0] == 0.0 m`, checked by `verify`).
- About **2 MB per hour** over the wire; **33 MB gzipped** for the whole box,
  roughly half a minute to fetch.

Cached under `ofs_cache/` (gitignored) as a `_meta.json` plus a gzipped binary
of `u`/`v` frames. `python currents.py cycles` lists what NOAA is serving;
`fetch` caches one.

### 1.3 Which cycle answers, and what happens when none does

Requested times do not always land inside what is cached. The resolution is a
ladder, and **real data always beats an estimate**:

1. **A cached cycle covers the window** — `covering_cycle`, which checks the
   **box as well as the span**. A cycle fetched for a box round Lewes cannot
   answer for a mission out of Cape May, and without that check it would report
   "no water on this leg" for every leg — which reads like a forecast of slack
   water rather than the wrong file.
2. **NOAA still serves one that covers it** — `remote_cycle_covering` reads the
   catalog and works each cycle's span out from its hour-file names, so asking
   costs **one page rather than 33 MB**. If one covers the window,
   `ensure_cycle_covering` downloads it and the answer is *real data*.
   **⚠ The archive is only about two days deep.** Measured 2026-08-13: three
   cycles that day, four the day before, one the day before that, nothing
   earlier. So this answers a mission that started within roughly two days and
   nothing further back.
3. **Nothing covers it** — the value is **projected** across whole tidal cycles
   (§1.4) and flagged everywhere it surfaces.
4. **Past the projection's reach** — it refuses. A guess has a range beyond
   which it stops being one.

### 1.4 Projection, and why by tidal cycles

The current here is semidiurnal, so a time the forecast cannot reach is
estimated from the value one **M2 period (12.4206 h)** away — not by holding the
last value, and never by extrapolating a line through a reversing tide.

That is a measurement, not a preference. Checked against this model's own 54 h
output, substituting the value *n* whole cycles away:

| method | RMS error vs the model |
|---|---|
| **project 1 cycle** (12.4 h) | **0.19 kt** |
| **project 2 cycles** (24.8 h) | **0.14 kt** |
| **project 3 cycles** (37.3 h) | **0.21 kt** |
| hold the last value | 0.57 – 2.21 kt |
| assume slack water | 0.36 – 1.46 kt |

Flat out to 37 h, because a tide repeats rather than decays. At the Bay entrance,
where it runs to 2.2 kt, projection lands within 0.27 kt while persistence is
wrong by the whole tide. **`MAX_PROJECT_CYCLES` is 3 for that reason** and not
because it is a round number: beyond about 37 h there is no evidence in hand,
and the non-tidal part — wind setup, river flow — does not repeat at all.

Two things projection does **not** do:

- **It never moves a position.** `at()` returns `None` where the model has no
  water, and it still returns `None` however far the time is shifted. No amount
  of time shifting invents an ocean over land or outside the domain.
- **It never happens silently.** `at_best` returns `(values, shift_hours)`, and
  a shift of `0.0` is the only thing that means "real". Downstream, a projected
  leg carries `estimated` and `projected_hours`, its note begins `ESTIMATE`, the
  provenance label gains `PART ESTIMATED`, the response carries `estimated_legs`
  with a warning, and the UI names the legs. An estimate that reads like a
  forecast is worse than no estimate, because it is acted on with the same
  confidence.

`at()` itself is unchanged and still raises outside its span. It is the truthful
primitive everything else is built on; projection is an explicit opt-in beside
it, not a loosening of it.

### 1.5 How the acquisition is checked

Direction fails quietly, so it is verified three independent ways:

1. **`currents.py crosscheck`** — the native ROMS path by hand, against the
   regridded product (median 0.07 kt / 1.0°).
2. **`tools/dbofs_plotcheck.py`** — redraws the extracted field onto NOAA's own
   published PNG in its colour bins, deriving the georeference from the plot's
   own graticule rather than trusting a stated corner.
3. **`currents.py station_check`** — against CO-OPS harmonic predictions at a
   real station: **r = +0.987 over 54 h**.

`currents.py verify` runs the structural rails, including that land returns
`None` rather than `0.0` and that a time outside the span raises rather than
extrapolating.

---

## 2. Model integration

### 2.1 What it does not touch

**No coefficient moves.** `model.json` is untouched by any of this, the engine
gained nothing, and no fitted value is involved. The forecast supplies **two
inputs that have existed since 2026-08-11**: a leg's `current_speed_kt` and
`current_set_deg`. That is the whole design. An operator who types the tide in
from a table gets exactly the same arithmetic.

### 2.2 The two seams

There are precisely two places a current enters a plan:

**(a) Per leg — one number each.** `resolve_legs` dead-reckons the mission from
the departure position and time, samples each leg along its own track at the
time the vehicle would be there (13 samples), and vector-averages. A transit is
walked down its course; **a survey holds position and only advances the clock**,
because a lawnmower ends roughly where it began and walking it `lines ×
line_length` down the first line's course would put the run home tens of miles
into the wrong water. A loiter is taken at the **start** of its leg, matching
the engine, so a hold changes which way the tide is running for everything after
it.

**(b) Along the track — a field.** `env_factory` supplies an
`env_at(lat, lon, hours)` callback that `plan(env_at=…)` calls **once per run**
— per survey line, per transit segment — with where the vehicle is and how many
hours into the mission. It returns that leg's environment with the current
replaced by what the forecast says *there and then*. This is the seam that turns
the current from a number into a field, and it needs geometry: a leg with no
`track` or `pattern` is never sampled.

`env_at.asked` / `.covered` report how much of the mission the field answered
for, and `.estimated` how many of those answers were **borrowed** rather than
forecast — reporting only `covered` would let a projected tail read as full
coverage.

### 2.3 What a current is allowed to change

**Fuel, never the clock.** `required_stw_kt()` converts the required speed over
ground into the speed through the water the hull must actually make, and *that*
goes through the gondola's speed law to get RPM. A leg still takes
`distance ÷ speed` hours; what changes is what it costs to hold that speed.

**⚠ It is partly counted twice, and the leg note says so.** The speed law was
fitted against SOG in an unrecorded tide, so some tidal effect is already inside
it. Reading the tide off DBOFS makes the *input* better; it does not make the
correction clean. The honest framing is "compare two plans" or "price today's
tide", not "a calibrated tidal model". Stripping it properly needs
reciprocal-heading pairs at steady RPM — see "Known gaps".

### 2.4 Provenance

The forecast is perishable, so the cycle is named wherever the numbers surface,
and **the label is qualified when any of it was estimated**. The label is what
the mission report prints, so the qualification travels with the numbers rather
than living only on screen. Typing over any current box drops the forecast label
entirely — the report must never attribute an operator's own number to a NOAA
run.

---

## 3. A worked example

Real transcript, `dbofs_20260813_t00z`, span 2026-08-12 19:00Z → 2026-08-15
00:00Z. Departing the DriX berth at Lewes (38.7897 N, 075.1609 W) at
**2026-08-13 12:00Z**: 12 NM out on 045, a 12 × 2 NM survey on 020, 12 NM home
on 225.

### 3.1 Acquisition

```
Transit out    12:00Z  13/13 samples   1.09 kt setting 312.3   along -0.05 kt
Survey         13:42Z  13/13 samples   1.45 kt setting 142.9   along -0.79 kt
Transit home   17:42Z  13/13 samples   1.72 kt setting 139.3   along +0.13 kt

label: DBOFS 20260813 t00z surface forecast (2026-08-12T19:00Z to 2026-08-15T00:00Z)
```

`along` is the signed component on the course made good: **plus is a fair tide,
minus is foul**. The survey is fighting 0.79 kt of it.

### 3.2 Integration — the same mission, three ways

```
slack water (no current)          17.18 L    7.43 h
per-leg currents from forecast    18.08 L    7.43 h    (+5.2% fuel)
sampled along the track           17.64 L    7.43 h    (+2.7% fuel)
                                  field answered 24 of 24 runs, 0 estimated
```

Three things worth reading off that:

- **The clock is identical in all three.** 7.43 h whatever the tide is doing.
  That is §2.3 visible: a current moves the fuel and never the clock.
- **The tide costs 5.2%** against planning it as slack water — which is the
  number an operator would otherwise never see.
- **Sampling along the track gives 2.7%, not 5.2%.** The per-leg figure is
  nearly double. It is not more conservative by luck: the survey sits on one
  ground for four hours while the tide turns under it, so a single vector
  average of a reversing current overstates what the boat actually fights. This
  is exactly why the field exists, and why entering geometry is worth the
  trouble.

### 3.3 Out of range

The same mission, departing 2026-08-14 21:00Z, with only 3 h of forecast left:

```
Transit out    forecast    0.43 kt 299.0
Survey         ESTIMATED   1.94 kt 316.8   (+12.42 h borrowed)
Transit home   ESTIMATED   0.38 kt 323.4   (+12.42 h borrowed)

label: … — PART ESTIMATED: Survey, Transit home projected across tidal
       cycles from outside the forecast
```

The first leg is inside the span and answered normally. The other two are past
the end, so each borrowed the value one M2 period earlier — and says so, in the
row, in the label, and in the plan's warnings.

---

## 4. Limits, in one place

- **Surface only.** The DriX draws 2.0 m; this is the 0 m layer. No shear is
  modelled.
- **Hourly, ~500 m mesh.** Finer geometry than that is not resolved; the planner
  never samples finer than the source.
- **Partly double-counted** against the SOG-fitted speed law (§2.3).
- **The archive is ~2 days deep** (§1.3), so a mission older than that cannot be
  answered exactly, only estimated.
- **Projection reaches 3 tidal cycles**, about 37 h, and refuses beyond it.
- **A position with no model water is never answered**, at any time, by any
  route.
- **Delaware Bay only.** DBOFS is one OFS among many; nothing here is
  region-general.

## 5. Where the code is

| what | where |
|---|---|
| acquisition, cache, projection | `currents.py` |
| the two seams | `resolve_legs`, `env_factory` |
| endpoints | `POST /api/currents`, `/api/plan` with `use_forecast_currents` |
| UI | the **Currents from forecast** button, **Read currents along the track** |
| verification | `currents.py verify` / `crosscheck` / `station_check`, `tools/dbofs_plotcheck.py` |
| tests | `tests/test_currents.py` — no test touches the network |
