# Quick start

Plan a transit–survey–transit mission and see whether it comes home above the
reserve. Start the planner, fill in three cards, press **Plan mission**.

## 1. Vessel

- **Gondola** — EM2040 is the one currently fitted and the one with measured
  curves at survey speed. The EM712 is flagged as an extrapolation at 8 kt.
- **Tank capacity** — 250 L is the drawing volume. The other options are
  evidence-based scenarios.
- **Return reserve** — the floor you plan to come home above. Default 25%.

## 2. Mission clock and waypoints

- **Start time** is optional. Leave it blank for elapsed hours only.
- **Mission waypoints** are distances from home, each timed twice: outbound on
  the first leg, inbound on the last. Enter them in **km or NM** — switching the
  unit converts what you typed, so the waypoints stay in the same place.

## 3. Mission legs

Three legs: transit out, survey, transit home. The survey block reads last but
is flown in the middle.

**Weather lives on each leg**, because a mission runs long enough for it to
change: every leg has its own sea state, wind and current on its Weather row.

- **Sea state** sets an RPM premium, shown beside the selector. It is an
  **assumption**, not a measurement — the source data supports one anchor.
  Treat it as a dial to turn.
- **Wind** is named for where it blows **from**.
- **Current** is named for where it **sets toward**. The two conventions are
  opposite, as at sea. A current changes the fuel, never the clock.

- A survey is **lines x line length** on a bearing, alternate lines reciprocal.
- **Max survey for the reserve** fills in the line count that fuel allows. Fill
  in the line length first and leave the count blank if you like. You can type
  over the answer afterwards.
- **Loiter** imposes a delay on any leg — launch or recovery hold-ups, traffic,
  a sensor problem. Enter minutes or hours, or use `+15m` / `+1h`. It burns at
  the measured idle rate and is taken at the start of the leg, so it delays that
  leg's crossings and everything after — a hold on the way home arrives home
  late. Press **Plan mission** again to replan with it.

## Reading the answer

The banner is the verdict. **The needle decides it, not the capacity row.**

- **WITHIN RESERVE** — comes home at or above the floor.
- **BREACHES RESERVE** — comes home below the floor.
- **BREACHES ON THE GAUGE** — the capacity row passes but the needle does not.
  Believe the needle.
- **RUNS DRY** — needs more fuel than the tank holds.

Then the tiles: fuel used, what the needle reads on return, margin, spare range
and time, mission time, loiter, and the current the plan was run under.

**Warnings and per-leg notes are the product, not noise.** An extrapolation flag
means that leg sits outside the RPM window its fuel law was fitted over.

## Three things the planner will keep telling you

1. **The sea-state premium is an assumption.** Every plan carries a sensitivity
   band for it. That band matters more than the single number.
2. **Legs outside the fitted RPM window are flagged.** A following current or a
   slow leg can drop below the floor of the fuel law just as easily as a fast
   leg can rise above its ceiling.
3. **The reserve band has never been measured.** Every gauge reading comes from
   the top third of the tank. Treat the floor as soft.

## If you only remember one thing

The reserve floor is a **needle position**, not a number of litres. The fuel a
mission may spend is what the gauge holds between the start level and the floor.
No tank-capacity assumption enters it.
