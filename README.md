# DriX mission fuel planner

Enter sea state, a wind vector, and the three legs of a mission — transit out,
survey, transit home — and get fuel burn, endurance, and margin against the 25%
return-to-port reserve.

A Python engine with a local browser UI. Standard library only: nothing to
install, works offline.

```bash
python server.py
```

Opens <http://127.0.0.1:8765>. `--port N` to move it, `--no-open` to skip
launching a browser.

Or double-click **`start_planner.bat`** — same thing, for people who do not use
a terminal. `powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1`
puts a shortcut to it on the desktop, pointed at whatever folder the project is
actually in. Moving the whole thing to another Windows machine is two steps and
needs nothing but Python: see [`docs/MOVING.md`](docs/MOVING.md).

New to it? [`docs/QUICKSTART.md`](docs/QUICKSTART.md) is a one-page tour. The **Quick
start** button in the app renders that same file, so the two cannot drift. Every
control in the app also explains itself on hover — or on keyboard focus — in a
tip anchored to the control rather than to the pointer, so the cursor never sits
on the text it just asked for.

```bash
python -m unittest discover -s tests -v
```

---

## What it is built on

Every coefficient lives in [`model.json`](model.json), traced back to
`DriX_fuel_efficiency_analysis.xlsx` and from there to the source logs. Each
block records whether it is `"fitted": true` — a regression against measured
data — or `"fitted": false`, meaning it is an assumption you may want to change.

**Fitted from measurement**

| Model | Form | Fit |
|---|---|---|
| Fuel rate (engine-side, shared) | `L/h = -1.776 + 0.002495 · RPM` | R² 0.972, 1020–2500 rpm |
| Speed vs RPM, EM712 gondola (2024) | `kt = 1.135 + 0.002624 · RPM` | R² 0.939 |
| Speed vs RPM, EM2040 gondola (2022) | `kt = -0.043 + 0.004033 · RPM` | R² 0.991 |
| Heading effect magnitude | ±6.91% speed spread at constant RPM | mean of three four-heading tests |

## Gondolas

Both datasets are the same hull (DriX-8) with different gondolas: the 2022
shakedown data is the **EM2040**, the 2024 trials the much larger and
heavier **EM712**. The planner carries both as selectable configurations.

**Both gondolas now carry measured curves.** The EM2040 was refitted directly
from six days of MCAP logs (04–09 Aug 2026, 4.85 h of steady cruise, flow meter
vs the PLC thruster-RPM channel — the shaft RPM sensor was faulted throughout):

| EM2040 (measured, Aug 2026) | |
|---|---|
| Fuel | `L/h = 2.9364 − 0.0029704·RPM + 1.4581e-6·RPM²` (R² 0.9995, 1400–3100 rpm) |
| Speed | `kt = 0.4861 + 0.003448·RPM` (R² 0.956; SOG-based, ±5% tidal) |
| At 8 kt | 2179 rpm · 3.39 L/h · **2.36 NM/L** (interpolation) |
| Loiter | **0.95 L/h** at ~1005 rpm (20.8 h observed) |

The earlier transfer estimate (EM712 fuel law through the 2022 speed curve)
predicted 2.50 NM/L at 8 kt — validated to ~6%. The EM712 at 8 kt is
1.70 NM/L (extrapolated beyond its 1020–2500 rpm window), so the EM712 costs
about **1.39×** the fuel per nautical mile at survey speed.

The refit also produced the first **direct gauge calibration**, since extended
across every day with material burn: **2.06 ± 0.11 L per indicated point** over
the 72–86% band (see `gauge_calibration` in `model.json`). A linear 250 L tank
would give 2.50 — the measurement sits **4σ** below it, and agrees with the
independent DD2024 refuel figure of 2.09 to 0.3σ. Single-day figures quoted in
earlier versions of this file (2.30, 1.73) were individual low-precision bands,
not the calibration.

The UI defaults to the EM2040 (currently fitted); the engine API defaults to
`em712` for backward compatibility — pass `Vessel(gondola='em2040')` from code.

### Refitting again later

Extract per-timestamp fuel flow, thruster RPM, SOG and COG from the MCAP bags;
segment for steady straight-line cruise (stable RPM/SOG/COG over 60 s); fit
per gondola and update the `gondolas.*` blocks in `model.json`. The mutation
tests fail loudly when coefficients change planning behaviour — that is what
they are for.

**Assumed, and editable**

- **Sea-state premium.** The source data contains exactly one (sea state, RPM
  premium) pair — the 2022 operational window at Beaufort 3–4 / WMO 2–3, which
  supports somewhere between 0% and 13% depending on which tank capacity you
  believe. Attempting to measure the relationship directly by binning that
  window on heave fails outright: the bins come out with no consistent relation,
  because speed and tank level move with sea state and one 22-hour window cannot
  separate three effects. The table in `model.json` interpolates through that
  single anchor and extrapolates past it on judgement. **Treat it as a dial.**
- **Wind scaling.** The ±6.91% magnitude is measured, but it mixes wind, sea and
  current and cannot be decomposed. How it scales with wind speed is a guess —
  currently a square law about a 12 kt reference. Set `wind_exponent` to `0` to
  disable scaling entirely.
- **Tank volume is ESTABLISHED at 250 L** — engineering drawings, every hull,
  every mission (`tank_volume` in `model.json`). It is not an assumption and it
  is not editable.
- **The gauge span is a different quantity, and it is ~206 L.** Three
  independent methods agree: the DD2024 refuel (pumped litres, 2.09 L/pt), the
  2022 telemetry drawdown (2.05), and the metered gauge scale (2.06 ± 0.11).
  All three are **slopes**, so a partial fill or partial drawdown cannot bias
  them — that moves a window along the gauge, it does not change what a point
  is worth. So **~44 L of the tank is real fuel the needle never shows you**,
  and it is either non-linearity in unmeasured bands or volume outside the
  sender's travel. Top-third data cannot say which; one drawdown would.
- **The reserve floor is a needle position, not a number of litres.** Because
  the policy is written in indicated percent, the fuel a mission may spend is
  what the gauge holds between the start level and the floor — an integral over
  the gauge, with no capacity assumption in it. Every plan reports it as the
  `gauge_*` fields and the *Needle on return* tile, and warns when the gauge and
  the assumed capacity reach different verdicts.

  **Adopted reading (2026-08-09): the gauge spans the tank and is non-linear.**
  `gauge_profile.reading = "A"` in `model.json`. The gauge is a *profile*, not a
  scalar: the measured 72–86% band stays at 2.06 L/point and the other 86 points
  carry the balance of the 250 L drawing volume, ~2.57. Mission fuel and the
  predicted needle are integrals over it. **185.7 L** to the 25% floor, against
  154.5 L on the conservative reading (B), which is one segment on the same code
  path. Set `reading` to `"B"` to plan on the measured band alone. (Review
  caught this paragraph still quoting 211/175 L — figures from the retired 15%
  floor.)

  This is an **inference, not a measurement**, and it moved planning fuel 21% in
  the unsafe direction. It also moved what the planner depends on: a +20% error
  in the measured gauge scale now shifts mission fuel by about 1 L, because the
  profile re-normalises to the drawings, while the tank volume drives it almost
  entirely. One drawdown to the floor settles which reading is right.

  **The needle decides the headline.** `PlanResult.verdict` is a single field —
  `ok` / `gauge_breach` / `breach` / `dry` — and every surface renders it, so
  the UI and the API cannot drift. `gauge_breach` means the capacity row passes
  but the needle does not: the banner goes red and reads **BREACHES ON THE
  GAUGE**, naming both bases. `within_reserve` keeps its capacity-only meaning.
  Spare range and time are quoted against `binding_margin_*` (whichever floor
  binds first), and **`max_survey_length` solves to that same floor** (370.9 NM
  on the default vessel under reading A at sea state 2; 301.8 NM under B). A
  solver whose answer the planner then flags red would be a bug, and a test
  asserts it never happens.

## Weather is per leg

A mission runs two days at survey speed, so the wind on the bow going out is not
the wind coming home. Every leg takes its own **`wmo_sea_state`**,
**`wind_speed_kt`**, **`wind_from_deg`**, **`current_speed_kt`** and
**`current_set_deg`**, and the UI puts all five on each leg card with the
sea-state premium shown beside the selector.

Each is optional and independent. **Omit one and the leg uses the mission
`environment`; set it to 0 and the leg is becalmed** — those are different
things, and `0` is a real sea-state code as well as a real wind speed. Setting a
speed alone keeps the mission's direction.

`environment` remains on the API as the fallback for callers that omit per-leg
values. The UI no longer sends one.

The effect is not small. A symmetric 25 NM out-and-back at 7 kt, with 25 kt on
the nose and 2 kt of foul tide outbound against 5 kt astern and the same tide
fair coming home, costs **31.8 L out and 5.7 L home** — and both legs are flagged,
one above the fitted RPM window and one below it.

## Current — set and drift

`current_speed_kt` and `current_set_deg` sit beside the wind fields.
**Note the conventions are opposite, as at sea:** a wind is named for where it
blows **from**, a current for where it **sets toward**.

Unlike the wind premium, this is not fitted — it is kinematics. The water
velocity the hull must make is the ground velocity minus the current:

```
STW = |Vg − Vc| = sqrt(Vg² + Vc² − 2·Vg·Vc·cos(set − course))
```

That through-water speed is what goes through the speed law into RPM. A head
current adds its drift, a following one subtracts it, and a beam current makes
the hull crab and costs a little either way. **The clock is untouched** —
duration follows SOG, because the ground still has to be covered.

It is resolved **per line**, so a reciprocal pair does not cancel: 2 kt setting
180 against a 000/180 transit pair takes the head leg from 9.6 to 17.2 L and the
following leg from 9.6 to 5.7 L — 22.9 L against 19.2 L in still water.

**One caveat that matters.** The speed law is SOG-based, fitted in an unrecorded
tide, and the ±6.91% heading effect already mixes wind, sea and current. So an
explicit current is **partly counted twice**, and the leg note says so. Use it to
compare plans and to ask what today's tide costs; do not read it as a calibrated
tidal model.

A following current can drop the required RPM below the fuel law's fitted floor,
and the ordinary extrapolation flag fires on it.

## Mission geometry

A leg can carry real geometry instead of a bare distance and course, and then
the environment is sampled where and when the vehicle actually is:

- **`track`** — a polyline of `[lat, lon]` for a transit.
- **`pattern`** — a survey lawnmower: `anchor`, `bearing_deg`, `length_nm`,
  `spacing_nm`, `lines`, and optionally `step_bearing_deg` / `turn_radius_nm`.
  The API is in nautical miles throughout; the **app's Line spacing box is in
  metres** and converts at the one seam that builds the request, so a spacing
  reads the way a surveyor writes it without a second unit reaching the engine.

Both are optional, and a leg without them plans exactly as it always did.
Given either, `distance_nm` and `course_deg` are derived from the geometry, and
`plan(env_at=...)` calls back per run — per survey line, per transit segment —
with the position and the hours elapsed. `currents.env_factory()` supplies a
callback backed by the forecast cache.

**Why it matters.** A current that turns cannot be represented by one number
per leg. At Delaware Bay Entrance a survey held on one ground costs **5–9%**
more than its own vector mean, and a 16 h survey averages to **0.16 kt** —
which reads as slack — while the water runs to 2.2 kt and reverses under it.

**Turns between lines are modelled**, from `model.json`'s `turn_model` block,
which is an **assumption** (`fitted: false`), not a measurement — the same
standing as the sea-state premium. Where line spacing is less than twice the
turn radius a simple 180° will not fit and the vehicle runs out and back, which
is charged accordingly. Surveys therefore cost more than they did before this,
because turn time and fuel were previously absent altogether.

### Importing a line plan

The **Mission geometry** card takes a file and fills the geometry from it:

| Format | Notes |
|---|---|
| CSV / TXT | endpoint-per-row (`lat1,lon1,lat2,lon2`) or point-per-row grouped by line name; columns from the header, positional as a fallback |
| GeoJSON | `LineString`, `MultiLineString`, and Features of either |
| KML / KMZ | `LineString` placemarks; KMZ unzipped in memory |
| GPX | routes and tracks |
| Hypack LNW | the plain-text `LIN`/`PNT` line file |

Deliberately **not** accepted, each for a reason rather than for want of time:
**shapefile** (geometry is easy, but the CRS lives in a sidecar `.prj` as WKT
and guessing a datum from partial WKT is the silent-failure class this avoids),
**UKOOA P1/90 and SEG-P1** (fixed-column formats where a one-character offset
still parses and yields plausible positions — these need a real sample to pin
against), and **QINSy / NaviPac / PDS native databases** (proprietary
containers, not interchange formats; all of them export to something above).

**Coordinates are where this bites.** Geographic degrees are read as they come,
decimal or degrees-minutes-seconds, and a hemisphere letter beats a sign — so
`-75.5 W` is west, not east. Projected coordinates are accepted for **UTM on
WGS84 only, and only when you give the zone**; a zone is never guessed, because
the wrong one puts the survey hundreds of miles away with nothing on screen to
show for it. WGS84 is assumed; NAD83 differs by 1–2 m here, two orders of
magnitude under the forecast's 500 m mesh.

The import reports what it read — line count, total distance, the **line
axis**, mean gap, and the first point with its hemisphere — so you can check it
against what you drew before planning against it.

## Currents from the NOAA forecast

Those two numbers per leg can be read off the operational forecast instead of a
tide table. Put a departure position and start time on the Mission clock card
and press **Currents from forecast**: the mission is dead-reckoned leg by leg,
the forecast sampled along each track at the time the vehicle would be there,
and the current boxes filled.

This is the one thing in the planner that reaches the network, so while it is
reading, the note under the button goes **bold red and blinks** — a state that
clears itself whatever the read returns. A red note that has stopped blinking is
a failed read, not a running one.

**When the mission runs outside the forecast**, real data is tried first: a
cached cycle, then one NOAA still serves — its archive runs about two days back,
so a mission that started yesterday is answered exactly. Only a time nothing
covers is **estimated**, by borrowing the value a whole tidal cycle away, which
is within about 0.2 kt of the model against 0.6–2.2 kt for simply holding the
last value. Every estimated leg is flagged in the response, named in the note
and marked in the report — and past three tidal cycles it stops estimating and
says so. A position with no model water is still left empty whatever the time.

**Reading currents along the track** does the same, and it matters more there: a
survey held on one ground through a turning tide is what the field exists for,
so losing its tail to the forecast horizon would put the leg back on a single
averaged number at the point the tide is doing the most. The plan reports how
many sampled runs the field answered for and how many of those were estimated,
and says so in its warnings.

`currents.py` is the module behind it — standard library, no new dependency —
and it is a usable tool on its own:

```bash
python currents.py cycles                              # what NOAA is serving
python currents.py fetch                               # cache the latest cycle
python currents.py point --at 38.7828,-75.1394         # a position, hour by hour
python currents.py frame --time 2026-08-13T14:00Z --csv frame.csv
python currents.py verify                              # the rails
```

**What it reads.** NOAA's Delaware Bay OFS (`DBOFS`) `regulargrid` product over
OPeNDAP: hourly surface velocity on a 0.005° mesh covering 37.79–40.22 N,
75.89–73.25 W, six nowcast hours plus a 48-hour forecast. The published map-plot
animation is drawn from the same model run — those are rendered PNGs, so this
reads the numbers behind them rather than the pictures. `--ofs` points it at
another region.

**Why the `regulargrid` product.** The native ROMS `fields` carry velocity in
GRID axes on staggered u/v points, needing per-cell rotation by `angle` before a
bearing means anything; `regulargrid` ships `u_eastward`/`v_northward` already
true-referenced. `python currents.py crosscheck` does the native path by hand
and compares — median 0.07 kt and 1.0° apart, so the shortcut is evidence
rather than assumption.

**How it was checked.** Against the native grid as above; against NOAA's own
published plot, redrawn by `tools/dbofs_plotcheck.py` after proving the
georeference off the plot's graticule; and against CO-OPS harmonic current
predictions at Delaware Bay Entrance — **correlation +0.987 over 54 hours, RMS
0.30 kt, peak 2.25 kt against the model's 2.28, slack water within half an
hour**. `python currents.py station` re-runs that comparison.

**Positions are displayed with their hemisphere** — `075.1394 W`, not
`-75.1394 E` — in the CLI, the overlay tool and the UI's departure readout,
with longitude padded to three degrees as charts write it. The boxes, the JSON
API, the CSV exports and the cache metadata all stay **signed decimal degrees**,
because that is what every consumer of them parses; the readout is where a
dropped minus becomes visible instead of merely present.

**Limits worth knowing before trusting a number.**

- It is a **forecast**, and a perishable one: cycles run four times a day. The
  mission report records which cycle filled the boxes, and the label is dropped
  the moment a current is typed over by hand.
- It is the **surface** layer. The gondola sits below it and the shear is real
  in a stratified estuary; 22 standard depths are available in the source if
  sampling at draft is ever wanted.
- A leg the forecast cannot see — outside the domain, or over land — is left
  **empty, never zero**. No data and slack water are different answers.
- The double-count above still applies. Real currents do not fix a speed law
  fitted in an unrecorded tide.
- This is **the only part of the planner that touches the network.** Everything
  else works offline, and these boxes can always be typed by hand.

## Loiter — delays you can plan around

Things happen at sea. Every leg takes a **`loiter_hours`**: time held on station
making no way, entered in the UI in minutes or hours with `+15m` / `+1h` /
`clear` buttons, and folded into the plan when you press **Plan mission** again.

It is charged at the gondola's **measured idle burn** — 0.95 L/h for the EM2040,
from 20.8 h of observed idle at ~1005 rpm — so a two-hour hold costs 1.9 L and
two hours of clock.

Three things worth knowing:

- **The hold is taken at the start of its leg.** One rule follows: a hold
  delays that leg's own crossings and everything after it — a launch delay
  moves the outbound waypoints, a hold on the way home arrives home late. (It
  was originally taken at the end, which read as "hold after arriving" on the
  return leg and moved nothing.) The placement is still a convention, not a
  measurement.
- **Underway figures stay underway.** A leg's `hours`, `litres`,
  `fuel_rate_lph` and `nm_per_l` describe making way, so `litres = rate × hours`
  still holds; `total_litres` and `end_hours − start_hours` are what the leg
  actually costs and takes.
- **The idle figure is calm water.** Station-keeping in a seaway has never been
  measured, so a hold carries **no sea-state premium**. The leg note says so
  rather than letting you assume it was included.

A gondola with no measured idle burn — the EM712 — does not borrow the EM2040's.
Its rate comes from its own fuel law at idle rpm and is flagged as an
extrapolation, because that rpm is below the window the law was fitted over.

## How a leg is computed

```
required SOG  ->  RPM in benign water     (speed-vs-RPM, inverted)
              ->  + sea-state premium
              ->  + heading premium        amplitude · (W/W_ref)^n · cos(θ)
              ->  actual RPM
              ->  fuel rate L/h            (fuel-vs-RPM)
              ->  litres                   (rate × hours)
```

θ is measured between the leg course and the direction the wind comes **from**,
so dead ahead is the full penalty and dead astern the full credit.

## Surveys are flown line by line

A survey is a lawnmower, so it takes **lines**, **line length** and a **bearing**;
alternate lines run the reciprocal, and the distance is `lines × line length`.
Fuel is summed line by line — never from an averaged premium.

That distinction is the whole point, and it is not cosmetic. The heading
premium averages to zero over a reciprocal pair, so the old single-distance
survey cancelled it exactly. But **fuel is convex in RPM**: the mean of the two
rates is strictly greater than the rate at the mean premium, because the line
into the weather costs more than the reciprocal saves. Cancelling therefore
*understates* survey fuel, and the error grows with the square of the wind:

| Wind | Penalty against the cancelled figure |
|---|---|
| 12 kt | +0.9% |
| 20 kt | +7.1% |
| 25 kt | +17.2% |

An **odd** number of lines cannot balance even in principle — one direction gets
an extra line — which the old input could not express at all. Three lines into a
20 kt wind costs **+21%** against the cancelled figure, and 12 lines over the
same ground costs +7%.

Two consequences worth knowing:

- **Extrapolation is flagged per line, not on the mean.** At 9 kt in 25 kt of
  wind the mean sits near 2540 rpm, comfortably inside the fitted window, while
  the into-wind lines need over 3280 and fall outside it. Flagging the mean
  would hide that entirely.
- **`max_survey_length` holds the line count and scales the length.** Scaling
  the count instead would flip the odd/even parity as it searched, so the
  objective would jump rather than vary smoothly.

### How many lines will actually fit

*Max survey for the reserve* answers the operational question directly: with
the line length and bearing fixed by the area, **how many lines can you run
before you must turn for home?**

```
Fuel allows 31 lines of 10.0 NM — 310.0 NM of survey.
19 of the 50 planned lines will not fit in this run.
```

`max_survey_lines()` searches integers rather than bisecting a distance,
because the marginal cost alternates sharply — at 25 kt an into-wind line costs
about **3.5x** the downwind line that follows it. Whether the last line fits can
turn on which direction it happens to run: at 20 kt with 20 NM lines the answer
is 16, and the 17th breaches purely because it runs into the weather, even
though the same 340 NM as an even set of lines fits comfortably.

Total fuel is strictly increasing in the line count, so the margin decreases
monotonically and the answer is well defined — a test pins that assumption,
because if it ever stopped holding the search would return a wrong count rather
than fail. A survey given as a plain distance still gets the continuous
answer.

A survey given as a plain distance, with no line count, still means one
reciprocal pair — now with the convexity honoured rather than cancelled. The
guard against banking a tailwind across a whole survey is unchanged: the mean
heading premium is still zero for any even number of lines.

## Two things the tool will keep telling you

**Extrapolation.** Each gondola's fuel law has its own fitted window — EM2040
1400–3100 rpm, EM712 1020–2500 rpm. Any leg outside its gondola's window is
flagged with ⚠ and called out in the warnings. On the EM712 this fires at the
8 kt survey speed (~2616 rpm); on the EM2040 survey speed is interpolation, but
slow legs below ~5.3 kt (1400 rpm) are flagged instead — there is no cruise
data down there, only the loiter figure.

**Sensitivity over a single answer.** Because the sea-state premium is assumed,
every plan comes with a band showing the result at −5%, +5%, +10% and +20%
premium, and against all four candidate tank capacities. If a mission only
closes at the nominal 250 L and the lowest premium, that is worth seeing.

## Verdicts

- **WITHIN RESERVE** — returns at or above the floor.
- **BREACHES RESERVE** — gets home, but eats into the reserve.
- **RUNS DRY** — needs more fuel than the tank holds. A different failure, and
  reported as one rather than as a large negative percentage.

## Layout

```
model.json           coefficients + provenance; the one place to edit assumptions
engine.py            pure planning engine — no I/O, no web framework
mission_report.py    renders a PlanResult as Markdown — pure, no I/O either
server.py            stdlib HTTP server, loopback only, serves the UI and a JSON API
ui/                  index.html, app.js, styles.css — no CDN, no build step
tests/               engine, server, UI and mission-report suites
start_planner.bat    double-click launcher; finds the project from its own
                     location (%~dp0), so the folder can be moved or copied

docs/                EVERY document lives here
  QUICKSTART.md        one-page tour; the app's help panel renders this file
  MOVING.md            putting this on another Windows machine
  DriX_Fuel_Efficiency_Report.docx/.pdf   the analysis this tool implements
  DriX8_Fuel_Gauge_Linearity.docx/.pdf    the gauge against the flow meter
  DriX8_Fuel_Methods.docx/.pdf            which topics feed which numbers
  DriX8_Endurance_EM2040.xlsx/.csv        the Hourly Ops Log endurance tab
  missions/            mission reports, one per plan — GITIGNORED, generated

tools/               MCAP extraction + EM2040 refit pipeline, and the adopted
                     fit snapshot (em2040_fit_2026-08-09.json) behind model v2.4
                     also: the bag topic inventory + reference-doc builder, and
                     the four document builders — build_report.py,
                     build_gauge_report.py, build_methods_doc.py,
                     build_endurance_sheet.py — all sharing docx_style.py
                     (page setup, helpers, the table-width rail, and the
                     SOURCE_DATE build-date override)
                     plus make_shortcut.ps1 / make_icon.py / fuel.ico — the
                     desktop shortcut and the icon it wears, both generated
```

`README.md` and `CLAUDE.md` stay at the root: GitHub renders one there and
Claude Code reads the other there.

The bag topic reference is built from here but kept outside the repo, at
`D:\Claude\ROS2\DriX8_ROS2_Topic_Reference.docx` — see below.

## Mission reports

**Every plan writes a Markdown report to `docs/missions/`**, timestamped to the
second and never overwritten, and the response carries its path so the UI can
show you where it went. One file per press of **Plan mission**.

It records the verdict, the summary figures, every leg with its own weather,
the mission marks, the warnings and leg notes, and the sea-state sensitivity
band — all taken straight off the `PlanResult`, never recomputed, so a report
cannot disagree with the plan it describes.

```bash
python server.py --no-reports          # plan without writing them
python server.py --report-dir D:\logs  # write them somewhere else
```

They are **gitignored**: an operator's working output, not source. The four
documents beside them in `docs/` are the tracked ones.

## The report

`DriX_Fuel_Efficiency_Report.docx` — 28 pages, 12 figures, 29 tables — is the
full derivation behind `model.json`: the curve fits and their statistics, the
2022-to-2024 comparison, the tank-capacity investigation, why the sea-state
response could not be fitted, and the planning framework this tool implements.

Section 8 has the leg computation chain and a worked mission that matches the
engine exactly, because it is produced by *calling* the engine. Appendix A is
the coefficient table, generated from `model.json`.

```bash
python tools/build_report.py
```

**Regenerate it; never hand-edit a number in it.** Until v2.4.0 this was the one
document with no builder, and it was correspondingly the one that drifted — it
was still carrying the four-day EM2040 fit and a retracted claim about gauge
non-linearity. Now every derived figure is recomputed at build time and the
§3.3 model comparison is refitted in-script, reproducing `model.json`'s
coefficients to nine significant figures.

Two inputs are not computed, both flagged in the builder: the **source
observations** (2024 trial steps, four-heading test, DD2024 refuel, Exail ROE
costs), which are transcribed measurements and therefore inputs; and the
**`SRC_2022` aggregates**, because the 2022 per-observation log is not in
this repo. That second one, plus Figure 8 which is drawn from it, is the
report's only remaining drift risk. The `.pdf` is a Word export and is not
produced by the builder — refresh it separately after a rebuild.
Section 9 is the data quality register — every known defect in the source
material and how it was handled.

## The source bags

`D:\Claude\ROS2\DriX8_ROS2_Topic_Reference.docx` documents what the MCAP
recordings actually contain: **241 ROS 2 topics across 26.8 M messages and ~77
hours** (4–7 Aug 2026), grouped by subsystem with publish rates and
descriptions, plus field definitions for the message types that matter — pulled
from the schemas embedded in the bags, so they match that firmware exactly.
Read it before mining the logs for anything new; it is also where the shaft-RPM
fault and the missing 2026-08-07 segment are written down. It opens with a
linked contents page — 30 entries, page-numbered — and runs to 16 pages.

The document lives outside this repo and so is **not versioned**: a rebuild
overwrites it in place with no history to fall back on. The generator and the
descriptions it draws from are versioned here, so the document is always
reproducible — but only by rebuilding it.

```bash
python tools/topic_inventory.py       # scan the bags -> tools/topic_inventory.json
npm install --prefix tools            # one-off; tools/node_modules is gitignored
node tools/build_topic_doc.js         # -> D:\Claude\ROS2\...docx (pass a path to override)
powershell -File tools/bake_toc.ps1   # fill in the contents page (Windows + Word)
```

The last step is required: the contents page is a Word field that the generator
leaves empty, so without it only Word shows any entries. It needs desktop Word
over COM, which makes that one step Windows-only — the rest of the pipeline is
not.

The scan reads bag metadata and MCAP summary sections only — never the message
stream — so it takes seconds where `tools/extract_bags.py` takes tens of
minutes. It needs `pyyaml` and `mcap`; the planner itself stays stdlib-only.
Topic descriptions live in `topic_inventory.py`, not in the document builder,
and the script refuses to emit anything if a topic has no description — a new
firmware that adds topics fails the run rather than shipping blank rows.

### API

| Route | Body | Returns |
|---|---|---|
| `GET /api/model` | — | `model.json` |
| `POST /api/plan` | `{environment, vessel, legs, start_time?, waypoints?, waypoint_unit?}` | full plan |
| | a leg also takes `loiter_hours` | |
| `POST /api/max-survey` | same | longest survey holding the reserve |
| `POST /api/currents` | `{lat, lon, departure_utc, legs, offline?}` | per-leg set and drift, plus the cycle it came from |

`departure_utc` is a **UTC instant** (`2026-08-13T18:00:00Z`), unlike `start_time`
above, which is the mission clock's local wall time. The UI converts; a caller
must too. `/api/currents` is the only route that reaches the network, and
`offline: true` makes it refuse rather than try.

```bash
curl -s localhost:8765/api/plan -H 'Content-Type: application/json' -d '{
  "environment": {"wmo_sea_state": 3, "wind_speed_kt": 15, "wind_from_deg": 270},
  "vessel": {"capacity_l": 250, "reserve_fraction": 0.25, "start_level_fraction": 1.0},
  "start_time": "2026-08-11T06:30", "waypoints": [13, 26], "waypoint_unit": "km",
  "legs": [
    {"name":"out","kind":"transit","distance_nm":25,"speed_kt":7,"course_deg":90},
    {"name":"survey","kind":"survey","distance_nm":120,"speed_kt":8,"course_deg":0},
    {"name":"home","kind":"transit","distance_nm":25,"speed_kt":7,"course_deg":270}]}'
```

## The mission clock

Every leg carries `start_hours`/`end_hours` from the mission start, always.
Supply `start_time` (ISO 8601, or a bare `HH:MM` dated today) and each leg also
gets `start_clock`/`end_clock` alongside ISO timestamps, and the plan gets a
`finish_clock`. **Displayed times carry the date and a `(+Nd)` suffix once they
roll over** — endurance at survey speed is over two days, so a bare `02:00` two
days out is a trap rather than a convenience.

`marks` are the mission's timed callouts, **in chronological order**. Each
carries elapsed hours, clock time, fuel burned by then, and what the gauge will
read. Two kinds, distinguished by `kind`:

| `kind` | `phase` | Where it sits |
|---|---|---|
| `phase` | `home_departure` | the first leg starts **making way** — after any launch hold |
| `range` | `outbound` | each radius, on the first leg |
| `phase` | `survey_arrival` | the start of the **first** survey leg, before its own hold |
| `phase` | `survey_departure` | the end of the **last** survey leg |
| `range` | `inbound` | each radius, on the last leg |
| `phase` | `home_arrival` | the end of the mission — always equal to `total_hours` |

Arrival to departure is time on task: the vehicle is on the survey area from
the moment it starts the first line until it leaves for good, so any
repositioning between patches sits **inside** that span rather than splitting
it. Subtract the two marks for time and fuel on task.

Distance from home is measured **along the planned track**: run made good on the
first leg, distance still to run on the last. The planner has no position model
— legs are distances and courses, never positions — so those are the only two
honest readings of it.

**Mission waypoints default to 13 km and 26 km**, each timed twice. `waypoints`
takes a list, a bare number, or a string like `"13, 26"`; repeats are
deduplicated and an absent or empty value falls back to the defaults rather than
meaning "no waypoints". Waypoints are independent — a transit that clears 13 km
but never reaches 26 gets the inner pair and a warning for the outer.

**`waypoint_unit` is `"km"` (default) or `"nm"`**, and it is a *display* choice:
it decides how the values you supply are read and how each mark is labelled, and
**it never moves a waypoint**. Omit the values and you get the same physical
radii either way — 13 and 26 km, which read as 7.019 and 14.039 NM. The UI
converts what you have typed when you change the selector, for the same reason.
Every mark carries both `km_from_home` and `nm_from_home` whatever unit was
chosen, plus `from_home` and `unit` in the chosen one, so two plans made in
different units can be compared without converting anything.

`home_marks_km` is the previous spelling, always km, and still works when
`waypoints` is absent. Supplying both is an error rather than a precedence
puzzle.

A leg shorter than the range mark produces a warning rather than a silently
missing row; a short return leg means the vehicle is already inside the radius
when it turns for home. A plan with no survey leg simply has no departure mark
— that is a legitimate mission shape, not a near-miss, so it warns about
nothing.

The clock is presentation only: a test asserts that adding a start time does not
move a single burn figure, margin or verdict.

## Tests

`tests/test_engine.py` includes a `TestMutationSensitivity` class that perturbs
each coefficient and asserts the answers actually move — a suite that passes
against a broken `model.json` would be worthless. The engine has also been
checked by deliberately breaking the survey-cancellation, the extrapolation
guard, the negative-fuel clamp and the runs-dry flag; each is caught.

## Limits worth stating plainly

- The fuel model comes from **one day of trials on one vehicle** (DriX-8,
  28 June 2024), nine fixed-RPM steps, of which seven survive into the fit.
- Every trial run was a **single heading**, so current is folded into the
  measured speeds. The four-heading test puts that at roughly ±7%.
- There is **no fuel measurement of any kind from 2022**, so the older
  configuration cannot be planned with.
- Alternator and payload load are **not** modelled separately. They sit inside
  the fitted idle term, at whatever level the trials happened to be carrying.
- The gauge **scale** is measured at 2.06 ± 0.11 L per indicated point, but
  **non-linearity is unresolved, not established** — the per-day figures
  (2.21 / 2.40 / 1.66) separate by only 1.5σ, each spanning 4–5 points at ±18%.
  An earlier version of this file called that spread non-linearity; that was an
  over-read and is retracted. A float sender in a non-prismatic tank has every
  reason to be non-linear, so the effect is unresolved rather than absent.
- **Every gauge reading is from the top third of the tank.** The 25% reserve
  band has no direct calibration at all, so treat it as a soft floor.

## Licence

MIT — see [`LICENSE`](LICENSE). The warranty disclaimer is not boilerplate here:
this is a planning aid whose sea-state response is an assumption and whose
reserve band has never been measured. Read "Limits worth stating plainly" above
before anyone plans a real mission on it.
