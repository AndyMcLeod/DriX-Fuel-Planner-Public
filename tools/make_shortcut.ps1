<#
.SYNOPSIS
    Create (or refresh) the "DriX Fuel Planner" desktop shortcut.

.DESCRIPTION
    Run this once after copying the project to a machine, or after moving the
    folder. It points a desktop shortcut at start_planner.bat IN THIS COPY of
    the project, resolved from the script's own location - so there is no path
    to edit and no way for it to point at a folder that has moved.

    Re-running it overwrites the existing shortcut rather than making a second
    one, so it is safe to run whenever you are not sure it is still right.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File tools\make_shortcut.ps1 -Name "Fuel Planner (port 9000)" -Arguments "--port 9000"
    Makes a SECOND shortcut beside the first, on another port. start_planner.bat
    forwards its arguments to server.py, so anything server.py accepts works.

.NOTES
    Creates nothing outside the Desktop folder and writes nothing into the
    project. Delete the .lnk to undo it completely.
#>
[CmdletBinding()]
param(
    # Shortcut file name, without the .lnk extension.
    [string] $Name = 'DriX Fuel Planner',

    # Passed straight through to start_planner.bat -> server.py.
    [string] $Arguments = '',

    # Where to put it. Defaults to the Desktop, which on a OneDrive-backed
    # profile is the redirected OneDrive\Desktop - GetFolderPath resolves that
    # correctly, whereas $env:USERPROFILE\Desktop does not.
    [string] $DesktopPath = [Environment]::GetFolderPath('Desktop')
)

$ErrorActionPreference = 'Stop'

# Resolve the project from THIS SCRIPT's location: tools\ -> repo root.
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root 'start_planner.bat'
$icon = Join-Path $root 'tools\fuel.ico'

if (-not (Test-Path -LiteralPath $target)) {
    throw "start_planner.bat not found at $target - is make_shortcut.ps1 still inside the project's tools\ folder?"
}
if (-not (Test-Path -LiteralPath $DesktopPath)) {
    throw "Desktop folder not found at $DesktopPath - pass -DesktopPath explicitly."
}

$link = Join-Path $DesktopPath "$Name.lnk"
$existed = Test-Path -LiteralPath $link

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($link)
$sc.TargetPath = $target
$sc.Arguments = $Arguments
$sc.WorkingDirectory = $root
$sc.Description = 'DriX-8 mission fuel and endurance planner (opens in your browser)'
$sc.WindowStyle = 1
# Only claim the icon if it is actually there; a missing IconLocation makes
# Explorer fall back to a blank page icon rather than the .bat's own.
if (Test-Path -LiteralPath $icon) { $sc.IconLocation = "$icon,0" }
$sc.Save()

# Verify by reading the shortcut back, rather than trusting Save() - a bad path
# saves happily and only fails when double-clicked.
$check = $shell.CreateShortcut($link)
if ($check.TargetPath -ne $target) {
    throw "Shortcut saved but points at '$($check.TargetPath)' instead of '$target'."
}

Write-Host ("{0} shortcut: {1}" -f $(if ($existed) { 'Updated' } else { 'Created' }), $link)
Write-Host ("  -> {0}" -f $check.TargetPath)
if ($Arguments) { Write-Host ("  args: {0}" -f $Arguments) }
Write-Host '  Double-click it to start the planner.'
