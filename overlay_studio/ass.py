from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable

from .models import OverlayEntry, ProjectSettings


def _ass_color(hex_color: str, alpha: int = 0) -> str:
    clean = hex_color.strip().lstrip("#")
    if len(clean) != 6 or not re.fullmatch(r"[0-9A-Fa-f]{6}", clean):
        clean = "FFFFFF"
    red, green, blue = clean[0:2], clean[2:4], clean[4:6]
    return f"&H{alpha:02X}{blue}{green}{red}&"


def _ass_time_from_frame(frame: int, fps: int = 30) -> str:
    # Floor to centiseconds. At constant 30 fps this keeps the intended frame
    # inclusive at start and exclusive at end without leaking into the next frame.
    centiseconds = math.floor(frame * 100 / fps + 1e-9)
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centis = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _escape_text(text: str) -> str:
    placeholder = "__OVERLAY_LINE_BREAK__"
    escaped = text.replace("\\N", placeholder)
    escaped = escaped.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    return escaped.replace(placeholder, "\\N")


def _render_text(entry: OverlayEntry, settings: ProjectSettings) -> str:
    text = _escape_text(entry.wrapped_text or entry.text)
    if entry.effect != "ACCENT_WORD" or not entry.accent_word:
        return text
    match = re.search(re.escape(entry.accent_word), text, flags=re.IGNORECASE)
    if not match:
        return text
    accent = _ass_color(settings.accent_color)
    normal = _ass_color(settings.text_color)
    return (
        text[: match.start()]
        + "{\\c"
        + accent
        + "}"
        + text[match.start() : match.end()]
        + "{\\c"
        + normal
        + "}"
        + text[match.end() :]
    )


def _anchor(entry: OverlayEntry, settings: ProjectSettings) -> tuple[int, int, int]:
    width, height = settings.output_width, settings.output_height
    side = round(width * settings.safe_side_percent / 100.0)
    y = round(height * settings.safe_top_percent / 100.0)
    if entry.resolved_position == "TOP_CENTER":
        return 8, width // 2, y
    if entry.resolved_position == "TOP_RIGHT":
        return 9, width - side, y
    return 7, side, y


def _effect_tags(entry: OverlayEntry, settings: ProjectSettings) -> str:
    scale = settings.output_height / 1080.0
    clean_border = max(2, round(3.0 * scale))
    strong_border = max(clean_border + 1, round(4.5 * scale))
    shadow = max(1, round(2.0 * scale))
    if entry.effect == "STRONG_OUTLINE":
        return (
            f"\\bord{strong_border}\\shad{shadow}\\blur0.18"
            r"\3c&H000000&\4c&H000000&\4a&H58&"
        )
    return (
        f"\\bord{clean_border}\\shad{shadow}\\blur0.12"
        r"\3c&H080808&\4c&H000000&\4a&H68&"
    )


def _motion_tags(
    entry: OverlayEntry,
    x: int,
    y: int,
    alignment: int,
    settings: ProjectSettings,
) -> str:
    duration_ms = max(1, round(entry.duration_seconds * 1000))
    intro_ms = min(300, max(150, round(duration_ms * 0.12)))
    outro_ms = min(230, max(130, round(duration_ms * 0.09)))
    if duration_ms < 900:
        intro_ms = min(130, max(60, duration_ms // 6))
        outro_ms = intro_ms
    movement_x = 0
    movement_y = 0
    transform = ""
    if entry.animation in {"EASE_SIDE", "SLIDE_IN", "SHORT_DRIFT"}:
        distance = max(12, round(settings.output_width * 0.008))
        movement_x = -distance if alignment != 9 else distance
    elif entry.animation in {"EASE_UP", "FADE_RISE", "GENTLE_DROP"}:
        movement_y = max(10, round(settings.output_height * 0.012))
    elif entry.animation in {"SOFT_SCALE", "SOFT_POP"}:
        transform = (
            f"\\fscx94\\fscy94"
            f"\\t(0,{intro_ms},0.55,\\fscx100\\fscy100)"
        )
    elif entry.animation == "FOCUS_IN":
        transform = f"\\blur1.25\\t(0,{intro_ms},0.55,\\blur0.12)"

    if movement_x or movement_y:
        position_tag = f"\\move({x + movement_x},{y + movement_y},{x},{y},0,{intro_ms})"
    else:
        position_tag = f"\\pos({x},{y})"
    return f"\\an{alignment}{position_tag}\\fad({intro_ms},{outro_ms}){transform}"


def build_ass(
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    *,
    window_start_frame: int = 0,
    window_end_frame: int | None = None,
) -> str:
    normal_color = _ass_color(settings.text_color)
    font_name = settings.font_name.replace(",", " ").strip() or "Segoe UI Semibold"
    header = f"""[Script Info]
Title: Overlay Text Studio
ScriptType: v4.00+
WrapStyle: 2
ScaledBorderAndShadow: yes
PlayResX: {settings.output_width}
PlayResY: {settings.output_height}
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Overlay,{font_name},100,{normal_color},{normal_color},&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,4,4,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    event_lines: list[str] = []
    for entry in entries:
        if not entry.enabled:
            continue
        if window_end_frame is not None and entry.start_frame >= window_end_frame:
            continue
        if entry.end_frame <= window_start_frame:
            continue
        start = max(entry.start_frame, window_start_frame) - window_start_frame
        end_global = entry.end_frame if window_end_frame is None else min(entry.end_frame, window_end_frame)
        end = end_global - window_start_frame
        if end <= start:
            continue
        alignment, x, y = _anchor(entry, settings)
        tags = (
            f"\\fs{entry.font_size_px}"
            + _effect_tags(entry, settings)
            + _motion_tags(entry, x, y, alignment, settings)
            + f"\\fsp{max(0, round(settings.output_height / 1080.0))}"
            + f"\\c{normal_color}"
        )
        text = _render_text(entry, settings)
        event_lines.append(
            "Dialogue: 1,"
            + _ass_time_from_frame(start, settings.fps)
            + ","
            + _ass_time_from_frame(end, settings.fps)
            + ",Overlay,,0,0,0,,{"
            + tags
            + "}"
            + text
        )
    return header + "\n".join(event_lines) + "\n"


def write_ass(
    path: str | Path,
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    *,
    window_start_frame: int = 0,
    window_end_frame: int | None = None,
) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        build_ass(
            entries,
            settings,
            window_start_frame=window_start_frame,
            window_end_frame=window_end_frame,
        ),
        encoding="utf-8-sig",
    )
    return destination
