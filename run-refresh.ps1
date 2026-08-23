$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $ProjectRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $LogDir "refresh-$stamp.log"

Start-Transcript -Path $log -Append | Out-Null
try {
    Set-Location $ProjectRoot
    $env:PYTHONIOENCODING = 'utf-8'
    python -m unittest -q
    python collector.py
    python ai_review.py queue --batch 40
    if ($LASTEXITCODE -ne 0) { throw "Refresh command failed with exit code $LASTEXITCODE" }
}
finally {
    Stop-Transcript | Out-Null
}
