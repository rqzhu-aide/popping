"""Regression checks for operator-facing database safety guidance."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_readme_requires_offline_database_maintenance():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Never edit, replace, initialize, or restore" in readme
    assert "On Render, suspend the web" in readme
    assert "restart the service" in readme
    assert "scripts/restore-course-db.py" in readme
    assert "INSERT INTO students" not in readme


def test_new_course_stays_inactive_until_its_database_exists():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    create_script = (PROJECT_ROOT / "scripts" / "create-course.sh").read_text(
        encoding="utf-8"
    )

    assert "active: false" in create_script
    assert "active: true\n" not in create_script
    assert "Local: initialize the database" in create_script
    assert (
        "Commit and deploy this course while active remains false" in create_script
    )
    assert "run those commands in Render Shell" in create_script
    assert "Change active to true" in create_script
    assert "Keep every course whose database is not yet on the Render disk" in readme
    assert "After the inactive configuration deploys successfully" in readme
    assert "course becomes public only after its database is ready" in readme


def test_readme_describes_validated_atomic_course_reset():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "Build and validate a replacement database" in readme
    assert "Save a verified copy of the previous database" in readme
    assert "Atomically replace `popping.db`" in readme
    assert "- Delete `popping.db`" not in readme
