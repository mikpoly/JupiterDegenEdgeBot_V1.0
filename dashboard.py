from __future__ import annotations

import base64
import html
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from jupiterdegenbot.config import Settings
from jupiterdegenbot.jupiter import JupiterClient
from jupiterdegenbot.positions import extract_position_rows, parse_position
from jupiterdegenbot.wallet import Wallet

from jupiterdegenbot.dashboard_utils import (
    ai_explanation_from_row,
    as_float,
    as_int,
    decision_summary_fr,
    display_frame,
    ensure_unique_columns,
    learning_progress,
    learning_stage,
    model_sources_from_row,
    parse_json_dict,
    settled_counts_by_asset,
)

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
DB = Path(os.getenv("DATABASE_PATH", "data/jupiter_degen.db"))
if not DB.is_absolute():
    DB = ROOT / DB

ASSET_COLORS = {
    "BTC": "#f7931a",
    "ETH": "#627eea",
    "SOL": "#14f195",
    "XRP": "#9aa8b4",
    "HYPE": "#56e8c3",
    "DOGE": "#c2a633",
    "BNB": "#f3ba2f",
}

TIMED_V2_MODEL_NAME = "DEGEN_QUANT_V6_TIMED_DIRECTION_V2"

st.set_page_config(page_title="Jupiter Degen Edge", page_icon="⚡", layout="wide")
st.markdown(
    """
<style>
:root{--n:#39ff14;--c:#00ffc8;--r:#ff426d;--a:#ffb020;--m:#7f9e86;--b:#020603;--p:#061009;--line:rgba(57,255,20,.24)}
html,body,[data-testid="stAppViewContainer"]{background:radial-gradient(circle at 50% -20%,#08230f 0,#020603 38%,#010301 100%);color:var(--n);font-family:"Cascadia Mono",Consolas,monospace}
[data-testid="stHeader"],#MainMenu,footer{background:transparent;visibility:hidden}.block-container{padding-top:1rem;max-width:1840px;padding-bottom:4rem}
h1,h2,h3,p,span,label,[data-testid="stMetricLabel"]{color:var(--n)!important}.stTabs [data-baseweb="tab-list"]{gap:1.2rem;border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{height:42px;padding:0;color:var(--n)}.stTabs [aria-selected="true"]{text-shadow:0 0 12px rgba(57,255,20,.65)}
.hero{position:relative;overflow:hidden;border:1px solid rgba(57,255,20,.42);background:linear-gradient(135deg,rgba(7,31,13,.97),rgba(2,6,3,.98));padding:22px;border-radius:16px;box-shadow:0 0 36px rgba(57,255,20,.09)}
.hero:after{content:"";position:absolute;inset:0;background:linear-gradient(transparent 49%,rgba(57,255,20,.025) 50%);background-size:100% 4px;pointer-events:none}.hero-glow{position:absolute;width:340px;height:340px;border-radius:50%;right:-120px;top:-210px;background:rgba(0,255,200,.10);filter:blur(28px)}
.kicker{color:var(--c);font-size:11px;letter-spacing:.18em}.title{font-size:36px;font-weight:900;text-shadow:0 0 18px rgba(57,255,20,.34);margin-top:6px}.subtitle{font-size:12px;color:#a1c8aa;margin-top:4px}
.pill{display:inline-block;border:1px solid rgba(57,255,20,.38);background:rgba(1,10,4,.62);padding:6px 9px;margin:12px 6px 0 0;border-radius:7px;font-size:10px}.pill.cyan{border-color:rgba(0,255,200,.5);color:var(--c)!important}.pill.warn{border-color:rgba(255,176,32,.55);color:var(--a)!important}
.card{border:1px solid var(--line);background:linear-gradient(145deg,rgba(7,20,10,.98),rgba(3,10,5,.98));padding:14px;border-radius:12px;min-height:91px;box-shadow:inset 0 0 22px rgba(57,255,20,.018)}.lab{font-size:10px;color:#77a77f;letter-spacing:.12em}.val{font-size:22px;font-weight:800;margin-top:8px}.small{font-size:12px;color:#b3ccb8;margin-top:6px}.bad{color:var(--r)!important}.warn{color:var(--a)!important}.cyan{color:var(--c)!important}.muted{color:#78907d!important}
.asset-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:10px 0 18px}.asset-card{border:1px solid var(--line);border-left:3px solid var(--asset);border-radius:12px;background:linear-gradient(145deg,#07130a,#030805);padding:13px;display:flex;gap:12px;align-items:center;min-height:82px}.asset-logo{width:43px;height:43px;filter:drop-shadow(0 0 8px color-mix(in srgb,var(--asset) 48%,transparent))}.asset-symbol{font-size:16px;font-weight:900}.asset-price{font-size:18px;font-weight:800;color:#e1ffe5}.asset-meta{font-size:10px;color:#7fa387;margin-top:4px}
.section-title{font-size:20px;font-weight:900;margin:8px 0 12px;text-transform:uppercase;letter-spacing:.04em}.decision{border:1px solid rgba(0,255,200,.38);background:linear-gradient(135deg,rgba(3,25,18,.96),rgba(4,11,7,.98));border-radius:15px;padding:18px;box-shadow:0 0 30px rgba(0,255,200,.07)}.decision-head{display:flex;align-items:center;gap:14px}.decision-logo{width:58px;height:58px}.decision-title{font-size:20px;font-weight:900;color:#e6ffeb}.outcome{display:inline-block;padding:5px 10px;border-radius:8px;border:1px solid var(--c);color:var(--c);font-size:13px;font-weight:900;margin-top:6px}.reason{margin-top:15px;padding:14px;border-left:3px solid var(--c);background:rgba(0,255,200,.045);color:#d8f7df;line-height:1.55;border-radius:0 10px 10px 0}.risk{margin-top:11px;padding:10px;border:1px solid rgba(255,176,32,.36);background:rgba(255,176,32,.06);border-radius:9px;color:#ffd28a}
.metric-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-top:14px}.mini{border:1px solid rgba(57,255,20,.18);background:rgba(1,8,3,.62);border-radius:9px;padding:10px}.mini-l{font-size:9px;letter-spacing:.1em;color:#74987d}.mini-v{font-size:17px;font-weight:900;color:#e4ffe8;margin-top:4px}
.learn-box{border:1px solid var(--line);background:#051008;border-radius:13px;padding:14px;margin-bottom:10px}.learn-head{display:flex;justify-content:space-between;gap:12px;font-size:12px}.bar{height:12px;background:#0c1d10;border-radius:999px;overflow:hidden;margin-top:9px;border:1px solid rgba(57,255,20,.12)}.bar-fill{height:100%;background:linear-gradient(90deg,#14f195,#39ff14,#00ffc8);box-shadow:0 0 13px rgba(57,255,20,.45)}.bar-sub{font-size:10px;color:#7fa188;margin-top:7px}.stage{display:inline-block;padding:5px 8px;border-radius:6px;background:rgba(0,255,200,.08);border:1px solid rgba(0,255,200,.28);color:var(--c);font-size:10px}
.callout{padding:12px 14px;border:1px solid var(--line);background:rgba(57,255,20,.035);border-radius:10px;color:#bddbc3;line-height:1.5}.empty{padding:18px;border:1px dashed rgba(57,255,20,.28);border-radius:11px;color:#78977f;text-align:center}
[data-testid="stDataFrame"]{border:1px solid var(--line);border-radius:11px;overflow:hidden}button{color:var(--n)!important}.stSelectbox label{color:#85a78d!important}
[data-testid="stRadio"]>div{gap:.35rem!important;flex-wrap:wrap}[data-testid="stRadio"] label{border:1px solid rgba(57,255,20,.22);border-radius:8px;padding:7px 10px;background:rgba(2,10,4,.72)}[data-testid="stRadio"] label:has(input:checked){border-color:rgba(0,255,200,.72);background:rgba(0,255,200,.08);box-shadow:0 0 16px rgba(0,255,200,.09)}
.refresh-note{border:1px solid rgba(0,255,200,.22);background:rgba(0,255,200,.035);border-radius:10px;padding:10px 12px;margin:8px 0 12px;color:#9fcdb1;font-size:11px}.refresh-live{color:var(--c);font-weight:800}.refresh-paused{color:var(--a);font-weight:800}
</style>
""",
    unsafe_allow_html=True,
)


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


@st.cache_data(ttl=20, show_spinner=False)
def read_wallet_direct() -> dict[str, Any]:
    """Fallback RPC used only when the latest run has no wallet snapshot."""
    try:
        balances = Wallet(Settings()).balances(force=True)
        return {
            "ok": True,
            "owner": balances.owner,
            "sol": float(balances.sol),
            "usdc": float(balances.usdc),
            "jupusd": float(balances.jupusd),
            "rpc": balances.rpc_url,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@st.cache_data(ttl=15, show_spinner=False)
def read_positions_direct() -> dict[str, Any]:
    """Read-only Jupiter snapshot used only after an explicit dashboard click."""
    try:
        settings = Settings()
        owner = Wallet(settings).owner()
        client = JupiterClient(settings)
        client.enable_fast_read_mode(timeout_seconds=6.0, max_retries=0)
        payload = client.positions(owner)
        rows = extract_position_rows(payload)
        parsed = [parse_position(row) for row in rows]
        active_statuses = {"open", "active", "pending", "closing", "closing_unknown"}
        active = [
            p for p in parsed
            if str(p.get("status") or "").casefold() in active_statuses
            and not bool(p.get("claimable"))
            and not bool(p.get("claimed"))
        ]
        claimable = [p for p in parsed if bool(p.get("claimable")) and not bool(p.get("claimed"))]
        return {
            "ok": True,
            "owner": owner,
            "active": active,
            "claimable": claimable,
            "rows": parsed,
            "checked_at": datetime.now().strftime("%H:%M:%S"),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "checked_at": datetime.now().strftime("%H:%M:%S")}



def _dashboard_optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dashboard_wallet_snapshot(candidate: Any) -> dict[str, Any] | None:
    # Valide un snapshot et refuse le faux fallback 0/0/0.
    if not isinstance(candidate, dict):
        return None

    sol = _dashboard_optional_float(candidate.get("sol"))
    usdc = _dashboard_optional_float(candidate.get("usdc"))
    jupusd = _dashboard_optional_float(candidate.get("jupusd"))

    if sol is None or usdc is None or jupusd is None:
        return None

    if max(sol, usdc, jupusd) <= 0.0:
        return None

    return {
        "sol": max(0.0, sol),
        "usdc": max(0.0, usdc),
        "jupusd": max(0.0, jupusd),
        "owner": str(candidate.get("owner") or ""),
        "rpc": str(candidate.get("rpc") or candidate.get("rpc_url") or ""),
    }


def _dashboard_snapshot_from_run_message(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None

    readiness = payload.get("live_readiness")
    funding = payload.get("live_funding")

    if isinstance(readiness, dict):
        balances = readiness.get("balances")
        snap = _dashboard_wallet_snapshot(balances)
        if snap is not None:
            return snap

        snap = _dashboard_wallet_snapshot(readiness)
        if snap is not None:
            return snap

    if isinstance(funding, dict):
        snap = _dashboard_wallet_snapshot(funding)
        if snap is not None:
            return snap

    return None


def _dashboard_last_good_wallet(runs: pd.DataFrame) -> tuple[dict[str, Any] | None, str]:
    if runs.empty:
        return None, ""

    for _, run_row in runs.iterrows():
        payload = parse_json_dict(run_row.get("message"))
        snapshot = _dashboard_snapshot_from_run_message(payload)
        if snapshot is None:
            continue

        run_id = run_row.get("id")
        stamp = (
            run_row.get("finished_at")
            or run_row.get("created_at")
            or run_row.get("started_at")
            or ""
        )

        source = f"dernier solde connu Â· run {run_id}"
        if stamp:
            source += f" Â· {stamp}"

        return snapshot, source

    return None, ""


def _dashboard_wallet_text(value: float | None, decimals: int, suffix: str = "") -> str:
    if value is None:
        return "â€”"
    return f"{value:.{decimals}f}{suffix}"


def exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def read(conn: sqlite3.Connection, query: str, params=()) -> pd.DataFrame:
    try:
        return ensure_unique_columns(pd.read_sql_query(query, conn, params=params))
    except Exception:
        return pd.DataFrame()


def total(df: pd.DataFrame, col: str) -> float:
    if df.empty or col not in df.columns:
        return 0.0
    return float(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())


def card(label: str, value: str, cls: str = "", sub: str = "") -> None:
    sub_html = f"<div class='small'>{esc(sub)}</div>" if sub else ""
    st.markdown(
        f"<div class='card'><div class='lab'>{esc(label)}</div><div class='val {esc(cls)}'>{esc(value)}</div>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def logo_uri(asset: str) -> str:
    path = ROOT / "assets" / "crypto" / f"{asset.upper()}.svg"
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def render_asset_grid(assets: list[str], latest_prices: dict[str, float], exposure: dict[str, float], settled: dict[str, int]) -> None:
    items = []
    for asset in assets:
        price = latest_prices.get(asset)
        price_text = f"${price:,.4f}" if price is not None and price < 100 else (f"${price:,.2f}" if price is not None else "EN ATTENTE")
        uri = logo_uri(asset)
        image = f"<img class='asset-logo' src='{uri}' alt='{esc(asset)}'>" if uri else ""
        items.append(
            f"<div class='asset-card' style='--asset:{ASSET_COLORS.get(asset, '#39ff14')}'>{image}<div>"
            f"<div class='asset-symbol'>{esc(asset)}</div><div class='asset-price'>{esc(price_text)}</div>"
            f"<div class='asset-meta'>EXPO {exposure.get(asset, 0.0):.2f} $ · RÉGLÉS {settled.get(asset, 0)}</div></div></div>"
        )
    st.markdown("<div class='asset-grid'>" + "".join(items) + "</div>", unsafe_allow_html=True)


def render_learning_bar(label: str, current: int, target: int, subtitle: str = "") -> None:
    pct = learning_progress(current, target)
    st.markdown(
        f"<div class='learn-box'><div class='learn-head'><strong>{esc(label)}</strong><span>{current}/{max(1, target)} · {pct*100:.0f}%</span></div>"
        f"<div class='bar'><div class='bar-fill' style='width:{pct*100:.2f}%'></div></div><div class='bar-sub'>{esc(subtitle)}</div></div>",
        unsafe_allow_html=True,
    )


def render_latest_decision(row: pd.Series | None) -> None:
    if row is None:
        st.markdown("<div class='empty'>Aucune position LIVE ou PAPER enregistrÃ©e pour le moment.</div>", unsafe_allow_html=True)
        return
    data = row.to_dict()
    decision_mode = str(data.get("decision_mode") or "PAPER").upper()
    created_raw = data.get("order_created_at")
    created_at = pd.to_datetime(created_raw, errors="coerce", utc=True)
    created_text = created_at.strftime("%Y-%m-%d %H:%M UTC") if not pd.isna(created_at) else "date inconnue"
    asset = str(data.get("asset") or "?").upper()
    outcome = str(data.get("outcome") or "?").upper()
    event_title = str(data.get("event_title") or data.get("signal_question") or data.get("market_id") or "Marché")
    uri = logo_uri(asset)
    image = f"<img class='decision-logo' src='{uri}' alt='{esc(asset)}'>" if uri else ""
    selected_probability = as_float(data.get("selected_probability"))
    entry = as_float(data.get("entry_price", data.get("paper_entry_price")))
    edge = as_float(data.get("edge"))
    confidence = as_float(data.get("confidence"))
    reliability = as_float(data.get("reliability"))
    spread = as_float(data.get("spread"))
    spread_ratio = spread / entry if entry > 0 else 0.0
    explanation = ai_explanation_from_row(data) or decision_summary_fr(data)
    summary = decision_summary_fr(data)
    if explanation and summary not in explanation:
        explanation = f"{summary} {explanation}"
    sources = model_sources_from_row(data)
    verdict = str(data.get("ai_verdict") or "en attente").upper()
    metrics = [
        ("PROBA CÔTÉ CHOISI", f"{selected_probability*100:.2f}%"),
        ("PRIX D'ENTRÉE", f"{entry*100:.2f}%"),
        ("EDGE", f"{edge*100:+.2f} pts"),
        ("CONFIANCE", f"{confidence*100:.1f}%"),
        ("FIABILITÉ", f"{reliability*100:.1f}%"),
        ("IA LOCALE", verdict),
    ]
    metric_html = "".join(f"<div class='mini'><div class='mini-l'>{esc(k)}</div><div class='mini-v'>{esc(v)}</div></div>" for k, v in metrics)
    risk_html = ""
    risks: list[str] = []
    if spread_ratio >= 0.30:
        risks.append(f"Spread relatif élevé : {spread_ratio*100:.1f}% du prix d'entrée")
    if as_float(data.get("liquidity")) <= 0:
        risks.append("Liquidité Jupiter non renseignée ou nulle")
    if as_float(data.get("volume_usd")) <= 0:
        risks.append("Volume Jupiter non renseigné ou nul")
    if risks:
        risk_html = "<div class='risk'>⚠ " + " · ".join(esc(x) for x in risks) + "</div>"
    source_text = ", ".join(sources) if sources else "sources quantitatives enregistrées dans l'audit"
    st.markdown(
        f"<div class='decision'><div class='decision-head'>{image}<div><div class='decision-title'>{esc(asset)} · {esc(event_title)}</div>"
        f"<span class='outcome'>{esc(decision_mode)} Â· POSITION {esc(outcome)}</span><div class='small'>CrÃ©Ã©e : {esc(created_text)} Â· Sources : {esc(source_text)}</div></div></div>"
        f"<div class='metric-row'>{metric_html}</div><div class='reason'><strong>POURQUOI CETTE POSITION ?</strong><br>{esc(explanation)}</div>{risk_html}</div>",
        unsafe_allow_html=True,
    )


mode = os.getenv("TRADING_MODE", "paper").upper()
auto = os.getenv("AUTO_EXECUTE", "false").lower() in {"1", "true", "yes", "on"}
release_live = os.getenv("LIVE_RELEASE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
micro_live_enabled = os.getenv("MICRO_LIVE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
micro_live_expires_at = os.getenv("MICRO_LIVE_EXPIRES_AT", "")
max_orders_per_day = max(1, int(os.getenv("MAX_ORDERS_PER_DAY", "1")))
max_live_stake_usd = max(0.0, float(os.getenv("MAX_LIVE_STAKE_USD", "5")))
max_open_positions_cfg = max(1, int(os.getenv("MAX_OPEN_POSITIONS", "1")))
max_total_open_exposure_cfg = max(0.0, float(os.getenv("MAX_TOTAL_OPEN_EXPOSURE_USD", "5")))
min_order_usd_cfg = max(0.0, float(os.getenv("MIN_ORDER_USD", "5")))
min_sol_balance_cfg = max(0.0, float(os.getenv("MIN_SOL_BALANCE", "0.005")))
local_ai_enabled = os.getenv("LOCAL_AI_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct-q4_K_M")
adaptive_min_settled = max(1, int(os.getenv("ADAPTIVE_MIN_SETTLED", "20")))
timed_direction_live_enabled = os.getenv("TIMED_DIRECTION_LIVE_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
timed_direction_live_min_settled = max(1, int(os.getenv("TIMED_DIRECTION_LIVE_MIN_SETTLED", "20")))
timed_direction_live_max_brier = max(0.0, float(os.getenv("TIMED_DIRECTION_LIVE_MAX_BRIER", "0.22")))
timed_direction_live_max_log_loss = max(0.0, float(os.getenv("TIMED_DIRECTION_LIVE_MAX_LOG_LOSS", "0.68")))
shadow_learning_enabled = os.getenv("SHADOW_LEARNING_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
paper_exploration_enabled = os.getenv("PAPER_EXPLORATION_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
auto_train_enabled = os.getenv("AUTO_NEURAL_TRAIN_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
history_budget_gb = max(0.1, float(os.getenv("HISTORY_MAX_DB_GB", "10")))
configured_assets = [x.strip().upper() for x in os.getenv("CRYPTO_ASSETS", "BTC,ETH,SOL,XRP,HYPE,DOGE,BNB").split(",") if x.strip()]
asset_label = " / ".join(configured_assets)
dashboard_refresh_seconds = max(0, int(os.getenv("DASHBOARD_AUTO_REFRESH_SECONDS", "15")))
if 0 < dashboard_refresh_seconds < 60:
    dashboard_refresh_seconds = 60
if "dashboard_auto_refresh_enabled" not in st.session_state:
    st.session_state["dashboard_auto_refresh_enabled"] = dashboard_refresh_seconds > 0
auto_refresh_enabled = st.toggle(
    "Actualisation douce automatique",
    key="dashboard_auto_refresh_enabled",
    help="Met à jour les données sans recharger entièrement la page. Désactive-la pendant une lecture longue.",
)
refresh_every = dashboard_refresh_seconds if auto_refresh_enabled and dashboard_refresh_seconds > 0 else None
refresh_pill = f"SOFT REFRESH {dashboard_refresh_seconds} S" if refresh_every else "REFRESH PAUSED"

st.markdown(
    f"""<div class='hero'><div class='hero-glow'></div><div class='kicker'>JUPITER PREDICTION · QUANT ENGINE + LOCAL AI GUARD · BUILD 0.3.1 CONTINUOUS LEARNING + PERSISTENT LIVE</div>
<div class='title'>JUPITER <span class='cyan'>DEGEN EDGE</span></div><div class='subtitle'>Scanner · parier en PAPER · apprendre sur chaque prédiction réglée · réentraîner quotidiennement</div>
<div><span class='pill'>MODE {esc(mode)}</span><span class='pill'>{esc(asset_label)}</span><span class='pill cyan'>MULTI-SOURCES</span>
<span class='pill cyan'>LOCAL AI {'ON' if local_ai_enabled else 'OFF'}</span><span class='pill'>ADAPTIVE MEMORY</span><span class='pill'>FAIL-CLOSED</span>
<span class='pill warn'>LIVE {'LIVE PERMANENT' if release_live and auto and micro_live_enabled else ('ARMED' if release_live and auto else 'LOCKED')}</span><span class='pill'>{esc(refresh_pill)}</span></div></div>""",
    unsafe_allow_html=True,
)

@st.fragment(run_every=refresh_every)
def render_live_dashboard() -> None:
    control_cols = st.columns([1, 4])
    with control_cols[0]:
        st.button("↻ Actualiser maintenant", use_container_width=True, key="dashboard_manual_refresh")
    with control_cols[1]:
        state_class = "refresh-live" if refresh_every else "refresh-paused"
        state_text = f"AUTO {dashboard_refresh_seconds} S" if refresh_every else "PAUSE"
        st.markdown(
            f"<div class='refresh-note'>Actualisation : <span class='{state_class}'>{esc(state_text)}</span> · "
            f"dernière lecture locale {datetime.now().strftime('%H:%M:%S')} · la section choisie reste mémorisée et le navigateur n'est plus rechargé.</div>",
            unsafe_allow_html=True,
        )
    if not DB.exists():
        st.error(f"Base absente : {DB}. Lance SCAN_ONCE.ps1.")
        return

    with connect() as conn:
        runs = read(conn, "SELECT * FROM runs ORDER BY id DESC LIMIT 500")
        positions = read(conn, "SELECT * FROM positions ORDER BY updated_at DESC LIMIT 1000") if exists(conn, "positions") else pd.DataFrame()
        markets = read(conn, "SELECT * FROM markets ORDER BY last_seen DESC LIMIT 1500") if exists(conn, "markets") else pd.DataFrame()
        prices = read(conn, "SELECT * FROM crypto_prices ORDER BY id DESC LIMIT 2000") if exists(conn, "crypto_prices") else pd.DataFrame()
        predictions = read(conn, "SELECT * FROM model_predictions ORDER BY id DESC LIMIT 1000") if exists(conn, "model_predictions") else pd.DataFrame()
        signals = read(conn, "SELECT * FROM signals ORDER BY id DESC LIMIT 1000") if exists(conn, "signals") else pd.DataFrame()
        orders_live = read(conn, """SELECT o.id,o.run_id,o.signal_id,o.created_at AS order_created_at,o.asset,o.market_id,o.event_id,o.outcome,
            o.amount_usd,o.status,o.order_pubkey,o.position_pubkey,o.signature,
            m.event_title,m.volume_usd,m.liquidity_usd,
            s.question AS signal_question,s.probability AS selected_probability,s.edge,s.confidence,s.reliability,s.source_agreement,
            s.entry_price AS entry_price,s.exit_price,s.spread,s.reasoning,s.evidence_json,
            ar.verdict AS ai_verdict,ar.available AS ai_available,ar.explanation AS ai_explanation,ar.flags_json AS ai_flags,ar.latency_ms AS ai_latency_ms
            FROM orders o
            LEFT JOIN signals s ON s.id=o.signal_id
            LEFT JOIN markets m ON m.market_id=o.market_id
            LEFT JOIN ai_reviews ar ON ar.id=(SELECT MAX(ar2.id) FROM ai_reviews ar2 WHERE ar2.signal_id=o.signal_id)
            WHERE o.mode='live' ORDER BY o.id DESC LIMIT 1000""") if exists(conn, "orders") else pd.DataFrame()
        orders_paper = read(conn, """SELECT o.id,o.run_id,o.signal_id,o.created_at AS order_created_at,o.asset,o.market_id,o.event_id,o.outcome,
            o.amount_usd,o.status,o.paper_entry_price AS entry_price,o.paper_shares,o.paper_mark_price,o.paper_value_usd,o.paper_pnl_usd,
            o.paper_result,o.paper_updated_at,m.event_title,m.volume_usd,m.liquidity_usd,
            s.question AS signal_question,s.probability AS selected_probability,s.edge,s.confidence,s.reliability,s.source_agreement,
            s.entry_price AS signal_entry_price,s.exit_price,s.spread,s.reasoning,s.evidence_json,
            ar.verdict AS ai_verdict,ar.available AS ai_available,ar.explanation AS ai_explanation,ar.flags_json AS ai_flags,ar.latency_ms AS ai_latency_ms
            FROM orders o
            LEFT JOIN signals s ON s.id=o.signal_id
            LEFT JOIN markets m ON m.market_id=o.market_id
            LEFT JOIN ai_reviews ar ON ar.id=(SELECT MAX(ar2.id) FROM ai_reviews ar2 WHERE ar2.signal_id=o.signal_id)
            WHERE o.mode='paper' ORDER BY o.id DESC LIMIT 1000""") if exists(conn, "orders") else pd.DataFrame()
        claims = read(conn, "SELECT * FROM claims ORDER BY id DESC LIMIT 500") if exists(conn, "claims") else pd.DataFrame()
        lifecycle = read(conn, "SELECT * FROM lifecycle_log ORDER BY id DESC LIMIT 1500") if exists(conn, "lifecycle_log") else pd.DataFrame()
        incidents = read(conn, "SELECT * FROM incidents ORDER BY id DESC LIMIT 500") if exists(conn, "incidents") else pd.DataFrame()
        calibration = read(conn, "SELECT * FROM calibration_results ORDER BY asset,horizon") if exists(conn, "calibration_results") else pd.DataFrame()
        ai_reviews = read(conn, "SELECT * FROM ai_reviews ORDER BY id DESC LIMIT 500") if exists(conn, "ai_reviews") else pd.DataFrame()
        learning_profiles = read(conn, "SELECT * FROM learning_profiles ORDER BY active DESC,asset,horizon,comparator") if exists(conn, "learning_profiles") else pd.DataFrame()
        shadow_predictions = read(conn, "SELECT * FROM shadow_predictions ORDER BY id DESC LIMIT 5000") if exists(conn, "shadow_predictions") else pd.DataFrame()
        timed_v2_gate = read(conn, """
            SELECT asset,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN status='RESOLVED' THEN 1 ELSE 0 END) AS resolved_count,
                   COUNT(DISTINCT CASE WHEN status='RESOLVED' THEN event_id END) AS events,
                   AVG(CASE WHEN status='RESOLVED' THEN brier_score END) AS brier,
                   AVG(CASE WHEN status='RESOLVED' THEN log_loss END) AS log_loss
            FROM shadow_predictions
            WHERE settlement_kind='timed_direction' AND model_name=?
            GROUP BY asset
            ORDER BY asset
        """, (TIMED_V2_MODEL_NAME,)) if exists(conn, "shadow_predictions") else pd.DataFrame()
        auto_training_runs = read(conn, "SELECT * FROM auto_training_runs ORDER BY id DESC LIMIT 20") if exists(conn, "auto_training_runs") else pd.DataFrame()
        research_sync = read(conn, "SELECT * FROM research_sync ORDER BY id DESC LIMIT 200") if exists(conn, "research_sync") else pd.DataFrame()
        data_quality = read(conn, """SELECT q.* FROM data_quality q JOIN (
            SELECT asset,source,timeframe,MAX(id) AS id FROM data_quality GROUP BY asset,source,timeframe
            ) latest ON latest.id=q.id ORDER BY q.asset,q.source,q.timeframe""") if exists(conn, "data_quality") else pd.DataFrame()
        derived_metrics = read(conn, "SELECT * FROM derived_metrics ORDER BY id DESC LIMIT 500") if exists(conn, "derived_metrics") else pd.DataFrame()
        neural_models = read(conn, "SELECT * FROM neural_models ORDER BY id DESC LIMIT 100") if exists(conn, "neural_models") else pd.DataFrame()
        validation_runs = read(conn, "SELECT * FROM validation_runs ORDER BY id DESC LIMIT 50") if exists(conn, "validation_runs") else pd.DataFrame()
        live_gate_checks = read(conn, "SELECT * FROM live_gate_checks ORDER BY id DESC LIMIT 20") if exists(conn, "live_gate_checks") else pd.DataFrame()
        live_orders_today = read(conn, "SELECT COUNT(*) AS n FROM orders WHERE mode='live' AND date(created_at)=date('now')") if exists(conn, "orders") else pd.DataFrame()
        counts = {}
        for table in ("observations", "model_predictions", "shadow_predictions", "signals", "ai_reviews", "candles", "feature_vectors"):
            if exists(conn, table):
                frame = read(conn, f"SELECT COUNT(*) AS n FROM {table}")
                counts[table] = as_int(frame.iloc[0]["n"]) if not frame.empty else 0
            else:
                counts[table] = 0

    if not neural_models.empty:
        neural_models = neural_models.copy()
        neural_models["model_status"] = neural_models.get("active", 0).apply(
            lambda value: "CHAMPION" if as_int(value) == 1 else "CANDIDAT REFUSÉ / EN ATTENTE"
        )

    last = runs.iloc[0].to_dict() if not runs.empty else {}
    message = parse_json_dict(last.get("message"))
    readiness = message.get("live_readiness") if isinstance(message.get("live_readiness"), dict) else {}
    maintenance = message.get("maintenance") if isinstance(message.get("maintenance"), dict) else {}

    # Le dashboard lit uniquement SQLite.
    # Aucun Wallet(...).balances(force=True) automatique ici.
    wallet: dict[str, Any] = {}
    wallet_source = ""
    wallet_error = ""

    current_snapshot = _dashboard_snapshot_from_run_message(message)
    if current_snapshot is not None:
        wallet = current_snapshot
        wallet_source = "dernier cycle"
    else:
        historical_wallet, historical_source = _dashboard_last_good_wallet(runs)
        if historical_wallet is not None:
            wallet = historical_wallet
            wallet_source = historical_source
            wallet_error = (
                "Le dernier cycle ne contient pas de solde wallet frais. "
                "Affichage du dernier solde valide enregistre localement."
            )
        else:
            wallet = {"sol": None, "usdc": None, "jupusd": None}
            wallet_source = "aucun snapshot local"
            wallet_error = (
                "Aucun solde wallet valide dans les derniers cycles. "
                "Le dashboard n'affiche plus de faux 0.00 pendant une panne RPC."
            )

    # Une position claimable est déjà réglée : elle ne doit jamais être comptée
    # comme POSITION LIVE ouverte. Les positions claimed sont également exclues.
    # DASHBOARD_OFFICIAL_POSITION_SNAPSHOT_V2
    # Les runs TIMED_FAST peuvent etre plus recents que le cycle principal.
    # On cherche donc le dernier run qui contient un instantane positions Jupiter complet.
    position_maintenance = maintenance if isinstance(maintenance, dict) else {}
    position_snapshot_run = last.get("id") if isinstance(last, dict) else None
    if "positions_snapshot_complete" not in position_maintenance:
        for _, _run_row in runs.iterrows():
            _payload = parse_json_dict(_run_row.get("message"))
            _maint = _payload.get("maintenance") if isinstance(_payload, dict) else None
            if isinstance(_maint, dict) and "positions_snapshot_complete" in _maint:
                position_maintenance = _maint
                position_snapshot_run = _run_row.get("id")
                break

    official_snapshot_complete = bool(position_maintenance.get("positions_snapshot_complete"))
    official_guards = position_maintenance.get("open_position_guards")
    if not isinstance(official_guards, list):
        official_guards = []
    official_position_keys = {
        str(item.get("position_key") or "")
        for item in official_guards
        if isinstance(item, dict) and str(item.get("position_key") or "")
    }

    open_pos = positions.copy()
    if official_snapshot_complete:
        # Source de verite pour le nombre de positions: dernier instantane Jupiter complet.
        if open_pos.empty or "position_key" not in open_pos.columns or not official_position_keys:
            open_pos = open_pos.iloc[0:0].copy() if not open_pos.empty else pd.DataFrame()
        else:
            open_pos = open_pos[open_pos["position_key"].astype(str).isin(official_position_keys)].copy()
        position_source = f"Jupiter officiel Â· run {position_snapshot_run}"
    else:
        # Fail-closed visuel: si Jupiter n'a pas fourni un instantane complet, on garde SQLite en secours.
        if not open_pos.empty:
            if "claimed" in open_pos.columns:
                open_pos = open_pos[pd.to_numeric(open_pos["claimed"], errors="coerce").fillna(0).ne(1)]
            if "claimable" in open_pos.columns:
                open_pos = open_pos[pd.to_numeric(open_pos["claimable"], errors="coerce").fillna(0).ne(1)]
            if "status" in open_pos.columns:
                open_pos = open_pos[open_pos["status"].astype(str).str.lower().isin([
                    "open", "active", "pending", "closing", "closing_unknown"
                ])]
        position_source = "SQLite secours Â· snapshot Jupiter incomplet"

    cost = total(open_pos, "cost_usd")
    value = total(open_pos, "value_usd")
    gross = total(open_pos, "pnl_usd")
    net = total(open_pos, "pnl_after_fees_usd") if "pnl_after_fees_usd" in open_pos.columns else gross
    fees = total(open_pos, "fees_paid_usd")
    realized = total(positions, "realized_pnl_usd")
    official_claims = position_maintenance.get("claims") if isinstance(position_maintenance.get("claims"), dict) else {}
    if official_snapshot_complete:
        claimable = max(0, as_int(official_claims.get("claimable")) - as_int(official_claims.get("claimed")))
    elif not positions.empty:
        claim_mask = pd.to_numeric(positions.get("claimable", pd.Series(0, index=positions.index)), errors="coerce").fillna(0).eq(1)
        if "claimed" in positions.columns:
            claim_mask &= pd.to_numeric(positions["claimed"], errors="coerce").fillna(0).ne(1)
        if "status" in positions.columns:
            claim_mask &= ~positions["status"].astype(str).str.lower().eq("claimed")
        claimable = int(claim_mask.sum())
    else:
        claimable = 0
    pnl_series = pd.to_numeric(open_pos.get("pnl_after_fees_usd", pd.Series(dtype=float)), errors="coerce").fillna(0)
    red = int((pnl_series < 0).sum())
    green = int((pnl_series > 0).sum())
    maintenance_errors: list[Any] = []
    for section in ("reconcile", "claims", "exits"):
        payload = maintenance.get(section) if isinstance(maintenance.get(section), dict) else {}
        maintenance_errors.extend(payload.get("errors") or [])
    alerts = len(message.get("errors") or []) + len(maintenance_errors)
    orders_today_live = as_int(live_orders_today.iloc[0]["n"]) if not live_orders_today.empty else 0

    wallet_usdc = _dashboard_optional_float(wallet.get("usdc"))
    wallet_jupusd = _dashboard_optional_float(wallet.get("jupusd"))
    wallet_sol = _dashboard_optional_float(wallet.get("sol"))

    funds_ok = (
        wallet_sol is not None
        and wallet_usdc is not None
        and wallet_jupusd is not None
        and wallet_sol >= min_sol_balance_cfg
        and max(wallet_usdc, wallet_jupusd) + 1e-9 >= min_order_usd_cfg
    )

    wallet_is_cached = wallet_source.startswith("dernier solde connu")

    if wallet_is_cached:
        wallet_state = "SOLDE CACHE"
        wallet_cls = "warn"
    elif readiness.get("ready") and wallet_sol is not None:
        wallet_state = "READY"
        wallet_cls = "cyan"
    elif funds_ok:
        wallet_state = "SOLDE OK / GATE PAUSE"
        wallet_cls = "warn"
    else:
        wallet_state = "SAFE / PAUSE"
        wallet_cls = "warn"

    cols = st.columns(5)
    with cols[0]: card("WALLET LIVE", wallet_state, wallet_cls, sub=f"source {wallet_source}")
    with cols[1]: card("USDC", _dashboard_wallet_text(wallet_usdc, 2, " $"))
    with cols[2]: card("JUPUSD", _dashboard_wallet_text(wallet_jupusd, 2, " $"))
    with cols[3]: card("SOL", _dashboard_wallet_text(wallet_sol, 5))
    with cols[4]: card("POSITIONS LIVE", str(len(open_pos)), sub=position_source)
    if wallet_error:
        st.warning(wallet_error)
    cols = st.columns(5)
    with cols[0]: card("CAPITAL ENGAGÉ", f"{cost:.2f} $")
    with cols[1]: card("VALEUR LIVE", f"{value:.2f} $")
    with cols[2]: card("P&L NET LIVE", f"{net:+.2f} $", "bad" if net < 0 else "cyan")
    with cols[3]: card("P&L BRUT", f"{gross:+.2f} $", "bad" if gross < 0 else "")
    with cols[4]: card("FRAIS PAYÉS", f"{fees:.2f} $")
    cols = st.columns(5)
    with cols[0]: card("P&L RÉALISÉ", f"{realized:+.2f} $", "bad" if realized < 0 else "")
    with cols[1]: card("CLAIMABLE", str(claimable))
    with cols[2]: card("ROUGES / VERTES", f"{red} / {green}", "bad" if red > green else "")
    with cols[3]: card("ORDRES LIVE DU JOUR", str(orders_today_live))
    with cols[4]: card("ALERTES DU CYCLE", str(alerts), "bad" if alerts else "")

    exposure = open_pos.groupby("asset")["cost_usd"].sum().to_dict() if not open_pos.empty and {"asset", "cost_usd"}.issubset(open_pos.columns) else {}
    latest_prices: dict[str, float] = {}
    if not prices.empty and {"asset", "price"}.issubset(prices.columns):
        for asset, group in prices.groupby(prices["asset"].astype(str).str.upper(), sort=False):
            latest_prices[str(asset)] = as_float(group.iloc[0]["price"], None)  # type: ignore[arg-type]
    settled_by_asset = settled_counts_by_asset(orders_paper, configured_assets)
    shadow_settled_by_asset = {asset: 0 for asset in configured_assets}
    if not shadow_predictions.empty and {"asset", "status"}.issubset(shadow_predictions.columns):
        resolved_shadow = shadow_predictions[shadow_predictions["status"].astype(str).str.upper().eq("RESOLVED")]
        for asset, group in resolved_shadow.groupby(resolved_shadow["asset"].astype(str).str.upper()):
            shadow_settled_by_asset[str(asset)] = len(group)
    learning_settled_by_asset = shadow_settled_by_asset if sum(shadow_settled_by_asset.values()) else settled_by_asset
    render_asset_grid(configured_assets, latest_prices, {str(k).upper(): as_float(v) for k, v in exposure.items()}, learning_settled_by_asset)

    section_options = ["COMMAND CENTER", "PAPER POSITIONS", "DEGEN MARKETS", "CRYPTO DATA", "SIGNALS / IA", "LEARNING", "RESEARCH / VALIDATION", "LIVE", "SYSTEM"]
    active_section = st.radio(
        "Navigation dashboard",
        section_options,
        horizontal=True,
        key="dashboard_active_section",
        label_visibility="collapsed",
    )

    if active_section == "COMMAND CENTER":
        st.markdown("<div class='section-title'>Dernière décision du moteur</div>", unsafe_allow_html=True)
        decision_frames = []
        if not orders_live.empty:
            live_decisions = orders_live.copy()
            live_decisions["decision_mode"] = "LIVE"
            decision_frames.append(live_decisions)
        if not orders_paper.empty:
            paper_decisions = orders_paper.copy()
            paper_decisions["decision_mode"] = "PAPER"
            decision_frames.append(paper_decisions)
        if decision_frames:
            latest_decisions = pd.concat(decision_frames, ignore_index=True, sort=False)
            latest_decisions["_decision_time"] = pd.to_datetime(
                latest_decisions.get("order_created_at"), errors="coerce", utc=True
            )
            latest_decisions = latest_decisions.sort_values(
                ["_decision_time", "id"], ascending=[False, False], na_position="last"
            )
            latest_order = latest_decisions.iloc[0]
        else:
            latest_order = None
        render_latest_decision(latest_order)

        st.markdown("<div class='section-title'>Cycle courant</div>", unsafe_allow_html=True)
        cycle_cols = st.columns(6)
        cycle_values = [
            ("RUN", str(last.get("id") or "—")),
            ("MARCHÉS", str(message.get("jupiter_degen_markets") or 0)),
            ("PRÉDICTIONS", str(message.get("degen_supported") or 0)),
            ("SIGNAUX", str(message.get("signals") or 0)),
            ("ORDRES PAPER", str(message.get("orders") or 0)),
            ("REVUES IA", str(message.get("ai_reviews") or 0)),
        ]
        for column, (label, val) in zip(cycle_cols, cycle_values):
            with column: card(label, val)
        outcome = str(message.get("cycle_outcome") or last.get("status") or "en attente")
        live_text = ('LIVE permanent actif' if mode == 'LIVE' and micro_live_enabled else 'verrouillé / PAPER')
        st.markdown(f"<div class='callout'><strong>ÉTAT :</strong> {esc(outcome)} · <strong>MODÈLE OLLAMA :</strong> {esc(ollama_model)} · <strong>LIVE :</strong> {esc(live_text)}.</div>", unsafe_allow_html=True)

        st.markdown("<div class='section-title'>Apprentissage adaptatif</div>", unsafe_allow_html=True)
        settled_total = sum(learning_settled_by_asset.values())
        active_profiles = int(pd.to_numeric(learning_profiles.get("active", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not learning_profiles.empty else 0
        stage = learning_stage(settled_total, adaptive_min_settled, active_profiles)
        learning_cols = st.columns([2, 1, 1, 1])
        with learning_cols[0]:
            render_learning_bar("Premier seuil de données réglées", settled_total, adaptive_min_settled, "Un profil précis actif exige ce minimum pour un même actif / horizon / comparateur.")
        with learning_cols[1]: card("PHASE", stage, "cyan")
        with learning_cols[2]: card("PROFILS ACTIFS", str(active_profiles))
        with learning_cols[3]: card("MÉMOIRE DB", f"{DB.stat().st_size / 1024**2:.2f} MB", sub=f"budget {history_budget_gb:.1f} GB")

        st.markdown("<div class='section-title'>UP/DOWN 5m/15m — GATE LIVE TIMED V2</div>", unsafe_allow_html=True)
        gate_lookup: dict[str, dict[str, Any]] = {}
        if not timed_v2_gate.empty and "asset" in timed_v2_gate.columns:
            for _, gate_row in timed_v2_gate.iterrows():
                gate_lookup[str(gate_row.get("asset") or "").upper()] = gate_row.to_dict()
        ready_count = 0
        for start in range(0, len(configured_assets), 4):
            chunk = configured_assets[start:start + 4]
            gcols = st.columns(len(chunk))
            for col, asset in zip(gcols, chunk):
                row = gate_lookup.get(asset, {})
                events = as_int(row.get("events"))
                brier_raw = row.get("brier")
                log_raw = row.get("log_loss")
                brier = _dashboard_optional_float(brier_raw)
                log_loss = _dashboard_optional_float(log_raw)
                ready = bool(
                    timed_direction_live_enabled
                    and events >= timed_direction_live_min_settled
                    and brier is not None and brier <= timed_direction_live_max_brier
                    and log_loss is not None and log_loss <= timed_direction_live_max_log_loss
                )
                ready_count += int(ready)
                brier_text = f"{brier:.4f}" if brier is not None else "—"
                log_text = f"{log_loss:.4f}" if log_loss is not None else "—"
                sub = (
                    f"Brier {brier_text} / {timed_direction_live_max_brier:.4f} · "
                    f"Log {log_text} / {timed_direction_live_max_log_loss:.4f} · "
                    f"events {events}/{timed_direction_live_min_settled} · "
                    f"open {as_int(row.get('open_count'))}"
                )
                with col:
                    card(f"{asset} LIVE READY", "TRUE" if ready else "FALSE", "cyan" if ready else "bad", sub=sub)
        st.caption(
            f"{ready_count}/{len(configured_assets)} actif(s) autorisé(s) par le gate TIMED V2. "
            "TRUE n'oblige jamais un pari : les autres garde-fous LIVE restent appliqués."
        )

    if active_section == "RESEARCH / VALIDATION":
        st.markdown("<div class='section-title'>Research / Validation professionnelle</div>", unsafe_allow_html=True)
        latest_validation = validation_runs.iloc[0].to_dict() if not validation_runs.empty else {}
        latest_gate = live_gate_checks.iloc[0].to_dict() if not live_gate_checks.empty else {}
        active_neural = int(pd.to_numeric(neural_models.get("active", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not neural_models.empty else 0
        quality_ok = int(pd.to_numeric(data_quality.get("passed", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not data_quality.empty else 0
        research_cols = st.columns(6)
        values = [
            ("BOUGIES HIST.", f"{counts.get('candles', 0):,}".replace(",", " ")),
            ("FEATURES", f"{counts.get('feature_vectors', 0):,}".replace(",", " ")),
            ("MODÈLES NEURAUX", str(active_neural)),
            ("QUALITÉ OK", f"{quality_ok}/{len(data_quality)}" if len(data_quality) else "EN ATTENTE"),
            ("BACKTEST", str(latest_validation.get("status") or "EN ATTENTE").upper()),
            ("LIVE GATE", "PASS" if as_int(latest_gate.get("passed")) == 1 else "BLOQUÉ"),
        ]
        for col, (label, value) in zip(research_cols, values):
            with col: card(label, value, "cyan" if value in {"PASS", "PASSED"} else ("warn" if value in {"BLOQUÉ", "FAILED"} else ""))
        if latest_validation:
            st.markdown("<div class='section-title'>Dernier walk-forward</div>", unsafe_allow_html=True)
            vcols = st.columns(6)
            metrics = [
                ("TESTS", as_int(latest_validation.get("test_samples"))),
                ("TRADES", as_int(latest_validation.get("trade_count"))),
                ("BRIER", f"{as_float(latest_validation.get('brier_score')):.4f}"),
                ("LOG-LOSS", f"{as_float(latest_validation.get('log_loss')):.4f}"),
                ("ROI SYNTH.", f"{as_float(latest_validation.get('roi_after_costs'))*100:+.2f}%"),
                ("DRAWDOWN", f"{as_float(latest_validation.get('max_drawdown'))*100:.2f}%"),
            ]
            for col, item in zip(vcols, metrics):
                with col: card(str(item[0]), str(item[1]))
            validation_metrics = parse_json_dict(latest_validation.get("metrics_json"))
            baseline_brier = as_float(latest_validation.get("baseline_brier"))
            model_brier = as_float(latest_validation.get("brier_score"))
            skill = baseline_brier - model_brier
            extra_cols = st.columns(5)
            extra = [
                ("BRIER RÉF.", f"{baseline_brier:.4f}"),
                ("SKILL BRIER", f"{skill:+.4f}"),
                ("FENÊTRES", str(as_int(validation_metrics.get("fold_count")))),
                ("TRAIN MIN / MAX", f"{as_int(latest_validation.get('train_samples'))} / {as_int(validation_metrics.get('max_train_samples'))}"),
                ("ANTI-FUITE", "PURGED" if validation_metrics.get("purged_temporal_split") else "NON"),
            ]
            for col, item in zip(extra_cols, extra):
                with col: card(str(item[0]), str(item[1]), "cyan" if item[1] == "PURGED" or (item[0] == "SKILL BRIER" and skill > 0) else "")
            per_asset = validation_metrics.get("per_asset") if isinstance(validation_metrics.get("per_asset"), dict) else {}
            if per_asset:
                per_asset_rows = []
                for asset, metric in per_asset.items():
                    if not isinstance(metric, dict):
                        continue
                    per_asset_rows.append({
                        "Actif": asset, "Échantillons": as_int(metric.get("samples")),
                        "Trades": as_int(metric.get("trades")),
                        "Brier": as_float(metric.get("brier_score")),
                        "Brier réf.": as_float(metric.get("baseline_brier")),
                        "ROI synth.": as_float(metric.get("roi_after_costs")),
                    })
                if per_asset_rows:
                    st.markdown("<div class='section-title'>Validation par actif</div>", unsafe_allow_html=True)
                    st.dataframe(pd.DataFrame(per_asset_rows), use_container_width=True, hide_index=True)
            st.caption("Le ROI walk-forward est synthétique à prix 0,50. Le découpage est groupé par timestamp et purgé des labels dont l'horizon chevauche la fenêtre de test. Le verrou LIVE exige aussi des résultats PAPER Jupiter réellement réglés.")
        else:
            st.markdown("<div class='empty'>Aucun backtest walk-forward. Lance RESEARCH_SYNC.ps1 puis RESEARCH_BACKTEST.ps1.</div>", unsafe_allow_html=True)
        st.markdown("<div class='section-title'>Qualité des historiques</div>", unsafe_allow_html=True)
        qtable = display_frame(data_quality, ["checked_at","asset","source","timeframe","row_count","missing_ratio","duplicate_count","stale","incomplete_dropped","passed"], {"checked_at":"Contrôle","asset":"Actif","source":"Source","timeframe":"TF","row_count":"Bougies","missing_ratio":"Manquantes","duplicate_count":"Doublons","stale":"Périmé","incomplete_dropped":"Bougies ouvertes retirées","passed":"OK"})
        st.dataframe(qtable, use_container_width=True, hide_index=True)
        st.markdown("<div class='section-title'>Réseaux neuronaux bornés</div>", unsafe_allow_html=True)
        ntable = display_frame(neural_models, ["trained_at","model_key","model_type","train_samples","positive_rate","brier_score","log_loss","auc","model_status"], {"trained_at":"Entraîné","model_key":"Modèle","model_type":"Type","train_samples":"Échantillons","positive_rate":"Classe +","brier_score":"Brier","log_loss":"Log-loss","auc":"AUC","model_status":"Statut gouvernance"})
        st.dataframe(ntable, use_container_width=True, hide_index=True)
        st.markdown("<div class='section-title'>Synchronisations historiques</div>", unsafe_allow_html=True)
        stable = display_frame(research_sync, ["finished_at","asset","source","timeframe","rows_written","pages","status","detail"], {"finished_at":"Fin","asset":"Actif","source":"Source","timeframe":"TF","rows_written":"Lignes","pages":"Pages","status":"Statut","detail":"Détail"})
        st.dataframe(stable, use_container_width=True, hide_index=True)
        st.markdown("<div class='section-title'>Données dérivées</div>", unsafe_allow_html=True)
        dtable = display_frame(derived_metrics, ["observed_at","asset","source","funding_rate","open_interest","oi_change","book_imbalance","book_spread_bps","basis_bps"], {"observed_at":"Observé","asset":"Actif","source":"Source","funding_rate":"Funding","open_interest":"Open interest","oi_change":"Δ OI","book_imbalance":"Imbalance","book_spread_bps":"Spread bps","basis_bps":"Basis bps"})
        st.dataframe(dtable, use_container_width=True, hide_index=True)
        if latest_gate:
            gate_reasons = parse_json_dict(latest_gate.get("reasons_json")) if isinstance(latest_gate.get("reasons_json"), dict) else None
            try:
                reasons = json.loads(str(latest_gate.get("reasons_json") or "[]"))
            except Exception:
                reasons = []
            st.markdown("<div class='section-title'>Verrou LIVE statistique</div>", unsafe_allow_html=True)
            st.markdown("<div class='callout'>" + ("Tous les critères sont validés." if not reasons else "<br>".join("• " + esc(x) for x in reasons)) + "</div>", unsafe_allow_html=True)

    if active_section == "PAPER POSITIONS":
        st.markdown("<div class='section-title'>PAPER — suivi séparé du LIVE</div>", unsafe_allow_html=True)
        if orders_paper.empty:
            st.markdown("<div class='empty'>Aucun ordre PAPER enregistré.</div>", unsafe_allow_html=True)
        else:
            paper_pnl = total(orders_paper, "paper_pnl_usd")
            paper_staked = total(orders_paper[orders_paper["paper_result"].astype(str).str.lower().eq("open")] if "paper_result" in orders_paper.columns else orders_paper, "amount_usd")
            won = int(orders_paper["paper_result"].astype(str).str.upper().eq("WON").sum()) if "paper_result" in orders_paper.columns else 0
            lost = int(orders_paper["paper_result"].astype(str).str.upper().eq("LOST").sum()) if "paper_result" in orders_paper.columns else 0
            pcols = st.columns(5)
            for col, item in zip(pcols, [("POSITIONS", len(orders_paper)), ("CAPITAL PAPER OUVERT", f"{paper_staked:.2f} $"), ("P&L PAPER", f"{paper_pnl:+.2f} $"), ("GAGNÉES", won), ("PERDUES", lost)]):
                with col: card(str(item[0]), str(item[1]), "bad" if item[0] == "P&L PAPER" and paper_pnl < 0 else "")
            table = display_frame(
                orders_paper,
                ["order_created_at", "asset", "event_title", "outcome", "amount_usd", "entry_price", "paper_mark_price", "paper_pnl_usd", "paper_result", "status", "ai_verdict"],
                {"order_created_at": "Créé", "asset": "Actif", "event_title": "Marché", "outcome": "Côté", "amount_usd": "Mise $", "entry_price": "Entrée", "paper_mark_price": "Prix actuel", "paper_pnl_usd": "P&L $", "paper_result": "Résultat", "status": "Statut", "ai_verdict": "IA"},
            )
            st.dataframe(table, use_container_width=True, hide_index=True)
            st.markdown("<div class='section-title'>Explication de chaque position</div>", unsafe_allow_html=True)
            for _, row in orders_paper.head(20).iterrows():
                label = f"{row.get('asset','?')} · {row.get('outcome','?')} · {row.get('event_title') or row.get('market_id')}"
                with st.expander(label):
                    st.write(ai_explanation_from_row(row) or decision_summary_fr(row))
                    st.caption(f"Edge {as_float(row.get('edge'))*100:+.2f} pts · confiance {as_float(row.get('confidence'))*100:.1f}% · fiabilité {as_float(row.get('reliability'))*100:.1f}%")

    if active_section == "DEGEN MARKETS":
        st.markdown("<div class='section-title'>Marchés Jupiter Degen</div>", unsafe_allow_html=True)
        if markets.empty:
            st.info("Aucun marché enregistré.")
        else:
            filter_assets = ["TOUS"] + sorted(markets["asset"].dropna().astype(str).str.upper().unique().tolist()) if "asset" in markets.columns else ["TOUS"]
            selected_asset = st.selectbox("Filtrer par actif", filter_assets)
            market_view = markets if selected_asset == "TOUS" else markets[markets["asset"].astype(str).str.upper().eq(selected_asset)]
            market_table = display_frame(
                market_view,
                ["last_seen", "asset", "event_title", "question", "comparator", "threshold_low", "threshold_high", "yes_price", "no_price", "sell_yes_price", "sell_no_price", "volume_usd", "liquidity_usd", "close_time"],
                {"last_seen": "Vu", "asset": "Actif", "event_title": "Événement", "question": "Outcome", "comparator": "Contrat", "threshold_low": "Seuil bas", "threshold_high": "Seuil haut", "yes_price": "YES", "no_price": "NO", "sell_yes_price": "Sortie YES", "sell_no_price": "Sortie NO", "volume_usd": "Volume $", "liquidity_usd": "Liquidité $", "close_time": "Échéance"},
            )
            st.dataframe(market_table, use_container_width=True, hide_index=True)

    if active_section == "CRYPTO DATA":
        st.markdown("<div class='section-title'>Prix multi-exchanges</div>", unsafe_allow_html=True)
        price_table = display_frame(prices, ["observed_at", "asset", "source", "price", "bid", "ask", "volume_24h"], {"observed_at": "Observé", "asset": "Actif", "source": "Source", "price": "Spot", "bid": "Bid", "ask": "Ask", "volume_24h": "Volume 24h"})
        st.dataframe(price_table, use_container_width=True, hide_index=True)
        st.markdown("<div class='section-title'>Prédictions quantitatives</div>", unsafe_allow_html=True)
        prediction_table = display_frame(predictions, ["created_at", "asset", "market_id", "comparator", "threshold_low", "threshold_high", "probability_yes", "confidence", "reliability", "source_agreement", "volatility"], {"created_at": "Créée", "asset": "Actif", "market_id": "Marché", "comparator": "Contrat", "threshold_low": "Seuil bas", "threshold_high": "Seuil haut", "probability_yes": "P(YES)", "confidence": "Confiance", "reliability": "Fiabilité", "source_agreement": "Accord sources", "volatility": "Volatilité"})
        st.dataframe(prediction_table, use_container_width=True, hide_index=True)

    if active_section == "SIGNALS / IA":
        st.markdown("<div class='section-title'>Signaux retenus</div>", unsafe_allow_html=True)
        signal_table = display_frame(signals, ["created_at", "asset", "market_id", "outcome", "price", "probability", "edge", "confidence", "reliability", "source_count", "source_agreement", "spread"], {"created_at": "Créé", "asset": "Actif", "market_id": "Marché", "outcome": "Côté", "price": "Prix", "probability": "Probabilité", "edge": "Edge", "confidence": "Confiance", "reliability": "Fiabilité", "source_count": "Sources", "source_agreement": "Accord", "spread": "Spread"})
        st.dataframe(signal_table, use_container_width=True, hide_index=True)
        st.markdown("<div class='section-title'>Contrôles IA locale</div>", unsafe_allow_html=True)
        if ai_reviews.empty:
            st.info(f"Aucune revue IA enregistrée. Modèle configuré : {ollama_model}")
        else:
            review_table = display_frame(ai_reviews, ["created_at", "market_id", "model", "verdict", "available", "confidence_penalty", "reliability_penalty", "explanation", "flags_json", "latency_ms"], {"created_at": "Créée", "market_id": "Marché", "model": "Modèle", "verdict": "Verdict", "available": "Disponible", "confidence_penalty": "Pénalité confiance", "reliability_penalty": "Pénalité fiabilité", "explanation": "Explication", "flags_json": "Alertes", "latency_ms": "Latence ms"})
            st.dataframe(review_table, use_container_width=True, hide_index=True)
        st.markdown("<div class='section-title'>Refus du moteur</div>", unsafe_allow_html=True)
        refusal_kinds = {"signal_rejected", "estimate_rejected", "degen_unsupported", "event_candidate_ranked_out", "local_ai_guard_rejected"}
        refus = lifecycle[lifecycle["kind"].isin(refusal_kinds)] if not lifecycle.empty and "kind" in lifecycle.columns else pd.DataFrame()
        st.dataframe(display_frame(refus, ["at", "kind", "market_id", "detail"], {"at": "Date", "kind": "Type", "market_id": "Marché", "detail": "Raison"}), use_container_width=True, hide_index=True)

    if active_section == "LEARNING":
        st.markdown("<div class='section-title'>Apprentissage continu réel</div>", unsafe_allow_html=True)
        shadow_total = len(shadow_predictions)
        shadow_resolved = int(shadow_predictions["status"].astype(str).str.upper().eq("RESOLVED").sum()) if not shadow_predictions.empty and "status" in shadow_predictions.columns else 0
        shadow_open = int(shadow_predictions["status"].astype(str).str.upper().eq("OPEN").sum()) if not shadow_predictions.empty and "status" in shadow_predictions.columns else 0
        resolved_today = 0
        if not shadow_predictions.empty and {"status", "resolved_at"}.issubset(shadow_predictions.columns):
            resolved_dates = pd.to_datetime(shadow_predictions["resolved_at"], errors="coerce", utc=True)
            today = pd.Timestamp.now(tz="UTC").date()
            resolved_today = int((shadow_predictions["status"].astype(str).str.upper().eq("RESOLVED") & resolved_dates.dt.date.eq(today)).sum())
        active_profiles = int(pd.to_numeric(learning_profiles.get("active", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not learning_profiles.empty else 0
        active_neural = int(pd.to_numeric(neural_models.get("active", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()) if not neural_models.empty else 0
        stage = learning_stage(shadow_resolved, adaptive_min_settled, active_profiles)
        st.markdown(
            f"<div class='callout'><span class='stage'>{esc(stage)}</span><br><br>"
            f"<strong>Apprentissage fantôme :</strong> {'ACTIF' if shadow_learning_enabled else 'DÉSACTIVÉ'} — chaque marché analysé devient un label, même sans pari. "
            f"<strong>Exploration PAPER :</strong> {'ACTIVE' if paper_exploration_enabled else 'DÉSACTIVÉE'}. "
            f"<strong>Réseau neuronal auto :</strong> {'ACTIF' if auto_train_enabled else 'DÉSACTIVÉ'}.</div>",
            unsafe_allow_html=True,
        )
        render_learning_bar("Labels réglés utilisables", shadow_resolved, adaptive_min_settled, "Le seuil s'applique séparément à chaque actif + horizon + type de contrat.")
        learning_cols = st.columns(6)
        brier_values = pd.to_numeric(shadow_predictions.get("brier_score", pd.Series(dtype=float)), errors="coerce").dropna() if not shadow_predictions.empty else pd.Series(dtype=float)
        avg_brier = float(brier_values.mean()) if not brier_values.empty else None
        with learning_cols[0]: card("PRÉDICTIONS FANTÔMES", str(shadow_total))
        with learning_cols[1]: card("EN ATTENTE", str(shadow_open))
        with learning_cols[2]: card("RÉGLÉES", str(shadow_resolved))
        with learning_cols[3]: card("RÉGLÉES AUJOURD'HUI", str(resolved_today))
        with learning_cols[4]: card("PROFILS ACTIFS", str(active_profiles))
        with learning_cols[5]: card("RÉSEAUX ACTIFS", str(active_neural), "cyan")
        metric_cols = st.columns(4)
        with metric_cols[0]: card("BRIER FANTÔME", f"{avg_brier:.4f}" if avg_brier is not None else "EN ATTENTE")
        with metric_cols[1]: card("OBSERVATIONS", f"{counts.get('observations', 0):,}".replace(",", " "))
        with metric_cols[2]: card("POSITIONS PAPER", str(len(orders_paper)))
        latest_auto = auto_training_runs.iloc[0].to_dict() if not auto_training_runs.empty else {}
        with metric_cols[3]: card("DERNIER AUTO-TRAIN", str(latest_auto.get("status") or "EN ATTENTE").upper(), "cyan" if str(latest_auto.get("status") or "").lower() == "ok" else "")

        st.markdown("<div class='section-title'>Progression par crypto</div>", unsafe_allow_html=True)
        for asset in configured_assets:
            subset = shadow_predictions[shadow_predictions["asset"].astype(str).str.upper().eq(asset)] if not shadow_predictions.empty and "asset" in shadow_predictions.columns else pd.DataFrame()
            resolved = subset[subset["status"].astype(str).str.upper().eq("RESOLVED")] if not subset.empty and "status" in subset.columns else pd.DataFrame()
            asset_brier = pd.to_numeric(resolved.get("brier_score", pd.Series(dtype=float)), errors="coerce").dropna() if not resolved.empty else pd.Series(dtype=float)
            active_for_asset = 0
            if not learning_profiles.empty and "asset" in learning_profiles.columns:
                prof = learning_profiles[learning_profiles["asset"].astype(str).str.upper().eq(asset)]
                active_for_asset = int(pd.to_numeric(prof.get("active", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
            subtitle = f"Fantômes total {len(subset)} · profils actifs {active_for_asset} · Brier {float(asset_brier.mean()):.4f}" if not asset_brier.empty else f"Fantômes total {len(subset)} · profils actifs {active_for_asset} · Brier en attente"
            render_learning_bar(f"{asset} — labels réglés", len(resolved), adaptive_min_settled, subtitle)

        st.markdown("<div class='section-title'>Dernières prédictions fantômes</div>", unsafe_allow_html=True)
        shadow_table = display_frame(shadow_predictions, ["first_seen_at", "asset", "question", "settlement_kind", "probability_yes", "market_yes_price", "selected_outcome", "selected_edge", "would_trade_strict", "would_trade_exploration", "status", "actual_yes", "brier_score", "resolved_at"], {"first_seen_at":"Créée", "asset":"Actif", "question":"Marché", "settlement_kind":"Type", "probability_yes":"P(YES)", "market_yes_price":"Prix YES", "selected_outcome":"Côté valeur", "selected_edge":"Edge", "would_trade_strict":"Strict", "would_trade_exploration":"Exploration", "status":"Statut", "actual_yes":"Résultat YES", "brier_score":"Brier", "resolved_at":"Réglée"})
        st.dataframe(shadow_table, use_container_width=True, hide_index=True)

        st.markdown("<div class='section-title'>Profils adaptatifs</div>", unsafe_allow_html=True)
        profile_table = display_frame(learning_profiles, ["asset", "horizon", "comparator", "sample_count", "brier_score", "residual_bias", "probability_adjustment", "confidence_multiplier", "active", "updated_at"], {"asset":"Actif", "horizon":"Horizon", "comparator":"Contrat", "sample_count":"Échantillon", "brier_score":"Brier", "residual_bias":"Biais", "probability_adjustment":"Correction proba", "confidence_multiplier":"Multiplicateur confiance", "active":"Actif", "updated_at":"Mis à jour"})
        st.dataframe(profile_table, use_container_width=True, hide_index=True)

    if active_section == "LIVE":
        st.markdown("<div class='section-title'>LIVE PERMANENT</div>", unsafe_allow_html=True)
        live_message = (
            f"LIVE permanent actif, sans date d'expiration. "
            f"Limites quotidiennes : {max_orders_per_day} ordre(s), "
            f"{max_live_stake_usd:.2f} USD maximum par ordre, "
            f"{max_open_positions_cfg} position(s) ouverte(s), "
            f"{max_total_open_exposure_cfg:.2f} USD d'exposition ouverte."
            if mode == 'LIVE' and micro_live_enabled
            else "LIVE inactif. Le bot reste en PAPER tant que le mode LIVE n'a pas ete valide."
        )
        st.markdown(f"<div class='callout'>{esc(live_message)} Les cartes LIVE n'intègrent jamais les ordres PAPER.</div>", unsafe_allow_html=True)
        st.caption("Les cartes du haut utilisent SQLite local. Les positions CLAIMABLE sont maintenant exclues du compteur de positions ouvertes.")
        if st.button("🔎 Vérifier les positions officielles Jupiter maintenant", key="verify_jupiter_positions", use_container_width=False):
            st.session_state["official_positions_snapshot"] = read_positions_direct()
        official_snapshot = st.session_state.get("official_positions_snapshot")
        if isinstance(official_snapshot, dict):
            if official_snapshot.get("ok"):
                official_active = official_snapshot.get("active") or []
                official_claimable = official_snapshot.get("claimable") or []
                ocols = st.columns(3)
                with ocols[0]: card("JUPITER OFFICIEL — OUVERTES", str(len(official_active)), "cyan")
                with ocols[1]: card("JUPITER OFFICIEL — CLAIMABLE", str(len(official_claimable)), "warn" if official_claimable else "cyan")
                with ocols[2]: card("VÉRIFIÉ À", str(official_snapshot.get("checked_at") or "—"))
                if len(official_active) != len(open_pos) or len(official_claimable) != claimable:
                    st.warning(
                        f"Écart détecté : SQLite local = {len(open_pos)} ouverte(s), {claimable} claimable(s) ; "
                        f"Jupiter officiel = {len(official_active)} ouverte(s), {len(official_claimable)} claimable(s). "
                        "Le bot doit encore réconcilier sa base locale."
                    )
            else:
                st.warning(f"Lecture Jupiter impossible : {official_snapshot.get('error') or 'erreur inconnue'}")
        st.subheader("Positions LIVE")
        st.dataframe(ensure_unique_columns(open_pos), use_container_width=True, hide_index=True)
        st.subheader("Ordres LIVE")
        st.dataframe(ensure_unique_columns(orders_live), use_container_width=True, hide_index=True)
        st.subheader("Claims")
        st.dataframe(ensure_unique_columns(claims), use_container_width=True, hide_index=True)

    if active_section == "SYSTEM":
        st.markdown("<div class='section-title'>Santé système</div>", unsafe_allow_html=True)
        sys_cols = st.columns(6)
        system_items = [
            ("BASE", f"{DB.stat().st_size / 1024**2:.2f} MB"),
            ("BUDGET", f"{history_budget_gb:.1f} GB"),
            ("PRÉDICTIONS", counts.get("model_predictions", 0)),
            ("FANTÔMES", counts.get("shadow_predictions", 0)),
            ("SIGNAUX", counts.get("signals", 0)),
            ("REVUES IA", counts.get("ai_reviews", 0)),
        ]
        for col, (label, val) in zip(sys_cols, system_items):
            with col: card(str(label), str(val))
        st.subheader("Runs")
        st.dataframe(ensure_unique_columns(runs), use_container_width=True, hide_index=True)
        st.subheader("Incidents")
        st.dataframe(ensure_unique_columns(incidents), use_container_width=True, hide_index=True)
        with st.expander("Message technique du dernier cycle"):
            st.json(message)


render_live_dashboard()
