from __future__ import annotations

import unittest
import subprocess
import tempfile
import re
from pathlib import Path

from overlay_studio.media import ffmpeg_path, ffprobe_path, probe_video
from overlay_studio.models import OverlayEntry, ProjectSettings
from overlay_studio.render import (
    _final_audio_codec_args,
    _relative_action_intervals,
    _sfx_mix_filter,
    _video_filter,
    render_clip,
    render_full_resumable,
    validate_sfx_assets,
)


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
        self.assertIn("(t-0.500000)/0.250000", result)
        self.assertIn("(2.500000-t)/0.250000", result)
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
        self.assertIn("(t-0.000000)/0.250000", result)
        self.assertIn("(2.000000-t)/0.250000", result)

    def test_audio_is_copied_without_sfx_and_encoded_only_for_mix(self):
        self.assertEqual(_final_audio_codec_args(False), ["-c:a", "copy"])
        self.assertEqual(
            _final_audio_codec_args(True),
            ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"],
        )

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

    def test_in_place_freeze_preserves_duration_frames_and_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source_with_audio.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(source),
                ],
                check=True,
            )
            entry = OverlayEntry(
                "A", 15, 45, "FREEZE", visual_action="FREEZE",
                action_start_frame=15, action_end_frame=33,
                font_size_px=32, wrapped_text="FREEZE", effect="CLEAN_SHADOW",
                animation="FADE_ONLY",
            )
            output = root / "freeze.mp4"
            render_clip(
                source, [entry], ProjectSettings(
                    output_width=320, output_height=180, x264_preset="ultrafast", crf=28
                ),
                start_frame=0, end_frame=60, output_path=output,
                work_dir=root / "work", app_root=ROOT, include_audio=True,
            )
            rendered = probe_video(output, ROOT)
            self.assertAlmostEqual(rendered.duration_seconds, 2.0, delta=0.08)
            self.assertTrue(rendered.has_audio)
            counted = subprocess.run(
                [
                    ffprobe_path(ROOT), "-v", "error", "-count_frames", "-select_streams", "v:0",
                    "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output),
                ], capture_output=True, text=True, check=True,
            )
            self.assertEqual(int(counted.stdout.strip()), 60)

    def test_sfx_mix_is_conservative_and_limited(self):
        chains, label = _sfx_mix_filter([(Path("soft_hit.wav"), 1500)])
        rendered = ";".join(chains)
        self.assertEqual(label, "[aout]")
        self.assertIn("volume=0.12", rendered)
        self.assertIn("adelay=1500", rendered)
        self.assertIn("duration=first", rendered)
        self.assertIn("alimiter=limit=0.95", rendered)

    def test_unlicensed_or_missing_sfx_is_reported_and_skipped(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sfx = root / "sfx"
            sfx.mkdir()
            (sfx / "tick.wav").write_bytes(b"not needed for validation")
            entry = OverlayEntry("A", 0, 30, "TICK", sfx="TICK")
            settings = ProjectSettings(sfx_folder=str(sfx))
            warnings = validate_sfx_assets([entry], settings, root / "project", ROOT)
            self.assertTrue(any("LICENSES.txt" in warning for warning in warnings))

    def test_local_sfx_mix_preserves_source_duration_without_clipping(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=320x180:r=30:d=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source),
                ], check=True,
            )
            sfx_dir = root / "project" / "sfx"
            sfx_dir.mkdir(parents=True)
            (sfx_dir / "README.md").write_text("Self-created test tone.", encoding="utf-8")
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000:duration=0.2",
                    str(sfx_dir / "tick.wav"),
                ], check=True,
            )
            video = probe_video(source, ROOT)
            entry = OverlayEntry(
                "A", 15, 45, "TICK", sfx="TICK", font_size_px=32,
                wrapped_text="TICK", effect="CLEAN_SHADOW", animation="FADE_ONLY",
            )
            output = root / "mixed.mp4"
            render_full_resumable(
                video, [entry], ProjectSettings(
                    output_width=320, output_height=180, chunk_target_seconds=10,
                    x264_preset="ultrafast", crf=28,
                ), output_path=output, project_dir=root / "project", app_root=ROOT,
            )
            rendered = probe_video(output, ROOT)
            self.assertAlmostEqual(rendered.duration_seconds, 2.0, delta=0.08)
            self.assertTrue(rendered.has_audio)
            levels = subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-i", str(output),
                    "-af", "volumedetect", "-f", "null", "NUL",
                ], capture_output=True, text=True,
            )
            self.assertIn("max_volume", levels.stderr)
            match = re.search(r"max_volume:\s*(-?[0-9.]+) dB", levels.stderr)
            self.assertIsNotNone(match)
            self.assertLessEqual(float(match.group(1)), 0.0)


if __name__ == "__main__":
    unittest.main()
