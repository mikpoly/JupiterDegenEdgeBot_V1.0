from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .instance_lock import old_android_bot_running, pc_bot_processes
from .live_gate import LiveValidationGate
from .micro_live import MicroLiveGate
from .positions import extract_position_rows, parse_position
from .wallet import WalletSendError

log = logging.getLogger(__name__)


class LiveFundingPause(RuntimeError):
    """New LIVE buys pause while analysis and reconciliation continue."""


class AmbiguousSendError(RuntimeError):
    """Compatibility wrapper for callers that still classify ambiguous sends."""

    def __init__(self, message: str, *, signature: str = "", data: dict | None = None):
        super().__init__(message)
        self.signature = signature
        self.data = data or {}


@dataclass(slots=True)
class ExecutionResult:
    status: str
    response: dict
    order_pubkey: str = ""
    position_pubkey: str = ""
    signature: str = ""
    deposit_mint: str = ""


def _first(mapping: dict, *keys, default=""):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return default


def _micro_usd(value, default: float = -1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number / 1_000_000 if abs(number) > 1.5 else number


def _depth_size(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    # Jupiter orderbook quantities can be returned in base units. Treat very
    # large values as micro-contracts to avoid ever overstating liquidity.
    return number / 1_000_000 if number > 100_000 else number


def _orderbook_depth(orderbook: dict, is_yes: bool) -> dict:
    key = "yes_dollars" if is_yes else "no_dollars"
    rows = orderbook.get(key) if isinstance(orderbook, dict) else None
    rows = rows if isinstance(rows, list) else []
    total = 0.0
    best_price = None
    valid_levels = 0
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            price = float(row[0])
        except (TypeError, ValueError):
            continue
        size = _depth_size(row[1])
        if not (0 < price < 1) or size <= 0:
            continue
        if best_price is None:
            best_price = price
        total += size
        valid_levels += 1
    return {"levels": valid_levels, "contracts": total, "best_price": best_price}


def _order_part(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    for key in ("order", "data", "result"):
        value = payload.get(key)
        if isinstance(value, dict) and (
            value.get("transaction") or value.get("orderPubkey") or value.get("order")
        ):
            if isinstance(value.get("order"), dict):
                return value["order"]
            return value
    return payload


class Executor:
    def __init__(self, settings, db, jupiter, wallet):
        self.s = settings
        self.db = db
        self.jupiter = jupiter
        self.wallet = wallet

    def _wallet_balances(self, *, force: bool = False):
        try:
            return self.wallet.balances(force=force)
        except TypeError:
            # Small test doubles and legacy adapters may not expose ``force``.
            return self.wallet.balances()

    def _require_live(self, *, ignore_order_id: int | None = None) -> None:
        if not bool(getattr(self.s, "live_release_enabled", False)):
            raise RuntimeError("LIVE désactivé: LIVE_RELEASE_ENABLED=false; utilise PAPER")
        if not bool(getattr(self.s, "release_live_capable", True)):
            raise RuntimeError("LIVE interdit par le code de la v1.0.0 MICRO-LIVE/Validation")
        if hasattr(self.s, "live_allowed_by_version") and not bool(self.s.live_allowed_by_version):
            raise RuntimeError("LIVE interdit dans la v1.0.0 MICRO-LIVE/Validation")
        if bool(getattr(self.s, "micro_live_enabled", False)):
            MicroLiveGate(self.s, self.db).require(ignore_order_id=ignore_order_id)
        elif bool(getattr(self.s, "live_validation_gate_enabled", False)):
            LiveValidationGate(self.s, self.db).require()
        if self.s.trading_mode != "live":
            raise RuntimeError("TRADING_MODE=live requis")
        if not self.s.auto_execute:
            raise RuntimeError("AUTO_EXECUTE=true requis")
        if self.s.live_confirmation != "I_ACCEPT_REAL_MONEY_RISK":
            raise RuntimeError("LIVE_CONFIRMATION invalide")
        if bool(getattr(self.s, "refuse_live_while_old_bot_running", True)):
            legacy = old_android_bot_running()
            if legacy:
                pids = ", ".join(str(row["pid"]) for row in legacy)
                raise RuntimeError(
                    "ancien bot Jupiter LIVE encore actif; arrête-le avant "
                    f"toute transaction du bot PC (PID {pids})"
                )
            duplicates = pc_bot_processes()
            if duplicates:
                detail = ", ".join(f"PID {row['pid']}" for row in duplicates)
                raise RuntimeError(
                    "autre version de JupiterDegenEdgeBot active; "
                    f"nouveaux ordres LIVE bloqués ({detail})"
                )

    def live_readiness(self, stake_usd: float | None = None) -> dict:
        """Read-only LIVE gate. Never creates or signs a transaction."""
        self._require_live()
        stake = float(stake_usd if stake_usd is not None else self.s.min_order_usd)
        balances = self._wallet_balances(force=True)
        if hasattr(self.wallet, "set_cycle_snapshot"):
            self.wallet.set_cycle_snapshot(balances)
        reasons: list[str] = []
        if balances.sol < float(self.s.min_sol_balance):
            reasons.append(
                f"SOL insuffisant pour les frais: {balances.sol:.6f} < {float(self.s.min_sol_balance):.6f}"
            )
        if hasattr(self.wallet, "funding_report"):
            funding = self.wallet.funding_report(stake, balances)
            candidates = list(funding.get("deposit_mints") or [])
        else:
            candidates = self.wallet.deposit_candidates(stake, balances)
            funding = {
                "reserved_usd": float(getattr(self.wallet, "reserved_usd", 0.0)),
                "available_usdc": float(balances.usdc),
                "available_jupusd": float(balances.jupusd),
                "deposit_mints": candidates,
            }
        if not candidates:
            reasons.append(
                f"USDC/JupUSD insuffisant pour une mise minimale de {stake:.2f}$ "
                f"(USDC={balances.usdc:.2f}, JupUSD={balances.jupusd:.2f})"
            )
        affordable = int(max(
            float(funding.get("available_usdc") or 0.0),
            float(funding.get("available_jupusd") or 0.0),
        ) // stake) if stake > 0 else 0
        return {
            "ready": not reasons,
            "owner": balances.owner,
            "sol": balances.sol,
            "usdc": balances.usdc,
            "jupusd": balances.jupusd,
            "stake_usd": stake,
            "deposit_mints": candidates,
            "reserved_usd": float(funding.get("reserved_usd") or 0.0),
            "available_usdc": float(funding.get("available_usdc") or 0.0),
            "available_jupusd": float(funding.get("available_jupusd") or 0.0),
            "affordable_orders": affordable,
            "reasons": reasons,
        }

    def funding_readiness(self, stake_usd: float) -> dict:
        """Check one more order against the frozen cycle balance."""
        stake = max(0.0, float(stake_usd))
        balances = getattr(self.wallet, "cycle_snapshot", None)
        if balances is None:
            balances = self._wallet_balances(force=False)
        if hasattr(self.wallet, "funding_report"):
            report = self.wallet.funding_report(stake, balances)
            candidates = list(report.get("deposit_mints") or [])
        else:
            candidates = self.wallet.deposit_candidates(stake, balances)
            report = {
                "owner": balances.owner,
                "sol": balances.sol,
                "usdc": balances.usdc,
                "jupusd": balances.jupusd,
                "reserved_usd": float(getattr(self.wallet, "reserved_usd", 0.0)),
                "available_usdc": balances.usdc,
                "available_jupusd": balances.jupusd,
                "deposit_mints": candidates,
            }
        report = dict(report)
        report["ready"] = bool(candidates) and float(report.get("sol") or 0.0) >= float(self.s.min_sol_balance)
        report["reasons"] = []
        if float(report.get("sol") or 0.0) < float(self.s.min_sol_balance):
            report["reasons"].append(
                f"SOL insuffisant: {float(report.get('sol') or 0.0):.6f} < {float(self.s.min_sol_balance):.6f}"
            )
        if not candidates:
            report["reasons"].append(
                f"fonds stables insuffisants: mise {stake:.2f}$, réservé {float(report.get('reserved_usd') or 0.0):.2f}$, "
                f"USDC disponible {float(report.get('available_usdc') or 0.0):.2f}$, "
                f"JupUSD disponible {float(report.get('available_jupusd') or 0.0):.2f}$"
            )
        return report

    def preflight_market(self, signal, market) -> dict:
        """Re-read executable prices and orderbook immediately before signing."""
        if self.s.trading_mode != "live":
            return {"ready": True, "paper": True}
        if hasattr(self.jupiter, "trade_quote"):
            quote = self.jupiter.trade_quote(market.id, signal.outcome == "YES")
            buy = float(quote.get("buy") or 0.0)
            sell = float(quote.get("sell") or 0.0)
            spread = quote.get("spread")
            volume = float(quote.get("volume_usd") or 0.0)
            liquidity = float(quote.get("liquidity_usd") or 0.0)
            quote_source = "jupiter_market_quote"
        else:
            buy = float(getattr(market, "yes_price", 0.0) if signal.outcome == "YES"
                        else getattr(market, "no_price", 0.0))
            if buy <= 0:
                buy = float(signal.price)
            sell = buy
            spread = 0.0
            volume = float(getattr(market, "volume_usd", 0.0) or 0.0)
            liquidity = float(getattr(market, "liquidity_usd", 0.0) or 0.0)
            quote_source = "legacy_adapter"

        reasons: list[str] = []
        drift = abs(buy - float(signal.price))
        max_drift = float(self.s.live_max_price_drift)
        if not (0 < buy < 1):
            reasons.append("prix d'achat LIVE invalide")
        if drift > max_drift:
            reasons.append(f"dérive de prix {drift:.3f} > {max_drift:.3f}")
        if bool(getattr(self.s, "require_exit_price_for_new_buy", True)) and sell <= 0:
            reasons.append("prix de sortie absent; nouvel achat refusé")
        max_spread = float(getattr(self.s, "max_entry_exit_spread", 0.15))
        spread_ratio = (float(spread) / buy) if spread is not None and buy > 0 else None
        if spread is not None and float(spread) > max_spread:
            reasons.append(f"spread entrée/sortie {float(spread):.3f} > {max_spread:.3f}")
        max_ratio = float(getattr(self.s, "live_max_entry_exit_spread_ratio", 0.15))
        if spread_ratio is not None and spread_ratio > max_ratio:
            reasons.append(f"spread relatif {spread_ratio:.1%} > {max_ratio:.1%}")

        depth = {"levels": 0, "contracts": 0.0, "best_price": None}
        depth_error = ""
        if hasattr(self.jupiter, "orderbook"):
            try:
                depth = _orderbook_depth(
                    self.jupiter.orderbook(market.id), signal.outcome == "YES"
                )
            except Exception as exc:
                depth_error = str(exc)
        required_contracts = (float(signal.stake_usd) / buy) if buy > 0 else float("inf")
        depth_required = required_contracts * float(
            getattr(self.s, "live_min_orderbook_depth_multiplier", 1.5)
        )
        depth_ok = depth["contracts"] + 1e-9 >= depth_required
        min_volume = float(getattr(self.s, "live_min_market_volume_usd", 0.0))
        min_liquidity = float(getattr(self.s, "live_min_market_liquidity_usd", 0.0))
        metadata_ok = (volume >= min_volume if min_volume > 0 else volume > 0) or (
            liquidity >= min_liquidity if min_liquidity > 0 else liquidity > 0
        )
        if hasattr(self.jupiter, "orderbook") and not depth_ok:
            reasons.append(
                f"profondeur carnet {depth['contracts']:.4f} contrats < {depth_required:.4f}"
            )
        if not metadata_ok and not depth_ok:
            detail = f"; orderbook={depth_error}" if depth_error else ""
            reasons.append("liquidité/volume Jupiter non vérifiables" + detail)
        return {
            "ready": not reasons,
            "buy": buy,
            "sell": sell,
            "spread": spread,
            "spread_ratio": spread_ratio,
            "volume_usd": volume,
            "liquidity_usd": liquidity,
            "orderbook_depth": depth,
            "required_contracts": required_contracts,
            "depth_required": depth_required,
            "quote_source": quote_source,
            "reasons": reasons,
        }

    def execute(self, signal, market, *, order_id: int | None = None) -> ExecutionResult:
        if self.s.trading_mode != "live":
            return ExecutionResult(
                status="paper_filled",
                response={"paper": True, "price": signal.price, "stake": signal.stake_usd},
            )
        self._require_live(ignore_order_id=order_id)
        if bool(getattr(self.s, "micro_live_enabled", False)):
            MicroLiveGate(self.s, self.db).require_signal(signal, ignore_order_id=order_id)
        stake = float(signal.stake_usd)
        if stake > float(self.s.max_live_stake_usd) + 1e-9:
            raise RuntimeError(f"mise LIVE {stake:.2f}$ > plafond {float(self.s.max_live_stake_usd):.2f}$")
        preflight = self.preflight_market(signal, market)
        if not preflight.get("ready"):
            raise RuntimeError(" | ".join(preflight.get("reasons") or ["pré-contrôle marché refusé"]))
        current_price = float(preflight.get("buy") or 0.0)
        status = self.jupiter.status()
        if status.get("trading_active") is not True:
            raise RuntimeError("trading Jupiter inactif")
        funding = self.funding_readiness(stake)
        balances = getattr(self.wallet, "cycle_snapshot", None) or self._wallet_balances(force=False)
        owner = balances.owner
        if not funding.get("ready"):
            raise LiveFundingPause(
                " | ".join(funding.get("reasons") or ["wallet LIVE non finançable"])
            )
        candidates = list(funding.get("deposit_mints") or [])

        create_errors = []
        payload = None
        selected_mint = ""
        for mint in candidates:
            try:
                payload = self.jupiter.create_order(
                    owner, market.id, signal.outcome == "YES", stake, mint
                )
                selected_mint = mint
                break
            except Exception as exc:
                create_errors.append(f"{mint[:8]}…: {exc}")
        if payload is None:
            raise RuntimeError(" | ".join(create_errors) or "création d'ordre impossible")

        # Jupiter Forecast may return atomic-swap orders that require a
        # second POST /execute step after signing. This release supports
        # only the keeper-filled prediction-order flow used by the current
        # provider. Refuse every other execution model rather than sending
        # a transaction through the wrong settlement path.
        execution_model = str(payload.get("executionModel") or "").strip().casefold()
        if execution_model:
            raise RuntimeError(
                f"modèle d'exécution Jupiter non pris en charge en MICRO-LIVE: {execution_model}"
            )

        order = payload.get("order") if isinstance(payload.get("order"), dict) else _order_part(payload)
        returned_owner = str(_first(order, "userPubkey", "ownerPubkey", "owner", default=""))
        returned_market = str(_first(order, "marketId", "market", default=""))
        returned_is_buy = _first(order, "isBuy", default=None)
        returned_is_yes = _first(order, "isYes", default=None)
        if not returned_owner or returned_owner != owner:
            raise RuntimeError(
                f"propriétaire de transaction Jupiter inattendu: {returned_owner or 'absent'} != {owner}"
            )
        if not returned_market or returned_market != str(market.id):
            raise RuntimeError(
                f"marché de transaction Jupiter inattendu: {returned_market or 'absent'} != {market.id}"
            )
        if returned_is_buy is not True:
            raise RuntimeError("réponse Jupiter ne confirme pas isBuy=true")
        if returned_is_yes is not (signal.outcome == "YES"):
            raise RuntimeError("côté YES/NO de la transaction Jupiter inattendu")

        order_cost = _micro_usd(_first(order, "orderCostUsd", default=None))
        if order_cost < 0 or abs(order_cost - stake) > float(
            getattr(self.s, "live_max_order_cost_drift_usd", 0.35)
        ):
            raise RuntimeError(
                f"coût ordre Jupiter inattendu: {order_cost:.4f}$ pour mise {stake:.2f}$"
            )
        max_buy_price = _micro_usd(_first(order, "maxBuyPriceUsd", default=None))
        if max_buy_price <= 0 or max_buy_price > current_price + float(self.s.live_max_price_drift) + 1e-9:
            raise RuntimeError(
                f"prix maximal construit par Jupiter invalide: {max_buy_price:.4f}"
            )
        fees = _micro_usd(_first(order, "estimatedTotalFeeUsd", default=None))
        max_fee_ratio = float(getattr(self.s, "live_max_estimated_fee_ratio", 0.20))
        if fees < 0 or fees > stake * max_fee_ratio + 1e-9:
            raise RuntimeError(
                f"frais estimés Jupiter {fees:.4f}$ > plafond {stake * max_fee_ratio:.4f}$"
            )

        transaction = str(
            _first(payload, "transaction", "serializedTransaction", default="")
            or _first(order, "transaction", "serializedTransaction", default="")
        )
        if not transaction:
            raise RuntimeError("transaction Jupiter absente")
        tx_meta = payload.get("txMeta") or order.get("txMeta") or {}
        if not isinstance(tx_meta, dict) or not tx_meta.get("blockhash") or not tx_meta.get("lastValidBlockHeight"):
            raise RuntimeError("métadonnées de transaction Jupiter incomplètes")
        required_signers = payload.get("requiredSigners")
        if isinstance(required_signers, list) and required_signers and owner not in {str(x) for x in required_signers}:
            raise RuntimeError("wallet propriétaire absent des signataires Jupiter requis")
        order_pubkey = str(_first(order, "orderPubkey", "pubkey", default=""))
        position_pubkey = str(_first(order, "positionPubkey", default=""))
        if not order_pubkey or not position_pubkey:
            raise RuntimeError("identifiants orderPubkey/positionPubkey absents")
        if order_id is not None:
            self.db.update_order(
                order_id,
                "unsigned_ready",
                {"create": payload, "stage": "unsigned_transaction_created"},
                order_pubkey=order_pubkey,
                position_pubkey=position_pubkey,
                deposit_mint=selected_mint,
            )

        self.wallet.reserve(stake)
        try:
            sent = self.wallet.sign_and_send(transaction, tx_meta)
        except WalletSendError as exc:
            response = {
                "create": payload,
                "send_error": exc.data or {"error": str(exc)},
                "market_id": market.id,
            }
            if exc.ambiguous:
                return ExecutionResult(
                    status="unknown_send",
                    response=response,
                    order_pubkey=order_pubkey,
                    position_pubkey=position_pubkey,
                    signature=exc.signature,
                    deposit_mint=selected_mint,
                )
            self.wallet.release(stake)
            raise RuntimeError(str(exc)) from exc

        signature = str(sent.get("signature") or "")
        response = {"create": payload, "send": sent, "market_id": market.id}
        fill_status = self._poll_fill(order_pubkey, position_pubkey, owner, response)
        if fill_status in {"failed", "simulation_error"}:
            self.wallet.release(stake)
        return ExecutionResult(
            status=fill_status,
            response=response,
            order_pubkey=order_pubkey,
            position_pubkey=position_pubkey,
            signature=signature,
            deposit_mint=selected_mint,
        )

    def _poll_fill(self, order_pubkey: str, position_pubkey: str, owner: str, response: dict) -> str:
        if not order_pubkey:
            return "sent"
        for attempt in range(max(1, int(self.s.fill_poll_attempts))):
            if attempt:
                time.sleep(float(self.s.fill_poll_seconds))
            try:
                last = self.jupiter.order_status(order_pubkey)
            except Exception as exc:
                text = str(exc)
                if "order_history_not_found" in text or "No order history found" in text:
                    continue
                log.warning("Statut ordre %s indisponible: %s", order_pubkey, exc)
                continue
            response["last_status"] = last
            raw = _order_part(last)
            status = str(_first(raw, "status", "orderStatus", default="")).casefold().replace("-", "_")
            filled = _first(raw, "filled", "isFilled", default=None)
            if filled is True or status in {"filled", "confirmed", "completed", "settled"}:
                return "filled"
            if status in {"partial", "partially_filled", "partial_fill", "partiallyfilled"}:
                return "partial_fill"
            if status in {"failed", "cancelled", "canceled", "rejected", "expired"}:
                return "failed"
        # Never infer a fill from any pre-existing position on the same market.
        # Only the exact positionPubkey returned for this order is accepted.
        if position_pubkey:
            try:
                rows = extract_position_rows(self.jupiter.positions(owner))
                for raw in rows:
                    parsed = parse_position(raw)
                    if parsed.get("position_key") == position_pubkey:
                        response["exact_position"] = raw
                        return "filled"
            except Exception as exc:
                response["position_fallback_error"] = str(exc)
        return "pending_fill"
