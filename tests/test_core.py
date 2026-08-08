from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from overlay_studio.ass import build_ass
from overlay_studio.layout import plan_layout_and_styles
from overlay_studio.media import extract_analysis_frames, ffmpeg_path, ffprobe_path, probe_video
from overlay_studio.models import OverlayEntry, ProjectSettings, VideoInfo
from overlay_studio.project import load_project, save_project
from overlay_studio.render import (
    RenderCancelled,
    plan_chunks,
    render_full_resumable,
    select_fast_encoder,
)
from overlay_studio.safety import _face_boxes, analyze_entries, candidate_boxes, text_box_width_fraction
from overlay_studio.timing import (
    TimingValidationError,
    frame_to_timecode,
    load_entries,
    timecode_to_frame,
)
import start_overlay
from overlay_studio import worker
from overlay_studio.worker import run as run_worker


ROOT = Path(__file__).resolve().parents[1]


class TimingTests(unittest.TestCase):
    def test_round_trip_30fps(self):
        for frame in (0, 1, 29, 30, 1837, 108_001):
            self.assertEqual(timecode_to_frame(frame_to_timecode(frame)), frame)

    def test_invalid_frame_field(self):
        with self.assertRaises(ValueError):
            timecode_to_frame("00:00:02.30")

    def test_three_explicit_lines_are_rejected_instead_of_silently_dropped(self):
        with tempfile.TemporaryDirectory() as temp_string:
            timing = Path(temp_string) / "three_lines.csv"
            timing.write_text(
                "START_TIME_30FPS,END_TIME_30FPS,ON_SCREEN_TEXT\n"
                '00:00:00.00,00:00:02.00,"ONE\\NTWO\\NTHREE"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TimingValidationError, "maximum is 2"):
                load_entries(timing, video_duration_frames=90)


class PlanningTests(unittest.TestCase):
    def test_automatic_positions_never_enter_caption_band(self):
        height = 1080
        caption_y0 = round(height * 0.65)
        boxes = candidate_boxes(1920, height, caption_safe_percent=35.0)
        self.assertTrue(all(box[3] <= caption_y0 for box in boxes.values()))

    def test_controlled_randomization_has_no_adjacent_animation_repeat(self):
        entries = [
            OverlayEntry(str(index), index * 90, index * 90 + 75, "A short overlay", resolved_position="TOP_CENTER")
            for index in range(12)
        ]
        settings = ProjectSettings(output_width=1920, output_height=1080)
        plan_layout_and_styles(entries, settings)
        animations = [entry.animation for entry in entries]
        self.assertTrue(all(a != b for a, b in zip(animations, animations[1:])))

    def test_chunk_boundary_never_splits_overlay(self):
        entries = [OverlayEntry("A", 1700, 1900, "Crosses planned boundary")]
        chunks = plan_chunks(3600, entries, target_seconds=60)
        self.assertEqual(chunks[0], (0, 1900))
        for _, boundary in chunks[:-1]:
            self.assertFalse(any(entry.start_frame < boundary < entry.end_frame for entry in entries))

    def test_ass_contains_expected_frame_times(self):
        entry = OverlayEntry(
            "A",
            1,
            2,
            "TEST",
            resolved_position="TOP_LEFT",
            font_size_px=80,
            wrapped_text="TEST",
            animation="FADE_RISE",
            effect="CLEAN_SHADOW",
        )
        output = build_ass([entry], ProjectSettings(output_width=1920, output_height=1080))
        self.assertIn("0:00:00.03,0:00:00.06", output)

    def test_professional_scale_animation_avoids_aggressive_pixelating_pop(self):
        entry = OverlayEntry(
            "A", 0, 60, "CRISP TEXT", resolved_position="TOP_CENTER",
            font_size_px=80, wrapped_text="CRISP TEXT",
            animation="SOFT_SCALE", effect="CLEAN_SHADOW",
        )
        output = build_ass([entry], ProjectSettings(output_width=1920, output_height=1080))
        self.assertIn("\\fscx94\\fscy94", output)
        self.assertNotIn("\\fscx82", output)
        self.assertNotIn("SOFT_GLOW", output)

    def test_outline_scales_with_output_resolution(self):
        entry = OverlayEntry(
            "A", 0, 60, "TEXT", font_size_px=80, wrapped_text="TEXT",
            animation="FADE_ONLY", effect="CLEAN_SHADOW",
        )
        hd = build_ass([entry], ProjectSettings(output_width=1920, output_height=1080))
        uhd = build_ass([entry], ProjectSettings(output_width=3840, output_height=2160))
        self.assertIn("\\bord3", hd)
        self.assertIn("\\bord6", uhd)

    def test_fast_reading_uses_protected_plate(self):
        entry = OverlayEntry(
            "FAST", 0, 30,
            "THIS OVERLAY CONTAINS FAR TOO MANY CHARACTERS TO READ IN ONE SECOND",
        )
        settings = ProjectSettings(output_width=1920, output_height=1080)
        plan_layout_and_styles([entry], settings)
        self.assertEqual(entry.reading_status, "TOO_FAST")
        self.assertEqual(entry.effect, "PROTECTED_PLATE")
        self.assertIn("OverlayPlate", build_ass([entry], settings))

    def test_overlapping_entries_choose_different_positions(self):
        with tempfile.TemporaryDirectory() as temp_string:
            frame = Path(temp_string) / "frame.jpg"
            Image.new("RGB", (640, 360), (30, 30, 30)).save(frame)
            entries = [
                OverlayEntry("A", 0, 60, "FIRST"),
                OverlayEntry("B", 15, 75, "SECOND"),
            ]
            analyze_entries(entries, [frame], sample_fps=0.5, max_workers=1)
            self.assertNotEqual(
                entries[0].resolved_position,
                entries[1].resolved_position,
            )

    def test_safe_region_expands_for_longer_rendered_text(self):
        short = OverlayEntry("S", 0, 60, "SHORT")
        long = OverlayEntry(
            "L", 0, 60, "THIS IS A MUCH LONGER OVERLAY THAT NEEDS A WIDER SAFE RESTING AREA"
        )
        self.assertGreater(
            text_box_width_fraction(long, 16 / 9),
            text_box_width_fraction(short, 16 / 9),
        )

    def test_port_search_continues_past_first_three_ports(self):
        original = start_overlay._port_available
        try:
            start_overlay._port_available = lambda port: port == 8507
            self.assertEqual(start_overlay._find_port(), 8507)
        finally:
            start_overlay._port_available = original

    def test_launcher_fingerprints_source_to_avoid_stale_imports(self):
        fingerprint = start_overlay._source_fingerprint()
        self.assertEqual(len(fingerprint), 64)
        launcher = (ROOT / "start_overlay.py").read_text(encoding="utf-8")
        self.assertIn("SOURCE_STAMP_FILE", launcher)
        self.assertIn("same_source", launcher)

    def test_project_reopen_preserves_random_seed(self):
        with tempfile.TemporaryDirectory() as temp_string:
            temp = Path(temp_string)
            source = temp / "source.mp4"
            source.write_bytes(b"placeholder")
            video = VideoInfo(str(source), 1920, 1080, 2.0, 30.0, False, "h264")
            settings = ProjectSettings(project_name="seed_test", random_seed=987654321)
            save_project(
                temp / "project",
                video=video,
                timing_path=str(temp / "timing.csv"),
                entries=[OverlayEntry("A", 0, 30, "TEXT")],
                settings=settings,
            )
            _, _, _, reopened = load_project(temp / "project" / "project.json")
            self.assertEqual(reopened.random_seed, 987654321)

    def test_face_detection_result_is_cached_per_frame(self):
        with tempfile.TemporaryDirectory() as temp_string:
            frame = Path(temp_string) / "frame.jpg"
            Image.new("RGB", (320, 180), (30, 30, 30)).save(frame)
            _face_boxes.cache_clear()
            _face_boxes(str(frame))
            _face_boxes(str(frame))
            self.assertEqual(_face_boxes.cache_info().hits, 1)

    def test_safe_stop_keeps_render_from_starting_new_chunk(self):
        with tempfile.TemporaryDirectory() as temp_string:
            temp = Path(temp_string)
            source = temp / "source.mp4"
            source.write_bytes(b"placeholder")
            video = VideoInfo(str(source), 320, 180, 2.0, 30.0, False, "h264")
            settings = ProjectSettings(output_width=320, output_height=180)
            with self.assertRaises(RenderCancelled):
                render_full_resumable(
                    video,
                    [],
                    settings,
                    output_path=temp / "stopped.mp4",
                    project_dir=temp / "project",
                    app_root=ROOT,
                    cancel_check=lambda: True,
                )
            self.assertFalse((temp / "stopped.mp4").exists())


class EndToEndTests(unittest.TestCase):
    def test_analysis_and_resumable_render(self):
        with tempfile.TemporaryDirectory() as temp_string:
            temp = Path(temp_string)
            source = temp / "source.mp4"
            command = [
                ffmpeg_path(ROOT),
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=640x360:rate=30:duration=3",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=3",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(source),
            ]
            subprocess.run(command, check=True)
            video = probe_video(source, ROOT)
            entries = [OverlayEntry("TEST", 15, 75, "WHAT DID IT KNOW?")]
            frames = extract_analysis_frames(source, temp / "analysis", app_root=ROOT)
            analyze_entries(entries, frames)
            settings = ProjectSettings(
                project_name="test",
                output_width=640,
                output_height=360,
                random_seed=17,
                chunk_target_seconds=10,
                x264_preset="ultrafast",
                crf=25,
            )
            plan_layout_and_styles(entries, settings)
            output = temp / "rendered.mp4"
            render_full_resumable(
                video,
                entries,
                settings,
                output_path=output,
                project_dir=temp / "project",
                app_root=ROOT,
            )
            rendered = probe_video(output, ROOT)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 10_000)
            self.assertAlmostEqual(rendered.source_fps, 30.0, places=2)
            self.assertTrue(rendered.has_audio)

    def test_short_audio_does_not_truncate_ten_second_video(self):
        with tempfile.TemporaryDirectory() as temp_string:
            temp = Path(temp_string)
            source = temp / "short_audio_source.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(source),
                ],
                check=True,
            )
            video = probe_video(source, ROOT)
            self.assertAlmostEqual(video.duration_seconds, 10.0, places=1)
            settings = ProjectSettings(
                project_name="short_audio", output_width=320, output_height=180,
                chunk_target_seconds=10, x264_preset="ultrafast", crf=28,
            )
            output = temp / "full_duration.mp4"
            render_full_resumable(
                video, [], settings, output_path=output,
                project_dir=temp / "project", app_root=ROOT,
            )
            rendered = probe_video(output, ROOT)
            self.assertAlmostEqual(rendered.duration_seconds, 10.0, delta=0.08)
            counted = subprocess.run(
                [
                    ffprobe_path(ROOT), "-v", "error", "-count_frames", "-select_streams", "v:0",
                    "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output),
                ],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(int(counted.stdout.strip()), 300)


class SetupFlowTests(unittest.TestCase):
    def test_repository_ignores_machine_local_state(self):
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for local_path in (
            "runtime_path.txt",
            "shared_components_path.txt",
            "logs/",
            "work/",
            "__pycache__/",
        ):
            self.assertIn(local_path, ignore)

    def test_setup_has_portable_default_and_no_required_checkout_path(self):
        script = (ROOT / "shared_setup.ps1").read_text(encoding="utf-8")
        self.assertIn("OVERLAY_STUDIO_SHARED_COMPONENTS", script)
        self.assertIn("OverlayTextStudio\\Shared_Components", script)
        self.assertNotIn("D:\\MUZ\\", script)

    def test_versioned_shared_ffmpeg_layout_is_supported(self):
        media = (ROOT / "overlay_studio" / "media.py").read_text(encoding="utf-8")
        self.assertIn('glob(f"*/bin/{executable}")', media)

    def test_cached_encoder_selection_needs_no_benchmark(self):
        with tempfile.TemporaryDirectory() as temp_string:
            cache = Path(temp_string) / "encoder.json"
            cache.write_text(
                '{"selected":"libx264","seconds":{"libx264":1.25}}',
                encoding="utf-8",
            )
            selected, timings = select_fast_encoder(ROOT, cache)
            self.assertEqual(selected, "libx264")
            self.assertEqual(timings["libx264"], 1.25)

    def test_background_worker_reports_safe_stop(self):
        with tempfile.TemporaryDirectory() as temp_string:
            temp = Path(temp_string)
            source = temp / "source.mp4"
            source.write_bytes(b"placeholder")
            video = VideoInfo(str(source), 320, 180, 2.0, 30.0, False, "h264")
            settings = ProjectSettings(
                project_name="worker", output_width=320, output_height=180
            )
            saved = save_project(
                temp / "project",
                video=video,
                timing_path=str(temp / "timing.csv"),
                entries=[],
                settings=settings,
            )
            stop = temp / "project" / "STOP_RENDER.REQUEST"
            stop.write_text("stop", encoding="utf-8")
            status = temp / "project" / "render_status.json"
            result = run_worker(
                saved["project"], temp / "output.mp4", status, stop
            )
            payload = __import__("json").loads(status.read_text(encoding="utf-8"))
            self.assertEqual(result, 2)
            self.assertEqual(payload["state"], "stopped")

    def test_status_write_retries_transient_windows_access_denied(self):
        with tempfile.TemporaryDirectory() as temp_string:
            status = Path(temp_string) / "render_status.json"
            real_replace = __import__("os").replace
            attempts = 0

            def temporarily_locked(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    error = PermissionError("temporarily locked")
                    error.winerror = 5
                    raise error
                return real_replace(source, destination)

            with patch.object(worker.os, "replace", side_effect=temporarily_locked):
                written = worker._write_status(status, state="running")

            self.assertTrue(written)
            self.assertEqual(attempts, 3)
            self.assertEqual(
                __import__("json").loads(status.read_text(encoding="utf-8"))["state"],
                "running",
            )
            self.assertEqual(list(status.parent.glob("render_status.json.tmp.*")), [])

    def test_noncritical_status_write_does_not_abort_after_lock_timeout(self):
        with tempfile.TemporaryDirectory() as temp_string:
            status = Path(temp_string) / "render_status.json"
            error = PermissionError("still locked")
            error.winerror = 5
            with patch.object(worker.os, "replace", side_effect=error), patch.object(
                worker.time, "sleep"
            ):
                written = worker._write_status(
                    status, strict=False, state="running"
                )

            self.assertFalse(written)
            self.assertEqual(list(status.parent.glob("render_status.json.tmp.*")), [])

    def test_shared_setup_has_installed_cached_download_precedence(self):
        script = (ROOT / "shared_setup.ps1").read_text(encoding="utf-8")
        self.assertIn("Python already installed in shared space; reusing it.", script)
        self.assertIn("using cached installer", script)
        self.assertIn("python-3.12.10-amd64.exe", script)
        self.assertIn("Dependencies already downloaded", script)
        self.assertIn("neither installed nor cached; downloading once", script)
        self.assertIn("PipCache", script)
        self.assertIn("Wheelhouse", script)


if __name__ == "__main__":
    unittest.main()
