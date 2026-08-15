"""Regression tests for between-session schema-line deployments."""

from pathlib import Path
import sqlite3

import pytest

import database


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _course_database(phase):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        (PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8")
    )
    instructor_id = connection.execute(
        """INSERT INTO instructors (username, name, pin)
           VALUES ('teacher', 'Teacher', '2468')"""
    ).lastrowid
    course_id = connection.execute(
        """INSERT INTO courses (name, slug, instructor_id)
           VALUES ('Course', 'course', ?)""",
        (instructor_id,),
    ).lastrowid
    connection.execute(
        "INSERT INTO course_state (course_id, phase) VALUES (?, ?)",
        (course_id, phase),
    )
    connection.commit()
    return connection


def _configure_pending_migration(monkeypatch, migration):
    monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
    monkeypatch.setattr(
        database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
    )
    monkeypatch.setattr(
        database,
        "_SCHEMA_MIGRATIONS",
        {("1.0.0", "1.1.0"): migration},
    )


@pytest.mark.parametrize("phase", ("setup", "ended"))
def test_cleared_between_session_state_allows_pending_migration_plan(
    monkeypatch,
    phase,
):
    connection = _course_database(phase)
    try:
        _configure_pending_migration(monkeypatch, lambda _db: None)
        assert database.validate_schema_compatibility(connection) == "1.0.0"
    finally:
        connection.close()


def test_active_session_guard_runs_before_migration_statement(monkeypatch):
    connection = _course_database("competition")
    migration_called = False

    def migration(_db):
        nonlocal migration_called
        migration_called = True

    try:
        _configure_pending_migration(monkeypatch, migration)
        connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="between class sessions"):
            database.migrate_schema_connection(connection)
        connection.rollback()

        assert migration_called is False
        assert [
            row[0] for row in connection.execute(
                "SELECT schema_version FROM schema_migrations ORDER BY id"
            )
        ] == ["1.0.0"]
    finally:
        connection.close()
