# DriX mission fuel planner — START HERE

Mission fuel/endurance planner for the DriX-8. Enter sea state, a wind vector,
and three legs (transit out / survey / transit home); get per-leg burn and
margin against the 25% return-to-port reserve.

```bash
python server.py                          # UI on http://127.0.0.1:8765
python -m unittest discover -s tests      # 122 tests — must stay green
```

Stdlib only. No dependencies, no build step.

## The one rule

**`model.json` is the single source of truth for every coefficient.** Each
block is tagged `"fitted": true/false` so measurements are never confused with
assumptions. Do not hardcode numbers in `engine.py` or the UI; do not change a
coefficient without knowing which measurement or decision it traces to.

## Where things stand (2026-08-09, model.json v2.6.0)

Tree clean, everything pushed, **122 tests** green. Six days of MCAP data
(04–09 Aug) cached and adopted. Nothing half-finished.

**What the planner does, in one paragraph.** Enter sea state, a wind vector, and
three legs. It converts each leg's required SOG to RPM through the gondola's
speed law, adds a sea-state and heading premium, reads fuel off the gondola's
fuel law, and judges the result against a reserve floor that is a **needle
position, not a number of litres**. Surveys are flown and costed line by line.
Every plan carries a mission clock, timed distance-from-home and survey-phase
marks, a sensitivity band, and one `verdict` field every surface renders.

**The numbers as they stand** (EM2040, 8 kt, full tank, reading A, 25% floor,
**sea state 2** — the `Environment` default; the max-survey row is not a
constant, it runs 398.5 NM in flat calm and 329.3 NM at sea state 3):

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

### What changed on 2026-08-09, newest first

Read the per-topic sections below for detail; this is the orientation.

1. **Both reports now quote the drawdown spec**, computed by `tools/drawdown.py`
   which `reserve_band.py` and both builders share.
2. **Max survey answers in LINES** (`max_survey_lines`), not just distance —
   the actionable answer when an area will not fit in one run.
3. **Surveys are flown line by line** — lines / line length / bearing, alternate
   lines reciprocal. Fuel is summed per line because the fuel law is convex in
   RPM, which the old cancelled-premium survey understated by up to 17%.
4. **Reserve floor raised 15% → 25%** (Andy). Costs 12% of mission fuel.
5. **Mission waypoints at 13 and 26 km** (km or NM since 2026-08-11), in and out, plus survey
   arrival and departure, all on a mission clock.
6. **Reading (A) adopted** for the gauge: it spans the tank and is non-linear.

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
5. **Speed law is SOG-based** — reciprocal-heading pairs would strip the tide out.
6. **Offered but not done:** importing the endurance sheet into the live Hourly
   Ops Log Google Sheet. Needs Andy's go-ahead; it touches a live ops document.
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

## Loiter: delays imposed on a leg (2026-08-11)

`Leg.loiter_hours` — time held on station making no way, charged at the
gondola's **idle burn**. Andy's framing: things happen at sea that are
uncontrolled but can be accommodated in the model, then you replan. Each leg
card carries a loiter row (value + min/hours + `+15m` / `+1h` / `clear`), and
pressing **Plan mission** is the replan.

**It costs the measured idle rate, which was already in `model.json` and had
never been used.** `gondolas.options.em2040.loiter.lph = 0.95` — 20.8 h of
observed idle at ~1005 rpm. A 2 h hold is 1.90 L, verified live.

**Held at the END of its leg.** That is the one part of this that is a
convention rather than a measurement, and it is chosen because it leaves the
leg's own distance waypoints where they were: a hold on the outbound transit
does not move the outbound 13 km mark, but shifts everything after it by the
delay. Two tests pin exactly that, and a mutation moving the hold to the start
is killed.

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

**On the compass rose**, a held leg gets an amber dot at the **outboard end of
its own course line** — where the engine takes the hold — and the duration rides
that leg's label in amber (`out 45m`, `survey 2h30`). Compact form via
`hmShort()`; the tile and the notes keep the long one.

**A hold is deliberately NOT drawn as a direction.** It has no bearing, and an
arrow would be inventing geometry the delay does not have. The rose reads the
FORM, not the plan — unlike the tiles — because it is a live picture of the
inputs and redraws as you type, switch units or press a quick-add.

**The duration rides the label instead of getting its own text**, and that was
measured rather than chosen: a separate text one ring inboard collided with the
label it belonged to on any east-west leg. Checking bounding boxes across the
rose is worth doing after any change here — it also caught the wind and current
readouts touching, which is why the wind figure lifts when a current is shown.
The one overlap that remains, two legs within about 5° of each other, is
pre-existing and behaves the same with or without holds.

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
from home, `outbound`/`inbound`, one pair per radius — 7 and 26 km by default) or `'phase'` (`survey_arrival`, the start of
the **first** survey leg, and `survey_departure`, the end of the **last**).
Arrival-to-departure is time on task, so a reposition between two survey
patches sits *inside* the span rather than splitting it. Sorting happens once at
the end of `_mission_marks`, so a new mark can be added anywhere in that
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
- The shaft-RPM sensor was faulted through all four MCAP days (reads 0/garbage);
  the PLC `thruster_rpm` channel is the working one.
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
