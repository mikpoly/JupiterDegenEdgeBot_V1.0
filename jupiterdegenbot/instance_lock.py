from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import psutil


class AlreadyRunning(RuntimeError):
    pass


def _command(proc) -> str:
    try:
        return " ".join(proc.cmdline())
    except (psutil.Error, OSError):
        return ""


def _current_launch_family_pids() -> set[int]:
    """Return this Python PID plus launcher/alias ancestor PIDs.

    Microsoft Store Python and some virtual-environment launchers briefly keep
    a proxy Python process as the parent of the interpreter that executes the
    bot.  That proxy has the same ``-m jupiterdegenbot run`` command line and must
    not be mistaken for a second bot instance.
    """

    pids = {os.getpid()}
    try:
        current = psutil.Process(os.getpid())
        for parent in current.parents():
            pids.add(int(parent.pid))
    except (psutil.Error, OSError):
        pass
    return pids


def pc_bot_processes(*, exclude_pid: int | None = None) -> list[dict]:
    """Find other Jupiter Degen bot processes, including older release folders.

    The active interpreter and all of its launcher ancestors are excluded so a
    Windows Python alias/proxy cannot trigger a false ``AlreadyRunning`` error.
    """

    excluded = _current_launch_family_pids()
    if exclude_pid is not None:
        excluded.add(int(exclude_pid))

    rows: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        pid = int(proc.info.get("pid") or 0)
        if not pid or pid in excluded:
            continue
        cmdline = _command(proc)
        lowered = cmdline.casefold()
        if "-m jupiterdegenbot run" in lowered:
            rows.append({"pid": pid, "command": cmdline})
    return rows


def _same_bot(pid: int) -> bool:
    try:
        proc = psutil.Process(pid)
        cmd = _command(proc).casefold()
        return "-m jupiterdegenbot run" in cmd
    except psutil.Error:
        return False


def _global_lock_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
    return base / "JupiterDegenEdgeBot" / "bot_instance.lock"


def _read_lock_pid(lock: Path) -> int:
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
        return int(payload.get("pid") or 0)
    except Exception:
        return 0


def _create_lock_atomically(lock: Path) -> None:
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
        }
    ).encode("utf-8")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(lock), flags, 0o600)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)


@contextmanager
def bot_instance_lock(path: str | None = None):
    """Allow one continuous bot or one one-shot scan across all releases."""

    others = pc_bot_processes()
    if others:
        detail = ", ".join(f"PID {row['pid']}" for row in others)
        raise AlreadyRunning(f"un autre JupiterDegenEdgeBot tourne déjà ({detail})")

    lock = Path(path) if path else _global_lock_path()
    lock.parent.mkdir(parents=True, exist_ok=True)
    own_family = _current_launch_family_pids()

    for _attempt in range(2):
        try:
            _create_lock_atomically(lock)
            break
        except FileExistsError:
            pid = _read_lock_pid(lock)
            if pid and pid not in own_family and _same_bot(pid):
                raise AlreadyRunning(f"un bot PC tourne déjà (PID {pid})")
            # Invalid/stale lock, or a lock left by this launcher's proxy.
            try:
                lock.unlink()
            except FileNotFoundError:
                pass
    else:
        raise AlreadyRunning("verrou global du bot déjà occupé")

    try:
        yield
    finally:
        try:
            if _read_lock_pid(lock) == os.getpid():
                lock.unlink(missing_ok=True)
        except Exception:
            pass


def old_android_bot_running() -> list[dict]:
    """Return legacy ``python -m jupiterbot run`` processes, if any."""
    rows = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        lowered = cmdline.casefold()
        if "-m jupiterbot run" in lowered and "jupiterdegenbot" not in lowered:
            rows.append({"pid": int(proc.info.get("pid") or 0), "command": cmdline})
    return rows
