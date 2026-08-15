param([switch]$InstallOllamaIfMissing)
$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
& '.\INSTALL.ps1'
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
if($InstallOllamaIfMissing){& '.\INSTALL_OLLAMA.ps1' -InstallIfMissing}else{& '.\INSTALL_OLLAMA.ps1'}
