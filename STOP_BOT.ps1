$ErrorActionPreference='Stop'
$Root=$PSScriptRoot
Set-Location $Root
$PidFile=Join-Path $Root 'data\bot.pid'

$targets=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($Root) -and $_.CommandLine -match '-m\s+jupiterdegenbot\s+run'
})
if($targets.Count -eq 0){
    if(Test-Path $PidFile){Remove-Item $PidFile -Force}
    Write-Host 'Aucun processus bot actif pour ce dossier.' -ForegroundColor Yellow
    exit 0
}

# Descendants tend to have the highest/deeper parent relationship. Repeat until none remain.
$remaining=$targets
while($remaining.Count -gt 0){
    $ids=@($remaining | Select-Object -ExpandProperty ProcessId)
    $parents=@($remaining | Select-Object -ExpandProperty ParentProcessId)
    $leaves=@($remaining | Where-Object { $parents -notcontains $_.ProcessId })
    if($leaves.Count -eq 0){$leaves=$remaining}
    foreach($proc in $leaves){
        Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Processus bot arrete PID $($proc.ProcessId)" -ForegroundColor Yellow
    }
    Start-Sleep -Milliseconds 200
    $remaining=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($Root) -and $_.CommandLine -match '-m\s+jupiterdegenbot\s+run'
    })
}
if(Test-Path $PidFile){Remove-Item $PidFile -Force}
Write-Host 'Famille bot arretee.' -ForegroundColor Green
