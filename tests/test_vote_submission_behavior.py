"""Behavior regression for student vote and instructor write feedback."""

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HARNESS = Path(__file__).resolve().parent / "js" / "vote_submission_harness.js"


def test_vote_retry_and_progress_feedback():
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


def test_vote_status_regions_are_accessible_and_fixed():
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    dashboard = (PROJECT_ROOT / "templates" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'id="student-vote-status"' in base
    assert 'id="instructor-save-status"' in base
    assert base.count('role="status" aria-live="polite"') >= 2
    assert 'aria-atomic="true" hidden' not in base
    rating_status = dashboard[dashboard.index('id="rating-status"'):][:240]
    assert 'role="status"' in rating_status
    assert 'aria-live="polite" aria-atomic="true"' in rating_status
    assert ".request-progress-status" in css
    assert "position: fixed;" in css
    assert '.request-progress-status:not([data-visible="true"])' in css
    assert "pointer-events: none;" in css
    assert "'Submitting your vote ...'" in source
    assert "status.textContent = 'Saving';" in source
    assert "btn.dataset.submitting = '1';" in source
    assert "event.returnValue = '';" in source
    assert source.count("{ retryVoteOnce: true }") == 1
    assert "Click the thumb again to retry." in source
    assert "queueDashboardReload(state);" in source
    assert "popping-unconfirmed-teammate-vote-warning" in source
    assert source.count("finishInstructorSave(") == 5
    assert "window.confirmRosterUpload" in source