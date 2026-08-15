param([switch]$InstallIfMissing)
$ErrorActionPreference='Stop'
$Model='qwen2.5:1.5b-instruct-q4_K_M'
if(-not(Get-Command ollama -ErrorAction SilentlyContinue)){
    if(-not $InstallIfMissing){
        Write-Host 'Ollama n est pas installe.' -ForegroundColor Yellow
        Write-Host 'Commande officielle Windows:' -ForegroundColor Cyan
        Write-Host 'irm https://ollama.com/install.ps1 | iex'
        Write-Host 'Puis relance: .\INSTALL_OLLAMA.ps1'
        exit 2
    }
    Write-Host 'Installation d Ollama depuis le script officiel ollama.com...' -ForegroundColor Cyan
    Invoke-Expression (Invoke-RestMethod 'https://ollama.com/install.ps1')
    $ollamaDir=Join-Path $env:LOCALAPPDATA 'Programs\Ollama'
    if(Test-Path $ollamaDir){$env:PATH="$ollamaDir;$env:PATH"}
}
if(-not(Get-Command ollama -ErrorAction SilentlyContinue)){throw 'Ollama reste introuvable. Ouvre un nouveau PowerShell puis relance.'}
Write-Host "Telechargement/verfication du modele local: $Model" -ForegroundColor Cyan
& ollama pull $Model
if($LASTEXITCODE -ne 0){throw 'ollama pull a echoue.'}
Write-Host 'Ollama et modele local prets.' -ForegroundColor Green
