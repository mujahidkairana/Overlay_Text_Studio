from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
import os
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable

import numpy as np
from PIL import Image, ImageFilter

from .layout import balanced_wrap, calculate_font_size
from .models import OverlayEntry, POSITIONS

try:
    import cv2  # type: ignore
except ImportError:  # pragma: no cover - optional fallback
    cv2 = None


ProgressCallback = Callable[[float, str], None]
_FACE_DETECT_LOCK = Lock()


@dataclass(slots=True)
class RegionScore:
    position: str
    safety: float
    risk: float
    detail: float
    edge: float
    motion: float
    contrast: float
    face_penalty: float


def candidate_boxes(
    width: int,
    height: int,
    *,
    box_width_fraction: float = 0.48,
    caption_safe_percent: float = 35.0,
) -> dict[str, tuple[int, int, int, int]]:
    """Return top regions that never enter the caption-reserved lower band."""
    y0 = round(height * 0.045)
    caption_y0 = round(height * (1.0 - caption_safe_percent / 100.0))
    y1 = min(round(height * 0.30), caption_y0)
    box_width = round(width * max(0.40, min(0.86, box_width_fraction)))
    margin = round(width * 0.04)
    return {
        "TOP_LEFT": (margin, y0, margin + box_width, y1),
        "TOP_CENTER": ((width - box_width) // 2, y0, (width + box_width) // 2, y1),
        "TOP_RIGHT": (width - margin - box_width, y0, width - margin, y1),
    }


def _normalized_map(array: np.ndarray, upper_percentile: float = 97.0) -> np.ndarray:
    high = float(np.percentile(array, upper_percentile))
    if high <= 1e-6:
        return np.zeros_like(array, dtype=np.float32)
    return np.clip(array / high, 0.0, 1.0).astype(np.float32)


@lru_cache(maxsize=512)
def _load_gray(path_string: str) -> np.ndarray:
    image = Image.open(path_string).convert("L")
    return np.asarray(image, dtype=np.float32) / 255.0


@lru_cache(maxsize=512)
def _frame_maps(path_string: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    image = Image.open(path_string).convert("L")
    gray = np.asarray(image, dtype=np.float32) / 255.0
    blurred = np.asarray(image.filter(ImageFilter.GaussianBlur(radius=5)), dtype=np.float32) / 255.0
    local_detail = _normalized_map(np.abs(gray - blurred), 98.0)
    grad_y, grad_x = np.gradient(gray)
    edge = _normalized_map(np.hypot(grad_x, grad_y), 98.0)
    attention = np.clip(local_detail * 0.58 + edge * 0.42, 0.0, 1.0)
    return gray, edge, attention


@lru_cache(maxsize=1)
def _face_detector():
    if cv2 is None:
        return None
    cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    return cv2.CascadeClassifier(str(cascade_path))


@lru_cache(maxsize=512)
def _face_boxes(path_string: str) -> tuple[tuple[int, int, int, int], ...]:
    detector = _face_detector()
    if detector is None:
        return ()
    frame = cv2.imread(path_string, cv2.IMREAD_GRAYSCALE)
    if frame is None:
        return ()
    # OpenCV's shared cascade instance is not guaranteed to be thread-safe.
    with _FACE_DETECT_LOCK:
        faces = detector.detectMultiScale(
            frame, scaleFactor=1.12, minNeighbors=5, minSize=(24, 24)
        )
    return tuple((int(x), int(y), int(x + w), int(y + h)) for x, y, w, h in faces)


def text_box_width_fraction(entry: OverlayEntry, aspect_ratio: float) -> float:
    """Estimate the rendered text width including outline/shadow breathing room."""
    wrapped = entry.wrapped_text or balanced_wrap(entry.text)
    font_size = entry.font_size_px or calculate_font_size(wrapped, 2160)
    font_height_ratio = font_size / 2160.0
    longest = max(len(line) for line in wrapped.split("\\N"))
    estimated = longest * font_height_ratio * 0.59 / max(0.5, aspect_ratio)
    return max(0.40, min(0.86, estimated + 0.07))


def _intersection_ratio(
    first: tuple[int, int, int, int], second: tuple[int, int, int, int]
) -> float:
    x0 = max(first[0], second[0])
    y0 = max(first[1], second[1])
    x1 = min(first[2], second[2])
    y1 = min(first[3], second[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    second_area = max(1, (second[2] - second[0]) * (second[3] - second[1]))
    return ((x1 - x0) * (y1 - y0)) / second_area


def _sample_indices(entry: OverlayEntry, sample_fps: float, total_frames: int) -> list[int]:
    start_seconds = entry.start_frame / 30.0
    end_seconds = entry.end_frame / 30.0
    start_index = max(0, int(np.floor(start_seconds * sample_fps)))
    end_index = min(total_frames - 1, int(np.ceil(end_seconds * sample_fps)))
    if end_index < start_index:
        end_index = start_index
    available = list(range(start_index, end_index + 1))
    if len(available) <= 7:
        return available
    selected = np.linspace(start_index, end_index, num=7)
    return sorted({int(round(value)) for value in selected})


def score_regions_for_entry(
    entry: OverlayEntry,
    frames: list[Path],
    *,
    sample_fps: float,
) -> list[RegionScore]:
    if not frames:
        raise ValueError("No analysis frames are available.")
    indices = _sample_indices(entry, sample_fps, len(frames))
    first_gray = _load_gray(str(frames[indices[0]]))
    height, width = first_gray.shape
    boxes = candidate_boxes(
        width,
        height,
        box_width_fraction=text_box_width_fraction(entry, width / max(1, height)),
    )
    values: dict[str, dict[str, list[float]]] = {
        position: {name: [] for name in ("detail", "edge", "motion", "contrast", "face")}
        for position in POSITIONS
    }
    previous_gray: np.ndarray | None = None

    for index in indices:
        gray, edge_map, detail_map = _frame_maps(str(frames[index]))
        if previous_gray is None:
            previous_index = max(0, index - 1)
            previous_gray = _load_gray(str(frames[previous_index]))
        motion_map = np.abs(gray - previous_gray)
        faces = _face_boxes(str(frames[index]))

        for position, box in boxes.items():
            x0, y0, x1, y1 = box
            detail_roi = detail_map[y0:y1, x0:x1]
            edge_roi = edge_map[y0:y1, x0:x1]
            gray_roi = gray[y0:y1, x0:x1]
            motion_roi = motion_map[y0:y1, x0:x1]
            values[position]["detail"].append(float(np.mean(detail_roi)))
            values[position]["edge"].append(float(np.mean(edge_roi)))
            values[position]["motion"].append(float(np.mean(motion_roi)))
            values[position]["contrast"].append(float(np.std(gray_roi)))
            face_overlap = max((_intersection_ratio(box, face) for face in faces), default=0.0)
            values[position]["face"].append(face_overlap)
        previous_gray = gray

    results: list[RegionScore] = []
    for position in POSITIONS:
        metrics = values[position]
        detail = float(np.percentile(metrics["detail"], 80))
        edge = float(np.percentile(metrics["edge"], 80))
        motion = float(np.percentile(metrics["motion"], 80))
        contrast = float(np.percentile(metrics["contrast"], 80))
        face_penalty = min(1.0, float(max(metrics["face"])))
        # Fixed normalizers keep scores comparable between different overlays.
        risk = (
            min(1.0, detail / 0.34) * 0.33
            + min(1.0, edge / 0.31) * 0.24
            + min(1.0, motion / 0.12) * 0.20
            + min(1.0, contrast / 0.26) * 0.11
            + face_penalty * 0.32
        )
        risk = min(1.0, risk)
        results.append(
            RegionScore(
                position=position,
                safety=round((1.0 - risk) * 100.0, 2),
                risk=risk,
                detail=detail,
                edge=edge,
                motion=motion,
                contrast=contrast,
                face_penalty=face_penalty,
            )
        )
    return sorted(results, key=lambda item: item.safety, reverse=True)


def analyze_entries(
    entries: Iterable[OverlayEntry],
    frames: list[Path],
    *,
    sample_fps: float = 1.0,
    progress: ProgressCallback | None = None,
    max_workers: int | None = None,
) -> list[OverlayEntry]:
    planned = list(entries)
    enabled = [entry for entry in planned if entry.enabled]
    workers = max_workers or min(4, max(1, (os.cpu_count() or 2) // 2))
    workers = max(1, min(workers, len(enabled) or 1))
    scored: dict[int, list[RegionScore]] = {}
    if workers == 1:
        for index, entry in enumerate(enabled):
            scored[id(entry)] = score_regions_for_entry(
                entry, frames, sample_fps=sample_fps
            )
            if progress:
                progress(
                    (index + 1) / max(1, len(enabled)) * 0.85,
                    f"Analyzing {entry.scene_id}",
                )
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    score_regions_for_entry, entry, frames, sample_fps=sample_fps
                ): entry
                for entry in enabled
            }
            for completed, future in enumerate(as_completed(futures), start=1):
                entry = futures[future]
                scored[id(entry)] = future.result()
                if progress:
                    progress(
                        completed / max(1, len(enabled)) * 0.85,
                        f"Parallel analysis {completed}/{len(enabled)}",
                    )

    recent: list[str] = []
    active: list[OverlayEntry] = []
    total = max(1, len(planned))
    for index, entry in enumerate(planned):
        if not entry.enabled:
            continue
        active = [other for other in active if other.end_frame > entry.start_frame]
        scores = scored[id(entry)]
        if entry.position in POSITIONS:
            chosen = next(score for score in scores if score.position == entry.position)
        else:
            chosen = scores[0]
            used_positions = {other.resolved_position for other in active}
            if chosen.position in used_positions:
                alternative = next(
                    (score for score in scores if score.position not in used_positions),
                    None,
                )
                if alternative is not None:
                    chosen = alternative
            # Avoid visual monotony only when the alternative is almost equally safe.
            if len(recent) >= 3 and len(set(recent[-3:])) == 1 and chosen.position == recent[-1]:
                close_alternative = next(
                    (score for score in scores[1:] if chosen.safety - score.safety <= 7.0), None
                )
                if close_alternative:
                    chosen = close_alternative
        entry.resolved_position = chosen.position
        entry.safety_score = chosen.safety
        if chosen.safety >= 66:
            entry.confidence = "HIGH"
            entry.note = "Stable low-detail area"
        elif chosen.safety >= 50:
            entry.confidence = "MEDIUM"
            entry.note = "Readable with outline/shadow"
        else:
            entry.confidence = "REVIEW"
            entry.note = "Busy top area; stronger text protection applied"
        recent.append(chosen.position)
        active.append(entry)
        if progress:
            progress(0.85 + (index + 1) / total * 0.15, f"Placing {entry.scene_id}")
    return planned
