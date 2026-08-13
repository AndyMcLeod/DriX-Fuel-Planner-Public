# DriX mission fuel planner — START HERE

Mission fuel/endurance planner for the DriX-8. Set up three legs (transit out /
survey / transit home), each with its own sea state, wind and current; get
per-leg burn, a mission clock, and margin against the 25% return-to-port
reserve.

```bash
python server.py                          # UI on http://127.0.0.1:8765
python -m unittest discover -s tests      # 361 tests — must stay green
```

Stdlib only. No dependencies, no build step.

## The one rule

**`model.json` is the single source of truth for every coefficient.** Each
block is tagged `"fitted": true/false` so measurements are never confused with
assumptions. Do not hardcode numbers in `engine.py` or the UI; do not change a
coefficient without knowing which measurement or decision it traces to.

## Where things stand (2026-08-13, model.json v2.7.0)

Tree clean, both remotes pushed, **361 tests** green. Six days of MCAP data
(04–09 Aug) cached and adopted; no new bag days since. Nothing half-finished.

**Newest thing: MISSION GEOMETRY (2026-08-13).** A leg can now carry a
`track` (a polyline of waypoints) or a `pattern` (a survey lawnmower), and
`plan(env_at=...)` samples the environment PER RUN — per survey line, per
transit segment — at where and when the vehicle actually is. That is what
makes a tide real to the planner instead of one averaged number per leg.
**Both are optional and absent behaves exactly as before**, pinned by a test.
`model.json` is **v2.7.0**: one new block, `turn_model`, `fitted: false`.

**Before that: the per-leg currents can be read off the NOAA forecast** rather
than typed from a tide table — `currents.py`, the `Currents from forecast`
button, and `POST /api/currents`. **model.json did not move and neither did the
engine**: it fills two fields that already existed. See its section below,
particularly the part about what it does NOT fix.

**What the planner does, in one paragraph.** Three legs, each carrying its OWN
sea state, wind and current, plus an optional loiter hold. For every leg it
resolves that weather, converts the required SOG to a through-water speed
through the current vector, puts THAT through the gondola's speed law to get
RPM, adds the sea-state and heading premiums, reads fuel off the gondola's fuel
law, and judges the total against a reserve floor that is a **needle position,
not a number of litres**. Surveys are flown and costed line by line. Every plan
carries a mission clock, bracketed marks (departure → waypoints → survey →
waypoints → arrival), a sensitivity band, one `verdict` field every surface
renders, and a Markdown report written to `docs/missions/`.

**The numbers as they stand** (EM2040, 8 kt, full tank, reading A, 25% floor,
sea state 2 — the max-survey row is not a constant, it runs 398.5 NM in flat
calm and 329.3 NM at sea state 3):

| | |
|---|---|
| Mission fuel to the floor | **185.7 L** — 54.8 h, 439 NM |
| Same under the conservative reading (B) | 154.5 L — 45.6 h, 365 NM |
| Max survey, default 20/20 NM transits | **370.9 NM** |
| Tank volume (drawings, established) | 250 L |
| Gauge scale, measured 72–86% | 2.06 ± 0.11 L/point |

**The one thing that could still overturn planning:** the reserve band has never
been measured. Everything below 68% indicated is inference. See open thread 1 —
it is the single highest-value measurement outstanding and `tools/reserve_band.py`
sizes it.

### TWO REPOSITORIES — read this before pushing

- **`AndyMcLeod/DriX-Fuel-Planner` is private and canonical.** All history.
- **`AndyMcLeod/DriX-Fuel-Planner-Public` is PUBLIC, MIT.** Built as a fresh
  single-commit tree because ~38 pre-scrub commits here carry client
  identifiers permanently.

**`python tools/make_public.py <dest>` is the only supported way to build the
public copy**, and it reads `git archive HEAD` — **commit first, it cannot see
the working tree.** Every public/private difference lives in that script or it
reverts on the next export. It refuses to finish if a client identifier
survives; that sweep has already stopped two accidental republications.

### What changed 2026-08-11/12 — the current shape of the thing

Read the per-topic sections below for detail; this is the orientation.

1. **Weather is PER LEG** — sea state, wind speed/direction, current
   speed/set, each optional (`None` = fall back to the mission `Environment`;
   `0` is a real value). The Environment card is gone from the UI, which sends
   `environment: {}`.
2. **Current is kinematic**, not an empirical premium: `required_stw_kt()`
   converts SOG to through-water speed per line. It moves the fuel, never the
   clock. Partly double-counts against the SOG-fitted speed law — the leg note
   says so.
3. **Loiter holds** per leg, at the measured 0.95 L/h idle burn, taken at the
   **START** of the leg so a hold on the way home arrives home late.
4. **Marks bracket the mission**: `home_departure` … `home_arrival`, and
   `home_arrival.elapsed_hours == total_hours` is a pinned identity.
5. **Mission waypoints** (renamed from home marks) in **km or NM** — the unit
   is a display choice that never moves a waypoint.
6. **Max survey fills in the line count**, and the field stays editable.
7. **All documents live in `docs/`**; `README.md` and `CLAUDE.md` stay at root.
   **Every plan writes a report to `docs/missions/`** (gitignored).
8. **A quick-start help panel** renders `docs/QUICKSTART.md` itself — one
   source, no drift.
9. **A 28-finding adversarial review** landed on 2026-08-12; every finding is
   fixed and mutation-checked. See its section below before assuming a
   defensive-looking guard is redundant.

### House rules this project runs on

- **Commit then push, docs in the SAME commit**, then export to the public repo.
- **Mutation-test every new guard.** A test that has not been shown to fail is
  not evidence. Use a sidecar + atomic write + verified restore; a killed runner
  has left real source mutated here before.
- **Restart the server after editing `server.py`** — Python does not reload,
  while the UI files beside it do, which makes the staleness confusing.
- **Verify rendered output by LOOKING at it.** Rasterise documents, screenshot
  the UI, extract PDF text with PyMuPDF (a byte grep of a PDF proves nothing).
  Three separate defects this session were invisible to every structural check.
- **The Browser pane cannot screenshot while hidden.** Headless Chrome against
  the running app is the working fallback (`--headless=new --screenshot`).

### How the gauge reading was settled — the history worth keeping

The drawings put the tank at **250 L**, every hull, every mission. But three
independent measurements of the gauge SCALE all land near 2.06 L per indicated
point: MCAP metered 2.055 (72–86%), 2022 telemetry 2.049 (54.5–82.5%), and the
DD2024 refuel — *pumped litres* — 2.090 (9–97.5%). All three are **slopes**, so
partial fills and partial drawdowns cannot explain them away: that moves a
window along the gauge without changing what a point is worth.

`100 × 2.06 = 206 L` against 250 L leaves **~44 L of the tank not represented in
the indicated range**, and exactly two readings fit:

- **(A)** the gauge spans the tank and is non-linear, so the 86 unmeasured
  points must average **2.57 L/pt**, +25% on the measured band;
- **(B)** the gauge covers only ~206 L and is linear, the other 44 L sitting
  outside its travel.

**Andy adopted (A).** `gauge_profile.reading = "A"`. The gauge is a **profile**,
not a scalar: `GaugeProfile` holds segments `[(0,72,2.5716), (72,86,2.06),
(86,100,2.5716)]`, **derived** from `tank_volume` + `band_pct` + `l_per_point`
so they cannot drift from those. Mission fuel and the predicted needle are
integrals over it, and a burn crossing a band boundary is split at it. Reading
(B) is the same code path with one segment — set `reading` to `"B"`. At the
current floor the two differ by **31 L, 9.2 h and 74 NM**; burning exactly the
mission fuel lands the needle exactly on the floor, which a test pins.

**Know what adopting (A) did to the dependency structure.** A +20% error in the
measured gauge scale now moves mission fuel by about **1 L**, because the
profile re-normalises to the drawing volume — while a +20% error in the tank
volume moves it by tens of litres. Planning rests on the **drawings** now, not
on the six days of MCAP calibration. A test asserts exactly this, because it is
the least obvious consequence and the easiest to forget.

**It is an inference, not a measurement**, and it moved planning in the unsafe
direction. It is the one adopted value in `model.json` a drawdown could still
overturn: the two readings predict burns 25% apart over a span that resolves to
1.6%. The SHAPE outside the measured band is unconstrained — uniform is used
because it is neutral — and shape is worth about ±6 L where the choice of
reading is worth 31 L.

**Standing decisions of Andy's, so they are not quietly re-litigated:**
- Tank volume 250 L from the drawings — established, not editable.
- Planner capacity default stays 250 L nominal; the evidence-based capacities
  are scenario rows beside it.
- Endurance-sheet `PCT/HR` stays on the 250 L basis; the footnote carries the
  measured scale and the understatement instead.
- The needle decides the headline verdict, not the capacity row.
- Reserve floor 25%.

**Open threads, in rough order of value:**

1. **One controlled drawdown into the reserve band, flow meter logging.** The
   single highest-value measurement outstanding — the 25% reserve band has no
   calibration at all (see the gauge report), and the gauge-denominated reserve makes that gap the
   binding constraint on every plan rather than a footnote. A tank sounding
   would settle capacity alongside it.

   `python tools/reserve_band.py` sizes the gap and specs the experiment, and
   **re-answers itself when the data lands** — the lowest observed level is read
   from the caches, so the first day that dips below the calibrated band flips
   it from "NO DRAWDOWN DATA" to a calibration with a sigma against the top
   band. As of 2026-08-09 at the 25% floor: lowest level ever logged **68%**, so
   **43 points** below the lowest datum and **86 of 100** never measured. The
   full run costs ~111 L and 33 h at survey speed for 1.6% on litres per point;
   half of it ~55 L and 16 h for 3.3%. Under reading (A) the exposure is a SHAPE
   question worth ±6 L, not a level one — see "The drawdown numbers live in
   tools/drawdown.py". Always run the tool for current figures rather than
   trusting any number written here.
2. **Rough-water cruise data.** The sea-state calm anchor is measured; the slope
   into rough water is not. A fixed-RPM leg flown in calm and again in a seaway,
   same heading, is the clean experiment.
3. **Weather sensor is still not bridged** into the connectivity-box recording —
   checked through 08-09. `extract_bags.py` announces it the moment it appears.
   Until then the wind model stays an assumption.
4. **No cruise data below 1400 rpm (~5.3 kt)**; a few steady runs at 1100–1400
   would close the gap between the fitted floor and the idle point.
5. **Speed law is SOG-based** — reciprocal-heading pairs would strip the tide
   out. This matters more now that current is modelled explicitly: the two
   partly double-count, and only a reciprocal-pair refit separates them.
6. **Offered but not done:** importing the endurance sheet into the live Hourly
   Ops Log Google Sheet. Needs Andy's go-ahead; it touches a live ops document.
6b. **Offered but not done:** a mission-name box in the UI. The report filename
   already takes an optional `title` from the request body and slugs it, but
   the UI never sends one, so app-generated reports are timestamp-only.
7. ~~ROS 2 topic reference lagging.~~ **Rebuilt and baked 2026-08-09** — it had
   still been carrying the retracted "~2.30 L per point, non-linear" claim.
   That was the SIXTH place that retraction was found and the first outside
   version control: the generator was fixed months earlier, but the document
   is unversioned so nothing flagged it. **Documents built into unversioned
   locations must be checked by hand — no diff, no test and no commit hook
   will do it for you.**
8. ~~Migrate the two older builders onto `docx_style`.~~ **Done 2026-08-09** —
   all four builders now share it, and all three Word documents came out
   byte-identical, so none was regenerated. See "Document builders" below.

## Model detail (v2.6.0)

- **Two gondolas, both measured.** `gondolas.em2040` (currently fitted) is
  refitted from **6 days** of MCAP logs (04–09 Aug 2026, 4.85 h steady cruise):
  fuel quadratic `L/h = 2.9364 − 0.0029704·RPM + 1.4581e-6·RPM²` (R² 0.9995,
  valid 1400–3100 rpm) and speed `kt = 0.4861 + 0.0034479·RPM` (SOG-based,
  ±5% tidal). `gondolas.em712` carries the 2024 trials trial laws (fuel valid
  1020–2500 rpm). At 8 kt: EM2040 2.36 NM/L (interpolation) vs EM712 1.70
  (extrapolated). **The 2 extra days moved efficiency ≤1% anywhere in 5–10 kt** —
  the curve is confirmed, not merely fitted.
- **Loiter figure:** 0.95 L/h at ~1005 rpm (20.8 h observed across 4 days; day
  medians span 0.85–1.05) — use below the 1400 rpm fuel-law floor.
- **Gauge — read this carefully, an earlier note here was wrong.**
  **Established:** the gauge scale is **2.06 ± 0.11 L per indicated point** over
  the 72–86% band — 4σ below the 2.50 a linear 250 L tank gives, and agreeing
  with the independent DD2024 refuel figure (2.09) to 0.3σ. That is the GAUGE
  SPAN — 100 points × 2.06 = 206 L — which is NOT the tank volume; the
  drawings put that at 250 L. Keeping the two quantities apart is the whole
  of §6 in the report.
  **Not established:** non-linearity. The per-day figures (2.21 / 2.40 / 1.66)
  look like a trend but separate by only **1.5σ** — each day spans just 4–5
  indicated points, so each carries ±18%. A previous version of this file called
  that spread "the non-linearity, not noise"; that was over-read. The effect is
  *unresolved*, not absent — a float sender in a non-prismatic tank has every
  reason to be non-linear, it is simply smaller than this data can see.
  **Never forget:** every measurement is from the top third of the tank. The 25%
  reserve band has **no direct calibration at all**.
  Full treatment: `DriX8_Fuel_Gauge_Linearity.docx`.
  **The planner consumes this** via `Model.gauge_profile` and the `gauge_*`
  fields on every `PlanResult` — see the engine docstring's third honesty rail.
  Capacity and gauge scale are *not* independent: assuming 250 L over 100
  points asserts 2.50 L/point, which this measurement rules out at 4σ. That
  gap is the ~44 L, and reading (A) resolves it by making the unmeasured bands
  richer. **Mission fuel is 185.7 L under (A) and 154.5 L under (B)** — the two
  readings do NOT agree, which is why the drawdown still matters.
- **Defaults:** UI defaults to EM2040 (the fitted gondola); the engine API
  defaults to `em712` for backward compatibility — pass
  `Vessel(gondola='em2040')` from code.

## Drivetrain facts (Andy, 2026-08-07) — these constrain the models

- **Direct drive: shaft rpm always equals engine rpm.** The only exception was
  the trawling motor, which is no longer used. So `thruster_rpm` IS engine rpm,
  and 0 rpm means the engine is stopped — 0 L/h exactly.
- **The engine idles at ~1000 rpm**, burning ≈0.95 L/h. The "loiter" figure IS
  the idle burn; they are the same measurement, not two.
- **Minimum engagement jumps to ~1100 rpm.** The 0–1000 rpm band is not an
  operating region — a curve may pass through it, but the vehicle never sits
  there.

Consequence: ops-facing endurance tables should use laws constrained through
the origin (see `tools/build_endurance_sheet.py`). The planner deliberately
keeps the *unconstrained* in-band laws in `model.json` — they are more accurate
where missions are actually flown, and the two agree to ≤3.7% in-window and
0.3% at 8 kt.

## Ops deliverable: the Endurance Data sheet

`DriX8_Endurance_EM2040.xlsx` / `.csv` live at the repo root with the other
documents. `tools/build_endurance_sheet.py` regenerates **both** in place (pass
a path to write elsewhere) — it is the **Endurance Data** tab for the Hourly Ops
Log Google Sheet (`Hourly Ops Log`), replacing the older
EM712 values. It matches that tab's layout — RPM | SOG-M/S | SOG-KNOTS | LPH |
PCT/HR | NM/LITER, the nominal-values footnote, the M/S-per-knot cell — and adds
a `Tank L` input cell plus a whole-knot companion table (4–11 kt) that drives
the chart so bars land on integer knots. The `.csv` is the values-only fallback
for imports that mangle the xlsx; it carries no chart.

It uses the **through-origin** law variants per the drivetrain facts above,
refitted in-script from `em2040_fit_2026-08-09.json` plus the idle anchor.
Import to Sheets via File → Import → Upload → *Insert new sheet(s)*.

## Weather belongs to the LEG, not the mission (2026-08-11)

Andy: mission durations make a single wind and current wrong. `Leg` now carries
optional `wind_speed_kt`, `wind_from_deg`, `current_speed_kt`, `current_set_deg`,
and the UI puts all four on each of the three leg cards. A mission runs two days
at survey speed — the wind on the bow going out is not the wind coming home, and
the tide turns twice in between.

**`None` means "use the mission Environment"; 0 means becalmed.** They are
different, and conflating them would silently give a calm leg the mission's
20 kt. `Leg.environment(env)` resolves each field independently, so setting a
speed alone keeps the mission's direction rather than resetting it to north —
which is what an operator changing strength expects. A mutation using
truthiness instead of `is None` is killed.

**Resolved ONCE at the top of `plan_leg`**, by rebinding `env`, so every path
below — premiums, current, notes — sees the same thing and none can accidentally
read the mission-wide value. `LegResult` reports the resolved four, so a plan can
be read back without the `Leg`s that produced it.

**Sea state is per-leg too**, added straight after (Andy). It was held back on
the grounds that the premium rests on a single measured anchor — **that argument
was wrong and is retracted**. It is about how far the DIAL can be trusted, not
about whether it should be one number for two days; a survey flown into a
building sea is an ordinary mission, and averaging it away helped nobody. `0` is
a real WMO code, so the `is None` test matters here more than anywhere: reading
it as "unset" would give a glassy leg the mission's sea 5.

**With sea state gone from it, the Environment card had nothing left and was
removed.** Each leg's weather row carries its own sea-state select with the
premium it implies beside it, so the cost of a building sea is visible before
planning rather than only in the results table. `environment` still exists on
the API as the fallback for a caller that omits per-leg values; the UI now sends
`{}`.

**Absent means old behaviour**, exactly: a plan with no per-leg weather
reproduces the mission-wide answer, and a plan setting every leg to the mission's
values reproduces it to nine places.

What it buys, measured live: 25 kt on the nose and 2 kt of foul tide outbound
against 5 kt astern and the same tide fair coming home turns a symmetric
25 NM/25 NM transit pair into **31.8 L out and 5.7 L home** — and flags both, one
above the fitted RPM window and one below it.

**In the UI** the Environment card is GONE — sea state followed the wind and
current onto the legs the same day (the paragraph here that said the card "keeps
only sea state" outlived that by hours; review caught it). The rose draws one
arrow per DISTINCT vector, tagged with the legs sharing it, and collapses back to
the old single pair when all three agree. Each leg line is coloured by ITS OWN
wind. The `Current` tile reports one value only when every leg agrees, otherwise
the range and "varies by leg" — averaging three forecasts into one figure is
exactly the quiet blending this planner exists to avoid. Disagreement is judged
**per family** (`windsVary`/`currentsVary`), never as an aggregate count: the
first version's `winds + currents > 2` let exactly two winds pass untagged with
one leg's figure presented at the centre as the mission's. And a slack current
carries no direction, so the tile keys 0 kt as `slack` whatever the set box
says — both found in review.

**The arrow tags had to join the label nudger.** With three forecasts a wind tag
and a current tag can land on the same bearing, and they did. `.wx-label` now
goes through `nudgeRoseLabels()` after the leg labels — leg names get first claim
on their own bearing — and only leg labels get a leader, because a weather tag
names its own legs. Verified by sweeping 2592 course-and-weather combinations:
zero overlaps.

## Mission geometry: tracklines and survey patterns (2026-08-13)

Andy: *"we need to input a trackline for each transit and a survey line pattern
for the survey itself... this will provide fixed time and position to overlay
current data."* Right, and it dissolves the limit rather than patching it.

**The problem it fixes.** A leg was a distance, a course and a speed, which
cannot say where the vehicle is at 14:20 — so a time-varying field could only
ever be applied as ONE number per leg. Measured on the real forecast at
Delaware Bay Entrance, a survey held on one ground costs **+5.0% to +9.4%**
more than its own vector mean; and worse than the litres, a 16 h survey's
leg-mean current reads **0.16 kt**, which any operator reads as slack, while
the water runs to 2.2 kt and reverses under them. The mean of a reversing tide
is not the tide.

**What was added.**

- `geometry.py` — stdlib, pure. `Point`, rhumb `move`/`distance_nm`/
  `course_deg`, `Run` (a straight piece that knows its endpoints and can
  `split()` finer), `SurveyPattern` (anchor, bearing, length, spacing, count →
  real lines, alternating, each starting where the last finished) and
  `TurnModel`.
- `Leg.track` and `Leg.pattern`, both OPTIONAL. Present, the leg's distance
  and course are DERIVED from the geometry; absent, every number is what it
  always was. `geometry_runs()` is the ONE seam that reads them.
- `plan(env_at=...)` / `plan_leg(env_at=..., start_hours=...)`: the engine
  calls `env_at(lat, lon, hours_from_mission_start)` per run. **It is
  INJECTED, never fetched** — that is what keeps the engine pure computation
  with no I/O, testable against a field made of arithmetic.
- `currents.env_factory(base_env, departure)` supplies one backed by the cache.

**Runs are split to 1 NM by default** (`max_run_nm`) — 7.5 minutes at 8 kt,
finer than the forecast's hourly ~500 m resolution and no finer than the source
supports. Splitting never changes the distance flown; a test pins it.

**The field may decline to answer.** Past the end of the forecast, or over a
shoal the model calls land, `env_at` returns None and the engine falls back to
the leg's own current — a plan completes on the operator's typed number rather
than failing or silently planning slack water. `env_at.covered / .asked` say
how much of the mission it actually answered for.

**Turns are modelled now, and they are an ASSUMPTION.** `model.json`
`turn_model` (`fitted: false`, radius 0.0135 NM ≈ 25 m). Where spacing ≥ 2r a
half-circle fits; tighter than that the vehicle runs out and back — the omega
turn — and `TurnModel.path_nm` charges the longer path. **This makes surveys
cost more than they used to**, because turn time and fuel were absent
altogether before. That is a correction, not a regression, and it is
measurable: the MCAP days carry position and INS heading, so a real rate of
turn is extractable from a recorded line change. On the known-gaps list.

**A turn's distance travelled and distance made good are different things**,
and fuel follows the former. The turn Run carries the arc length while
displacing the vehicle only from one line's end to the next line's start.

**What bit here.** Two of my tests over-specified and one guard was thin:
`turn_angle(0, 180)` is ±180 and the SIGN at an exact reversal is arbitrary, so
only the magnitude can be asserted; the lawnmower spacing identity holds to
about 0.2 m rather than machine precision, because rhumb steps composed in
different orders differ under the flat-earth approximation; and `Run.split`
needed an epsilon, since a 20 NM run comes back as 20.000000000000004 and
`ceil` then asked for 21 pieces, the last a few microns long.

### The importer, and why THAT is the scope (2026-08-13)

`lineplan.py` reads CSV/TXT, GeoJSON, KML, KMZ, GPX and Hypack LNW. The scope
was chosen on one test — **can the file be read without guessing?** A parser
that half-works is worse than none, because a plan that loads cleanly a mile
off looks exactly like one that loaded correctly.

Refused deliberately, each with a reason rather than a shrug: **shapefile**
(geometry is trivial, but the CRS is WKT in a sidecar `.prj` and guessing a
datum from partial WKT is the whole failure class), **UKOOA P1/90 and SEG-P1**
(fixed-column, where a one-character offset still parses into plausible
positions — needs a real sample to pin against), and **QINSy / NaviPac / PDS
databases** (proprietary containers; all of them export to something on the
list). If Andy supplies a sample of any of these, adding it is small.

**The CRS is what actually bites, not the format.** Geographic degrees are read
as they come, decimal or DMS, and a HEMISPHERE LETTER BEATS A SIGN — `-75.5 W`
is west, because someone writing both means west twice and a double negative
would put it in China. Projected coordinates are UTM/WGS84 only and only with
the zone given; the zone is NEVER guessed. WGS84 is assumed (NAD83 differs by
1–2 m here, two orders under the forecast mesh).

**What the live run caught, and it is a good one.** The import summary reported
`mean bearing 110°T` for a survey running 020/200 — the arithmetic mean of
alternating reciprocals is the one direction the vessel never steers, square
across the lines — and it was being written straight into the survey course
box. `describe()` now reports the **line AXIS** (circular mean of the doubled
angles, halved, 0–180) plus the first line's actual heading, and the UI takes
the first bearing. Two things had to go wrong together for it to be invisible:
a plausible number, and no picture to check it against.

Also from that run: `csv.Sniffer` gives up on short files and the fallback
split each row on whitespace into ONE token, so every row was skipped for
having no numbers and the file reported as holding no coordinates; and the
header matcher did not recognise `lat1`/`lon1`/`lat2`/`lon2`, which is exactly
how endpoint-per-row files are written, so they fell through to the positional
path and became one-point lines.

### The geometry UI (2026-08-13)

A **Mission geometry** card, all of it optional: import a line plan, type
transit waypoints (lat, lon per line, monospaced so a transposed digit shows),
or give a survey anchor/bearing/spacing. Line count and length come from the
survey leg, so there is ONE place to change them.

**Imported lines beat a generated pattern** — they are what the surveyor drew.
They are held in a JS variable, never round-tripped through a textarea: a real
plan is hundreds of vertices and a half-edit that parses is not what was drawn.
Importing to a transit REFUSES a multi-line file rather than flying the first
of them.

**"Read currents along the track"** sends `use_forecast_currents`, and
`/api/plan` builds the field via `env_factory` when the legs carry geometry.
The response carries `currents_field` with `asked`/`covered` so a surface can
say how much of the mission the forecast answered for. Verified live from the
browser: a 12-line KML imported, 41 runs, 41 covered, 28.19 L against the field
against 27.65 L without.

## Currents from the NOAA forecast (2026-08-13)

Andy asked for surface currents off the OFS map-plot animation, then for them
wired into the planner. `currents.py` (repo root, **stdlib only**, so the app
takes no new dependency) reads them; the button on the Mission clock card fills
the per-leg boxes; `POST /api/currents` is the seam.

**The model did not change and neither did the engine.** This supplies two
inputs that have existed since 2026-08-11. That is the whole design: no new
coefficient, no new physics, nothing in `model.json`, and the planner behaves
exactly as before for anyone who never presses the button.

### What it reads, and the one decision that mattered

The page Andy pointed at draws **rendered PNGs**, one per hour — there are no
numbers in them. The model output behind them is served over OPeNDAP, and NOAA
publishes each OFS twice:

- **`fields`** — the native ROMS output. Curvilinear grid, velocities in GRID
  axes on staggered u/v points. Using it means averaging u and v onto rho
  points and rotating every cell by `angle` before a bearing means anything.
- **`regulargrid`** — NOAA's own regrid. Rectilinear 0.005° mesh,
  `u_eastward`/`v_northward` already true-referenced, land mask included.

**This reads `regulargrid`**, because the two operations the native product
needs are exactly the two that fail silently: get the rotation wrong and every
set is out by tens of degrees while every speed still looks reasonable.
`python currents.py crosscheck` does the native path BY HAND and compares —
**median 0.07 kt and 1.0° apart** — so the shortcut is evidence, not assumption.
Keep that subcommand. It is the only thing standing behind the choice.

Domain 37.79–40.22 N, 75.89–73.25 W; six nowcast hours plus 48 forecast, so a
**54-hour span**; ~2 MB per hour over the wire, 33 MB gzipped for the whole box,
about half a minute to fetch. Cache in `ofs_cache/` (gitignored).

### How it was verified — three independent ways, because direction fails quietly

1. **Native ROMS crosscheck**, above.
2. **NOAA's own picture.** `tools/dbofs_plotcheck.py` redraws the extracted
   field onto the published PNG in NOAA's colour bins. It **derives the
   georeference from the plot's own graticule** — finding the dotted lines by
   their neutral grey, since the arrows are saturated — and refuses to draw if
   the fit residual exceeds 2 px or the derived box does not contain the model
   domain. The plot frame is NOT the model box: it is padded, 37.60–40.22 N,
   76.02–72.98 W.
3. **CO-OPS harmonic predictions** at DEB0002, a wholly separate product:
   **correlation +0.987 over 54 h, RMS 0.30 kt**, peak 2.25 kt against 2.28,
   slack water within half an hour. `python currents.py station` re-runs it.
   They will never match exactly — harmonics are astronomical tide only, the
   station bin is feet down, and the prediction is rectilinear where the model
   carries a vector. That is the point of reading a model.

### What bit, and would bite again

- **A mirrored latitude axis fits a straight line perfectly.** Pairing ascending
  pixel rows with ascending latitudes gave sub-pixel residuals AND passed the
  aspect-ratio check, with the whole map upside down. Only an explicit sign
  assertion catches it, and that assertion is now in `georeference()`. Do not
  "simplify" it away.
- **`cache=CACHE` as a default argument binds at import.** A test that pointed
  the cache at a temp directory silently read the operator's real 33 MB cycle
  instead — it passed for the wrong reason and ran three times slower. Every
  cache argument now resolves `cache or CACHE` inside the call.
- **A uniform test field cannot catch a bank-averaging bug.** The land-mask test
  passed against a mutant that averaged land straight into the channel, because
  every node held the same value. Land now carries a different value from water
  in that fixture, and the test asserts the number a broken version would give.
- **`datetime-local` is wall time with no zone.** Sending it raw to a forecast
  indexed in UTC is four hours out in EDT — most of the way from slack to peak
  flood at the mouth of the bay. **The browser converts** (`toISOString()`),
  because only it knows the operator's offset. Verified live against a machine
  in `America/New_York`: local 14:00 went out as 18:00Z.

Eight mutations were run against the guards, all caught, source verified
unchanged afterwards.

### Positions are displayed with a hemisphere (Andy, 2026-08-13)

`075.1394 W`, never `-75.1394 E`. A signed number under an "E" heading reads as
east to anyone scanning it, with the minus the only thing saying otherwise —
and this whole domain is west. `fmt_lat` / `fmt_lon` / `fmt_span` in
`currents.py` are the one implementation; longitude is padded to three degrees
the way charts write it, which also tells the two apart at a glance. A span
carries a hemisphere on EACH end so a box straddling the meridian cannot read
as one signed range.

**Only the display changed.** The input boxes, the JSON API, the CSV exports
and the cache metadata are all still **signed decimal degrees** — that is what
every consumer parses, and a test asserts it stays that way. The UI echoes the
typed position back formatted, which is where a dropped minus becomes visible:
`-75.1394` reads `075.1394 W`, and `75.1394` reads `075.1394 E`, the wrong
ocean. `app.js` has a second implementation of the same convention (it cannot
import Python), and a test pins the two together on the padding widths, which
is the part that would silently drift.

### The rules it follows

- **Missing is never zero.** A leg the forecast cannot see is left EMPTY — in
  the response, in the UI box, everywhere. No data and slack water are different
  answers and only one of them belongs in a plan. Three tests and a mutation
  pin this.
- **A survey does not walk down its first line.** A lawnmower ends roughly where
  it began, so a survey leg holds position and only advances the clock. Walking
  it `lines × line_length` down `course_deg` would put the run home tens of
  miles into the wrong water. Mutation-checked.
- **The loiter is taken at the start**, matching the engine, so a hold changes
  which way the tide is running for everything after it.
- **The forecast is perishable, so the report names the cycle.** And the label
  is dropped the instant a current box is edited by hand — the report must never
  attribute an operator's own number to a NOAA run.
- **Offline stays first-class.** This is the ONLY endpoint that reaches the
  network. A failure is a message an operator can act on, never a hang, and
  every box it fills can be typed by hand.

### The reading state is loud (Andy, 2026-08-13)

"More visual indication of reading current forecast. maybe bold and flashing
red." So `setCurrentsNote` gained a third kind, `busy`, and the note goes **bold
700 and blinks in `var(--bad)`** while the fetch is in flight — the only
animated thing in the app, for the only state where an operator waits on a
network the boat may not have.

- **It borrows the FAILURE colour, and the blink is what tells them apart.** A
  red note sitting still has failed; one that pulses is still working. Flagged
  to Andy as the cost of "red" — amber or the accent would have kept red meaning
  "this didn't work" — and he took it. The weight goes past `.bad`'s 600 for the
  same separation.
- **`busy` is TOGGLED like `warn` and `bad`, never added.** Every exit path
  calls `setCurrentsNote` again — success, partial, failure, hand edit — so the
  flash is cleared by the note that replaces it and no path has to know it
  exists. A one-way `add` would leave a flashing red note on a finished read;
  that is a test and a mutation.
- **~0.9 Hz, and there is a test that keeps it there.** Past 3 Hz a flash is a
  seizure risk for photosensitive readers (WCAG 2.3.1), and the obvious way to
  make a warning feel more urgent is to speed it up. `test_the_flash_stays_under_
  three_a_second` parses the period out of the CSS and fails under a third of a
  second. **If it reads too sleepy, raise the contrast between the two phases,
  not the rate.**
- **`prefers-reduced-motion` drops the pulse and keeps the bold red**, and the
  note carries `aria-live="assertive"` while busy so the state reaches a reader
  who cannot see a colour change at all.

**Verifying it needed the animation's own clock.** The Browser pane cannot
composite while hidden, so a live sample of the computed opacity reads a frozen
`1` forever and the flash looks broken when it is fine — `document.visibilityState`
is `hidden` and Chrome has paused the animation. Setting `currentTime` by hand
through `getAnimations()[0]` proved the square wave (opacity 1 for 550 ms, 0.3
for 550 ms) without needing a visible tab. The still frame was taken by pointing
headless Chrome at a scratch page that loads THIS server's `styles.css`, with
each sample carrying `id="currentsOut"` — the rules are id-scoped, so a preview
using any other id renders unstyled and proves nothing.

### What it does NOT fix

The speed law is still SOG-fitted in an unrecorded tide, and the ±6.91% heading
premium still mixes wind, sea and current. **A real forecast current is still
partly counted twice** — the leg note continues to say so. Reading the tide off
DBOFS makes the input better; it does not make the correction clean, and it is
not a substitute for the reciprocal-heading pairs that would strip the tide out
of the fit itself (see "Known gaps").

## Every control explains itself: the hover tips (2026-08-13)

Andy: "Add floating tooltips to all components." All **73** controls now carry
an explanation, shown in ONE floating layer (`#uiTip`) anchored to the CONTROL
— below its left edge, flipped above at the viewport bottom, clamped either
way — never to the pointer.

**Ported from the ASV console's `1035ef7`, deliberately**, and for the reason
that mechanism exists there: a native `title` tooltip is drawn by the BROWSER,
which on this platform parks it under the cursor, and its position is not the
page's to move. On hover the title is stashed off the element — which is what
suppresses the native tip — and restored on the way out.

Two deliberate divergences from the sibling, both worth keeping:

- **The copy lives in `TIPS` in `app.js`, not as `title=` in the markup.** The
  sibling already had 118 hand-written titles and only needed re-positioning;
  this page had **none**, so the copy was new, and one reviewable block beats it
  scattered through 460 lines of form markup. `applyTips()` writes it onto the
  elements at boot, after which the DOM matches the sibling's exactly and the
  layer is the same code. Keys are CSS selectors, so grouped rows
  (`'#outSea, #surSea, #homeSea'`) say a thing once and the three loiter buttons
  per leg are reached by `[data-loiter-add]` without inventing ids.
- **Focus shows a tip, not just hover.** This is a form — 39 inputs, 11 selects
  — and a keyboard operator would otherwise be the only one who never sees an
  explanation. `focusin`/`focusout`, which bubble where `focus` does not.

**`applyTips` never overwrites a title already present**, and `hideTip` restores
the stash only if the attribute is still absent — a readout rewritten mid-hover
has to win. That second rule is inherited from the sibling, which learned it
with five runtime-written readouts.

**The check that enforces the ask is `test_every_control_with_an_id_has_a_tip`.**
It reads every `input|select|button|textarea|output` out of `index.html` and
fails on any that `TIPS` does not cover — so a control added later cannot ship
unexplained, and "all components" cannot decay into "the ones that had a tip the
day it was written". Its mirror, `test_no_tip_points_at_a_control_that_does_not_exist`,
catches the other direction: a renamed id leaves a tip silently unapplied.
Both were mutation-checked, 12/12 caught. The coverage test earned its keep
immediately — it caught `helpClose` and `planTarget` missing on the first run.

`pointer-events: none` on the layer is load-bearing, not decoration: without it
the tip can land under the cursor, take the `mouseover`, and flicker itself
away.

## Current: set and drift (2026-08-11)

`Environment.current_speed_kt` / `current_set_deg`, beside the wind fields.

**CURRENT USES THE OPPOSITE CONVENTION TO WIND**, as at sea: a wind is named for
where it comes FROM, a current for where it SETS TOWARD. The field name says
which, the UI label reads "Current sets toward", and a mutation that reads set
as a from-direction is killed by four tests. This is the single easiest way to
plan a mission backwards.

**It is KINEMATICS, not an empirical premium, and it enters one step earlier
than the wind does.** The water velocity a hull must make is the ground velocity
minus the current:

    STW = |Vg − Vc| = sqrt(Vg² + Vc² − 2·Vg·Vc·cos(set − course))

so `required_stw_kt()` converts the leg's required SOG into the through-water
speed, and THAT is what goes through the speed law into RPM. Head current adds
its drift, following subtracts it, a beam current makes the hull crab and costs
a little either way.

**A current moves the fuel, never the clock.** Duration follows SOG because the
ground still has to be covered; fuel follows STW because that is what the hull
pushes against. A mutation dividing the distance by SOG+drift is killed.

**Resolved per LINE, not per leg.** A reciprocal pair does not see the same
water, and the two do not cancel — same convexity argument as the wind premium.
Measured live: with 2 kt setting 180 against a 000/180 transit pair, the head
leg goes 9.6 → 17.2 L while the following leg goes 9.6 → 5.7 L. The pair costs
22.9 L against 19.2 L in still water.

**Know what it double-counts.** The speed law is **SOG-based**, fitted in an
unrecorded tide (README: ±5% tidal), and the ±6.91% heading premium "mixes wind,
sea and current and cannot be decomposed". So an explicit current is partly
counted twice, and the leg note says exactly that rather than implying the
correction is clean. Dropping that sentence is a killed mutation. The honest
position: this is right for *comparing* plans and for asking "what does today's
tide cost me", and it is not a calibrated tidal model.

A following current can drop the required RPM **below the fuel law's fitted
floor** — 1348 rpm in the case above — and the existing extrapolation flag fires
on it unchanged. That is the rail working, not a defect. A current strong enough
to carry the hull (required STW near zero) gets its own note.

The compass rose draws the current as a **dashed amber arrow pointing where the
water goes**, beside the solid wind arrow pointing where the wind goes. Distinct
colour and dash precisely because the two conventions are opposite.

**A `Current` summary tile** reads `PlanResult.current_speed_kt` /
`current_set_deg` — the first environment fields the result has ever carried,
added so a surface reports **the conditions the numbers were computed under**
rather than whatever the form says now. Edit the inputs without replanning and
the tile keeps showing the planned current; a test pins that the plan carries
it, and it was checked live. The tile spells out "sets 285°T" rather than a bare
bearing, because current takes the opposite convention to wind and an unlabelled
bearing there would be exactly the trap the field naming exists to avoid. Shown
at zero as "0.0 kt / slack", same reasoning as the Loiter tile, and left
unstyled — a current is a condition, not a warning.

## docs/ holds every document; every plan writes a report (2026-08-12)

**All documents moved to `docs/`** — the four deliverables (8 files with their
PDFs/CSV), plus `QUICKSTART.md` and `MOVING.md`. Moved with `git mv`, so blame
follows. **`README.md` and `CLAUDE.md` deliberately stayed at the root**:
GitHub renders one there and Claude Code reads the other there.

Four things had to move with them, and a test now pins each: the four builders'
default `OUT` paths, the server's `/quickstart.md` route, `test_ui.py`'s doc
path, and `make_public.py`'s `MOVING.md` retarget. A builder left pointing at
the root would have written a stale duplicate beside a fresh one with nothing
noticing.

**Every successful `/api/plan` writes a Markdown mission report** to
`docs/missions/`, and the response carries `report: {written, path, error}` so
the UI can name the file. One per press of Plan mission, stamped to the second,
**never overwritten** — two plans a second apart are two missions and the
earlier is not scratch.

- **`mission_report.py` is PURE** — `render()` returns a string, `server.py`
  does the writing. Same rail as `engine.py`, and it is what lets the tests
  exercise the whole report without touching the operator's `docs/`.
- **Every figure comes off the `PlanResult`.** Nothing is recomputed: a report
  doing its own arithmetic could disagree with the plan it claims to describe.
- **A write failure never costs a plan.** `_write_report` catches `OSError`,
  returns the message in the payload, and the UI shows it — a silently missing
  report is worse than none.
- **`REPORT_DIR` is module-level and `None`-able** precisely so tests can point
  it at a temp dir; `test_mission_report.py` asserts the real `docs/missions`
  is untouched by the suite. That rail exists because this project has already
  had a harness write the operator's real files.
- **A title from the request body reaches the filename**, so it is slugged —
  `../../etc/passwd` cannot escape the directory, and a test tries.
- **`docs/missions/` is gitignored.** Generated operator output; the four
  documents beside it are the tracked ones.

`--no-reports` and `--report-dir PATH` are on `server.py`, and
`start_planner.bat` forwards them.

## The 2026-08-12 adversarial review — 28 confirmed findings, all fixed

A five-reviewer fan-out (engine / server / UI / tests / docs) with adversarial
verification of every claim: 28 confirmed, 0 refuted, all fixed the same day
and each fix mutation-checked. The ones worth knowing about when reading code:

- **`plan()` could CRASH**: the convexity note divided by a fuel rate the EM712
  law clamps to zero — any survey below ~5 kt in wind was a ZeroDivisionError
  served as a 500. Guarded; the clamp note is the message in that regime.
- **`max_survey_lines`' cap exit lied**: `hi > cap` returned `lines=cap,
  completes=True` without ever asking `fits(cap)` — a mission whose true max
  was 20 was told 30 fit, 52 L past the floor. The exit now verifies the cap
  and bisects honestly below it when it does not fit.
- **The wind-vs-sea sanity warning was dead** for the UI (it read the mission
  Environment, which the UI sends empty since weather went per-leg). Per-leg
  now, naming the odd legs.
- **`Environment.validate` never checked `wmo_sea_state`**, and the premium
  table's "above the table, hold the top value" fallback cannot tell above
  from below — a `-1` typo silently planned the whole mission at the TOP
  premium. Rejected now, mirroring `Leg.validate`.
- **Transport hygiene**: `json.loads` ADMITS `NaN`/`Infinity` literals and
  `NaN <= 0` is False, so a NaN speed passed validation and died at response
  serialization; a JSON-array body escaped `do_POST`'s except-net and dropped
  the connection with no response; `int(lines)` truncated 12.5 to 12 silently;
  both waypoint spellings at once silently preferred one where the engine's
  own rule is to refuse. All four fixed at the seam, with `tests/test_server.py`
  new — **the HTTP parser had no tests at all**.
- **`waypoints: []` now really means "no waypoints"**: `... or fallback` had
  quietly closed the documented escape hatch. The UI sends `null` for a blank
  box (defaults), `[]` on the wire means none.
- **The rose judged disagreement as an aggregate**: `winds + currents > 2` let
  exactly two winds pass untagged with one leg's figure at the centre as "the"
  wind. Per family now (`windsVary`/`currentsVary`). And the Current tile keys
  0 kt as `slack` whatever the set box holds, so an all-slack mission cannot
  read "varies by leg".
- **`server.py` carried the repo's THIRD hard-coded reserve floor**, unread by
  the agreement test. It asks the model now — and the test that pins this had
  its own lesson: the first version checked one line, and a mutant hardcoding
  0.25 one line higher SURVIVED as behaviourally equivalent *at today's policy
  value*. Equivalent-today is exactly the drift the guard exists for; the test
  now reads the whole parser source.
- **Doc drift**: README still quoted 211/175 L and 428/347 NM — 15%-floor
  figures retired three days ago; QUICKSTART described the removed Environment
  card; `tests/test_ui.py`'s leg regex matched the `.legs` CONTAINER as the
  first leg and produced the right answer by coincidence.

201 tests (was 183). The near-misses to carry forward: a solver's cap path had
no test because no test passed a cap, and the HTTP seam had none because the
suite only ever imported `engine`.

## Loiter moved to the START of its leg; home marks added (2026-08-12)

Andy's report: "when I add 4 hours to a straightforward loiter on the return to
home, I do not get 4 hours later arrival." Reproduced exactly — under the old
end-of-leg convention a hold on transit home was a hold AFTER arriving, so
`total_hours` moved and **not one mark did**. The fix and its consequences:

- **The hold now sits at the start of its leg.** One rule everywhere: a hold
  delays its own leg's crossings and everything after. Launch delay moves the
  outbound waypoints; a hold on the way home arrives home late.
- **`_mission_marks.add()` carries the hold** — `start_hours + loiter_hours +
  into_nm/speed`, and the burn line carries `loiter_litres` the same way.
  Dropping either is the reported bug restored; both mutations are killed.
- **`survey_arrival` is the one before-the-hold mark** (`after_hold=False`):
  the vehicle arrives, then waits. `home_departure` is the opposite — it names
  the vehicle leaving, so it is stamped after any launch hold, with the hold's
  fuel already burned.
- **`home_departure` and `home_arrival` bracket every mission** (transit-only
  ones included), and `home_arrival.elapsed_hours == total_hours` is an
  identity a test pins — it is what keeps the marks table and the "Back
  alongside" readout agreeing. Every marks consumer that counted 6 now counts 8.
- The rose hold-dot moved to the **inboard** end of the leg line (r=18) to
  match, clear of the centre text.

Five mutants killed; the reported scenario is a named test. Verified live:
4 h on transit home moves inbound 26 km T+20.14→24.14, inbound 13 km
T+21.14→25.14, Back alongside T+22.14→26.14.

## Loiter: delays imposed on a leg (2026-08-11)

`Leg.loiter_hours` — time held on station making no way, charged at the
gondola's **idle burn**. Andy's framing: things happen at sea that are
uncontrolled but can be accommodated in the model, then you replan. Each leg
card carries a loiter row (value + min/hours + `+15m` / `+1h` / `clear`), and
pressing **Plan mission** is the replan.

**It costs the measured idle rate, which was already in `model.json` and had
never been used.** `gondolas.options.em2040.loiter.lph = 0.95` — 20.8 h of
observed idle at ~1005 rpm. A 2 h hold is 1.90 L, verified live.

**Held at the START of its leg** — changed 2026-08-12 from the end, and the
change was a bug fix, not taste. Andy reported that 4 h of loiter on the return
leg did not arrive 4 h later, and he was right: "end of the leg" on transit
home meant holding AFTER arriving, so the finish clock moved and not one mark
did. At the start, every leg reads operationally — launch delay outbound, hold
before the first line on survey, offshore hold before running in — and one rule
falls out: **a hold delays its own leg's crossings and everything after**. The
mark times and burns carry the hold via `add()`'s offset; a mutation restoring
the old behaviour is killed by four tests, including one named for the report.

**Underway and held figures stay separate on `LegResult`.** `hours`, `litres`,
`fuel_rate_lph` and `nm_per_l` remain **underway** quantities, so
`litres == fuel_rate_lph * hours` still holds and `nm_per_l` still describes
what the gondola does on a litre. The leg's real cost is `total_litres` and its
real duration is `end_hours - start_hours`. Blending them would have quietly
turned the leg table into a mixture of a rate and a hold.

**A gondola with no measured idle burn does not borrow one.** The EM712 has no
`loiter` block, so its rate is read off **its own** fuel law at idle rpm and
flagged as an EXTRAPOLATION — that rpm is below the window the law was fitted
over. Borrowing the EM2040's 0.95 would mix two drag states, which is the whole
reason the planner carries two gondolas. A mutation that borrows it is killed.

**The idle figure is calm-water and the note says so.** Station-keeping in a
seaway has never been measured, so the hold carries **no sea-state premium** —
stated in the leg note rather than silently assumed away. Dropping that sentence
is a killed mutation.

**On the compass rose**, a held leg gets an amber dot at the **inboard end of
its own course line** — the hold sits at the start of the leg since the
2026-08-12 fix, and the dot moved with it — and the duration rides that leg's
label in amber (`out 45m`, `survey 2h30`). Compact form via `hmShort()`; the
tile and the notes keep the long one.

**A hold is deliberately NOT drawn as a direction.** It has no bearing, and an
arrow would be inventing geometry the delay does not have. The rose reads the
FORM, not the plan — unlike the tiles — because it is a live picture of the
inputs and redraws as you type, switch units or press a quick-add.

**The duration rides the label instead of getting its own text**, and that was
measured rather than chosen: a separate text one ring inboard collided with the
label it belonged to on any east-west leg.

**Leg labels are separated by `nudgeRoseLabels()`, which runs AFTER the SVG is
in the document and measures what the browser actually drew.** Two legs on a
similar course otherwise put their labels in the same place — a survey running
near a transit, which is ordinary rather than pathological. Each label is nudged
in y (alternating down/up so it stays near its own bearing) until it clears
every box already placed, including the compass points and the wind/current
readouts; anything displaced gets a dashed leader back to its own leg line. The
amber hold dot stays at the line END regardless, because it marks where the hold
happens, not where its caption fits.

**Three attempts, and only the third works — do not "simplify" it back:**

1. *Radial rings.* Fails because the text is horizontal: on an east-west leg two
   labels one ring apart are 14 units apart and need ~23 to clear. **1557
   collisions** in a 9216-case sweep.
2. *Vertical nudging against ESTIMATED boxes* (0.55em per character). Fails
   quietly — it reads "home" as 17.6 wide when it renders **21.1**, and
   font-size 8 as 8 tall when it renders **10**, so it under-nudges. **240
   collisions.**
3. *Vertical nudging against measured `getBBox()`.* **Zero.**

The step is **13**, not 11: a label renders 10 tall and clearance allows 1.5
either side, so anything under 11.5 leaves the pair touching — an 11-step
version failed exactly the cases where two legs share a course.

**Verified by sweeping course combinations, not by looking at one:** 3888 across
two hold sets, 1024 more across no-wind/no-current and full-wind-and-current
furniture, plus the worst case of all three legs on the same bearing with 10 h /
24 h / 1 h 30 holds — zero overlaps and nothing outside the viewBox in any of
them. Re-run that sweep after any change to rose layout; it is cheap and it has
caught every one of these.

**A `Loiter` summary tile** sits between Mission time and Distance, reading
`PlanResult.total_loiter_hours` / `total_loiter_litres`. Those are broken out
of, **not additional to**, `total_hours` and `total_litres` — a test asserts the
mission totals move by exactly the hold, because a double-counting tile would
read plausibly and be wrong. It is shown **even at zero** ("0 min / no holds"):
that is the confirmation a plan reviewer wants, and a tile that vanished would
leave them unsure whether they had forgotten to set one. Amber when non-zero,
matching the marker on a holding leg, via a new `.tile.warn` rule — the tile
CSS only had `ok` and `bad`.

`_total_litres` sums `total_litres`, so the sensitivity rows carry the hold;
otherwise shifting the premium would appear to change a mission's whole burn
while a two-hour hold sat outside every row.

Verified live: 0 / 60 / 115 / 140 h holds run WITHIN RESERVE → BREACHES, spare
falling 112.2 → 55.2 → 2.9 → −19.0 L. The flip at ~118 h agrees with the
~190 h station-keeping endurance once the underway burn is taken off. Unit
switching converts (90 min ↔ 1.5 h) and round-trips exactly, same rule as the
waypoint unit.

## Quick start, and the help button (2026-08-11)

`QUICKSTART.md` at the repo root is a one-page tour, and the **Quick start**
button in the top bar renders **that same file** — fetched from `/quickstart.md`,
which `server.py` serves from the repo root rather than a copy under `ui/`.

**One source, deliberately.** This repo has repeatedly paid for the same claim
living in several places (the retracted gauge figure survived in five). A help
panel with its own prose would be that trap again, so there is no help text in
`index.html` at all — a test asserts it.

**The renderer is a deliberately small markdown subset** in `app.js`
(`renderMarkdownSubset`): headings, lists, paragraphs, bold, inline code, plus
joining a wrapped list continuation back onto its item. It escapes before
applying marks, so nothing in the document can inject markup. It is NOT a
markdown parser and must not grow into one — a full parser is a dependency this
project does not take.

**That constraint is the interesting part, and it is tested.** A table, a link,
a fenced block or an image would render as literal punctuation in the panel
while looking perfect on GitHub — a defect nobody notices, because the document
is usually read on GitHub. `tests/test_ui.py` fails the build on any of those.
It also checks that **no bold or code span crosses a line break**, since the
renderer works line by line: the first draft had `**Plan\n  mission**` and would
have shown the asterisks.

**Escape, the Close button and a backdrop click all dismiss it**, focus moves to
the panel on open and returns to the button on close, and a click inside the
sheet does not close it. All verified live.

**Restart the server after editing `server.py`.** The new route 404'd on first
test because the running process predated the edit — Python does not reload, and
the UI edits beside it did hot-reload, which is exactly what makes this
confusing.

## The UI is an operating instrument now, not a briefing (2026-08-11)

Andy stripped the explanatory apparatus out of the private UI as well, not just
the public export. Removed outright:

- the gondola description under Vessel (`gondolaNote`),
- the sea-state assumption note under Environment (`seaNote`),
- the mission-clock note,
- the page footer (fit windows and the assumptions caveat),
- **the whole tank/gauge card** — its discussion, `gaugeNote`, and the
  `capTable` capacity-scenario table.

The JS that filled each was deleted with it; nothing writes to a node that is no
longer there. `styles.css` lost its now-dead `footer` rules.

**What was checked before agreeing the tank card could go.** It held the only
rendering of `capacity_scenarios`, so that comparison is genuinely gone from the
UI — it is still on `PlanResult` for any caller that wants it. What it did NOT
hold is the needle: `indicated_return_pct` is the **"Needle on return" tile** in
the Summary card, and `verdict` still drives the banner, `gauge_breach`
included. **Verification rail 6 therefore still holds** — every surface renders
`verdict` and quotes spare against `binding_margin_*`. Confirmed live across all
four states: WITHIN RESERVE, BREACHES RESERVE, RUNS DRY (twice, at different
depths), each with its detail line.

What survives is what an operator acts on: the verdict banner, nine summary
tiles, warnings, the per-leg table with its extrapolation flags, mission marks,
and the sea-state sensitivity band.

## Max survey fills in the line count (2026-08-11)

Pressing **Max survey for the reserve** now writes the solved count into the
Lines field whenever a line length is set. Nothing was computed to make this
work — `max_survey_lines()` already returned the count and the API already sent
it; the UI was only *printing* it. The change is four lines of write-back plus a
`refreshDerived()` so the Survey-distance readout cannot show a total that
disagrees with its own inputs.

**A blank Lines field is the case the button exists for, and it used to 422.**
`Leg.validate` rejects a line length with no count (`lines must be a whole
number, 1 or more`), so the request never reached the solver. The UI now sends a
probe count of 1 when the length is set and the count is not. **The answer does
not depend on the probe** — `max_survey_lines` searches upward from 1 — but the
engine's note compares the answer against the count that was *requested*, so
after a probe it would read "the planned 1 lines fit, with room for 35 more":
true, and useless. That note is suppressed when the probe was used.

**The field stays an ordinary input.** Type over the filled value and the next
plan or solve uses what you typed; nothing locks it or re-imposes the solved
number. That is Andy's explicit requirement and `tests/test_ui.py` asserts it
directly — a future change that made the field `readonly` for tidiness would
look like an improvement and would not be.

**Lesson from the mutation run here.** The first version of the probe test
asserted only that `leg.lines = 1` appeared in the function, and **survived**
`if (false) leg.lines = 1;` — the text was still there while the probe was dead.
A static test that matches a bare fragment tests the source, not the behaviour.
It now pins the guard and what `probed` is derived from. All six mutants die:
locking the field, disabling it, dropping the write-back, dropping the readout
refresh, disabling the probe, and inverting its condition.

These remain source assertions. The behaviour was verified in the browser
instead: blank Lines with a 10 NM length fills 36 and reads 360.0 NM; a
pre-entered 3 fills to 36 and keeps the engine's planned-vs-fits note; typing 20
over the filled value sticks, plans 20, and returns 200.0 NM in the table.

## Three leg orders, and only the visual one differs (2026-08-11)

The survey block **reads below both transits** (Andy's call), while the form is
**typed and tabbed in mission order**:

| | order |
|---|---|
| Document — and therefore **tab order** | out → survey → home |
| Request, from `buildBody()` | out → survey → home |
| Visual, from CSS `order` | out → **home** → survey |

**The markup stays in mission order; CSS does the moving.** `.legs` is a flex
column and `.leg-visual-last` carries `order: 1`. Tab order is document order
and nothing else, so the keyboard walks the form in the order the mission is
flown even though the eye reads the survey last.

**Not `tabindex`** — that was the first instinct and it is wrong. A *positive*
tabindex forms its own sequence ahead of every `tabindex=0` element in the
document, so tabbing from the top of the page would reach the leg inputs before
Environment and Vessel. A test asserts no positive tabindex exists, because the
next person will have the same instinct.

Know the cost: focus order and visual order now genuinely differ, which is the
usual argument *against* CSS reordering. It is deliberate here — data entry
follows the mission, reading follows the layout — and it is the trade Andy
asked for, not an oversight.

`:first-of-type` still suppresses the separator correctly: it follows DOCUMENT
order, and `order: 1` moves only the survey, so Transit out leads either way.
Verified in the browser — border-top 0px on Transit out, 1px on the other two.

`tests/test_ui.py` pins all three orders, that exactly one block carries the
moving class, that the class actually resolves to a flex `order`, and that no
positive tabindex exists. Four mutations killed: dropping the class, zeroing the
order, removing the flex parent, and adding a positive tabindex. They are
static-source assertions — they check what the files say, not what a browser
renders — which is the right trade when the failure guarded against is a source
edit. The geometry was checked by hand instead.

## Mission waypoints, and their unit (2026-08-11)

Renamed from "home marks", and the distances now take a unit — `km` (default) or
`nm`. `plan(..., waypoints=(13.0, 26.0), waypoint_unit='km')`; the body keys are
`waypoints` / `waypoint_unit`.

**The unit is a DISPLAY choice and must never move a waypoint.** It decides how
supplied values are read and how a mark is labelled, nothing else. Omitting the
values gives the same physical radii in either unit — `default_waypoints(unit)`
returns 13/26 km as 7.019/14.039 NM — because a plan whose callouts jumped when
someone flipped a selector would be a genuine trap. Three tests pin this and a
mutation that made the defaults ignore the unit is killed by them.

**`NM_PER_WAYPOINT_UNIT` is named for the direction of the conversion**:
nautical miles *per one unit*, so converting a waypoint to NM **multiplies**. It
was originally called `WAYPOINT_UNITS` and the code divided, putting a 13 km
waypoint at 24.08 NM instead of 7.02. The existing marks tests caught it
immediately; the name was changed so the next person is not invited to make the
same mistake.

Every mark carries **both** `km_from_home` and `nm_from_home` regardless of the
unit chosen, plus `from_home`/`unit` in the chosen one. Two plans made in
different units are therefore directly comparable — and the unreachable-waypoint
warnings speak the operator's unit, or they would name a distance nobody typed.

**`home_marks_km` still works** (always km) when `waypoints` is absent, because
it is the spelling the published API documented. Passing both raises rather than
resolving by precedence: silently preferring one would plan callouts the caller
did not ask for. The older mission-clock tests deliberately still call the
deprecated kwarg, which is what keeps that path exercised.

**In the UI**, changing the selector converts what is already typed. It
re-renders from unrounded NM values held in `waypointsNm` rather than from the
box's own text — converting the displayed text each time loses precision to the
rounding, and 13 km came back as 12.999. `lastRendered` is how an operator's own
edit is told apart from the script's writing.

## Mission clock and distance-from-home marks (2026-08-09)

`plan(..., start_time=datetime, home_marks_km=(13.0, 26.0))`. Legs always carry
`start_hours`/`end_hours`; with a start time they also carry clock strings, and
the plan gets `finish_clock`. **Clock displays carry the date and a `(+Nd)`
suffix once they roll over** — survey-speed endurance is over two days, so an
undated `02:00` is ambiguous by a day or more.

`marks` are the mission's timed callouts, sorted chronologically, each reporting
fuel burned and the gauge reading there. `kind` is `'range'` (a fixed distance
from home, `outbound`/`inbound`, one pair per radius — 13 and 26 km by default)
or `'phase'`: `home_departure` (the first leg starts **making way**, after any
launch hold), `survey_arrival` (the start of the **first** survey leg, before
its own hold), `survey_departure` (the end of the **last**), and `home_arrival`
(the end of the mission — **always equal to `total_hours`**, an identity a test
pins so the marks table and the mission's own total cannot disagree; the home
marks were added 2026-08-12 with the loiter-placement fix). **The Mission clock
card's "Back alongside" readout was removed 2026-08-13 (Andy)** — the same
figure is still in the marks table as `home_arrival`, and `finish_clock` is
untouched on the API and in the report, so nothing but the duplicate readout
went.
Arrival-to-departure is time on task, so a reposition between two survey
patches sits *inside* the span rather than splitting it. Sorting happens once at
the end of `_mission_marks` (stable, so same-time marks keep insertion order:
departure → survey → arrival), and a new mark can be added anywhere in that
function without minding order.

**"Distance from home" means distance along the planned track** — run made good
on the first leg, distance still to run on the last — because the planner has no
position model. Anything else would be inventing geometry the inputs do not
contain. A leg shorter than the mark warns rather than silently omitting it, but
a plan with **no survey leg** warns about nothing: that is a legitimate mission
shape, not a near-miss, and the distinction is deliberate.

The clock is presentation only, and a test pins that: adding a start time must
not move any burn figure, margin or verdict.

**Lesson from the mutation run here:** an ordering assertion is not a
measurement. `test_fuel_at_the_mark` originally checked only
`outbound < inbound`, which stayed true even when the fuel burned on every
earlier leg was dropped — the inbound mark still sits 16 NM into its own leg.
It now checks the value against arithmetic done outside the engine. If a test
compares two of the engine's own outputs, ask what it would still pass with.

## Reserve floor raised to 25% (Andy, 2026-08-09)

`reserve.default_fraction` 0.15 → 0.25. Policy, not a measurement. Under the
adopted reading (A) mission fuel falls **211.4 → 185.7 L** (−12%), endurance
62.4 → 54.8 h, planning range 499 → 439 NM, and `max_survey_length` on the
default vessel **428 → 371 NM**.

Two things worth knowing about the change:

- **The higher floor sits deeper into the band nobody has measured.** Every
  gauge reading is from the top third, so a 25% floor is more conservative in
  litres while resting on no better evidence about what the needle means down
  there. `tools/reserve_band.py` now reports 43 uncalibrated points below the
  lowest datum instead of 53.
- **The floor is written in two places** — `model.json` and the `Vessel`
  dataclass default — and they can drift. `test_the_reserve_policy_agrees_
  everywhere_it_is_written` pins them together, because nothing else would
  notice: a mismatch would quietly plan the UI and the API to different floors.

**Chasing it cost more than it should have**, and two things are fixed so it
does not next time:

- Four tools carried their own hardcoded `15%` — the endurance sheet, the gauge
  report, the methods doc and `compare_fits.py`. All four now read the floor
  from `model.json`. `compare_fits.py` had its own copy of the capacity too.
- **`build_methods_doc.py` defaulted its output to `Downloads`**, not the repo,
  so a plain run rebuilt a copy nobody reads and left the committed document
  stale. It now defaults alongside its three siblings. This was caught only by
  text-searching the rebuilt documents for "15%" — worth doing after any change
  that reaches into prose.

Tests reference `RESERVE_PCT` from one pinned constant rather than retyping the
figure, so a future policy change touches that constant and the scenarios it
invalidates, not thirty assertions.

## Max survey: lines, not just distance (2026-08-09)

`max_survey_lines()` answers "how many lines fit", which is what an operator can
act on when the area will not fit in one run. It returns a dict — count,
distance, requested, shortfall/spare, and a note — because the count alone is
half an answer.

It searches INTEGERS rather than bisecting a distance, and that is not
fussiness. The marginal cost alternates: at 25 kt an into-wind line costs ~3.5x
the downwind line after it, so whether the last line fits turns on its
direction. At 20 kt with 20 NM lines the answer is 16 and the 17th breaches on
parity alone, though the same 340 NM as an even set of lines fits.

The search assumes fuel is monotonic in the line count. It is (every line adds
a positive burn) and a test pins it, because a broken assumption there would
return a quietly wrong count rather than fail.

**A mutation worth remembering.** "Scale the line length, hold an even count"
survived the first run — it produces the same total distance and, on the test
input, the same answer. It is not equivalent: a scan found 26 configurations
where it is wrong by one line, always where the true answer is odd. The test
case had simply landed where parity did not bite. When a mutant survives, check
whether it is genuinely equivalent before assuming the test is fine — and if it
is not, take the discriminating input from the search rather than inventing one.

## The drawdown numbers live in tools/drawdown.py

`reserve_band.py` reports them, and both Word reports quote them, so the
arithmetic lives in `tools/drawdown.py` and all three import `spec()`. Nothing
in that module prints or writes. If a drawdown ever lands, the reports pick up
the new figures on their next build without anyone editing prose.

It computes under the ADOPTED reading, which matters twice:

- a drawdown span costs the **profile integral**, not `span × L/point` — the
  band below the calibrated one is richer under (A), so the full 68→25% run is
  ~111 L and 33 h, not the ~89 L a flat scale gives;
- **exposure is a SHAPE question, not a level one.** The drawings pin the whole
  gauge at 250 L, so the unmeasured points hold 221.2 L however it is
  distributed. A ±10% shape error is ±6 L — and the sign is inverted: a richer
  bottom band means LESS mission fuel, because more of the fixed volume sits
  below the floor. The reading-(B) table this replaced had it the other way
  round and twice as large.

`spec()` raises if a shape row does not conserve the drawing volume; that
invariant is what makes the table mean anything, and it was verified to fire.

## Are the documents current? Rebuild and diff — do not eyeball

The check that actually works, and the one that found a wrong formula that had
survived three sessions of edits:

```bash
SOURCE_DATE=<commit date> python tools/build_report.py /tmp/f/report.docx
SOURCE_DATE=<commit date> python tools/build_gauge_report.py /tmp/f/gauge.docx
SOURCE_DATE=<commit date> python tools/build_methods_doc.py /tmp/f/methods.docx
python tools/build_endurance_sheet.py /tmp/f/sheet.xlsx
```

then compare each against the committed copy on FORMATTING, not just text. Pin
the date or every title page differs for no reason.

Two traps this caught on 2026-08-09:

- The methods report's §6.1 gave `usable = capacity x (1 - reserve fraction)`,
  retired back in v2.4.0. It read as harmless because under reading (A) the two
  land close (185.7 vs 187.5 L) — under (B) they are 154.5 vs 187.5. **A stale
  formula can hide behind a coincidence in the current numbers.**
- Importing `compare_all.py` as a module ran it as a script against a stale
  directory and `sys.exit`ed before the real comparison. The output looked like
  a genuine diff and pointed the wrong way — the "rebuilt" column was months
  old. If a diff says the committed file is NEWER than a fresh build, suspect
  the harness before the file.

## Verification rails — do not remove

1. **Per-gondola fit windows.** Legs outside a gondola's fuel-law RPM window
   are flagged as extrapolation. EM2040 flags below ~5.3 kt; EM712 flags at
   survey speed. These flags are the product, not noise.
2. **Sensitivity bands.** The sea-state premium is an assumption (one anchor
   point exists; see report §7) — every plan carries premium and capacity
   sensitivity rows.
3. **Mutation tests.** `tests/test_engine.py` perturbs coefficients and
   asserts results move, per-gondola. If you change the model and no test
   fails, the tests are broken, not the change safe.
4. **Survey fuel is summed LINE BY LINE, never from an averaged premium.**
   The heading premium cancels over a reciprocal pair, but fuel is convex in
   RPM, so the mean of the two rates exceeds the rate at the mean premium — the
   line into the weather costs more than the reciprocal saves. Cancelling
   understates survey fuel by +0.9% at 12 kt, +7.1% at 20 kt, +17.2% at 25 kt,
   and an odd line count cannot balance at all (3 lines at 20 kt: +21%). The
   EM712's linear law shows exactly zero penalty, which is the control proving
   the effect is curvature and not a loop artefact. Extrapolation is flagged
   **per line**: at 9 kt in 25 kt of wind the mean is inside the fitted window
   while the into-wind lines are outside it.
   The old guard still holds — the mean heading premium is zero for any even
   line count, so a tailwind cannot be banked across a whole survey.
   This is deliberate; it also prevents banking a tailwind across a survey.
5. **Table widths are checked on every document build.**
   `docx_style.check_table_widths` hard-fails if a table is wider than the 6.5"
   text column, and all three Word builders call it. Column-wrap defects are
   invisible in builder source and only appear on render — this repo has paid
   for that twice. The rail was written against the *published* report, which
   had four wrapping tables; the gauge and methods reports already passed.
6. **One verdict, one binding floor.** Every surface renders `PlanResult.verdict`
   and quotes spare against `binding_margin_*`; `max_survey_length` solves to
   the same floor. If you add a surface, read those fields — recomputing a
   verdict locally is how the UI and the API drift apart.
7. **The forecast currents are checked against two things that are not them.**
   `python currents.py crosscheck` rebuilds the vectors from the native ROMS
   grid by hand (staggered averaging plus the `angle` rotation) and
   `python currents.py station` compares against CO-OPS harmonic predictions.
   `python currents.py verify` runs eleven cheap rails, including that the
   binary DAP2 path agrees with NOAA's ASCII rendering. None of these run in
   the unit suite — they need the network, and the suite must not. Run them
   after touching anything in `currents.py`, and read
   `tools/dbofs_plotcheck.py` before trusting a georeferenced overlay: it
   refuses to draw rather than drawing arrows in the wrong place.

## Document builders

All four document builders share `tools/docx_style.py` — page setup, styles and
the `para`/`mono`/`bullets`/`table`/`callout`/`figure` helpers, plus the
table-width rail. Nothing is duplicated between them any more.

The three Word documents genuinely differ in three ways, and those survive as
arguments to `new_document()` rather than as forked code: `right_from` (the
methods report left-aligns table columns, the others right-align from column 1),
`warn_prefix` (the methods report tints `⚠`/`†` cells amber, the others do not)
and `callout_spacer` (only the efficiency report wants trailing space after a
callout). Heading sizes are per-document too — the efficiency report runs
16/12.5/11 pt, the older two 15/12/10.5.

**Pin the build date when diffing.** Every Word title page carries a date, so a
rebuild the day after a commit differs by one line for no real reason and the
"rebuild and check nothing changed" test stops meaning anything. Set
`SOURCE_DATE` (ISO) or `SOURCE_DATE_EPOCH` (the reproducible-builds convention,
UTC) to the date the document was committed:

```bash
SOURCE_DATE=2026-08-09 python tools/build_report.py /tmp/check.docx
```

A malformed value raises rather than falling back to today — a silent fallback
on a typo would reintroduce exactly the spurious diff this removes.

**The endurance sheet is the exception, and not in the way this file used to
say.** It has no *title-page* date to pin, but openpyxl stamps
`docProps/core.xml` with `dcterms:created`/`modified` at write time, so the
`.xlsx` **can never rebuild byte-identically** and will always show as modified
after a rebuild. Do not read that as a content change: compare the XML members
excluding `docProps`. Rebuilding all four documents on 2026-08-11 changed
nothing but zip metadata in the three `.docx` and those two timestamps in the
`.xlsx`, so none of them were committed.

**If you change `docx_style`, re-run all four builders and diff the output.**
The migration was held to exactly that standard: each builder was run before and
after, and the documents compared on paragraph style, spacing, alignment, every
run's text/bold/italic/size/colour, table dimensions, column widths and cell
alignment. All three came out identical, which is why the committed `.docx`
files were not regenerated — there was nothing to regenerate. The comparison
script is worth rewriting rather than trusting a text-only diff: an earlier,
cruder version compared only visible text and would have missed the callout
spacer and the amber cell runs entirely.

## Documents

- `DriX8_Fuel_Gauge_Linearity.docx` / `.pdf` — 10 pages with figures: the tank
  gauge measured against the flow meter. Establishes the gauge scale, shows why
  the apparent band-to-band trend is not significant, and states plainly that
  the reserve band has never been calibrated. Regenerate with
  `python tools/build_gauge_report.py` — it recomputes the significance test, so
  if accumulating data ever makes the non-linearity real, the document says so.
- `DriX8_Fuel_Methods.docx` / `.pdf` — 10 pages: which ROS 2 topics feed the
  endurance numbers and exactly how they are produced (extraction, segmentation,
  binning, fitting, derived quantities, verification, limits). Regenerate with
  `python tools/build_methods_doc.py`; every figure is read from the pipeline
  output so it cannot drift.
- `DriX_Fuel_Efficiency_Report.docx` / `.pdf` — 28 pages: derivations, the
  gondola attribution (§5), the MCAP refit (§5.5), tank investigation (§6),
  sea-state treatment (§7), planning framework (§8), data-quality register
  (§9). Regenerate with `python tools/build_report.py` — **it had no builder
  until v2.4.0, which is exactly why it was the document that drifted.** Every
  derived figure is recomputed at build time from `model.json` and the fit
  JSON, and the planning tables come from calling the engine rather than
  repeating its arithmetic; the §3.3 model comparison is refitted in-script and
  reproduces `model.json`'s `f0`/`f1` to 9 significant figures.

  Two things it cannot recompute, both marked in the builder:
  - **Source observations** — the 2024 trial steps, the four-heading test, the
    DD2024 refuel and the Exail ROE costs. These are transcribed measurements,
    i.e. inputs, and everything else derives from them.
  - **`SRC_2022`** — aggregates of the 2022 operational log. The
    per-observation log (21 rows) is **not in this repo**, so those constants
    and Figure 8 (a carried PNG asset) are the report's one remaining drift
    risk. Recover that log and both become computable.

  Eleven of the twelve figures are regenerated on every run into
  `tools/report_figs/`; `fig08_tank_trace_2022.png` is the carried asset.

  **§8.5 asserts a claim rather than quoting a count.** It first quoted the
  live test count, discovered at build time so it could not drift — but that
  made the report churn whenever an unrelated test was added, and nobody acts
  on the number. `check_test_claims()` now verifies what a reader actually
  relies on: that tests exist and that mutation guards are among them, failing
  the build if either stops being true (including if the guards are merely
  *renamed* out of recognition). Counts print to the console instead. The
  general rule: put a number in a document only if a reader would act on it —
  otherwise assert the property and let the build enforce it.

  **The `.pdf` is not produced by the builder.** It is a Word export and has to
  be refreshed separately after a rebuild, or it goes stale against the `.docx`
  beside it.
- `D:\Claude\ROS2\DriX8_ROS2_Topic_Reference.docx` — what the source bags
  contain: all 241 ROS 2 topics (26.8 M messages, ~77 h) grouped by subsystem
  with rates and descriptions, plus field definitions for the message types
  that matter, taken from the schemas embedded in the bags. Read it before
  mining the logs for anything new.

  **It lives outside this repo and is not versioned** (Andy's call, 2026-08-07;
  the builder stays here). A rebuild overwrites it with no history behind it,
  so treat the generator plus `topic_inventory.py` as the real source. The
  output path is hardcoded in `build_topic_doc.js` (`OUT`) and duplicated as
  the default in `bake_toc.ps1` — move the document again and both must change.
  Regenerate:

  ```bash
  python tools/topic_inventory.py       # bags -> tools/topic_inventory.json
  npm install --prefix tools            # one-off; tools/node_modules gitignored
  node tools/build_topic_doc.js         # -> D:\Claude\ROS2\...docx
  powershell -File tools/bake_toc.ps1   # fill in the contents page
  ```

  Descriptions live in `tools/topic_inventory.py`; the `.js` is presentation
  only. The script hard-fails on any topic missing a description, so a new
  firmware that adds topics surfaces as a failed run, not blank rows.

  **The bake is not optional.** The contents page is a Word TOC *field* over
  the Heading 1/2 styles, and the generator emits it empty — only Word fills a
  field, so an un-baked build has a blank contents page in every other reader.
  `bake_toc.ps1` opens the document through Word once, updates the field and
  saves the entries back as its cached result; the build prints a reminder
  because a rebuild empties it again. Two consequences to know:

  - The committed `.docx` is Word's output, not the generator's, so it is
    reproducible in *content* but not byte-for-byte (Word stamps revision ids).
    Do not expect a clean diff from a rebuild.
  - Word clears `features.updateFields` when it saves, so the baked file no
    longer refreshes itself on open. That is what stops the "save changes?"
    prompt, and it is also why stale content needs a rebuild plus a re-bake
    rather than just opening the file.

  Section numbers in the TOC are just the heading text (`h1("4.  …")`);
  renumber a section and the TOC follows only because the heading did.

  The `TOC1`/`TOC2` paragraph styles are not decoration — they carry the
  right-aligned dot-leader tab that puts entry page numbers at the margin.
  Word invents equivalents when it updates the field, so deleting them looks
  harmless in Word and misplaces every page number everywhere else. Their
  `name` must stay `"toc 1"`/`"toc 2"`; that string is how Word binds an entry
  level to a style.

## Client identifiers are OUT of this repo (2026-08-11, Andy)

The cruise, vessel and location identifiers for the 2022 and 2024 campaigns were
removed from every tracked file. **The exact tokens are deliberately not written
here** — this file ships in the public copy, and a list of the strings that were
scrubbed is itself the thing being scrubbed. They are named in full in the
scrub commit's message, which stays in this private repo's history.

None of it was load-bearing: they were provenance labels saying which cruise a
measurement came from, so they were genericised ("2022 shakedown", "2024 speed
trials", "the 2022 operational window", "the Hourly Ops Log") and **no
coefficient, figure or derived value moved**. The scrub diff was 36 lines
replaced by 36, tests stayed at 122 green, and every rebuilt document reproduced
its numbers.

**`DD2024` was deliberately left.** It is an internal refuel-event code, not a
cruise or client identifier, and it is load-bearing in `reserve_band.py` (a
source-constant consistency check raises on it). If it should go too, that is a
rename plus the assertion text — say so.

**What this cost, and what to do if it happens again:**

- **The prose is baked into the documents**, so scrubbing source is only half of
  it. All four builders had to be re-run, and **the three PDFs are Word exports
  no builder produces** — they had to be re-exported through Word COM or they
  would have shipped the old names beside clean `.docx` files.
- **A byte grep of a PDF proves nothing.** PDF text lives in compressed streams,
  so `grep`-ing the raw bytes reported all three PDFs CLEAN while two of them
  still carried a scrubbed name on six pages between them. Extract with PyMuPDF
  (`page.get_text()`) and search that. The same trap applies to `.docx`/`.xlsx`:
  read the XML out of the zip, do not grep the container.
- **`fig08_tank_trace_2022.png` is the carried asset and had the name in its
  TITLE**, where no rebuild could reach it. It was repainted in place, because
  the per-observation log needed to redraw it is not in this repo.
- **A first attempt at that repaint detected "the topmost rows with ink" and
  wiped the axes frame and the first data point with the title.** The band is
  pinned to measured rows now (title 20–46, frame at 55) with an assertion that
  the frame row still has its 996 ink pixels afterwards. Rasterise and LOOK —
  the damaged version would have passed any text check.

The identifiers are still throughout **git history**, which is why history has
to be dealt with separately before this repo can be public.

## Publishing: the public repo is an EXPORT, not a mirror (2026-08-11)

`AndyMcLeod/DriX-Fuel-Planner-Public` is a **separate, MIT-licensed, public**
repo built as a fresh single-commit tree from this one. It is not a branch and
not a mirror: this repo's pre-scrub history carries client identifiers
permanently, so the public copy had to start from nothing.

**`python tools/make_public.py <dest>` is the only supported way to build it.**
It exports `git archive HEAD` (tracked files only, so gitignored bags and caches
cannot leak), strips the marked UI commentary, and refuses to finish if any
client identifier survives.

**Every public/private difference must live in that script.** Anything hand-made
downstream reverts on the next export — this has now bitten twice:

- a `LICENSE` added only to the public repo would vanish on re-export, which is
  why the MIT licence lives HERE and travels with the export;
- the card commentary, if deleted by hand in the public repo, would come back.

**The commentary split is deliberate.** Static explanatory prose is marked
`<p class="note commentary">` and the public build drops it (Andy: cleaner view
in public; the private UI keeps the rest). **Four blocks remain** — survey
lines, the compass legend, mission marks, and the sensitivity preamble — so
`EXPECTED_COMMENTARY` is 4. It was 6 until the mission-clock note and the whole
tank/gauge card were deleted outright on 2026-08-11 (see below).

The **id-bearing** notes are JS-filled with computed output and **must never be
stripped**. Two survive: `legNotes`, which carries the per-leg extrapolation
flags that verification rail 1 calls the product rather than noise, and
`maxOut`. `seaNote`, `gondolaNote` and `gaugeNote` are **gone** — deleted with
their cards, along with the JS that wrote them.

The script counts what it removes and aborts on a mismatch rather than quietly
publishing a UI that still explains itself, or one missing a warning.

**`git archive HEAD` reads the last COMMIT.** Uncommitted UI edits are invisible
to it — the count assertion caught exactly that on its first run. Commit before
exporting.

## Launching it: the desktop shortcut (2026-08-11)

`start_planner.bat` at the repo root is the double-click launcher, and
`tools/make_shortcut.ps1` puts a shortcut to it on the desktop. Both are
modelled on the ASV console's `start_sim.bat`, deliberately — same `%~dp0`
self-location, same `where python || py` fallback, same `%*` pass-through, so
the two projects behave identically at the point an operator touches them.

**Nothing knows where the project lives.** The bat resolves the project from its
own location; the PowerShell script resolves it from `$PSScriptRoot`'s parent
and writes that into the `.lnk`. Copy the folder anywhere, re-run the script,
and the shortcut is correct. Re-running overwrites rather than duplicating.

Three things worth knowing before changing any of it:

- **The script verifies by reading the `.lnk` back.** `Save()` accepts a target
  that does not exist and only fails on double-click, so a save-and-trust
  version would report success for a broken shortcut.
- **`tools/fuel.ico` is generated** by `tools/make_icon.py`, not downloaded — no
  licence question, and a binary in the repo has a builder beside it like
  everything else here. **The small frames are not the big one shrunk**: below
  32 px the dial is redrawn with a fatter arc and a stubbier needle, because the
  faithful geometry renders as a dark blob at the 16 px Explorer uses in list
  view. That was found by rasterising the frames and looking at them — the
  builder looked perfectly correct either way.
- **The Desktop path is asked of Windows**, not assembled from `%USERPROFILE%`.
  This profile's desktop is redirected to `OneDrive\Desktop`, and the assembled
  path silently creates a folder nobody sees.

`MOVING.md` is the operator-facing version of all this: prerequisites, the two
transfer routes with what not to copy, verification, and the four pipeline paths
still hardcoded to this machine's drive layout.

## Bag locations (outside this repo)

`E:\fuel\D8_2040\<day>T00\*.mcap` — 4 days, EM2040 configuration, 04–07 Aug
2026. The 2026-08-07 folder is a partial copy: no `metadata.yaml` and segment
`_4` is absent (~1.3 h hole) — segment time-series across it.

**A copy of those bags also sits at `D:\Claude\Fuel\D8_2040\`, inside this
working tree — now 6.5 GB** (measured 2026-08-11; it was 4.5 GB at four days and
grows with every day added, so do not trust a figure quoted here over `du`). It
is gitignored (`D8_2040/`, `*.mcap`) — do not remove those rules. This workflow
uses `git add -A` routinely, and without them a commit would try to push
gigabytes of binaries past GitHub's 100 MB file limit.

For scale, when copying the project anywhere: the bags are **6.5 GB**, `.git` is
15 MB, and the project proper — everything git tracks, code and UI and model and
all four documents — is **5.5 MB across 52 files**, of which 3.6 MB is the
documents themselves.

**Do not derive that figure by subtracting the bags from the folder size.** That
gives 33 MB, because the working tree also carries 18 MB of `tools/rosbags/`
`.npz` caches and 9 MB of `tools/node_modules/`, both gitignored and neither
needed to run the planner. `git ls-files` is the honest measure. The caches
being gitignored has a consequence worth knowing: a fresh clone cannot run
`fit_em2040.py` or `reserve_band.py` until it either copies `tools/rosbags/` or
re-extracts from bags.

## Known gaps / next data worth collecting

- No cruise data below 1400 rpm (~5.3 kt) — a few steady runs at 1100–1400 rpm
  close it.
- Speed law is SOG-based — reciprocal-heading pairs would strip the tide out.
  **Now partly answerable without new trials:** the MCAP days carry position,
  COG/SOG and INS heading, and `currents.py` can say what DBOFS thought the
  tide was doing at that place and hour. Differencing the two over the fitted
  runs would give the tide the law absorbed, which is the double-count the
  current model has to apologise for. Not attempted yet; it needs the bag days
  to fall inside a retrievable OFS cycle, and NOAA's archive does not go back
  indefinitely.
- The shaft-RPM sensor was faulted through all four MCAP days (reads 0/garbage);
  the PLC `thruster_rpm` channel is the working one.
- **Turn rate is assumed, not measured.** `model.json` `turn_model` puts the
  radius at ~25 m on no evidence at all. The MCAP days carry position and INS
  heading through real line changes, so a rate of turn is extractable from a
  recorded survey — this is the one new assumption the project has ADDED
  rather than closed, and the cheapest outstanding measurement to settle.
- Sea state → RPM premium: the CALM ANCHOR is now measured (Aug 2026 MCAP
  motion analysis: no premium above ±2% at heave-std 0.03–0.13 m) but the slope
  into rough water is not — a fixed-RPM leg in a genuine seaway is still the
  missing measurement.
- The weather sensor is NOT bridged into the connectivity-box bags (checked
  Aug 2026: zero live wind/met topics across all 255 recorded channels; the one
  WeatherInfos message was a blank operator form). Ask ops to bridge its topic;
  extract_bags.py probes every new day and announces arrivals.
- Raw PHINS is not recorded either — only the PHINS-derived `light_ins`
  (~1.4 Hz heading/heave/roll/pitch), which the motion analysis uses. High-rate
  attitude would sharpen the motion statistics if ever bridged.
- Trim-tab telemetry (`trimmer/status`) starts 08-06 and is an ACTIVE surface
  (±30° swings in cruise); no first-order fuel correlation in calm water, but
  it is a drag variable to watch in rough-water data. Now extracted.
- Onboard Exail static endurance model decoded (`exail_static_model` block):
  L/h = 0.0845·V² + 1.127 on a 250 L / 20%-min tank — ~1.9× the measured burn
  at 8 kn. Reference only; the planner never uses it.

## Daily iteration (Andy adds a day at a time to D8_2040)

```bash
python tools/extract_bags.py D:/Claude/Fuel/D8_2040   # cached days are skipped
python tools/fit_em2040.py                            # refit + per-day agreement
python tools/compare_fits.py                          # new vs adopted, verdict
```

`python tools/reserve_band.py` is worth running alongside these: it is the one
tool whose answer changes the moment a day dips below the calibrated band, and
it cross-checks the caches against `model.json`'s gauge scale as a side effect.
Its consistency rail deliberately pools **only days inside the calibrated band** —
pooling everything would compare a wider span against a figure fitted to a
narrower one, and would abort on the first genuine drawdown, which is precisely
the day the tool exists for.

`compare_fits.py` reports **operational** deltas (burn at survey speed, planning
range, endurance) rather than raw coefficients, because coefficient diffs are
not interpretable alone. Adopt only if those justify it — then update
`model.json`, the reference values in `TestGondolas`, the endurance sheet and
the methods doc **together in one commit**.

Two behaviours worth knowing:

- **The newest day usually has one segment still being written.** The reader
  cannot parse it (`RecordLengthLimitExceeded` on a nonsense footer length).
  The extractor skips that segment, keeps the rest of the day, and writes a
  `.partial` sidecar naming what was dropped. Delete both the `.npz` and the
  `.partial` to re-extract once recording has finished.
- **Never pipe the extractor into `head`.** SIGPIPE kills the decode part-way
  while the pipeline still reports exit 0 — it looks like a clean run that
  silently wrote no cache.

## Refit procedure (when new MCAP data arrives)

The pipeline lives in `tools/` (extra deps: `pip install numpy mcap
mcap-ros2-support` — the planner itself stays stdlib-only):

```bash
python tools/extract_bags.py     # MCAP -> tools/rosbags/<day>.npz
python tools/fit_em2040.py       # caches -> fits + em2040_fit.json
```

Each takes an optional argv[1]: the bag root for `extract_bags.py` (defaults to
the location below), the cache dir for `fit_em2040.py` (defaults to
`tools/rosbags`). A bag root at the wrong level — day folders holding no
`.mcap` — aborts rather than writing empty caches.

Then update the `gondolas.*` block in `model.json` from the fit JSON, bump the
version, and run the tests — the mutation guards will tell you what moved.
`tools/em2040_fit_2026-08-07.json` is the adopted output snapshot that produced
model.json v2 coefficients; the scripts reproduce its coefficients to machine precision.
Traps already encoded in the scripts: lexical `.mcap` ordering, SOG in m/s,
the faulted shaft-RPM sensor (use `thruster_rpm`), and idle-day gauge wander.

## Lessons this project has already paid for

Each of these cost real time or produced a wrong statement. They are here so the
next session does not buy them again.

- **Never pipe a long extractor into `head`.** SIGPIPE kills the decode part-way
  while the shell still reports exit 0 — it looks like a clean run that wrote no
  cache.
- **Always pass `encoding='utf-8'` when reading or writing `model.json`.** On
  this machine `open()` defaults to cp1252 and silently turns every em-dash into
  mojibake, which then gets written back escaped.
- **Excel and Word hold file locks.** A build will fail with PermissionError if
  the workbook is open. Stage to a temp path, or ask.
- **Check significance before calling a pattern a finding.** The gauge
  "non-linearity" was three numbers that looked like a trend and separated by
  1.5σ. It propagated into the model, the handoff and memory before an error
  analysis caught it. Spread across few samples is not evidence.
- **Retracting a claim in one place does not retract it.** That same
  non-linearity claim was corrected in `gauge_calibration` in v2.3.0 but
  survived in **five** other places — `capacity_options._doc` (1.73), the
  endurance-sheet footnote (2.30), two spots in the README (2.30, 1.73) and the
  `gasoline_level_percent` description in `build_topic_doc.js` (2.30) — each
  quoting a *different* number, and the last of them feeding a document in
  another directory entirely. All five are fixed in v2.4.0. When you retract
  something, grep the repo for **the number**, not the sentence, and check the
  generators that write outside the repo.
- **Verify generated documents by rendering them**, not by trusting the builder.
  Blank pages, orphaned lines, split callouts and column-wrap defects have all
  appeared this way; none were visible in the code. The v2.4.0 report build
  added a fresh example: the wrong picture sat under "Figure 8" and the code
  looked perfectly correct. Rendering page 17 was the only thing that caught it.
  Render path on this machine: build to a scratch path, export via Word COM
  (`ExportAsFixedFormat`, plain string args — `[ref]` wrappers fail), then
  rasterise with PyMuPDF and actually look at the pages.
- **Word numbers `word/media/imageN.png` by relationship, not by caption.** In
  the previous report edition image8 was Figure 12 and image9 was Figure 8, and
  image6/image7 were Figures 7/6 — swapped. Extracting figures by media index
  silently mismatches captions. Walk the document body and read the `r:embed`
  ids in order instead.
- **Regenerate rather than hand-edit numbers in documents.** Every builder here
  reads from the pipeline output for that reason.
- **A premium on RPM is not a premium on litres.** `L/h = f0 + f1·RPM` has a
  non-zero intercept, so scaling RPM by (1+p) gives `f0 + (rate0 − f0)·(1+p)`,
  not `rate0·(1+p)`. When `build_report.py` was written it scaled litres, and
  Table 11's 250 L row published **+44%** where the correct RPM premium is
  **+27%** — with Appendix B.2 stating the right formula three pages later.
  Rendering the page caught it; the code looked fine. Both helpers now take
  `hours` and go through the fuel law, and they reproduce every value the
  pre-builder report published.

## Conventions (user)

- Docs (README, CLAUDE.md, generated documents) land in the SAME commit as the
  work they describe, never as a follow-up.
- Plan to the 25% reserve floor; assume 250 L nominal but show the
  evidence-based capacities alongside.
