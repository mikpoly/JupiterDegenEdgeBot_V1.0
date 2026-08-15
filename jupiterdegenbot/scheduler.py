from __future__ import annotations

import logging
import math
import threading
import time

from .engine import BotEngine
from .instance_lock import bot_instance_lock
from .auto_training import AutoNeuralTrainer

log = logging.getLogger(__name__)


def _run_timed_fast_worker(settings, db, stop_event: threading.Event) -> None:
    """Low-latency 5m/15m worker independent from the long normal cycle."""
    engine = BotEngine(settings, db)
    engine.enable_timed_fast_worker_mode()
    # 8 seconds with the shipped configuration; bounded so an unusual .env
    # cannot turn this into a tight API loop or a one-minute blind spot.
    configured = float(getattr(settings, "timed_direction_scan_offset_seconds", 8) or 8)
    poll_seconds = max(5.0, min(12.0, configured))
    log.info("TIMED FAST WORKER V2.3.1 démarré · interrogation toutes les %.0f s", poll_seconds)
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            summary = engine.scan_timed_fast_once()
            if int(summary.get("processed", 0) or 0) > 0:
                log.info(
                    "TIMED FAST · actifs=%s signaux=%s ordres=%s outcome=%s",
                    summary.get("processed"), summary.get("signals"),
                    summary.get("orders"), summary.get("cycle_outcome"),
                )
        except Exception:
            # A fast-path failure must never kill the normal bot. The next poll
            # retries, while every real-money order still uses the common lock.
            log.exception("TIMED FAST WORKER en erreur; nouvel essai au prochain poll")
        elapsed = time.monotonic() - started
        stop_event.wait(max(1.0, poll_seconds - elapsed))
    log.info("TIMED FAST WORKER arrêté")


def run_forever(settings, db) -> None:
    """Run the normal Degen cycle plus an independent short-TIMED worker."""
    with bot_instance_lock():
        engine = BotEngine(settings, db)
        trainer = AutoNeuralTrainer(settings, db)
        engine.recover()

        fast_stop = threading.Event()
        fast_thread = None
        if bool(getattr(settings, "timed_direction_model_enabled", True)):
            fast_thread = threading.Thread(
                target=_run_timed_fast_worker,
                args=(settings, db, fast_stop),
                name="jupiter-timed-fast",
                daemon=True,
            )
            fast_thread.start()

        cycle_number = 0
        try:
            while True:
                cycle_number += 1
                started = time.monotonic()
                try:
                    summary = engine.scan_once()
                    log.info("CYCLE DEGEN #%d TERMINÉ: %s", cycle_number, summary.get("cycle_outcome"))
                    training = trainer.maybe_run()
                    if training.get("ran"):
                        log.info("Apprentissage neuronal automatique: %s", training.get("status"))
                except KeyboardInterrupt:
                    log.info("Arrêt demandé par l'utilisateur")
                    raise
                except Exception:
                    log.exception("Cycle Degen en erreur; le prochain cycle pourra reprendre")
                elapsed = time.monotonic() - started
                configured = float(settings.scan_interval_seconds or 0.0)
                interval = configured if configured > 0 else float(settings.scan_interval_minutes) * 60.0
                wait_seconds = max(1.0, interval - elapsed)

                # The normal scanner keeps its original scheduling behavior. The
                # short contracts no longer depend on it; the worker above stays
                # active even while this cycle is doing maintenance or 300-market
                # quantitative research.
                if bool(getattr(settings, "timed_direction_model_enabled", True)) and bool(
                    getattr(settings, "timed_direction_align_scan_to_window", True)
                ):
                    window = max(60.0, float(getattr(settings, "timed_direction_scan_window_seconds", 300)))
                    if abs(interval - window) <= 5.0:
                        offset = float(getattr(settings, "timed_direction_scan_offset_seconds", 8))
                        offset = max(0.0, min(window - 1.0, offset))
                        wall_now = time.time()
                        next_boundary = (math.floor(wall_now / window) + 1.0) * window + offset
                        wait_seconds = max(1.0, next_boundary - wall_now)

                next_at = time.strftime("%H:%M:%S", time.localtime(time.time() + wait_seconds))
                log.info("Cycle écoulé %.1f s · prochain démarrage dans %.0f s vers %s", elapsed, wait_seconds, next_at)
                if fast_stop.wait(wait_seconds):
                    break
        finally:
            fast_stop.set()
            if fast_thread is not None and fast_thread.is_alive():
                fast_thread.join(timeout=15.0)
