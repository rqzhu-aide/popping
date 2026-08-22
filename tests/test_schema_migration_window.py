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


def _seed_session_activity(connection, table, session_key):
    course_id = connection.execute("SELECT id FROM courses").fetchone()[0]
    first = connection.execute(
        """INSERT INTO students (course_id, student_id, name, pin)
           VALUES (?, 's1', 'Student 1', '1111')""",
        (course_id,),
    ).lastrowid
    second = connection.execute(
        """INSERT INTO students (course_id, student_id, name, pin)
           VALUES (?, 's2', 'Student 2', '2222')""",
        (course_id,),
    ).lastrowid
    if table == "teammate_thumbs":
        connection.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, question_key, grader_id, recipient_id)
               VALUES (?, ?, 'discussion', ?, ?)""",
            (course_id, session_key, first, second),
        )
    elif table == "presentation_ratings":
        connection.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key)
               VALUES (?, ?, 'presentation', ?)""",
            (course_id, first, session_key),
        )
    elif table == "challenge_rounds":
        connection.execute(
            """INSERT INTO challenge_rounds
               (course_id, session_key, presentation_key, challenge_key,
                challenge_num, challenger_id)
               VALUES (?, ?, 'presentation', 'challenge', 1, ?)""",
            (course_id, session_key, first),
        )
    else:
        connection.execute(
            """INSERT INTO challenge_ratings
               (course_id, session_key, challenge_key, presentation_key,
                challenger_id, rater_id, score)
               VALUES (?, ?, 'challenge', 'presentation', ?, ?, 5)""",
            (course_id, session_key, first, second),
        )
    connection.commit()


@pytest.mark.parametrize(
    "table",
    (
        "teammate_thumbs",
        "presentation_ratings",
        "challenge_rounds",
        "challenge_ratings",
    ),
)
def test_setup_rejects_current_session_durable_activity(monkeypatch, table):
    connection = _course_database("setup")
    try:
        _seed_session_activity(connection, table, session_key=0)
        _configure_pending_migration(monkeypatch, lambda _db: None)
        with pytest.raises(RuntimeError, match="between class sessions"):
            database.validate_schema_compatibility(connection)
    finally:
        connection.close()


def test_setup_allows_activity_from_an_older_session(monkeypatch):
    connection = _course_database("setup")
    try:
        _seed_session_activity(connection, "teammate_thumbs", session_key=9)
        _configure_pending_migration(monkeypatch, lambda _db: None)
        assert database.validate_schema_compatibility(connection) == "1.0.0"
    finally:
        connection.close()


def test_ended_allows_current_session_durable_activity(monkeypatch):
    connection = _course_database("ended")
    try:
        _seed_session_activity(connection, "teammate_thumbs", session_key=0)
        _configure_pending_migration(monkeypatch, lambda _db: None)
        assert database.validate_schema_compatibility(connection) == "1.0.0"
    finally:
        connection.close()


def test_setup_rejects_current_session_presentation_history(monkeypatch):
    connection = _course_database("setup")
    try:
        connection.execute(
            """UPDATE course_state
               SET presentation_history = '[{"session_key": 0}]'"""
        )
        connection.commit()
        _configure_pending_migration(monkeypatch, lambda _db: None)
        with pytest.raises(RuntimeError, match="between class sessions"):
            database.validate_schema_compatibility(connection)
    finally:
        connection.close()


def test_runtime_schema_check_does_not_mutate_an_older_database(monkeypatch):
    connection = _course_database("setup")
    slug = "read_only_schema_check"
    before_schema = [
        tuple(row) for row in connection.execute(
            """SELECT type, name, sql FROM sqlite_master
               ORDER BY type, name"""
        ).fetchall()
    ]
    before_changes = connection.total_changes
    database._schema_checked.discard(slug)
    monkeypatch.setattr(database, "get_db", lambda _slug: connection)
    try:
        with pytest.raises(RuntimeError, match="offline schema migration"):
            database.ensure_schema(slug)
        after_schema = [
            tuple(row) for row in connection.execute(
                """SELECT type, name, sql FROM sqlite_master
                   ORDER BY type, name"""
            ).fetchall()
        ]
        assert after_schema == before_schema
        assert connection.total_changes == before_changes
        assert connection.in_transaction is False
        assert slug not in database._schema_checked
    finally:
        database._schema_checked.discard(slug)
        connection.close()
