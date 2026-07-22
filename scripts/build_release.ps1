<#
Rebuilds the WhisperDeck portable zip from the current working tree.
Run with no arguments: powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot   = Split-Path -Parent $PSScriptRoot
$CacheDir   = Join-Path $PSScriptRoot ".build-cache"
$BuildDir   = Join-Path $RepoRoot "dist\WhisperDeck-portable"
$DistDir    = Join-Path $RepoRoot "dist"
$PythonVer  = "3.13.1"
$PythonUrl  = "https://www.python.org/ftp/python/$PythonVer/python-$PythonVer-embed-amd64.zip"
$FfmpegUrl  = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
$GetPipUrl  = "https://bootstrap.pypa.io/get-pip.py"

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# --- Fetch (and cache) the embeddable Python runtime ---
$PythonZip = Join-Path $CacheDir "python-embed-$PythonVer.zip"
if (-not (Test-Path $PythonZip)) {
    Write-Host "[*] Downloading embeddable Python $PythonVer..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZip
} else {
    Write-Host "[*] Using cached embeddable Python $PythonVer"
}

# --- Fetch (and cache) the static ffmpeg build ---
$FfmpegZip = Join-Path $CacheDir "ffmpeg-latest-win64-gpl.zip"
if (-not (Test-Path $FfmpegZip)) {
    Write-Host "[*] Downloading ffmpeg..."
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip
} else {
    Write-Host "[*] Using cached ffmpeg build"
}

# --- Fetch (and cache) the pip bootstrapper ---
$GetPipPy = Join-Path $CacheDir "get-pip.py"
if (-not (Test-Path $GetPipPy)) {
    Write-Host "[*] Downloading get-pip.py..."
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipPy
} else {
    Write-Host "[*] Using cached get-pip.py"
}

# --- Fresh build tree ---
if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir }
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

Write-Host "[*] Extracting Python runtime..."
$PythonDir = Join-Path $BuildDir "python"
Expand-Archive -Path $PythonZip -DestinationPath $PythonDir -Force

Write-Host "[*] Bootstrapping pip into embedded Python..."
# The embeddable distribution ships without pip/ensurepip and disables
# site-packages by default (the ._pth file comments out "import site").
# Re-enable site-packages so get-pip.py's install lands somewhere the
# interpreter will actually import from, then bootstrap pip.
$PthFile = Get-ChildItem -Path $PythonDir -Filter "python3*._pth" | Select-Object -First 1
$PthContent = (Get-Content $PthFile.FullName) -replace '^#\s*import site', 'import site'
# The ._pth file also disables PYTHONPATH/registry-based sys.path entries
# (isolation mode), so "app" (containing app.py, database, services,
# backends) has to be added here directly or "import database" etc. inside
# app.py raise ModuleNotFoundError at runtime. Insert it ahead of the
# "import site" line so it takes effect regardless of ordering rules.
$PthContent = $PthContent -replace '^\.$', ".`r`n..\app"
Set-Content -Path $PthFile.FullName -Value $PthContent
& (Join-Path $PythonDir "python.exe") $GetPipPy --no-warn-script-location
if ($LASTEXITCODE -ne 0) { throw "get-pip.py bootstrap failed with exit code $LASTEXITCODE" }

Write-Host "[*] Extracting ffmpeg..."
$FfmpegExtractTmp = Join-Path $CacheDir "ffmpeg-extract-tmp"
if (Test-Path $FfmpegExtractTmp) { Remove-Item -Recurse -Force $FfmpegExtractTmp }
Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtractTmp -Force
$FfmpegBinSrc = Get-ChildItem -Path $FfmpegExtractTmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1 | Select-Object -ExpandProperty DirectoryName
New-Item -ItemType Directory -Force -Path (Join-Path $BuildDir "ffmpeg") | Out-Null
Copy-Item (Join-Path $FfmpegBinSrc "ffmpeg.exe") (Join-Path $BuildDir "ffmpeg\ffmpeg.exe")
Copy-Item (Join-Path $FfmpegBinSrc "ffprobe.exe") (Join-Path $BuildDir "ffmpeg\ffprobe.exe")

Write-Host "[*] Copying app source..."
$AppDest = Join-Path $BuildDir "app"
New-Item -ItemType Directory -Force -Path $AppDest | Out-Null
foreach ($item in @("app.py", "backends", "services", "database", "static", "requirements.txt", "__init__.py")) {
    Copy-Item (Join-Path $RepoRoot $item) $AppDest -Recurse -Force
}

Write-Host "[*] Cleaning __pycache__ and .pyc files..."
Get-ChildItem -Recurse -Path $AppDest -Include __pycache__ -Directory | Remove-Item -Recurse -Force
Get-ChildItem -Recurse -Path $AppDest -Include *.pyc -File | Remove-Item -Force

$VersionLine = Select-String -Path (Join-Path $RepoRoot "app.py") -Pattern 'version="([\d.]+)"' | Select-Object -First 1
$Version = $VersionLine.Matches[0].Groups[1].Value

Write-Host "[*] Writing launcher and README..."
$LauncherTemplate = Get-Content (Join-Path $RepoRoot "scripts\portable-template\WhisperDeck.bat.template") -Raw
$LauncherTemplate.Replace("__PORT__", "9781").Replace("__VERSION__", $Version) | Set-Content -Path (Join-Path $BuildDir "WhisperDeck.bat") -NoNewline
Copy-Item (Join-Path $RepoRoot "scripts\portable-template\README.txt") (Join-Path $BuildDir "README.txt")

# --- Zip ---
$ZipPath = Join-Path $DistDir "WhisperDeck-portable-v$Version.zip"
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }

Write-Host "[*] Zipping to $ZipPath ..."
Compress-Archive -Path (Join-Path $BuildDir "*") -DestinationPath $ZipPath

Write-Host "[*] Done: $ZipPath"
