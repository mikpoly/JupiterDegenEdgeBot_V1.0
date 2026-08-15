# Troubleshooting — Windows

## `python`, `node`, `npm` or `ollama` is not recognized

Close PowerShell, open a new PowerShell window in the project folder and retry. If the command is still missing, run `SETUP_FROM_ZERO_WINDOWS.ps1` or install the missing prerequisite manually using `INSTALL_COMMANDS_WINDOWS.txt`.

## `winget` is not recognized

Install/update **App Installer** from Microsoft Store, or install Python and Node.js manually from their official sites. `winget` is only used by the convenience from-zero setup; the bot itself does not require winget at runtime.

## Jupiter returns 401 / 403

Check `JUPITER_API_KEY` in `.env`. Make sure the placeholder was replaced and there are no extra quotes/spaces. Some Jupiter Prediction endpoints may also be unavailable in restricted regions.

## Jupiter returns 429

The API is rate limiting requests. The bot has pacing/retry controls; do not remove them. Wait for the cooldown instead of creating multiple bot instances.

## Ollama model missing

```powershell
ollama pull qwen2.5:1.5b-instruct-q4_K_M
.\OLLAMA_STATUS.ps1
```

If Ollama is installed but the local API is not responding, open the Ollama application or run `ollama serve` in another terminal.

## Dashboard does not open

Run:

```powershell
.\STOP_DASHBOARD.ps1
.\DASHBOARD.ps1
```

Then open `http://127.0.0.1:8501`. Check `logs\dashboard.err.log` if it still fails.

## Bot says another instance is running

Run:

```powershell
.\STOP_BOT.ps1
.\START_BOT.ps1
```

The scripts identify only the bot family launched from the current project directory, including a child Python process when Windows/venv creates one.

## Fresh install has no models / no learning statistics

This is normal. The public package contains no private database or trained user artifacts. A new installation starts its own SQLite database and must collect/resolve its own PAPER/SHADOW labels.

## TIMED LIVE is false

TIMED learning is deliberately separated from LIVE eligibility. `TIMED_DIRECTION_LIVE_ENABLED=false` is the public default. Even after it is enabled manually, each asset must still satisfy its calibration gates before LIVE use.

## Claim transaction reports an additional unsigned signer

Do not disable `tools/signer_policy.mjs`. The transaction is intentionally rejected before broadcast when a required non-local signature is missing. Retry later or use the platform's normal claim path; never fabricate/skip a required Solana signature.
