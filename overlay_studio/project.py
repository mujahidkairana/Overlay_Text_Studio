from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from .models import OverlayEntry, ProjectSettings, VideoInfo
from .timing import entries_to_table, normalize_overlay_text


def save_project(
    project_dir: str | Path,
    *,
    video: VideoInfo,
    timing_path: str,
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    srt_path: str = "",
) -> dict[str, Path]:
    destination = Path(project_dir)
    destination.mkdir(parents=True, exist_ok=True)
    entry_list = list(entries)
    project_json = destination / "project.json"
    plan_csv = destination / "resolved_overlay_plan.csv"
    plan_xlsx = destination / "resolved_overlay_plan.xlsx"
    qa_json = destination / "overlay_qa_report.json"

    payload = {
        "video": {
            "path": video.path,
            "width": video.width,
            "height": video.height,
            "duration_seconds": video.duration_seconds,
            "source_fps": video.source_fps,
            "has_audio": video.has_audio,
            "codec_name": video.codec_name,
        },
        "timing_path": str(Path(timing_path).resolve()),
        "srt_path": str(Path(srt_path).resolve()) if srt_path else "",
        "settings": settings.to_dict(),
        "entries": [entry.to_dict() for entry in entry_list],
    }
    project_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    table = entries_to_table(entry_list)
    table.to_csv(plan_csv, index=False, encoding="utf-8-sig")
    table.to_excel(plan_xlsx, index=False)
    qa = {
        "total_overlays": len(entry_list),
        "enabled_overlays": sum(1 for entry in entry_list if entry.enabled),
        "high_confidence": sum(1 for entry in entry_list if entry.confidence == "HIGH"),
        "medium_confidence": sum(1 for entry in entry_list if entry.confidence == "MEDIUM"),
        "review_required": [
            {
                "scene_id": entry.scene_id,
                "time": f"{entry.start_frame}-{entry.end_frame}",
                "reason": entry.note,
            }
            for entry in entry_list
            if entry.confidence == "REVIEW"
        ],
        "long_text": [entry.scene_id for entry in entry_list if len(entry.text) > 88],
    }
    qa_json.write_text(json.dumps(qa, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"project": project_json, "csv": plan_csv, "xlsx": plan_xlsx, "qa": qa_json}


def load_project(path: str | Path) -> tuple[VideoInfo, str, list[OverlayEntry], ProjectSettings]:
    video, timing_path, _, entries, settings = load_project_data(path)
    return video, timing_path, entries, settings


def load_project_data(
    path: str | Path,
) -> tuple[VideoInfo, str, str, list[OverlayEntry], ProjectSettings]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    video = VideoInfo(**payload["video"])
    settings = ProjectSettings.from_dict(payload["settings"])
    entries = [OverlayEntry.from_dict(value) for value in payload["entries"]]
    return video, payload.get("timing_path", ""), payload.get("srt_path", ""), entries, settings


def apply_edited_table(entries: list[OverlayEntry], table: pd.DataFrame) -> list[OverlayEntry]:
    by_id = {entry.scene_id: entry for entry in entries}
    for _, row in table.iterrows():
        scene_id = str(row.get("SCENE_ID", "")).strip()
        entry = by_id.get(scene_id)
        if not entry:
            continue
        entry.enabled = bool(row.get("ENABLED", True))
        entry.text = normalize_overlay_text(
            row.get("ON_SCREEN_TEXT", entry.text), context=f"{scene_id} text"
        )
        position = str(row.get("POSITION", entry.resolved_position)).strip().upper()
        if position in {"TOP_LEFT", "TOP_CENTER", "TOP_RIGHT"}:
            entry.resolved_position = position
            entry.position = position
        try:
            entry.font_size_px = int(row.get("FONT_SIZE_PX", entry.font_size_px))
        except (TypeError, ValueError):
            pass
        animation = str(row.get("ANIMATION", entry.animation)).strip().upper()
        effect = str(row.get("EFFECT", entry.effect)).strip().upper()
        if animation:
            entry.animation = animation
        if effect:
            entry.effect = effect
        entry.semantic_type = str(row.get("TYPE", entry.semantic_type)).strip().upper()
        entry.priority = str(row.get("PRIORITY", entry.priority)).strip().upper()
        entry.accent_word = str(row.get("EMPHASIS_WORD", entry.accent_word)).strip()
        entry.visual_action = str(
            row.get("VISUAL_ACTION", entry.visual_action)
        ).strip().upper()
        entry.sfx = str(row.get("SFX", entry.sfx)).strip().upper()
        entry.lock_style = str(row.get("LOCK_STYLE", entry.lock_style)).strip().upper() in {
            "TRUE", "YES", "Y", "1"
        }
    return entries
