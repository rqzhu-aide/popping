"""End-to-end tests for the demo entry flow.

Regression guard for the production bug where both demo roles bounced
silently back to the landing page. Root causes were:
  - classes/demo/course.yaml missing from the repo (availability 'invalid')
  - init-demo-db.py seeding the demo course with is_active=0, which the
    availability check refuses

Entry-flow tests run against a *copy* of the repo's classes/ tree, because
the app's one-time appendix migration would otherwise move tracked files
out of the working tree. A separate test asserts the real committed
course.yaml exists and is valid.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module  # noqa: E402
import config  # noqa: E402


SCRIPT = PROJECT_ROOT / 'scripts' / 'init-demo-db.py'


def _run_init(data_dir, *args):
    env = dict(os.environ, DATA_DIR=str(data_dir))
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    """Copy of repo classes/ + temporary DATA_DIR with a fresh demo DB."""
    classes_dir = tmp_path / 'classes'
    shutil.copytree(PROJECT_ROOT / 'classes', classes_dir)
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    monkeypatch.setattr(config, 'CLASSES_DIR', str(classes_dir))
    monkeypatch.setattr(config, 'CONFIG_DIR', str(classes_dir))
    monkeypatch.setattr(config, 'DATA_DIR', str(data_dir))
    monkeypatch.setitem(app_module.app.config, 'TESTING', True)
    monkeypatch.setitem(app_module.app.config, 'SECRET_KEY', 'demo-entry-test-key')
    app_module._clear_course_availability_cache()
    result = _run_init(data_dir)
    assert result.returncode == 0, f'stderr: {result.stderr}'
    yield {'data_dir': data_dir, 'db_path': data_dir / 'demo' / 'popping.db'}
    app_module._clear_course_availability_cache()


def test_real_demo_course_yaml_committed():
    """The shipped classes/demo/course.yaml must exist and be active."""
    yaml_path = PROJECT_ROOT / 'classes' / 'demo' / 'course.yaml'
    assert yaml_path.is_file(), 'classes/demo/course.yaml is not committed'
    cfg = yaml.safe_load(yaml_path.read_text())
    assert cfg.get('slug') == 'demo'
    assert cfg.get('active') is True


class TestDemoEntry:
    def test_student_enters_dashboard(self, demo_env):
        client = app_module.app.test_client()
        resp = client.get('/demo/student')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/dashboard')
        assert client.get('/dashboard').status_code == 200
        poll = client.get('/api/poll')
        assert poll.status_code == 200
        assert poll.get_json()['state']['phase'] in (
            'setup', 'discussion', 'competition', 'ended')

    def test_instructor_enters_dashboard(self, demo_env):
        client = app_module.app.test_client()
        resp = client.get('/demo/instructor')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/instructor/demo')
        assert client.get('/instructor/demo').status_code == 200
        assert client.get('/api/poll').status_code == 200

    def test_demo_hidden_from_landing(self, demo_env):
        slugs = [c['slug'] for c in app_module._scan_courses()]
        assert 'demo' not in slugs
        client = app_module.app.test_client()
        resp = client.get('/')
        assert resp.status_code == 200
        assert b'/login/demo' not in resp.data
        assert b'/instructor_login/demo' not in resp.data

    def test_demo_db_created_on_demand(self, demo_env):
        """Entry works even when the DB does not exist yet."""
        os.remove(demo_env['db_path'])
        client = app_module.app.test_client()
        resp = client.get('/demo/student')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/dashboard')
        assert demo_env['db_path'].exists()

    def test_existing_inactive_demo_db_is_healed(self, demo_env):
        """Databases seeded before is_active=1 was required are fixed in place."""
        conn = sqlite3.connect(demo_env['db_path'])
        conn.execute("UPDATE courses SET is_active = 0 WHERE slug = 'demo'")
        conn.commit()
        conn.close()

        client = app_module.app.test_client()
        resp = client.get('/demo/student')
        assert resp.status_code == 302
        assert resp.headers['Location'].endswith('/dashboard')
        assert client.get('/api/poll').status_code == 200

        conn = sqlite3.connect(demo_env['db_path'])
        is_active = conn.execute(
            "SELECT is_active FROM courses WHERE slug = 'demo'").fetchone()[0]
        conn.close()
        assert is_active == 1
