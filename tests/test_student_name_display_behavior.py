"""Executable regressions for student display-name browser rendering."""

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "js" / "student_name_display_harness.js"


def test_student_name_display_behavior_harness():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the JS behavior harness")
    result = subprocess.run(
        [node, str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=PROJECT_ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
