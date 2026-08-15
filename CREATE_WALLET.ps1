$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
if(-not(Get-Command node -ErrorAction SilentlyContinue)){throw 'Node.js introuvable. Installe Node.js LTS.'}
if(-not(Test-Path '.\node_modules')){throw 'node_modules absent. Lance INSTALL.ps1.'}
$Target=Join-Path $PSScriptRoot 'wallet\bot-keypair.json'
if(Test-Path $Target){throw "Wallet deja present: $Target. Aucun ecrasement autorise."}
Write-Host 'ATTENTION: ce fichier contient une cle privee. Ne le publie jamais sur GitHub.' -ForegroundColor Yellow
$confirm=Read-Host 'Tape CREATE pour generer un nouveau wallet local'
if($confirm -ne 'CREATE'){Write-Host 'Annule.'; exit 1}
$pub=& node '.\tools\create_wallet.mjs' $Target
if($LASTEXITCODE -ne 0){throw 'Creation wallet echouee.'}
Write-Host "Wallet cree. Adresse publique: $pub" -ForegroundColor Green
Write-Host "Cle privee enregistree uniquement dans: $Target" -ForegroundColor Yellow
