"""Database tests for the legacy CLI seed and private web demo instances."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / 'scripts' / 'init-demo-db.py'
SCHEMA = PROJECT_ROOT / 'popping.sql'
CLASSES_DIR = PROJECT_ROOT / 'classes'

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import demo_instance  # noqa: E402


def _run_init(data_dir, *args):
    env = dict(os.environ, DATA_DIR=str(data_dir))
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _open_db(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def _load_init_module(data_dir, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(data_dir))
    spec = importlib.util.spec_from_file_location(
        f'init_demo_db_test_{time.time_ns()}', SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def cli_demo_db(tmp_path):
    result = _run_init(tmp_path)
    assert result.returncode == 0, result.stderr
    path = tmp_path / 'demo' / 'popping.db'
    assert path.is_file()
    return path


def _assert_seed_shape(path, expected_slug='demo'):
    connection = _open_db(path)
    try:
        assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('SELECT COUNT(*) FROM instructors').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM students').fetchone()[0] == 2
        assert connection.execute('SELECT COUNT(*) FROM teams').fetchone()[0] == 2
        assert connection.execute(
            'SELECT COUNT(*) FROM students WHERE team_id IS NULL'
        ).fetchone()[0] == 2
        assert connection.execute(
            'SELECT COUNT(*) FROM courses WHERE slug = ?', [expected_slug]
        ).fetchone()[0] == 1
        state = connection.execute(
            '''SELECT phase, max_teams, max_members_per_team
               FROM course_state'''
        ).fetchone()
        assert tuple(state) == ('setup', 2, 2)
        questions = connection.execute(
            '''SELECT question_num, title, content, source_key
               FROM questions ORDER BY question_num'''
        ).fetchall()
        assert [row['question_num'] for row in questions] == [1, 2, 3, 4]
        assert [row['source_key'] for row in questions] == [
            'week-1-q-bagging-vs-boosting',
            'week-1-q-bias-variance-decomposition',
            'week-1-q-gradient-boosting-parameters',
            'week-1-q-regularization-analysis',
        ]
        assert [row['title'] for row in questions] == [
            'Bagging vs Boosting',
            'Bias-Variance Decomposition',
            'Gradient Boosting Parameters',
            'Regularization Analysis',
        ]
        assert all(row['content'] for row in questions)
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 3
    finally:
        connection.close()


class TestCliDemoSeed:
    """Keep the maintenance script safe even though web requests do not call it."""

    def test_fresh_seed_has_exact_small_shape(self, cli_demo_db):
        _assert_seed_shape(cli_demo_db)

    def test_honors_data_dir(self, tmp_path):
        custom = tmp_path / 'custom-data'
        result = _run_init(custom)
        assert result.returncode == 0, result.stderr
        assert (custom / 'demo' / 'popping.db').is_file()

    def test_reset_restores_database_and_appendix(self, tmp_path):
        assert _run_init(tmp_path).returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'
        appendix_dir = tmp_path / 'demo' / 'appendix'
        appendix = appendix_dir / 'week-1-appendix.md'
        shipped = (CLASSES_DIR / 'demo' / 'week-1-appendix.md').read_text(
            encoding='utf-8'
        )

        connection = _open_db(db_path)
        connection.execute("UPDATE course_state SET phase = 'competition'")
        connection.execute(
            "UPDATE students SET team_id = (SELECT id FROM teams ORDER BY id LIMIT 1)"
        )
        connection.commit()
        connection.close()
        appendix.write_text('changed', encoding='utf-8')
        (appendix_dir / 'week-2-appendix.md').write_text('extra', encoding='utf-8')

        result = _run_init(tmp_path)
        assert result.returncode == 0, result.stderr
        _assert_seed_shape(db_path)
        assert appendix.read_text(encoding='utf-8') == shipped
        assert not (appendix_dir / 'week-2-appendix.md').exists()

    def test_failed_reset_rolls_back_existing_data(self, tmp_path, monkeypatch):
        assert _run_init(tmp_path).returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'
        connection = _open_db(db_path)
        connection.execute("UPDATE course_state SET phase = 'competition'")
        connection.commit()
        connection.close()

        module = _load_init_module(tmp_path, monkeypatch)

        def fail_population(_connection):
            raise RuntimeError('controlled seed failure')

        monkeypatch.setattr(module, '_populate', fail_population)
        with pytest.raises(RuntimeError, match='controlled seed failure'):
            module.init_demo_db()

        connection = _open_db(db_path)
        try:
            assert connection.execute(
                'SELECT phase FROM course_state'
            ).fetchone()[0] == 'competition'
            assert connection.execute('SELECT COUNT(*) FROM students').fetchone()[0] == 2
        finally:
            connection.close()

    def test_ensure_preserves_current_data(self, tmp_path):
        assert _run_init(tmp_path).returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'
        connection = _open_db(db_path)
        connection.execute("UPDATE course_state SET phase = 'competition'")
        connection.commit()
        connection.close()

        result = _run_init(tmp_path, '--ensure')
        assert result.returncode == 0, result.stderr
        connection = _open_db(db_path)
        try:
            assert connection.execute(
                'SELECT phase FROM course_state'
            ).fetchone()[0] == 'competition'
        finally:
            connection.close()

    def test_ensure_upgrades_version_one_seed_to_current_shape(self, tmp_path):
        assert _run_init(tmp_path).returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'
        connection = _open_db(db_path)
        connection.execute('PRAGMA user_version = 1')
        connection.execute(
            "INSERT INTO students (course_id, student_id, name, pin) "
            "VALUES (1, 'old-extra', 'Old Extra', 'demo')"
        )
        connection.commit()
        connection.close()

        result = _run_init(tmp_path, '--ensure')
        assert result.returncode == 0, result.stderr
        _assert_seed_shape(db_path)

    def test_ensure_migrates_version_two_legacy_question_rows(self, tmp_path):
        assert _run_init(tmp_path).returncode == 0
        db_path = tmp_path / 'demo' / 'popping.db'
        connection = _open_db(db_path)
        connection.execute(
            """UPDATE questions
               SET source_key = 'presentation:1:' || question_num,
                   content = NULL"""
        )
        connection.execute('PRAGMA user_version = 2')
        connection.commit()
        connection.close()

        result = _run_init(tmp_path, '--ensure')

        assert result.returncode == 0, result.stderr
        _assert_seed_shape(db_path)

    def test_concurrent_ensure_leaves_one_complete_seed(self, tmp_path):
        def run_ensure(_index):
            return _run_init(tmp_path, '--ensure')

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(run_ensure, range(4)))
        assert all(result.returncode == 0 for result in results), [
            result.stderr for result in results
        ]
        _assert_seed_shape(tmp_path / 'demo' / 'popping.db')
        assert list((tmp_path / 'demo').glob('.demo-candidate-*')) == []

    def test_check_flag(self, tmp_path):
        env = dict(os.environ, DATA_DIR=str(tmp_path))
        missing = subprocess.run(
            [sys.executable, str(SCRIPT), '--check'],
            capture_output=True,
            env=env,
            timeout=10,
        )
        assert missing.returncode == 1
        assert _run_init(tmp_path).returncode == 0
        present = subprocess.run(
            [sys.executable, str(SCRIPT), '--check'],
            capture_output=True,
            env=env,
            timeout=10,
        )
        assert present.returncode == 0


@pytest.fixture
def private_demo_env(tmp_path):
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    return {
        'data_dir': data_dir,
        'classes_dir': CLASSES_DIR,
        'schema': SCHEMA,
    }


def _create_private(env, slug=None):
    instance_slug = demo_instance.create_demo_instance(
        str(env['data_dir']),
        str(env['classes_dir']),
        str(env['schema']),
        slug=slug,
    )
    path = Path(demo_instance.demo_database_path(
        str(env['data_dir']), instance_slug
    ))
    return instance_slug, path


class TestPrivateDemoDatabase:
    def test_instance_uses_random_valid_slug_and_exact_shape(self, private_demo_env):
        slug, path = _create_private(private_demo_env)
        assert demo_instance.is_demo_instance_slug(slug)
        assert demo_instance.canonical_class_slug(slug) == 'demo'
        assert path == private_demo_env['data_dir'] / slug / 'popping.db'
        _assert_seed_shape(path, expected_slug=slug)

    def test_instances_have_separate_databases_and_appendices(self, private_demo_env):
        slug_a, path_a = _create_private(private_demo_env)
        slug_b, path_b = _create_private(private_demo_env)
        assert slug_a != slug_b
        assert path_a != path_b

        connection = _open_db(path_a)
        connection.execute("UPDATE course_state SET phase = 'competition'")
        connection.execute(
            "UPDATE students SET team_id = (SELECT id FROM teams ORDER BY id LIMIT 1) "
            "WHERE student_id = 'demo001'"
        )
        connection.commit()
        connection.close()
        appendix_a = private_demo_env['data_dir'] / slug_a / 'appendix' / 'week-1-appendix.md'
        appendix_b = private_demo_env['data_dir'] / slug_b / 'appendix' / 'week-1-appendix.md'
        appendix_a.write_text('instance A only', encoding='utf-8')

        connection = _open_db(path_b)
        try:
            assert connection.execute(
                'SELECT phase FROM course_state'
            ).fetchone()[0] == 'setup'
            assert connection.execute(
                'SELECT COUNT(*) FROM students WHERE team_id IS NOT NULL'
            ).fetchone()[0] == 0
        finally:
            connection.close()
        assert appendix_b.read_text(encoding='utf-8') != 'instance A only'

    def test_reset_restores_only_requested_instance(self, private_demo_env):
        slug_a, path_a = _create_private(private_demo_env)
        slug_b, path_b = _create_private(private_demo_env)
        for path in (path_a, path_b):
            connection = _open_db(path)
            connection.execute("UPDATE course_state SET phase = 'competition'")
            connection.execute(
                "UPDATE students SET team_id = (SELECT id FROM teams ORDER BY id LIMIT 1) "
                "WHERE student_id = 'demo001'"
            )
            connection.commit()
            connection.close()

        demo_instance.reset_demo_instance(
            str(private_demo_env['data_dir']),
            str(private_demo_env['classes_dir']),
            slug_a,
        )
        _assert_seed_shape(path_a, expected_slug=slug_a)
        connection = _open_db(path_b)
        try:
            assert connection.execute(
                'SELECT phase FROM course_state'
            ).fetchone()[0] == 'competition'
            assert connection.execute(
                'SELECT COUNT(*) FROM students WHERE team_id IS NOT NULL'
            ).fetchone()[0] == 1
        finally:
            connection.close()

    def test_reset_cooldown_is_shared_by_the_demo_database(
            self, private_demo_env):
        slug, path = _create_private(private_demo_env)
        args = (
            str(private_demo_env['data_dir']),
            str(private_demo_env['classes_dir']),
            slug,
        )

        demo_instance.reset_demo_instance(
            *args, cooldown_seconds=10, now=1000
        )
        with pytest.raises(demo_instance.DemoResetCooldown) as exc_info:
            demo_instance.reset_demo_instance(
                *args, cooldown_seconds=10, now=1005
            )
        assert 4.9 <= exc_info.value.retry_after <= 5.1

        demo_instance.reset_demo_instance(
            *args, cooldown_seconds=10, now=1010
        )
        _assert_seed_shape(path, expected_slug=slug)

    def test_bounded_creation_prunes_an_interrupted_candidate(
            self, private_demo_env):
        partial_slug = 'demo_' + 'a' * 32
        partial_dir = private_demo_env['data_dir'] / partial_slug
        partial_dir.mkdir()
        (partial_dir / '.candidate-interrupted.db').write_bytes(b'partial')

        slug, removed = demo_instance.create_bounded_demo_instance(
            str(private_demo_env['data_dir']),
            str(private_demo_env['classes_dir']),
            str(private_demo_env['schema']),
        )

        assert partial_slug in removed
        assert not partial_dir.exists()
        assert demo_instance.is_demo_instance_slug(slug)
        assert demo_instance.count_demo_instances(
            str(private_demo_env['data_dir'])
        ) == 1

    def test_bounded_creation_never_exceeds_four_across_processes(
            self, private_demo_env):
        for _index in range(3):
            _create_private(private_demo_env)

        gate = private_demo_env['data_dir'] / 'start-workers'
        worker = """
import sys
import time
from pathlib import Path

import demo_instance

gate = Path(sys.argv[4])
deadline = time.time() + 20
while not gate.exists():
    if time.time() >= deadline:
        raise RuntimeError('worker gate timed out')
    time.sleep(0.01)
slug, _removed = demo_instance.create_bounded_demo_instance(
    sys.argv[1], sys.argv[2], sys.argv[3], lock_timeout=10
)
print(slug or 'NONE')
"""
        args = [
            str(private_demo_env['data_dir']),
            str(private_demo_env['classes_dir']),
            str(private_demo_env['schema']),
            str(gate),
        ]
        processes = [
            subprocess.Popen(
                [sys.executable, '-c', worker, *args],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _index in range(3)
        ]
        gate.touch()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            results.append(stdout.strip())

        assert sum(value.startswith('demo_') for value in results) == 1
        assert results.count('NONE') == 2
        assert demo_instance.count_demo_instances(
            str(private_demo_env['data_dir'])
        ) == 4

    @pytest.mark.parametrize('slug', [
        'demo',
        'demo_bad',
        'demo_' + 'a' * 31,
        'demo_' + 'g' * 32,
        '../demo_' + 'a' * 32,
    ])
    def test_rejects_invalid_instance_slugs(self, private_demo_env, slug):
        with pytest.raises(ValueError, match='Invalid demo instance'):
            _create_private(private_demo_env, slug=slug)

    def test_cleanup_removes_expired_instance_but_keeps_fresh_one(
            self, private_demo_env):
        expired_slug, _path = _create_private(private_demo_env)
        fresh_slug, _path = _create_private(private_demo_env)
        old_time = time.time() - demo_instance.DEMO_INSTANCE_TTL_SECONDS - 1
        demo_instance.touch_demo_instance(
            str(private_demo_env['data_dir']), expired_slug, now=old_time
        )

        removed = demo_instance.cleanup_expired_demo_instances(
            str(private_demo_env['data_dir']), now=time.time()
        )
        assert removed == [expired_slug]
        assert not (private_demo_env['data_dir'] / expired_slug).exists()
        assert (private_demo_env['data_dir'] / fresh_slug).is_dir()
