"""Regression tests for upgrading fresh databases from the fixed SQL baseline."""

import builtins
import importlib.util
from pathlib import Path
import sqlite3

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "popping.sql"
INIT_COURSE_SCRIPT = PROJECT_ROOT / "scripts" / "init-course-db.py"
INIT_DEMO_SCRIPT = PROJECT_ROOT / "scripts" / "init-demo-db.py"

import config  # noqa: E402
import database  # noqa: E402
import demo_instance  # noqa: E402


def _load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def init_course_module():
    return _load_script("future_init_course_db", INIT_COURSE_SCRIPT)


def _baseline_connection():
    connection = sqlite3.connect(":memory:")
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return connection


def _ledger_versions(connection):
    return [
        row[0] for row in connection.execute(
            "SELECT schema_version FROM schema_migrations ORDER BY id"
        )
    ]


def _remove_data_version_defaults(connection):
    """Simulate a v1.1 migration that requires explicit provenance stamps."""
    for table in database._VERSIONED_DATA_TABLES:
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()[0]
        index_sql = [
            row[0] for row in connection.execute(
                """SELECT sql FROM sqlite_master
                   WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL""",
                (table,),
            )
        ]
        columns = [
            row[1] for row in connection.execute(f"PRAGMA table_info({table})")
        ]
        replacement = f"__future_{table}"
        create_sql = create_sql.replace(
            f"CREATE TABLE {table}", f'CREATE TABLE "{replacement}"', 1
        )
        old_default = "data_version TEXT NOT NULL DEFAULT '1.0.0'"
        assert create_sql.count(old_default) == 1
        create_sql = create_sql.replace(
            old_default, "data_version TEXT NOT NULL"
        )
        connection.execute(create_sql)
        column_sql = ", ".join(f'"{column}"' for column in columns)
        connection.execute(
            f'INSERT INTO "{replacement}" ({column_sql}) '
            f'SELECT {column_sql} FROM "{table}"'
        )
        connection.execute(f'DROP TABLE "{table}"')
        connection.execute(
            f'ALTER TABLE "{replacement}" RENAME TO "{table}"'
        )
        for statement in index_sql:
            connection.execute(statement)


def _future_migration(connection):
    _remove_data_version_defaults(connection)
    connection.execute("CREATE TABLE future_schema_marker (id INTEGER)")


def _configure_future(monkeypatch, migrations=None):
    monkeypatch.setattr(database, "APP_VERSION", "1.1.0")
    monkeypatch.setattr(database, "SCHEMA_VERSION", "1.1.0")
    monkeypatch.setattr(
        database, "SCHEMA_VERSION_HISTORY", ("1.0.0", "1.1.0")
    )
    monkeypatch.setattr(
        database,
        "_SCHEMA_MIGRATIONS",
        {} if migrations is None else migrations,
    )


def test_sql_remains_fixed_baseline_and_helper_builds_future_prefix(monkeypatch):
    connection = _baseline_connection()
    try:
        assert _ledger_versions(connection) == ["1.0.0"]
        _configure_future(
            monkeypatch,
            {("1.0.0", "1.1.0"): _future_migration},
        )

        assert database.upgrade_schema_connection(connection) == "1.1.0"
        assert _ledger_versions(connection) == ["1.0.0", "1.1.0"]
        assert connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'future_schema_marker'"""
        ).fetchone() is not None
    finally:
        connection.close()


def test_upgrade_helper_resolves_complete_path_before_first_mutation(
    monkeypatch,
):
    connection = _baseline_connection()
    try:
        monkeypatch.setattr(database, "APP_VERSION", "1.2.0")
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.2.0")
        monkeypatch.setattr(
            database,
            "SCHEMA_VERSION_HISTORY",
            ("1.0.0", "1.1.0", "1.2.0"),
        )
        monkeypatch.setattr(
            database,
            "_SCHEMA_MIGRATIONS",
            {("1.0.0", "1.1.0"): _future_migration},
        )

        with pytest.raises(RuntimeError, match="no migration path"):
            database.upgrade_schema_connection(connection)

        assert _ledger_versions(connection) == ["1.0.0"]
        assert connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type = 'table' AND name = 'future_schema_marker'"""
        ).fetchone() is None
    finally:
        connection.close()


def test_upgrade_helper_rolls_back_all_steps_when_migration_fails(monkeypatch):
    connection = _baseline_connection()
    try:
        monkeypatch.setattr(database, "APP_VERSION", "1.2.0")
        monkeypatch.setattr(database, "SCHEMA_VERSION", "1.2.0")
        monkeypatch.setattr(
            database,
            "SCHEMA_VERSION_HISTORY",
            ("1.0.0", "1.1.0", "1.2.0"),
        )

        def fail_second(db):
            db.execute("CREATE TABLE failed_schema_marker (id INTEGER)")
            raise RuntimeError("simulated migration failure")

        monkeypatch.setattr(
            database,
            "_SCHEMA_MIGRATIONS",
            {
                ("1.0.0", "1.1.0"): _future_migration,
                ("1.1.0", "1.2.0"): fail_second,
            },
        )

        with pytest.raises(RuntimeError, match="simulated migration failure"):
            database.upgrade_schema_connection(connection)

        assert _ledger_versions(connection) == ["1.0.0"]
        for marker in ("future_schema_marker", "failed_schema_marker"):
            assert connection.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = ?""",
                (marker,),
            ).fetchone() is None
    finally:
        connection.close()


def test_database_init_bootstraps_future_schema(monkeypatch, tmp_path):
    _configure_future(
        monkeypatch,
        {("1.0.0", "1.1.0"): _future_migration},
    )
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "DATABASE_SCHEMA", str(SCHEMA_PATH))

    database.init_db("future101")

    path = tmp_path / "data" / "future101" / "popping.db"
    with sqlite3.connect(path) as connection:
        assert _ledger_versions(connection) == ["1.0.0", "1.1.0"]
        assert connection.execute(
            "SELECT 1 FROM future_schema_marker"
        ).fetchall() == []


def test_database_init_missing_migration_does_not_publish_database(
    monkeypatch,
    tmp_path,
):
    _configure_future(monkeypatch)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "DATABASE_SCHEMA", str(SCHEMA_PATH))

    with pytest.raises(RuntimeError, match="no migration path"):
        database.init_db("future101")

    course_dir = data_dir / "future101"
    assert not (course_dir / "popping.db").exists()
    assert list(course_dir.glob(".popping-init-*.tmp.db*")) == []


def test_course_candidate_bootstraps_then_validates_future_schema(
    init_course_module,
    monkeypatch,
    tmp_path,
):
    _configure_future(
        monkeypatch,
        {("1.0.0", "1.1.0"): _future_migration},
    )
    monkeypatch.setattr(init_course_module, "SCHEMA_VERSION", "1.1.0")
    path = tmp_path / "candidate.db"
    course_config = {
        "slug": "future101",
        "name": "Future Course",
        "code": "FUT 101",
        "semester": "Test 2027",
    }
    teams = [{"name": "Team 1", "color": "#ef4444"}]

    init_course_module.build_candidate_database(
        str(path),
        str(tmp_path),
        course_config,
        teams,
        1,
        4,
        "teacher",
        "Test Teacher",
        "2468",
    )
    init_course_module.validate_course_database(
        str(path), "future101", require_current_version=True
    )

    with sqlite3.connect(path) as connection:
        assert _ledger_versions(connection) == ["1.0.0", "1.1.0"]
        assert connection.execute(
            "SELECT 1 FROM future_schema_marker"
        ).fetchall() == []


def test_missing_candidate_migration_never_installs_live_database(
    init_course_module,
    monkeypatch,
    tmp_path,
):
    _configure_future(monkeypatch)
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "classes" / "future101"
    config_dir.mkdir(parents=True)
    (config_dir / "course.yaml").write_text(
        """slug: future101
name: Future Course
code: FUT 101
semester: Test 2027
team_pool_size: 2
max_teams: 2
max_members_per_team: 4
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    answers = iter(["teacher", "Test Teacher"])
    monkeypatch.setattr(builtins, "input", lambda _prompt: next(answers))
    monkeypatch.setattr(
        init_course_module.getpass, "getpass", lambda _prompt: "2468"
    )

    assert init_course_module.main([str(config_dir)]) == 1
    course_dir = data_dir / "future101"
    assert not (course_dir / "popping.db").exists()
    assert list(course_dir.glob(".popping-candidate-*.tmp.db")) == []


def test_private_demo_candidate_bootstraps_future_schema(monkeypatch, tmp_path):
    _configure_future(
        monkeypatch,
        {("1.0.0", "1.1.0"): _future_migration},
    )
    slug = "demo_" + "a" * 32
    data_dir = tmp_path / "data"

    demo_instance.create_demo_instance(
        str(data_dir),
        str(PROJECT_ROOT / "classes"),
        str(SCHEMA_PATH),
        slug=slug,
    )

    path = Path(demo_instance.demo_database_path(str(data_dir), slug))
    with sqlite3.connect(path) as connection:
        assert _ledger_versions(connection) == ["1.0.0", "1.1.0"]


def test_shared_demo_candidate_bootstraps_future_schema(monkeypatch, tmp_path):
    module = _load_script("future_init_demo_db", INIT_DEMO_SCRIPT)
    _configure_future(
        monkeypatch,
        {("1.0.0", "1.1.0"): _future_migration},
    )
    path = tmp_path / "shared-demo.db"

    module._build_candidate(str(path))

    with sqlite3.connect(path) as connection:
        assert _ledger_versions(connection) == ["1.0.0", "1.1.0"]
