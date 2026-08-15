param([switch]$SkipNode)
$ErrorActionPreference='Stop'
Set-ExecutionPolicy -Scope Process Bypass -Force
$Root=$PSScriptRoot
Set-Location $Root
Write-Host '=== JupiterDegenEdgeBot V1.0 - INSTALLATION ===' -ForegroundColor Cyan

function Test-PythonCmd {
    param([string]$Exe,[string[]]$PrefixArgs=@())
    try {
        $out = & $Exe @PrefixArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
        if($LASTEXITCODE -eq 0){ return ([string]$out).Trim() }
    } catch {}
    return $null
}

$PyExe=$null
$PyArgs=@()
if(Get-Command py -ErrorAction SilentlyContinue){
    foreach($selector in @('-3.13','-3.12','-3.11')){
        $v=Test-PythonCmd -Exe 'py' -PrefixArgs @($selector)
        if($v){ $PyExe='py'; $PyArgs=@($selector); break }
    }
}
if(-not $PyExe -and (Get-Command python -ErrorAction SilentlyContinue)){
    $v=Test-PythonCmd -Exe 'python'
    if($v){
        $parts=$v.Split('.')
        if([int]$parts[0]-eq 3 -and [int]$parts[1]-ge 11 -and [int]$parts[1]-le 13){
            $PyExe='python'; $PyArgs=@()
        }
    }
}
if(-not $PyExe){
    throw 'Python 3.11-3.13 introuvable. Python 3.13 est recommande pour V1.0. Lance SETUP_FROM_ZERO_WINDOWS.ps1 ou installe Python puis relance.'
}
$v=Test-PythonCmd -Exe $PyExe -PrefixArgs $PyArgs
Write-Host "Python detecte: $v" -ForegroundColor Green

if(-not(Test-Path '.\.venv\Scripts\python.exe')){
    & $PyExe @PyArgs -m venv .venv
    if($LASTEXITCODE -ne 0){throw 'Creation .venv echouee.'}
}
$Python=Join-Path $Root '.venv\Scripts\python.exe'
& $Python -m pip install --upgrade pip setuptools wheel
if($LASTEXITCODE -ne 0){throw 'Mise a jour pip/setuptools/wheel echouee.'}
& $Python -m pip install -r requirements.txt
if($LASTEXITCODE -ne 0){throw 'Installation des dependances Python echouee.'}

if(-not $SkipNode){
    if(-not(Get-Command node -ErrorAction SilentlyContinue)){throw 'Node.js introuvable. Lance SETUP_FROM_ZERO_WINDOWS.ps1 ou installe Node.js LTS puis relance INSTALL.ps1.'}
    if(-not(Get-Command npm -ErrorAction SilentlyContinue)){throw 'npm introuvable.'}
    Write-Host "Node: $(& node --version)" -ForegroundColor Green
    & npm install --no-audit --no-fund
    if($LASTEXITCODE -ne 0){throw 'npm install a echoue.'}
    & node '.\tools\test_signer_policy.mjs'
    if($LASTEXITCODE -ne 0){throw 'Self-test signer policy echoue.'}
}

New-Item -ItemType Directory -Force data,'data\cache','data\models',logs,exports,backups,wallet,'.streamlit' | Out-Null
if(-not(Test-Path '.\.env')){
    Copy-Item '.\.env.example' '.\.env'
    Write-Host '.env cree depuis .env.example (PAPER par defaut).' -ForegroundColor Yellow
}else{
    Write-Host '.env existant conserve.' -ForegroundColor Yellow
}

& $Python -m compileall -q jupiterdegenbot
if($LASTEXITCODE -ne 0){throw 'Compilation Python echouee.'}
& $Python -m py_compile dashboard.py TIMED_DIRECTION_STATUS.py
if($LASTEXITCODE -ne 0){throw 'Compilation dashboard/status echouee.'}
& $Python -c "from jupiterdegenbot.config import Settings; from jupiterdegenbot.storage import DB; s=Settings(); s.ensure_dirs(); DB(s.database_path); print('SQLite schema: OK')"
if($LASTEXITCODE -ne 0){throw 'Initialisation SQLite echouee.'}

Write-Host ''
Write-Host 'INSTALLATION LOGICIEL TERMINEE.' -ForegroundColor Green
Write-Host '1) Ollama: .\INSTALL_OLLAMA.ps1 -InstallIfMissing' -ForegroundColor Cyan
Write-Host '2) Cle Jupiter: notepad .env' -ForegroundColor Cyan
Write-Host '3) Verification: .\VERIFY_INSTALL.ps1 ; .\DOCTOR.ps1 ; .\SOURCE_TEST.ps1' -ForegroundColor Cyan
Write-Host '4) Premier scan: .\SCAN_ONCE.ps1' -ForegroundColor Cyan
Write-Host '5) Demarrage: .\START_BOT.ps1 ; .\DASHBOARD.ps1' -ForegroundColor Cyan
Write-Host 'PAPER est le mode public par defaut. INSTALL.ps1 n active aucun ordre reel.' -ForegroundColor Yellow
