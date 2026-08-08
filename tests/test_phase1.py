from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from overlay_studio.models import OverlayEntry, ProjectSettings, VideoInfo
from overlay_studio.layout import plan_layout_and_styles
from overlay_studio.project import load_project, load_project_data, save_project
from overlay_studio.srt import (
    SRTValidationError,
    active_captions,
    long_caption_gaps,
    parse_srt_text,
    reading_load_warnings,
)
from overlay_studio.timing import entries_to_table, load_entries


class SRTTests(unittest.TestCase):
    def test_one_and_two_line_captions_map_to_30fps(self):
        captions = parse_srt_text(
            "1\n00:00:00,001 --> 00:00:01,000\nOne line\n\n"
            "2\n00:00:01,001 --> 00:00:03,501\nFirst\nSecond\n"
        )
        self.assertEqual((captions[0].start_frame, captions[0].end_frame), (1, 30))
        self.assertEqual(captions[0].line_count, 1)
        self.assertEqual((captions[1].start_frame, captions[1].end_frame), (31, 106))
        self.assertEqual(captions[1].line_count, 2)

    def test_overlapping_caption_intervals_are_preserved(self):
        captions = parse_srt_text(
            "1\n00:00:00,000 --> 00:00:02,000\nFirst\n\n"
            "2\n00:00:01,500 --> 00:00:03,000\nSecond\n"
        )
        self.assertEqual(len(active_captions(captions, 50, 55)), 2)

    def test_malformed_srt_is_rejected(self):
        with self.assertRaisesRegex(SRTValidationError, "invalid timestamp"):
            parse_srt_text("1\nnot a timestamp\nCaption")

    def test_reading_load_warning_and_long_gap_detection(self):
        captions = parse_srt_text(
            "1\n00:00:00,000 --> 00:00:01,000\nA very dense subtitle line\nSecond line\n\n"
            "2\n00:00:04,000 --> 00:00:05,000\nLater\n"
        )
        entry = OverlayEntry("A", 0, 30, "OVERLAY")
        self.assertEqual(len(reading_load_warnings([entry], captions)), 1)
        self.assertEqual(long_caption_gaps(captions), [(30, 120)])


class RichOverlayAndPersistenceTests(unittest.TestCase):
    def test_legacy_four_column_overlay_still_loads(self):
        with tempfile.TemporaryDirectory() as temporary:
            timing = Path(temporary) / "legacy.csv"
            timing.write_text(
                "SCENE_ID,START_TIME_30FPS,END_TIME_30FPS,ON_SCREEN_TEXT\n"
                "A,00:00:00.00,00:00:02.00,IS THIS LEGACY?\n",
                encoding="utf-8",
            )
            entries, _ = load_entries(timing)
            self.assertEqual(entries[0].semantic_type, "QUESTION")
            self.assertEqual(entries[0].priority, "MEDIUM")
            self.assertEqual(entries[0].sfx, "NONE")

    def test_rich_overlay_columns_are_mapped(self):
        with tempfile.TemporaryDirectory() as temporary:
            timing = Path(temporary) / "rich.csv"
            timing.write_text(
                "SCENE_ID,START_TIME_30FPS,END_TIME_30FPS,ON_SCREEN_TEXT,"
                "TYPE,PRIORITY,EMPHASIS_WORD,VISUAL_ACTION,SFX,LOCK_STYLE\n"
                "A,00:00:00.00,00:00:02.00,THE WATER CLUE,EVIDENCE,HIGH,"
                "WATER,PUNCH_IN,SOFT_HIT,TRUE\n",
                encoding="utf-8",
            )
            entry = load_entries(timing)[0][0]
            self.assertEqual(entry.semantic_type, "EVIDENCE")
            self.assertEqual(entry.priority, "HIGH")
            self.assertEqual(entry.accent_word, "WATER")
            self.assertEqual(entry.visual_action, "PUNCH_IN")
            self.assertEqual(entry.sfx, "SOFT_HIT")
            self.assertTrue(entry.lock_style)

    def test_explicit_emphasis_word_uses_existing_accent_renderer(self):
        entry = OverlayEntry(
            "A", 0, 60, "THE WATER CLUE", accent_word="WATER",
            resolved_position="TOP_LEFT", effect="AUTO",
        )
        plan_layout_and_styles(
            [entry], ProjectSettings(output_width=1920, output_height=1080)
        )
        self.assertEqual(entry.effect, "ACCENT_WORD")
        self.assertEqual(entry.accent_word, "WATER")

    def test_resolved_plan_table_exposes_analysis_and_action_frames(self):
        entry = OverlayEntry(
            "A", 0, 60, "THE CLUE", section_cue=True,
            motion_score=0.125, detail_score=0.375,
            action_start_frame=15, action_end_frame=45,
        )
        row = entries_to_table([entry]).iloc[0]
        self.assertTrue(row["SECTION_CUE"])
        self.assertEqual(row["MOTION_SCORE"], 0.125)
        self.assertEqual(row["DETAIL_SCORE"], 0.375)
        self.assertEqual(row["ACTION_START_30FPS"], "00:00:00.15")
        self.assertEqual(row["ACTION_END_30FPS"], "00:00:01.15")

    def test_old_project_loads_and_new_project_persists_srt_and_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            video = VideoInfo("source.mp4", 1920, 1080, 2.0, 30.0, True, "h264")
            entry = OverlayEntry(
                "A", 0, 60, "QUESTION?", semantic_type="QUESTION",
                priority="HIGH", accent_word="QUESTION", visual_action="PUNCH_IN",
                sfx="SOFT_HIT",
            )
            saved = save_project(
                root / "new", video=video, timing_path=str(root / "timing.csv"),
                srt_path=str(root / "captions.srt"), entries=[entry],
                settings=ProjectSettings(),
            )
            _, _, srt_path, loaded_entries, _ = load_project_data(saved["project"])
            self.assertTrue(srt_path.endswith("captions.srt"))
            self.assertEqual(loaded_entries[0].visual_action, "PUNCH_IN")

            payload = json.loads(saved["project"].read_text(encoding="utf-8"))
            payload.pop("srt_path")
            for key in ("semantic_type", "priority", "visual_action", "sfx"):
                payload["entries"][0].pop(key)
            old_project = root / "old_project.json"
            old_project.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_project_data(old_project)[2], "")
            self.assertEqual(load_project(old_project)[2][0].priority, "MEDIUM")


if __name__ == "__main__":
    unittest.main()
