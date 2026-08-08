from __future__ import annotations

import argparse
from pathlib import Path

from .ass import write_ass
from .layout import plan_layout_and_styles
from .media import extract_analysis_frames, probe_video
from .models import ProjectSettings
from .project import load_project, save_project
from .render import render_full_resumable
from .safety import analyze_entries
from .timing import load_entries


def _progress(value: float, label: str) -> None:
    print(f"[{value * 100:6.1f}%] {label}", flush=True)


def command_plan(args: argparse.Namespace) -> int:
    app_root = Path(__file__).resolve().parents[1]
    video = probe_video(args.video, app_root)
    entries, warnings = load_entries(args.timing, video_duration_frames=video.duration_frames_30)
    for warning in warnings:
        print(f"WARNING: {warning}")
    settings = ProjectSettings(
        project_name=args.project_name or Path(args.video).stem,
        output_width=args.width,
        output_height=args.height,
        analysis_fps=args.analysis_fps,
        random_seed=args.seed,
    )
    project_dir = Path(args.project_dir).resolve()
    frames = extract_analysis_frames(
        video.path,
        project_dir / "cache" / "analysis_frames",
        sample_fps=settings.analysis_fps,
        app_root=app_root,
        duration_seconds=video.duration_seconds,
        progress=_progress,
    )
    analyze_entries(entries, frames, sample_fps=settings.analysis_fps, progress=_progress)
    plan_layout_and_styles(entries, settings)
    outputs = save_project(
        project_dir,
        video=video,
        timing_path=args.timing,
        entries=entries,
        settings=settings,
    )
    ass_path = write_ass(project_dir / "resolved_overlay_plan.ass", entries, settings)
    print(f"Project: {outputs['project']}")
    print(f"ASS plan: {ass_path}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    app_root = Path(__file__).resolve().parents[1]
    project_path = Path(args.project).resolve()
    video, _, entries, settings = load_project(project_path)
    output = Path(args.output).resolve()
    render_full_resumable(
        video,
        entries,
        settings,
        output_path=output,
        project_dir=project_path.parent,
        app_root=app_root,
        progress=_progress,
    )
    print(f"Output: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline animated overlay text planner and renderer")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="validate timing and build an automatic overlay plan")
    plan.add_argument("--video", required=True)
    plan.add_argument("--timing", required=True)
    plan.add_argument("--project-dir", required=True)
    plan.add_argument("--project-name", default="")
    plan.add_argument("--width", type=int, default=3840)
    plan.add_argument("--height", type=int, default=2160)
    plan.add_argument("--analysis-fps", type=float, default=1.0)
    plan.add_argument("--seed", type=int, default=20260807)
    plan.set_defaults(func=command_plan)

    render = subparsers.add_parser("render", help="render a saved project with resume-safe chunks")
    render.add_argument("--project", required=True)
    render.add_argument("--output", required=True)
    render.set_defaults(func=command_render)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

