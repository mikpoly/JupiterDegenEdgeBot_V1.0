# JupiterDegenEdgeBot V1.0

A Windows-first research and prediction-market bot for **Jupiter Prediction**, with quantitative crypto data, PAPER/SHADOW learning, 5m/15m TIMED direction analysis, adaptive profiles, bounded neural models, local Ollama review, and an SQLite-backed dashboard.

> **Default safety:** the public release starts in **PAPER** mode. Real-money LIVE execution is disabled until manually opted into. No profitability is promised.

## Main capabilities

- Assets: **BTC, ETH, SOL, XRP, HYPE, DOGE, BNB**
- Jupiter YES/NO market discovery and quantitative scoring
- Short-window **UP/DOWN 5m/15m TIMED Direction V2** learning
- Multi-source crypto market data and derived features
- SHADOW predictions, PAPER exploration, adaptive calibration
- Neural research/training + walk-forward validation
- Brier score / log-loss LIVE gates
- Local AI reviewer through **Ollama + Qwen2.5 1.5B Q4_K_M**
- Streamlit dashboard
- Optional Solana/Jupiter LIVE execution with simulation and fail-closed signer checks
- Position reconciliation and claim handling

## Quick install — Windows

Prerequisites: Windows 10/11, Python 3.13 recommended (3.11–3.13 supported by this release), Node.js LTS + npm, Ollama, and a Jupiter Prediction API key.

For a new Windows machine, the easiest path is:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\SETUP_FROM_ZERO_WINDOWS.ps1
notepad .env
```

If Python and Node.js are already installed:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\INSTALL.ps1
.\INSTALL_OLLAMA.ps1 -InstallIfMissing
notepad .env
```

In `.env`, replace:

```env
JUPITER_API_KEY=PASTE_YOUR_JUPITER_API_KEY_HERE
```

Then validate and start:

```powershell
.\DOCTOR.ps1
.\OLLAMA_STATUS.ps1
.\SOURCE_TEST.ps1
.\SCAN_ONCE.ps1
.\START_BOT.ps1
.\DASHBOARD.ps1
```

Dashboard: `http://127.0.0.1:8501`

For an exact copy/paste command sheet, open **`INSTALL_COMMANDS_WINDOWS.txt`**. French documentation is in **`README_FR.md`**.

## Ollama

Official Windows command:

```powershell
irm https://ollama.com/install.ps1 | iex
ollama pull qwen2.5:1.5b-instruct-q4_K_M
```

The default model tag is configurable with `OLLAMA_MODEL`. See `docs/OLLAMA.md`.

## First run behavior

The public ZIP intentionally contains **no database and no trained user models**. A fresh installation creates its own SQLite database and begins learning from scratch. The bot can generate PAPER/SHADOW observations while LIVE remains locked.

The integrated V1.0 TIMED worker keeps 5m/15m learning active even when every asset is temporarily `live_ready=False`. This avoids the calibration deadlock where no new labels could be collected. LIVE candidates remain separately restricted to assets passing the TIMED calibration gate.

## Useful commands

```powershell
.\CHECK_ACTIVITY.ps1
.\TIMED_DIRECTION_STATUS.ps1
.\LEARNING_STATUS.ps1
.\LIVE_STATUS.ps1
.\STOP_BOT.ps1
.\STOP_DASHBOARD.ps1
```

## API key

Get a Jupiter API key from the Jupiter Developer Platform/Portal. The Prediction API uses the `x-api-key` header. The Prediction API is currently beta and subject to breaking changes, so API compatibility may require future updates.

## LIVE mode

Do **not** switch LIVE on just to obtain more trades. Read [`docs/LIVE_TRADING.md`](docs/LIVE_TRADING.md) first. The public defaults are:

```env
TRADING_MODE=paper
AUTO_EXECUTE=false
LIVE_RELEASE_ENABLED=false
TIMED_DIRECTION_LIVE_ENABLED=false
```

## Repository hygiene

Do not commit `.env`, wallet JSON, databases, logs, backups, or model artifacts. The provided `.gitignore` blocks these by default. See `SECURITY.md`.

## Development tests

```powershell
.\RUN_TESTS.ps1
```

## More documentation

- `docs/CONFIGURATION.md` — configuration and safe defaults
- `docs/OLLAMA.md` — local model setup
- `docs/ARCHITECTURE.md` — components and learning/execution separation
- `docs/TROUBLESHOOTING.md` — common Windows/API/Ollama problems
- `docs/LIVE_TRADING.md` — optional real-money mode
- `GET_JUPITER_API_KEY.txt` — API-key setup

## License / disclaimer

No open-source license has been selected in this V1.0 package. Add the license you want before advertising the project as open source. See `DISCLAIMER.md`.
