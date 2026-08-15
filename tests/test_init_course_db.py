"""Regression tests for safe course database initialization."""

import builtins
import importlib.util
from pathlib import Path
import sqlite3

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "init-course-db.py"


@pytest.fixture
def init_course_db_module():
    spec = importlib.util.spec_from_file_location("init_course_db", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def course_config(tmp_path):
    config_dir = tmp_path / "classes" / "safe101"
    config_dir.mkdir(parents=True)
    (config_dir / "course.yaml").write_text(
        """slug: safe101
name: Safe Initialization
code: SAFE 101
semester: Test 2026
team_pool_size: 3
max_teams: 2
max_members_per_team: 4
teams:
  - color: '#ef4444'
  - color: '#3b82f6'
""",
        encoding="utf-8",
    )
    week_dir = config_dir / "week1"
    week_dir.mkdir()
    (week_dir / "index.md").write_text(
        "1. First presentation question\n2. Second presentation question\n",
        encoding="utf-8",
    )
    (week_dir / "q01.html").write_text("<p>First question</p>\n", encoding="utf-8")
    (week_dir / "q02.html").write_text("<p>Second question</p>\n", encoding="utf-8")
    (config_dir / "week-1-questions.md").write_text(
        """---
title: First discussion question
id: discussion-1
---

Discuss the first question.
""",
        encoding="utf-8",
    )
    return config_dir


def run_initializer(module, monkeypatch, config_dir, responses, pin="2468"):
    answers = iter(responses)
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: pin)
    return module.main([str(config_dir)])


def assert_valid_course_db(path, slug="safe101"):
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("SELECT slug FROM courses").fetchall() == [(slug,)]
        course_id = db.execute("SELECT id FROM courses").fetchone()[0]
        assert db.execute(
            "SELECT course_id FROM course_state"
        ).fetchall() == [(course_id,)]
    finally:
        db.close()


def test_initializer_honors_data_dir_and_validates_new_database(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "external-data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    )

    db_path = data_dir / "safe101" / "popping.db"
    assert result == 0
    assert db_path.is_file()
    assert_valid_course_db(db_path)
    db = sqlite3.connect(db_path)
    try:
        assert db.execute(
            "SELECT max_teams, max_members_per_team FROM course_state"
        ).fetchone() == (2, 4)
        assert db.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 3
        question = db.execute(
            """SELECT question_num, title, content, source_key
               FROM questions"""
        ).fetchone()
        assert question == (
            1,
            "First discussion question",
            "Discuss the first question.",
            "week-1-q-discussion-1",
        )
    finally:
        db.close()
    assert list(db_path.parent.glob(".popping-candidate-*.tmp.db")) == []


def test_replacement_requires_slug_and_creates_recoverable_sqlite_backup(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["old-teacher", "Old Teacher"],
    ) == 0

    db_path = data_dir / "safe101" / "popping.db"
    db = sqlite3.connect(db_path)
    course_id = db.execute("SELECT id FROM courses").fetchone()[0]
    team_id = db.execute("SELECT id FROM teams ORDER BY id LIMIT 1").fetchone()[0]
    db.execute(
        """INSERT INTO students
           (course_id, student_id, name, pin, team_id)
           VALUES (?, 'legacy-student', 'Legacy Student', '1111', ?)""",
        (course_id, team_id),
    )
    db.commit()
    db.close()

    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["new-teacher", "New Teacher", "safe101", "SERVICE STOPPED"],
    ) == 0

    assert_valid_course_db(db_path)
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT username FROM instructors").fetchone()[0] == (
            "new-teacher"
        )
        assert db.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
    finally:
        db.close()

    backups = list(
        (db_path.parent / "init-backups").glob("popping-before-init-*.db")
    )
    assert len(backups) == 1
    assert_valid_course_db(backups[0])
    backup = sqlite3.connect(backups[0])
    try:
        assert backup.execute("SELECT username FROM instructors").fetchone()[0] == (
            "old-teacher"
        )
        assert backup.execute(
            "SELECT student_id FROM students"
        ).fetchone()[0] == "legacy-student"
    finally:
        backup.close()


def test_wrong_slug_confirmation_leaves_existing_database_unchanged(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["old-teacher", "Old Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"
    original_bytes = db_path.read_bytes()

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["new-teacher", "New Teacher", "wrong-slug"],
    )

    assert result == 1
    assert db_path.read_bytes() == original_bytes
    assert_valid_course_db(db_path)
    assert not (db_path.parent / "init-backups").exists()
    assert list(db_path.parent.glob(".popping-candidate-*.tmp.db")) == []


def test_candidate_validation_failure_preserves_live_db_and_cleans_temp_file(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["old-teacher", "Old Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"
    original_bytes = db_path.read_bytes()
    original_validate = init_course_db_module.validate_course_database

    def reject_candidate(path, expected_slug):
        if Path(path).name.startswith(".popping-candidate-"):
            raise RuntimeError("simulated validation failure")
        return original_validate(path, expected_slug)

    monkeypatch.setattr(
        init_course_db_module, "validate_course_database", reject_candidate
    )
    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["new-teacher", "New Teacher"],
    )

    assert result == 1
    assert db_path.read_bytes() == original_bytes
    assert not (db_path.parent / "init-backups").exists()
    assert list(db_path.parent.glob(".popping-candidate-*.tmp.db")) == []


def test_validation_rejects_foreign_key_errors_and_wrong_course_slug(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"

    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO teams (course_id, name) VALUES (999999, 'Orphan Team')"
    )
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="foreign key check"):
        init_course_db_module.validate_course_database(db_path, "safe101")

    db = sqlite3.connect(db_path)
    db.execute("DELETE FROM teams WHERE name = 'Orphan Team'")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="course slug"):
        init_course_db_module.validate_course_database(db_path, "other-course")


def test_initializer_rejects_team_capacity_above_99(init_course_db_module):
    config = {
        "teams": [{"color": "#ef4444"}],
        "max_teams": 1,
        "max_members_per_team": 100,
    }
    team_rows = init_course_db_module.build_team_rows(config)

    with pytest.raises(ValueError, match="between 1 and 99"):
        init_course_db_module.course_defaults(config, team_rows)


@pytest.mark.parametrize(
    "config, message",
    (
        ({"team_pool_size": 101}, "between 1 and 100"),
        ({"teams": [{"name": f"Team {i}"} for i in range(101)]},
         "No more than 100"),
    ),
)
def test_initializer_caps_team_pool_at_100(
        init_course_db_module, config, message):
    with pytest.raises(ValueError, match=message):
        init_course_db_module.build_team_rows(config)


def test_initializer_rejects_invalid_canonical_catalog_before_writing_database(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    (course_config / "week-1-questions.md").write_text(
        "---\ntitle: Missing stable id\n---\n\nDiscuss this.\n",
        encoding="utf-8",
    )

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        [],
    )

    assert result == 1
    assert not (data_dir / "safe101" / "popping.db").exists()


def test_initializer_allows_new_course_without_question_material(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    (course_config / "week-1-questions.md").unlink()
    for path in (course_config / "week1").iterdir():
        path.unlink()
    (course_config / "week1").rmdir()

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    )

    db_path = data_dir / "safe101" / "popping.db"
    assert result == 0
    assert_valid_course_db(db_path)
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    finally:
        db.close()


def test_initializer_validates_only_material_that_exists(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    for path in (course_config / "week1").iterdir():
        path.unlink()
    (course_config / "week1").rmdir()

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    )

    assert result == 0
    assert_valid_course_db(data_dir / "safe101" / "popping.db")


def test_initializer_ignores_legacy_presentation_material_without_canonical_file(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    (course_config / "week-1-questions.md").unlink()

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    )

    db_path = data_dir / "safe101" / "popping.db"
    assert result == 0
    assert_valid_course_db(db_path)
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 0
    finally:
        db.close()


def test_initializer_accepts_utf8_bom_in_config_and_canonical_questions(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    config_path = course_config / "course.yaml"
    config_path.write_bytes(b"\xef\xbb\xbf" + config_path.read_bytes())
    question_path = course_config / "week-1-questions.md"
    question_path.write_bytes(b"\xef\xbb\xbf" + question_path.read_bytes())

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    )

    assert result == 0
    db = sqlite3.connect(data_dir / "safe101" / "popping.db")
    try:
        question = db.execute(
            """SELECT title, content, source_key FROM questions"""
        ).fetchone()
        assert question == (
            "First discussion question",
            "Discuss the first question.",
            "week-1-q-discussion-1",
        )
    finally:
        db.close()


def test_initializer_rejects_missing_course_state(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"
    db = sqlite3.connect(db_path)
    db.execute("DELETE FROM course_state")
    db.commit()
    db.close()

    with pytest.raises(RuntimeError, match="matching course_state"):
        init_course_db_module.validate_course_database(db_path, "safe101")


def test_initializer_validation_requires_interaction_tables(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TABLE teammate_thumbs")

    with pytest.raises(RuntimeError, match="teammate_thumbs"):
        init_course_db_module.validate_course_database(db_path, "safe101")


def test_replacement_requires_service_stopped_confirmation(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["old-teacher", "Old Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"
    original_bytes = db_path.read_bytes()

    result = run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["new-teacher", "New Teacher", "safe101", "still running"],
    )

    assert result == 1
    assert db_path.read_bytes() == original_bytes
    assert not (db_path.parent / "init-backups").exists()


def test_init_backup_retention_keeps_only_newest_three(
    init_course_db_module, tmp_path
):
    backup_dir = tmp_path / "init-backups"
    backup_dir.mkdir()
    for index in range(5):
        path = backup_dir / f"popping-before-init-20260101-00000{index}.db"
        path.write_bytes(str(index).encode("ascii"))
        path.touch()
        path.with_name(path.name + "-wal").write_bytes(b"wal")
        path.with_name(path.name + "-shm").write_bytes(b"shm")

    init_course_db_module.prune_backups(
        str(backup_dir), "popping-before-init-"
    )

    remaining = sorted(backup_dir.glob("*.db"))
    assert [path.name for path in remaining] == [
        "popping-before-init-20260101-000002.db",
        "popping-before-init-20260101-000003.db",
        "popping-before-init-20260101-000004.db",
    ]
    assert not (
        backup_dir / "popping-before-init-20260101-000000.db-wal"
    ).exists()


def test_init_prepare_checkpoints_wal_and_removes_sidecars(
    init_course_db_module, course_config, tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    assert run_initializer(
        init_course_db_module,
        monkeypatch,
        course_config,
        ["teacher", "Test Teacher"],
    ) == 0
    db_path = data_dir / "safe101" / "popping.db"
    db = sqlite3.connect(db_path)
    assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    db.execute("UPDATE courses SET name = 'Checkpointed'")
    db.commit()
    db.close()
    db_path.with_name("popping.db-wal").touch(exist_ok=True)
    db_path.with_name("popping.db-shm").touch(exist_ok=True)
    db_path.with_name("popping.db-journal").touch(exist_ok=True)

    init_course_db_module.prepare_database_for_replacement(str(db_path))

    assert not db_path.with_name("popping.db-wal").exists()
    assert not db_path.with_name("popping.db-shm").exists()
    assert not db_path.with_name("popping.db-journal").exists()
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert db.execute("SELECT name FROM courses").fetchone()[0] == (
            "Checkpointed"
        )
    finally:
        db.close()
