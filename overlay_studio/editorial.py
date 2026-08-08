from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from .models import OverlayEntry, ProjectSettings
from .scene_analysis import cuts_inside
from .srt import CaptionInterval, active_captions


DENSITY_STRONG_EVENTS_PER_MINUTE = {"CALM": 2, "STANDARD": 3, "ENERGETIC": 4}
STRONG_ACTIONS = {"PUNCH_IN", "FREEZE"}


def _caption_is_busy(entry: OverlayEntry, captions: list[CaptionInterval]) -> bool:
    overlapping = active_captions(captions, entry.start_frame, entry.end_frame)
    return bool(overlapping) and (
        max(item.reading_cps for item in overlapping) >= 18.0
        or max(item.line_count for item in overlapping) >= 2
    )


def _semantic_style(entry: OverlayEntry) -> None:
    if entry.lock_style:
        return
    if entry.semantic_type == "QUESTION":
        entry.animation = "SOFT_SCALE"
    elif entry.semantic_type in {"WARNING", "UNCERTAINTY"}:
        entry.animation = "FADE_ONLY"
        entry.effect = "PROTECTED_PLATE"
    elif entry.semantic_type == "TAKEAWAY":
        entry.animation = "SOFT_SCALE"
    elif entry.semantic_type == "SECTION":
        entry.animation = "FADE_ONLY"
        entry.effect = "CLEAN_SHADOW"


def plan_editorial_actions(
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    *,
    captions: Iterable[CaptionInterval] = (),
) -> list[OverlayEntry]:
    planned = sorted(entries, key=lambda item: (item.start_frame, item.end_frame))
    caption_list = list(captions)
    preset = settings.density_preset.upper()
    maximum_per_minute = DENSITY_STRONG_EVENTS_PER_MINUTE.get(preset, 3)
    recent_strong_frames: list[int] = []
    cooldown_frames = settings.fps * (12 if preset == "CALM" else 8 if preset == "STANDARD" else 5)

    for entry in planned:
        _semantic_style(entry)
        entry.action_start_frame = entry.start_frame
        entry.action_end_frame = entry.end_frame
        if entry.visual_action not in {"AUTO", "NONE"}:
            requested = entry.visual_action
        elif entry.priority == "HIGH" and entry.semantic_type in {
            "QUESTION", "NUMBER", "EVIDENCE", "TAKEAWAY"
        }:
            requested = "PUNCH_IN"
        elif entry.semantic_type in {"QUESTION", "WARNING", "UNCERTAINTY", "TAKEAWAY"}:
            requested = "DIM_FOCUS"
        else:
            requested = "NONE"

        if requested in STRONG_ACTIONS:
            minute_start = max(0, entry.start_frame - settings.fps * 60)
            recent_strong_frames = [frame for frame in recent_strong_frames if frame >= minute_start]
            too_close = bool(recent_strong_frames) and entry.start_frame - recent_strong_frames[-1] < cooldown_frames
            too_many = len(recent_strong_frames) >= maximum_per_minute
            unsafe_motion = entry.confidence == "REVIEW" or _caption_is_busy(entry, caption_list)
            if too_close or too_many or unsafe_motion:
                requested = "NONE"

        internal_cuts = cuts_inside(settings.scene_cut_frames, entry.start_frame, entry.end_frame)
        if requested in STRONG_ACTIONS and internal_cuts:
            entry.action_end_frame = internal_cuts[0]
        if entry.action_end_frame - entry.action_start_frame < settings.fps // 2:
            requested = "NONE"
        entry.visual_action = requested
        if requested in STRONG_ACTIONS:
            recent_strong_frames.append(entry.start_frame)
    return planned


def editorial_report(entries: Iterable[OverlayEntry], duration_seconds: float) -> dict[str, object]:
    items = [entry for entry in entries if entry.enabled]
    minutes = max(duration_seconds / 60.0, 1 / 60.0)
    return {
        "overlay_events": len(items),
        "events_per_minute": round(len(items) / minutes, 2),
        "semantic_types": dict(Counter(entry.semantic_type for entry in items)),
        "visual_actions": dict(Counter(entry.visual_action for entry in items)),
        "strong_actions": sum(entry.visual_action in STRONG_ACTIONS for entry in items),
    }


def write_editorial_report(path: str | Path, entries: Iterable[OverlayEntry], duration_seconds: float) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(editorial_report(entries, duration_seconds), indent=2), encoding="utf-8")
    return destination
