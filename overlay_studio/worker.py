from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

from .project import load_project
from .progress import ProgressEstimator, RENDER_TASKS
from .render import RenderCancelled, render_full_resumable
from .render_lock import release as release_render_lock
from .render_lock import update as update_render_lock


def _write_status(path: Path, *, strict: bool = True, **values: object) -> bool:
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **values,
    }
    temporary = path.with_name(
        f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}"
    )
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(40):
            try:
                os.replace(temporary, path)
                return True
            except OSError as exc:
                retryable = isinstance(exc, PermissionError) or getattr(
                    exc, "winerror", None
                ) in {5, 32}
                if not retryable or attempt == 39:
                    if strict:
                        raise
                    return False
                time.sleep(min(0.025 * (attempt + 1), 0.2))
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def run(
    project_path: Path, output: Path, status: Path, stop: Path, lock_token: str = ""
) -> int:
    app_root = Path(__file__).resolve().parents[1]
    video, _, entries, settings = load_project(project_path)
    estimator = ProgressEstimator(RENDER_TASKS)
    if lock_token and not update_render_lock(
        project_path.parent, lock_token, pid=os.getpid()
    ):
        _write_status(
            status, state="error", progress=0.0,
            label="Render ownership was lost before the worker started.",
            output=str(output),
        )
        return 1
    _write_status(
        status,
        state="running",
        pid=os.getpid(),
        output=str(output),
        **estimator.snapshot(0.0, "Starting background render"),
    )

    chunk_number = 0
    chunk_count = 0
    chunk_started_at = None

    def progress(value: float, label: str) -> None:
        nonlocal chunk_number, chunk_count, chunk_started_at
        match = re.search(r"Chunk (\d+)/(\d+)", label)
        if match:
            number, count = map(int, match.groups())
            if number != chunk_number:
                chunk_number, chunk_count, chunk_started_at = number, count, time.time()
        chunk_details = {}
        if chunk_number and chunk_started_at is not None:
            chunk_details = {
                "chunk_number": chunk_number,
                "chunk_count": chunk_count,
                "chunk_started_at": chunk_started_at,
                "chunk_elapsed_seconds": max(0.0, time.time() - chunk_started_at),
            }
        _write_status(
            status, strict=False, state="running", pid=os.getpid(),
            output=str(output), **estimator.snapshot(value, label), **chunk_details,
        )

    try:
        result = render_full_resumable(
            video,
            entries,
            settings,
            output_path=output,
            project_dir=project_path.parent,
            app_root=app_root,
            progress=progress,
            cancel_check=stop.exists,
        )
        _write_status(
            status,
            state="complete",
            output=str(result),
            **estimator.snapshot(1.0, "Final video verified"),
        )
        return 0
    except RenderCancelled as exc:
        _write_status(
            status,
            state="stopped",
            progress=0.0,
            label=str(exc),
            output=str(output),
        )
        return 2
    except Exception as exc:
        _write_status(
            status,
            state="error",
            progress=0.0,
            label=str(exc),
            output=str(output),
        )
        return 1
    finally:
        if lock_token:
            release_render_lock(project_path.parent, lock_token)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    parser.add_argument("--lock-token", default="")
    args = parser.parse_args()
    return run(
        args.project.resolve(),
        args.output.resolve(),
        args.status.resolve(),
        args.stop.resolve(),
        args.lock_token,
    )


if __name__ == "__main__":
    raise SystemExit(main())
