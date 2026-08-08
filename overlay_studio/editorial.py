from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable

from .models import OverlayEntry, ProjectSettings
from .scene_analysis import cuts_inside
from .srt import CaptionInterval, active_captions, long_caption_gaps


DENSITY_STRONG_EVENTS_PER_MINUTE = {"CALM": 2, "STANDARD": 3, "ENERGETIC": 4}
TARGET_EVENTS_PER_MINUTE = {"CALM": 4, "STANDARD": 6, "ENERGETIC": 7}
STRONG_ACTIONS = {"PUNCH_IN", "FREEZE"}
MEDIUM_ACTIONS = {"DIM_FOCUS"}


def _caption_is_busy(entry: OverlayEntry, captions: list[CaptionInterval]) -> bool:
    overlapping = active_captions(captions, entry.start_frame, entry.end_frame)
    return bool(overlapping) and (
        max(item.reading_cps for item in overlapping) >= 18.0
        or max(item.line_count for item in overlapping) >= 2
    )


def _semantic_style(entry: OverlayEntry) -> None:
    if entry.lock_style:
        return
    if entry.section_cue or entry.semantic_type == "SECTION":
        entry.animation = "FADE_ONLY"
        entry.effect = "CLEAN_SHADOW"
    elif entry.semantic_type == "QUESTION":
        entry.animation = "SOFT_SCALE"
    elif entry.semantic_type == "NUMBER":
        entry.animation = "SOFT_SCALE"
        entry.effect = "STRONG_OUTLINE"
    elif entry.semantic_type == "EVIDENCE":
        entry.animation = "EASE_SIDE"
        entry.effect = "CLEAN_SHADOW"
    elif entry.semantic_type == "COMPARISON":
        entry.animation = "EASE_SIDE"
        entry.effect = "PROTECTED_PLATE"
    elif entry.semantic_type in {"WARNING", "UNCERTAINTY"}:
        entry.animation = "FADE_ONLY"
        entry.effect = "PROTECTED_PLATE"
    elif entry.semantic_type == "TAKEAWAY":
        entry.animation = "SOFT_SCALE"
        entry.effect = "STRONG_OUTLINE"


def apply_srt_gap_section_cues(
    entries: Iterable[OverlayEntry], captions: Iterable[CaptionInterval]
) -> None:
    ordered = sorted(entries, key=lambda item: item.start_frame)
    for gap_start, gap_end in long_caption_gaps(captions, minimum_seconds=3.5):
        candidate = next(
            (
                entry for entry in ordered
                if gap_start <= entry.start_frame <= gap_end + 60
                and entry.semantic_type != "SECTION"
            ),
            None,
        )
        if candidate is not None:
            candidate.section_cue = True


def plan_editorial_actions(
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    *,
    captions: Iterable[CaptionInterval] = (),
) -> list[OverlayEntry]:
    planned = sorted(entries, key=lambda item: (item.start_frame, item.end_frame))
    caption_list = list(captions)
    apply_srt_gap_section_cues(planned, caption_list)
    preset = settings.density_preset.upper()
    maximum_per_minute = DENSITY_STRONG_EVENTS_PER_MINUTE.get(preset, 3)
    recent_strong_frames: list[int] = []
    freeze_count = 0
    sfx_count = 0
    selected_action_frames: list[int] = []
    last_medium_frame: int | None = None
    last_sfx_frame: int | None = None
    total_frames = max((entry.end_frame for entry in planned), default=0)
    freeze_cap = max(1, math.ceil(total_frames / settings.fps / 600.0 * 4))
    sfx_per_ten_minutes = {"CALM": 8, "STANDARD": 10, "ENERGETIC": 12}.get(preset, 10)
    sfx_cap = max(1, math.ceil(total_frames / settings.fps / 600.0 * sfx_per_ten_minutes))
    cooldown_frames = settings.fps * (12 if preset == "CALM" else 8 if preset == "STANDARD" else 5)
    medium_cooldown_frames = settings.fps * (
        6 if preset == "CALM" else 5 if preset == "STANDARD" else 4
    )
    sfx_cooldown_frames = settings.fps * (
        10 if preset == "CALM" else 8 if preset == "STANDARD" else 6
    )

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

        if (
            not settings.scene_analysis_available
            and requested in STRONG_ACTIONS | MEDIUM_ACTIONS
        ):
            requested = "NONE"
        if requested == "FREEZE" and entry.priority != "HIGH":
            requested = "NONE"

        unsafe_motion = (
            entry.confidence == "REVIEW"
            or entry.motion_score >= 0.10
            or entry.detail_score >= 0.34
            or _caption_is_busy(entry, caption_list)
        )
        if requested in STRONG_ACTIONS:
            minute_start = max(0, entry.start_frame - settings.fps * 60)
            recent_strong_frames = [frame for frame in recent_strong_frames if frame >= minute_start]
            too_close = bool(recent_strong_frames) and entry.start_frame - recent_strong_frames[-1] < cooldown_frames
            too_many = len(recent_strong_frames) >= maximum_per_minute
            recent_overlay_count = sum(
                other.start_frame <= entry.start_frame
                and other.start_frame >= minute_start
                for other in planned
            )
            density_full = (
                recent_overlay_count
                + sum(frame >= minute_start for frame in selected_action_frames)
                >= TARGET_EVENTS_PER_MINUTE.get(preset, 6)
            )
            if too_close or too_many or unsafe_motion or density_full:
                requested = "NONE"
        elif requested in MEDIUM_ACTIONS:
            too_close = (
                last_medium_frame is not None
                and entry.start_frame - last_medium_frame < medium_cooldown_frames
            )
            if too_close or unsafe_motion:
                requested = "NONE"

        internal_cuts = cuts_inside(settings.scene_cut_frames, entry.start_frame, entry.end_frame)
        if requested in STRONG_ACTIONS | MEDIUM_ACTIONS and internal_cuts:
            entry.action_end_frame = internal_cuts[0]
        if requested == "FREEZE":
            if freeze_count >= freeze_cap:
                requested = "NONE"
            else:
                entry.action_end_frame = min(
                    entry.action_end_frame,
                    entry.action_start_frame + round(settings.fps * 0.6),
                )
        if entry.sfx not in {"", "NONE", "AUTO"}:
            too_close = (
                last_sfx_frame is not None
                and entry.start_frame - last_sfx_frame < sfx_cooldown_frames
            )
            if sfx_count >= sfx_cap or too_close:
                entry.sfx = "NONE"
            else:
                sfx_count += 1
                last_sfx_frame = entry.start_frame
        if entry.action_end_frame - entry.action_start_frame < settings.fps // 2:
            requested = "NONE"
        entry.visual_action = requested
        if requested in STRONG_ACTIONS:
            recent_strong_frames.append(entry.start_frame)
        if requested in MEDIUM_ACTIONS:
            last_medium_frame = entry.start_frame
        if requested != "NONE":
            selected_action_frames.append(entry.start_frame)
        if requested == "FREEZE":
            freeze_count += 1
    return planned


def editorial_report(entries: Iterable[OverlayEntry], duration_seconds: float) -> dict[str, object]:
    items = [entry for entry in entries if entry.enabled]
    minutes = max(duration_seconds / 60.0, 1 / 60.0)
    position_counts = Counter(entry.resolved_position for entry in items)
    animation_counts = Counter(entry.animation for entry in items)
    action_counts = Counter(entry.visual_action for entry in items)
    def percentages(counts: Counter[str]) -> dict[str, float]:
        return {key: round(value / max(1, len(items)) * 100, 1) for key, value in counts.items()}

    ordered = sorted(items, key=lambda entry: entry.start_frame)
    longest_repeat = 0
    current_repeat = 0
    previous_pattern = None
    for entry in ordered:
        pattern = (entry.resolved_position, entry.animation, entry.visual_action)
        current_repeat = current_repeat + 1 if pattern == previous_pattern else 1
        longest_repeat = max(longest_repeat, current_repeat)
        previous_pattern = pattern
    gaps = [
        round((second.start_frame - first.end_frame) / 30.0, 1)
        for first, second in zip(ordered, ordered[1:])
        if second.start_frame > first.end_frame
    ]
    maximum_window_events = 0
    for entry in ordered:
        window_start = entry.start_frame - 30 * 60
        window_overlays = sum(window_start <= item.start_frame <= entry.start_frame for item in ordered)
        window_actions = sum(
            window_start <= item.start_frame <= entry.start_frame and item.visual_action != "NONE"
            for item in ordered
        )
        maximum_window_events = max(maximum_window_events, window_overlays + window_actions)
    return {
        "overlay_events": len(items),
        "events_per_minute": round(len(items) / minutes, 2),
        "target_events_per_minute": "4-7",
        "maximum_rolling_minute_events": maximum_window_events,
        "density_status": "OVER_TARGET" if maximum_window_events > 7 else "IN_TARGET",
        "semantic_types": dict(Counter(entry.semantic_type for entry in items)),
        "position_usage_percent": percentages(position_counts),
        "animation_usage_percent": percentages(animation_counts),
        "visual_action_usage_percent": percentages(action_counts),
        "strong_actions": sum(entry.visual_action in STRONG_ACTIONS for entry in items),
        "punch_in_count": action_counts.get("PUNCH_IN", 0),
        "freeze_count": action_counts.get("FREEZE", 0),
        "section_cue_count": sum(entry.section_cue for entry in items),
        "longest_repeated_pattern": longest_repeat,
        "longest_quiet_interval_seconds": max(gaps, default=0.0),
    }


def write_editorial_report(path: str | Path, entries: Iterable[OverlayEntry], duration_seconds: float) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(editorial_report(entries, duration_seconds), indent=2), encoding="utf-8")
    return destination
