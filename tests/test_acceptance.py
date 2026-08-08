from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from overlay_studio.media import ffmpeg_path, ffprobe_path, probe_video
from overlay_studio.models import OverlayEntry, ProjectSettings
from overlay_studio.render import render_clip, render_full_resumable
from overlay_studio.scene_analysis import detect_scene_cuts
from overlay_studio.srt import parse_srt_text, validate_srt_video_timing


ROOT = Path(__file__).resolve().parents[1]


class BranchHelperIntegrationTests(unittest.TestCase):
    def test_cross_feature_daily_sequence_and_collision_rejection(self):
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            self.skipTest("PowerShell is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            work = root / "Overlay_Text_Studio"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
            def git(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(["git", *args], cwd=work, text=True, capture_output=True, check=True)
            git("config", "user.email", "qa@example.invalid")
            git("config", "user.name", "QA")
            git("commit", "--allow-empty", "-m", "initial")
            git("remote", "add", "origin", str(remote))
            date = dt.datetime.now().strftime("%Y/%m/%d")
            for feature, sequence in (("first-feature", "01"), ("second-feature", "02")):
                branch = f"overlay-text-studio/{feature}/{date}/{sequence}"
                git("switch", "-c", branch)
                git("push", "-u", "origin", branch)
                git("switch", "main")
            helper = ROOT / "scripts" / "new_branch.ps1"
            created = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper), "-Feature", "offline auto enhance"],
                cwd=work, text=True, capture_output=True,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertTrue(git("branch", "--show-current").stdout.strip().endswith("/03"))
            git("switch", "main")
            collision = subprocess.run(
                [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper), "-Feature", "second feature", "-Sequence", "2"],
                cwd=work, text=True, capture_output=True,
            )
            self.assertNotEqual(collision.returncode, 0)
            self.assertIn("already exists", collision.stderr + collision.stdout)


class LongRenderAcceptanceTests(unittest.TestCase):
    def test_enhanced_video_pipeline_combines_cuts_srt_overlays_actions_and_sfx(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "enhanced_source.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=6",
                    "-f", "lavfi", "-i", "color=white:size=160x90:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=12",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", str(source),
                ], check=True,
            )
            captions = parse_srt_text(
                "1\n00:00:00,000 --> 00:00:02,000\nOpening caption\n\n"
                "2\n00:00:07,000 --> 00:00:10,000\nSecond caption\n"
            )
            self.assertFalse(validate_srt_video_timing(captions, 360))
            project = root / "project"
            sfx = project / "sfx"
            sfx.mkdir(parents=True)
            (sfx / "README.md").write_text("Locally generated QA tone", encoding="utf-8")
            subprocess.run(
                [ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=880:duration=0.15",
                 str(sfx / "soft_hit.wav")], check=True,
            )
            entries = [
                OverlayEntry("Q", 30, 90, "WHAT CHANGED?", semantic_type="QUESTION", visual_action="PUNCH_IN", action_start_frame=30, action_end_frame=90, sfx="SOFT_HIT", font_size_px=24, wrapped_text="WHAT CHANGED?", effect="CLEAN_SHADOW", animation="SOFT_SCALE"),
                OverlayEntry("E", 120, 180, "THE EVIDENCE", semantic_type="EVIDENCE", visual_action="DIM_FOCUS", action_start_frame=120, action_end_frame=180, font_size_px=24, wrapped_text="THE EVIDENCE", effect="CLEAN_SHADOW", animation="EASE_SIDE"),
                OverlayEntry("T", 210, 270, "KEY TAKEAWAY", semantic_type="TAKEAWAY", visual_action="FREEZE", action_start_frame=210, action_end_frame=228, font_size_px=24, wrapped_text="KEY TAKEAWAY", effect="STRONG_OUTLINE", animation="SOFT_SCALE"),
            ]
            settings = ProjectSettings(
                output_width=160, output_height=90, chunk_target_seconds=6,
                x264_preset="ultrafast", crf=30,
            )
            settings.scene_cut_frames = detect_scene_cuts(
                source, project / "cache" / "cuts.json", app_root=ROOT
            )
            self.assertTrue(any(175 <= frame <= 185 for frame in settings.scene_cut_frames))
            video = probe_video(source, ROOT)
            output = root / "enhanced.mp4"
            render_full_resumable(
                video, entries, settings, output_path=output,
                project_dir=project, app_root=ROOT,
            )
            rendered = probe_video(output, ROOT)
            self.assertAlmostEqual(rendered.duration_seconds, 12.0, delta=0.08)
            self.assertTrue(rendered.has_audio)
            frames = subprocess.run(
                [ffprobe_path(ROOT), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output)],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(int(frames.stdout.strip()), 360)

    def test_multiple_freezes_in_one_chunk_preserve_frames_and_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=30:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source),
                ], check=True,
            )
            entries = [
                OverlayEntry("A", 15, 45, "ONE", visual_action="FREEZE", action_start_frame=15, action_end_frame=33, font_size_px=24, wrapped_text="ONE", effect="CLEAN_SHADOW", animation="FADE_ONLY"),
                OverlayEntry("B", 75, 105, "TWO", visual_action="FREEZE", action_start_frame=75, action_end_frame=93, font_size_px=24, wrapped_text="TWO", effect="CLEAN_SHADOW", animation="FADE_ONLY"),
            ]
            output = root / "two_freezes.mp4"
            render_clip(
                source, entries, ProjectSettings(output_width=160, output_height=90, x264_preset="ultrafast", crf=30),
                start_frame=0, end_frame=120, output_path=output, work_dir=root / "work", app_root=ROOT,
            )
            rendered = probe_video(output, ROOT)
            self.assertAlmostEqual(rendered.duration_seconds, 4.0, delta=0.08)
            self.assertTrue(rendered.has_audio)
            frames = subprocess.run(
                [ffprobe_path(ROOT), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output)],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(int(frames.stdout.strip()), 120)

    def test_ten_minute_render_preserves_18000_frames_duration_and_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "ten_minutes.mp4"
            subprocess.run(
                [
                    ffmpeg_path(ROOT), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=black:s=160x90:r=30:d=600",
                    "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=600",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", "-c:a", "aac", "-b:a", "48k", str(source),
                ], check=True,
            )
            video = probe_video(source, ROOT)
            output = root / "accepted.mp4"
            render_full_resumable(
                video, [], ProjectSettings(output_width=160, output_height=90, chunk_target_seconds=120, x264_preset="ultrafast", crf=35),
                output_path=output, project_dir=root / "project", app_root=ROOT,
            )
            rendered = probe_video(output, ROOT)
            self.assertAlmostEqual(rendered.duration_seconds, 600.0, delta=0.08)
            self.assertTrue(rendered.has_audio)
            frames = subprocess.run(
                [ffprobe_path(ROOT), "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(output)],
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(int(frames.stdout.strip()), 18_000)


if __name__ == "__main__":
    unittest.main()
