# WhisperDeck tray icon: starts the server and lives in the notification area.
# Right-click for Open / Start / Stop / Restart / Open log / Quit; double-click
# opens the page. install-tray.ps1 adds this to the Startup folder so the server
# comes up at login.
#
# Written for Windows PowerShell 5.1 (always present), so no PS7-only syntax.
# Keep this file ASCII: 5.1 reads a BOM-less file as the ANSI code page.

Add-Type -AssemblyName System.Windows.Forms, System.Drawing

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Port = 9781 # app.py's default, overridden by $env:PORT the same way app.py does
if ($env:PORT) { $Port = [int]$env:PORT }
$Url = "http://localhost:$Port"
$Log = Join-Path $Root 'tray-server.log' # *.log is gitignored

# One tray per login session. A second launch (Startup folder plus a manual
# run) would otherwise put two icons up fighting over one port.
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\WhisperDeckTray', [ref]$createdNew)
if (-not $createdNew) { exit }

# Output goes to a file, not a console: unbuffered so the log is current, and
# UTF-8 so a non-ASCII print cannot crash the server on the cp1252 default.
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'

$script:proc = $null   # the cmd.exe this tray started, if any
$script:state = ''

function Test-Listening {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne(300)) { return $false }
        $client.EndConnect($ar)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Start-Server {
    if (Test-Listening) { return }
    if ($script:proc -and -not $script:proc.HasExited) { return }
    # Same dependency check run.bat does first, so pulling a change that adds a
    # requirement does not turn into a silent failed start.
    $inner = "(`"$Python`" -m pip install -q -r requirements.txt & `"$Python`" app.py) > `"$Log`" 2>&1"
    $script:proc = Start-Process -FilePath cmd.exe -ArgumentList '/s', '/c', "`"$inner`"" `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru
    Update-State
}

function Stop-Server {
    # By port, not only by the tracked process, so this also stops a server
    # started some other way (run.bat, a terminal).
    $ids = @()
    foreach ($c in @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) {
        $ids += $c.OwningProcess
    }
    if ($script:proc -and -not $script:proc.HasExited) { $ids += $script:proc.Id }
    foreach ($id in ($ids | Select-Object -Unique)) {
        # /T takes the tree: the venv python.exe is a launcher with the real
        # interpreter as its child. /F because a windowless console process
        # ignores the polite close.
        & taskkill.exe /PID $id /T /F 2>&1 | Out-Null
    }
    $script:proc = $null
    Update-State
}

function New-BadgeIcon([string]$hex) {
    $bmp = New-Object System.Drawing.Bitmap 32, 32
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $g.Clear([System.Drawing.Color]::Transparent)
    $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml($hex))
    $g.FillEllipse($brush, 1, 1, 30, 30)
    $font = New-Object System.Drawing.Font('Segoe UI', 17, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $fmt = New-Object System.Drawing.StringFormat
    $fmt.Alignment = [System.Drawing.StringAlignment]::Center
    $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center
    $g.DrawString('W', $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF 0, 1, 32, 32), $fmt)
    $g.Dispose()
    return [System.Drawing.Icon]::FromHandle($bmp.GetHicon())
}

$icons = @{
    Running  = New-BadgeIcon '#2E9E5B'
    Starting = New-BadgeIcon '#D08A1E'
    Stopped  = New-BadgeIcon '#7A7A7A'
}

$tray = New-Object System.Windows.Forms.NotifyIcon
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$statusItem = $menu.Items.Add('WhisperDeck')
$statusItem.Enabled = $false
[void]$menu.Items.Add('-')
$openItem = $menu.Items.Add('Open WhisperDeck')
$openItem.Font = New-Object System.Drawing.Font($openItem.Font, [System.Drawing.FontStyle]::Bold)
$startItem = $menu.Items.Add('Start server')
$stopItem = $menu.Items.Add('Stop server')
$restartItem = $menu.Items.Add('Restart server')
$logItem = $menu.Items.Add('Open server log')
[void]$menu.Items.Add('-')
$quitItem = $menu.Items.Add('Quit (stops server)')
$tray.ContextMenuStrip = $menu

function Update-State {
    $listening = Test-Listening
    $ours = $script:proc -and -not $script:proc.HasExited
    if ($listening) { $new = 'Running' }
    elseif ($ours) { $new = 'Starting' }
    else { $new = 'Stopped' }

    if ($new -eq $script:state) { return }
    if ($script:state -eq 'Starting' -and $new -eq 'Stopped') {
        $tray.ShowBalloonTip(5000, 'WhisperDeck', 'Server exited before it started listening. See the server log.',
            [System.Windows.Forms.ToolTipIcon]::Warning)
    }
    $script:state = $new
    $tray.Icon = $icons[$new]
    $tray.Text = "WhisperDeck: $($new.ToLower()) (port $Port)"
    $statusItem.Text = "WhisperDeck: $($new.ToLower())"
    $openItem.Enabled = $listening
    $startItem.Enabled = ($new -eq 'Stopped')
    $stopItem.Enabled = ($new -ne 'Stopped')
    $restartItem.Enabled = ($new -eq 'Running')
}

$openItem.Add_Click({ Start-Process $Url })
$startItem.Add_Click({ Start-Server })
$stopItem.Add_Click({ Stop-Server })
$restartItem.Add_Click({ Stop-Server; Start-Sleep -Milliseconds 500; Start-Server })
$logItem.Add_Click({ if (Test-Path $Log) { Start-Process notepad.exe $Log } })
$quitItem.Add_Click({
    Stop-Server
    $tray.Visible = $false
    [System.Windows.Forms.Application]::Exit()
})
$tray.Add_MouseDoubleClick({ if (Test-Listening) { Start-Process $Url } })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({ Update-State })

$tray.Visible = $true
Update-State
Start-Server
$timer.Start()
[System.Windows.Forms.Application]::Run()

$timer.Stop()
$tray.Dispose()
$mutex.ReleaseMutex()
