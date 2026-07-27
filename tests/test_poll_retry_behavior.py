"""Behavior tests for the student poll loop's secondary-sync retry logic.

Regression coverage: a failed roster / discussion-questions / my-responses
request must retry on later poll cycles even while the main poll keeps
returning ``changed: false``, with a bounded backoff; a steady healthy
session must send only the cheap main poll. Delayed requests must not overlap
or block polling, stale results must not overwrite a newer context, and a
compact poll-close signal must safely close rating controls without erasing
an unsaved draft.

Runs a Node vm harness (tests/js/poll_retry_harness.js) against the real
static/js/app.js; skipped when Node.js is unavailable.
"""

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "js" / "poll_retry_harness.js"


def test_failed_secondary_student_requests_retry_on_unchanged_polls():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the JS behavior harness")
    result = subprocess.run(
        [node, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
