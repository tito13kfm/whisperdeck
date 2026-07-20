param(
    [string]$Port = "9786",
    [string]$DataDir = "$env:TEMP\whisperdeck-test-$(Get-Random)"
)

$env:PORT = $Port
$env:WHISPERDECK_DATA_DIR = $DataDir
$env:HUGGINGFACE_TOKEN = $env:HUGGINGFACE_TOKEN

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

Set-Location "C:\Claude\whisperdesk\whisperdesk"
python app.py