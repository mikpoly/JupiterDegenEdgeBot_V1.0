# JupiterDegenEdgeBot V1.0 — First Public Release

First public release of JupiterDegenEdgeBot.

## Highlights

- Jupiter Prediction YES/NO scanning and PAPER execution
- BTC, ETH, SOL, XRP, HYPE, DOGE and BNB market-data pipeline
- TIMED Direction V2 for short 5m/15m UP/DOWN markets
- V2.5 learning gate fix: SHADOW/PAPER TIMED learning continues for all configured assets even when LIVE readiness is false
- Adaptive profiles, Brier/log-loss tracking and bounded neural research
- Local Ollama review with Qwen2.5 1.5B Q4_K_M
- Streamlit dashboard
- Solana transaction simulation and fail-closed signer validation
- Position reconciliation and claim discovery fallback

## Safety

The public configuration starts in PAPER mode. LIVE execution, TIMED LIVE and auto-claim are disabled by default. Real-money trading can lose the entire stake and is not guaranteed to be profitable.

## Install

For a fresh Windows machine, run `SETUP_FROM_ZERO_WINDOWS.ps1` or follow `INSTALL_COMMANDS_WINDOWS.txt`.
