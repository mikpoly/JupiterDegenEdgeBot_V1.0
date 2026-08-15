$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
$Model='qwen2.5:1.5b-instruct-q4_K_M'
if(-not(Get-Command ollama -ErrorAction SilentlyContinue)){Write-Host 'OLLAMA: NON INSTALLE' -ForegroundColor Red; Write-Host 'Installation officielle: irm https://ollama.com/install.ps1 | iex'; exit 2}
Write-Host "Ollama: $(& ollama --version)" -ForegroundColor Green
$models=& ollama list
if($models -match [regex]::Escape($Model)){Write-Host "Modele $Model: OK" -ForegroundColor Green}else{Write-Host "Modele $Model: ABSENT" -ForegroundColor Yellow; Write-Host "Lance: ollama pull $Model"}
