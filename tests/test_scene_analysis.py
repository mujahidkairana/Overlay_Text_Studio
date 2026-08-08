from __future__ import annotations

import unittest

from overlay_studio.scene_analysis import cuts_inside, select_cut_frames


class SceneAnalysisTests(unittest.TestCase):
    def test_hard_cuts_pass_threshold_and_gradual_changes_do_not(self):
        scores = [(30, 0.08), (60, 0.14), (90, 0.36), (96, 0.51), (180, 0.62)]
        self.assertEqual(
            select_cut_frames(scores, threshold=0.35, minimum_gap_frames=12),
            [90, 180],
        )

    def test_overlay_spanning_cut_reports_internal_boundary(self):
        self.assertEqual(cuts_inside([30, 90, 150], 60, 120), [90])


if __name__ == "__main__":
    unittest.main()
