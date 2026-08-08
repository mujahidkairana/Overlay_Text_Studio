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
    accent = _ass_color(settings.accent_color)
    normal = _ass_color(settings.text_color)
    small_size = max(18, round(entry.font_size_px * 0.48))
    if entry.semantic_type == "EVIDENCE":
        return f"{{\\fs{small_size}\\c{accent}}}EVIDENCE  {{\\fs{entry.font_size_px}\\c{normal}}}{text}"
    if entry.semantic_type == "WARNING":
        return f"{{\\fs{small_size}\\c{accent}}}CAUTION  {{\\fs{entry.font_size_px}\\c{normal}}}{text}"
    if entry.semantic_type == "UNCERTAINTY":
        separator = "\\N" if "\\N" not in text else "  "
        return f"{{\\fs{small_size}\\c{accent}}}POSSIBLE / NOT PROVEN{separator}{{\\fs{entry.font_size_px}\\c{normal}}}{text}"
    if entry.semantic_type == "SECTION":
        return text.upper()
    if entry.semantic_type == "NUMBER":
        match = re.search(r"\d[\d,.]*%?", text)
        if match:
            large_size = round(entry.font_size_px * 1.22)
            label_size = max(18, round(entry.font_size_px * 0.58))
            return (
                f"{{\\fs{label_size}\\c{normal}}}{text[:match.start()]}"
                f"{{\\fs{large_size}\\c{accent}}}{match.group(0)}"
                f"{{\\fs{label_size}\\c{normal}}}{text[match.end():]}"
            )
    if entry.semantic_type == "COMPARISON":
        match = re.search(r"\b(?:VS\.?|VERSUS)\b", text, flags=re.IGNORECASE)
        if match:
            return text[:match.start()] + "{\\c" + accent + "}" + match.group(0).upper() + "{\\c" + normal + "}" + text[match.end():]
    if entry.effect != "ACCENT_WORD" or not entry.accent_word:
        return text
    match = re.search(re.escape(entry.accent_word), text, flags=re.IGNORECASE)
    if not match:
        return text
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
    if entry.effect == "PROTECTED_PLATE":
        return (
            f"\\bord{clean_border}\\shad{max(1, round(1.5 * scale))}\\blur0.12"
            r"\3c&H080808&\4c&H000000&\4a&H72&"
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


def _estimated_text_width(text: str, font_size: int) -> int:
    """Estimate a semibold sans-serif line width without relying on local fonts."""
    narrow = set(" !'.,:;Iijl|1")
    wide = set("MW@%#QO")
    units = 0.0
    for character in text:
        if character in narrow:
            units += 0.28
        elif character in wide:
            units += 0.58
        else:
            units += 0.38
    return max(font_size, round(units * font_size))


def _protected_plate_geometry(
    entry: OverlayEntry,
    settings: ProjectSettings,
    alignment: int,
    anchor_x: int,
    anchor_y: int,
) -> tuple[int, int, int, int, int]:
    lines = (entry.wrapped_text or entry.text).split("\\N")
    font_size = max(1, entry.font_size_px)
    scale = settings.output_height / 1080.0
    horizontal_padding = max(round(22 * scale), round(font_size * 0.25))
    vertical_padding = max(round(12 * scale), round(font_size * 0.15))
    width = max(_estimated_text_width(line, font_size) for line in lines)
    rendered_line_count = len(lines)
    if entry.semantic_type == "UNCERTAINTY" and len(lines) == 1:
        rendered_line_count += 1
        label_size = max(18, round(font_size * 0.48))
        width = max(width, _estimated_text_width("POSSIBLE / NOT PROVEN", label_size))
    if entry.semantic_type == "WARNING" and len(lines) == 1:
        label_size = max(18, round(font_size * 0.48))
        width = max(
            width,
            _estimated_text_width("CAUTION", label_size)
            + _estimated_text_width("  " + lines[0], font_size),
        )
    height = round(font_size * (1.12 + max(0, rendered_line_count - 1) * 1.08))
    plate_width = width + horizontal_padding * 2
    plate_height = height + vertical_padding * 2
    radius = min(
        max(round(14 * scale), round(font_size * 0.16)),
        plate_height // 2,
    )
    if alignment == 7:
        center_x = anchor_x + plate_width // 2
    elif alignment == 9:
        center_x = anchor_x - plate_width // 2
    else:
        center_x = anchor_x
    center_y = anchor_y + plate_height // 2
    return center_x, center_y, plate_width, plate_height, radius


def _rounded_rectangle(width: int, height: int, radius: int) -> str:
    # Cubic Bezier approximation of a rounded rectangle. Vector rendering keeps
    # the plate smooth at both 1080p and 4K output resolutions.
    kappa = 0.5522848
    control = round(radius * kappa)
    right, bottom = width, height
    return (
        f"m {radius} 0 l {right - radius} 0 "
        f"b {right - radius + control} 0 {right} {radius - control} {right} {radius} "
        f"l {right} {bottom - radius} "
        f"b {right} {bottom - radius + control} {right - radius + control} {bottom} {right - radius} {bottom} "
        f"l {radius} {bottom} "
        f"b {radius - control} {bottom} 0 {bottom - radius + control} 0 {bottom - radius} "
        f"l 0 {radius} "
        f"b 0 {radius - control} {radius - control} 0 {radius} 0"
    )


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
Style: OverlayPlate,{font_name},100,&HB8101010&,&HB8101010&,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

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
        if entry.effect == "PROTECTED_PLATE":
            center_x, center_y, plate_width, plate_height, radius = (
                _protected_plate_geometry(entry, settings, alignment, x, y)
            )
            plate_tags = (
                "\\p1\\bord0\\shad0\\blur0.55"
                r"\c&H101010&\alpha&H48&"
                + _motion_tags(entry, center_x, center_y, 5, settings)
            )
            event_lines.append(
                "Dialogue: 0,"
                + _ass_time_from_frame(start, settings.fps)
                + ","
                + _ass_time_from_frame(end, settings.fps)
                + ",OverlayPlate,,0,0,0,,{"
                + plate_tags
                + "}"
                + _rounded_rectangle(plate_width, plate_height, radius)
            )
            alignment, x, y = 5, center_x, center_y
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
