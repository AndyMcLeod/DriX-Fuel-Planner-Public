# Moving the fuel planner to another Windows machine

The planner itself is **standard library Python and three static files**. There
is nothing to compile, no package to install, no service to register and no
network access required. Moving it is: copy a folder, run one script.

The analysis *pipeline* behind it — the document builders and the MCAP refit —
is a different story and needs pip packages plus the source bags. That is
[further down](#optional-the-analysis-pipeline); most people moving this only
want the planner.

---

## The short version

```powershell
# 1. Get the project onto the new machine (either route, see below)
git clone https://github.com/AndyMcLeod/DriX-Fuel-Planner-Public.git C:\Tools\Fuel

# 2. Make the desktop shortcut
powershell -ExecutionPolicy Bypass -File C:\Tools\Fuel\tools\make_shortcut.ps1

# 3. Double-click "DriX Fuel Planner" on the desktop
```

The path is an example — put it anywhere. Nothing in the launcher is tied to
`D:\Claude\Fuel`.

---

## 1. What the new machine needs

**Python 3.9 or newer, on `PATH`.** That is the whole list.

Developed and tested on **3.11.9** (the Microsoft Store build). Older 3.9/3.10
should work — the planner uses no syntax newer than 3.9 and imports nothing
outside the standard library — but 3.11 is the only version actually exercised
here, so prefer it if you are choosing.

Check what a machine has:

```powershell
python --version
```

If that errors, install from [python.org](https://www.python.org/downloads/) or
the Microsoft Store. **Tick "Add python.exe to PATH"** if the installer offers
it. `start_planner.bat` falls back to the `py` launcher if `python` is not on
`PATH`, and prints a plain-English message naming the problem if neither
resolves — it will not fail silently.

A browser. Any current one; the UI has no framework, no CDN and no build step.

**Not needed:** admin rights, an internet connection, pip, a virtualenv, Node.

## 2. Getting the project across

### Route A — clone from GitHub (preferred)

```powershell
git clone https://github.com/AndyMcLeod/DriX-Fuel-Planner-Public.git C:\Tools\Fuel
```

Public repo, so no credentials are needed. This is the route that keeps the
history and lets the new machine pull later fixes.

### Route B — copy the folder

Fine on an air-gapped machine, or onto a stick. **Copy everything except the
bag data**, which is over a thousand times the size of the project:

| | size | copy it? |
|---|---|---|
| Project files (code, UI, model, all four documents) | **5.5 MB** | **yes** — 52 files |
| `.git\` | 15 MB | yes, if you want history |
| `D8_2040\` — raw MCAP bags | **6.5 GB** | **no** |
| `tools\rosbags\` — extraction caches | 18 MB | only for the pipeline (§5) |
| `tools\node_modules\` | 9 MB | no — `npm install` regenerates it |
| `__pycache__\` | small | no, regenerated |

The whole planner is **5.5 MB**, and 3.6 MB of that is the four Word/PDF
documents. It fits on anything.

Robocopy with the same exclusions the repo already declares in `.gitignore`:

```powershell
robocopy D:\Claude\Fuel E:\transfer\Fuel /E /XD D8_2040 __pycache__ node_modules /XF *.mcap
```

That keeps `.git\` and the `tools\rosbags\` caches. For a planner-only copy —
the smallest thing that works — add them to the exclusions:

```powershell
robocopy D:\Claude\Fuel E:\transfer\Fuel /E /XD D8_2040 __pycache__ node_modules .git rosbags /XF *.mcap
```

**That second command is the one that was tested.** The exact procedure in this
document — copy with those exclusions, run `make_shortcut.ps1` in the copy,
launch from the shortcut — was run end to end on 2026-08-11: 58 files, 5.6 MB,
the moved copy served the UI and its API, and all 122 tests passed from the new
location.

The planner does **not** read the bags at run time — every coefficient it needs
is baked into `model.json`. Leaving 6.5 GB behind costs the new machine nothing
unless it is going to refit the model.

## 3. Make the shortcut

```powershell
powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1
```

It resolves the project from **its own location**, so it always points the
shortcut at the copy it was run from. There is no path to edit. Run it again any
time the folder moves — it overwrites the existing shortcut rather than piling
up duplicates.

`-ExecutionPolicy Bypass` applies to that one invocation only and changes no
machine setting. It is needed because the default policy on Windows blocks
unsigned local scripts.

Options, if you want them:

```powershell
# a second shortcut on another port, so two planners can run side by side
powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1 `
    -Name "Fuel Planner (9000)" -Arguments "--port 9000"
```

`start_planner.bat` forwards anything you pass to `server.py`, so `--port N`,
`--no-open` and `--host` all work through the shortcut.

**To undo it completely:** delete the `.lnk` from the desktop. The script writes
nothing else, anywhere.

### Or skip the shortcut entirely

```powershell
cd C:\Tools\Fuel
python server.py
```

Same thing. The shortcut exists so the planner can be handed to someone who does
not use a terminal.

## 4. Check it worked

Double-click the shortcut. A console window opens and says it is serving on
`http://127.0.0.1:8765`; your browser opens on the planner a moment later.

**That console window IS the server.** Closing it stops the planner — which is
the intended way to stop it.

Then confirm the model travelled intact, which the UI alone will not tell you:

```powershell
cd C:\Tools\Fuel
python -m unittest discover -s tests
```

**122 tests, all passing.** These are not smoke tests — they include mutation
guards that fail if any coefficient in `model.json` has changed. If they pass on
the new machine, the model is byte-for-byte the model that was validated here.
If any fail, stop and read the failure before planning a mission on it.

## 5. Optional: the analysis pipeline

Only needed to **rebuild the documents** or **refit the model from new bags**.
Skip entirely if the new machine is just running the planner.

```powershell
pip install numpy matplotlib python-docx openpyxl mcap mcap-ros2-support
```

- `numpy` — every fit and the drawdown arithmetic
- `matplotlib` — the report figures
- `python-docx` — the three Word documents
- `openpyxl` — the endurance sheet (`.xlsx`)
- `mcap`, `mcap-ros2-support` — reading the ROS 2 bags

The ROS 2 topic reference additionally needs **Node.js** (`npm install --prefix
tools`, then `node tools/build_topic_doc.js`).

**Two things will not work on a new machine without more than pip:**

- **Anything reading the bags** — `extract_bags.py`, `fit_em2040.py`,
  `topic_inventory.py` — needs the MCAP files, which you deliberately did not
  copy. Either copy `D8_2040\` too, or copy `tools\rosbags\*.npz` (18 MB) and
  work from the extraction caches, which is enough for `fit_em2040.py`,
  `compare_fits.py` and `reserve_band.py`.
- **`build_report.py` exports no PDF.** The `.pdf` beside each `.docx` is a Word
  export done by hand; it needs Word installed and is not part of any build.

### Paths hardcoded to this machine

If the pipeline is moving too, these four defaults assume Andy's drive layout
and will need a path argument or an edit:

| Where | Default | Fix |
|---|---|---|
| `tools/extract_bags.py` | bag root `E:/fuel/D8_2040` | pass the root as `argv[1]` |
| `tools/topic_inventory.py` | bag root `E:/fuel/D8_2040` | pass the root as `argv[1]` |
| `tools/build_topic_doc.js` | writes to `D:\Claude\ROS2\...docx` | pass a path as `argv[2]`, or edit `OUT` |
| `tools/bake_toc.ps1` | same path, duplicated | pass `-Path`, or edit the default |

The first two take an argument, so they need no edit. The last two are the same
path written twice — **change one and you must change the other**, or the bake
step will silently update a different file than the build wrote.

## 6. If something goes wrong

**"Python 3.9 or newer was not found"** — Python is missing or not on `PATH`.
Install it, ticking "Add python.exe to PATH". Reopen the console afterwards;
`PATH` changes do not reach an already-open window.

**"Could not find server.py next to this script"** — `start_planner.bat` was
moved away from the project. It must sit in the same folder as `server.py`;
that is how it finds everything else. Move it back and re-run
`make_shortcut.ps1`.

**The shortcut opens a console that closes instantly** — it should never do
this; the batch file ends in `pause`. If it happens, the shortcut is pointing at
something else. Re-run `make_shortcut.ps1`.

**`OSError: [WinError 10048] ... address already in use`** — something else has
port 8765, most likely a planner you already have running. Use the existing one,
or start another on a different port with `--port 9000`.

**The browser opens but the page is blank or unstyled** — `ui\` did not travel.
It is three files: `index.html`, `app.js`, `styles.css`. Recopy them.

**Tests fail on the new machine but passed here** — treat `model.json` as
suspect first; a mutation guard failing means a coefficient differs. A partial
or text-mode copy is the usual cause. Re-copy the file in binary/verbatim mode.

**Shortcut lands somewhere other than the visible desktop** — on a
OneDrive-backed profile the real desktop is `...\OneDrive\Desktop`. The script
asks Windows for the redirected path rather than assuming
`%USERPROFILE%\Desktop`, so this should not happen; if it does, pass
`-DesktopPath` explicitly.

---

## What the shortcut actually is

No magic worth hiding:

```
Desktop\DriX Fuel Planner.lnk
    target      <project>\start_planner.bat
    working dir <project>
    icon        <project>\tools\fuel.ico

start_planner.bat
    cd /d "%~dp0"          <- finds the project from its own location
    python server.py %*    <- forwards any arguments through
```

`tools\fuel.ico` is drawn by `tools\make_icon.py` rather than downloaded, so it
carries no licence question and can be regenerated. Everything is plain text you
can read and change.
