$ErrorActionPreference='Stop'
$Root=$PSScriptRoot
Set-Location $Root
$Python=Join-Path $Root '.venv\Scripts\python.exe'
if(-not(Test-Path $Python)){throw 'Lance INSTALL.ps1.'}
New-Item -ItemType Directory -Force logs,data | Out-Null
$existing=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($Root) -and $_.CommandLine -match 'streamlit.*dashboard\.py'
})
if($existing.Count -gt 0){
    Write-Host "Dashboard deja actif PID $($existing[0].ProcessId)" -ForegroundColor Yellow
    Start-Process 'http://127.0.0.1:8501'
    exit 0
}
$PidFile=Join-Path $Root 'data\dashboard.pid'
if(Test-Path $PidFile){Remove-Item $PidFile -Force}
$p=Start-Process -FilePath $Python -ArgumentList @('-m','streamlit','run','dashboard.py','--server.address','127.0.0.1','--server.port','8501','--server.headless','true') -WorkingDirectory $Root -RedirectStandardOutput "$Root\logs\dashboard.out.log" -RedirectStandardError "$Root\logs\dashboard.err.log" -PassThru -WindowStyle Hidden
$p.Id | Set-Content $PidFile
Write-Host "Dashboard PID $($p.Id) - http://127.0.0.1:8501" -ForegroundColor Green
Start-Sleep -Seconds 2
Start-Process 'http://127.0.0.1:8501'
