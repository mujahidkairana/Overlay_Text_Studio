from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .models import VideoInfo


ProgressCallback = Callable[[float, str], None]


class MediaError(RuntimeError):
    pass


def _candidate_binary(name: str, app_root: str | Path | None = None) -> str:
    executable = f"{name}.exe" if os.name == "nt" else name
    if app_root:
        root = Path(app_root)
        bundled = root / "tools" / executable
        if bundled.exists():
            return str(bundled)
        nested = root / "tools" / "ffmpeg" / "bin" / executable
        if nested.exists():
            return str(nested)
        shared_roots: list[Path] = []
        configured = (
            os.environ.get("OVERLAY_STUDIO_SHARED_COMPONENTS", "").strip()
            or os.environ.get("OVERLAY_SHARED_COMPONENTS", "").strip()
        )
        if configured:
            shared_roots.append(Path(configured))
        pointer = root / "shared_components_path.txt"
        if pointer.exists():
            try:
                shared_roots.append(Path(pointer.read_text(encoding="utf-8-sig").strip()))
            except OSError:
                pass
        for shared in shared_roots:
            for candidate in (
                shared / "ffmpeg" / "bin" / executable,
                shared / "FFmpeg" / "bin" / executable,
                shared / "tools" / executable,
            ):
                if candidate.exists():
                    return str(candidate)
            # Reuse versioned shared layouts such as FFmpeg/8.1.2/bin.
            versioned = sorted(
                (shared / "FFmpeg").glob(f"*/bin/{executable}"),
                reverse=True,
            )
            if versioned:
                return str(versioned[0])
    found = shutil.which(executable) or shutil.which(name)
    if found:
        return found
    raise MediaError(
        f"{executable} was not found. Put FFmpeg files in the app's tools folder or add FFmpeg to PATH."
    )


def ffmpeg_path(app_root: str | Path | None = None) -> str:
    return _candidate_binary("ffmpeg", app_root)


def ffprobe_path(app_root: str | Path | None = None) -> str:
    return _candidate_binary("ffprobe", app_root)


def _ratio(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            return float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_video(path: str | Path, app_root: str | Path | None = None) -> VideoInfo:
    if not str(path).strip():
        raise MediaError("Choose a source video first.")
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise MediaError(f"Video not found: {source}")
    if not source.is_file():
        raise MediaError(f"Video path is not a file: {source}")
    command = [
        ffprobe_path(app_root),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(source),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode != 0:
        raise MediaError(completed.stderr.strip() or "FFprobe could not read the video.")
    data = json.loads(completed.stdout)
    video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video_stream:
        raise MediaError("The selected file has no video stream.")
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    duration = float(video_stream.get("duration") or data.get("format", {}).get("duration") or 0.0)
    return VideoInfo(
        path=str(source),
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        duration_seconds=duration,
        source_fps=_ratio(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")),
        has_audio=has_audio,
        codec_name=str(video_stream.get("codec_name") or ""),
    )


def file_fingerprint(path: str | Path) -> str:
    source = Path(path).resolve()
    stat = source.stat()
    return f"{source}|{stat.st_size}|{stat.st_mtime_ns}"


def extract_analysis_frames(
    video_path: str | Path,
    output_dir: str | Path,
    *,
    sample_fps: float = 1.0,
    analysis_width: int = 640,
    app_root: str | Path | None = None,
    duration_seconds: float | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Extract low-resolution frames in one pass; reuse a matching cache."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    manifest_path = destination / "analysis_manifest.json"
    fingerprint = file_fingerprint(video_path)
    expected = {
        "fingerprint": fingerprint,
        "sample_fps": sample_fps,
        "analysis_width": analysis_width,
    }
    if manifest_path.exists():
        try:
            current = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            current = {}
        cached = sorted(destination.glob("frame_*.jpg"))
        if current == expected and cached:
            if progress:
                progress(1.0, f"Reusing {len(cached)} cached analysis frames")
            return cached

    for old in destination.glob("frame_*.jpg"):
        old.unlink(missing_ok=True)
    pattern = destination / "frame_%06d.jpg"
    command = [
        ffmpeg_path(app_root),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(Path(video_path).resolve()),
        "-vf",
        f"fps={sample_fps},scale={analysis_width}:-2:flags=area",
        "-q:v",
        "5",
        str(pattern),
    ]
    if progress:
        progress(0.02, "Extracting small analysis frames")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    _, stderr = process.communicate()
    if process.returncode != 0:
        raise MediaError(stderr.strip() or "Could not extract analysis frames.")
    frames = sorted(destination.glob("frame_*.jpg"))
    if not frames:
        raise MediaError("FFmpeg produced no analysis frames.")
    manifest_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    if progress:
        progress(1.0, f"Extracted {len(frames)} analysis frames")
    return frames
