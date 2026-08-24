"""Regression tests for Render-compatible maintenance confirmations."""

from pathlib import Path

import pytest

from scripts.maintenance_safety import (
    COURSE_OFFLINE_CONFIRMATION,
    SERVICE_STOPPED_CONFIRMATION,
    validate_confirmation,
)


def _config(tmp_path, active):
    path = tmp_path / "course.yaml"
    path.write_text(
        f"slug: safe101\nactive: {'true' if active else 'false'}\n",
        encoding="utf-8",
    )
    return path


def test_service_stopped_confirmation_remains_supported(tmp_path):
    missing = tmp_path / "missing.yaml"
    assert validate_confirmation(
        SERVICE_STOPPED_CONFIRMATION, missing
    ) == SERVICE_STOPPED_CONFIRMATION


def test_course_offline_requires_inactive_configuration(tmp_path):
    assert validate_confirmation(
        COURSE_OFFLINE_CONFIRMATION, _config(tmp_path, False)
    ) == COURSE_OFFLINE_CONFIRMATION

    with pytest.raises(ValueError, match="active: false"):
        validate_confirmation(
            COURSE_OFFLINE_CONFIRMATION, _config(tmp_path, True)
        )


@pytest.mark.parametrize("confirmation", ("", "yes", "SERVICE STOPPED "))
def test_other_confirmations_are_rejected(tmp_path, confirmation):
    with pytest.raises(ValueError, match="was not confirmed"):
        validate_confirmation(confirmation, _config(tmp_path, False))
