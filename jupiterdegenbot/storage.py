from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import CryptoMarketSpec, EngineEstimate, Market, Signal

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS runs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL DEFAULT 'degen',
 started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, message TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events(
 event_id TEXT PRIMARY KEY, title TEXT, category TEXT, subcategory TEXT,
 close_time INTEGER, raw_json TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS markets(
 market_id TEXT PRIMARY KEY, event_id TEXT, event_title TEXT, question TEXT,
 rules TEXT, category TEXT, subcategory TEXT, resolution_source TEXT,
 yes_price REAL, no_price REAL, sell_yes_price REAL, sell_no_price REAL,
 volume_usd REAL, liquidity_usd REAL, close_time INTEGER, search_query TEXT,
 asset TEXT, comparator TEXT, threshold_low REAL, threshold_high REAL,
 event_family TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshots(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, market_id TEXT,
 yes_price REAL, no_price REAL, sell_yes_price REAL, sell_no_price REAL,
 volume_usd REAL, liquidity_usd REAL, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS crypto_prices(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT, price REAL,
 bid REAL, ask REAL, volume_24h REAL, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS candles(
 source TEXT, asset TEXT, timeframe TEXT, ts INTEGER,
 open REAL, high REAL, low REAL, close REAL, volume REAL,
 PRIMARY KEY(source,asset,timeframe,ts)
);
CREATE TABLE IF NOT EXISTS order_books(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT,
 bid REAL, ask REAL, spread REAL, raw_json TEXT, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS funding(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT,
 rate REAL, next_funding_at TEXT, raw_json TEXT, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS open_interest(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT,
 value_usd REAL, raw_json TEXT, observed_at TEXT
);
CREATE TABLE IF NOT EXISTS observations(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, market_id TEXT,
 engine TEXT, source TEXT, kind TEXT, value REAL, reliability REAL,
 observed_at TEXT, metadata_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS model_predictions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, market_id TEXT,
 asset TEXT, comparator TEXT, threshold_low REAL, threshold_high REAL,
 expiry INTEGER, model_name TEXT, probability_yes REAL, confidence REAL,
 reliability REAL, source_agreement REAL, volatility REAL, evidence_json TEXT,
 created_at TEXT
);
CREATE TABLE IF NOT EXISTS signals(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, market_id TEXT,
 event_id TEXT, asset TEXT, event_family TEXT, question TEXT, outcome TEXT,
 expiry INTEGER, resolution_source TEXT, price REAL, probability REAL,
 confidence REAL, reliability REAL, edge REAL, score REAL, stake_usd REAL,
 signal_type TEXT, source_count INTEGER, source_agreement REAL,
 volatility REAL, liquidity REAL, entry_price REAL, exit_price REAL, spread REAL,
 reasoning TEXT, evidence_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS orders(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, signal_id INTEGER,
 mode TEXT, market_id TEXT, event_id TEXT, asset TEXT, event_family TEXT,
 outcome TEXT, amount_usd REAL, deposit_mint TEXT, status TEXT,
 order_pubkey TEXT, position_pubkey TEXT, signature TEXT, response_json TEXT,
 paper_entry_price REAL, paper_shares REAL, paper_mark_price REAL,
 paper_value_usd REAL, paper_pnl_usd REAL, paper_result TEXT DEFAULT '',
 paper_updated_at TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS positions(
 position_key TEXT PRIMARY KEY, market_id TEXT, event_id TEXT, asset TEXT,
 event_family TEXT, outcome TEXT, shares REAL, entry_price REAL, cost_usd REAL,
 value_usd REAL, pnl_usd REAL, pnl_after_fees_usd REAL, fees_paid_usd REAL,
 realized_pnl_usd REAL, mark_price REAL, sell_price REAL, payout_usd REAL,
 no_exit_price INTEGER DEFAULT 0, event_title TEXT, close_time INTEGER,
 status TEXT, question TEXT, claimable INTEGER DEFAULT 0, claimed INTEGER DEFAULT 0,
 raw_json TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS claims(
 id INTEGER PRIMARY KEY AUTOINCREMENT, position_key TEXT, market_id TEXT,
 status TEXT, signature TEXT, response_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS lifecycle_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, market_id TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS incidents(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, severity TEXT,
 kind TEXT, market_id TEXT, detail TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS calibration_results(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, horizon TEXT, sample_count INTEGER,
 brier_score REAL, log_loss REAL, calibration_json TEXT, calculated_at TEXT
);
CREATE TABLE IF NOT EXISTS ai_reviews(
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, market_id TEXT, signal_id INTEGER,
 model TEXT, verdict TEXT, available INTEGER, confidence_penalty REAL,
 reliability_penalty REAL, explanation TEXT, flags_json TEXT, latency_ms INTEGER,
 raw_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS learning_profiles(
 asset TEXT, horizon TEXT, comparator TEXT, sample_count INTEGER,
 brier_score REAL, residual_bias REAL, probability_adjustment REAL,
 confidence_multiplier REAL, source_weights_json TEXT, active INTEGER,
 updated_at TEXT, PRIMARY KEY(asset,horizon,comparator)
);
CREATE TABLE IF NOT EXISTS research_sync(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT, timeframe TEXT,
 requested_start INTEGER, requested_end INTEGER, rows_written INTEGER, pages INTEGER,
 status TEXT, detail TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS data_quality(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT, timeframe TEXT,
 row_count INTEGER, missing_ratio REAL, duplicate_count INTEGER, stale INTEGER,
 incomplete_dropped INTEGER, first_ts INTEGER, last_ts INTEGER, passed INTEGER,
 detail_json TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS derived_metrics(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, source TEXT, observed_at TEXT,
 funding_rate REAL, open_interest REAL, oi_change REAL, book_imbalance REAL,
 book_spread_bps REAL, liquidation_bias REAL, basis_bps REAL, raw_json TEXT
);
CREATE TABLE IF NOT EXISTS feature_vectors(
 id INTEGER PRIMARY KEY AUTOINCREMENT, asset TEXT, timeframe TEXT, ts INTEGER,
 horizon_steps INTEGER, threshold_z REAL, feature_json TEXT, label INTEGER,
 split TEXT, created_at TEXT, UNIQUE(asset,timeframe,ts,horizon_steps,threshold_z)
);
CREATE TABLE IF NOT EXISTS neural_models(
 id INTEGER PRIMARY KEY AUTOINCREMENT, model_key TEXT, model_type TEXT, version TEXT,
 train_samples INTEGER, positive_rate REAL, brier_score REAL, log_loss REAL,
 auc REAL, artifact_path TEXT, feature_names_json TEXT, metrics_json TEXT,
 active INTEGER, trained_at TEXT
);
CREATE TABLE IF NOT EXISTS validation_runs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, status TEXT, assets_json TEXT,
 train_samples INTEGER, test_samples INTEGER, trade_count INTEGER, brier_score REAL,
 log_loss REAL, baseline_brier REAL, baseline_log_loss REAL, roi_after_costs REAL,
 max_drawdown REAL, win_rate REAL, metrics_json TEXT, started_at TEXT, finished_at TEXT
);
CREATE TABLE IF NOT EXISTS validation_predictions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, validation_run_id INTEGER, asset TEXT,
 timeframe TEXT, ts INTEGER, probability REAL, actual INTEGER, market_price REAL,
 selected INTEGER, pnl REAL, feature_json TEXT
);
CREATE TABLE IF NOT EXISTS live_gate_checks(
 id INTEGER PRIMARY KEY AUTOINCREMENT, passed INTEGER, reasons_json TEXT,
 metrics_json TEXT, checked_at TEXT
);
CREATE TABLE IF NOT EXISTS shadow_predictions(
 id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT NOT NULL, event_id TEXT,
 asset TEXT, comparator TEXT, settlement_kind TEXT, horizon TEXT, expiry INTEGER,
 question TEXT, model_name TEXT NOT NULL, probability_yes REAL, market_yes_price REAL,
 market_no_price REAL, confidence REAL, reliability REAL, source_agreement REAL,
 selected_outcome TEXT, selected_edge REAL, would_trade_strict INTEGER DEFAULT 0,
 would_trade_exploration INTEGER DEFAULT 0, evidence_json TEXT, status TEXT DEFAULT 'OPEN',
 actual_yes INTEGER, result_source TEXT, brier_score REAL, log_loss REAL,
 first_seen_at TEXT, last_seen_at TEXT, resolved_at TEXT,
 UNIQUE(market_id,model_name)
);
CREATE TABLE IF NOT EXISTS auto_training_runs(
 id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT,
 trigger_reason TEXT, new_labels INTEGER, result_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_sync ON research_sync(asset,source,timeframe,finished_at);
CREATE INDEX IF NOT EXISTS idx_quality ON data_quality(asset,timeframe,checked_at);
CREATE INDEX IF NOT EXISTS idx_features ON feature_vectors(asset,timeframe,ts);
CREATE INDEX IF NOT EXISTS idx_validation_runs ON validation_runs(finished_at,status);
CREATE INDEX IF NOT EXISTS idx_signals_run ON signals(run_id);
CREATE INDEX IF NOT EXISTS idx_orders_day ON orders(created_at,status);
CREATE INDEX IF NOT EXISTS idx_orders_asset ON orders(asset,created_at);
CREATE INDEX IF NOT EXISTS idx_positions_asset ON positions(asset,status);
CREATE INDEX IF NOT EXISTS idx_obs_market ON observations(market_id,created_at);
CREATE INDEX IF NOT EXISTS idx_prices_asset ON crypto_prices(asset,observed_at);
CREATE INDEX IF NOT EXISTS idx_candles_asset ON candles(asset,timeframe,ts);
CREATE INDEX IF NOT EXISTS idx_ai_reviews_market ON ai_reviews(market_id,created_at);
CREATE INDEX IF NOT EXISTS idx_learning_profiles_active ON learning_profiles(active,asset,horizon);
CREATE INDEX IF NOT EXISTS idx_shadow_open ON shadow_predictions(status,expiry,last_seen_at);
CREATE INDEX IF NOT EXISTS idx_shadow_asset ON shadow_predictions(asset,status,resolved_at);
CREATE INDEX IF NOT EXISTS idx_auto_training ON auto_training_runs(finished_at,status);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def database_size_mb(self) -> float:
        total = 0
        for candidate in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
        return total / (1024 ** 2)

    def history_storage_status(self, max_gb: float) -> dict:
        size_mb = self.database_size_mb()
        budget_mb = max(0.1, float(max_gb)) * 1024.0
        return {
            "size_mb": round(size_mb, 2),
            "budget_gb": round(float(max_gb), 2),
            "under_budget": size_mb <= budget_mb,
            "note": "SQLite persistant; aucun espace n'est prealloue",
        }

    def connect(self, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        else:
            conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def start_run(self, kind: str = "degen") -> int:
        with self.connect() as conn:
            cur = conn.execute("INSERT INTO runs(kind,started_at,status) VALUES(?,?,?)", (kind, now(), "running"))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str | dict = "") -> None:
        rendered = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False, default=str)
        with self.connect() as conn:
            conn.execute("UPDATE runs SET finished_at=?,status=?,message=? WHERE id=?",
                         (now(), status, rendered[:12000], run_id))

    def recover_runs(self) -> int:
        with self.connect() as conn:
            cur = conn.execute("UPDATE runs SET finished_at=?,status='interrupted' WHERE status='running'", (now(),))
            return int(cur.rowcount or 0)

    def upsert_markets(self, markets: Iterable[Market], specs: dict[str, CryptoMarketSpec] | None = None,
                       run_id: int | None = None) -> None:
        specs = specs or {}
        stamp = now()
        with self.connect() as conn:
            for m in markets:
                spec = specs.get(m.id)
                conn.execute(
                    """INSERT INTO events(event_id,title,category,subcategory,close_time,last_seen)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET
                       title=excluded.title,category=excluded.category,subcategory=excluded.subcategory,
                       close_time=excluded.close_time,last_seen=excluded.last_seen""",
                    (m.event_id, m.event_title, m.category, m.subcategory, m.close_time, stamp),
                )
                conn.execute(
                    """INSERT INTO markets(
                       market_id,event_id,event_title,question,rules,category,subcategory,resolution_source,
                       yes_price,no_price,sell_yes_price,sell_no_price,volume_usd,liquidity_usd,close_time,
                       search_query,asset,comparator,threshold_low,threshold_high,event_family,last_seen
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(market_id) DO UPDATE SET
                       event_id=excluded.event_id,event_title=excluded.event_title,question=excluded.question,
                       rules=excluded.rules,category=excluded.category,subcategory=excluded.subcategory,
                       resolution_source=excluded.resolution_source,yes_price=excluded.yes_price,
                       no_price=excluded.no_price,sell_yes_price=excluded.sell_yes_price,
                       sell_no_price=excluded.sell_no_price,volume_usd=excluded.volume_usd,
                       liquidity_usd=excluded.liquidity_usd,close_time=excluded.close_time,
                       search_query=excluded.search_query,asset=excluded.asset,comparator=excluded.comparator,
                       threshold_low=excluded.threshold_low,threshold_high=excluded.threshold_high,
                       event_family=excluded.event_family,last_seen=excluded.last_seen""",
                    (m.id, m.event_id, m.event_title, m.question, m.rules, m.category, m.subcategory,
                     m.resolution_source, m.yes_price, m.no_price, m.sell_yes_price, m.sell_no_price,
                     m.volume_usd, m.liquidity_usd, m.close_time, m.search_query,
                     spec.asset if spec else "", spec.comparator if spec else "",
                     spec.threshold_low if spec else None, spec.threshold_high if spec else None,
                     spec.event_family if spec else "", stamp),
                )
                if run_id is not None:
                    conn.execute(
                        """INSERT INTO market_snapshots(run_id,market_id,yes_price,no_price,
                           sell_yes_price,sell_no_price,volume_usd,liquidity_usd,observed_at)
                           VALUES(?,?,?,?,?,?,?,?,?)""",
                        (run_id, m.id, m.yes_price, m.no_price, m.sell_yes_price, m.sell_no_price,
                         m.volume_usd, m.liquidity_usd, stamp),
                    )

    def add_crypto_snapshot(self, snapshot) -> None:
        with self.connect() as conn:
            for item in snapshot.sources:
                conn.execute(
                    "INSERT INTO crypto_prices(asset,source,price,bid,ask,volume_24h,observed_at) VALUES(?,?,?,?,?,?,?)",
                    (snapshot.asset, item.source, item.spot, item.bid, item.ask, item.volume_24h, snapshot.observed_at),
                )
                conn.execute(
                    "INSERT INTO order_books(asset,source,bid,ask,spread,raw_json,observed_at) VALUES(?,?,?,?,?,?,?)",
                    (snapshot.asset, item.source, item.bid, item.ask,
                     item.ask - item.bid if item.ask > 0 and item.bid > 0 else None, "{}", snapshot.observed_at),
                )
                for tf, candles in item.candles.items():
                    conn.executemany(
                        """INSERT INTO candles(source,asset,timeframe,ts,open,high,low,close,volume)
                           VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,asset,timeframe,ts) DO UPDATE SET
                           open=excluded.open,high=excluded.high,low=excluded.low,
                           close=excluded.close,volume=excluded.volume""",
                        [(c.source, c.asset, tf, c.ts, c.open, c.high, c.low, c.close, c.volume) for c in candles],
                    )

    def add_observations(self, run_id: int, market_id: str, engine: str, observations) -> None:
        rows = [(run_id, market_id, engine, o.source, o.kind, o.value, o.reliability, o.observed_at,
                 json.dumps(o.metadata, ensure_ascii=False, default=str), now()) for o in observations]
        if rows:
            with self.connect() as conn:
                conn.executemany("""INSERT INTO observations(run_id,market_id,engine,source,kind,value,
                    reliability,observed_at,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", rows)

    def add_model_prediction(self, run_id: int, market: Market, estimate: EngineEstimate,
                             spec: CryptoMarketSpec) -> None:
        with self.connect() as conn:
            conn.execute("""INSERT INTO model_predictions(run_id,market_id,asset,comparator,
                threshold_low,threshold_high,expiry,model_name,probability_yes,confidence,reliability,
                source_agreement,volatility,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, market.id, spec.asset, spec.comparator, spec.threshold_low, spec.threshold_high,
                 spec.expiry_ts, estimate.engine, estimate.probability_yes, estimate.confidence,
                 estimate.reliability, estimate.source_agreement, estimate.volatility,
                 json.dumps(estimate.evidence_json, ensure_ascii=False, default=str), now()))

    def upsert_shadow_prediction(self, market: Market, estimate: EngineEstimate,
                                 spec: CryptoMarketSpec, *, strict_ok: bool = False,
                                 exploration_ok: bool = False) -> int:
        """Persist the first auditable prediction for a market.

        The probability is intentionally not overwritten later: one settled market
        contributes one out-of-sample label instead of hundreds of correlated cycle
        snapshots. last_seen_at is refreshed for operational visibility.
        """
        probability = max(0.001, min(0.999, float(estimate.probability_yes)))
        yes_edge = probability - float(market.yes_price)
        no_edge = (1.0 - probability) - float(market.no_price)
        selected_outcome = "YES" if yes_edge >= no_edge else "NO"
        selected_edge = max(yes_edge, no_edge)
        horizon = "unknown"
        try:
            from .adaptive import horizon_bucket
            horizon = horizon_bucket(spec.expiry_ts)
        except Exception:
            pass
        stamp = now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO shadow_predictions(
                   market_id,event_id,asset,comparator,settlement_kind,horizon,expiry,question,
                   model_name,probability_yes,market_yes_price,market_no_price,confidence,
                   reliability,source_agreement,selected_outcome,selected_edge,
                   would_trade_strict,would_trade_exploration,evidence_json,status,
                   first_seen_at,last_seen_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(market_id,model_name) DO UPDATE SET
                   last_seen_at=excluded.last_seen_at,
                   would_trade_strict=MAX(shadow_predictions.would_trade_strict,excluded.would_trade_strict),
                   would_trade_exploration=MAX(shadow_predictions.would_trade_exploration,excluded.would_trade_exploration)""",
                (market.id, market.event_id, spec.asset, spec.comparator, spec.settlement_kind,
                 horizon, spec.expiry_ts, market.question, estimate.engine, probability,
                 market.yes_price, market.no_price, estimate.confidence, estimate.reliability,
                 estimate.source_agreement, selected_outcome, selected_edge,
                 1 if strict_ok else 0, 1 if exploration_ok else 0,
                 json.dumps(estimate.evidence_json, ensure_ascii=False, default=str),
                 "OPEN", stamp, stamp),
            )
            row = conn.execute(
                "SELECT id FROM shadow_predictions WHERE market_id=? AND model_name=?",
                (market.id, estimate.engine),
            ).fetchone()
            return int(row["id"])

    def open_shadow_predictions(self, limit: int = 200, grace_minutes: int = 2) -> list[sqlite3.Row]:
        """Return a fair resolver batch without letting legacy rows starve TIMED V2.

        The current TIMED V2 model receives up to two thirds of the batch.
        Remaining capacity is used for older/other models.

        Within each group, the newest eligible expiries are checked first so
        very old permanently unresolved rows cannot monopolize the resolver.
        """
        limit = max(1, int(limit))
        grace = f"-{max(0, int(grace_minutes))} minutes"
        current_model = "DEGEN_QUANT_V6_TIMED_DIRECTION_V2"

        current_quota = max(1, (limit * 2) // 3)

        with self.connect(readonly=True) as conn:
            current_rows = conn.execute(
                """SELECT * FROM shadow_predictions
                   WHERE status='OPEN'
                     AND expiry<=strftime('%s','now',?)
                     AND model_name=?
                   ORDER BY expiry DESC,id DESC
                   LIMIT ?""",
                (grace, current_model, current_quota),
            ).fetchall()

            remaining = max(0, limit - len(current_rows))
            if remaining == 0:
                return current_rows

            other_rows = conn.execute(
                """SELECT * FROM shadow_predictions
                   WHERE status='OPEN'
                     AND expiry<=strftime('%s','now',?)
                     AND COALESCE(model_name,'')<>?
                   ORDER BY expiry DESC,id DESC
                   LIMIT ?""",
                (grace, current_model, remaining),
            ).fetchall()

            return current_rows + other_rows

    def resolve_shadow_prediction(self, shadow_id: int, actual_yes: int, source: str) -> None:
        actual = 1 if int(actual_yes) else 0
        with self.connect() as conn:
            row = conn.execute(
                "SELECT probability_yes FROM shadow_predictions WHERE id=? AND status='OPEN'",
                (shadow_id,),
            ).fetchone()
            if row is None:
                return
            p = max(1e-6, min(1-1e-6, float(row["probability_yes"])))
            brier = (p - actual) ** 2
            import math
            loss = -(actual * math.log(p) + (1-actual) * math.log(1-p))
            conn.execute(
                """UPDATE shadow_predictions SET status='RESOLVED',actual_yes=?,
                   result_source=?,brier_score=?,log_loss=?,resolved_at=? WHERE id=?""",
                (actual, str(source or "Jupiter")[:200], brier, loss, now(), shadow_id),
            )

    def shadow_summary(self) -> dict:
        with self.connect(readonly=True) as conn:
            row = conn.execute(
                """SELECT COUNT(*) total,
                   SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) open_count,
                   SUM(CASE WHEN status='RESOLVED' THEN 1 ELSE 0 END) resolved,
                   AVG(CASE WHEN status='RESOLVED' THEN brier_score END) brier,
                   AVG(CASE WHEN status='RESOLVED' THEN log_loss END) log_loss,
                   SUM(CASE WHEN status='RESOLVED' AND date(resolved_at)=date('now') THEN 1 ELSE 0 END) resolved_today
                   FROM shadow_predictions"""
            ).fetchone()
        return {
            "total": int(row["total"] or 0), "open": int(row["open_count"] or 0),
            "resolved": int(row["resolved"] or 0),
            "resolved_today": int(row["resolved_today"] or 0),
            "brier": float(row["brier"]) if row["brier"] is not None else None,
            "log_loss": float(row["log_loss"]) if row["log_loss"] is not None else None,
        }

    def shadow_settled_by_asset(self) -> list[sqlite3.Row]:
        with self.connect(readonly=True) as conn:
            return conn.execute(
                """SELECT asset,COUNT(*) n,AVG(brier_score) brier,AVG(log_loss) log_loss
                   FROM shadow_predictions WHERE status='RESOLVED' GROUP BY asset ORDER BY asset"""
            ).fetchall()

    def latest_auto_training(self):
        with self.connect(readonly=True) as conn:
            return conn.execute(
                "SELECT * FROM auto_training_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def start_auto_training(self, reason: str, new_labels: int) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO auto_training_runs(started_at,status,trigger_reason,new_labels,result_json) VALUES(?,?,?,?,?)",
                (now(), "running", reason[:500], int(new_labels), "{}"),
            )
            return int(cur.lastrowid)

    def finish_auto_training(self, run_id: int, status: str, result: dict) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE auto_training_runs SET finished_at=?,status=?,result_json=? WHERE id=?",
                (now(), status, json.dumps(result, ensure_ascii=False, default=str)[:200000], run_id),
            )

    def add_ai_review(self, run_id: int, market_id: str, signal_id: int | None, review) -> int:
        payload = review.dict() if hasattr(review, "dict") else dict(review or {})
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO ai_reviews(run_id,market_id,signal_id,model,verdict,available,
                   confidence_penalty,reliability_penalty,explanation,flags_json,latency_ms,raw_json,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, market_id, signal_id, str(payload.get("model") or ""),
                 str(payload.get("verdict") or ""), 1 if payload.get("available") else 0,
                 float(payload.get("confidence_penalty") or 0.0),
                 float(payload.get("reliability_penalty") or 0.0),
                 str(payload.get("explanation_fr") or "")[:4000],
                 json.dumps(payload.get("risk_flags") or [], ensure_ascii=False),
                 int(payload.get("latency_ms") or 0),
                 json.dumps(payload.get("raw") or {}, ensure_ascii=False, default=str)[:12000], now()),
            )
            return int(cur.lastrowid)

    def upsert_learning_profile(self, profile) -> None:
        payload = profile.dict() if hasattr(profile, "dict") else dict(profile or {})
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO learning_profiles(asset,horizon,comparator,sample_count,brier_score,
                   residual_bias,probability_adjustment,confidence_multiplier,source_weights_json,active,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(asset,horizon,comparator) DO UPDATE SET
                   sample_count=excluded.sample_count,brier_score=excluded.brier_score,
                   residual_bias=excluded.residual_bias,probability_adjustment=excluded.probability_adjustment,
                   confidence_multiplier=excluded.confidence_multiplier,
                   source_weights_json=excluded.source_weights_json,active=excluded.active,
                   updated_at=excluded.updated_at""",
                (str(payload.get("asset") or ""), str(payload.get("horizon") or ""),
                 str(payload.get("comparator") or ""), int(payload.get("sample_count") or 0),
                 payload.get("brier_score"), float(payload.get("residual_bias") or 0.0),
                 float(payload.get("probability_adjustment") or 0.0),
                 float(payload.get("confidence_multiplier") or 1.0),
                 json.dumps(payload.get("source_weights") or {}, ensure_ascii=False, default=str),
                 1 if payload.get("active") else 0, now()),
            )

    def get_learning_profile(self, asset: str, horizon: str, comparator: str):
        with self.connect(readonly=True) as conn:
            return conn.execute(
                "SELECT * FROM learning_profiles WHERE asset=? AND horizon=? AND comparator=?",
                (asset, horizon, comparator),
            ).fetchone()

    def learning_profiles(self) -> list[sqlite3.Row]:
        with self.connect(readonly=True) as conn:
            return conn.execute(
                "SELECT * FROM learning_profiles ORDER BY active DESC,asset,horizon,comparator"
            ).fetchall()

    def latest_ai_reviews(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect(readonly=True) as conn:
            return conn.execute(
                "SELECT * FROM ai_reviews ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
            ).fetchall()

    def add_signal(self, run_id: int, event_id: str, signal: Signal) -> int:
        with self.connect() as conn:
            cur = conn.execute("""INSERT INTO signals(run_id,market_id,event_id,asset,event_family,
                question,outcome,expiry,resolution_source,price,probability,confidence,reliability,edge,
                score,stake_usd,signal_type,source_count,source_agreement,volatility,liquidity,
                entry_price,exit_price,spread,reasoning,evidence_json,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, signal.market_id, event_id, signal.asset, signal.event_family, signal.question,
                 signal.outcome, signal.expiry, signal.resolution_source, signal.price, signal.probability,
                 signal.confidence, signal.reliability, signal.edge, signal.score, signal.stake_usd,
                 signal.signal_type, signal.source_count, signal.source_agreement, signal.volatility,
                 signal.liquidity, signal.entry_price, signal.exit_price, signal.spread, signal.reasoning,
                 json.dumps({"evidence": signal.evidence, **signal.evidence_json}, ensure_ascii=False, default=str), now()))
            return int(cur.lastrowid)

    def update_signal(self, signal_id: int, signal: Signal) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE signals SET outcome=?,price=?,probability=?,confidence=?,reliability=?,edge=?,
                   score=?,stake_usd=?,signal_type=?,source_count=?,source_agreement=?,volatility=?,
                   liquidity=?,entry_price=?,exit_price=?,spread=?,reasoning=?,evidence_json=? WHERE id=?""",
                (signal.outcome, signal.price, signal.probability, signal.confidence,
                 signal.reliability, signal.edge, signal.score, signal.stake_usd,
                 signal.signal_type, signal.source_count, signal.source_agreement,
                 signal.volatility, signal.liquidity, signal.entry_price, signal.exit_price,
                 signal.spread, signal.reasoning,
                 json.dumps({"evidence": signal.evidence, **signal.evidence_json}, ensure_ascii=False, default=str),
                 signal_id),
            )

    def add_order(self, run_id: int, signal_id: int, market_id: str, event_id: str, outcome: str,
                  amount_usd: float, mode: str, status: str, deposit_mint: str = "",
                  response: dict | None = None, order_pubkey: str = "", position_pubkey: str = "",
                  signature: str = "") -> int:
        payload = response or {}
        with self.connect() as conn:
            sig = conn.execute("SELECT asset,event_family FROM signals WHERE id=?", (signal_id,)).fetchone()
            asset = str(sig["asset"] or "") if sig else ""
            family = str(sig["event_family"] or "") if sig else ""
            entry = shares = mark = value = pnl = paper_updated = None
            if mode == "paper":
                try:
                    entry = float(payload.get("price") or 0)
                    if entry > 0:
                        shares, mark, value, pnl, paper_updated = amount_usd / entry, entry, amount_usd, 0.0, now()
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
            cur = conn.execute("""INSERT INTO orders(run_id,signal_id,mode,market_id,event_id,asset,
                event_family,outcome,amount_usd,deposit_mint,status,order_pubkey,position_pubkey,signature,
                response_json,paper_entry_price,paper_shares,paper_mark_price,paper_value_usd,
                paper_pnl_usd,paper_result,paper_updated_at,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, signal_id, mode, market_id, event_id, asset, family, outcome, amount_usd,
                 deposit_mint, status, order_pubkey, position_pubkey, signature,
                 json.dumps(payload, ensure_ascii=False, default=str), entry, shares, mark, value, pnl,
                 "open" if mode == "paper" else "", paper_updated, now()))
            return int(cur.lastrowid)

    def update_order(self, order_id: int, status: str, response: dict | None = None, **fields) -> None:
        allowed = {"order_pubkey", "position_pubkey", "signature", "deposit_mint"}
        with self.connect() as conn:
            sets, params = ["status=?"], [status]
            if response is not None:
                row = conn.execute("SELECT response_json FROM orders WHERE id=?", (order_id,)).fetchone()
                merged = {}
                try:
                    merged = json.loads(row["response_json"] or "{}") if row else {}
                except Exception:
                    merged = {}
                merged.update(response if isinstance(response, dict) else {"update": response})
                sets.append("response_json=?")
                params.append(json.dumps(merged, ensure_ascii=False, default=str))
            for key, value in fields.items():
                if key in allowed:
                    sets.append(f"{key}=?")
                    params.append(value)
            params.append(order_id)
            conn.execute(f"UPDATE orders SET {','.join(sets)} WHERE id=?", params)

    def live_orders_for_reconcile(self, limit: int = 500) -> list[sqlite3.Row]:
        statuses = ("preparing", "unsigned_ready", "sent", "pending_fill", "partial_fill", "unknown_send")
        marks = ",".join("?" for _ in statuses)
        with self.connect(readonly=True) as conn:
            return conn.execute(f"""SELECT o.*,s.question,s.signal_type,s.probability,s.edge
                FROM orders o LEFT JOIN signals s ON s.id=o.signal_id
                WHERE o.mode='live' AND o.status IN ({marks})
                ORDER BY o.id LIMIT ?""", (*statuses, max(1, int(limit)))).fetchall()

    def position_rows(self, statuses: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
        with self.connect(readonly=True) as conn:
            if not statuses:
                return conn.execute("SELECT * FROM positions ORDER BY updated_at DESC").fetchall()
            marks = ",".join("?" for _ in statuses)
            return conn.execute(f"SELECT * FROM positions WHERE status IN ({marks}) ORDER BY updated_at DESC", statuses).fetchall()

    def mark_position_status(self, position_key: str, status: str, *, claimable: bool | None = None,
                             claimed: bool | None = None) -> None:
        sets, params = ["status=?", "updated_at=?"], [status, now()]
        if claimable is not None:
            sets.append("claimable=?"); params.append(1 if claimable else 0)
        if claimed is not None:
            sets.append("claimed=?"); params.append(1 if claimed else 0)
        params.append(position_key)
        with self.connect() as conn:
            conn.execute(f"UPDATE positions SET {','.join(sets)} WHERE position_key=?", params)

    def finalize_position_from_history(self, position_key: str, event: dict) -> bool:
        """Persist an authoritative terminal Jupiter history event.

        FINAL_POSITION_RECONCILE_V1
        Only position_lost and payout_claimed are terminal here. Any unknown
        or non-terminal event is deliberately ignored (fail closed).
        USD fields received from Jupiter history are micro-USD.
        """
        if not position_key or not isinstance(event, dict):
            return False

        event_type = str(event.get("eventType") or "").strip().casefold()
        if event_type not in {"position_lost", "payout_claimed"}:
            return False

        def micro_usd(value):
            if value is None or value == "":
                return None
            try:
                return float(value) / 1_000_000.0
            except (TypeError, ValueError):
                return None

        official_net = micro_usd(event.get("realizedPnl"))
        official_gross = micro_usd(event.get("realizedPnlBeforeFees"))
        official_payout = micro_usd(event.get("payoutAmountUsd"))

        with self.connect() as conn:
            row = conn.execute(
                """SELECT position_key,market_id,cost_usd,fees_paid_usd,
                          realized_pnl_usd,raw_json
                   FROM positions WHERE position_key=?""",
                (position_key,),
            ).fetchone()
            if not row:
                return False

            cost = float(row["cost_usd"] or 0.0)
            fees = float(row["fees_paid_usd"] or 0.0)

            if event_type == "position_lost":
                gross = official_gross if official_gross is not None else -cost
                net = official_net if official_net is not None else gross - fees
                payout = official_payout if official_payout is not None else 0.0
                status = "lost"
                claimed = 0
            else:
                payout = official_payout if official_payout is not None else 0.0
                gross = official_gross if official_gross is not None else payout - cost
                net = official_net if official_net is not None else gross - fees
                status = "claimed"
                claimed = 1

            try:
                raw = json.loads(str(row["raw_json"] or "{}"))
                if not isinstance(raw, dict):
                    raw = {}
            except Exception:
                raw = {}
            raw["_final_history_event"] = event
            raw["_reconciled_by"] = "FINAL_POSITION_RECONCILE_V1"

            conn.execute(
                """UPDATE positions
                   SET status=?,
                       value_usd=0,
                       pnl_usd=?,
                       pnl_after_fees_usd=?,
                       realized_pnl_usd=?,
                       payout_usd=?,
                       mark_price=0,
                       sell_price=0,
                       no_exit_price=0,
                       claimable=0,
                       claimed=?,
                       raw_json=?,
                       updated_at=?
                   WHERE position_key=?""",
                (
                    status,
                    float(gross),
                    float(net),
                    float(net),
                    float(payout),
                    int(claimed),
                    json.dumps(raw, ensure_ascii=False, default=str),
                    now(),
                    position_key,
                ),
            )
        return True

    def live_summary(self) -> dict:
        with self.connect(readonly=True) as conn:
            order = conn.execute("""SELECT COUNT(*) total,
              SUM(CASE WHEN status IN ('filled','confirmed') THEN 1 ELSE 0 END) filled,
              SUM(CASE WHEN status IN ('preparing','unsigned_ready','sent','pending_fill','partial_fill','unknown_send') THEN 1 ELSE 0 END) pending,
              SUM(CASE WHEN status IN ('failed','simulation_error') THEN 1 ELSE 0 END) failed,
              COALESCE(SUM(CASE WHEN status NOT IN ('failed','simulation_error','blocked') THEN amount_usd ELSE 0 END),0) committed
              FROM orders WHERE mode='live'""").fetchone()
            position = conn.execute("""SELECT COUNT(*) total,
              SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) open_count,
              COUNT(DISTINCT CASE WHEN status='open' THEN NULLIF(event_id,'') END) open_events,
              SUM(CASE WHEN claimable=1 AND claimed=0 THEN 1 ELSE 0 END) claimable,
              SUM(CASE WHEN status='open' AND COALESCE(no_exit_price,0)=1 THEN 1 ELSE 0 END) no_exit,
              COALESCE(SUM(cost_usd),0) cost_usd,COALESCE(SUM(value_usd),0) value_usd,
              COALESCE(SUM(pnl_usd),0) pnl_usd,
              COALESCE(SUM(COALESCE(pnl_after_fees_usd,pnl_usd)),0) pnl_after_fees_usd,
              COALESCE(SUM(fees_paid_usd),0) fees_paid_usd,
              COALESCE(SUM(realized_pnl_usd),0) realized_pnl_usd FROM positions""").fetchone()
        return {"orders_total": int(order["total"] or 0), "orders_filled": int(order["filled"] or 0),
                "orders_pending": int(order["pending"] or 0), "orders_failed": int(order["failed"] or 0),
                "committed_usd": round(float(order["committed"] or 0), 4),
                "positions_total": int(position["total"] or 0), "positions_open": int(position["open_count"] or 0),
                "open_events": int(position["open_events"] or 0),
                "positions_claimable": int(position["claimable"] or 0),
                "positions_no_exit": int(position["no_exit"] or 0),
                "positions_cost_usd": round(float(position["cost_usd"] or 0), 4),
                "positions_value_usd": round(float(position["value_usd"] or 0), 4),
                "positions_pnl_usd": round(float(position["pnl_usd"] or 0), 4),
                "positions_pnl_after_fees_usd": round(float(position["pnl_after_fees_usd"] or 0), 4),
                "positions_fees_paid_usd": round(float(position["fees_paid_usd"] or 0), 4),
                "positions_realized_pnl_usd": round(float(position["realized_pnl_usd"] or 0), 4)}

    def orders_today(self, mode: str | None = None) -> tuple[int, float]:
        statuses = ("paper_filled","paper_won","paper_lost","paper_refunded","preparing","unsigned_ready",
                    "sent","pending_fill","partial_fill","filled","confirmed","unknown_send")
        marks = ",".join("?" for _ in statuses)
        mode_clause = " AND mode=?" if mode else ""
        params = (*statuses, str(mode)) if mode else statuses
        with self.connect(readonly=True) as conn:
            row = conn.execute(f"SELECT COUNT(*) n,COALESCE(SUM(amount_usd),0) total FROM orders "
                               f"WHERE date(created_at)=date('now') AND status IN ({marks}){mode_clause}", params).fetchone()
        return int(row["n"] or 0), float(row["total"] or 0)

    def market_ordered_today(self, market_id: str, mode: str | None = None) -> bool:
        mode_clause = " AND mode=?" if mode else ""
        params = (market_id, str(mode)) if mode else (market_id,)
        with self.connect(readonly=True) as conn:
            return bool(conn.execute("""SELECT 1 FROM orders WHERE market_id=? AND date(created_at)=date('now')
                AND status NOT IN ('failed','simulation_error','blocked')""" + mode_clause + " LIMIT 1", params).fetchone())

    def active_live_order_for_market(self, market_id: str):
        statuses = ("preparing","unsigned_ready","sent","pending_fill","partial_fill","unknown_send")
        marks = ",".join("?" for _ in statuses)
        with self.connect(readonly=True) as conn:
            return conn.execute(f"SELECT * FROM orders WHERE mode='live' AND market_id=? AND status IN ({marks}) "
                                "ORDER BY id DESC LIMIT 1", (market_id, *statuses)).fetchone()

    def active_live_order_for_event(self, event_id: str):
        if not event_id:
            return None
        statuses = ("preparing","unsigned_ready","sent","pending_fill","partial_fill","unknown_send")
        marks = ",".join("?" for _ in statuses)
        with self.connect(readonly=True) as conn:
            return conn.execute(f"SELECT * FROM orders WHERE mode='live' AND event_id=? AND status IN ({marks}) "
                                "ORDER BY id DESC LIMIT 1", (event_id, *statuses)).fetchone()

    def event_orders_today(self, event_id: str, mode: str | None = None) -> int:
        mode_clause = " AND mode=?" if mode else ""
        params = (event_id, str(mode)) if mode else (event_id,)
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) n FROM orders WHERE event_id=? AND date(created_at)=date('now')
                AND status NOT IN ('failed','simulation_error','blocked')""" + mode_clause, params).fetchone()
        return int(row["n"] or 0)

    def asset_orders_today(self, asset: str, mode: str | None = None) -> int:
        mode_clause = " AND mode=?" if mode else ""
        params = (asset, str(mode)) if mode else (asset,)
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) n FROM orders WHERE asset=? AND date(created_at)=date('now')
                AND status NOT IN ('failed','simulation_error','blocked')""" + mode_clause, params).fetchone()
        return int(row["n"] or 0)

    def paper_open_summary(self) -> tuple[int, float, int]:
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) n,COALESCE(SUM(amount_usd),0) exposure,
                COUNT(DISTINCT NULLIF(event_id,'')) events FROM orders
                WHERE mode='paper' AND status='paper_filled'""").fetchone()
        return int(row["n"] or 0), float(row["exposure"] or 0), int(row["events"] or 0)

    def paper_open_for_asset(self, asset: str) -> tuple[int, float]:
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) n,COALESCE(SUM(amount_usd),0) exposure FROM orders
                WHERE mode='paper' AND status='paper_filled' AND asset=?""", (asset,)).fetchone()
        return int(row["n"] or 0), float(row["exposure"] or 0)

    def paper_correlated_exposure(self, event_family: str) -> float:
        if not event_family:
            return 0.0
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COALESCE(SUM(amount_usd),0) exposure FROM orders
                WHERE mode='paper' AND status='paper_filled' AND event_family=?""",
                (event_family,)).fetchone()
        return float(row["exposure"] or 0)

    def live_open_summary(self) -> tuple[int, float, int]:
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) n,COALESCE(SUM(cost_usd),0) exposure,
                COUNT(DISTINCT NULLIF(event_id,'')) events FROM positions
                WHERE status IN ('open','active','pending','claimable') AND claimed=0""").fetchone()
        return int(row["n"] or 0), float(row["exposure"] or 0), int(row["events"] or 0)

    def open_positions_for_asset(self, asset: str) -> tuple[int, float]:
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) n,COALESCE(SUM(cost_usd),0) exposure FROM positions
                WHERE asset=? AND status IN ('open','active','pending','claimable')""", (asset,)).fetchone()
        return int(row["n"] or 0), float(row["exposure"] or 0)

    def correlated_exposure(self, event_family: str) -> float:
        if not event_family:
            return 0.0
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COALESCE(SUM(cost_usd),0) exposure FROM positions
                WHERE event_family=? AND status IN ('open','active','pending','claimable')""", (event_family,)).fetchone()
            pending = conn.execute("""SELECT COALESCE(SUM(amount_usd),0) exposure FROM orders
                WHERE event_family=? AND status IN ('preparing','unsigned_ready','sent','pending_fill','partial_fill','unknown_send','filled')""",
                (event_family,)).fetchone()
        return float(row["exposure"] or 0) + float(pending["exposure"] or 0)

    def paper_orders_for_refresh(self, limit: int, refresh_minutes: int) -> list[sqlite3.Row]:
        with self.connect(readonly=True) as conn:
            return conn.execute("""SELECT o.*,s.question,s.price signal_price FROM orders o
                LEFT JOIN signals s ON s.id=o.signal_id WHERE o.mode='paper' AND o.status='paper_filled'
                AND (o.paper_updated_at IS NULL OR julianday(o.paper_updated_at)<julianday('now',?))
                ORDER BY COALESCE(o.paper_updated_at,o.created_at),o.id LIMIT ?""",
                (f"-{max(1,int(refresh_minutes))} minutes", max(1,int(limit)))).fetchall()

    def update_paper_order(self, order_id: int, *, status: str, mark_price: float, value_usd: float,
                           pnl_usd: float, result: str, entry_price: float | None = None,
                           shares: float | None = None, response_patch: dict | None = None) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT response_json FROM orders WHERE id=?", (order_id,)).fetchone()
            try:
                payload = json.loads(row["response_json"] or "{}") if row else {}
            except Exception:
                payload = {}
            if response_patch:
                payload.setdefault("paper_tracking", {}).update(response_patch)
            conn.execute("""UPDATE orders SET status=?,paper_mark_price=?,paper_value_usd=?,paper_pnl_usd=?,
                paper_result=?,paper_updated_at=?,response_json=?,paper_entry_price=COALESCE(paper_entry_price,?),
                paper_shares=COALESCE(paper_shares,?) WHERE id=?""",
                (status, mark_price, value_usd, pnl_usd, result, now(),
                 json.dumps(payload, ensure_ascii=False, default=str), entry_price, shares, order_id))

    def paper_summary(self) -> dict:
        with self.connect(readonly=True) as conn:
            row = conn.execute("""SELECT COUNT(*) total,
                SUM(CASE WHEN status='paper_filled' THEN 1 ELSE 0 END) open_count,
                SUM(CASE WHEN status='paper_won' THEN 1 ELSE 0 END) won,
                SUM(CASE WHEN status='paper_lost' THEN 1 ELSE 0 END) lost,
                SUM(CASE WHEN status='paper_refunded' THEN 1 ELSE 0 END) refunded,
                COALESCE(SUM(paper_pnl_usd),0) pnl,COALESCE(SUM(amount_usd),0) staked
                FROM orders WHERE mode='paper'""").fetchone()
        won, lost = int(row["won"] or 0), int(row["lost"] or 0)
        settled = won + lost
        return {"total": int(row["total"] or 0), "open": int(row["open_count"] or 0),
                "won": won, "lost": lost, "refunded": int(row["refunded"] or 0),
                "settled": settled, "win_rate": round(won / settled, 4) if settled else None,
                "pnl_usd": round(float(row["pnl"] or 0), 4), "staked_usd": round(float(row["staked"] or 0), 4)}

    def upsert_position(self, p: dict) -> None:
        market = self.get_market(str(p.get("market_id") or ""))
        asset = str((market["asset"] if market else "") or p.get("asset") or "")
        family = str((market["event_family"] if market else "") or p.get("event_family") or "")
        with self.connect() as conn:
            conn.execute("""INSERT INTO positions(position_key,market_id,event_id,asset,event_family,event_title,
                outcome,shares,entry_price,mark_price,sell_price,cost_usd,value_usd,pnl_usd,pnl_after_fees_usd,
                fees_paid_usd,realized_pnl_usd,payout_usd,no_exit_price,close_time,status,question,claimable,
                claimed,raw_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(position_key) DO UPDATE SET market_id=excluded.market_id,event_id=excluded.event_id,
                asset=excluded.asset,event_family=excluded.event_family,event_title=excluded.event_title,
                outcome=excluded.outcome,shares=excluded.shares,entry_price=excluded.entry_price,
                mark_price=excluded.mark_price,sell_price=excluded.sell_price,cost_usd=excluded.cost_usd,
                value_usd=excluded.value_usd,pnl_usd=excluded.pnl_usd,
                pnl_after_fees_usd=excluded.pnl_after_fees_usd,fees_paid_usd=excluded.fees_paid_usd,
                realized_pnl_usd=excluded.realized_pnl_usd,payout_usd=excluded.payout_usd,
                no_exit_price=excluded.no_exit_price,close_time=excluded.close_time,status=excluded.status,
                question=excluded.question,claimable=excluded.claimable,claimed=excluded.claimed,
                raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                (p.get("position_key", ""), p.get("market_id", ""), p.get("event_id", ""), asset, family,
                 p.get("event_title", ""), p.get("outcome", ""), p.get("shares", 0), p.get("entry_price", 0),
                 p.get("mark_price", 0), p.get("sell_price", 0), p.get("cost_usd", 0), p.get("value_usd", 0),
                 p.get("pnl_usd", 0), p.get("pnl_after_fees_usd", p.get("pnl_usd", 0)), p.get("fees_paid_usd", 0),
                 p.get("realized_pnl_usd", 0), p.get("payout_usd", 0), 1 if p.get("no_exit_price") else 0,
                 p.get("close_time"), p.get("status", "open"), p.get("question", ""),
                 1 if p.get("claimable") else 0, 1 if p.get("claimed") else 0,
                 json.dumps(p.get("raw", {}), ensure_ascii=False, default=str), now()))

    def get_market(self, market_id: str):
        if not market_id:
            return None
        with self.connect(readonly=True) as conn:
            return conn.execute("SELECT * FROM markets WHERE market_id=?", (market_id,)).fetchone()

    def latest_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.connect(readonly=True) as conn:
            return conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def add_claim(self, position_key: str, market_id: str, status: str,
                  signature: str = "", response: dict | None = None) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO claims(position_key,market_id,status,signature,response_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (position_key, market_id, status, signature,
                 json.dumps(response or {}, ensure_ascii=False, default=str), now()),
            )
            return int(cur.lastrowid)

    def recent_log(self, kind: str, market_id: str, minutes: int = 30) -> bool:
        with self.connect(readonly=True) as conn:
            return bool(conn.execute("""SELECT 1 FROM lifecycle_log WHERE kind=? AND market_id=?
                AND julianday(at)>=julianday('now',?) LIMIT 1""", (kind, market_id, f"-{int(minutes)} minutes")).fetchone())

    def log(self, kind: str, market_id: str, detail: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO lifecycle_log(at,kind,market_id,detail) VALUES(?,?,?,?)",
                         (now(), kind, market_id, detail[:6000]))
            if kind in {"error", "incident", "unknown_send", "simulation_error"}:
                conn.execute("INSERT INTO incidents(severity,kind,market_id,detail,created_at) VALUES(?,?,?,?,?)",
                             ("error", kind, market_id, detail[:6000], now()))
