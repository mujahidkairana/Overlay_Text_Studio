from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from .project import load_project
from .render import RenderCancelled, render_full_resumable


def _write_status(path: Path, **values: object) -> None:
    payload = {
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        **values,
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def run(project_path: Path, output: Path, status: Path, stop: Path) -> int:
    app_root = Path(__file__).resolve().parents[1]
    video, _, entries, settings = load_project(project_path)
    _write_status(
        status,
        state="running",
        progress=0.0,
        label="Starting background render",
        pid=os.getpid(),
        output=str(output),
    )

    def progress(value: float, label: str) -> None:
        _write_status(
            status,
            state="running",
            progress=max(0.0, min(1.0, float(value))),
            label=label,
            pid=os.getpid(),
            output=str(output),
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
            progress=1.0,
            label="Final video verified",
            output=str(result),
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--stop", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.project.resolve(),
        args.output.resolve(),
        args.status.resolve(),
        args.stop.resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
