from __future__ import annotations

import unittest

from overlay_studio.ass import _protected_plate_geometry, _rendered_line_widths, build_ass
from overlay_studio.editorial import editorial_report, plan_editorial_actions
from overlay_studio.models import OverlayEntry, ProjectSettings
from overlay_studio.srt import CaptionInterval


class EditorialPlannerTests(unittest.TestCase):
    def test_standard_density_enforces_strong_event_cooldown(self):
        entries = [
            OverlayEntry(str(index), index * 90, index * 90 + 60, "KEY FACT", semantic_type="EVIDENCE", priority="HIGH", confidence="HIGH")
            for index in range(4)
        ]
        plan_editorial_actions(entries, ProjectSettings(output_width=1920, output_height=1080))
        self.assertEqual(entries[0].visual_action, "PUNCH_IN")
        self.assertTrue(all(item.visual_action == "NONE" for item in entries[1:3]))

    def test_busy_subtitle_interval_suppresses_strong_motion(self):
        entry = OverlayEntry("A", 0, 90, "EVIDENCE", semantic_type="EVIDENCE", priority="HIGH", confidence="HIGH")
        captions = [CaptionInterval(0, 90, "Busy\nsubtitle", 2, 20.0)]
        plan_editorial_actions([entry], ProjectSettings(), captions=captions)
        self.assertEqual(entry.visual_action, "NONE")

    def test_action_is_clamped_at_hard_cut(self):
        entry = OverlayEntry("A", 0, 120, "TAKEAWAY", semantic_type="TAKEAWAY", priority="HIGH", confidence="HIGH")
        settings = ProjectSettings(scene_cut_frames=[75])
        plan_editorial_actions([entry], settings)
        self.assertEqual(entry.visual_action, "PUNCH_IN")
        self.assertEqual(entry.action_end_frame, 75)

    def test_dim_focus_is_clamped_at_hard_cut(self):
        entry = OverlayEntry(
            "A", 0, 120, "QUESTION?", semantic_type="QUESTION",
            priority="MEDIUM", confidence="HIGH",
        )
        plan_editorial_actions([entry], ProjectSettings(scene_cut_frames=[75]))
        self.assertEqual(entry.visual_action, "DIM_FOCUS")
        self.assertEqual(entry.action_end_frame, 75)

    def test_failed_scene_analysis_disables_strong_actions_and_low_freeze(self):
        punch = OverlayEntry(
            "P", 0, 90, "EVIDENCE", semantic_type="EVIDENCE",
            priority="HIGH", confidence="HIGH",
        )
        low_freeze = OverlayEntry(
            "F", 300, 390, "MINOR DETAIL", semantic_type="FACT",
            priority="LOW", confidence="HIGH", visual_action="FREEZE",
        )
        plan_editorial_actions(
            [punch, low_freeze], ProjectSettings(scene_analysis_available=False)
        )
        self.assertEqual(punch.visual_action, "NONE")
        self.assertEqual(low_freeze.visual_action, "NONE")

    def test_failed_scene_analysis_also_disables_dim_focus(self):
        dim = OverlayEntry(
            "D", 0, 90, "WHAT HAPPENED?", semantic_type="QUESTION",
            priority="MEDIUM", confidence="HIGH",
        )
        plan_editorial_actions(
            [dim], ProjectSettings(scene_analysis_available=False)
        )
        self.assertEqual(dim.visual_action, "NONE")

    def test_report_is_variation_not_monetization_score(self):
        report = editorial_report([OverlayEntry("A", 0, 60, "FACT")], 60.0)
        self.assertEqual(report["events_per_minute"], 1.0)
        self.assertNotIn("monetization", " ".join(report).lower())
        self.assertEqual(report["target_events_per_minute"], "4-7")

    def test_report_flags_supplied_overlay_density_above_target(self):
        entries = [OverlayEntry(str(index), index * 90, index * 90 + 30, "FACT") for index in range(9)]
        report = editorial_report(entries, 60.0)
        self.assertEqual(report["density_status"], "OVER_TARGET")

    def test_all_semantic_types_receive_distinct_renderer_treatment(self):
        expected = {
            "NUMBER": "STRONG_OUTLINE",
            "EVIDENCE": "CLEAN_SHADOW",
            "COMPARISON": "PROTECTED_PLATE",
            "WARNING": "PROTECTED_PLATE",
            "UNCERTAINTY": "PROTECTED_PLATE",
            "TAKEAWAY": "STRONG_OUTLINE",
            "SECTION": "CLEAN_SHADOW",
        }
        entries = [
            OverlayEntry(
                name, index * 120, index * 120 + 90,
                "42%" if name == "NUMBER" else "A VS B" if name == "COMPARISON" else "THE FINDING",
                semantic_type=name, confidence="HIGH", resolved_position="TOP_LEFT",
                font_size_px=64, wrapped_text="42%" if name == "NUMBER" else "A VS B" if name == "COMPARISON" else "THE FINDING",
            )
            for index, name in enumerate(expected)
        ]
        plan_editorial_actions(entries, ProjectSettings())
        self.assertEqual({entry.semantic_type: entry.effect for entry in entries}, expected)
        rendered = build_ass(entries, ProjectSettings(output_width=1920, output_height=1080))
        self.assertIn("EVIDENCE", rendered)
        self.assertIn("CAUTION", rendered)
        self.assertIn("POSSIBLE / NOT PROVEN", rendered)

        uncertainty = next(entry for entry in entries if entry.semantic_type == "UNCERTAINTY")
        _, _, _, plate_height, _ = _protected_plate_geometry(
            uncertainty, ProjectSettings(output_width=1920, output_height=1080), 8, 960, 60
        )
        self.assertGreater(plate_height, uncertainty.font_size_px * 2)

    def test_long_srt_gap_marks_first_suitable_overlay_as_section_cue(self):
        captions = [
            CaptionInterval(0, 30, "First", 1, 5.0),
            CaptionInterval(180, 240, "Next", 1, 5.0),
        ]
        entries = [OverlayEntry("A", 90, 150, "NEW IDEA", confidence="HIGH")]
        plan_editorial_actions(entries, ProjectSettings(), captions=captions)
        self.assertTrue(entries[0].section_cue)
        self.assertEqual(entries[0].animation, "FADE_ONLY")

    def test_measured_high_motion_suppresses_punch_in(self):
        entry = OverlayEntry(
            "A", 0, 90, "EVIDENCE", semantic_type="EVIDENCE", priority="HIGH",
            confidence="HIGH", motion_score=0.2,
        )
        plan_editorial_actions([entry], ProjectSettings())
        self.assertEqual(entry.visual_action, "NONE")

    def test_punch_in_and_sfx_caps_are_enforced(self):
        punch_entries = [
            OverlayEntry(
                str(index), index * 300, index * 300 + 60, "KEY",
                semantic_type="EVIDENCE", priority="HIGH", confidence="HIGH",
            )
            for index in range(6)
        ]
        plan_editorial_actions(punch_entries, ProjectSettings(density_preset="STANDARD"))
        self.assertLessEqual(sum(entry.visual_action == "PUNCH_IN" for entry in punch_entries), 3)

        sfx_entries = [
            OverlayEntry(str(index), index * 1794, index * 1794 + 60, "CUE", sfx="TICK")
            for index in range(11)
        ]
        plan_editorial_actions(sfx_entries, ProjectSettings(density_preset="STANDARD"))
        self.assertLessEqual(sum(entry.sfx != "NONE" for entry in sfx_entries), 10)

    def test_dim_focus_and_sfx_have_independent_spacing(self):
        dim_entries = [
            OverlayEntry(
                str(index), index * 60, index * 60 + 45, "QUESTION?",
                semantic_type="QUESTION", confidence="HIGH",
            )
            for index in range(3)
        ]
        plan_editorial_actions(dim_entries, ProjectSettings(density_preset="STANDARD"))
        self.assertEqual(dim_entries[0].visual_action, "DIM_FOCUS")
        self.assertEqual(dim_entries[1].visual_action, "NONE")

        sfx_entries = [
            OverlayEntry("A", 0, 60, "ONE", sfx="SOFT_HIT"),
            OverlayEntry("B", 90, 150, "TWO", sfx="SUBTLE_WHOOSH"),
        ]
        plan_editorial_actions(sfx_entries, ProjectSettings(density_preset="STANDARD"))
        self.assertEqual(sfx_entries[0].sfx, "SOFT_HIT")
        self.assertEqual(sfx_entries[1].sfx, "NONE")

    def test_short_warning_plate_accounts_for_caution_prefix(self):
        warning = OverlayEntry(
            "W", 0, 60, "DANGER", semantic_type="WARNING",
            font_size_px=64, wrapped_text="DANGER",
        )
        _, _, plate_width, _, _ = _protected_plate_geometry(
            warning, ProjectSettings(output_width=1920, output_height=1080), 8, 960, 60
        )
        fact = OverlayEntry(
            "F", 0, 60, "DANGER", semantic_type="FACT",
            font_size_px=64, wrapped_text="DANGER",
        )
        _, _, fact_width, _, _ = _protected_plate_geometry(
            fact, ProjectSettings(output_width=1920, output_height=1080), 8, 960, 60
        )
        self.assertGreater(plate_width, fact_width)

    def test_protected_plate_contains_real_screenshot_text_bounds(self):
        settings = ProjectSettings(output_width=1920, output_height=1080)
        entries = [
            OverlayEntry(
                "A", 0, 90, "LOOKS LIKE PROOF. PROVES ALMOST NOTHING.",
                semantic_type="FACT", effect="PROTECTED_PLATE", font_size_px=77,
                wrapped_text="LOOKS LIKE PROOF.\\NPROVES ALMOST NOTHING.",
            ),
            OverlayEntry(
                "B", 120, 210, "COULD A DINOSAUR USE A TOOL?",
                semantic_type="QUESTION", effect="PROTECTED_PLATE", font_size_px=64,
                wrapped_text="COULD A DINOSAUR USE A TOOL?",
            ),
        ]
        for entry in entries:
            text_width = max(_rendered_line_widths(entry, settings))
            _, _, plate_width, _, _ = _protected_plate_geometry(
                entry, settings, 8, 960, 60
            )
            self.assertGreaterEqual(plate_width - text_width, entry.font_size_px // 2)
            self.assertLessEqual(plate_width, round(text_width * 1.14))


if __name__ == "__main__":
    unittest.main()
