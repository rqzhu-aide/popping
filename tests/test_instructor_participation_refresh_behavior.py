"""Client regressions for instructor participation count reconciliation."""

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "js" / "app.js"
HARNESS = (
    Path(__file__).resolve().parent
    / "js"
    / "instructor_participation_refresh_harness.js"
)


def _handler(source, name):
    start = source.index(name)
    end = source.index("\n};", start) + len("\n};")
    return source[start:end]


def test_participation_readback_is_wired_to_instructor_mutations():
    source = APP_JS.read_text(encoding="utf-8")

    assert "COMPETITION_PARTICIPATION_READBACK_ATTEMPTS = 2" in source
    assert "COMPETITION_PARTICIPATION_LATER_RETRY_MS = 5000" in source
    assert "_competitionParticipationReadbackAppliedSerial" in source
    assert (
        "return _competitionParticipationReadbackAppliedSerial > "
        "requestSerial;"
    ) in source
    assert "_competitionParticipationReadbackDirty = true" in source
    assert "retryCompetitionParticipationCountsIfDirty();" in source
    assert (
        "if (historyChanged) markCompetitionParticipationCountsDirty();"
    ) in source
    assert "syncCompetitionParticipationStateIdentity(state);" in source

    challenge_start = source.index("function renderInstructorChallenge")
    challenge_end = source.index("window.selectChallenger", challenge_start)
    challenge_renderer = source[challenge_start:challenge_end]
    assert "applyStudentParticipationCounts(" not in challenge_renderer

    for name in (
        "window.selectChallenger",
        "window.clearChallenger",
        "window.nextPresentation",
        "window.cancelPresentation",
    ):
        assert (
            "await reconcileCompetitionParticipationCounts();"
            in _handler(source, name)
        ), name

    finish = _handler(source, "window.nextPresentation")
    assert "permanently recorded" in finish
    assert "Use Cancel" in finish
    assert "Mistake first" in finish
    assert finish.index("confirm(") < finish.index("postJSON(")

    phase_change = _handler(source, "window.setPhase")
    assert "permanentParticipationWarning" in phase_change
    assert "permanently recorded" in phase_change
    assert "Use Cancel Mistake first" in phase_change

def test_instructor_participation_readback_behavior():
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
