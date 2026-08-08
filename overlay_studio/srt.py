from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import OverlayEntry


SRT_TIMESTAMP = re.compile(
    r"^(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,3}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$"
)


class SRTValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CaptionInterval:
    start_frame: int
    end_frame: int
    text: str
    line_count: int
    reading_cps: float


def _timestamp_ms(parts: tuple[str, str, str, str]) -> int:
    hours, minutes, seconds, milliseconds = map(int, parts)
    if minutes > 59 or seconds > 59:
        raise SRTValidationError("SRT minutes and seconds must be between 00 and 59.")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _frame_at_or_after(milliseconds: int, fps: int) -> int:
    return math.ceil(milliseconds * fps / 1000.0)


def parse_srt_text(content: str, *, fps: int = 30) -> list[CaptionInterval]:
    normalized = content.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block for block in re.split(r"\n\s*\n", normalized.strip()) if block.strip()]
    captions: list[CaptionInterval] = []
    for block_number, block in enumerate(blocks, start=1):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        if lines and lines[0].isdigit():
            lines = lines[1:]
        if len(lines) < 2:
            raise SRTValidationError(f"SRT block {block_number} is incomplete.")
        match = SRT_TIMESTAMP.match(lines[0])
        if not match:
            raise SRTValidationError(
                f"SRT block {block_number} has an invalid timestamp line: {lines[0]}"
            )
        start_ms = _timestamp_ms(match.groups()[0:4])
        end_ms = _timestamp_ms(match.groups()[4:8])
        if end_ms <= start_ms:
            raise SRTValidationError(f"SRT block {block_number} must end after it starts.")
        text_lines = lines[1:]
        text = "\n".join(text_lines)
        duration = (end_ms - start_ms) / 1000.0
        visible_characters = len(" ".join(text_lines))
        captions.append(
            CaptionInterval(
                start_frame=_frame_at_or_after(start_ms, fps),
                end_frame=_frame_at_or_after(end_ms, fps),
                text=text,
                line_count=len(text_lines),
                reading_cps=round(visible_characters / max(0.001, duration), 1),
            )
        )
    if not captions:
        raise SRTValidationError("No usable subtitle intervals were found.")
    return sorted(captions, key=lambda item: (item.start_frame, item.end_frame))


def load_srt(path: str | Path, *, fps: int = 30) -> list[CaptionInterval]:
    source = Path(path).expanduser()
    if not source.is_file() or source.suffix.lower() != ".srt":
        raise SRTValidationError("Choose a valid SRT subtitle file.")
    try:
        content = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        content = source.read_text(encoding="cp1252")
    return parse_srt_text(content, fps=fps)


def active_captions(
    captions: Iterable[CaptionInterval], start_frame: int, end_frame: int
) -> list[CaptionInterval]:
    return [
        caption
        for caption in captions
        if caption.start_frame < end_frame and caption.end_frame > start_frame
    ]


def reading_load_warnings(
    entries: Iterable[OverlayEntry], captions: Iterable[CaptionInterval]
) -> list[str]:
    caption_list = list(captions)
    warnings: list[str] = []
    for entry in entries:
        overlapping = active_captions(
            caption_list, entry.start_frame, entry.end_frame
        )
        if overlapping and (
            max(caption.reading_cps for caption in overlapping) >= 18.0
            or max(caption.line_count for caption in overlapping) >= 2
        ):
            warnings.append(
                f"{entry.scene_id}: overlay overlaps rapid or two-line subtitles; keep the text concise."
            )
    return warnings


def long_caption_gaps(
    captions: Iterable[CaptionInterval], *, minimum_seconds: float = 2.5, fps: int = 30
) -> list[tuple[int, int]]:
    ordered = sorted(captions, key=lambda item: (item.start_frame, item.end_frame))
    gaps: list[tuple[int, int]] = []
    current_end = ordered[0].end_frame if ordered else 0
    minimum_frames = round(minimum_seconds * fps)
    for caption in ordered[1:]:
        if caption.start_frame - current_end >= minimum_frames:
            gaps.append((current_end, caption.start_frame))
        current_end = max(current_end, caption.end_frame)
    return gaps
