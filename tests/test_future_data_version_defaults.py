"""Focused contract tests for data-version defaults after schema v1.0."""

import sqlite3

import pytest

import database
from tests.test_database_legacy_migration import _legacy_db


def _provenance_schema(default_clause, future=False):
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """CREATE TABLE schema_migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_version TEXT NOT NULL UNIQUE,
            applied_by_app_version TEXT NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    connection.execute(
        """INSERT INTO schema_migrations
           (schema_version, applied_by_app_version)
           VALUES ('1.0.0', '1.0.0')"""
    )
    if future:
        connection.execute(
            """INSERT INTO schema_migrations
               (schema_version, applied_by_app_version)
               VALUES ('1.1.0', '1.1.0')"""
        )
    for table in database._VERSIONED_DATA_TABLES:
        connection.execute(
            f"""CREATE TABLE {table} (
                id INTEGER PRIMARY KEY,
                data_version TEXT NOT NULL{default_clause}
            )"""
        )
    return connection


@pytest.mark.parametrize(
    "default_clause",
    ("", " DEFAULT '1.1.8'"),
)
def test_future_schema_accepts_explicit_only_or_compatible_default(
    monkeypatch,
    default_clause,
):
    monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
    monkeypatch.setattr(
        database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
    )
    connection = _provenance_schema(default_clause, future=True)
    try:
        database.validate_data_version_schema(connection)
    finally:
        connection.close()


def test_future_schema_rejects_stale_baseline_default(monkeypatch):
    monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
    monkeypatch.setattr(
        database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
    )
    connection = _provenance_schema(" DEFAULT '1.0.0'", future=True)
    try:
        with pytest.raises(RuntimeError, match="data-version default"):
            database.validate_data_version_schema(connection)
    finally:
        connection.close()


def test_baseline_schema_still_requires_fixed_baseline_default():
    connection = _provenance_schema("")
    try:
        with pytest.raises(RuntimeError, match="data-version default"):
            database.validate_data_version_schema(connection)
    finally:
        connection.close()

def test_legacy_peer_review_conversion_stamps_baseline_without_default(
    monkeypatch,
):
    db, course_id, students = _legacy_db()
    try:
        db.execute(
            """INSERT INTO peer_reviews
               (course_id, grader_id, recipient_id, criterion, score)
               VALUES (?, ?, ?, 'overall', 1)""",
            (course_id, students[0], students[1]),
        )
        create_sql = db.execute(
            """SELECT sql FROM sqlite_master
               WHERE type = 'table' AND name = 'teammate_thumbs'"""
        ).fetchone()[0]
        explicit_only_sql = create_sql.replace(
            "data_version TEXT NOT NULL DEFAULT '1.0.0'",
            "data_version TEXT NOT NULL",
        ).replace(
            "CREATE TABLE teammate_thumbs",
            "CREATE TABLE teammate_thumbs_new",
            1,
        )
        assert explicit_only_sql != create_sql
        db.execute(explicit_only_sql)
        columns = [
            row[1] for row in db.execute("PRAGMA table_info(teammate_thumbs)")
        ]
        column_list = ", ".join(columns)
        db.execute(
            f"INSERT INTO teammate_thumbs_new ({column_list}) "
            f"SELECT {column_list} FROM teammate_thumbs"
        )
        db.execute("DROP TABLE teammate_thumbs")
        db.execute("ALTER TABLE teammate_thumbs_new RENAME TO teammate_thumbs")
        db.execute(
            """INSERT INTO schema_migrations
               (schema_version, applied_by_app_version)
               VALUES ('1.1.0', '1.1.0')"""
        )
        db.commit()

        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
        monkeypatch.setattr(
            database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
        )
        monkeypatch.setattr(
            database, "_VERSIONED_DATA_TABLES", ("teammate_thumbs",)
        )
        monkeypatch.setattr(
            database, "_validate_participation_schema",
            lambda _db, repair_indexes=False: None,
        )
        database._ensure_schema_locked(db)

        row = db.execute(
            """SELECT data_version FROM teammate_thumbs
               WHERE source_question_key = 'legacy'"""
        ).fetchone()
        assert row["data_version"] == database.BASELINE_DATA_VERSION
        version_column = {
            row["name"]: row
            for row in db.execute("PRAGMA table_info(teammate_thumbs)")
        }["data_version"]
        assert version_column["notnull"] == 1
        assert version_column["dflt_value"] is None
    finally:
        db.close()
