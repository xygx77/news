param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Target,
    [Parameter(Mandatory = $true, Position = 1)]
    [string]$Out,
    [int]$Width = 1200,
    [int]$Height = 1200,
    [string]$Click,
    [string]$Channel = "chrome",
    [string]$Python
)

# Screenshot wrapper: finds an interpreter that has Playwright, then runs
# scripts/shot.py. The project venv (.venv) does not ship Playwright, so the
# default order is: -Python, $env:DSH_SHOT_PYTHON, the system Python 3.12.
#
# Playwright spawns the browser over a named pipe, which the DSH sandbox
# blocks; call this with full access, e.g.
#   powershell -File scripts\shot.ps1 output\daily\index.html output\index.png

$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "shot.py"

if (-not $Python) { $Python = $env:DSH_SHOT_PYTHON }
if (-not $Python) { $Python = "C:\Users\$env:USERNAME\AppData\Local\Programs\Python\Python312\python.exe" }
if (-not (Test-Path $Python)) {
    $found = Get-Command py -ErrorAction SilentlyContinue
    if ($found) { $Python = (py -3 -c "import sys; print(sys.executable)") }
}
if (-not (Test-Path $Python)) { throw "no Python found; pass -Python <path>" }

$args = @($script, $Target, "--out", $Out, "--width", $Width, "--height", $Height, "--channel", $Channel)
if ($Click) { $args += @("--click", $Click) }

& $Python @args
exit $LASTEXITCODE
