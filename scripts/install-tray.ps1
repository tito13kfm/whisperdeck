# Adds the WhisperDeck tray icon (tray.ps1) to the current user's Startup
# folder and starts it now. -Uninstall removes the shortcut; it leaves a
# running tray alone (use its Quit item).
param([switch]$Uninstall)

$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'WhisperDeck Tray.lnk'

if ($Uninstall) {
    if (Test-Path $lnk) { Remove-Item $lnk; "Removed $lnk" } else { "Not installed" }
    return
}

$tray = Join-Path $PSScriptRoot 'tray.ps1'
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnk)
$sc.TargetPath = $ps
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File `"$tray`""
$sc.WorkingDirectory = $PSScriptRoot
$sc.WindowStyle = 7 # minimized, so the console never flashes up before -WindowStyle Hidden applies
$sc.Description = 'WhisperDeck server tray icon'
$sc.Save()
"Installed $lnk"

Start-Process -FilePath $lnk
