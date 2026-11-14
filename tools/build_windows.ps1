# Build Z1PCB.exe (PyInstaller, one folder) and the installer (Inno Setup 6). Run from the repo root on Windows.
# Packages come only from .\wheels, checked against the hashes in wheels\requirements.lock (tools/fetch_wheels.sh).
#   -Test  also run the test suite, install the result silently and self-test the installed app
param([switch]$Test)
$ErrorActionPreference = 'Stop'
function Check($what) { if ($LASTEXITCODE) { throw "$what failed ($LASTEXITCODE)" } }
$ver = (Select-String -Path z1pcb\__init__.py -Pattern "__version__ = '(.+)'").Matches[0].Groups[1].Value
Write-Host "Version $ver"
$py = '.\.venv\Scripts\python.exe'
Remove-Item -Recurse -Force .venv, build, dist -ErrorAction SilentlyContinue
& 'C:\Program Files\Python313\python.exe' -m venv .venv; Check 'venv'
& $py -m pip install -q --no-index --find-links wheels --require-hashes --no-deps -r wheels\requirements.lock; Check 'pip install'
if ($Test) {
    $env:QT_QPA_PLATFORM = 'offscreen'; $env:QT_QPA_FONTDIR = 'C:\Windows\Fonts'
    & $py -m pytest -q -n auto -p no:cacheprovider tests; Check 'tests'  # in parallel on every core
}
& $py -m PyInstaller --noconfirm --clean --windowed --name Z1PCB --icon assets\z1pcb.ico `
    --add-data 'assets\z1pcb.png;assets' -p . --exclude-module tkinter --exclude-module pytest `
    --log-level WARN tools\z1pcb_app.py; Check 'PyInstaller'
& $py tools\collect_licenses.py dist\Z1PCB\licenses; Check 'license collection'
$env:Z1PCB_VERSION = $ver
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" /Q installer\z1pcb.iss; Check 'ISCC'
$setup = "dist\Z1PCB-$ver-setup.exe"
"{0}  {1:N1} MB  sha256 {2}" -f $setup, ((Get-Item $setup).Length / 1MB), (Get-FileHash $setup -Algorithm SHA256).Hash.ToLower()
if ($Test) {
    Get-Process Z1PCB -ErrorAction SilentlyContinue | Stop-Process -Force
    $p = Start-Process $setup -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -PassThru
    $p.WaitForExit(); if ($p.ExitCode) { throw "installer exit $($p.ExitCode)" }
    Start-Sleep 3; Get-Process Z1PCB -ErrorAction SilentlyContinue | Stop-Process -Force   # the installer starts the app
    $exe = "$env:LOCALAPPDATA\Programs\Z1 PCB\Z1PCB.exe"
    New-Item -ItemType Directory -Force out | Out-Null
    $p = Start-Process $exe -ArgumentList '--selftest', 'tests\data\ir_lista', '--screenshot', 'out\installed.png', '--sim', '0.5' -PassThru
    $p.WaitForExit(); "installed self-test exit: " + $p.ExitCode; if ($p.ExitCode) { throw 'installed self-test failed' }
    $p = Start-Process $exe -ArgumentList '--update-check', 'out\https.txt' -PassThru
    $p.WaitForExit(); "update check over HTTPS: " + (Get-Content out\https.txt); if ($p.ExitCode) { throw 'HTTPS update check failed' }
}
