from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from overlay_studio.media import MediaError, ffmpeg_path, ffprobe_path


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
REPORT = LOG_DIR / "SYSTEM_CHECK.txt"


def _run(command: list[str]) -> tuple[bool, str]:
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    return completed.returncode == 0, (completed.stdout + completed.stderr).strip()


def build_report() -> tuple[list[str], bool]:
    lines = [
        "OVERLAY TEXT STUDIO v2 SYSTEM CHECK",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Operating system: {platform.platform()}",
        f"Python: {sys.version.splitlines()[0]}",
        f"Runtime: {sys.executable}",
        "",
    ]
    healthy = True
    for module in ("streamlit", "pandas", "numpy", "PIL", "openpyxl", "cv2"):
        found = importlib.util.find_spec(module) is not None
        lines.append(f"Module {module}: {'OK' if found else 'MISSING'}")
        healthy = healthy and found

    lines.append("")
    try:
        ffmpeg = ffmpeg_path(ROOT)
        ok_ffmpeg, ffmpeg_text = _run([ffmpeg, "-hide_banner", "-version"])
        lines.append(f"FFmpeg: {'OK' if ok_ffmpeg else 'FAILED'} — {ffmpeg}")
        if ffmpeg_text:
            lines.append(f"FFmpeg version: {ffmpeg_text.splitlines()[0]}")
        ok_filters, filters = _run([ffmpeg, "-hide_banner", "-filters"])
        has_ass = ok_filters and any(name in filters for name in (" ass ", " subtitles "))
        lines.append(f"FFmpeg ASS/subtitles filter: {'OK' if has_ass else 'MISSING'}")
        healthy = healthy and ok_ffmpeg and has_ass
    except MediaError as exc:
        lines.append(f"FFmpeg: MISSING — {exc}")
        healthy = False

    try:
        ffprobe = ffprobe_path(ROOT)
        ok_ffprobe, ffprobe_text = _run([ffprobe, "-hide_banner", "-version"])
        lines.append(f"FFprobe: {'OK' if ok_ffprobe else 'FAILED'} — {ffprobe}")
        if ffprobe_text:
            lines.append(f"FFprobe version: {ffprobe_text.splitlines()[0]}")
        healthy = healthy and ok_ffprobe
    except MediaError as exc:
        lines.append(f"FFprobe: MISSING — {exc}")
        healthy = False

    shared_pointer = ROOT / "shared_components_path.txt"
    shared = shared_pointer.read_text(encoding="utf-8-sig").strip() if shared_pointer.exists() else "NOT CONFIGURED"
    lines += ["", f"Shared components: {shared}"]
    check_path = Path(shared) if shared != "NOT CONFIGURED" else ROOT
    try:
        usage = shutil.disk_usage(check_path)
        lines.append(f"Free space: {usage.free / 1024**3:.1f} GB")
        if usage.free < 2 * 1024**3:
            lines.append("Disk space warning: less than 2 GB free")
    except OSError as exc:
        lines.append(f"Disk space check failed: {exc}")
        healthy = False

    lines += ["", f"Overall result: {'READY' if healthy else 'NEEDS ATTENTION'}"]
    return lines, healthy


def make_diagnostic_zip(report_path: Path) -> Path:
    destination = LOG_DIR / f"Overlay_Text_Studio_Diagnostics_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    allowed = [report_path, ROOT / "requirements.txt", ROOT / "VERSION.txt"]
    allowed.extend(path for path in LOG_DIR.glob("*.log") if path.is_file())
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in allowed:
            if path.exists() and path.stat().st_size <= 5 * 1024 * 1024:
                archive.write(path, arcname=path.name)
        context = {
            "app_root": str(ROOT),
            "python": sys.executable,
            "platform": platform.platform(),
            "environment_keys": sorted(
                key for key in os.environ if key.startswith(("OVERLAY_", "MUZ_"))
            ),
        }
        archive.writestr("diagnostic_context.json", json.dumps(context, indent=2))
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostic-zip", action="store_true")
    args = parser.parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lines, healthy = build_report()
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nReport saved: {REPORT}")
    if args.diagnostic_zip:
        print(f"Diagnostic ZIP: {make_diagnostic_zip(REPORT)}")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())

