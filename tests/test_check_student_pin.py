"""Regression tests for the read-only student PIN lookup tool."""

import importlib.util
from pathlib import Path
import sqlite3
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check-student-pin.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import database  # noqa: E402


@pytest.fixture
def lookup_module():
    spec = importlib.util.spec_from_file_location(
        "check_student_pin", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_current_course(path, slug):
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
        "INSERT INTO courses (name, code, semester, slug, instructor_id)"
        " VALUES (?, ?, ?, ?, ?)",
        ("Course", "C101", "Test", slug, instructor_id),
    ).lastrowid
    connection.execute(
        "INSERT INTO course_state (course_id, phase, discussion_week,"
        " session_key) VALUES (?, 'setup', 1, 7)",
        (course_id,),
    )
    connection.commit()
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN IMMEDIATE")
    database.migrate_schema_connection(connection)
    connection.commit()
    connection.close()
    return course_id


def _add_student(path, course_id, student_id, pin, active=True):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO students (course_id, student_id, name, pin, is_active)"
            " VALUES (?, ?, ?, ?, ?)",
            (course_id, student_id, "Roster Name", pin, int(active)),
        )


def _course_database(tmp_path, slug="alpha"):
    data_dir = tmp_path / "data"
    course_dir = data_dir / slug
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    course_id = _create_current_course(database_path, slug)
    return data_dir, database_path, course_id


def test_lookup_is_case_insensitive_and_read_only(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir, database_path, course_id = _course_database(tmp_path)
    _add_student(database_path, course_id, "abc123", "2468")
    original_database = database_path.read_bytes()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["alpha", "ABC123"])

    output = capsys.readouterr().out
    assert result == 0
    assert "Student ID: abc123" in output
    assert "Status: active" in output
    assert "PIN: 2468" in output
    assert database_path.read_bytes() == original_database


def test_lookup_reports_archived_student(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir, database_path, course_id = _course_database(tmp_path)
    _add_student(
        database_path, course_id, "archived01", "1357", active=False
    )
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["alpha", "archived01"])

    output = capsys.readouterr().out
    assert result == 0
    assert "Status: archived (cannot log in)" in output
    assert "PIN: 1357" in output


def test_lookup_missing_student_never_prints_another_pin(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir, database_path, course_id = _course_database(tmp_path)
    _add_student(database_path, course_id, "present01", "8642")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["alpha", "missing01"])

    output = capsys.readouterr().out
    assert result == 1
    assert "Student not found" in output
    assert "8642" not in output


def test_lookup_rejects_invalid_slug_without_creating_a_database(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["../alpha", "abc123"])

    assert result == 1
    assert "Invalid course slug" in capsys.readouterr().out
    assert list(data_dir.iterdir()) == []


def test_lookup_missing_course_does_not_create_it(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["ghost", "abc123"])

    assert result == 1
    assert "Course database not found" in capsys.readouterr().out
    assert not (data_dir / "ghost").exists()


def test_lookup_rejects_database_with_wrong_course_slug(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir = tmp_path / "data"
    course_dir = data_dir / "alpha"
    course_dir.mkdir(parents=True)
    database_path = course_dir / "popping.db"
    course_id = _create_current_course(database_path, "beta")
    _add_student(database_path, course_id, "abc123", "2468")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["alpha", "abc123"])

    output = capsys.readouterr().out
    assert result == 1
    assert "course slug does not match" in output
    assert "2468" not in output


def test_lookup_rejects_case_insensitive_duplicate_ids(
        lookup_module, tmp_path, monkeypatch, capsys):
    data_dir, database_path, course_id = _course_database(tmp_path)
    _add_student(database_path, course_id, "abc123", "2468")
    _add_student(database_path, course_id, "ABC123", "1357")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = lookup_module.main(["alpha", "AbC123"])

    output = capsys.readouterr().out
    assert result == 1
    assert "Multiple student records differ only by letter case" in output
    assert "2468" not in output
    assert "1357" not in output


@pytest.mark.parametrize("args", ([], ["alpha"], ["alpha", "id", "extra"]))
def test_lookup_requires_one_course_and_one_student(
        lookup_module, args, capsys):
    assert lookup_module.main(args) == 1
    assert "Usage: python3 scripts/check-student-pin.py" in (
        capsys.readouterr().out
    )
