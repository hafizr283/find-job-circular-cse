$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
New-Item -ItemType Directory -Force logs | Out-Null
$python = (Get-Command python.exe -ErrorAction Stop).Source
& $python -m http.server 8769 --bind 0.0.0.0 *> (Join-Path $root 'logs\site.log')
