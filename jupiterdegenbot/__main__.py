from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)

import typer
from rich.console import Console
from rich.table import Table

from .audit import export_audit
from .calibration import calculate_calibration
from .config import Settings
from .crypto_data import CryptoDataClient
from .engine import BotEngine
from .http import HttpClient
from .history import HistoricalDataManager
from .backtest import WalkForwardResearch
from .live_gate import LiveValidationGate
from .micro_live import MicroLiveGate, MICRO_LIVE_CONFIRMATION
from .instance_lock import bot_instance_lock, old_android_bot_running, pc_bot_processes
from .jupiter import JupiterClient
from .lifecycle import LiveLifecycle
from .local_ai import LocalAIReviewer
from .market_parser import parse_crypto_market
from .memory import available_gb
from .research_ml import NeuralModelManager
from .scheduler import run_forever
from .auto_training import AutoNeuralTrainer
from .source_test import run_source_tests
from .storage import DB
from .wallet import Wallet

VERSION = "1.0.0"
app = typer.Typer(help=f"JupiterDegenEdgeBot PC v{VERSION} — BTC/ETH/SOL/XRP/HYPE/DOGE/BNB, PAPER par défaut, LIVE permanent configurable")
console = Console()


def context() -> tuple[Settings, DB]:
    settings = Settings()
    settings.ensure_dirs()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[logging.FileHandler(PROJECT_ROOT / "logs" / "jupiter_degen.log", encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )
    return settings, DB(settings.database_path)


def _node_version() -> tuple[str, str]:
    try:
        proc = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10, check=False)
        return ("OK" if proc.returncode == 0 else "ERREUR", proc.stdout.strip() or proc.stderr.strip())
    except Exception as exc:
        return "ERREUR", str(exc)


def _print_rows(title: str, rows, columns: list[str]) -> None:
    table = Table(title=title)
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*(str(row[column] if column in row.keys() else "") for column in columns))
    console.print(table)


@app.command()
def doctor() -> None:
    """Diagnostic local et réseau en lecture seule."""
    s, db = context()
    table = Table(title=f"Diagnostic JupiterDegenEdgeBot PC v{VERSION}")
    table.add_column("Élément"); table.add_column("État"); table.add_column("Détail")
    table.add_row("Python", "OK" if sys.version_info >= (3, 11) else "ERREUR", sys.version.split()[0])
    free = available_gb()
    table.add_row("Mémoire", "OK" if free >= s.min_free_memory_gb else "WARN", f"{free:.2f} Go libres")
    table.add_row("Base séparée", "OK", str(db.path.resolve()))
    table.add_row("Actifs", "OK", ", ".join(s.crypto_assets))
    table.add_row("Timeframes", "OK", ", ".join(s.crypto_timeframes))
    table.add_row("Sources", "OK", ", ".join(s.crypto_sources))
    table.add_row("Barrière", "VERROUILLÉE", "DEGEN_QUANT uniquement; marchés ambigus refusés")
    table.add_row("Mode", s.trading_mode.upper(), f"AUTO_EXECUTE={s.auto_execute}")
    release_blocked = not s.release_live_capable or not s.live_release_enabled
    table.add_row("LIVE release", "BLOQUÉ" if release_blocked else "ACTIVÉ",
                  f"MICRO-LIVE={s.micro_live_enabled} · release={s.live_release_enabled}")
    gate = LiveValidationGate(s, db).evaluate(persist=False)
    table.add_row("Verrou statistique long terme", "OK" if gate["passed"] else "BLOQUÉ", " | ".join(gate["reasons"][:3]) or "critères validés")
    micro = MicroLiveGate(s, db).evaluate(persist=False)
    table.add_row("LIVE permanent", "OK" if micro["passed"] else "BLOQUÉ",
                  " | ".join(micro["reasons"][:3]) or "sans expiration · limites quotidiennes actives")
    node_state, node_detail = _node_version()
    table.add_row("Node.js", node_state, node_detail or "requis pour un futur LIVE")
    ai_state = LocalAIReviewer(s).status()
    ai_ok = bool(ai_state.get("available") and ai_state.get("installed"))
    table.add_row("IA locale", "OK" if ai_ok else ("DÉSACTIVÉE" if not s.local_ai_enabled else "WARN"),
                  f"{ai_state.get('model')} · {ai_state.get('detail')}")
    profiles = db.learning_profiles()
    active_profiles = sum(1 for row in profiles if bool(row["active"]))
    shadow = db.shadow_summary()
    table.add_row("Apprentissage fantôme", "ACTIF" if s.shadow_learning_enabled else "DÉSACTIVÉ",
                  f"{shadow['resolved']} réglées / {shadow['total']} prédites · {shadow['resolved_today']} aujourd'hui")
    table.add_row("Exploration PAPER", "ACTIVE" if s.paper_exploration_enabled else "DÉSACTIVÉE",
                  f"max {s.paper_exploration_max_per_cycle}/cycle · mise {s.paper_exploration_stake_usd:.2f}$")
    table.add_row("PAPER parallèle LIVE", "ACTIF" if getattr(s, "paper_parallel_live_enabled", False) else "DÉSACTIVÉ",
                  "PAPER isolé du wallet et des limites LIVE")
    table.add_row("Mémoire adaptative", "ACTIVE" if active_profiles else "EN APPRENTISSAGE",
                  f"{active_profiles}/{len(profiles)} profil(s) actif(s), minimum {s.adaptive_min_settled} labels réglés")
    auto = AutoNeuralTrainer(s, db).status()
    table.add_row("Auto-train neuronal", "PRÊT" if auto.get("ready") else "EN ATTENTE",
                  f"nouveaux labels {auto.get('new_labels', 0)} · modèles actifs {auto.get('active_models', 0)}")
    history = db.history_storage_status(s.history_max_db_gb)
    table.add_row("Historique SQLite", "OK" if history["under_budget"] else "WARN",
                  f"{history['size_mb']:.2f} Mo / budget {history['budget_gb']:.1f} Go")
    table.add_row("Jupiter API", "CONFIGURÉE" if s.jupiter_api_key else "À CONFIGURER", "clé présente" if s.jupiter_api_key else "JUPITER_API_KEY vide")
    table.add_row("Ancien bot", "WARN" if old_android_bot_running() else "OK", "processus ancien détecté" if old_android_bot_running() else "aucun")
    console.print(table)


@app.command("live-doctor")
def live_doctor() -> None:
    """Contrôles LIVE en lecture seule, sans créer ni signer de transaction."""
    s, db = context()
    table = Table(title="Contrôle LIVE fail-closed v1.0.0")
    table.add_column("Contrôle"); table.add_column("Résultat"); table.add_column("Détail")
    table.add_row("RELEASE_LIVE_CAPABLE", "OK" if s.release_live_capable else "BLOQUÉ", str(s.release_live_capable))
    table.add_row("LIVE_RELEASE_ENABLED", "OK" if s.live_release_enabled else "BLOQUÉ", str(s.live_release_enabled))
    table.add_row("TRADING_MODE", "OK" if s.trading_mode == "live" else "NON ACTIF", s.trading_mode)
    table.add_row("AUTO_EXECUTE", "OK" if s.auto_execute else "NON ACTIF", str(s.auto_execute))
    table.add_row("LIVE_CONFIRMATION", "OK" if s.live_confirmation == "I_ACCEPT_REAL_MONEY_RISK" else "BLOQUÉ", "phrase exacte requise")
    table.add_row("MICRO confirmation", "OK" if s.micro_live_confirmation == MICRO_LIVE_CONFIRMATION else "BLOQUÉ", "phrase exacte requise")
    table.add_row("Simulation", "OK" if s.live_simulate_before_send else "ERREUR", str(s.live_simulate_before_send))
    table.add_row("Position management", "DÉSACTIVÉ" if not s.position_management_enabled else "ACTIF", str(s.position_management_enabled))
    micro = MicroLiveGate(s, db).evaluate(persist=True)
    table.add_row("Verrou LIVE permanent", "OK" if micro["passed"] else "BLOQUÉ", " | ".join(micro["reasons"][:6]) or "sans expiration · limites quotidiennes actives")
    gate = LiveValidationGate(s, db).evaluate(persist=True)
    table.add_row("Verrou LIVE long terme", "OK" if gate["passed"] else "BLOQUÉ", " | ".join(gate["reasons"][:4]) or "tous les critères validés")
    duplicates = pc_bot_processes()
    table.add_row("Autre version active", "BLOQUÉ" if duplicates else "OK", str(duplicates) if duplicates else "aucune")
    console.print(table)


@app.command("micro-live-status")
def micro_live_status() -> None:
    """État du LIVE permanent et de ses limites quotidiennes."""
    s, db = context()
    result = MicroLiveGate(s, db).evaluate(persist=True)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if not result["passed"]:
        raise typer.Exit(3)


@app.command("scan-once")
def scan_once() -> None:
    s, db = context()
    with bot_instance_lock():
        summary = BotEngine(s, db).scan_once()
    console.print_json(json.dumps(summary, ensure_ascii=False, default=str))


@app.command()
def run() -> None:
    s, db = context()
    run_forever(s, db)


@app.command("wallet-balance")
def wallet_balance() -> None:
    s, _ = context()
    balances = Wallet(s).balances(force=True)
    console.print({"owner": balances.owner, "SOL": balances.sol, "USDC": balances.usdc, "JupUSD": balances.jupusd, "rpc": balances.rpc_url})


@app.command()
def positions() -> None:
    s, db = context()
    if s.trading_mode == "live" and s.jupiter_api_key:
        lifecycle = LiveLifecycle(s, db, JupiterClient(s), Wallet(s))
        lifecycle.sync_positions()
    rows = db.position_rows()
    _print_rows("Positions LIVE enregistrées", rows, ["position_key", "asset", "market_id", "outcome", "cost_usd", "value_usd", "pnl_after_fees_usd", "status", "claimable"])


@app.command()
def reconcile() -> None:
    s, db = context()
    report = LiveLifecycle(s, db, JupiterClient(s), Wallet(s)).reconcile_orders()
    console.print_json(json.dumps(report, ensure_ascii=False, default=str))


@app.command("claim-all")
def claim_all() -> None:
    s, db = context()
    report = LiveLifecycle(s, db, JupiterClient(s), Wallet(s)).claim_all()
    console.print_json(json.dumps(report, ensure_ascii=False, default=str))


@app.command("close-position")
def close_position(position_key: str) -> None:
    s, db = context()
    report = LiveLifecycle(s, db, JupiterClient(s), Wallet(s)).close_position(position_key)
    console.print_json(json.dumps(report, ensure_ascii=False, default=str))


@app.command("live-status")
def live_status() -> None:
    s, db = context()
    console.print({"mode": s.trading_mode, "auto_execute": s.auto_execute,
                   "release_enabled": s.live_release_enabled,
                   "micro_live_enabled": s.micro_live_enabled,
                   "persistent_live": True,
                   **db.live_summary()})


@app.command("ai-status")
def ai_status() -> None:
    """État Ollama et modèle local, sans lancer de pari."""
    s, _ = context()
    console.print_json(json.dumps(LocalAIReviewer(s).status(), ensure_ascii=False, default=str))


@app.command("learning-status")
def learning_status() -> None:
    """État de l'apprentissage fantôme, adaptatif et neuronal automatique."""
    s, db = context()
    profiles = BotEngine(s, db).quant.memory.rebuild() if s.adaptive_learning_enabled else []
    payload = {
        "shadow": db.shadow_summary(),
        "shadow_by_asset": [dict(row) for row in db.shadow_settled_by_asset()],
        "adaptive_profiles": [p.dict() for p in profiles],
        "auto_neural": AutoNeuralTrainer(s, db).status(),
    }
    console.print_json(json.dumps(payload, ensure_ascii=False, default=str))


@app.command("source-test")
def source_test() -> None:
    s, db = context()
    result = run_source_tests(s, db)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if not result.get("ok"):
        raise typer.Exit(1)


@app.command("degen-markets")
def degen_markets(limit: int = typer.Option(100, "--limit", min=1, max=1000)) -> None:
    s, db = context()
    markets = JupiterClient(s).markets()
    specs = {m.id: parse_crypto_market(m) for m in markets}
    db.upsert_markets(markets, specs)
    rows = []
    for m in markets[:limit]:
        spec = specs[m.id]
        rows.append({"asset": spec.asset, "market": m.id, "question": m.question[:80],
                     "comparator": spec.comparator, "close": m.close_time, "ambiguous": spec.ambiguous,
                     "reason": spec.reject_reason})
    table = Table(title="Marchés Degen Jupiter")
    for col in ["asset", "market", "question", "comparator", "close", "ambiguous", "reason"]:
        table.add_column(col)
    for row in rows:
        table.add_row(*(str(row[c]) for c in row))
    console.print(table)


@app.command("crypto-data")
def crypto_data(asset: str = typer.Argument("BTC")) -> None:
    s, db = context()
    snap = CryptoDataClient(s, HttpClient(s), db).fetch(asset.upper())
    console.print({"asset": snap.asset, "spot_median": snap.spot_median,
                   "dispersion": snap.spot_dispersion, "agreement": snap.source_agreement,
                   "sources": [{"source": x.source, "spot": x.spot, "timeframes": list(x.candles)} for x in snap.sources]})


@app.command("show-rejections")
def show_rejections(limit: int = typer.Option(100, "--limit", min=1, max=1000)) -> None:
    _, db = context()
    with db.connect(readonly=True) as conn:
        rows = conn.execute("""SELECT at,kind,market_id,detail FROM lifecycle_log
            WHERE kind IN ('signal_rejected','estimate_rejected','degen_unsupported','event_candidate_ranked_out')
            ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
    _print_rows("Refus récents", rows, ["at", "kind", "market_id", "detail"])


@app.command()
def calibrate() -> None:
    """Calcule Brier score, log-loss et courbe de calibration PAPER réglée."""
    _, db = context()
    result = calculate_calibration(db, persist=True)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))


@app.command("research-sync")
def research_sync(days: int = typer.Option(None, "--days", min=1, max=3650),
                  asset: str = typer.Option("", "--asset"),
                  timeframe: str = typer.Option("", "--timeframe")) -> None:
    """Télécharge l'historique profond avec pagination, cache et rate limiting."""
    s, db = context()
    manager = HistoricalDataManager(s, HttpClient(s), db)
    assets = [asset.upper()] if asset else s.research_history_assets
    timeframes = [timeframe] if timeframe else s.research_primary_timeframes
    result = manager.sync_all(days=days or s.research_history_days, assets=assets, timeframes=timeframes)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if not result.get("ok"):
        raise typer.Exit(2)


@app.command("research-backtest")
def research_backtest(max_rows_per_asset: int = typer.Option(6000, "--max-rows-per-asset", min=500, max=50000)) -> None:
    """Lance le walk-forward chronologique et entraîne le réseau borné."""
    s, db = context()
    result = WalkForwardResearch(s, db).run(max_rows_per_asset=max_rows_per_asset)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if not result.get("ok"):
        raise typer.Exit(2)


@app.command("research-status")
def research_status() -> None:
    s, db = context()
    # Deactivate incompatible artifacts before displaying status.
    NeuralModelManager(s, db)
    with db.connect(readonly=True) as conn:
        sync = conn.execute("SELECT * FROM research_sync ORDER BY id DESC LIMIT 20").fetchall()
        quality = conn.execute("SELECT * FROM data_quality ORDER BY id DESC LIMIT 20").fetchall()
        validation = conn.execute("SELECT * FROM validation_runs ORDER BY id DESC LIMIT 5").fetchall()
        models = conn.execute("SELECT model_key,train_samples,brier_score,log_loss,auc,active,trained_at FROM neural_models ORDER BY id DESC LIMIT 20").fetchall()
    console.print({"history_budget": db.history_storage_status(s.history_max_db_gb),
                   "sync": [dict(x) for x in sync], "quality": [dict(x) for x in quality],
                   "validation": [dict(x) for x in validation], "models": [dict(x) for x in models]})


@app.command("live-gate")
def live_gate() -> None:
    s, db = context()
    result = LiveValidationGate(s, db).evaluate(persist=True)
    console.print_json(json.dumps(result, ensure_ascii=False, default=str))
    if not result["passed"]:
        raise typer.Exit(3)


@app.command("audit-last-run")
def audit_last_run() -> None:
    _, db = context()
    result = export_audit(db)
    console.print(result)


@app.command("config-show")
def config_show() -> None:
    s, _ = context()
    console.print_json(json.dumps(s.public_dict(), ensure_ascii=False, default=str))


if __name__ == "__main__":
    app()
