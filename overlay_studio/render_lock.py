from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

LOCK_NAME = "render_worker.lock"


def pid_running(pid: object) -> bool:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {value}", "/NH"], capture_output=True,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(value) in result.stdout
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False


def read_lock(project_dir: Path) -> dict[str, object] | None:
    try:
        return json.loads((project_dir / LOCK_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def acquire(project_dir: Path) -> tuple[str | None, dict[str, object] | None]:
    project_dir.mkdir(parents=True, exist_ok=True)
    path = project_dir / LOCK_NAME
    for _ in range(2):
        token = uuid.uuid4().hex
        payload = {"token": token, "pid": os.getpid(), "state": "launching", "created_at": time.time()}
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            return token, None
        except FileExistsError:
            owner = read_lock(project_dir)
            if owner and pid_running(owner.get("pid")):
                return None, owner
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return None, owner
    return None, read_lock(project_dir)


def update(project_dir: Path, token: str, *, pid: int, state: str = "running") -> bool:
    path = project_dir / LOCK_NAME
    owner = read_lock(project_dir)
    if not owner or owner.get("token") != token:
        return False
    owner.update(pid=int(pid), state=state, updated_at=time.time())
    temporary = path.with_suffix(f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(owner, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return True


def release(project_dir: Path, token: str) -> bool:
    path = project_dir / LOCK_NAME
    owner = read_lock(project_dir)
    if not owner or owner.get("token") != token:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return True
