from __future__ import annotations

from .crypto_data import CryptoDataClient
from .http import HttpClient
from .jupiter import JupiterClient
from .local_ai import LocalAIReviewer


def run_source_tests(settings, db=None) -> dict:
    checks: list[dict] = []
    http = HttpClient(settings)
    client = CryptoDataClient(settings, http, db)
    for asset in settings.crypto_assets:
        try:
            snapshot = client.fetch(asset)
            checks.append({
                "name": f"crypto_{asset}", "ok": True,
                "detail": f"{len(snapshot.sources)} sources; spot={snapshot.spot_median:.2f}; agreement={snapshot.source_agreement:.3f}",
            })
        except Exception as exc:
            checks.append({"name": f"crypto_{asset}", "ok": False, "detail": str(exc)})
    if settings.jupiter_api_key:
        try:
            status = JupiterClient(settings).status()
            checks.append({"name": "jupiter_status", "ok": True, "detail": str(status)[:500]})
        except Exception as exc:
            checks.append({"name": "jupiter_status", "ok": False, "detail": str(exc)})
    else:
        checks.append({"name": "jupiter_api_key", "ok": False, "detail": "JUPITER_API_KEY absente"})
    if bool(getattr(settings, "local_ai_enabled", True)):
        ai = LocalAIReviewer(settings).status()
        checks.append({
            "name": "ollama_local_ai",
            "ok": bool(ai.get("available") and ai.get("installed")),
            "detail": f"{ai.get('detail')} | modèle={ai.get('model')}",
        })
    return {"ok": all(row["ok"] for row in checks), "checks": checks}
