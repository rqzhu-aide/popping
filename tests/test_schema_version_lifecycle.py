"""Focused regression tests for schema-version adoption and maintenance tools."""

import builtins
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "popping.sql"
INIT_SCRIPT = PROJECT_ROOT / "scripts" / "init-course-db.py"
RESTORE_SCRIPT = PROJECT_ROOT / "scripts" / "restore-course-db.py"

import database  # noqa: E402
import versioning  # noqa: E402


_LEDGER_SCHEMA = """CREATE TABLE schema_migrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_version TEXT NOT NULL UNIQUE,
    applied_by_app_version TEXT NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);"""


def _load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def init_module():
    return _load_script("versioned_init_course_db", INIT_SCRIPT)


@pytest.fixture
def restore_module():
    return _load_script("versioned_restore_course_db", RESTORE_SCRIPT)


def _schema_sql(versioned=True):
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    if versioned:
        return schema

    ledger_block = _LEDGER_SCHEMA + """

INSERT INTO schema_migrations (schema_version, applied_by_app_version)
VALUES ('1.0.0', '1.0.0');
"""
    assert schema.count(ledger_block) == 1
    schema = schema.replace(ledger_block, "")
    version_column = "    data_version TEXT NOT NULL DEFAULT '1.0.0',\n"
    assert schema.count(version_column) == 4
    return schema.replace(version_column, "")


def _seed_course(connection, slug="safe101", history=None):
    instructor_id = connection.execute(
        """INSERT INTO instructors (username, name, pin)
           VALUES ('teacher', 'Teacher', '2468')"""
    ).lastrowid
    course_id = connection.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id)
           VALUES ('Safe Course', 'SAFE 101', 'Test 2026', ?, ?)""",
        (slug, instructor_id),
    ).lastrowid
    connection.execute(
        """INSERT INTO course_state (course_id, presentation_history)
           VALUES (?, ?)""",
        (course_id, json.dumps(history or [])),
    )
    connection.commit()
    return course_id


def _create_course_db(path=None, *, versioned=True, history=None, slug="safe101"):
    connection = sqlite3.connect(":memory:" if path is None else path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_schema_sql(versioned=versioned))
    course_id = _seed_course(connection, slug=slug, history=history)
    return connection, course_id


def _seed_durable_rows(connection, course_id):
    first = connection.execute(
        """INSERT INTO students (course_id, student_id, name, pin)
           VALUES (?, 's1', 'Student 1', '1111')""",
        (course_id,),
    ).lastrowid
    second = connection.execute(
        """INSERT INTO students (course_id, student_id, name, pin)
           VALUES (?, 's2', 'Student 2', '1111')""",
        (course_id,),
    ).lastrowid
    connection.execute(
        """INSERT INTO presentation_ratings
           (course_id, student_id, question_key, q1_developed, q2_easy)
           VALUES (?, ?, 'presentation-1', 4, 3)""",
        (course_id, first),
    )
    connection.execute(
        """INSERT INTO teammate_thumbs
           (course_id, session_key, question_key, grader_id, recipient_id)
           VALUES (?, 1, 'discussion-1', ?, ?)""",
        (course_id, first, second),
    )
    connection.execute(
        """INSERT INTO challenge_rounds
           (course_id, session_key, presentation_key, challenge_key,
            challenge_num, challenger_id)
           VALUES (?, 1, 'presentation-1', 'challenge-1', 1, ?)""",
        (course_id, first),
    )
    connection.execute(
        """INSERT INTO challenge_ratings
           (course_id, session_key, challenge_key, presentation_key,
            challenger_id, rater_id, score)
           VALUES (?, 1, 'challenge-1', 'presentation-1', ?, ?, 5)""",
        (course_id, first, second),
    )
    connection.commit()


def _add_ledger(connection, schema_version="1.0.0", app_version="1.0.0"):
    connection.execute(_LEDGER_SCHEMA)
    connection.execute(
        """INSERT INTO schema_migrations
           (schema_version, applied_by_app_version) VALUES (?, ?)""",
        (schema_version, app_version),
    )
    connection.commit()


def test_baseline_schema_contract_is_fixed():
    assert versioning.BASELINE_SCHEMA_VERSION == "1.0.0"
    assert versioning.SCHEMA_VERSION_HISTORY == ("1.0.0", "1.1.0")


def test_legacy_adoption_stamps_fixed_baseline_and_backfills_once():
    connection, course_id = _create_course_db(
        versioned=False,
        history=[{"presentation_key": "old-presentation"}],
    )
    try:
        _seed_durable_rows(connection, course_id)
        connection.execute("UPDATE course_state SET phase = 'ended'")
        database._ensure_schema_locked(connection)

        assert connection.execute(
            "SELECT schema_version FROM schema_migrations ORDER BY id"
        ).fetchall()[0][0] == versioning.BASELINE_SCHEMA_VERSION
        for table in database._versioned_data_tables_for(
            versioning.BASELINE_SCHEMA_VERSION
        ):
            assert connection.execute(
                f"SELECT DISTINCT data_version FROM {table}"
            ).fetchall()[0][0] == versioning.BASELINE_DATA_VERSION
        history = json.loads(
            connection.execute(
                "SELECT presentation_history FROM course_state"
            ).fetchone()[0]
        )
        assert history[0]["data_version"] == versioning.BASELINE_DATA_VERSION
    finally:
        connection.close()


def test_existing_ledger_does_not_rewrite_missing_history_provenance():
    connection, _course_id = _create_course_db(
        history=[{"presentation_key": "already-versioned-database"}]
    )
    try:
        connection.execute("UPDATE course_state SET phase = 'ended'")
        database._ensure_schema_locked(connection)
        history = json.loads(
            connection.execute(
                "SELECT presentation_history FROM course_state"
            ).fetchone()[0]
        )
        assert "data_version" not in history[0]
    finally:
        connection.close()


def test_baseline_ledger_install_does_not_follow_future_current_version(
    monkeypatch,
):
    connection = sqlite3.connect(":memory:")
    try:
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
        database._install_baseline_schema_ledger(connection)
        assert connection.execute(
            "SELECT schema_version FROM schema_migrations"
        ).fetchone()[0] == "1.0.0"
    finally:
        connection.close()


def test_inspection_allows_known_older_prefix_but_planning_requires_a_path(
    monkeypatch,
):
    connection, _course_id = _create_course_db()
    try:
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
        monkeypatch.setattr(
            database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
        )
        monkeypatch.setattr(database, "_SCHEMA_MIGRATIONS", {})

        assert database.inspect_schema_version(connection) == "1.0.0"
        with pytest.raises(RuntimeError, match="no migration path"):
            database.validate_schema_compatibility(connection)
    finally:
        connection.close()


def test_missing_future_migration_path_fails_before_baseline_adoption(
    monkeypatch,
):
    connection, _course_id = _create_course_db(
        versioned=False,
        history=[{"presentation_key": "must-remain-untouched"}],
    )
    try:
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
        monkeypatch.setattr(
            database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
        )
        monkeypatch.setattr(database, "_SCHEMA_MIGRATIONS", {})

        with pytest.raises(RuntimeError, match="no migration path"):
            database._ensure_schema_locked(connection)

        assert database.inspect_schema_version(connection) is None
        for table in database._VERSIONED_DATA_TABLES:
            columns = {
                row[1] for row in connection.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            assert "data_version" not in columns
        history = json.loads(
            connection.execute(
                "SELECT presentation_history FROM course_state"
            ).fetchone()[0]
        )
        assert "data_version" not in history[0]
    finally:
        connection.close()


def test_future_migration_cannot_leave_stale_baseline_defaults(monkeypatch):
    connection, _course_id = _create_course_db()
    try:
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
        monkeypatch.setattr(
            database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
        )

        def migrate(db):
            db.execute("CREATE TABLE migration_1_1_marker (id INTEGER)")

        monkeypatch.setattr(
            database,
            "_SCHEMA_MIGRATIONS",
            {("1.0.0", "1.1.0"): migrate},
        )
        monkeypatch.setattr(
            database, "_validate_participation_schema", lambda _db, **_kw: None
        )
        connection.execute("BEGIN")
        with pytest.raises(RuntimeError, match="data-version default"):
            database.migrate_schema_connection(connection)
        connection.rollback()

        assert [
            row[0] for row in connection.execute(
                "SELECT schema_version FROM schema_migrations ORDER BY id"
            )
        ] == ["1.0.0"]
        assert connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'migration_1_1_marker'"""
        ).fetchone() is None
    finally:
        connection.close()


def test_ledger_order_and_unknown_versions_are_rejected(monkeypatch):
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            """CREATE TABLE schema_migrations (
                id INTEGER PRIMARY KEY,
                schema_version TEXT NOT NULL,
                applied_by_app_version TEXT NOT NULL,
                applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        connection.executemany(
            """INSERT INTO schema_migrations
               (id, schema_version, applied_by_app_version)
               VALUES (?, ?, '1.0.0')""",
            [(1, "1.1.0"), (2, "1.0.0")],
        )
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
        monkeypatch.setattr(
            database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
        )
        with pytest.raises(RuntimeError, match="not strictly ordered"):
            database.inspect_schema_version(connection)
    finally:
        connection.close()


def test_init_requires_current_metadata_for_candidate_but_accepts_legacy_backup(
    init_module,
    tmp_path,
):
    current_path = tmp_path / "current.db"
    current, _course_id = _create_course_db(current_path)
    database.upgrade_schema_connection(current)
    current.close()
    init_module.validate_course_database(
        current_path, "safe101", require_current_version=True
    )

    legacy_path = tmp_path / "legacy.db"
    legacy, _course_id = _create_course_db(legacy_path, versioned=False)
    legacy.close()
    init_module.validate_course_database(legacy_path, "safe101")
    with pytest.raises(RuntimeError, match="ledger is missing"):
        init_module.validate_course_database(
            legacy_path, "safe101", require_current_version=True
        )

    claimed_path = tmp_path / "claimed-current.db"
    claimed, _course_id = _create_course_db(claimed_path, versioned=False)
    _add_ledger(claimed)
    claimed.execute(
        """INSERT INTO schema_migrations
           (schema_version, applied_by_app_version)
           VALUES ('1.1.0', '1.1.0')"""
    )
    claimed.commit()
    claimed.close()
    with pytest.raises(RuntimeError, match="missing required column"):
        init_module.validate_course_database(
            claimed_path, "safe101", require_current_version=True
        )


def test_restore_accepts_preversioning_baseline(restore_module, tmp_path):
    legacy_path = tmp_path / "legacy.db"
    legacy, _course_id = _create_course_db(legacy_path, versioned=False)
    legacy.close()

    restore_module.validate_course_database(legacy_path, "safe101")


def test_restore_rejects_v1_1_database_missing_participant_table(
    restore_module,
    tmp_path,
):
    path = tmp_path / "missing-participants.db"
    connection, _course_id = _create_course_db(path)
    database.upgrade_schema_connection(connection)
    connection.execute("DROP TABLE presentation_participants")
    connection.commit()
    connection.close()

    with pytest.raises(
        RuntimeError,
        match="presentation_participants is missing required column",
    ):
        restore_module.validate_course_database(path, "safe101")


def test_current_schema_contract_repairs_only_missing_expected_indexes():
    connection, _course_id = _create_course_db()
    try:
        database.upgrade_schema_connection(connection)
        connection.execute(
            "DROP INDEX idx_presentation_participants_student"
        )

        with pytest.raises(RuntimeError, match="missing required index"):
            database.validate_current_schema(connection)
        database.validate_current_schema(connection, repair_indexes=True)
        database.validate_current_schema(connection)

        columns = tuple(
            row[2] for row in connection.execute(
                "PRAGMA index_info(idx_presentation_participants_student)"
            )
        )
        assert columns == ("course_id", "student_id")
    finally:
        connection.close()


def test_current_schema_contract_rejects_named_partial_index():
    connection, _course_id = _create_course_db()
    try:
        database.upgrade_schema_connection(connection)
        connection.execute(
            "DROP INDEX idx_presentation_participants_student"
        )
        connection.execute(
            """CREATE INDEX idx_presentation_participants_student
               ON presentation_participants(course_id, student_id)
               WHERE course_id > 0"""
        )

        with pytest.raises(RuntimeError, match="invalid definition"):
            database.validate_current_schema(connection, repair_indexes=True)
    finally:
        connection.close()


def test_current_schema_contract_rejects_missing_participant_unique_key():
    connection, _course_id = _create_course_db()
    try:
        database.upgrade_schema_connection(connection)
        connection.execute(
            """ALTER TABLE presentation_participants
               RENAME TO old_presentation_participants"""
        )
        connection.execute(
            """CREATE TABLE presentation_participants AS
               SELECT * FROM old_presentation_participants WHERE 0"""
        )
        connection.execute("DROP TABLE old_presentation_participants")

        with pytest.raises(RuntimeError, match="missing required unique key"):
            database.validate_current_schema(connection, repair_indexes=True)
    finally:
        connection.close()


def test_current_schema_contract_rejects_missing_participant_foreign_keys():
    connection, _course_id = _create_course_db()
    try:
        database.upgrade_schema_connection(connection)
        connection.execute(
            """ALTER TABLE presentation_participants
               RENAME TO old_presentation_participants"""
        )
        connection.execute(
            """CREATE TABLE presentation_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                session_key INTEGER NOT NULL,
                week_num INTEGER,
                presentation_key TEXT NOT NULL,
                student_id INTEGER NOT NULL,
                student_identifier TEXT NOT NULL,
                student_name TEXT,
                team_id INTEGER,
                team_name TEXT,
                data_version TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(course_id, presentation_key, student_id)
            )"""
        )
        connection.execute(
            """INSERT INTO presentation_participants
               (id, course_id, session_key, week_num, presentation_key,
                student_id, student_identifier, student_name, team_id,
                team_name, data_version, created_at)
               SELECT id, course_id, session_key, week_num, presentation_key,
                      student_id, student_identifier, student_name, team_id,
                      team_name, data_version, created_at
               FROM old_presentation_participants"""
        )
        connection.execute("DROP TABLE old_presentation_participants")

        with pytest.raises(RuntimeError, match="missing required foreign key"):
            database.validate_current_schema(connection, repair_indexes=True)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "schema_version, app_version, message",
    (
        ("1.2.0", "1.1.0", "newer"),
        ("0.9.0", "1.0.0", "unknown"),
        ("1.0.1", "1.0.0", "patch-level"),
        ("1.0.0", "not-a-version", "invalid version"),
    ),
)
def test_restore_rejects_present_invalid_ledgers(
    restore_module,
    tmp_path,
    schema_version,
    app_version,
    message,
):
    path = tmp_path / f"invalid-{schema_version}-{app_version}.db"
    connection, _course_id = _create_course_db(path)
    connection.execute(
        """UPDATE schema_migrations
           SET schema_version = ?, applied_by_app_version = ?""",
        (schema_version, app_version),
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match=message):
        restore_module.validate_course_database(path, "safe101")


def test_restore_rejects_future_source_before_confirmation_or_live_backup(
    restore_module,
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "data"
    live_path = data_dir / "safe101" / "popping.db"
    live_path.parent.mkdir(parents=True)
    live, _course_id = _create_course_db(live_path)
    live.close()
    source_path = tmp_path / "future.db"
    source, _course_id = _create_course_db(source_path)
    source.execute(
        "UPDATE schema_migrations SET schema_version = '1.2.0'"
    )
    source.commit()
    source.close()
    original_live = live_path.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("confirmation should not be requested"),
    )

    assert restore_module.main(["safe101", str(source_path)]) == 1
    assert live_path.read_bytes() == original_live
    assert not (live_path.parent / "restore-backups").exists()

def test_legacy_adoption_preflight_is_read_only_and_preserves_legacy_rows():
    connection, course_id = _create_course_db(
        versioned=False,
        history=[{"presentation_key": "legacy-presentation"}],
    )
    try:
        _seed_durable_rows(connection, course_id)
        connection.execute(
            """ALTER TABLE teammate_thumbs
               ADD COLUMN data_version TEXT NOT NULL DEFAULT '1.0.0'"""
        )
        connection.execute(
            "UPDATE teammate_thumbs SET data_version = '0.9.9'"
        )
        connection.commit()

        database.validate_legacy_adoption_candidate(connection)

        assert database.inspect_schema_version(connection) is None
        history = json.loads(
            connection.execute(
                "SELECT presentation_history FROM course_state"
            ).fetchone()[0]
        )
        assert "data_version" not in history[0]
        assert connection.execute(
            "SELECT data_version FROM teammate_thumbs"
        ).fetchone()[0] == "0.9.9"
    finally:
        connection.close()


def test_legacy_adoption_preflight_rejects_malformed_history_without_writing():
    connection, _course_id = _create_course_db(versioned=False)
    try:
        connection.execute(
            "UPDATE course_state SET presentation_history = '{broken'"
        )
        connection.commit()

        with pytest.raises(
            RuntimeError, match="Cannot version malformed presentation history"
        ):
            database.validate_legacy_adoption_candidate(connection)

        assert database.inspect_schema_version(connection) is None
        assert connection.execute(
            "SELECT presentation_history FROM course_state"
        ).fetchone()[0] == "{broken"
    finally:
        connection.close()


@pytest.mark.parametrize(
    "definition,message",
    (
        ("TEXT", "lacks required data-version metadata"),
        (
            "TEXT NOT NULL DEFAULT '0.9.0'",
            "invalid data-version default",
        ),
    ),
)
def test_legacy_adoption_preflight_rejects_invalid_existing_provenance_column(
    definition,
    message,
):
    connection, _course_id = _create_course_db(versioned=False)
    try:
        connection.execute(
            f"ALTER TABLE challenge_rounds ADD COLUMN data_version {definition}"
        )
        connection.commit()

        with pytest.raises(RuntimeError, match=message):
            database.validate_legacy_adoption_candidate(connection)
        assert database.inspect_schema_version(connection) is None
    finally:
        connection.close()


def test_maintenance_validators_reject_unadoptable_legacy_database(
    init_module,
    restore_module,
    tmp_path,
):
    path = tmp_path / "malformed-legacy.db"
    connection, _course_id = _create_course_db(path, versioned=False)
    connection.execute(
        """UPDATE course_state
           SET phase = 'ended', presentation_history = '{broken}'"""
    )
    connection.commit()
    connection.close()

    for validator in (
        init_module.validate_course_database,
        restore_module.validate_course_database,
    ):
        with pytest.raises(
            RuntimeError, match="Cannot version malformed presentation history"
        ):
            validator(path, "safe101")
