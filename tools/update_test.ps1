# End-to-end update test on Windows: installed old version -> local release server -> the app downloads,
# verifies the signature and hash, installs the new version and starts it again.
#   -NewSetup  dist\Z1PCB-x.y.z-setup.exe (its .sig next to it)
#   -Expect    exit code expected from the app: 0 installer started, 3 release refused, 4 download refused, 6 no update
param([string]$NewSetup, [int]$Expect = 0)
$ErrorActionPreference = 'Stop'
$py = '.\.venv\Scripts\python.exe'
$srv = Start-Process $py -ArgumentList @('tools\mock_release.py', $NewSetup, '8765') -PassThru -WindowStyle Hidden
Start-Sleep 2
$installed = { (Get-ChildItem HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall | Get-ItemProperty | ? DisplayName -like 'Z1 PCB*').DisplayVersion }
"before: " + (& $installed)
$env:QT_QPA_PLATFORM = 'offscreen'; $env:QT_QPA_FONTDIR = 'C:\Windows\Fonts'
$app = Start-Process "$env:LOCALAPPDATA\Programs\Z1 PCB\Z1PCB.exe" -ArgumentList @('--update-test', 'http://127.0.0.1:8765/latest') -PassThru
$app.WaitForExit(90000) | Out-Null
"app exit: " + $app.ExitCode
$deadline = (Get-Date).AddSeconds(240)
do { Start-Sleep 3; $setup = Get-Process | ? { $_.ProcessName -like 'Z1PCB-*-setup*' -or $_.ProcessName -like 'is-*' } } while ($setup -and (Get-Date) -lt $deadline)
Start-Sleep 3
"after: " + (& $installed)
"relaunched app running: " + [bool](Get-Process Z1PCB -ErrorAction SilentlyContinue)
Get-Process Z1PCB -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-Process -Id $srv.Id -Force
if ($app.ExitCode -ne $Expect) { throw "app exit $($app.ExitCode), expected $Expect" }
