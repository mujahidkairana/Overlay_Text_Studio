from __future__ import annotations

import copy
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

from overlay_studio.editorial import editorial_report, plan_editorial_actions, write_editorial_report
from overlay_studio.layout import balanced_wrap, calculate_font_size, plan_layout_and_styles, prepare_text_layout
from overlay_studio.media import MediaError, extract_analysis_frames, probe_video
from overlay_studio.models import (
    ANIMATIONS,
    EFFECTS,
    POSITIONS,
    PRIORITIES,
    SEMANTIC_TYPES,
    VISUAL_ACTIONS,
    ProjectSettings,
)
from overlay_studio.project import apply_edited_table, load_project_data, save_project
from overlay_studio.render import (
    RenderCancelled,
    ensure_free_space,
    estimate_required_bytes,
    render_clip,
    render_full_resumable,
    select_fast_encoder,
    validate_sfx_assets,
)
from overlay_studio.safety import analyze_entries
from overlay_studio.scene_analysis import detect_scene_cuts
from overlay_studio.srt import SRTValidationError, load_srt, reading_load_warnings
from overlay_studio.timing import TimingValidationError, entries_to_table, load_entries


APP_ROOT = Path(__file__).resolve().parent


st.set_page_config(page_title="Overlay Text Studio", page_icon="✦", layout="wide")
st.markdown(
    """
<style>
  .block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1480px;}
  h1, h2, h3 {letter-spacing: -0.02em;}
  div[data-testid="stMetric"] {background: #111827; border: 1px solid #263244; padding: 12px 16px; border-radius: 12px;}
  .safe-note {padding: 12px 14px; border-radius: 10px; background: #0f2431; border: 1px solid #1f536b;}
  .step-note {color: #9fb0c3; font-size: 0.93rem; margin-top: -0.5rem;}
</style>
""",
    unsafe_allow_html=True,
)


def _initial_state() -> None:
    defaults = {
        "video_path": "",
        "timing_path": "",
        "srt_path": "",
        "output_root": str((Path.home() / "Videos" / "OverlayTextStudio").resolve()),
        "video_info": None,
        "entries": None,
        "settings": None,
        "warnings": [],
        "reroll": 0,
        "random_seed": random.SystemRandom().randint(1, 2_147_483_647),
        "edited_table": None,
        "preview_path": "",
        "project_path": "",
        "final_output_path": "",
        "project_name": "Overlay_Project",
        "resolution": "Auto — match source",
        "analysis_fps": 0.5,
        "font_name": "Segoe UI Semibold",
        "accent_color": "#FFD84D",
        "safe_top_percent": 5.5,
        "chunk_seconds": 60,
        "encoder": "libx264",
        "x264_preset": "veryfast",
        "crf": 18,
        "render_status_path": "",
        "density_preset": "STANDARD",
        "editorial_report": None,
        "sfx_folder": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _browse_file(title: str, types: list[tuple[str, str]]) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result = filedialog.askopenfilename(title=title, filetypes=types)
        root.destroy()
        return result
    except Exception as exc:
        st.warning(f"Browse window could not open ({exc}). Paste the full path instead.")
        return ""


def _browse_directory(title: str) -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        result = filedialog.askdirectory(title=title)
        root.destroy()
        return result
    except Exception as exc:
        st.warning(f"Browse window could not open ({exc}). Paste the full path instead.")
        return ""


def _path_row(label: str, key: str, types: list[tuple[str, str]] | None = None) -> None:
    text_col, button_col = st.columns([8, 1.35], vertical_alignment="bottom")

    # IMPORTANT: Process Browse before instantiating the text_input that owns
    # ``key``. Streamlit forbids assigning st.session_state[key] after a
    # widget with the same key has been instantiated in the current run.
    # Rendering the button first keeps Browse updates legal while preserving
    # the same visual column layout.
    with button_col:
        if st.button("Browse", key=f"browse_{key}", width="stretch"):
            selected = _browse_directory(label) if types is None else _browse_file(label, types)
            if selected:
                st.session_state[key] = selected
                if key == "video_path":
                    st.session_state.project_name = Path(selected).stem
                    st.session_state.video_info = None
                    st.session_state.entries = None
                    st.session_state.edited_table = None
                elif key == "timing_path":
                    st.session_state.entries = None
                    st.session_state.edited_table = None
                elif key == "srt_path":
                    st.session_state.entries = None
                    st.session_state.edited_table = None
                st.rerun()

    with text_col:
        st.text_input(label, key=key)


def _settings_from_ui(
    video_stem: str,
    source_width: int = 1920,
    source_height: int = 1080,
) -> ProjectSettings:
    resolution = st.session_state.get("resolution", "Auto — match source")
    if resolution.startswith("1080p"):
        width, height = 1920, 1080
    elif resolution.startswith("4K"):
        width, height = 3840, 2160
    elif source_height >= 1800 or source_width >= 3200:
        width, height = 3840, 2160
    else:
        width, height = 1920, 1080
    return ProjectSettings(
        project_name=st.session_state.get("project_name") or video_stem,
        output_width=width,
        output_height=height,
        analysis_fps=float(st.session_state.get("analysis_fps", 0.5)),
        random_seed=int(st.session_state.get("random_seed", 20260807)),
        font_name=st.session_state.get("font_name", "Segoe UI Semibold"),
        accent_color=st.session_state.get("accent_color", "#FFD84D"),
        safe_top_percent=float(st.session_state.get("safe_top_percent", 5.5)),
        chunk_target_seconds=int(st.session_state.get("chunk_seconds", 60)),
        encoder=st.session_state.get("encoder", "auto"),
        x264_preset=st.session_state.get("x264_preset", "veryfast"),
        crf=int(st.session_state.get("crf", 18)),
        style_preset="YOUTUBE_PRO",
        parallel_analysis_workers=min(4, max(1, (os.cpu_count() or 2) // 2)),
        density_preset=st.session_state.get("density_preset", "STANDARD"),
        sfx_folder=st.session_state.get("sfx_folder", ""),
    )


def _next_output_path(output_root: str, video_stem: str) -> Path:
    root = Path(output_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    base = root / f"{video_stem}_WITH_OVERLAY_TEXT.mp4"
    if not base.exists():
        return base
    for number in range(2, 1000):
        candidate = root / f"{video_stem}_WITH_OVERLAY_TEXT_{number:02d}.mp4"
        if not candidate.exists():
            return candidate
    raise ValueError("Could not choose a free automatic output filename.")


def _run_progress(callable_with_progress):
    bar = st.progress(0.0)
    status = st.empty()
    started = time.monotonic()

    def update(value: float, label: str) -> None:
        normalized = max(0.0, min(1.0, float(value)))
        bar.progress(normalized)
        elapsed = time.monotonic() - started
        if normalized >= 0.02 and normalized < 1.0:
            remaining = max(0.0, elapsed * (1.0 - normalized) / normalized)
            status.caption(f"{label} · elapsed {elapsed / 60:.1f} min · about {remaining / 60:.1f} min left")
        else:
            status.caption(label)

    result = callable_with_progress(update)
    bar.progress(1.0)
    return result


def _open_project(path_text: str) -> None:
    if not path_text.strip():
        raise ValueError("Choose an existing project.json file first.")
    path = Path(path_text).expanduser().resolve()
    if not path.is_file() or path.name.lower() != "project.json":
        raise ValueError("Choose a valid Overlay Text Studio project.json file.")
    video, timing_path, srt_path, entries, settings = load_project_data(path)
    if not Path(video.path).is_file():
        raise ValueError(f"The saved source video is no longer at: {video.path}")
    st.session_state.video_path = video.path
    st.session_state.timing_path = timing_path
    st.session_state.srt_path = srt_path
    st.session_state.output_root = str(path.parent.parent)
    st.session_state.project_name = settings.project_name
    st.session_state.video_info = video
    st.session_state.entries = entries
    st.session_state.settings = settings
    st.session_state.edited_table = entries_to_table(entries)
    st.session_state.random_seed = settings.random_seed
    st.session_state.resolution = (
        "1080p — 1920×1080" if settings.output_height == 1080 else "4K — 3840×2160"
    )
    st.session_state.analysis_fps = settings.analysis_fps
    st.session_state.font_name = settings.font_name
    st.session_state.accent_color = settings.accent_color
    st.session_state.safe_top_percent = settings.safe_top_percent
    st.session_state.chunk_seconds = settings.chunk_target_seconds
    st.session_state.encoder = settings.encoder
    st.session_state.x264_preset = settings.x264_preset
    st.session_state.crf = settings.crf
    st.session_state.density_preset = settings.density_preset
    st.session_state.sfx_folder = settings.sfx_folder
    report_path = path.parent / "editorial_plan.json"
    st.session_state.editorial_report = (
        json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else None
    )


_initial_state()

st.title("Overlay Text Studio")
st.caption("Offline animated overlays for 30‑fps YouTube long videos · exact timing · safe top placement · resumable export")
st.markdown(
    '<div class="safe-note">Recommended settings are already selected. Add the final video, SRT subtitles, and overlay sheet, then click AUTO ENHANCE VIDEO. Everything else is automatic and offline.</div>',
    unsafe_allow_html=True,
)

with st.expander("Continue an existing project", expanded=False):
    _path_row("Saved project.json", "project_path", [("Overlay project", "project.json"), ("JSON", "*.json")])
    if st.button("Open existing project", width="stretch"):
        try:
            _open_project(st.session_state.project_path)
            st.success("Project opened with its saved styles and random seed.")
            st.rerun()
        except (OSError, ValueError, KeyError) as exc:
            st.error(str(exc))

with st.sidebar:
    st.header("Output")
    st.selectbox(
        "Final resolution",
        ["Auto — match source", "1080p — 1920×1080", "4K — 3840×2160"],
        index=None,
        key="resolution",
        help="Recommended: avoids slow, unnecessary upscaling while keeping native 4K sources in 4K.",
    )
    st.color_picker("Accent color", key="accent_color")
    with st.expander("Advanced settings"):
        st.selectbox(
            "Safe-zone analysis",
            [0.5, 1.0, 2.0],
            index=None,
            key="analysis_fps",
            format_func=lambda x: f"{x:g} sample(s)/second",
            help="0.5 is the recommended fast setting. Cached frames are reused.",
        )
        st.text_input("Font family", key="font_name")
        st.slider("Top safe margin", min_value=4.0, max_value=10.0, step=0.5, key="safe_top_percent", help="Keeps text away from YouTube/player edges.")
        st.number_input("Random style seed", min_value=1, step=1, key="random_seed")
        st.selectbox(
            "Encoder",
            ["auto", "libx264", "h264_qsv", "h264_nvenc", "h264_amf"],
            index=None,
            key="encoder",
            format_func=lambda x: {
                "auto": "Auto benchmark — recommended",
                "libx264": "CPU — reliable",
                "h264_qsv": "Intel Quick Sync",
                "h264_nvenc": "NVIDIA NVENC",
                "h264_amf": "AMD AMF",
            }[x],
        )
        st.selectbox("CPU speed", ["veryfast", "faster", "fast", "medium"], index=None, key="x264_preset")
        st.slider("Quality (lower is better)", min_value=16, max_value=24, key="crf")
        st.slider("Resume chunk length", min_value=30, max_value=120, step=15, key="chunk_seconds")
        st.selectbox(
            "Editorial density",
            ["CALM", "STANDARD", "ENERGETIC"],
            index=None,
            key="density_preset",
            format_func=lambda value: value.title(),
        )

st.subheader("Add your content")
st.markdown('<p class="step-note">Choose three files once. The app validates subtitle reading load, plans safe overlays, renders, and verifies automatically.</p>', unsafe_allow_html=True)
template_path = APP_ROOT / "templates" / "overlay_template.csv"
st.download_button(
    "Download sample timing template",
    data=template_path.read_bytes(),
    file_name="overlay_template.csv",
    mime="text/csv",
)
_path_row("Source video", "video_path", [("Video", "*.mp4 *.mov *.mkv *.m4v"), ("All files", "*.*")])
_path_row("Subtitle SRT", "srt_path", [("Subtitles", "*.srt"), ("All files", "*.*")])
_path_row("Overlay timing CSV/XLSX", "timing_path", [("Timing sheet", "*.csv *.xlsx *.xlsm"), ("All files", "*.*")])
with st.expander("Optional output location"):
    _path_row("Project and output folder", "output_root", None)
    _path_row("Licensed local SFX folder", "sfx_folder", None)

files_ready = bool(
    st.session_state.video_path.strip()
    and st.session_state.srt_path.strip()
    and st.session_state.timing_path.strip()
)
if st.button(
    "AUTO ENHANCE VIDEO",
    type="primary",
    width="stretch",
    disabled=not files_ready,
):
    try:
        if not st.session_state.video_path.strip():
            raise MediaError("Choose a source video first.")
        if not st.session_state.timing_path.strip():
            raise TimingValidationError("Choose an overlay timing CSV/XLSX file first.")
        if not st.session_state.srt_path.strip():
            raise SRTValidationError("Choose an SRT subtitle file first.")
        video = probe_video(st.session_state.video_path, APP_ROOT)
        captions = load_srt(st.session_state.srt_path)
        entries, warnings = load_entries(
            st.session_state.timing_path,
            video_duration_frames=video.duration_frames_30,
        )
        warnings.extend(reading_load_warnings(entries, captions))
        settings = _settings_from_ui(
            Path(video.path).stem,
            source_width=video.width,
            source_height=video.height,
        )
        settings.project_name = Path(video.path).stem
        project_dir = settings.project_dir(st.session_state.output_root)
        if settings.encoder == "auto":
            settings.encoder, encoder_timings = select_fast_encoder(
                APP_ROOT,
                project_dir / "cache" / "encoder_benchmark.json",
                crf=settings.crf,
            )
            st.caption(
                f"Automatic encoder: {settings.encoder} "
                f"(benchmarked {len(encoder_timings)} available option(s))"
            )
        prepare_text_layout(entries, settings)

        def automatic_plan(progress):
            progress(0.01, "Detecting and caching hard scene cuts")
            settings.scene_cut_frames = detect_scene_cuts(
                video.path,
                project_dir / "cache" / "scene_cuts.json",
                app_root=APP_ROOT,
                fps=settings.fps,
            )
            frames = extract_analysis_frames(
                video.path,
                project_dir / "cache" / "analysis_frames",
                sample_fps=settings.analysis_fps,
                app_root=APP_ROOT,
                duration_seconds=video.duration_seconds,
                progress=progress,
            )
            analyze_entries(
                entries,
                frames,
                sample_fps=settings.analysis_fps,
                progress=progress,
                max_workers=settings.parallel_analysis_workers,
            )
            plan_layout_and_styles(entries, settings, reroll=st.session_state.reroll)
            plan_editorial_actions(entries, settings, captions=captions)
            warnings.extend(validate_sfx_assets(entries, settings, project_dir, APP_ROOT))
            st.session_state.editorial_report = editorial_report(
                entries, video.duration_seconds
            )
            write_editorial_report(
                project_dir / "editorial_plan.json", entries, video.duration_seconds
            )
            return frames

        _run_progress(automatic_plan)
        st.session_state.video_info = video
        st.session_state.entries = entries
        st.session_state.settings = settings
        st.session_state.warnings = warnings
        st.session_state.edited_table = entries_to_table(entries)
        st.session_state.preview_path = ""
        saved = save_project(
            project_dir,
            video=video,
            timing_path=st.session_state.timing_path,
            entries=entries,
            settings=settings,
            srt_path=st.session_state.srt_path,
        )
        output_path = _next_output_path(
            st.session_state.output_root, Path(video.path).stem
        )
        st.session_state.final_output_path = str(output_path)
        cancel_file = project_dir / "STOP_RENDER.REQUEST"
        status_file = project_dir / "render_status.json"
        cancel_file.unlink(missing_ok=True)
        status_file.unlink(missing_ok=True)
        ensure_free_space(project_dir, video, settings)
        log_path = project_dir / "background_render.log"
        command = [
            sys.executable,
            "-m",
            "overlay_studio.worker",
            "--project",
            str(saved["project"]),
            "--output",
            str(output_path),
            "--status",
            str(status_file),
            "--stop",
            str(cancel_file),
        ]
        status_file.write_text(
            json.dumps(
                {
                    "state": "running",
                    "progress": 0.0,
                    "label": "Launching background render",
                    "output": str(output_path),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        with log_path.open("ab", buffering=0) as log:
            process = subprocess.Popen(
                command,
                cwd=APP_ROOT,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
                start_new_session=os.name != "nt",
            )
        st.session_state.render_status_path = str(status_file)
        st.success("Background render started. You can safely leave this page open.")
    except (TimingValidationError, SRTValidationError, MediaError, OSError, ValueError) as exc:
        st.error(str(exc))

if st.session_state.video_info is not None:
    video = st.session_state.video_info
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Video", f"{video.width}×{video.height}")
    c2.metric("Source FPS", f"{video.source_fps:.3f}")
    c3.metric("Duration", f"{video.duration_seconds / 60:.1f} min")
    c4.metric("Overlay rows", len(st.session_state.entries or []))
    if abs(video.source_fps - 30.0) > 0.05:
        st.warning("The timing sheet is 30 fps; export will be normalized to constant 30 fps.")
    for warning in st.session_state.warnings:
        st.warning(warning)
    report = st.session_state.get("editorial_report")
    if report:
        with st.expander("Creative Variation Report", expanded=False):
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("Events/min", report.get("events_per_minute", 0))
            r2.metric("Punch-ins", report.get("punch_in_count", 0))
            r3.metric("Freezes", report.get("freeze_count", 0))
            r4.metric("Longest quiet gap", f"{report.get('longest_quiet_interval_seconds', 0):g}s")
            st.caption("Local editing-pattern report only; this is not a monetization score.")
            if report.get("density_status") == "OVER_TARGET":
                st.warning(
                    "The supplied overlay timing exceeds the recommended 4-7 events/minute in at least one interval. Extra strong effects were suppressed; source overlay rows were preserved."
                )
            st.json(report, expanded=False)

@st.fragment(run_every=2)
def _render_monitor() -> None:
    status_text = st.session_state.get("render_status_path", "")
    if not status_text:
        return
    status_path = Path(status_text)
    if not status_path.exists():
        return
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    state = status.get("state", "running")
    progress = float(status.get("progress", 0.0))
    label = str(status.get("label", "Rendering"))
    if state == "running":
        st.progress(max(0.0, min(1.0, progress)), text=label)
        if st.button("Stop background render safely", width="stretch"):
            status_path.parent.joinpath("STOP_RENDER.REQUEST").write_text(
                "stop", encoding="utf-8"
            )
            st.warning("Stop requested. Completed chunks will remain reusable.")
    elif state == "complete":
        output = Path(str(status.get("output", "")))
        st.success(f"Final video verified and ready: {output}")
        if output.exists():
            st.video(str(output))
    elif state == "stopped":
        st.warning(label)
    else:
        st.error(label)


_render_monitor()


if st.session_state.edited_table is not None and st.checkbox(
    "Show optional expert adjustments", value=False
):
    entries = st.session_state.entries
    table = st.session_state.edited_table
    high = sum(entry.confidence == "HIGH" for entry in entries)
    medium = sum(entry.confidence == "MEDIUM" for entry in entries)
    review = sum(entry.confidence == "REVIEW" for entry in entries)
    c1, c2, c3 = st.columns(3)
    c1.metric("High confidence", high)
    c2.metric("Medium confidence", medium)
    c3.metric("Needs review", review)

    st.caption("The automatic plan is ready. Manual changes below are optional.")
    edited = st.data_editor(
        table,
        width="stretch",
        hide_index=True,
        height=430,
        column_config={
            "POSITION": st.column_config.SelectboxColumn("POSITION", options=list(POSITIONS)),
            "ANIMATION": st.column_config.SelectboxColumn("ANIMATION", options=list(ANIMATIONS)),
            "EFFECT": st.column_config.SelectboxColumn("EFFECT", options=list(EFFECTS)),
            "TYPE": st.column_config.SelectboxColumn("TYPE", options=list(SEMANTIC_TYPES)),
            "PRIORITY": st.column_config.SelectboxColumn("PRIORITY", options=list(PRIORITIES)),
            "VISUAL_ACTION": st.column_config.SelectboxColumn(
                "VISUAL_ACTION", options=list(VISUAL_ACTIONS)
            ),
            "SAFETY_SCORE": st.column_config.NumberColumn("SAFETY_SCORE", disabled=True, format="%.1f"),
            "CONFIDENCE": st.column_config.TextColumn("CONFIDENCE", disabled=True),
            "NOTE": st.column_config.TextColumn("NOTE", disabled=True),
        },
        disabled=["SCENE_ID", "START_TIME_30FPS", "END_TIME_30FPS", "SAFETY_SCORE", "CONFIDENCE", "NOTE"],
        key="overlay_editor",
    )
    apply_col, reroll_col = st.columns(2)
    with apply_col:
        if st.button("Apply and save table changes", width="stretch"):
            try:
                apply_edited_table(entries, edited)
                for entry in entries:
                    entry.wrapped_text = balanced_wrap(entry.text)
                    if entry.font_size_px <= 0:
                        entry.font_size_px = calculate_font_size(entry.wrapped_text, st.session_state.settings.output_height)
                st.session_state.edited_table = entries_to_table(entries)
                project_dir = st.session_state.settings.project_dir(st.session_state.output_root)
                save_project(
                    project_dir,
                    video=st.session_state.video_info,
                    timing_path=st.session_state.timing_path,
                    entries=entries,
                    settings=st.session_state.settings,
                    srt_path=st.session_state.srt_path,
                )
                st.success("Changes applied and saved.")
            except (TimingValidationError, OSError, ValueError) as exc:
                st.error(str(exc))
    with reroll_col:
        if st.button("Reroll unlocked animations and effects", width="stretch"):
            try:
                st.session_state.reroll += 1
                apply_edited_table(entries, edited)
                plan_layout_and_styles(entries, st.session_state.settings, reroll=st.session_state.reroll)
                st.session_state.edited_table = entries_to_table(entries)
                st.rerun()
            except (TimingValidationError, ValueError) as exc:
                st.error(str(exc))

    st.subheader("3. Preview one overlay")
    scene_ids = [entry.scene_id for entry in entries if entry.enabled]
    selected_id = st.selectbox("Overlay to preview", scene_ids)
    preview_quality = st.selectbox(
        "Preview quality",
        ["1080p (recommended for sharpness)", "720p (timing only)"],
    )
    if st.button("Render selected preview", width="stretch"):
        try:
            apply_edited_table(entries, edited)
            selected = next(entry for entry in entries if entry.scene_id == selected_id)
            preview_settings = copy.deepcopy(st.session_state.settings)
            if preview_quality.startswith("720p"):
                preview_settings.output_width, preview_settings.output_height = 1280, 720
            else:
                preview_settings.output_width, preview_settings.output_height = 1920, 1080
            ratio = preview_settings.output_height / st.session_state.settings.output_height
            preview_entries = copy.deepcopy(entries)
            for entry in preview_entries:
                entry.font_size_px = max(28, round(entry.font_size_px * ratio))
            project_dir = st.session_state.settings.project_dir(st.session_state.output_root)
            preview_path = project_dir / "previews" / f"{selected.scene_id}_{preview_settings.output_height}p.mp4"
            start = max(0, selected.start_frame - 30)
            end = min(st.session_state.video_info.duration_frames_30, selected.end_frame + 30)

            def do_preview(progress):
                return render_clip(
                    st.session_state.video_info.path,
                    preview_entries,
                    preview_settings,
                    start_frame=start,
                    end_frame=end,
                    output_path=preview_path,
                    work_dir=project_dir / "previews" / "ass",
                    app_root=APP_ROOT,
                    include_audio=True,
                    progress=progress,
                )

            result = _run_progress(do_preview)
            st.session_state.preview_path = str(result)
        except (MediaError, OSError, ValueError) as exc:
            st.error(str(exc))
    if st.session_state.preview_path and Path(st.session_state.preview_path).exists():
        st.video(st.session_state.preview_path)

    st.subheader("4. Export the full video")
    default_name = Path(st.session_state.video_info.path).stem + "_WITH_OVERLAY_TEXT.mp4"
    if not st.session_state.final_output_path:
        st.session_state.final_output_path = str((Path(st.session_state.output_root) / default_name).resolve())
    output_path = st.text_input("Final output path", key="final_output_path")
    st.caption("If the app or PC stops, completed chunks remain cached. Start the same export again to resume.")
    estimated = estimate_required_bytes(st.session_state.video_info, st.session_state.settings)
    st.caption(f"Conservative temporary-space estimate: about {estimated / 1024**3:.1f} GB.")
    output_exists = bool(output_path.strip()) and Path(output_path).expanduser().exists()
    overwrite_ok = True
    if output_exists:
        st.warning("A file already exists at the final output path.")
        overwrite_ok = st.checkbox("Replace the existing output after the new file passes verification")

    project_dir = st.session_state.settings.project_dir(st.session_state.output_root)
    cancel_file = project_dir / "STOP_RENDER.REQUEST"
    render_col, stop_col = st.columns([3, 1])
    with stop_col:
        if st.button("Stop safely", width="stretch"):
            cancel_file.parent.mkdir(parents=True, exist_ok=True)
            cancel_file.write_text("stop", encoding="utf-8")
            st.warning("Stop requested. The active step will end and completed chunks will remain cached.")
    with render_col:
        start_full_render = st.button(
            "Render full overlay video",
            type="primary",
            width="stretch",
            disabled=not overwrite_ok,
        )
    if start_full_render:
        try:
            if not output_path.strip():
                raise ValueError("Choose a final output path first.")
            if Path(output_path).suffix.lower() != ".mp4":
                raise ValueError("Final output must use the .mp4 extension.")
            cancel_file.unlink(missing_ok=True)
            apply_edited_table(entries, edited)
            settings = st.session_state.settings
            ensure_free_space(project_dir, st.session_state.video_info, settings)
            save_project(
                project_dir,
                video=st.session_state.video_info,
                timing_path=st.session_state.timing_path,
                entries=entries,
                settings=settings,
                srt_path=st.session_state.srt_path,
            )

            def do_full(progress):
                return render_full_resumable(
                    st.session_state.video_info,
                    entries,
                    settings,
                    output_path=output_path,
                    project_dir=project_dir,
                    app_root=APP_ROOT,
                    progress=progress,
                    cancel_check=cancel_file.exists,
                )

            final_path = _run_progress(do_full)
            st.success(f"Final video verified and ready: {final_path}")
            verification = project_dir / "final_verification.json"
            if verification.exists():
                st.caption(f"Verification report: {verification}")
        except RenderCancelled as exc:
            st.warning(str(exc))
        except (TimingValidationError, MediaError, OSError, ValueError) as exc:
            st.error(str(exc))
        finally:
            cancel_file.unlink(missing_ok=True)
