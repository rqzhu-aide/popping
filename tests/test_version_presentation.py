"""Regression checks for public version presentation and operator guidance."""

from pathlib import Path

import pytest
from flask import render_template, session

import app as app_module


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_templates_present_version_and_unambiguous_download_labels():
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    instructor = (PROJECT_ROOT / "templates" / "instructor.html").read_text(
        encoding="utf-8"
    )

    assert "Popping {{ app_version }}" in base
    assert "Download Results" in base
    assert "Current Week ({{ export_week }})" in base
    assert "week=w" in base
    assert "Download Current Week Results" in instructor
    assert "Download Legacy Data" in base
    assert (
        "Older-version or unclassified records excluded from current-week results"
        in base
    )
    assert "Download Legacy Feedback (week unknown)" not in base


def test_instructor_login_accepts_longer_ascii_digit_pins():
    """Instructor PINs may be 4-32 ASCII digits everywhere.

    Regression: init-course-db.py once accepted any PIN (e.g. 8 digits)
    while the login form clipped input at 4 characters, locking the
    instructor out of a freshly initialized course.
    """
    login = (PROJECT_ROOT / "templates" / "instructor_login.html").read_text(
        encoding="utf-8"
    )
    init_db = (PROJECT_ROOT / "scripts" / "init-course-db.py").read_text(
        encoding="utf-8"
    )

    assert 'maxlength="32"' in login
    assert 'pattern="[0-9]{4,32}"' in login
    assert 'maxlength="4"' not in login
    assert 'is_valid_instructor_pin(pin)' in init_db
    # Student PINs intentionally remain exactly 4 digits (roster contract).
    student_login = (PROJECT_ROOT / "templates" / "login.html").read_text(
        encoding="utf-8"
    )
    assert 'maxlength="4"' in student_login


def test_versioning_policy_documents_patch_compatibility_and_legacy_boundary():
    policy = (PROJECT_ROOT / "VERSIONING.md").read_text(encoding="utf-8")
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "baseline is `v1.0.0`" in policy
    assert "major and minor numbers match" in policy
    assert "`v1.0.3` data is compatible with every `v1.0.x`" in policy
    assert "database schema advances to `v1.1.0`" in policy
    assert "popping-course-backup-v1" in policy
    assert "## [v1.0.0]" in changelog


@pytest.mark.parametrize(
    "session_data",
    [
        {
            "role": "student",
            "student_id": "student-1",
            "name": "Student One",
            "slug": "test-course",
        },
        {
            "role": "instructor",
            "instructor_id": 1,
            "instructor_name": "Instructor One",
            "slug": "test-course",
        },
    ],
    ids=["student", "instructor"],
)
def test_authenticated_header_shows_version_before_theme_toggle(session_data):
    with app_module.app.test_request_context("/"):
        session.update(session_data)
        rendered = render_template("base.html")

    navbar = rendered[
        rendered.index('<nav class="navbar">'):rendered.index("</nav>")
    ]
    assert navbar.count('class="nav-version"') == 1
    assert app_module.public_version(app_module.APP_VERSION) in navbar
    assert navbar.index('class="nav-version"') < navbar.index(
        'id="theme-toggle"'
    )


def test_anonymous_header_omits_version_but_footer_keeps_it():
    with app_module.app.test_request_context("/"):
        rendered = render_template("base.html")

    navbar = rendered[
        rendered.index('<nav class="navbar">'):rendered.index("</nav>")
    ]
    footer = rendered[rendered.index('<footer class="site-footer">'):]
    assert 'class="nav-version"' not in navbar
    assert app_module.public_version(app_module.APP_VERSION) not in navbar
    assert f"Popping {app_module.public_version(app_module.APP_VERSION)}" in footer
