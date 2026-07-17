"""Smoke tests for demo database initialization.

Verifies that init-demo-db.py:
  - Produces a valid, complete database
  - Honors the DATA_DIR environment variable
  - Resets transactionally while readers remain connected
  - Serializes concurrent first-time initialization
  - Exits with the correct status for --check
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'init-demo-db.py')
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_init(data_dir, *args):
    """Run init-demo-db.py with DATA_DIR set, return CompletedProcess."""
    env = dict(os.environ, DATA_DIR=str(data_dir))
    return subprocess.run(
        [sys.executable, SCRIPT, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _open_db(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _load_init_module(data_dir, monkeypatch):
    """Load the script directly so failure paths can be controlled."""
    monkeypatch.setenv('DATA_DIR', str(data_dir))
    spec = importlib.util.spec_from_file_location('init_demo_db_test', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def demo_db(tmp_path):
    """Create a fresh demo DB in a temp directory."""
    result = _run_init(tmp_path)
    assert result.returncode == 0, f'stderr: {result.stderr}'
    db_path = tmp_path / 'demo' / 'popping.db'
    assert db_path.exists(), f'DB not created at {db_path}'
    yield db_path


class TestDemoDbCreation:
    """Verify the freshly created demo database is valid and complete."""

    def test_integrity_check_passes(self, demo_db):
        conn = _open_db(demo_db)
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        conn.close()

    def test_foreign_key_check_passes(self, demo_db):
        conn = _open_db(demo_db)
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        conn.close()

    def test_one_course_with_slug_demo(self, demo_db):
        conn = _open_db(demo_db)
        row = conn.execute(
            "SELECT count(*) FROM courses WHERE slug = 'demo'"
        ).fetchone()
        assert row[0] == 1
        conn.close()

    def test_four_teams(self, demo_db):
        conn = _open_db(demo_db)
        count = conn.execute('SELECT count(*) FROM teams').fetchone()[0]
        assert count == 4
        conn.close()

    def test_twenty_students_across_teams(self, demo_db):
        conn = _open_db(demo_db)
        total = conn.execute('SELECT count(*) FROM students').fetchone()[0]
        per_team = conn.execute(
            'SELECT count(*) FROM students GROUP BY team_id ORDER BY team_id'
        ).fetchall()
        assert total == 20
        assert len(per_team) == 4
        for row in per_team:
            assert row[0] == 5
        conn.close()

    def test_course_state_in_setup(self, demo_db):
        conn = _open_db(demo_db)
        phase = conn.execute('SELECT phase FROM course_state').fetchone()[0]
        assert phase == 'setup'
        conn.close()

    def test_questions_exist(self, demo_db):
        conn = _open_db(demo_db)
        count = conn.execute('SELECT count(*) FROM questions').fetchone()[0]
        assert count >= 1, 'Demo should have at least one question'
        conn.close()

    def test_required_tables_exist(self, demo_db):
        conn = _open_db(demo_db)
        for table in ('instructors', 'courses', 'teams', 'students',
                       'questions', 'course_state'):
            row = conn.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
                (table,)
            ).fetchone()
            assert row[0] == 1, f'Missing table: {table}'
        conn.close()


class TestDemoDbHonorsDataDir:
    """Verify the script respects the DATA_DIR environment variable."""

    def test_db_created_in_custom_data_dir(self, tmp_path):
        custom = tmp_path / 'custom-data'
        result = _run_init(custom)
        assert result.returncode == 0
        assert (custom / 'demo' / 'popping.db').exists()


class TestDemoDbReset:
    """Verify reset is complete, transactional, and safe for WAL readers."""

    def test_reset_replaces_old_data(self, tmp_path):
        result = _run_init(tmp_path)
        assert result.returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'

        # Modify the database
        conn = _open_db(db_path)
        initial_ids = (
            conn.execute("SELECT id FROM instructors").fetchone()[0],
            conn.execute("SELECT id FROM courses").fetchone()[0],
        )
        conn.execute("UPDATE course_state SET phase = 'competition'")
        conn.execute("INSERT INTO students (course_id, student_id, name, pin) VALUES (1, 'extra', 'Extra', 'x')")
        conn.commit()
        conn.close()

        # Reset
        result = _run_init(tmp_path)
        assert result.returncode == 0

        # Verify old modifications are gone
        conn = _open_db(db_path)
        assert conn.execute("SELECT phase FROM course_state").fetchone()[0] == 'setup'
        assert conn.execute("SELECT count(*) FROM students WHERE student_id = 'extra'").fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM students').fetchone()[0] == 20
        reset_ids = (
            conn.execute("SELECT id FROM instructors").fetchone()[0],
            conn.execute("SELECT id FROM courses").fetchone()[0],
        )
        assert reset_ids == initial_ids == (1, 1)
        conn.close()

    def test_failed_reset_rolls_back_existing_data(self, tmp_path, monkeypatch):
        result = _run_init(tmp_path)
        assert result.returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'

        conn = _open_db(db_path)
        conn.execute("UPDATE course_state SET phase = 'competition'")
        conn.execute(
            "INSERT INTO students (course_id, student_id, name, pin) "
            "VALUES (1, 'sentinel', 'Keep Me', 'x')"
        )
        conn.commit()
        conn.close()

        module = _load_init_module(tmp_path, monkeypatch)
        original_populate = module._populate

        def fail_after_populating(conn):
            original_populate(conn)
            raise RuntimeError('injected reset failure')

        monkeypatch.setattr(module, '_populate', fail_after_populating)
        with pytest.raises(RuntimeError, match='injected reset failure'):
            module.init_demo_db()

        conn = _open_db(db_path)
        assert conn.execute("SELECT phase FROM course_state").fetchone()[0] == 'competition'
        assert conn.execute(
            "SELECT count(*) FROM students WHERE student_id = 'sentinel'"
        ).fetchone()[0] == 1
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        conn.close()

    def test_reset_keeps_live_wal_reader_available(self, tmp_path, monkeypatch):
        result = _run_init(tmp_path)
        assert result.returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'

        setup = _open_db(db_path)
        assert setup.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
        setup.execute("UPDATE course_state SET phase = 'competition'")
        setup.commit()
        setup.close()

        module = _load_init_module(tmp_path, monkeypatch)
        original_populate = module._populate
        writer_ready = threading.Event()
        release_writer = threading.Event()
        writer_errors = []

        def pause_after_populating(conn):
            count = original_populate(conn)
            writer_ready.set()
            if not release_writer.wait(timeout=10):
                raise RuntimeError('reader test timed out')
            return count

        monkeypatch.setattr(module, '_populate', pause_after_populating)

        def forbid_live_database_replace(_source, destination):
            if os.path.abspath(destination) == os.path.abspath(db_path):
                raise AssertionError('reset must not replace the live database')
            return original_replace(_source, destination)

        original_replace = module.os.replace
        monkeypatch.setattr(module.os, 'replace', forbid_live_database_replace)

        reader = _open_db(db_path)
        reader.execute('BEGIN')
        assert reader.execute("SELECT phase FROM course_state").fetchone()[0] == 'competition'

        def reset_in_thread():
            try:
                module.init_demo_db()
            except Exception as exc:
                writer_errors.append(exc)

        writer = threading.Thread(target=reset_in_thread)
        writer.start()
        try:
            assert writer_ready.wait(timeout=10)
            assert db_path.exists()
            assert reader.execute(
                "SELECT phase FROM course_state"
            ).fetchone()[0] == 'competition'

            concurrent_reader = _open_db(db_path)
            try:
                assert concurrent_reader.execute(
                    "SELECT phase FROM course_state"
                ).fetchone()[0] == 'competition'
            finally:
                concurrent_reader.close()

            release_writer.set()
            writer.join(timeout=10)
            assert not writer.is_alive()
            assert writer_errors == []
            assert reader.execute(
                "SELECT phase FROM course_state"
            ).fetchone()[0] == 'competition'
            reader.commit()
        finally:
            release_writer.set()
            writer.join(timeout=10)
            reader.close()

        final = _open_db(db_path)
        try:
            assert final.execute(
                "SELECT phase FROM course_state"
            ).fetchone()[0] == 'setup'
        finally:
            final.close()


class TestDemoDbConcurrency:
    """Verify simultaneous workers publish one complete first demo."""

    def test_ensure_does_not_reset_existing_demo(self, tmp_path):
        assert _run_init(tmp_path).returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'
        conn = _open_db(db_path)
        conn.execute("UPDATE course_state SET phase = 'competition'")
        conn.commit()
        conn.close()

        result = _run_init(tmp_path, '--ensure')
        assert result.returncode == 0, result.stderr

        conn = _open_db(db_path)
        assert conn.execute(
            "SELECT phase FROM course_state"
        ).fetchone()[0] == 'competition'
        conn.close()

    def test_concurrent_ensure_calls_leave_complete_demo(self, tmp_path):
        start_gate = threading.Barrier(4)

        def run_ensure():
            start_gate.wait(timeout=5)
            return _run_init(tmp_path, '--ensure')

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _index: run_ensure(), range(4)))
        for result in results:
            assert result.returncode == 0, (
                f'stdout: {result.stdout}\nstderr: {result.stderr}'
            )

        db_path = tmp_path / 'demo' / 'popping.db'
        conn = _open_db(db_path)
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute("SELECT count(*) FROM courses WHERE slug = 'demo'").fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM teams').fetchone()[0] == 4
        assert conn.execute('SELECT count(*) FROM students').fetchone()[0] == 20
        conn.close()
        assert list((tmp_path / 'demo').glob('.demo-candidate-*')) == []


class TestDemoDbAppIntegration:
    """Verify the web app selects create-only versus explicit reset behavior."""

    def test_app_ensure_uses_create_only_mode_and_configured_data_dir(
            self, tmp_path, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module.config, 'DATA_DIR', str(tmp_path))
        captured = {}

        def fake_run(command, **kwargs):
            captured['command'] = command
            captured['kwargs'] = kwargs
            db_path = tmp_path / 'demo' / 'popping.db'
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.touch()

        monkeypatch.setattr(subprocess, 'run', fake_run)
        assert app_module._ensure_demo_db() is True
        assert captured['command'][-1] == '--ensure'
        assert captured['kwargs']['env']['DATA_DIR'] == str(tmp_path)

    def test_app_reset_uses_explicit_reset_and_configured_data_dir(
            self, tmp_path, monkeypatch):
        import app as app_module

        monkeypatch.setattr(app_module.config, 'DATA_DIR', str(tmp_path))
        captured = {}

        def fake_run(command, **kwargs):
            captured['command'] = command
            captured['kwargs'] = kwargs

        monkeypatch.setattr(subprocess, 'run', fake_run)
        with app_module.app.test_request_context('/demo/reset'):
            response = app_module.demo_reset()

        assert response.status_code == 302
        assert '--ensure' not in captured['command']
        assert captured['kwargs']['env']['DATA_DIR'] == str(tmp_path)


class TestCheckFlag:
    """Verify --check exits correctly."""

    def test_check_returns_1_when_no_db(self, tmp_path):
        env = dict(os.environ, DATA_DIR=str(tmp_path))
        result = subprocess.run(
            [sys.executable, SCRIPT, '--check'], capture_output=True, env=env, timeout=10
        )
        assert result.returncode == 1

    def test_check_returns_0_after_init(self, tmp_path):
        result = _run_init(tmp_path)
        assert result.returncode == 0
        env = dict(os.environ, DATA_DIR=str(tmp_path))
        result = subprocess.run(
            [sys.executable, SCRIPT, '--check'], capture_output=True, env=env, timeout=10
        )
        assert result.returncode == 0
