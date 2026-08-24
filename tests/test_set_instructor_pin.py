"""Regression tests for the command-line instructor PIN tool."""

import importlib.util
from pathlib import Path
import sqlite3
import sys
import types

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "set-instructor-pin.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
import database  # noqa: E402


@pytest.fixture
def pin_module():
    spec = importlib.util.spec_from_file_location(
        "set_instructor_pin", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_current_course(path, slug, pin="9999", migrate=True):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        (PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8")
    )
    instructor_id = connection.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ("teacher", "Teacher", pin),
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
    if migrate:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        database.migrate_schema_connection(connection)
        connection.commit()
    connection.close()


def _stored_pin(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT pin FROM instructors").fetchone()[0]


def _run_cli(module, monkeypatch, argv, pin_answers):
    answers = iter(pin_answers)
    module.getpass = types.SimpleNamespace(
        getpass=lambda _prompt="": next(answers)
    )
    return module.main(argv)


def test_set_pin_updates_every_course_by_default(pin_module, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for slug in ("alpha", "beta"):
        course_dir = data_dir / slug
        course_dir.mkdir(parents=True)
        _create_current_course(course_dir / "popping.db", slug)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(pin_module, monkeypatch, [], ("12345678", "12345678"))

    assert result == 0
    for slug in ("alpha", "beta"):
        assert _stored_pin(data_dir / slug / "popping.db") == "12345678"


def test_set_pin_single_course_only(pin_module, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    for slug in ("alpha", "beta"):
        course_dir = data_dir / slug
        course_dir.mkdir(parents=True)
        _create_current_course(course_dir / "popping.db", slug)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(pin_module, monkeypatch, ["beta"], ("43214321", "43214321"))

    assert result == 0
    assert _stored_pin(data_dir / "alpha" / "popping.db") == "9999"
    assert _stored_pin(data_dir / "beta" / "popping.db") == "43214321"


def test_set_pin_retries_until_valid_and_matching(pin_module, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    course_dir = data_dir / "alpha"
    course_dir.mkdir(parents=True)
    _create_current_course(course_dir / "popping.db", "alpha")
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(
        pin_module,
        monkeypatch,
        ["alpha"],
        (
            "123",            # too short -> retry
            "١٢٣٤",           # Unicode digits -> retry
            "１２３４",          # full-width digits -> retry
            " 1234",          # surrounding whitespace -> retry
            "1234 ",          # surrounding whitespace -> retry
            "12345678",       # valid
            "12345679",       # mismatch -> retry
            "87654321", "87654321",
        ),
    )

    assert result == 0
    assert _stored_pin(course_dir / "popping.db") == "87654321"


@pytest.mark.parametrize(
    "invalid_pin",
    ("123", "1" * 33, "12a4", " 1234", "1234 ", "١٢٣٤"),
)
def test_set_pin_function_rejects_invalid_pin_without_writing(
        pin_module, tmp_path, invalid_pin):
    course_dir = tmp_path / "alpha"
    course_dir.mkdir()
    database_path = course_dir / "popping.db"
    _create_current_course(database_path, "alpha")

    with pytest.raises(ValueError, match="only 4 to 32 digits"):
        pin_module.set_instructor_pin(database_path, "alpha", invalid_pin)

    assert _stored_pin(database_path) == "9999"


def test_set_pin_refuses_unmigrated_course(pin_module, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    course_dir = data_dir / "alpha"
    course_dir.mkdir(parents=True)
    _create_current_course(
        course_dir / "popping.db", "alpha", migrate=False
    )
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(pin_module, monkeypatch, ["alpha"], ("12345678", "12345678"))

    assert result == 1
    # The old PIN is untouched.
    assert _stored_pin(course_dir / "popping.db") == "9999"


def test_set_pin_missing_course(pin_module, tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))

    result = _run_cli(pin_module, monkeypatch, ["ghost"], ("12345678", "12345678"))

    assert result == 1
