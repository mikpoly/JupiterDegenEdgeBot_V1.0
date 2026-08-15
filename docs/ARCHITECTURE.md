# Architecture overview

JupiterDegenEdgeBot V1.0 separates observation/learning from real-money execution.

## Data and market discovery

`jupiter.py` discovers Jupiter Prediction events/markets. `crypto_data.py`, `history.py`, `derivatives.py`, `features.py` and related modules collect/normalize quantitative crypto observations for BTC, ETH, SOL, XRP, HYPE, DOGE and BNB.

## Probability and decision engine

`market_parser.py` converts supported contracts into structured market specifications. `probability.py` computes quantitative probabilities, including the dedicated TIMED Direction V2 path for short UP/DOWN markets. `risk.py` applies market-quality, edge, exposure and portfolio constraints.

## Learning

`shadow.py` stores predictions independently of actual order execution. `adaptive.py`, `calibration.py`, `research_ml.py` and `auto_training.py` update bounded calibration/adaptive/neural components using settled labels and validation results.

The V2.5 TIMED worker deliberately keeps SHADOW/PAPER learning active for all configured assets even when no asset is currently LIVE-ready. LIVE eligibility is a separate gate.

## Local AI

`local_ai.py` talks to Ollama. The local model is a reviewer/guard; quantitative probability generation remains in the deterministic/quantitative pipeline.

## Execution and lifecycle

`execution.py`, `wallet.py`, `lifecycle.py` and Node helpers under `tools/` handle optional Solana/Jupiter LIVE execution, position reconciliation, claim discovery, simulation and transaction signing.

Signer validation is fail-closed: if a transaction declares an additional required signer whose signature was not pre-applied, it is not broadcast.

## Storage/dashboard

`storage.py` owns the SQLite schema. `dashboard.py` reads local SQLite state and displays runs, prices, predictions, orders, positions, learning and system status. The public dashboard does not automatically require a wallet RPC call to render.
