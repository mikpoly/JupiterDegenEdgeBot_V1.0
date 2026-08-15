$ErrorActionPreference='Stop'; Set-Location $PSScriptRoot
if(-not(Test-Path '.\wallet\bot-keypair.json')){throw 'wallet\bot-keypair.json absent. Voir docs\LIVE_TRADING.md.'}
& '.\.venv\Scripts\python.exe' -m jupiterdegenbot wallet-balance
