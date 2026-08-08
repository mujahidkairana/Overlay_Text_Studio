from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TaskStage:
    name: str
    start: float
    end: float


RENDER_TASKS = (
    TaskStage("Render video chunks", 0.0, 0.90),
    TaskStage("Join rendered chunks", 0.90, 0.96),
    TaskStage("Restore source audio", 0.96, 0.995),
    TaskStage("Verify final video", 0.995, 1.0),
)

PLANNING_TASKS = (
    TaskStage("Detect scene changes", 0.0, 0.15),
    TaskStage("Extract analysis frames", 0.15, 0.40),
    TaskStage("Analyse safe text areas", 0.40, 0.90),
    TaskStage("Build editorial plan", 0.90, 1.0),
)


class ProgressEstimator:
    """Turn overall progress into stable task and ETA details."""

    def __init__(self, stages: tuple[TaskStage, ...], *, now: float | None = None):
        if not stages:
            raise ValueError("At least one progress stage is required.")
        self.stages = stages
        self.job_started_at = time.time() if now is None else float(now)
        self.task_started_at = self.job_started_at
        self._task_index = 0

    def snapshot(
        self, value: float, label: str, *, now: float | None = None
    ) -> dict[str, object]:
        moment = time.time() if now is None else float(now)
        progress = max(0.0, min(1.0, float(value)))
        index = len(self.stages) - 1
        if progress < 1.0:
            for candidate, stage in enumerate(self.stages):
                if progress < stage.end:
                    index = candidate
                    break
        if index != self._task_index:
            self._task_index = index
            self.task_started_at = moment

        stage = self.stages[index]
        span = max(0.000001, stage.end - stage.start)
        task_progress = max(0.0, min(1.0, (progress - stage.start) / span))
        total_elapsed = max(0.0, moment - self.job_started_at)
        task_elapsed = max(0.0, moment - self.task_started_at)

        total_estimated = (
            total_elapsed / progress
            if 0.01 <= progress < 1.0
            else (total_elapsed if progress >= 1.0 else None)
        )
        total_remaining = (
            max(0.0, total_estimated - total_elapsed)
            if total_estimated is not None
            else (0.0 if progress >= 1.0 else None)
        )
        if 0.03 <= task_progress < 1.0 and task_elapsed >= 1.0:
            task_remaining = max(
                0.0, task_elapsed * (1.0 - task_progress) / task_progress
            )
        elif task_progress >= 1.0:
            task_remaining = 0.0
        elif total_estimated is not None:
            task_remaining = total_estimated * max(0.0, stage.end - progress)
        else:
            task_remaining = None
        return {
            "snapshot_at": moment,
            "progress": progress,
            "label": label,
            "task_index": index + 1,
            "task_count": len(self.stages),
            "task_name": stage.name,
            "task_progress": task_progress,
            "job_started_at": self.job_started_at,
            "task_started_at": self.task_started_at,
            "task_started_text": datetime.fromtimestamp(self.task_started_at).strftime(
                "%I:%M:%S %p"
            ),
            "current_elapsed_seconds": task_elapsed,
            "current_estimated_remaining_seconds": task_remaining,
            "current_estimated_seconds": (
                task_elapsed + task_remaining if task_remaining is not None else None
            ),
            "total_elapsed_seconds": total_elapsed,
            "total_estimated_remaining_seconds": total_remaining,
            "total_estimated_seconds": total_estimated,
        }
