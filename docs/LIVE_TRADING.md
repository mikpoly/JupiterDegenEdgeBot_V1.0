# Optional LIVE trading

**V1.0 ships with LIVE disabled.** PAPER/SHADOW mode is the supported first-run path. Real-money prediction markets can lose the entire amount staked. This project does not promise profitability.

Jupiter Prediction is a beta API and can change. Before enabling LIVE, ensure the bot has accumulated/validated its own calibration data and that `LIVE_DOCTOR.ps1` reports the relevant gates as passed.

## Wallet

Use a dedicated Solana wallet with only the funds you intend to risk. Never commit `wallet/bot-keypair.json`. You can either copy a compatible Solana JSON keypair to that path or generate a new local wallet:

```powershell
.\CREATE_WALLET.ps1
.\WALLET_BALANCE.ps1
```

Fund it manually with enough SOL for transaction fees and the supported deposit token(s). Jupiter documentation currently states that Prediction orders use USDC or JupUSD and that the minimum order is $5.

## Manual opt-in

Do not enable these values until you have reviewed every risk limit in `.env`:

```env
TRADING_MODE=live
AUTO_EXECUTE=true
LIVE_RELEASE_ENABLED=true
LIVE_ALLOWED_BY_VERSION=true
LIVE_CONFIRMATION=I_ACCEPT_REAL_MONEY_RISK
MICRO_LIVE_ENABLED=true
MICRO_LIVE_CONFIRMATION=I_ACCEPT_MICRO_LIVE_5_USD_RISK
TIMED_DIRECTION_LIVE_ENABLED=true
AUTO_CLAIM_ENABLED=true
```

Then run:

```powershell
.\LIVE_DOCTOR.ps1
.\LIVE_STATUS.ps1
```

If gates are blocked, **do not lower Brier/log-loss/quality thresholds just to force orders**. The TIMED worker continues SHADOW/PAPER learning even while no asset is LIVE-ready.

## Signer safety

The Node signer verifies every required Solana signer. The bot will not send a transaction if Jupiter returns an additional required signer without a pre-applied signature. Do not remove this check.
