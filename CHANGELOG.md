# Changelog

## 1.0.0 — 2026-08-15

First public release.

- Jupiter Prediction market discovery and YES/NO analysis.
- BTC, ETH, SOL, XRP, HYPE, DOGE, BNB quantitative data pipeline.
- 5m/15m TIMED direction V2 SHADOW/PAPER learning.
- Integrated TIMED FAST V2.5 learning fix: learning continues for all configured assets even if none is LIVE-ready, while LIVE remains restricted to calibrated assets.
- Adaptive profiles, shadow learning, neural training, walk-forward validation, Brier/log-loss gates.
- Local Ollama review with `qwen2.5:1.5b-instruct-q4_K_M`.
- Streamlit dashboard and PowerShell start/stop/status scripts.
- Jupiter position claim discovery fallback.
- Fail-closed signer policy for sponsored/multi-signer Solana transactions.
- Public package contains no user wallet, API key, SQLite history, logs, or trained user data.
