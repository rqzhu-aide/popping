"""Executable regressions for seating layout and recognition drag gestures."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_seating_geometry_and_gesture_behavior():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the JS behavior harness")
    project_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, str(project_root / "tests" / "js" / "seating_harness.js")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=project_root,
    )
    assert result.returncode == 0, result.stdout + result.stderr
