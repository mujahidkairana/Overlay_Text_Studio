from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Iterable

import pandas as pd

from .models import OverlayEntry, PRIORITIES, SEMANTIC_TYPES, VISUAL_ACTIONS


FPS = 30
TIMECODE_RE = re.compile(r"^\s*(\d{1,3}):(\d{1,2}):(\d{1,2})[.:](\d{1,2})\s*$")

COLUMN_ALIASES = {
    "scene_id": {"SCENE_ID", "ID", "OVERLAY_ID", "SCENE"},
    "start": {"START_TIME_30FPS", "START_30FPS", "START_TIME", "START"},
    "end": {"END_TIME_30FPS", "END_30FPS", "END_TIME", "END"},
    "text": {"ON_SCREEN_TEXT", "OVERLAY_TEXT", "TEXT", "SCREEN_TEXT"},
    "semantic_type": {"TYPE", "SEMANTIC_TYPE"},
    "priority": {"PRIORITY"},
    "accent_word": {"EMPHASIS_WORD", "ACCENT_WORD"},
    "visual_action": {"VISUAL_ACTION"},
    "sfx": {"SFX", "SOUND_EFFECT"},
    "lock_style": {"LOCK_STYLE"},
}


class TimingValidationError(ValueError):
    pass


def normalize_overlay_text(value: object, *, context: str = "Overlay text") -> str:
    """Normalize explicit line breaks and reject layouts that cannot be rendered safely."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    explicit_lines = [part.strip() for part in re.split(r"\\N|\n", text) if part.strip()]
    if len(explicit_lines) > 2:
        raise TimingValidationError(
            f"{context} uses {len(explicit_lines)} explicit lines; the maximum is 2. "
            "Shorten it or combine the extra line."
        )
    return "\\N".join(explicit_lines) if len(explicit_lines) > 1 else text


def timecode_to_frame(value: object, fps: int = FPS) -> int:
    """Convert HH:MM:SS.FF (or HH:MM:SS:FF) to an integer frame index."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        raise TimingValidationError("timecode is blank")
    if isinstance(value, int):
        if value < 0:
            raise TimingValidationError("frame number cannot be negative")
        return value
    if isinstance(value, float) and value.is_integer():
        if value < 0:
            raise TimingValidationError("frame number cannot be negative")
        return int(value)
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    match = TIMECODE_RE.match(text)
    if not match:
        raise TimingValidationError(
            f"'{text}' is invalid; use HH:MM:SS.FF with frame FF from 00 to {fps - 1:02d}"
        )
    hours, minutes, seconds, frames = map(int, match.groups())
    if minutes > 59 or seconds > 59 or frames >= fps:
        raise TimingValidationError(
            f"'{text}' is invalid; MM/SS must be 00-59 and FF must be 00-{fps - 1:02d}"
        )
    return ((hours * 60 + minutes) * 60 + seconds) * fps + frames


def frame_to_timecode(frame: int, fps: int = FPS) -> str:
    if frame < 0:
        raise TimingValidationError("frame number cannot be negative")
    seconds_total, frames = divmod(int(frame), fps)
    hours, remainder = divmod(seconds_total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{frames:02d}"


def frame_to_seconds(frame: int, fps: int = FPS) -> float:
    return frame / float(fps)


def _normalize_header(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", str(value).strip().upper()).strip("_")


def resolve_columns(columns: Iterable[object]) -> dict[str, str]:
    actual = {str(col): _normalize_header(col) for col in columns}
    resolved: dict[str, str] = {}
    for role, aliases in COLUMN_ALIASES.items():
        for original, normalized in actual.items():
            if normalized in aliases:
                resolved[role] = original
                break
    missing = [role for role in ("start", "end", "text") if role not in resolved]
    if missing:
        expected = "SCENE_ID, START_TIME_30FPS, END_TIME_30FPS, ON_SCREEN_TEXT"
        raise TimingValidationError(
            f"Missing required column(s): {', '.join(missing)}. Recommended headers: {expected}."
        )
    return resolved


def _optional_text(row: pd.Series, columns: dict[str, str], role: str, default: str) -> str:
    if role not in columns:
        return default
    value = str(row[columns[role]]).strip()
    return value if value else default


def _parse_bool(value: object, *, context: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().upper()
    if not normalized:
        return False
    if normalized in {"TRUE", "YES", "Y", "1"}:
        return True
    if normalized in {"FALSE", "NO", "N", "0"}:
        return False
    raise TimingValidationError(f"{context}: use TRUE or FALSE.")


def classify_overlay_type(text: str, requested: str = "AUTO") -> str:
    normalized = requested.strip().upper() or "AUTO"
    if normalized != "AUTO":
        if normalized not in SEMANTIC_TYPES:
            raise TimingValidationError(
                f"TYPE '{requested}' is invalid; use AUTO or one of {', '.join(SEMANTIC_TYPES)}."
            )
        return normalized
    if text.rstrip().endswith("?"):
        return "QUESTION"
    if re.search(r"(?:^|\s)(?:\d[\d,.]*|\d+(?:\.\d+)?%)(?:\s|$)", text):
        return "NUMBER"
    return "FACT"


def read_timing_table(path: str | Path) -> pd.DataFrame:
    if not str(path).strip():
        raise TimingValidationError("Choose an overlay timing CSV/XLSX file first.")
    source = Path(path)
    if not source.exists():
        raise TimingValidationError(f"Timing file not found: {source}")
    if not source.is_file():
        raise TimingValidationError(f"Timing path is not a file: {source}")
    suffix = source.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(source, dtype=object, keep_default_na=False)
    if suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(source, dtype=object, keep_default_na=False)
    raise TimingValidationError("Timing input must be CSV or XLSX.")


def load_entries(
    path: str | Path,
    *,
    video_duration_frames: int | None = None,
    fps: int = FPS,
) -> tuple[list[OverlayEntry], list[str]]:
    table = read_timing_table(path)
    columns = resolve_columns(table.columns)
    entries: list[OverlayEntry] = []
    warnings: list[str] = []

    for index, row in table.iterrows():
        excel_row = index + 2
        raw_text = str(row[columns["text"]]).strip()
        if not raw_text:
            warnings.append(f"Row {excel_row}: blank overlay text; row skipped.")
            continue
        scene_id = (
            str(row[columns["scene_id"]]).strip()
            if "scene_id" in columns and str(row[columns["scene_id"]]).strip()
            else f"OVERLAY_{len(entries) + 1:03d}"
        )
        raw_text = normalize_overlay_text(raw_text, context=f"Row {excel_row} ({scene_id})")
        try:
            start = timecode_to_frame(row[columns["start"]], fps)
            end = timecode_to_frame(row[columns["end"]], fps)
        except TimingValidationError as exc:
            raise TimingValidationError(f"Row {excel_row} ({scene_id}): {exc}") from exc
        if end <= start:
            raise TimingValidationError(
                f"Row {excel_row} ({scene_id}): end must be after start."
            )
        if video_duration_frames is not None and end > video_duration_frames + fps:
            raise TimingValidationError(
                f"Row {excel_row} ({scene_id}): end {frame_to_timecode(end)} is beyond the video."
            )
        if end - start < 15:
            warnings.append(
                f"{scene_id}: visible for under 0.5 seconds; animation will be reduced."
            )
        semantic_type = classify_overlay_type(
            raw_text, _optional_text(row, columns, "semantic_type", "AUTO")
        )
        priority = _optional_text(row, columns, "priority", "MEDIUM").upper()
        if priority not in PRIORITIES:
            raise TimingValidationError(
                f"Row {excel_row} ({scene_id}): PRIORITY must be {', '.join(PRIORITIES)}."
            )
        visual_action = _optional_text(row, columns, "visual_action", "AUTO").upper()
        if visual_action not in VISUAL_ACTIONS:
            raise TimingValidationError(
                f"Row {excel_row} ({scene_id}): VISUAL_ACTION must be {', '.join(VISUAL_ACTIONS)}."
            )
        accent_word = _optional_text(row, columns, "accent_word", "")
        sfx = _optional_text(row, columns, "sfx", "NONE").upper()
        lock_style = _parse_bool(
            row[columns["lock_style"]] if "lock_style" in columns else False,
            context=f"Row {excel_row} ({scene_id}) LOCK_STYLE",
        )
        entries.append(
            OverlayEntry(
                scene_id=scene_id,
                start_frame=start,
                end_frame=end,
                text=raw_text,
                semantic_type=semantic_type,
                priority=priority,
                accent_word=accent_word,
                visual_action=visual_action,
                sfx=sfx,
                lock_style=lock_style,
            )
        )

    entries.sort(key=lambda item: (item.start_frame, item.end_frame, item.scene_id))
    if not entries:
        raise TimingValidationError("No usable overlay rows were found.")

    seen: set[str] = set()
    for item in entries:
        if item.scene_id in seen:
            warnings.append(f"Duplicate SCENE_ID: {item.scene_id}.")
        seen.add(item.scene_id)
    for first, second in zip(entries, entries[1:]):
        if first.end_frame > second.start_frame:
            warnings.append(
                f"{first.scene_id} overlaps {second.scene_id}; both may appear together."
            )
    return entries, warnings


def entries_to_table(entries: Iterable[OverlayEntry]) -> pd.DataFrame:
    rows = []
    for item in entries:
        rows.append(
            {
                "ENABLED": item.enabled,
                "SCENE_ID": item.scene_id,
                "START_TIME_30FPS": frame_to_timecode(item.start_frame),
                "END_TIME_30FPS": frame_to_timecode(item.end_frame),
                "ON_SCREEN_TEXT": item.text,
                "TYPE": item.semantic_type,
                "PRIORITY": item.priority,
                "EMPHASIS_WORD": item.accent_word,
                "VISUAL_ACTION": item.visual_action,
                "SFX": item.sfx,
                "POSITION": item.resolved_position,
                "FONT_SIZE_PX": item.font_size_px,
                "ANIMATION": item.animation,
                "EFFECT": item.effect,
                "SAFETY_SCORE": round(item.safety_score, 1),
                "MOTION_SCORE": round(item.motion_score, 3),
                "DETAIL_SCORE": round(item.detail_score, 3),
                "SECTION_CUE": item.section_cue,
                "ACTION_START_30FPS": frame_to_timecode(item.action_start_frame)
                if item.action_start_frame is not None
                else "",
                "ACTION_END_30FPS": frame_to_timecode(item.action_end_frame)
                if item.action_end_frame is not None
                else "",
                "CONFIDENCE": item.confidence,
                "READING_CPS": item.reading_cps,
                "READING_STATUS": item.reading_status,
                "NOTE": item.note,
                "LOCK_STYLE": item.lock_style,
            }
        )
    return pd.DataFrame(rows)
