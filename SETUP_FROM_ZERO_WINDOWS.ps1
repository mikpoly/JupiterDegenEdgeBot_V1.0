$ErrorActionPreference='Stop'
Set-ExecutionPolicy -Scope Process Bypass -Force
$Root=$PSScriptRoot
Set-Location $Root
Write-Host '=== JupiterDegenEdgeBot V1.0 - SETUP WINDOWS DEPUIS ZERO ===' -ForegroundColor Cyan

function Refresh-Path {
    $machine=[Environment]::GetEnvironmentVariable('Path','Machine')
    $user=[Environment]::GetEnvironmentVariable('Path','User')
    $env:Path="$machine;$user"
}

if(-not(Get-Command winget -ErrorAction SilentlyContinue)){
    throw 'winget introuvable. Installe/actualise App Installer depuis Microsoft Store, puis relance ce script. Sinon suis INSTALL_COMMANDS_WINDOWS.txt pour une installation manuelle.'
}

if(-not(Get-Command py -ErrorAction SilentlyContinue) -and -not(Get-Command python -ErrorAction SilentlyContinue)){
    Write-Host 'Installation de Python 3.13 (winget)...' -ForegroundColor Cyan
    & winget install --id Python.Python.3.13 --exact --source winget --accept-source-agreements --accept-package-agreements
    if($LASTEXITCODE -ne 0){throw 'Installation Python via winget echouee.'}
    Refresh-Path
}else{Write-Host 'Python deja present.' -ForegroundColor Green}

if(-not(Get-Command node -ErrorAction SilentlyContinue)){
    Write-Host 'Installation de Node.js LTS (winget)...' -ForegroundColor Cyan
    & winget install --id OpenJS.NodeJS.LTS --exact --source winget --accept-source-agreements --accept-package-agreements
    if($LASTEXITCODE -ne 0){throw 'Installation Node.js via winget echouee.'}
    Refresh-Path
}else{Write-Host "Node deja present: $(& node --version)" -ForegroundColor Green}

if(-not(Get-Command ollama -ErrorAction SilentlyContinue)){
    Write-Host 'Installation d Ollama depuis le script officiel ollama.com...' -ForegroundColor Cyan
    Invoke-Expression (Invoke-RestMethod 'https://ollama.com/install.ps1')
    Refresh-Path
    $ollamaDir=Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
    if(Test-Path $ollamaDir){$env:Path="$ollamaDir;$env:Path"}
}else{Write-Host 'Ollama deja present.' -ForegroundColor Green}

Write-Host ''
Write-Host 'Installation des dependances du bot...' -ForegroundColor Cyan
& '.\INSTALL.ps1'
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& '.\INSTALL_OLLAMA.ps1'
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}

Write-Host ''
Write-Host 'SETUP DEPUIS ZERO TERMINE.' -ForegroundColor Green
Write-Host 'Etape obligatoire suivante: notepad .env' -ForegroundColor Yellow
Write-Host 'Renseigne JUPITER_API_KEY, puis lance .\VERIFY_INSTALL.ps1 et .\SOURCE_TEST.ps1.' -ForegroundColor Yellow
