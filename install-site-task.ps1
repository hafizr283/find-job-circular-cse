$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $root 'start-site.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
try {
    Register-ScheduledTask -TaskName 'InternBD Website' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force -ErrorAction Stop | Out-Null
    Start-ScheduledTask -TaskName 'InternBD Website' -ErrorAction Stop
    Write-Host 'Website task installed and started at http://localhost:8769'
} catch {
    Write-Error "Could not install or start the website task: $($_.Exception.Message)"
    exit 1
}
