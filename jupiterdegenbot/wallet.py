from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = PROJECT_ROOT / "tools"


def _rooted(path_value: str) -> str:
    path = Path(str(path_value or "").strip()).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


@dataclass(slots=True)
class WalletBalances:
    owner: str
    sol: float
    usdc: float
    jupusd: float
    rpc_url: str = ""


class WalletSendError(RuntimeError):
    """A signed transaction failed or has an uncertain network outcome."""

    def __init__(self, message: str, *, signature: str = "", ambiguous: bool = False, data: dict | None = None):
        super().__init__(message)
        self.signature = signature
        self.ambiguous = bool(ambiguous)
        self.data = data or {}


class Wallet:
    def __init__(self, settings):
        self.s = settings
        self._cached: WalletBalances | None = None
        self._cache_at = 0.0
        self._reserved_usd = 0.0
        self._cycle_snapshot: WalletBalances | None = None
        # Instance-local read timeout used only by the dedicated TIMED FAST worker.
        # The normal bot keeps the historical 30 s RPC timeout. sign_and_send.mjs
        # is deliberately unaffected because a real signed send must keep its
        # existing confirmation/error semantics.
        self._fast_read_timeout_seconds: float | None = None
        self._fast_read_max_urls: int | None = None

    def start_cycle(self) -> None:
        self._reserved_usd = 0.0
        self._cycle_snapshot = None
        self._cached = None
        self._cache_at = 0.0

    def set_cycle_snapshot(self, balances: WalletBalances) -> None:
        """Freeze one authoritative balance snapshot for the order loop.

        The on-chain balance can decrease after a submitted order while the
        same order is still held in the local reservation counter. Re-reading
        the chain and subtracting that reservation again would double-count
        spent funds. A cycle snapshot plus local reservations avoids that bug.
        """
        self._cycle_snapshot = balances
        self._cached = balances
        self._cache_at = time.monotonic()

    @property
    def cycle_snapshot(self) -> WalletBalances | None:
        return self._cycle_snapshot

    @property
    def reserved_usd(self) -> float:
        return max(0.0, float(self._reserved_usd))

    def reserve(self, amount_usd: float) -> None:
        self._reserved_usd += max(0.0, float(amount_usd))

    def release(self, amount_usd: float) -> None:
        self._reserved_usd = max(0.0, self._reserved_usd - max(0.0, float(amount_usd)))

    def enable_fast_read_mode(self, timeout_seconds: float = 2.5, max_urls: int = 2) -> None:
        """Bound read-only Solana RPC latency for the short-TIMED worker.

        This is instance-local. The normal engine is untouched, and transaction
        signing/sending does not use this requests-based helper.
        """
        self._fast_read_timeout_seconds = max(1.0, min(10.0, float(timeout_seconds)))
        self._fast_read_max_urls = max(1, min(4, int(max_urls)))

    def _rpc_urls(self) -> list[str]:
        values = [str(self.s.solana_rpc_url or "").strip()]
        raw = str(getattr(self.s, "solana_rpc_fallback_urls_raw", "") or "")
        values.extend(part.strip() for part in raw.split(","))
        return list(dict.fromkeys(value for value in values if value))

    @staticmethod
    def _decode_json(stdout: str, stderr: str) -> dict:
        decoded: list[dict] = []
        for stream in (stdout, stderr):
            for line in reversed(str(stream or "").splitlines()):
                candidate = line.strip()
                if not candidate:
                    continue
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    if value.get("ok") is True:
                        return value
                    decoded.append(value)
        if decoded:
            return decoded[0]
        text = (str(stdout or "") + "\n" + str(stderr or "")).strip()
        return {"ok": False, "error": text or "sortie Node illisible"}

    def owner(self) -> str:
        wallet_path = _rooted(self.s.solana_keypair_path)
        env = {**os.environ, "SOLANA_KEYPAIR_PATH": wallet_path}
        proc = subprocess.run(
            ["node", str(TOOLS_DIR / "wallet.mjs"), "pubkey"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(PROJECT_ROOT),
            timeout=30,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "wallet pubkey impossible")
        owner = proc.stdout.strip()
        if not owner:
            raise RuntimeError("adresse wallet vide")
        return owner

    def _rpc(self, method: str, params: list):
        errors: list[str] = []
        urls = self._rpc_urls()
        if self._fast_read_max_urls is not None:
            urls = urls[: self._fast_read_max_urls]
        timeout = (
            float(self._fast_read_timeout_seconds)
            if self._fast_read_timeout_seconds is not None
            else 30.0
        )
        for url in urls:
            try:
                response = requests.post(
                    url,
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("error"):
                    raise RuntimeError(str(payload["error"]))
                return payload.get("result"), url
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        raise RuntimeError("RPC Solana indisponible: " + " | ".join(errors)[:800])

    def token_balance(self, owner: str, mint: str) -> tuple[float, str]:
        result, rpc_url = self._rpc(
            "getTokenAccountsByOwner",
            [owner, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
        result = result or {}
        total = 0.0
        for item in result.get("value") or []:
            amount = (
                item.get("account", {}).get("data", {}).get("parsed", {})
                .get("info", {}).get("tokenAmount", {})
            )
            raw = amount.get("uiAmountString")
            if raw in (None, ""):
                raw = amount.get("uiAmount")
            try:
                total += float(raw or 0)
            except (TypeError, ValueError):
                pass
        return total, rpc_url

    def balances(self, force: bool = False) -> WalletBalances:
        age = time.monotonic() - self._cache_at
        cache_seconds = max(1.0, float(getattr(self.s, "wallet_balance_cache_seconds", 20.0)))
        if not force and self._cached is not None and age <= cache_seconds:
            return self._cached
        owner = self.owner()
        native, rpc_url = self._rpc("getBalance", [owner, {"commitment": "confirmed"}])
        usdc, _ = self.token_balance(owner, self.s.usdc_mint)
        jupusd, _ = self.token_balance(owner, self.s.jupusd_mint)
        balances = WalletBalances(
            owner=owner,
            sol=float((native or {}).get("value") or 0) / 1_000_000_000,
            usdc=usdc,
            jupusd=jupusd,
            rpc_url=rpc_url,
        )
        self._cached = balances
        self._cache_at = time.monotonic()
        return balances

    def funding_report(self, stake_usd: float, balances: WalletBalances | None = None) -> dict:
        # Prefer the cycle-start snapshot. It is paired with local reservations
        # and therefore never double-counts orders already reflected on-chain.
        balances = self._cycle_snapshot or balances or self.balances()
        stake = max(0.0, float(stake_usd))
        reserved = self.reserved_usd
        available_usdc = max(0.0, float(balances.usdc) - reserved)
        available_jupusd = max(0.0, float(balances.jupusd) - reserved)
        mode = str(self.s.deposit_mint_mode or "auto").strip().lower()
        candidates: list[str] = []
        if mode == "usdc":
            if available_usdc + 1e-9 >= stake:
                candidates.append(self.s.usdc_mint)
        elif mode == "jupusd":
            if available_jupusd + 1e-9 >= stake:
                candidates.append(self.s.jupusd_mint)
        else:
            # Keep the historical preference for JupUSD, then USDC.
            if available_jupusd + 1e-9 >= stake:
                candidates.append(self.s.jupusd_mint)
            if available_usdc + 1e-9 >= stake:
                candidates.append(self.s.usdc_mint)
        return {
            "owner": balances.owner,
            "sol": float(balances.sol),
            "usdc": float(balances.usdc),
            "jupusd": float(balances.jupusd),
            "reserved_usd": reserved,
            "available_usdc": available_usdc,
            "available_jupusd": available_jupusd,
            "stake_usd": stake,
            "deposit_mint_mode": mode,
            "deposit_mints": candidates,
        }

    def deposit_candidates(self, stake_usd: float, balances: WalletBalances | None = None) -> list[str]:
        return list(self.funding_report(stake_usd, balances).get("deposit_mints") or [])

    def signature_status(self, signature: str) -> dict | None:
        if not signature:
            return None
        result, rpc_url = self._rpc(
            "getSignatureStatuses",
            [[signature], {"searchTransactionHistory": True}],
        )
        rows = (result or {}).get("value") or []
        if not rows or rows[0] is None:
            return None
        return {"rpc_url": rpc_url, **rows[0]}

    def sign_and_send(self, transaction: str, tx_meta: dict):
        payload = {
            "transaction": transaction,
            "txMeta": tx_meta,
            "walletPath": _rooted(self.s.solana_keypair_path),
            "rpcUrls": self._rpc_urls(),
            "commitment": str(getattr(self.s, "live_confirmation_commitment", "confirmed")),
            "simulate": bool(getattr(self.s, "live_simulate_before_send", True)),
        }
        proc = subprocess.run(
            ["node", str(TOOLS_DIR / "sign_and_send.mjs")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
            timeout=180,
            check=False,
        )
        data = self._decode_json(proc.stdout, proc.stderr)
        if data.get("ok") is True:
            return data
        raise WalletSendError(
            str(data.get("error") or proc.stderr or proc.stdout or "envoi Solana échoué"),
            signature=str(data.get("signature") or ""),
            ambiguous=bool(data.get("ambiguous")),
            data=data,
        )
