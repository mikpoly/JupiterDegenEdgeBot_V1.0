$ErrorActionPreference='Stop'
$Root=$PSScriptRoot
Set-Location $Root
$targets=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($Root) -and $_.CommandLine -match 'streamlit.*dashboard\.py'
})
foreach($proc in $targets){Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Dashboard arrete PID $($proc.ProcessId)" -ForegroundColor Yellow}
$PidFile=Join-Path $Root 'data\dashboard.pid'
if(Test-Path $PidFile){Remove-Item $PidFile -Force}
if($targets.Count -eq 0){Write-Host 'Aucun dashboard actif pour ce dossier.' -ForegroundColor Yellow}
