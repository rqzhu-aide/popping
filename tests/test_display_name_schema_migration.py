"""Regression tests for the v1.2 display-name schema migration."""

from pathlib import Path
import sqlite3
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database  # noqa: E402
from versioning import (  # noqa: E402
    APP_VERSION,
    BASELINE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SCHEMA_VERSION_HISTORY,
)


V1_1_SCHEMA_VERSION = "1.1.0"


def _baseline_connection():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(
        (PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8")
    )
    return db


def _seed_student(db):
    instructor_id = db.execute(
        """INSERT INTO instructors (username, name, pin)
           VALUES ('instructor', 'Instructor', '9999')"""
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses
           (name, slug, instructor_id, is_active)
           VALUES ('Schema Test', 'schema-test', ?, 1)""",
        [instructor_id],
    ).lastrowid
    student_id = db.execute(
        """INSERT INTO students (course_id, student_id, name, pin)
           VALUES (?, 's1', 'Roster Name', '1111')""",
        [course_id],
    ).lastrowid
    db.commit()
    return student_id


def _mark_schema_1_1(db):
    migration = database._SCHEMA_MIGRATIONS[
        (BASELINE_SCHEMA_VERSION, V1_1_SCHEMA_VERSION)
    ]
    migration(db)
    db.execute(
        """INSERT INTO schema_migrations
           (schema_version, applied_by_app_version) VALUES (?, ?)""",
        [V1_1_SCHEMA_VERSION, V1_1_SCHEMA_VERSION],
    )
    db.commit()


def _student_columns(db):
    return {
        row["name"]: row
        for row in db.execute("PRAGMA table_info(students)").fetchall()
    }


def test_v1_1_to_v1_2_preserves_roster_name_and_adds_null_display_name():
    db = _baseline_connection()
    try:
        student_db_id = _seed_student(db)
        _mark_schema_1_1(db)
        assert "display_name" not in _student_columns(db)

        plan = database._schema_migration_plan(V1_1_SCHEMA_VERSION)
        assert [(source, target) for source, target, _migration in plan] == [
            (V1_1_SCHEMA_VERSION, SCHEMA_VERSION)
        ]

        assert database.upgrade_schema_connection(db) == SCHEMA_VERSION

        display_name = _student_columns(db)["display_name"]
        assert display_name["type"].upper() == "TEXT"
        assert display_name["notnull"] == 0
        assert display_name["dflt_value"] is None
        student = db.execute(
            "SELECT name, display_name FROM students WHERE id = ?",
            [student_db_id],
        ).fetchone()
        assert dict(student) == {
            "name": "Roster Name",
            "display_name": None,
        }
        assert [
            row["schema_version"]
            for row in db.execute(
                "SELECT schema_version FROM schema_migrations ORDER BY id"
            ).fetchall()
        ] == list(SCHEMA_VERSION_HISTORY)
        assert db.execute(
            """SELECT applied_by_app_version FROM schema_migrations
               WHERE schema_version = ?""",
            [SCHEMA_VERSION],
        ).fetchone()[0] == APP_VERSION
        assert database.validate_current_schema(db) == SCHEMA_VERSION
    finally:
        db.close()


def test_v1_2_validation_rejects_ledger_without_display_name_column():
    db = _baseline_connection()
    try:
        _mark_schema_1_1(db)
        db.execute(
            """INSERT INTO schema_migrations
               (schema_version, applied_by_app_version) VALUES (?, ?)""",
            [SCHEMA_VERSION, APP_VERSION],
        )
        db.commit()

        with pytest.raises(
            RuntimeError,
            match=r"students is missing required column\(s\): display_name",
        ):
            database.validate_current_schema(db)
    finally:
        db.close()


def test_v1_2_migration_rolls_back_column_and_ledger_on_validation_failure(
        monkeypatch):
    db = _baseline_connection()
    try:
        _mark_schema_1_1(db)

        def reject_profile_schema(_db):
            raise RuntimeError("forced profile-schema validation failure")

        monkeypatch.setattr(
            database, "_validate_student_profile_schema", reject_profile_schema
        )
        with pytest.raises(
            RuntimeError, match="forced profile-schema validation failure"
        ):
            database.upgrade_schema_connection(db)

        assert "display_name" not in _student_columns(db)
        assert [
            row["schema_version"]
            for row in db.execute(
                "SELECT schema_version FROM schema_migrations ORDER BY id"
            ).fetchall()
        ] == [BASELINE_SCHEMA_VERSION, V1_1_SCHEMA_VERSION]
    finally:
        db.close()
