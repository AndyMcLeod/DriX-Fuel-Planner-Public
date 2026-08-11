@echo off
setlocal
title DriX Fuel Planner

REM Launch the fuel planner UI. This is what the desktop shortcut runs.
REM
REM It locates the project from ITS OWN LOCATION (%~dp0), not a hardcoded path, so
REM the folder can be moved, copied to another machine or cloned elsewhere and this
REM still works - only the desktop shortcut's target would need repointing, and
REM tools\make_shortcut.ps1 does that for you.
cd /d "%~dp0"
if not exist "server.py" goto :nofile

REM Go through PATH rather than a pinned interpreter. The Store build of Python
REM lives under a VERSION-STAMPED directory (...Python.3.11_qbz5n2kfra8p0...), so
REM naming the exe outright would break on the next Python upgrade; "python" keeps
REM resolving. "py" is the fallback for a standard python.org install whose
REM installer added the launcher but not python.exe to PATH.
where python >nul 2>&1 && (set PY=python) || (set PY=py)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if errorlevel 1 goto :nopython

echo  Starting the DriX fuel planner.
echo  The planner will open in your browser at http://127.0.0.1:8765
echo.
echo  Leave this window open - it IS the server. Close it, or press
echo  Ctrl+C, to stop the planner.
echo.
REM %* forwards anything passed through, so this one launcher also serves a variant
REM shortcut: --port 9000 for a second planner beside this one, --no-open to start
REM without a browser. The desktop shortcut passes nothing.
%PY% server.py %*
goto :done

:nofile
echo  Could not find server.py next to this script.
echo    looked in: %CD%
echo  If the project moved, move start_planner.bat with it - it must stay in
echo  the same folder as server.py.
goto :done

:nopython
echo  Python 3.9 or newer was not found on this machine.
echo.
echo  Install it from https://www.python.org/downloads/ or the Microsoft Store,
echo  and tick "Add python.exe to PATH" if the installer offers it.
echo  Nothing else needs installing - the planner is standard library only.

:done
echo.
echo  The DriX fuel planner has stopped.
pause
