"""Regression tests for atomic course database restoration."""

import builtins
import importlib.util
import os
from pathlib import Path
import sqlite3

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "restore-course-db.py"
SCHEMA_PATH = PROJECT_ROOT / "popping.sql"


@pytest.fixture
def restore_course_db_module():
    spec = importlib.util.spec_from_file_location("restore_course_db", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_course_db(path, slug, username):
    """Create a fully-seeded course database with realistic classroom data.

    Seeds instructor, course, course_state, two teams, four students,
    two presentation ratings, and one teammate thumb — so that restore
    tests can verify ALL data types survive, not just the instructor row.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    instructor_id = db.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, '2468')",
        (username, f"{username} name"),
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id)
           VALUES ('Recovery Test', 'SAFE 101', 'Test 2026', ?, ?)""",
        (slug, instructor_id),
    ).lastrowid
    db.execute(
        """INSERT INTO course_state
           (course_id, phase, max_teams, max_members_per_team)
           VALUES (?, 'setup', 2, 4)""",
        (course_id,),
    )
    # Two teams
    team_a = db.execute(
        "INSERT INTO teams (course_id, name) VALUES (?, 'Team 1')",
        (course_id,),
    ).lastrowid
    team_b = db.execute(
        "INSERT INTO teams (course_id, name) VALUES (?, 'Team 2')",
        (course_id,),
    ).lastrowid
    # Four students across both teams
    students = []
    for i, (sid, name, team_id) in enumerate([
        ("s001", "Alice", team_a),
        ("s002", "Bob", team_a),
        ("s003", "Carol", team_b),
        ("s004", "Dave", team_b),
    ]):
        row_id = db.execute(
            """INSERT INTO students (course_id, student_id, name, pin, team_id)
               VALUES (?, ?, ?, '1111', ?)""",
            (course_id, sid, name, team_id),
        ).lastrowid
        students.append(row_id)
    # One presentation rating (Bob rates Team 1's presentation)
    db.execute(
        """INSERT INTO presentation_ratings
           (course_id, student_id, question_key, session_key, week_num,
            presenting_team_id, presenting_team_name,
            q1_developed, q2_easy)
           VALUES (?, ?, 'pres-w1-q1', 1, 1, ?, 'Team 1', 4, 3)""",
        (course_id, students[1], team_a),
    )
    # One teammate thumb (Alice gives Bob a thumbs-up)
    db.execute(
        """INSERT INTO teammate_thumbs
           (course_id, session_key, question_key, week_num,
            grader_id, recipient_id, grader_team_id, grader_team_name,
            recipient_team_id, recipient_team_name)
           VALUES (?, 1, 'disc-w1-q1', 1, ?, ?, ?, 'Team 1', ?, 'Team 1')""",
        (course_id, students[0], students[1], team_a, team_a),
    )
    db.commit()
    db.close()


def instructor_username(path):
    db = sqlite3.connect(path)
    try:
        return db.execute("SELECT username FROM instructors").fetchone()[0]
    finally:
        db.close()


def course_data_summary(path):
    """Return a snapshot of all user-generated data for integrity comparison."""
    db = sqlite3.connect(path)
    try:
        return {
            "students": db.execute(
                "SELECT student_id, name, team_id FROM students ORDER BY student_id"
            ).fetchall(),
            "ratings": db.execute(
                """SELECT student_id, question_key, q1_developed, q2_easy
                   FROM presentation_ratings ORDER BY question_key, student_id"""
            ).fetchall(),
            "thumbs": db.execute(
                """SELECT grader_id, recipient_id, question_key
                   FROM teammate_thumbs ORDER BY question_key, grader_id"""
            ).fetchall(),
            "teams": db.execute(
                "SELECT name FROM teams ORDER BY id"
            ).fetchall(),
        }
    finally:
        db.close()


def run_restore(module, monkeypatch, slug, source_path, responses):
    answers = iter(responses)
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    return module.main([slug, str(source_path)])


def test_restore_snapshots_live_db_then_atomically_replaces_it(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "selected-backup.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "safe101", "restored-teacher")
    source_bytes = source_db.read_bytes()
    live_data_before = course_data_summary(live_db)
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    replace_calls = []
    real_replace = restore_course_db_module.os.replace

    def record_replace(source, target):
        replace_calls.append((Path(source), Path(target)))
        return real_replace(source, target)

    monkeypatch.setattr(restore_course_db_module.os, "replace", record_replace)
    result = run_restore(
        restore_course_db_module,
        monkeypatch,
        "safe101",
        source_db,
        ["safe101", "SERVICE STOPPED"],
    )

    assert result == 0
    assert instructor_username(live_db) == "restored-teacher"
    assert source_db.read_bytes() == source_bytes
    # Verify ALL student data survived the restore, not just the instructor
    assert course_data_summary(live_db) == course_data_summary(source_db)
    restore_backups = list(
        (live_db.parent / "restore-backups").glob(
            "popping-before-restore-*.db"
        )
    )
    assert len(restore_backups) == 1
    assert instructor_username(restore_backups[0]) == "current-teacher"
    # Backup must contain the live DB's student data before restore
    assert course_data_summary(restore_backups[0]) == live_data_before
    assert replace_calls[-1][1] == live_db
    assert replace_calls[-1][0].parent == live_db.parent
    assert list(live_db.parent.glob(".popping-restore-*.tmp.db")) == []


def test_restore_rejects_source_for_another_course_before_touching_live_db(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "wrong-course.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "other101", "other-teacher")
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("confirmation should not be requested"),
    )

    result = restore_course_db_module.main(["safe101", str(source_db)])

    assert result == 1
    assert live_db.read_bytes() == original_live
    assert not (live_db.parent / "restore-backups").exists()


def test_restore_rejects_invalid_sqlite_source_before_touching_live_db(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "broken.db"
    create_course_db(live_db, "safe101", "current-teacher")
    source_db.write_bytes(b"this is not a SQLite database")
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("confirmation should not be requested"),
    )

    result = restore_course_db_module.main(["safe101", str(source_db)])

    assert result == 1
    assert live_db.read_bytes() == original_live
    assert not (live_db.parent / "restore-backups").exists()


def test_restore_wrong_confirmation_preserves_live_db_without_backup(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "selected-backup.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "safe101", "restored-teacher")
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    result = run_restore(
        restore_course_db_module,
        monkeypatch,
        "safe101",
        source_db,
        ["wrong-slug"],
    )

    assert result == 1
    assert live_db.read_bytes() == original_live
    assert not (live_db.parent / "restore-backups").exists()
    assert list(live_db.parent.glob(".popping-restore-*.tmp.db")) == []


def test_restore_candidate_failure_keeps_live_db_and_recovery_snapshot(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "selected-backup.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "safe101", "restored-teacher")
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original_validate = restore_course_db_module.validate_course_database

    def reject_staged_candidate(path, expected_slug):
        if Path(path).name.startswith(".popping-restore-"):
            raise RuntimeError("simulated staged validation failure")
        return original_validate(path, expected_slug)

    monkeypatch.setattr(
        restore_course_db_module,
        "validate_course_database",
        reject_staged_candidate,
    )
    result = run_restore(
        restore_course_db_module,
        monkeypatch,
        "safe101",
        source_db,
        ["safe101", "SERVICE STOPPED"],
    )

    assert result == 1
    assert live_db.read_bytes() == original_live
    restore_backups = list(
        (live_db.parent / "restore-backups").glob(
            "popping-before-restore-*.db"
        )
    )
    assert len(restore_backups) == 1
    assert instructor_username(restore_backups[0]) == "current-teacher"
    assert list(live_db.parent.glob(".popping-restore-*.tmp.db")) == []


def test_restore_requires_service_stopped_confirmation(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "selected-backup.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "safe101", "restored-teacher")
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    result = run_restore(
        restore_course_db_module,
        monkeypatch,
        "safe101",
        source_db,
        ["safe101", "still running"],
    )

    assert result == 1
    assert live_db.read_bytes() == original_live
    assert not (live_db.parent / "restore-backups").exists()


def test_restore_rejects_source_without_matching_course_state(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "missing-state.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "safe101", "restored-teacher")
    db = sqlite3.connect(source_db)
    db.execute("DELETE FROM course_state")
    db.commit()
    db.close()
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("confirmation should not be requested"),
    )

    result = restore_course_db_module.main(["safe101", str(source_db)])

    assert result == 1
    assert live_db.read_bytes() == original_live
    assert not (live_db.parent / "restore-backups").exists()


def test_restore_rejects_source_without_interaction_tables(
    restore_course_db_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "missing-interactions.db"
    create_course_db(live_db, "safe101", "current-teacher")
    create_course_db(source_db, "safe101", "restored-teacher")
    with sqlite3.connect(source_db) as db:
        db.execute("DROP TABLE presentation_ratings")
    original_live = live_db.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("confirmation should not be requested"),
    )

    result = restore_course_db_module.main(["safe101", str(source_db)])

    assert result == 1
    assert live_db.read_bytes() == original_live
    assert not (live_db.parent / "restore-backups").exists()


def test_restore_preserves_corrupt_live_database_as_unverified_snapshot(
    restore_course_db_module, tmp_path, monkeypatch, capsys
):
    data_dir = tmp_path / "data"
    live_db = data_dir / "safe101" / "popping.db"
    source_db = tmp_path / "selected-backup.db"
    live_db.parent.mkdir(parents=True)
    corrupt_bytes = b"not a live sqlite database"
    live_db.write_bytes(corrupt_bytes)
    (live_db.parent / "popping.db-wal").write_bytes(b"unverified wal")
    (live_db.parent / "popping.db-shm").write_bytes(b"unverified shm")
    (live_db.parent / "popping.db-journal").write_bytes(b"unverified journal")
    create_course_db(source_db, "safe101", "restored-teacher")
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    result = run_restore(
        restore_course_db_module,
        monkeypatch,
        "safe101",
        source_db,
        ["safe101", "SERVICE STOPPED"],
    )

    assert result == 0
    assert instructor_username(live_db) == "restored-teacher"
    # Verify ALL student data survived the restore, not just the instructor
    assert course_data_summary(live_db) == course_data_summary(source_db)
    backup_dir = live_db.parent / "restore-backups"
    snapshots = list(
        backup_dir.glob("popping-before-restore-unverified-*.db")
    )
    assert len(snapshots) == 1
    assert snapshots[0].read_bytes() == corrupt_bytes
    assert snapshots[0].with_name(snapshots[0].name + "-wal").read_bytes() == (
        b"unverified wal"
    )
    assert snapshots[0].with_name(snapshots[0].name + "-shm").read_bytes() == (
        b"unverified shm"
    )
    assert snapshots[0].with_name(
        snapshots[0].name + "-journal"
    ).read_bytes() == b"unverified journal"
    assert not (live_db.parent / "popping.db-wal").exists()
    assert not (live_db.parent / "popping.db-shm").exists()
    assert not (live_db.parent / "popping.db-journal").exists()
    assert "unverified recovery snapshot" in capsys.readouterr().out


def test_prepare_database_checkpoints_wal_and_removes_sidecars(
    restore_course_db_module, tmp_path
):
    db_path = tmp_path / "popping.db"
    create_course_db(db_path, "safe101", "current-teacher")
    db = sqlite3.connect(db_path)
    assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db.execute("UPDATE courses SET name = 'Checkpointed'")
    db.commit()
    db.close()
    (tmp_path / "popping.db-wal").touch(exist_ok=True)
    (tmp_path / "popping.db-shm").touch(exist_ok=True)

    assert restore_course_db_module.prepare_database_for_replacement(
        str(db_path)
    ) is True

    assert not (tmp_path / "popping.db-wal").exists()
    assert not (tmp_path / "popping.db-shm").exists()
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("SELECT name FROM courses").fetchone()[0] == (
            "Checkpointed"
        )
    finally:
        db.close()


def test_restore_backup_retention_keeps_only_newest_three(
    restore_course_db_module, tmp_path
):
    backup_dir = tmp_path / "restore-backups"
    backup_dir.mkdir()
    names = [
        "popping-before-restore-20260101-000000.db",
        "popping-before-restore-unverified-20260101-000001.db",
        "popping-before-restore-20260101-000002.db",
        "popping-before-restore-unverified-20260101-000003.db",
        "popping-before-restore-20260101-000004.db",
    ]
    for index, name in enumerate(names):
        path = backup_dir / name
        path.write_bytes(str(index).encode("ascii"))
        # Explicit increasing mtimes: rapid touch() calls can land in the
        # same mtime tick, which made the retention order nondeterministic.
        mtime = 1_700_000_000 + index
        os.utime(path, (mtime, mtime))
        path.with_name(path.name + "-wal").write_bytes(b"wal")

    restore_course_db_module.prune_backups(str(backup_dir))

    remaining = sorted(path.name for path in backup_dir.glob("*.db"))
    assert remaining == sorted(names[-3:])
    assert not (backup_dir / f"{names[0]}-wal").exists()
