"""Regression tests for the explicit course-schema migration command."""

import builtins
import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "migrate-course-db.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from versioning import SCHEMA_VERSION  # noqa: E402


@pytest.fixture
def migrate_course_module():
    spec = importlib.util.spec_from_file_location(
        "migrate_course_db", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_baseline_course(path, slug="safe101", phase="setup"):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        (PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8")
    )
    instructor_id = connection.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ("teacher", "Teacher", "9999"),
    ).lastrowid
    course_id = connection.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id)
           VALUES (?, ?, ?, ?, ?)""",
        ("Safe Course", "SAFE101", "Test", slug, instructor_id),
    ).lastrowid
    team_id = connection.execute(
        "INSERT INTO teams (course_id, name) VALUES (?, 'Team 1')",
        (course_id,),
    ).lastrowid
    question_id = connection.execute(
        """INSERT INTO questions
           (course_id, question_num, question_text, title, week_num, source_key)
           VALUES (?, 1, 'Question', 'Question', 1, 'week-1-q-1')""",
        (course_id,),
    ).lastrowid
    connection.execute(
        """INSERT INTO students
           (course_id, student_id, name, pin, team_id)
           VALUES (?, 's1', 'Student', '1111', ?)""",
        (course_id, team_id),
    )
    active = phase == "competition"
    connection.execute(
        """INSERT INTO course_state
           (course_id, phase, discussion_week, session_key,
            active_team_id, active_question_id, current_question,
            presentation_started_at, presentation_created_at,
            poll_question_key)
           VALUES (?, ?, 1, 7, ?, ?, ?, ?, ?, ?)""",
        (
            course_id,
            phase,
            team_id if active else None,
            question_id if active else None,
            "Question" if active else None,
            "2026-08-21 12:00:00" if active else None,
            "2026-08-21 12:00:00" if active else None,
            "pres-active" if active else None,
        ),
    )
    connection.commit()
    connection.close()


def _ledger(path):
    with sqlite3.connect(path) as connection:
        return [
            row[0] for row in connection.execute(
                "SELECT schema_version FROM schema_migrations ORDER BY id"
            )
        ]


def _run_cli(module, monkeypatch, slug, responses):
    answers = iter(responses)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": next(answers))
    return module.main([slug])


def test_cli_backs_up_then_migrates_baseline_course_atomically(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "safe101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(
        migrate_course_module,
        monkeypatch,
        slug,
        (slug, migrate_course_module.OFFLINE_CONFIRMATION),
    )

    assert result == 0
    assert _ledger(database_path) == ["1.0.0", SCHEMA_VERSION]
    with sqlite3.connect(database_path) as connection:
        participant_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(presentation_participants)"
            )
        }
        indexes = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert {
        "course_id", "session_key", "week_num", "presentation_key",
        "student_id", "student_identifier", "student_name", "team_id",
        "team_name", "data_version", "created_at",
    }.issubset(participant_columns)
    assert {
        "idx_presentation_participants_student",
        "idx_presentation_participants_session",
        "idx_presentation_participants_export_week",
        "idx_challenge_rounds_challenger",
    }.issubset(indexes)

    backups = list(
        (course_dir / "migration-backups").glob(
            "popping-before-migration-*.db"
        )
    )
    assert len(backups) == 1
    assert _ledger(backups[0]) == ["1.0.0"]


def test_cli_preflight_rejects_active_session_before_confirmation_or_backup(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "active101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug, phase="competition")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt="": pytest.fail("preflight must precede confirmation"),
    )

    assert migrate_course_module.main([slug]) == 1
    assert _ledger(database_path) == ["1.0.0"]
    assert not (course_dir / "migration-backups").exists()


def test_cli_requires_offline_confirmation_before_backup_or_mutation(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "safe101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(
        migrate_course_module,
        monkeypatch,
        slug,
        (slug, "NOT STOPPED"),
    )

    assert result == 1
    assert _ledger(database_path) == ["1.0.0"]
    assert not (course_dir / "migration-backups").exists()


def test_cli_is_a_no_op_when_database_is_already_current(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "safe101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    migrate_course_module.migrate_database(str(database_path), slug)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt="": pytest.fail("current databases need no confirmation"),
    )

    assert migrate_course_module.main([slug]) == 0
    assert _ledger(database_path) == ["1.0.0", SCHEMA_VERSION]
    assert not (course_dir / "migration-backups").exists()


def test_backup_failure_leaves_baseline_database_unchanged(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "safe101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        migrate_course_module,
        "create_migration_backup",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("backup failed")),
    )

    result = _run_cli(
        migrate_course_module,
        monkeypatch,
        slug,
        (slug, migrate_course_module.OFFLINE_CONFIRMATION),
    )

    assert result == 1
    assert _ledger(database_path) == ["1.0.0"]


def test_cli_repairs_missing_current_schema_index_after_backup(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "safe101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug)
    migrate_course_module.migrate_database(str(database_path), slug)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX idx_presentation_participants_student")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(
        migrate_course_module,
        monkeypatch,
        slug,
        (slug, migrate_course_module.OFFLINE_CONFIRMATION),
    )

    assert result == 0
    assert _ledger(database_path) == ["1.0.0", SCHEMA_VERSION]
    with sqlite3.connect(database_path) as connection:
        repaired = connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'index'
                 AND name = 'idx_presentation_participants_student'"""
        ).fetchone()
    assert repaired == (1,)
    assert len(list(
        (course_dir / "migration-backups").glob(
            "popping-before-migration-*.db"
        )
    )) == 1


def test_cli_rejects_malformed_current_schema_before_confirmation_or_backup(
    migrate_course_module, tmp_path, monkeypatch
):
    slug = "safe101"
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    _create_baseline_course(database_path, slug)
    migrate_course_module.migrate_database(str(database_path), slug)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE presentation_participants")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt="": pytest.fail("preflight must precede confirmation"),
    )

    assert migrate_course_module.main([slug]) == 1
    assert _ledger(database_path) == ["1.0.0", SCHEMA_VERSION]
    assert not (course_dir / "migration-backups").exists()
