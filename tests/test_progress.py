from __future__ import annotations

import unittest

from overlay_studio.progress import ProgressEstimator, RENDER_TASKS


class ProgressEstimatorTests(unittest.TestCase):
    def test_reports_task_number_and_time_estimates(self):
        tracker = ProgressEstimator(RENDER_TASKS, now=100.0)
        details = tracker.snapshot(0.45, "Rendering", now=145.0)

        self.assertEqual(details["task_index"], 1)
        self.assertEqual(details["task_count"], 4)
        self.assertEqual(details["task_name"], "Render video chunks")
        self.assertAlmostEqual(details["task_progress"], 0.5)
        self.assertAlmostEqual(details["current_elapsed_seconds"], 45.0)
        self.assertAlmostEqual(
            details["current_estimated_remaining_seconds"], 45.0
        )
        self.assertAlmostEqual(details["total_estimated_seconds"], 100.0)
        self.assertAlmostEqual(
            details["total_estimated_remaining_seconds"], 55.0
        )

    def test_resets_current_timer_when_next_task_starts(self):
        tracker = ProgressEstimator(RENDER_TASKS, now=100.0)
        tracker.snapshot(0.80, "Rendering", now=180.0)
        details = tracker.snapshot(0.92, "Joining", now=190.0)

        self.assertEqual(details["task_index"], 2)
        self.assertEqual(details["task_started_at"], 190.0)
        self.assertEqual(details["current_elapsed_seconds"], 0.0)
        self.assertGreater(details["current_estimated_remaining_seconds"], 0.0)

    def test_completed_job_has_zero_remaining_and_actual_total(self):
        tracker = ProgressEstimator(RENDER_TASKS, now=100.0)
        details = tracker.snapshot(1.0, "Complete", now=220.0)

        self.assertEqual(details["task_index"], 4)
        self.assertEqual(details["total_estimated_remaining_seconds"], 0.0)
        self.assertEqual(details["total_estimated_seconds"], 120.0)


if __name__ == "__main__":
    unittest.main()
