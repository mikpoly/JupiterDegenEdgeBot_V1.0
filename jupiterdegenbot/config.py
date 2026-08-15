from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _project_path(value: str) -> str:
    path = Path(str(value or "").strip()).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)).strip())


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)).strip())


@dataclass(slots=True)
class Settings:
    # Core
    database_path: str = os.getenv("DATABASE_PATH", "data/jupiter_degen.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    scan_interval_minutes: int = _i("SCAN_INTERVAL_MINUTES", 5)
    scan_interval_seconds: float = _f("SCAN_INTERVAL_SECONDS", 0.0)
    min_free_memory_gb: float = _f("MIN_FREE_MEMORY_GB", 1.0)
    cache_dir: str = os.getenv("CACHE_DIR", "data/cache")
    http_timeout_seconds: float = _f("HTTP_TIMEOUT_SECONDS", 20.0)
    http_max_attempts: int = _i("HTTP_MAX_ATTEMPTS", 3)

    # Jupiter Prediction API
    jupiter_api_key: str = os.getenv("JUPITER_API_KEY", "")
    jupiter_base_url: str = os.getenv("JUPITER_BASE_URL", "https://api.jup.ag/prediction/v1")
    jupiter_provider: str = os.getenv("JUPITER_PROVIDER", "polymarket")
    jupiter_request_interval_seconds: float = _f("JUPITER_REQUEST_INTERVAL_SECONDS", 1.2)
    jupiter_max_retries: int = _i("JUPITER_MAX_RETRIES", 5)
    max_event_pages: int = _i("MAX_EVENT_PAGES", 12)
    max_events_per_page: int = _i("MAX_EVENTS_PER_PAGE", 50)
    max_markets_fetched: int = _i("MAX_MARKETS_FETCHED", 5000)
    event_filters_raw: str = os.getenv("JUPITER_EVENT_FILTERS", "live,new,trending,upcoming")
    categories_raw: str = os.getenv("JUPITER_CATEGORIES", "")
    min_hours_to_close: float = _f("MIN_HOURS_TO_CLOSE", 0.08)
    paper_min_hours_to_close: float = _f("PAPER_MIN_HOURS_TO_CLOSE", 0.02)
    max_hours_to_close: float = _f("MAX_HOURS_TO_CLOSE", 168.0)
    min_trade_price: float = _f("MIN_TRADE_PRICE", 0.04)
    max_trade_price: float = _f("MAX_TRADE_PRICE", 0.96)
    degen_markets_per_cycle: int = _i("DEGEN_MARKETS_PER_CYCLE", 300)
    include_live_degen_events: bool = _b("JUPITER_INCLUDE_LIVE_DEGEN", True)
    jupiter_degen_base_url: str = os.getenv("JUPITER_DEGEN_BASE_URL", "https://prediction-market-api.jup.ag/api/v1")
    include_timed_crypto_events: bool = _b("JUPITER_INCLUDE_TIMED_CRYPTO", True)
    jupiter_timed_cache_seconds: int = _i("JUPITER_TIMED_CACHE_SECONDS", 600)
    timed_crypto_assets_raw: str = os.getenv("JUPITER_TIMED_ASSETS", "BTC,ETH,SOL,XRP,HYPE,DOGE,BNB")
    timed_crypto_tags_raw: str = os.getenv("JUPITER_TIMED_TAGS", "5m,15m")

    # Conservative per-source rate pacing (seconds between requests)
    source_default_min_interval_seconds: float = _f("SOURCE_DEFAULT_MIN_INTERVAL_SECONDS", 0.35)
    rate_limit_cooldown_seconds: float = _f("RATE_LIMIT_COOLDOWN_SECONDS", 30.0)
    rate_coinbase_min_interval_seconds: float = _f("RATE_COINBASE_MIN_INTERVAL_SECONDS", 0.45)
    rate_binance_min_interval_seconds: float = _f("RATE_BINANCE_MIN_INTERVAL_SECONDS", 0.25)
    rate_kraken_min_interval_seconds: float = _f("RATE_KRAKEN_MIN_INTERVAL_SECONDS", 1.10)
    rate_bybit_min_interval_seconds: float = _f("RATE_BYBIT_MIN_INTERVAL_SECONDS", 0.35)
    rate_hyperliquid_min_interval_seconds: float = _f("RATE_HYPERLIQUID_MIN_INTERVAL_SECONDS", 0.35)
    rate_okx_min_interval_seconds: float = _f("RATE_OKX_MIN_INTERVAL_SECONDS", 0.20)
    rate_kucoin_min_interval_seconds: float = _f("RATE_KUCOIN_MIN_INTERVAL_SECONDS", 0.35)
    rate_coingecko_min_interval_seconds: float = _f("RATE_COINGECKO_MIN_INTERVAL_SECONDS", 6.50)
    rate_coinmarketcap_min_interval_seconds: float = _f("RATE_COINMARKETCAP_MIN_INTERVAL_SECONDS", 12.0)
    rate_jupiter_min_interval_seconds: float = _f("RATE_JUPITER_MIN_INTERVAL_SECONDS", 1.20)

    # Crypto sources
    crypto_assets_raw: str = os.getenv("CRYPTO_ASSETS", "BTC,ETH,SOL,XRP,HYPE,DOGE,BNB")
    crypto_timeframes_raw: str = os.getenv("CRYPTO_TIMEFRAMES", "5m,15m,1h,4h,1d")
    crypto_sources_raw: str = os.getenv("CRYPTO_SOURCES", "coinbase,kraken,binance,bybit,hyperliquid,okx,kucoin")
    okx_api_base_urls_raw: str = os.getenv("OKX_API_BASE_URLS", "https://eea.okx.com,https://www.okx.com")
    kucoin_api_base_urls_raw: str = os.getenv("KUCOIN_API_BASE_URLS", "https://api.kucoin.com,https://api.kucoin.eu")
    crypto_min_sources: int = _i("CRYPTO_MIN_SOURCES", 2)
    crypto_source_cache_seconds: int = _i("CRYPTO_SOURCE_CACHE_SECONDS", 45)
    crypto_candle_limit: int = _i("CRYPTO_CANDLE_LIMIT", 240)
    crypto_max_price_dispersion: float = _f("CRYPTO_MAX_PRICE_DISPERSION", 0.025)
    data_min_source_agreement: float = _f("DATA_MIN_SOURCE_AGREEMENT", 0.68)
    probability_min_sigma: float = _f("PROBABILITY_MIN_SIGMA", 0.002)
    probability_max_sigma: float = _f("PROBABILITY_MAX_SIGMA", 0.35)
    model_prior_weight: float = _f("MODEL_PRIOR_WEIGHT", 0.12)
    require_resolution_source: bool = _b("REQUIRE_RESOLUTION_SOURCE", True)

    # Research / deep history / validation
    research_mode_enabled: bool = _b("RESEARCH_MODE_ENABLED", True)
    research_history_days: int = _i("RESEARCH_HISTORY_DAYS", 730)
    research_history_assets_raw: str = os.getenv("RESEARCH_HISTORY_ASSETS", "BTC,ETH,SOL,XRP,HYPE,DOGE,BNB")
    research_core_assets_raw: str = os.getenv("RESEARCH_CORE_ASSETS", "BTC,ETH,SOL")
    research_primary_timeframes_raw: str = os.getenv("RESEARCH_PRIMARY_TIMEFRAMES", "15m,1h,4h,1d")
    research_page_pause_seconds: float = _f("RESEARCH_PAGE_PAUSE_SECONDS", 0.05)
    research_max_pages_per_source: int = _i("RESEARCH_MAX_PAGES_PER_SOURCE", 500)
    research_min_history_candles: int = _i("RESEARCH_MIN_HISTORY_CANDLES", 800)
    research_drop_incomplete_candle: bool = _b("RESEARCH_DROP_INCOMPLETE_CANDLE", True)
    research_max_missing_ratio: float = _f("RESEARCH_MAX_MISSING_RATIO", 0.015)
    research_min_coverage_ratio: float = _f("RESEARCH_MIN_COVERAGE_RATIO", 0.90)
    research_max_stale_intervals: int = _i("RESEARCH_MAX_STALE_INTERVALS", 3)
    coingecko_enabled: bool = _b("COINGECKO_ENABLED", True)
    coingecko_api_key: str = os.getenv("COINGECKO_API_KEY", "")
    coinmarketcap_enabled: bool = _b("COINMARKETCAP_ENABLED", False)
    coinmarketcap_api_key: str = os.getenv("COINMARKETCAP_API_KEY", "")
    derivatives_enabled: bool = _b("DERIVATIVES_ENABLED", True)
    orderbook_depth_levels: int = _i("ORDERBOOK_DEPTH_LEVELS", 20)
    ensemble_min_timeframes: int = _i("ENSEMBLE_MIN_TIMEFRAMES", 3)
    ensemble_max_probability_spread: float = _f("ENSEMBLE_MAX_PROBABILITY_SPREAD", 0.28)
    neural_enabled: bool = _b("NEURAL_ENABLED", True)
    neural_min_train_samples: int = _i("NEURAL_MIN_TRAIN_SAMPLES", 250)
    neural_hidden_layers: str = os.getenv("NEURAL_HIDDEN_LAYERS", "32,16")
    neural_max_iter: int = _i("NEURAL_MAX_ITER", 300)
    neural_weight_max: float = _f("NEURAL_WEIGHT_MAX", 0.25)
    neural_model_dir: str = os.getenv("NEURAL_MODEL_DIR", "data/models")
    walk_forward_min_train: int = _i("WALK_FORWARD_MIN_TRAIN", 300)
    walk_forward_test_size: int = _i("WALK_FORWARD_TEST_SIZE", 120)
    walk_forward_step: int = _i("WALK_FORWARD_STEP", 120)
    walk_forward_cost_bps: float = _f("WALK_FORWARD_COST_BPS", 35.0)
    live_gate_min_settled: int = _i("LIVE_GATE_MIN_SETTLED", 200)
    live_gate_min_backtest_trades: int = _i("LIVE_GATE_MIN_BACKTEST_TRADES", 500)
    live_gate_max_brier: float = _f("LIVE_GATE_MAX_BRIER", 0.22)
    live_gate_max_log_loss: float = _f("LIVE_GATE_MAX_LOG_LOSS", 0.68)
    live_gate_min_roi_after_costs: float = _f("LIVE_GATE_MIN_ROI_AFTER_COSTS", 0.02)
    live_gate_min_paper_roi: float = _f("LIVE_GATE_MIN_PAPER_ROI", 0.02)
    live_gate_min_brier_skill: float = _f("LIVE_GATE_MIN_BRIER_SKILL", 0.005)
    live_gate_require_neural_model: bool = _b("LIVE_GATE_REQUIRE_NEURAL_MODEL", True)
    live_gate_min_active_neural_models: int = _i("LIVE_GATE_MIN_ACTIVE_NEURAL_MODELS", 1)
    live_gate_require_core_neural_models: bool = _b("LIVE_GATE_REQUIRE_CORE_NEURAL_MODELS", True)
    live_gate_min_quality_sources: int = _i("LIVE_GATE_MIN_QUALITY_SOURCES", 2)
    live_gate_max_drawdown: float = _f("LIVE_GATE_MAX_DRAWDOWN", 0.20)
    live_gate_max_validation_age_hours: float = _f("LIVE_GATE_MAX_VALIDATION_AGE_HOURS", 168.0)
    live_gate_require_all_assets: bool = _b("LIVE_GATE_REQUIRE_ALL_ASSETS", False)
    live_allowed_by_version: bool = _b("LIVE_ALLOWED_BY_VERSION", False)
    # v1.0.0 supports only the explicitly bounded MICRO-LIVE preview.
    # The long-term automatic LIVE gate remains statistical and separate.
    release_live_capable: bool = True
    live_validation_gate_enabled: bool = _b("LIVE_VALIDATION_GATE_ENABLED", True)

    # MICRO-LIVE 24h preview. Disabled by default and activated only by the
    # dedicated PowerShell script with two exact consent phrases.
    micro_live_enabled: bool = _b("MICRO_LIVE_ENABLED", False)
    micro_live_confirmation: str = os.getenv("MICRO_LIVE_CONFIRMATION", "")
    micro_live_started_at: str = os.getenv("MICRO_LIVE_STARTED_AT", "")
    micro_live_expires_at: str = os.getenv("MICRO_LIVE_EXPIRES_AT", "")
    micro_live_max_window_hours: float = _f("MICRO_LIVE_MAX_WINDOW_HOURS", 24.0)
    micro_live_min_paper_settled: int = _i("MICRO_LIVE_MIN_PAPER_SETTLED", 20)
    micro_live_max_paper_brier: float = _f("MICRO_LIVE_MAX_PAPER_BRIER", 0.30)
    micro_live_max_paper_log_loss: float = _f("MICRO_LIVE_MAX_PAPER_LOG_LOSS", 0.90)
    micro_live_min_active_models: int = _i("MICRO_LIVE_MIN_ACTIVE_MODELS", 1)
    live_min_orderbook_depth_multiplier: float = _f("LIVE_MIN_ORDERBOOK_DEPTH_MULTIPLIER", 1.50)
    live_max_estimated_fee_ratio: float = _f("LIVE_MAX_ESTIMATED_FEE_RATIO", 0.20)
    live_max_order_cost_drift_usd: float = _f("LIVE_MAX_ORDER_COST_DRIFT_USD", 0.35)

    # Local AI guard (Ollama) — advisory/veto only, never creates probability
    local_ai_enabled: bool = _b("LOCAL_AI_ENABLED", True)
    local_ai_required_for_new_signal: bool = _b("LOCAL_AI_REQUIRED_FOR_NEW_SIGNAL", False)
    local_ai_allow_veto: bool = _b("LOCAL_AI_ALLOW_VETO", True)
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
    ollama_keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "0")
    local_ai_timeout_seconds: float = _f("LOCAL_AI_TIMEOUT_SECONDS", 180.0)
    local_ai_num_ctx: int = _i("LOCAL_AI_NUM_CTX", 2048)
    local_ai_num_predict: int = _i("LOCAL_AI_NUM_PREDICT", 320)
    local_ai_max_reviews_per_cycle: int = _i("LOCAL_AI_MAX_REVIEWS_PER_CYCLE", 4)
    local_ai_min_horizon_minutes: float = _f("LOCAL_AI_MIN_HORIZON_MINUTES", 20.0)

    # Adaptive quantitative memory — learns only from settled PAPER outcomes
    adaptive_learning_enabled: bool = _b("ADAPTIVE_LEARNING_ENABLED", True)
    adaptive_min_settled: int = _i("ADAPTIVE_MIN_SETTLED", 20)
    adaptive_lookback: int = _i("ADAPTIVE_LOOKBACK", 2000)
    adaptive_max_probability_adjustment: float = _f("ADAPTIVE_MAX_PROBABILITY_ADJUSTMENT", 0.06)
    adaptive_source_weight_min: float = _f("ADAPTIVE_SOURCE_WEIGHT_MIN", 0.65)
    adaptive_source_weight_max: float = _f("ADAPTIVE_SOURCE_WEIGHT_MAX", 1.35)
    history_max_db_gb: float = _f("HISTORY_MAX_DB_GB", 10.0)

    # Continuous learning: every supported market becomes a shadow label.
    shadow_learning_enabled: bool = _b("SHADOW_LEARNING_ENABLED", True)
    shadow_refresh_limit: int = _i("SHADOW_REFRESH_LIMIT", 300)
    shadow_resolution_grace_minutes: int = _i("SHADOW_RESOLUTION_GRACE_MINUTES", 2)
    shadow_individual_fallback_limit: int = _i("SHADOW_INDIVIDUAL_FALLBACK_LIMIT", 5)

    # Fast barrier/touch contracts. Still quantitative and fail-closed.
    touch_model_enabled: bool = _b("TOUCH_MODEL_ENABLED", True)
    touch_model_min_sources: int = _i("TOUCH_MODEL_MIN_SOURCES", 2)
    touch_model_max_horizon_hours: float = _f("TOUCH_MODEL_MAX_HORIZON_HOURS", 168.0)

    # Jupiter timed Up/Down markets are one-sided YES instruments.  They use
    # a dedicated short-horizon direction model and a separate LIVE
    # calibration gate.  PAPER/SHADOW can learn immediately while LIVE stays
    # fail-closed until enough timed labels are actually settled.
    timed_direction_model_enabled: bool = _b("TIMED_DIRECTION_MODEL_ENABLED", True)
    timed_direction_min_sources: int = _i("TIMED_DIRECTION_MIN_SOURCES", 2)
    timed_direction_live_enabled: bool = _b("TIMED_DIRECTION_LIVE_ENABLED", False)
    timed_direction_live_min_settled: int = _i("TIMED_DIRECTION_LIVE_MIN_SETTLED", 20)
    timed_direction_live_max_brier: float = _f("TIMED_DIRECTION_LIVE_MAX_BRIER", 0.22)
    timed_direction_live_max_log_loss: float = _f("TIMED_DIRECTION_LIVE_MAX_LOG_LOSS", 0.68)
    timed_direction_live_min_hours_to_close: float = _f("TIMED_DIRECTION_LIVE_MIN_HOURS_TO_CLOSE", 0.03)
    # V2 timing/reference controls. Defaults are safe for the 5m Jupiter cadence.
    timed_direction_reference_boundary_tolerance_seconds: int = _i("TIMED_DIRECTION_REFERENCE_BOUNDARY_TOLERANCE_SECONDS", 3)
    timed_direction_reference_spot_grace_seconds: int = _i("TIMED_DIRECTION_REFERENCE_SPOT_GRACE_SECONDS", 20)
    timed_direction_discovery_min_hours_to_close: float = _f("TIMED_DIRECTION_DISCOVERY_MIN_HOURS_TO_CLOSE", 0.02)
    timed_direction_align_scan_to_window: bool = _b("TIMED_DIRECTION_ALIGN_SCAN_TO_WINDOW", True)
    timed_direction_scan_window_seconds: int = _i("TIMED_DIRECTION_SCAN_WINDOW_SECONDS", 300)
    timed_direction_scan_offset_seconds: int = _i("TIMED_DIRECTION_SCAN_OFFSET_SECONDS", 8)

    # PAPER exploration is isolated from strict/LIVE thresholds.
    paper_exploration_enabled: bool = _b("PAPER_EXPLORATION_ENABLED", True)
    paper_exploration_min_edge: float = _f("PAPER_EXPLORATION_MIN_EDGE", 0.025)
    paper_exploration_min_confidence: float = _f("PAPER_EXPLORATION_MIN_CONFIDENCE", 0.62)
    paper_exploration_min_reliability: float = _f("PAPER_EXPLORATION_MIN_RELIABILITY", 0.58)
    paper_exploration_max_spread_ratio: float = _f("PAPER_EXPLORATION_MAX_SPREAD_RATIO", 0.65)
    paper_exploration_max_per_cycle: int = _i("PAPER_EXPLORATION_MAX_PER_CYCLE", 3)
    paper_exploration_max_orders_per_day: int = _i("PAPER_EXPLORATION_MAX_ORDERS_PER_DAY", 12)
    paper_exploration_max_open_positions: int = _i("PAPER_EXPLORATION_MAX_OPEN_POSITIONS", 25)
    paper_exploration_stake_usd: float = _f("PAPER_EXPLORATION_STAKE_USD", 1.0)

    # Heavy neural retraining is daily/label-triggered, never every 5-minute cycle.
    auto_neural_train_enabled: bool = _b("AUTO_NEURAL_TRAIN_ENABLED", True)
    auto_neural_train_interval_hours: float = _f("AUTO_NEURAL_TRAIN_INTERVAL_HOURS", 24.0)
    auto_neural_train_min_new_labels: int = _i("AUTO_NEURAL_TRAIN_MIN_NEW_LABELS", 25)
    auto_neural_train_max_rows_per_asset: int = _i("AUTO_NEURAL_TRAIN_MAX_ROWS_PER_ASSET", 3000)
    auto_neural_train_required_free_gb: float = _f("AUTO_NEURAL_TRAIN_REQUIRED_FREE_GB", 1.5)

    # PAPER / LIVE safeguards
    trading_mode: str = os.getenv("TRADING_MODE", "paper").strip().lower()
    auto_execute: bool = _b("AUTO_EXECUTE", False)
    live_confirmation: str = os.getenv("LIVE_CONFIRMATION", "")
    live_release_enabled: bool = _b("LIVE_RELEASE_ENABLED", False)
    starting_bankroll_usd: float = _f("STARTING_BANKROLL_USD", 45.0)
    min_order_usd: float = _f("MIN_ORDER_USD", 5.0)
    max_stake_usd: float = _f("MAX_STAKE_USD", 5.0)
    max_live_stake_usd: float = _f("MAX_LIVE_STAKE_USD", 5.0)
    max_orders_per_day: int = _i("MAX_ORDERS_PER_DAY", 4)
    daily_exposure_limit_usd: float = _f("DAILY_EXPOSURE_LIMIT_USD", 20.0)
    max_orders_per_event_per_day: int = _i("MAX_ORDERS_PER_EVENT_PER_DAY", 1)
    max_orders_per_asset_per_day: int = _i("MAX_ORDERS_PER_ASSET_PER_DAY", 2)
    max_live_orders_per_cycle: int = _i("MAX_LIVE_ORDERS_PER_CYCLE", 1)
    max_live_exposure_per_cycle_usd: float = _f("MAX_LIVE_EXPOSURE_PER_CYCLE_USD", 5.0)
    max_open_positions: int = _i("MAX_OPEN_POSITIONS", 6)
    max_open_events: int = _i("MAX_OPEN_EVENTS", 6)
    max_open_positions_per_asset: int = _i("MAX_OPEN_POSITIONS_PER_ASSET", 2)
    max_total_open_exposure_usd: float = _f("MAX_TOTAL_OPEN_EXPOSURE_USD", 30.0)
    max_correlated_exposure_usd: float = _f("MAX_CORRELATED_EXPOSURE_USD", 10.0)
    event_ranking_enabled: bool = _b("EVENT_RANKING_ENABLED", True)
    require_event_diversification: bool = _b("REQUIRE_EVENT_DIVERSIFICATION", True)
    require_exit_price_for_new_buy: bool = _b("REQUIRE_EXIT_PRICE_FOR_NEW_BUY", True)
    max_entry_exit_spread: float = _f("MAX_ENTRY_EXIT_SPREAD", 0.15)
    max_entry_exit_spread_ratio: float = _f("MAX_ENTRY_EXIT_SPREAD_RATIO", 0.50)
    min_market_volume_usd: float = _f("MIN_MARKET_VOLUME_USD", 0.0)
    min_market_liquidity_usd: float = _f("MIN_MARKET_LIQUIDITY_USD", 0.0)
    live_max_entry_exit_spread_ratio: float = _f("LIVE_MAX_ENTRY_EXIT_SPREAD_RATIO", 0.15)
    live_min_market_volume_usd: float = _f("LIVE_MIN_MARKET_VOLUME_USD", 1000.0)
    live_min_market_liquidity_usd: float = _f("LIVE_MIN_MARKET_LIQUIDITY_USD", 500.0)
    stale_pending_order_minutes: int = _i("STALE_PENDING_ORDER_MINUTES", 120)
    min_edge: float = _f("MIN_EDGE", 0.10)
    min_confidence: float = _f("MIN_CONFIDENCE", 0.80)
    min_reliability: float = _f("MIN_RELIABILITY", 0.70)
    edge_hard_cap: float = _f("EDGE_HARD_CAP", 0.55)
    live_max_price_drift: float = _f("LIVE_MAX_PRICE_DRIFT", 0.02)
    kelly_fraction: float = _f("KELLY_FRACTION", 0.06)
    position_management_enabled: bool = _b("POSITION_MANAGEMENT_ENABLED", False)
    take_profit_price_delta: float = _f("TAKE_PROFIT_PRICE_DELTA", 0.20)
    stop_loss_price_delta: float = _f("STOP_LOSS_PRICE_DELTA", 0.20)
    exit_minutes_before_close: float = _f("EXIT_MINUTES_BEFORE_CLOSE", 15.0)
    auto_claim_enabled: bool = _b("AUTO_CLAIM_ENABLED", True)
    fill_poll_seconds: float = _f("FILL_POLL_SECONDS", 8.0)
    fill_poll_attempts: int = _i("FILL_POLL_ATTEMPTS", 10)
    live_simulate_before_send: bool = _b("LIVE_SIMULATE_BEFORE_SEND", True)
    live_confirmation_commitment: str = os.getenv("LIVE_CONFIRMATION_COMMITMENT", "confirmed").strip().lower()
    refuse_live_while_old_bot_running: bool = _b("REFUSE_LIVE_WHILE_OLD_BOT_RUNNING", True)

    # PAPER tracking
    paper_tracking_enabled: bool = _b("PAPER_TRACKING_ENABLED", True)
    paper_refresh_limit: int = _i("PAPER_REFRESH_LIMIT", 20)
    paper_refresh_interval_minutes: int = _i("PAPER_REFRESH_INTERVAL_MINUTES", 10)

    # Optional PAPER book kept alive while TRADING_MODE=live.  It is isolated
    # from LIVE order/exposure guards and never signs or touches the wallet.
    paper_parallel_live_enabled: bool = _b("PAPER_PARALLEL_LIVE_ENABLED", False)
    paper_parallel_stake_usd: float = _f("PAPER_PARALLEL_STAKE_USD", 1.0)
    paper_parallel_max_orders_per_day: int = _i("PAPER_PARALLEL_MAX_ORDERS_PER_DAY", 20)
    paper_parallel_max_open_positions: int = _i("PAPER_PARALLEL_MAX_OPEN_POSITIONS", 40)

    # Multi-order LIVE may coexist with already-sent/pending-fill orders.
    # Unknown/preparing send states remain a global safety stop.
    live_allow_multiple_pending: bool = _b("LIVE_ALLOW_MULTIPLE_PENDING", False)

    # Wallet / Solana
    solana_rpc_url: str = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    solana_rpc_fallback_urls_raw: str = os.getenv("SOLANA_RPC_FALLBACK_URLS", "")
    solana_keypair_path: str = os.getenv("SOLANA_KEYPAIR_PATH", "wallet/bot-keypair.json")
    wallet_balance_cache_seconds: float = _f("WALLET_BALANCE_CACHE_SECONDS", 20.0)
    usdc_mint: str = os.getenv("USDC_MINT", "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v")
    jupusd_mint: str = os.getenv("JUPUSD_MINT", "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD")
    deposit_mint_mode: str = os.getenv("DEPOSIT_MINT_MODE", "auto").strip().lower()
    min_sol_balance: float = _f("MIN_SOL_BALANCE", 0.005)

    def __post_init__(self) -> None:
        self.database_path = _project_path(self.database_path)
        self.cache_dir = _project_path(self.cache_dir)
        self.solana_keypair_path = _project_path(self.solana_keypair_path)
        self.neural_model_dir = _project_path(self.neural_model_dir)
        if self.trading_mode not in {"paper", "live"}:
            raise ValueError("TRADING_MODE doit être paper ou live")

    @property
    def research_history_assets(self) -> list[str]:
        allowed = {"BTC", "ETH", "SOL", "XRP", "HYPE", "DOGE", "BNB"}
        return [x for x in dict.fromkeys(a.strip().upper() for a in self.research_history_assets_raw.split(",")) if x in allowed]

    @property
    def research_core_assets(self) -> list[str]:
        allowed = {"BTC", "ETH", "SOL", "XRP", "HYPE", "DOGE", "BNB"}
        return [x for x in dict.fromkeys(a.strip().upper() for a in self.research_core_assets_raw.split(",")) if x in allowed]

    @property
    def research_primary_timeframes(self) -> list[str]:
        allowed = {"5m", "15m", "1h", "4h", "1d"}
        return [x for x in dict.fromkeys(a.strip() for a in self.research_primary_timeframes_raw.split(",")) if x in allowed]

    @property
    def neural_hidden_layer_sizes(self) -> tuple[int, ...]:
        values = []
        for raw in str(self.neural_hidden_layers).split(","):
            try:
                value = int(raw.strip())
            except ValueError:
                continue
            if 1 <= value <= 256:
                values.append(value)
        return tuple(values or [32, 16])

    @property
    def timed_crypto_assets(self) -> list[str]:
        allowed = {"BTC", "ETH", "SOL", "XRP", "HYPE", "DOGE", "BNB"}
        return [x for x in dict.fromkeys(a.strip().upper() for a in self.timed_crypto_assets_raw.split(",")) if x in allowed]

    @property
    def timed_crypto_tags(self) -> list[str]:
        return [x for x in dict.fromkeys(a.strip().lower() for a in self.timed_crypto_tags_raw.split(",")) if x in {"5m", "15m"}]

    @property
    def event_filters(self) -> list[str]:
        return [x.strip() for x in self.event_filters_raw.split(",") if x.strip()]

    @property
    def categories(self) -> list[str]:
        return list(dict.fromkeys(x.strip() for x in self.categories_raw.split(",") if x.strip()))

    @property
    def crypto_assets(self) -> list[str]:
        allowed = {"BTC", "ETH", "SOL", "XRP", "HYPE", "DOGE", "BNB"}
        return [x for x in dict.fromkeys(a.strip().upper() for a in self.crypto_assets_raw.split(",")) if x in allowed]

    @property
    def crypto_timeframes(self) -> list[str]:
        allowed = {"5m", "15m", "1h", "4h", "1d"}
        return [x for x in dict.fromkeys(a.strip() for a in self.crypto_timeframes_raw.split(",")) if x in allowed]

    @property
    def crypto_sources(self) -> list[str]:
        allowed = {"coinbase", "kraken", "binance", "bybit", "hyperliquid", "okx", "kucoin"}
        return [x for x in dict.fromkeys(a.strip().casefold() for a in self.crypto_sources_raw.split(",")) if x in allowed]

    @property
    def okx_api_base_urls(self) -> list[str]:
        return [x.strip().rstrip("/") for x in self.okx_api_base_urls_raw.split(",") if x.strip()]

    @property
    def kucoin_api_base_urls(self) -> list[str]:
        return [x.strip().rstrip("/") for x in self.kucoin_api_base_urls_raw.split(",") if x.strip()]

    @property
    def solana_rpc_fallback_urls(self) -> list[str]:
        return [x.strip() for x in self.solana_rpc_fallback_urls_raw.split(",") if x.strip()]


    def ensure_dirs(self) -> None:
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.cache_dir).mkdir(parents=True, exist_ok=True)
        Path(self.solana_keypair_path).parent.mkdir(parents=True, exist_ok=True)
        (PROJECT_ROOT / "logs").mkdir(parents=True, exist_ok=True)
        (PROJECT_ROOT / "exports").mkdir(parents=True, exist_ok=True)

    def live_enabled(self) -> bool:
        base = (
            self.release_live_capable
            and self.live_release_enabled
            and self.live_allowed_by_version
            and self.trading_mode == "live"
            and self.auto_execute
            and self.live_confirmation == "I_ACCEPT_REAL_MONEY_RISK"
        )
        if not base:
            return False
        if self.micro_live_enabled:
            return self.micro_live_confirmation == "I_ACCEPT_MICRO_LIVE_5_USD_RISK"
        return True

    def public_dict(self) -> dict:
        hidden = {"jupiter_api_key"}
        return {name: ("***" if name in hidden and getattr(self, name) else getattr(self, name))
                for name in self.__dataclass_fields__}
