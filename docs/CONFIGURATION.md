# Configuration

Installation creates `.env` from `.env.example` if it does not already exist. `.env` is ignored by Git.

## Minimum required configuration

Set your Jupiter API key:

```env
JUPITER_API_KEY=YOUR_KEY_HERE
```

The public release starts in PAPER mode:

```env
TRADING_MODE=paper
AUTO_EXECUTE=false
LIVE_RELEASE_ENABLED=false
```

Keep those values while learning, testing the dashboard, and validating your data sources.

## Assets and timeframes

Default assets: BTC, ETH, SOL, XRP, HYPE, DOGE, BNB. Default crypto timeframes: 5m, 15m, 1h, 4h, 1d. The TIMED direction subsystem watches Jupiter short-duration markets and keeps SHADOW/PAPER learning active independently of LIVE eligibility.

## External data

CoinGecko is enabled and may work without a key depending on endpoint/rate policy; an optional key can be added. CoinMarketCap is disabled by default. Exchange public market data is used without private exchange API credentials.

## Database

SQLite is created automatically at `data/jupiter_degen.db`. It is not included in the public ZIP. A fresh installation starts with no trained history and must accumulate its own observations, labels, adaptive profiles, and model artifacts.
