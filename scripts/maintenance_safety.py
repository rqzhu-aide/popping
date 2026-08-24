"""Shared confirmation rules for destructive course database maintenance."""

from pathlib import Path

import yaml


SERVICE_STOPPED_CONFIRMATION = "SERVICE STOPPED"
COURSE_OFFLINE_CONFIRMATION = "COURSE OFFLINE"


def confirmation_prompt():
    return (
        f"Type {SERVICE_STOPPED_CONFIRMATION} if all web workers are stopped, "
        f"or {COURSE_OFFLINE_CONFIRMATION} if this deployed course is inactive: "
    )


def validate_confirmation(value, course_config_path):
    """Accept a full service stop or a deployed inactive-course boundary.

    Render Shell is available only while an instance is running. On Render, an
    operator therefore takes one course offline by deploying ``active: false``
    before changing its database. Local operators can instead stop every web
    worker and use the original confirmation.
    """
    if value == SERVICE_STOPPED_CONFIRMATION:
        return SERVICE_STOPPED_CONFIRMATION
    if value != COURSE_OFFLINE_CONFIRMATION:
        raise ValueError(
            "database maintenance safety was not confirmed"
        )

    path = Path(course_config_path)
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(
            f"could not verify inactive course configuration: {exc}"
        ) from exc
    if not isinstance(config, dict):
        raise ValueError("course.yaml must contain a mapping")
    if config.get("active") is not False:
        raise ValueError(
            "COURSE OFFLINE requires course.yaml to contain active: false"
        )
    return COURSE_OFFLINE_CONFIRMATION
