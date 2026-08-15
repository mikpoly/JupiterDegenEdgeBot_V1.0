$ErrorActionPreference='Stop'
$Root=$PSScriptRoot
Set-Location $Root
$Python=Join-Path $Root '.venv\Scripts\python.exe'
if(-not(Test-Path $Python)){throw 'Lance INSTALL.ps1.'}
if(-not(Test-Path '.\.env')){throw '.env absent. Lance INSTALL.ps1.'}
New-Item -ItemType Directory -Force logs,data | Out-Null

$existing=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($Root) -and $_.CommandLine -match '-m\s+jupiterdegenbot\s+run'
}
if($existing){
    $ids=($existing | Select-Object -ExpandProperty ProcessId) -join ', '
    throw "Une famille du bot est deja active pour ce dossier (PID: $ids). Lance STOP_BOT.ps1 avant de redemarrer."
}

$PidFile=Join-Path $Root 'data\bot.pid'
if(Test-Path $PidFile){Remove-Item $PidFile -Force}
$p=Start-Process -FilePath $Python -ArgumentList @('-m','jupiterdegenbot','run') -WorkingDirectory $Root -RedirectStandardOutput "$Root\logs\bot.out.log" -RedirectStandardError "$Root\logs\bot.err.log" -PassThru -WindowStyle Hidden
$p.Id | Set-Content $PidFile
Write-Host "Bot V1.0 demarre PID $($p.Id)" -ForegroundColor Green
Write-Host 'Logs: logs\bot.out.log et logs\bot.err.log' -ForegroundColor DarkGray
