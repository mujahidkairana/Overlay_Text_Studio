from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


POSITIONS = ("TOP_LEFT", "TOP_CENTER", "TOP_RIGHT")
ANIMATIONS = (
    "EASE_UP",
    "EASE_SIDE",
    "SOFT_SCALE",
    "FOCUS_IN",
    "FADE_ONLY",
)
EFFECTS = ("CLEAN_SHADOW", "ACCENT_WORD", "STRONG_OUTLINE")


@dataclass(slots=True)
class VideoInfo:
    path: str
    width: int
    height: int
    duration_seconds: float
    source_fps: float
    has_audio: bool
    codec_name: str = ""

    @property
    def duration_frames_30(self) -> int:
        return max(0, round(self.duration_seconds * 30))


@dataclass(slots=True)
class OverlayEntry:
    scene_id: str
    start_frame: int
    end_frame: int
    text: str
    enabled: bool = True
    position: str = "AUTO"
    resolved_position: str = "TOP_LEFT"
    font_size_px: int = 0
    wrapped_text: str = ""
    animation: str = "AUTO"
    effect: str = "AUTO"
    safety_score: float = 0.0
    confidence: str = "NOT_ANALYZED"
    note: str = ""
    lock_style: bool = False
    accent_word: str = ""

    @property
    def duration_frames(self) -> int:
        return self.end_frame - self.start_frame

    @property
    def duration_seconds(self) -> float:
        return self.duration_frames / 30.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "OverlayEntry":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})


@dataclass(slots=True)
class ProjectSettings:
    project_name: str = "Overlay_Project"
    fps: int = 30
    output_width: int = 3840
    output_height: int = 2160
    analysis_fps: float = 0.5
    random_seed: int = 20260807
    font_name: str = "Segoe UI Semibold"
    font_file: str = ""
    accent_color: str = "#FFD84D"
    text_color: str = "#FFFFFF"
    safe_top_percent: float = 5.5
    safe_side_percent: float = 4.5
    caption_safe_percent: float = 35.0
    chunk_target_seconds: int = 60
    encoder: str = "libx264"
    x264_preset: str = "veryfast"
    crf: int = 18
    end_time_is_exclusive: bool = True
    keep_analysis_frames: bool = True
    style_preset: str = "YOUTUBE_PRO"
    parallel_analysis_workers: int = 4

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProjectSettings":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value[key] for key in allowed if key in value})

    def project_dir(self, base: str | Path) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.project_name)
        return Path(base).expanduser().resolve() / safe
