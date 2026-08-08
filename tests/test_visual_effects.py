from __future__ import annotations

import unittest
import subprocess
import tempfile
from pathlib import Path

from overlay_studio.media import ffmpeg_path, ffprobe_path
from overlay_studio.models import OverlayEntry, ProjectSettings
from overlay_studio.render import _relative_action_intervals, _video_filter, render_clip


ROOT = Path(__file__).resolve().parents[1]


class VisualEffectFilterTests(unittest.TestCase):
    def test_punch_in_is_subtle_and_chunk_relative(self):
        entry = OverlayEntry(
            "A", 300, 390, "TEXT", visual_action="PUNCH_IN",
            action_start_frame=315, action_end_frame=375,
        )
        settings = ProjectSettings(output_width=1920, output_height=1080)
        intervals = _relative_action_intervals(
            [entry], "PUNCH_IN", window_start_frame=300,
            window_end_frame=390, fps=30,
        )
        self.assertEqual(intervals, [(0.5, 2.5)])
        result = _video_filter(
            Path("overlay.ass"), settings, [entry],
            window_start_frame=300, window_end_frame=390,
        )
        self.assertIn("1+0.05", result)
        self.assertIn("crop=1920:1080", result)
        self.assertNotIn("zoompan", result)

    def test_dim_focus_is_mild_and_temporary(self):
        entry = OverlayEntry(
            "A", 0, 60, "QUESTION?", visual_action="DIM_FOCUS",
            action_end_frame=60,
        )
        result = _video_filter(
            Path("overlay.ass"), ProjectSettings(output_width=640, output_height=360),
            [entry], window_start_frame=0, window_end_frame=90,
        )
        self.assertIn("-0.06", result)
        self.assertIn("between(t", result)

    def test_punch_in_render_preserves_exact_frame_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(source),
                ],
                check=True,
            )
            entry = OverlayEntry(
                "A", 0, 60, "PUNCH", enabled=False, visual_action="PUNCH_IN",
                action_end_frame=60,
            )
            # Keep text disabled while exercising the visual action itself.
            entry.enabled = True
            entry.font_size_px = 32
            entry.wrapped_text = "PUNCH"
            entry.effect = "CLEAN_SHADOW"
            entry.animation = "FADE_ONLY"
            output = root / "output.mp4"
            render_clip(
                source, [entry], ProjectSettings(
                    output_width=320, output_height=180, x264_preset="ultrafast", crf=28
                ),
                start_frame=0, end_frame=60, output_path=output,
                work_dir=root / "work", app_root=ROOT, include_audio=False,
            )
            counted = subprocess.run(
                [
                    ffprobe_path(ROOT), "-v", "error", "-count_frames", "-select_streams", "v:0",
                    "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output),
                ],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(int(counted.stdout.strip()), 60)


if __name__ == "__main__":
    unittest.main()
