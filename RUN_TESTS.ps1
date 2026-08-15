$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
$Python='.\.venv\Scripts\python.exe'
if(-not(Test-Path $Python)){throw 'Lance INSTALL.ps1.'}
& $Python -m pip install -r requirements-dev.txt
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& $Python -m pytest -q
if($LASTEXITCODE -ne 0){exit $LASTEXITCODE}
& node '.\tools\test_signer_policy.mjs'
