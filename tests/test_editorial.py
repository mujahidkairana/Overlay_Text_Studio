from __future__ import annotations

import unittest

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

    def test_report_is_variation_not_monetization_score(self):
        report = editorial_report([OverlayEntry("A", 0, 60, "FACT")], 60.0)
        self.assertEqual(report["events_per_minute"], 1.0)
        self.assertNotIn("monetization", " ".join(report).lower())


if __name__ == "__main__":
    unittest.main()
