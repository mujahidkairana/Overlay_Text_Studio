from __future__ import annotations

import random
import re
from collections.abc import Iterable

from .models import ANIMATIONS, EFFECTS, OverlayEntry, ProjectSettings


STOP_WORDS = {
    "THE",
    "AND",
    "THAT",
    "THIS",
    "WITH",
    "FROM",
    "WERE",
    "WHAT",
    "WHEN",
    "HAVE",
    "COULD",
    "WOULD",
    "THEIR",
}


class ShuffleBag:
    """Randomized deck that avoids consecutive repeats and fixed-order cycles."""

    def __init__(self, values: Iterable[str], rng: random.Random):
        self.values = list(values)
        self.rng = rng
        self.bag: list[str] = []
        self.previous = ""

    def next(self, allowed: Iterable[str] | None = None) -> str:
        allowed_set = set(allowed or self.values)
        available = [value for value in self.bag if value in allowed_set]
        if not available:
            self.bag = list(self.values)
            self.rng.shuffle(self.bag)
            available = [value for value in self.bag if value in allowed_set]
        if not available:
            raise ValueError("ShuffleBag received no allowed values")
        chosen = available[-1]
        if len(available) > 1 and chosen == self.previous:
            chosen = available[-2]
        self.bag.remove(chosen)
        self.previous = chosen
        return chosen


def balanced_wrap(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    explicit = [part.strip() for part in re.split(r"\\N|\n", normalized) if part.strip()]
    if len(explicit) > 2:
        raise ValueError(
            f"Overlay text uses {len(explicit)} explicit lines; the maximum is 2."
        )
    if len(explicit) == 2:
        return "\\N".join(explicit)
    clean = re.sub(r"\s+", " ", normalized.strip())
    words = clean.split()
    if len(clean) <= 30 or len(words) <= 3:
        return clean
    best_index = 1
    best_cost = float("inf")
    for index in range(1, len(words)):
        left = " ".join(words[:index])
        right = " ".join(words[index:])
        longest = max(len(left), len(right))
        imbalance = abs(len(left) - len(right))
        cost = longest * 1.8 + imbalance
        if cost < best_cost:
            best_cost = cost
            best_index = index
    return " ".join(words[:best_index]) + "\\N" + " ".join(words[best_index:])


def calculate_font_size(wrapped_text: str, output_height: int) -> int:
    lines = wrapped_text.split("\\N")
    longest = max(len(line) for line in lines)
    word_count = len(wrapped_text.replace("\\N", " ").split())
    if len(lines) == 1 and longest <= 18 and word_count <= 3:
        ratio = 0.071
    elif longest <= 27 and word_count <= 6:
        ratio = 0.061
    elif longest <= 34:
        ratio = 0.052
    elif longest <= 42:
        ratio = 0.046
    else:
        ratio = 0.041
    # Cap estimated line width to about 86% of a 16:9 frame. Very long text can
    # shrink slightly below the normal floor, but is still flagged for review.
    width_cap_ratio = 1.52 / max(1.0, longest * 0.59)
    ratio = min(ratio, width_cap_ratio)
    minimum = round(output_height * 0.032)
    maximum = round(output_height * 0.074)
    return max(minimum, min(maximum, round(output_height * ratio)))


def choose_accent_word(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", text)
    candidates = [word for word in words if len(word) >= 4 and word.upper() not in STOP_WORDS]
    if not candidates:
        return ""
    return max(candidates, key=lambda word: (len(word), words.index(word)))


def prepare_text_layout(
    entries: Iterable[OverlayEntry], settings: ProjectSettings
) -> list[OverlayEntry]:
    """Resolve wrapping and font size before video safety analysis."""
    prepared = list(entries)
    for entry in prepared:
        if not entry.enabled:
            continue
        entry.wrapped_text = balanced_wrap(entry.text)
        if not entry.font_size_px or not entry.lock_style:
            entry.font_size_px = calculate_font_size(entry.wrapped_text, settings.output_height)
    return prepared


def compatible_animations(position: str, duration_seconds: float) -> tuple[str, ...]:
    if duration_seconds < 0.9:
        return ("FADE_ONLY",)
    if duration_seconds < 1.4:
        return ("EASE_UP", "SOFT_SCALE", "FADE_ONLY")
    if position == "TOP_CENTER":
        return ("EASE_UP", "SOFT_SCALE", "FADE_ONLY")
    return ("EASE_UP", "EASE_SIDE", "SOFT_SCALE", "FADE_ONLY")


def plan_layout_and_styles(
    entries: Iterable[OverlayEntry],
    settings: ProjectSettings,
    *,
    reroll: int = 0,
) -> list[OverlayEntry]:
    planned = list(entries)
    rng = random.Random(settings.random_seed + reroll * 104729)
    animation_bag = ShuffleBag(ANIMATIONS, rng)

    prepare_text_layout(planned, settings)

    for entry in planned:
        if not entry.enabled:
            continue
        if entry.animation == "AUTO" or not entry.lock_style:
            entry.animation = animation_bag.next(
                compatible_animations(entry.resolved_position, entry.duration_seconds)
            )
        visible_characters = len(re.sub(r"\s+", "", entry.text))
        entry.reading_cps = round(
            visible_characters / max(0.1, entry.duration_seconds), 1
        )
        if entry.reading_cps <= 17:
            entry.reading_status = "COMFORTABLE"
        elif entry.reading_cps <= 22:
            entry.reading_status = "FAST"
        else:
            entry.reading_status = "TOO_FAST"

        if entry.effect == "AUTO" or not entry.lock_style:
            # YouTube Pro keeps one coherent visual language. Accent is used
            # sparingly; glow is deliberately excluded because it softens edges.
            entry.effect = "ACCENT_WORD" if rng.random() < 0.20 else "CLEAN_SHADOW"
        if entry.confidence == "REVIEW" or entry.reading_status == "TOO_FAST":
            entry.effect = "PROTECTED_PLATE"
        elif entry.reading_status == "FAST" and entry.effect == "ACCENT_WORD":
            entry.effect = "CLEAN_SHADOW"
        entry.accent_word = choose_accent_word(entry.text) if entry.effect == "ACCENT_WORD" else ""
        if entry.reading_status != "COMFORTABLE":
            entry.note = (
                entry.note
                + f"; {entry.reading_status.lower().replace('_', ' ')} reading "
                + f"({entry.reading_cps:g} chars/sec)"
            ).strip("; ")
        if len(entry.wrapped_text.replace("\\N", "")) > 88:
            entry.note = (entry.note + "; shorten text if possible").strip("; ")
    return planned
