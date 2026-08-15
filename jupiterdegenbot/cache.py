from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable


class JsonCache:
    def __init__(self, root: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, key: str, max_age_seconds: float) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(temp, path)

    def remember(self, key: str, max_age_seconds: float, loader: Callable[[], Any]) -> Any:
        cached = self.get(key, max_age_seconds)
        if cached is not None:
            return cached
        value = loader()
        self.set(key, value)
        return value

    def cleanup(self, max_files: int = 2000, max_age_days: int = 14) -> None:
        files = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        cutoff = time.time() - max_age_days * 86400
        for index, path in enumerate(files):
            if index >= max_files or path.stat().st_mtime < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass
