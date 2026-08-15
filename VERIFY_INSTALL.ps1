$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
$fail=0
Write-Host '=== VERIFY JupiterDegenEdgeBot V1.0 ===' -ForegroundColor Cyan
if(Test-Path '.\.venv\Scripts\python.exe'){
    Write-Host 'Python venv: OK' -ForegroundColor Green
    & '.\.venv\Scripts\python.exe' -m compileall -q jupiterdegenbot
    if($LASTEXITCODE -ne 0){Write-Host 'Compilation package: FAIL' -ForegroundColor Red;$fail++}
    & '.\.venv\Scripts\python.exe' -m py_compile dashboard.py TIMED_DIRECTION_STATUS.py
    if($LASTEXITCODE -ne 0){Write-Host 'Compilation dashboard/status: FAIL' -ForegroundColor Red;$fail++}
}else{Write-Host 'Python venv: MISSING' -ForegroundColor Red;$fail++}
if(Get-Command node -ErrorAction SilentlyContinue){
    Write-Host "Node: $(& node --version)" -ForegroundColor Green
    if(Test-Path '.\node_modules'){
        & node '.\tools\test_signer_policy.mjs'
        if($LASTEXITCODE -ne 0){$fail++}
    }else{Write-Host 'node_modules: MISSING (run INSTALL.ps1)' -ForegroundColor Red;$fail++}
}else{Write-Host 'Node: MISSING' -ForegroundColor Red;$fail++}
if(Test-Path '.\.env'){
    Write-Host '.env: OK' -ForegroundColor Green
    $envText=Get-Content '.\.env'
    if($envText -match '^JUPITER_API_KEY=PASTE_YOUR_JUPITER_API_KEY_HERE$' -or $envText -match '^JUPITER_API_KEY=$'){
        Write-Host 'Jupiter API key: NOT CONFIGURED' -ForegroundColor Yellow
    }
    if($envText -match '^TRADING_MODE=paper$'){Write-Host 'Public safety mode: PAPER' -ForegroundColor Green}
}else{Write-Host '.env: MISSING' -ForegroundColor Red;$fail++}
if(Get-Command ollama -ErrorAction SilentlyContinue){Write-Host 'Ollama command: OK' -ForegroundColor Green}else{Write-Host 'Ollama: MISSING or not in PATH' -ForegroundColor Yellow}
if($fail -gt 0){Write-Host "Verification terminee avec $fail erreur(s)." -ForegroundColor Red;exit 2}
Write-Host 'Installation locale: OK' -ForegroundColor Green
