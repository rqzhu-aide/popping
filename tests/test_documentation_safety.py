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
