from __future__ import annotations

import unittest
import subprocess
import tempfile
from pathlib import Path

from overlay_studio.media import ffmpeg_path
from overlay_studio.scene_analysis import cuts_inside, detect_scene_cuts, select_cut_frames


ROOT = Path(__file__).resolve().parents[1]


class SceneAnalysisTests(unittest.TestCase):
    def test_hard_cuts_pass_threshold_and_gradual_changes_do_not(self):
        scores = [(30, 0.08), (60, 0.14), (90, 0.36), (96, 0.51), (180, 0.62)]
        self.assertEqual(
            select_cut_frames(scores, threshold=0.35, minimum_gap_frames=12),
            [90, 180],
        )

    def test_overlay_spanning_cut_reports_internal_boundary(self):
        self.assertEqual(cuts_inside([30, 90, 150], 60, 120), [90])

    def test_ffmpeg_pass_detects_real_hard_cut_and_reuses_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "hard_cut.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=black:s=320x180:r=30:d=1",
                    "-f", "lavfi", "-i", "color=white:s=320x180:r=30:d=1",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
                    "-c:v", "libx264", "-preset", "ultrafast", str(source),
                ], check=True,
            )
            cache = root / "scene_cuts.json"
            cuts = detect_scene_cuts(source, cache, app_root=ROOT)
            self.assertTrue(any(28 <= frame <= 32 for frame in cuts), cuts)
            self.assertEqual(detect_scene_cuts(source, cache, app_root=ROOT), cuts)


if __name__ == "__main__":
    unittest.main()
