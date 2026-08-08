# Overlay Text Studio v2.3

## Development branches

Every new working branch uses this order:

    overlay-text-studio/<feature>/YYYY/MM/DD/NN

Example:

    overlay-text-studio/caption-export/2026/08/08/01

Create the next numbered branch automatically:

    .\scripts\new_branch.ps1 -Feature "caption export"

See CONTRIBUTING.md for the complete rules. GitHub checks this format on every
pull request.

## Portable GitHub checkout

Clone this repository to any normal folder on a Windows laptop, double-click
`SETUP_ONCE.bat`, then use `START_APP.bat`. Setup discovers or installs a
compatible Python runtime and FFmpeg without requiring administrator access.

No committed file is required to contain an absolute machine path. Setup creates
local runtime pointer files; those files, logs, caches, previews, and rendered
media are excluded by `.gitignore`.

For unattended setup:

```powershell
SETUP_ONCE.bat "D:\\PortableApps\\Shared_Components"
```

The optional `OVERLAY_STUDIO_SHARED_COMPONENTS` environment variable can also
define the component location. The automatic planner reserves the lower 35% of
the frame for standard captions/subtitles.

Overlay Text Studio is a local Windows app for polished animated text overlays on 30-fps YouTube long videos. It uses exact `HH:MM:SS.FF` timing, evaluates top-left/top-center/top-right against the real text footprint, gives each overlay a stable safe resting place, and exports a verified MP4 without changing the source.

## Start here

1. Clone the repository or extract the complete ZIP to any normal folder.
2. Double-click `SETUP_ONCE.bat`.
3. Choose a drive/folder for `Shared_Components` when asked. No administrator permission is required.
4. When setup finishes, double-click `START_APP.bat`.
5. In the app, download the sample timing template or select your own CSV/XLSX.

Normal use is fully offline after setup. Keep `Shared_Components` available because the app runtime and FFmpeg may be stored there.

## Shared setup decision order

Each component follows the same explicit precedence:

| Priority | State found | Action |
| ---: | --- | --- |
| 1 | Healthy compatible component is already installed | Reuse it; do not download or reinstall |
| 2 | Not installed, but a complete installer/archive/wheelhouse is cached | Install from the shared cache without downloading |
| 3 | Neither installed nor cached | Download once into `Shared_Components\Downloads` or the shared wheelhouse, then install |

The selected path is remembered in `shared_components_path.txt`. Python, FFmpeg, dependency wheels, pip cache, app runtime, and setup logs all use the selected shared space. Runtime folders are keyed by Python version plus the requirements hash, so another compatible app using the same lock file can reuse the healthy installed environment. A compatible system-wide Python or FFmpeg/FFprobe installation is also reused when already healthy.

Run `CHECK_SETUP.bat` to verify Python packages, both FFmpeg tools, the ASS text filter, and disk space. Run `MAKE_DIAGNOSTIC_ZIP.bat` if a support report is needed.

## Timing input

Use the in-app **Download sample timing template** button or `templates/overlay_template.csv`.

| Column | Required | Example |
| --- | --- | --- |
| `SCENE_ID` | Recommended | `OVERLAY_001` |
| `START_TIME_30FPS` | Yes | `00:01:12.05` |
| `END_TIME_30FPS` | Yes | `00:01:16.20` |
| `ON_SCREEN_TEXT` | Yes | `THE CLUE WAS HIDDEN` |

Start is inclusive and end is exclusive. Frame fields must be `00–29`. Integer frame numbers are also accepted. Explicit `\N` or spreadsheet line breaks are allowed for a maximum of two lines; a third line is rejected with a clear row error instead of being silently removed.

## Recommended workflow

1. Select the source video and timing sheet.
2. Click **Create final YouTube video automatically**. Validation, native
   resolution selection, sizing, parallel safe placement, professional styling,
   rendering, and verification run without intermediate questions.
3. Optional manual review and preview controls remain available after completion.

Use **Continue an existing project** and select its `project.json` to resume later. The saved random seed and styles are restored, so unchanged chunks remain reusable. If a render is stopped, start the same export again to reuse completed chunks.

## Visual rules

- Safe zones expand from roughly 40% to 86% of frame width according to the rendered text, including outline/shadow breathing room.
- Safe scoring samples the complete overlay interval and penalizes detail, edges, motion, contrast, and faces.
- The selected position remains stable after the short entrance; it never chases the subject frame by frame.
- The YouTube Pro preset uses restrained crisp effects and avoids adjacent
  animation repetition.
- Borders, shadows, spacing, and movement scale with output resolution.
- Analysis uses a cached half-rate frame pass and up to four parallel workers.
- Rendering runs in a separate background process, keeping Stop and status
  controls responsive.
- The fastest reliable CPU/Intel/NVIDIA/AMD encoder is benchmarked once and cached.
- Simultaneous overlays are automatically separated across available positions.
- Reading speed is measured; overly dense text receives a protected contrast plate.
- Text normally uses one or two balanced lines. Very long lines can shrink to about 69 px at 4K and are flagged to shorten.
- Entrance motion is short, rest time is dominant, and exit is a restrained fade.

## Font-size guide at 4K

| Text shape | Approximate size | Typical behavior |
| --- | ---: | --- |
| 1–3 words / up to 18 characters | 153 px | Large single line |
| Up to 6 words / 27 characters | 132 px | Strong single line or balanced wrap |
| Medium line up to 34 characters | 112 px | Balanced one/two line |
| Longer line up to 42 characters | 99 px | Maximum two lines |
| Very long wording | 69–89 px | Width-capped and flagged to shorten |

## Export safety

- Output is written to a temporary file first.
- Existing output requires explicit replacement confirmation.
- A conservative free-space check runs before export.
- Video renders in overlay-safe chunks; no chunk boundary cuts through active text.
- Source audio is remuxed to the full video duration. Short audio no longer truncates the video.
- `final_verification.json` records the checked duration, 30-fps rate, dimensions, audio presence, file size, and timestamp.
- **Stop safely** requests cancellation while preserving completed chunks.

## Troubleshooting

- App does not open: run `CHECK_SETUP.bat`, then inspect `logs\streamlit.log`.
- Setup fails: inspect `logs\setup_console.log` and the timestamped setup log inside shared components.
- Shared drive moved: run `SETUP_ONCE.bat "X:\Your\Shared_Components"` with the new full path.
- FFmpeg issue: the check requires both `ffmpeg.exe` and `ffprobe.exe`, plus the ASS/subtitles filter.
- Port busy: the launcher starts at 8501 and keeps searching until a free local port is found. It reuses an already-running healthy instance.

## Tests

From an installed runtime:

```powershell
set /p RUNTIME_ROOT=<runtime_path.txt
"%RUNTIME_ROOT%\Scripts\python.exe" -m unittest discover -s tests -v
```

The suite covers exact 30-fps timing, three-line rejection, seeded randomization, dynamic safe-zone width, project seed restoration, unlimited port fallback, resume-safe chunking, rendering, full-duration short-audio remux, final frame count, and shared setup precedence.
