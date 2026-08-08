from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from .media import MediaError, ffmpeg_path, file_fingerprint


SCENE_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def select_cut_frames(
    scored_frames: list[tuple[int, float]],
    *,
    threshold: float = 0.35,
    minimum_gap_frames: int = 12,
) -> list[int]:
    selected: list[int] = []
    for frame, score in sorted(scored_frames):
        if score < threshold:
            continue
        if selected and frame - selected[-1] < minimum_gap_frames:
            continue
        selected.append(frame)
    return selected


def detect_scene_cuts(
    video_path: str | Path,
    cache_path: str | Path,
    *,
    app_root: str | Path | None = None,
    fps: int = 30,
    threshold: float = 0.35,
    warning_callback: Callable[[str], None] | None = None,
) -> list[int]:
    source = Path(video_path).resolve()
    cache = Path(cache_path)
    expected = {
        "fingerprint": file_fingerprint(source),
        "fps": fps,
        "threshold": threshold,
    }
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            if payload.get("analysis") == expected:
                return [int(frame) for frame in payload.get("cut_frames", [])]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    command = [
        ffmpeg_path(app_root),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(source),
        "-an",
        "-vf",
        f"scale=320:-2:flags=area,select=gt(scene\\,{threshold}),showinfo",
        "-fps_mode",
        "vfr",
        "-f",
        "null",
        "NUL" if __import__("os").name == "nt" else "/dev/null",
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Scene-cut analysis failed."
        if warning_callback is None:
            raise MediaError(detail)
        warning_callback(
            "Scene-cut analysis was unavailable; strong scene-sensitive effects "
            "were disabled and safe overlay rendering will continue. " + detail
        )
        return []
    cut_frames = sorted(
        {
            round(float(match.group(1)) * fps)
            for match in SCENE_TIME_RE.finditer(completed.stderr)
            if float(match.group(1)) > 0
        }
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"analysis": expected, "cut_frames": cut_frames}, indent=2),
        encoding="utf-8",
    )
    return cut_frames


def cuts_inside(cut_frames: list[int], start_frame: int, end_frame: int) -> list[int]:
    return [frame for frame in cut_frames if start_frame < frame < end_frame]
