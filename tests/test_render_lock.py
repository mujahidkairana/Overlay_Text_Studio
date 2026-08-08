from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from overlay_studio import render_lock


class RenderLockTests(unittest.TestCase):
    def test_active_owner_blocks_duplicate_render(self):
        with tempfile.TemporaryDirectory() as temp_string:
            project = Path(temp_string)
            token, owner = render_lock.acquire(project)
            self.assertIsNotNone(token)
            self.assertIsNone(owner)
            with patch.object(render_lock, "pid_running", return_value=True):
                duplicate, active = render_lock.acquire(project)
            self.assertIsNone(duplicate)
            self.assertEqual(active["token"], token)
            self.assertTrue(render_lock.release(project, token))

    def test_dead_owner_is_recovered(self):
        with tempfile.TemporaryDirectory() as temp_string:
            project = Path(temp_string)
            (project / render_lock.LOCK_NAME).write_text(
                json.dumps({"token": "stale", "pid": 99999999}), encoding="utf-8"
            )
            with patch.object(render_lock, "pid_running", return_value=False):
                token, owner = render_lock.acquire(project)
            self.assertIsNotNone(token)
            self.assertIsNone(owner)
            self.assertNotEqual(token, "stale")

    def test_token_prevents_other_owner_from_releasing_lock(self):
        with tempfile.TemporaryDirectory() as temp_string:
            project = Path(temp_string)
            token, _ = render_lock.acquire(project)
            self.assertFalse(render_lock.release(project, "different-token"))
            self.assertTrue((project / render_lock.LOCK_NAME).exists())
            self.assertTrue(render_lock.release(project, token))


if __name__ == "__main__":
    unittest.main()
