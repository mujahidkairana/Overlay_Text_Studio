from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable

from .ass import write_ass
from .media import MediaError, ffmpeg_path, ffprobe_path, file_fingerprint, probe_video
from .models import OverlayEntry, ProjectSettings, VideoInfo


ProgressCallback = Callable[[float, str], None]
CancelCheck = Callable[[], bool]


class RenderCancelled(MediaError):
    pass


def _filter_path(path: str | Path) -> str:
    text = Path(path).resolve().as_posix()
    text = text.replace("'", r"\'")
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        text = text[0] + r"\:" + text[2:]
    return text


def _relative_action_intervals(
    entries: Iterable[OverlayEntry],
    action: str,
    *,
    window_start_frame: int,
    window_end_frame: int,
    fps: int,
) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for entry in entries:
        if not entry.enabled or entry.visual_action != action:
            continue
        action_start = entry.action_start_frame or entry.start_frame
        action_end = entry.action_end_frame if entry.action_end_frame > action_start else entry.end_frame
        start = max(window_start_frame, action_start)
        end = min(window_end_frame, action_end)
        if end > start:
            intervals.append(((start - window_start_frame) / fps, (end - window_start_frame) / fps))
    return intervals


def _between_expression(intervals: list[tuple[float, float]]) -> str:
    if not intervals:
        return "0"
    return "+".join(f"between(t\\,{start:.6f}\\,{end:.6f})" for start, end in intervals)


def _video_filter(
    ass_path: Path,
    settings: ProjectSettings,
    entries: Iterable[OverlayEntry] = (),
    *,
    window_start_frame: int = 0,
    window_end_frame: int | None = None,
) -> str:
    end_frame = window_end_frame if window_end_frame is not None else 2**31 - 1
    scale = (
        f"fps={settings.fps},"
        f"scale={settings.output_width}:{settings.output_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={settings.output_width}:{settings.output_height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    punch_intervals = _relative_action_intervals(
        entries, "PUNCH_IN", window_start_frame=window_start_frame,
        window_end_frame=end_frame, fps=settings.fps,
    )
    dim_intervals = _relative_action_intervals(
        entries, "DIM_FOCUS", window_start_frame=window_start_frame,
        window_end_frame=end_frame, fps=settings.fps,
    )
    effects: list[str] = []
    if punch_intervals:
        active = _between_expression(punch_intervals)
        zoom = f"1+0.05*min(1\\,{active})"
        effects.extend(
            [
                f"scale=w='iw*({zoom})':h='ih*({zoom})':eval=frame",
                f"crop={settings.output_width}:{settings.output_height}:(iw-ow)/2:(ih-oh)/2",
            ]
        )
    if dim_intervals:
        active = _between_expression(dim_intervals)
        effects.append(f"eq=brightness='-0.06*min(1\\,{active})':eval=frame")
    ass_filter = f"ass=filename='{_filter_path(ass_path)}'"
    if settings.font_file:
        ass_filter += f":fontsdir='{_filter_path(Path(settings.font_file).resolve().parent)}'"
    return ",".join([scale, *effects, ass_filter])


def _encoder_args(settings: ProjectSettings) -> list[str]:
    if settings.encoder == "h264_qsv":
        return [
            "-c:v",
            "h264_qsv",
            "-global_quality",
            str(max(1, min(51, settings.crf + 2))),
            "-look_ahead",
            "0",
        ]
    if settings.encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p4",
            "-rc", "vbr", "-cq", str(settings.crf), "-b:v", "0",
        ]
    if settings.encoder == "h264_amf":
        return [
            "-c:v", "h264_amf", "-quality", "speed",
            "-rc", "cqp", "-qp_i", str(settings.crf), "-qp_p", str(settings.crf),
        ]
    return [
        "-c:v",
        "libx264",
        "-preset",
        settings.x264_preset,
        "-crf",
        str(settings.crf),
        "-tune",
        "stillimage",
    ]


def select_fast_encoder(
    app_root: str | Path,
    cache_path: str | Path,
    *,
    crf: int = 18,
) -> tuple[str, dict[str, float]]:
    """Benchmark available encoders once and cache the fastest reliable choice."""
    cache = Path(cache_path)
    if cache.exists():
        try:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            selected = str(payload.get("selected", ""))
            timings = {
                str(key): float(value)
                for key, value in dict(payload.get("seconds", {})).items()
            }
            if selected in {"libx264", "h264_qsv", "h264_nvenc", "h264_amf"}:
                return selected, timings
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    timings: dict[str, float] = {}
    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"):
        probe_settings = ProjectSettings(
            output_width=1280,
            output_height=720,
            encoder=encoder,
            x264_preset="veryfast",
            crf=crf,
        )
        command = [
            ffmpeg_path(app_root),
            "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=3",
            *_encoder_args(probe_settings),
            "-pix_fmt", "yuv420p", "-f", "null", os.devnull,
        ]
        started = time.monotonic()
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode == 0:
            timings[encoder] = round(time.monotonic() - started, 3)

    cpu_time = timings.get("libx264")
    hardware = {
        name: seconds
        for name, seconds in timings.items()
        if name != "libx264"
    }
    if hardware:
        hardware_name = min(hardware, key=hardware.get)
        if cpu_time is None or hardware[hardware_name] <= cpu_time * 1.40:
            selected = hardware_name
        else:
            selected = "libx264"
    else:
        selected = "libx264"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps({"selected": selected, "seconds": timings}, indent=2),
        encoding="utf-8",
    )
    return selected, timings


def _run_ffmpeg(
    command: list[str],
    *,
    expected_seconds: float,
    progress: ProgressCallback | None,
    label: str,
    cancel_check: CancelCheck | None = None,
) -> None:
    command = command[:-1] + ["-progress", "pipe:1", "-nostats", command[-1]]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            if cancel_check and cancel_check():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                raise RenderCancelled("Render stopped safely. Completed chunks are still reusable.")
            key, separator, value = line.strip().partition("=")
            if separator and key in {"out_time_us", "out_time_ms"}:
                try:
                    elapsed = int(value) / 1_000_000.0
                except ValueError:
                    continue
                if progress:
                    progress(min(0.99, elapsed / max(0.01, expected_seconds)), label)
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        raise
    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    process.stdout.close()
    if process.stderr:
        process.stderr.close()
    if return_code != 0:
        raise MediaError(stderr.strip() or "FFmpeg render failed.")
    if progress:
        progress(1.0, label)


def render_clip(
    video_path: str | Path,
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    *,
    start_frame: int,
    end_frame: int,
    output_path: str | Path,
    work_dir: str | Path,
    app_root: str | Path | None = None,
    include_audio: bool = True,
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> Path:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    workspace = Path(work_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    ass_path = workspace / f"overlay_{start_frame}_{end_frame}.ass"
    write_ass(
        ass_path,
        entries,
        settings,
        window_start_frame=start_frame,
        window_end_frame=end_frame,
    )
    duration = (end_frame - start_frame) / settings.fps
    if duration <= 0:
        raise MediaError("Render range is empty.")
    temporary = destination.with_suffix(destination.suffix + ".part.mp4")
    temporary.unlink(missing_ok=True)
    command = [
        ffmpeg_path(app_root),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_frame / settings.fps:.9f}",
        "-i",
        str(Path(video_path).resolve()),
        "-t",
        f"{duration:.9f}",
        "-map",
        "0:v:0",
    ]
    if include_audio:
        command += ["-map", "0:a:0?"]
    command += [
        "-vf",
        _video_filter(
            ass_path,
            settings,
            entries,
            window_start_frame=start_frame,
            window_end_frame=end_frame,
        ),
    ]
    command += _encoder_args(settings)
    command += [
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(settings.fps),
        "-g",
        str(settings.fps * 2),
        "-video_track_timescale",
        "30000",
    ]
    if include_audio:
        command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        command += ["-an"]
    command += ["-movflags", "+faststart", str(temporary)]
    _run_ffmpeg(
        command,
        expected_seconds=duration,
        progress=progress,
        label="Rendering clip",
        cancel_check=cancel_check,
    )
    temporary.replace(destination)
    return destination


def plan_chunks(
    duration_frames: int,
    entries: Iterable[OverlayEntry],
    *,
    target_seconds: int,
    fps: int = 30,
) -> list[tuple[int, int]]:
    active = [entry for entry in entries if entry.enabled]
    target = max(fps * 10, target_seconds * fps)
    chunks: list[tuple[int, int]] = []
    start = 0
    while start < duration_frames:
        boundary = min(duration_frames, start + target)
        if boundary < duration_frames:
            changed = True
            while changed:
                changed = False
                for entry in active:
                    if entry.start_frame < boundary < entry.end_frame:
                        boundary = min(duration_frames, entry.end_frame)
                        changed = True
            if boundary <= start:
                boundary = min(duration_frames, start + target)
        chunks.append((start, boundary))
        start = boundary
    return chunks


def _chunk_key(
    video_path: str | Path,
    start: int,
    end: int,
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
) -> str:
    relevant = [
        entry.to_dict()
        for entry in entries
        if entry.enabled and entry.start_frame < end and entry.end_frame > start
    ]
    payload = {
        "video": file_fingerprint(video_path),
        "start": start,
        "end": end,
        "entries": relevant,
        "settings": settings.to_dict(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:12]


def _valid_media(path: Path, app_root: str | Path | None = None) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    command = [ffprobe_path(app_root), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True)
    return completed.returncode == 0 and bool(completed.stdout.strip())


def estimate_required_bytes(video: VideoInfo, settings: ProjectSettings) -> int:
    """Conservative space estimate for cached chunks, joined video, and final output."""
    source_size = Path(video.path).stat().st_size
    bitrate_mbps = 38.0 if settings.output_height >= 2160 else 14.0
    encoded = int(video.duration_seconds * bitrate_mbps * 1_000_000 / 8)
    return max(int(source_size * 2.2), int(encoded * 3.15)) + 512 * 1024 * 1024


def ensure_free_space(
    destination_dir: str | Path,
    video: VideoInfo,
    settings: ProjectSettings,
) -> tuple[int, int]:
    path = Path(destination_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    required = estimate_required_bytes(video, settings)
    if free < required:
        raise MediaError(
            f"Not enough free disk space. About {required / 1024**3:.1f} GB is required, "
            f"but only {free / 1024**3:.1f} GB is available in {path}."
        )
    return free, required


def verify_render_output(
    path: str | Path,
    expected: VideoInfo,
    settings: ProjectSettings,
    *,
    app_root: str | Path | None = None,
) -> dict[str, object]:
    """Verify the completed file before it is announced or moved into place."""
    output = Path(path)
    if not output.exists() or output.stat().st_size < 10_000:
        raise MediaError("Final verification failed: output file is missing or too small.")
    rendered = probe_video(output, app_root)
    tolerance = max(2 / settings.fps, 0.08)
    if abs(rendered.duration_seconds - expected.duration_seconds) > tolerance:
        raise MediaError(
            "Final verification failed: output duration "
            f"{rendered.duration_seconds:.3f}s does not match source {expected.duration_seconds:.3f}s."
        )
    if abs(rendered.source_fps - settings.fps) > 0.05:
        raise MediaError(
            f"Final verification failed: expected {settings.fps} fps, got {rendered.source_fps:.3f} fps."
        )
    if (rendered.width, rendered.height) != (settings.output_width, settings.output_height):
        raise MediaError(
            "Final verification failed: expected "
            f"{settings.output_width}x{settings.output_height}, got {rendered.width}x{rendered.height}."
        )
    if expected.has_audio and not rendered.has_audio:
        raise MediaError("Final verification failed: the source audio track is missing.")
    return {
        "duration_seconds": rendered.duration_seconds,
        "fps": rendered.source_fps,
        "width": rendered.width,
        "height": rendered.height,
        "has_audio": rendered.has_audio,
        "bytes": output.stat().st_size,
        "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _concat_chunks(
    chunks: list[Path],
    destination: Path,
    *,
    app_root: str | Path | None,
) -> None:
    list_path = destination.parent / "concat_video_chunks.txt"
    lines = []
    for chunk in chunks:
        escaped = chunk.name.replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = [
        ffmpeg_path(app_root),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        list_path.name,
        "-c",
        "copy",
        str(destination.name),
    ]
    completed = subprocess.run(command, cwd=destination.parent, capture_output=True, text=True)
    if completed.returncode != 0:
        raise MediaError(completed.stderr.strip() or "Could not join rendered chunks.")


def render_full_resumable(
    video: VideoInfo,
    entries: list[OverlayEntry],
    settings: ProjectSettings,
    *,
    output_path: str | Path,
    project_dir: str | Path,
    app_root: str | Path | None = None,
    progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> Path:
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    project = Path(project_dir)
    chunks_dir = project / "cache" / "render_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    ensure_free_space(project, video, settings)
    if destination.parent != project.resolve() and project.resolve() not in destination.parents:
        ensure_free_space(destination.parent, video, settings)
    chunks = plan_chunks(
        video.duration_frames_30,
        entries,
        target_seconds=settings.chunk_target_seconds,
        fps=settings.fps,
    )
    rendered: list[Path] = []
    total = max(1, len(chunks))
    for index, (start, end) in enumerate(chunks):
        if cancel_check and cancel_check():
            raise RenderCancelled("Render stopped safely. Completed chunks are still reusable.")
        key = _chunk_key(video.path, start, end, entries, settings)
        chunk_path = chunks_dir / f"chunk_{index:04d}_{key}.mp4"
        if _valid_media(chunk_path, app_root):
            rendered.append(chunk_path)
            if progress:
                progress((index + 1) / total * 0.88, f"Reusing chunk {index + 1}/{total}")
            continue

        def chunk_progress(value: float, label: str) -> None:
            if progress:
                overall = (index + value) / total * 0.88
                progress(overall, f"Chunk {index + 1}/{total}: {label}")

        render_clip(
            video.path,
            entries,
            settings,
            start_frame=start,
            end_frame=end,
            output_path=chunk_path,
            work_dir=chunks_dir / "ass",
            app_root=app_root,
            include_audio=False,
            progress=chunk_progress,
            cancel_check=cancel_check,
        )
        rendered.append(chunk_path)

    video_only = chunks_dir / "joined_video_only.mp4"
    if progress:
        progress(0.90, "Joining cached video chunks")
    _concat_chunks(rendered, video_only, app_root=app_root)
    temporary = destination.with_suffix(destination.suffix + ".part.mp4")
    temporary.unlink(missing_ok=True)
    if video.has_audio:
        command = [
            ffmpeg_path(app_root),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_only),
            "-i",
            video.path,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-t",
            f"{video.duration_seconds:.9f}",
            "-movflags",
            "+faststart",
            str(temporary),
        ]
        _run_ffmpeg(
            command,
            expected_seconds=video.duration_seconds,
            progress=(
                (lambda value, label: progress(0.96 + value * 0.035, "Restoring source audio"))
                if progress
                else None
            ),
            label="Restoring source audio",
            cancel_check=cancel_check,
        )
    else:
        shutil.copy2(video_only, temporary)
    verification = verify_render_output(temporary, video, settings, app_root=app_root)
    report_path = project / "final_verification.json"
    report_path.write_text(json.dumps(verification, indent=2), encoding="utf-8")
    temporary.replace(destination)
    if progress:
        progress(1.0, "Final video ready")
    return destination
