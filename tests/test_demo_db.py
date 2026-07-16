"""Smoke tests for demo database initialization.

Verifies that init-demo-db.py:
  - Produces a valid, complete database
  - Honors the DATA_DIR environment variable
  - Resets atomically (no stale sidecars, old data fully replaced)
  - Exits with the correct status for --check
"""
import os
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts', 'init-demo-db.py')
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run_init(data_dir):
    """Run init-demo-db.py with DATA_DIR set, return CompletedProcess."""
    env = dict(os.environ, DATA_DIR=str(data_dir))
    return subprocess.run(
        [sys.executable, SCRIPT], capture_output=True, text=True, env=env, timeout=30
    )


def _open_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


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
    """Verify atomic reset: old data fully replaced, no stale sidecars."""

    def test_reset_replaces_old_data(self, tmp_path):
        result = _run_init(tmp_path)
        assert result.returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'

        # Modify the database
        conn = _open_db(db_path)
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
        conn.close()

    def test_reset_removes_wal_sidecars(self, tmp_path):
        result = _run_init(tmp_path)
        assert result.returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'

        # Create WAL sidecars
        conn = sqlite3.connect(str(db_path))
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute("UPDATE course_state SET phase = 'competition'")
        conn.commit()
        conn.close()

        # Reset
        result = _run_init(tmp_path)
        assert result.returncode == 0

        # Sidecars should be gone
        assert not os.path.exists(str(db_path) + '-wal')
        assert not os.path.exists(str(db_path) + '-shm')


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
