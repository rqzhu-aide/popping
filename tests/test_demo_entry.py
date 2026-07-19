"""End-to-end tests for private, per-visitor demo instances."""

import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
from demo_instance import is_demo_instance_slug  # noqa: E402


@pytest.fixture
def demo_env(tmp_path, monkeypatch):
    classes_dir = tmp_path / 'classes'
    shutil.copytree(PROJECT_ROOT / 'classes', classes_dir)
    data_dir = tmp_path / 'data'
    data_dir.mkdir()

    monkeypatch.setattr(config, 'CLASSES_DIR', str(classes_dir))
    monkeypatch.setattr(config, 'CONFIG_DIR', str(classes_dir))
    monkeypatch.setattr(config, 'DATA_DIR', str(data_dir))
    monkeypatch.setitem(app_module.app.config, 'TESTING', True)
    monkeypatch.setitem(
        app_module.app.config, 'SECRET_KEY', 'private-demo-entry-test-key'
    )
    app_module._clear_course_availability_cache()
    database._schema_checked.clear()
    yield {
        'classes_dir': classes_dir,
        'data_dir': data_dir,
    }
    app_module._clear_course_availability_cache()
    database._schema_checked.clear()


def _start_demo(client, env):
    response = client.post('/demo/start')
    assert response.status_code == 302
    path = urlparse(response.headers['Location']).path
    slug = path.rstrip('/').rsplit('/', 1)[-1]
    assert is_demo_instance_slug(slug)
    db_path = env['data_dir'] / slug / 'popping.db'
    assert db_path.is_file()
    assert client.get(path).status_code == 200
    return slug, db_path


def _enter_student(client, slug, number):
    response = client.get(f'/demo/{slug}/student/{number}')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')
    assert client.get('/dashboard').status_code == 200


def _enter_instructor(client, slug):
    response = client.get(f'/demo/{slug}/instructor')
    assert response.status_code == 302
    assert response.headers['Location'].endswith(f'/instructor/{slug}')
    assert client.get(f'/instructor/{slug}').status_code == 200


def _state(instructor):
    response = instructor.get('/api/poll')
    assert response.status_code == 200
    return response.get_json()['state']


def _change_phase(instructor, phase):
    state = _state(instructor)
    payload = {
        'phase': phase,
        'expected_phase': state['phase'],
        'expected_session_key': state['session_key'],
        'expected_roster_version': state.get('roster_version', 0),
        'presentation_key': state.get('presentation_key') or '',
    }
    if phase == 'ended':
        payload['confirm_end_session'] = True
    response = instructor.post('/api/set_phase', json=payload)
    assert response.status_code == 200, response.get_json()
    return response


def _database_value(path, query, parameters=()):
    connection = sqlite3.connect(path)
    try:
        return connection.execute(query, parameters).fetchone()[0]
    finally:
        connection.close()


def test_real_demo_course_yaml_matches_small_private_seed():
    yaml_path = PROJECT_ROOT / 'classes' / 'demo' / 'course.yaml'
    assert yaml_path.is_file()
    course = yaml.safe_load(yaml_path.read_text(encoding='utf-8'))
    assert course.get('slug') == 'demo'
    assert course.get('active') is True
    assert course.get('team_pool_size') == 2
    assert course.get('max_teams') == 2
    assert course.get('max_members_per_team') == 2


class TestPrivateDemoEntry:
    def test_landing_is_read_only_until_start(self, demo_env):
        client = app_module.app.test_client()
        response = client.get('/demo')
        assert response.status_code == 200
        assert list(demo_env['data_dir'].iterdir()) == []
        assert b'/demo/start' in response.data

    def test_start_does_not_spawn_a_subprocess(
            self, demo_env, monkeypatch):
        def forbid_subprocess(*_args, **_kwargs):
            raise AssertionError('web demo creation must not spawn a subprocess')

        monkeypatch.setattr(subprocess, 'run', forbid_subprocess)
        client = app_module.app.test_client()
        slug, db_path = _start_demo(client, demo_env)
        assert db_path == demo_env['data_dir'] / slug / 'popping.db'

    def test_instance_page_exposes_three_distinct_roles(self, demo_env):
        creator = app_module.app.test_client()
        slug, _db_path = _start_demo(creator, demo_env)
        page = creator.get(f'/demo/{slug}')
        assert page.status_code == 200
        assert f'/demo/{slug}/instructor'.encode() in page.data
        assert f'/demo/{slug}/student/1'.encode() in page.data
        assert f'/demo/{slug}/student/2'.encode() in page.data

        instructor = app_module.app.test_client()
        student_one = app_module.app.test_client()
        student_two = app_module.app.test_client()
        _enter_instructor(instructor, slug)
        _enter_student(student_one, slug, 1)
        _enter_student(student_two, slug, 2)

        with instructor.session_transaction() as session_data:
            assert session_data['slug'] == slug
            assert session_data['role'] == 'instructor'
            assert session_data['is_demo'] is True
            assert 'student_id' not in session_data
        with student_one.session_transaction() as session_data:
            assert session_data['slug'] == slug
            assert session_data['role'] == 'student'
            assert session_data['student_id'] == 'demo001'
            assert 'instructor_id' not in session_data
        with student_two.session_transaction() as session_data:
            assert session_data['slug'] == slug
            assert session_data['role'] == 'student'
            assert session_data['student_id'] == 'demo002'

    def test_instances_do_not_share_identity_or_team_state(self, demo_env):
        creator_a = app_module.app.test_client()
        creator_b = app_module.app.test_client()
        slug_a, path_a = _start_demo(creator_a, demo_env)
        slug_b, path_b = _start_demo(creator_b, demo_env)
        assert slug_a != slug_b
        assert path_a != path_b

        student_a = app_module.app.test_client()
        student_b = app_module.app.test_client()
        _enter_student(student_a, slug_a, 1)
        _enter_student(student_b, slug_b, 1)
        teams_a = student_a.get('/api/teams').get_json()
        teams_b = student_b.get('/api/teams').get_json()
        assert [team['member_count'] for team in teams_a] == [0, 0]
        assert [team['member_count'] for team in teams_b] == [0, 0]

        joined = student_a.post('/api/join_team', json={'team_id': teams_a[0]['id']})
        assert joined.status_code == 200
        assert [
            team['member_count'] for team in student_a.get('/api/teams').get_json()
        ] == [1, 0]
        assert [
            team['member_count'] for team in student_b.get('/api/teams').get_json()
        ] == [0, 0]
        assert _database_value(
            path_a, "SELECT slug FROM courses"
        ) == slug_a
        assert _database_value(
            path_b, "SELECT slug FROM courses"
        ) == slug_b

    def test_question_assets_are_reused_without_sharing_writable_data(
            self, demo_env):
        creator = app_module.app.test_client()
        slug, db_path = _start_demo(creator, demo_env)
        instructor = app_module.app.test_client()
        _enter_instructor(instructor, slug)

        bank = instructor.get('/api/discussion_questions')
        assert bank.status_code == 200
        titles = {item['title'] for item in bank.get_json()['questions']}
        assert {'Why Ensemble?', 'Tuning Gradient Boosting'}.issubset(titles)
        presentation_titles = set()
        connection = sqlite3.connect(db_path)
        try:
            presentation_titles = {
                row[0] for row in connection.execute(
                    "SELECT title FROM questions WHERE source_key LIKE 'presentation:1:%'"
                ).fetchall()
            }
        finally:
            connection.close()
        assert 'Bagging vs Boosting' in presentation_titles

    def test_invalid_and_legacy_role_urls_do_not_create_shared_demo(
            self, demo_env):
        client = app_module.app.test_client()
        assert client.get('/demo/instructor').status_code == 302
        assert client.get('/demo/student').status_code == 302
        assert client.get('/demo/demo_not_valid/student/1').status_code == 302
        assert list(demo_env['data_dir'].iterdir()) == []

    def test_demo_courses_are_hidden_from_normal_landing(self, demo_env):
        client = app_module.app.test_client()
        slug, _db_path = _start_demo(client, demo_env)
        assert slug not in {course['slug'] for course in app_module._scan_courses()}
        landing = app_module.app.test_client().get('/')
        assert landing.status_code == 200
        assert f'/login/{slug}'.encode() not in landing.data
        assert f'/instructor_login/{slug}'.encode() not in landing.data


class TestDemoReset:
    def test_reset_is_authorized_isolated_and_has_no_subprocess(
            self, demo_env, monkeypatch):
        slug_a, path_a = _start_demo(app_module.app.test_client(), demo_env)
        slug_b, path_b = _start_demo(app_module.app.test_client(), demo_env)
        student_a = app_module.app.test_client()
        student_b = app_module.app.test_client()
        _enter_student(student_a, slug_a, 1)
        _enter_student(student_b, slug_b, 1)
        team_a = student_a.get('/api/teams').get_json()[0]['id']
        team_b = student_b.get('/api/teams').get_json()[0]['id']
        assert student_a.post('/api/join_team', json={'team_id': team_a}).status_code == 200
        assert student_b.post('/api/join_team', json={'team_id': team_b}).status_code == 200

        unauthenticated = app_module.app.test_client()
        assert unauthenticated.post(f'/demo/{slug_a}/reset').status_code == 403
        assert student_b.post(f'/demo/{slug_a}/reset').status_code == 403
        assert unauthenticated.post('/demo/reset').status_code == 403

        def forbid_subprocess(*_args, **_kwargs):
            raise AssertionError('web demo reset must not spawn a subprocess')

        monkeypatch.setattr(subprocess, 'run', forbid_subprocess)
        reset = student_a.post(f'/demo/{slug_a}/reset')
        assert reset.status_code == 302
        assert reset.headers['Location'].endswith(f'/demo/{slug_a}')
        assert _database_value(
            path_a, 'SELECT COUNT(*) FROM students WHERE team_id IS NOT NULL'
        ) == 0
        assert _database_value(
            path_b, 'SELECT COUNT(*) FROM students WHERE team_id IS NOT NULL'
        ) == 1
        assert _database_value(path_a, 'SELECT COUNT(*) FROM students') == 2
        assert _database_value(path_a, 'SELECT COUNT(*) FROM instructors') == 1

    def test_reset_cooldown_survives_role_reentry(self, demo_env):
        slug, _db_path = _start_demo(app_module.app.test_client(), demo_env)
        student = app_module.app.test_client()
        _enter_student(student, slug, 1)
        assert student.post(f'/demo/{slug}/reset').status_code == 302
        _enter_student(student, slug, 1)
        response = student.post(f'/demo/{slug}/reset')
        assert response.status_code == 429
        assert int(response.headers['Retry-After']) >= 1


class TestTwoStudentWorkflow:
    def test_same_team_students_can_record_a_discussion_thumb(self, demo_env):
        slug, db_path = _start_demo(app_module.app.test_client(), demo_env)
        student_one = app_module.app.test_client()
        student_two = app_module.app.test_client()
        instructor = app_module.app.test_client()
        _enter_student(student_one, slug, 1)
        _enter_student(student_two, slug, 2)
        team_id = student_one.get('/api/teams').get_json()[0]['id']
        assert student_one.post('/api/join_team', json={'team_id': team_id}).status_code == 200
        assert student_two.post('/api/join_team', json={'team_id': team_id}).status_code == 200
        _enter_instructor(instructor, slug)
        _change_phase(instructor, 'discussion')

        question = instructor.get('/api/discussion_questions').get_json()['questions'][0]
        state = _state(instructor)
        posted = instructor.post('/api/set_question', json={
            'key': question['key'],
            'title': question['title'],
            'content': question['content'],
            'expected_phase': state['phase'],
            'expected_session_key': state['session_key'],
            'expected_discussion_key': '',
        })
        assert posted.status_code == 200
        discussion_key = posted.get_json()['discussion_question']['key']
        thumb = student_one.post('/api/grade_peer', json={
            'recipient_id': 'demo002',
            'selected': True,
            'question_key': discussion_key,
        })
        assert thumb.status_code == 200
        assert _database_value(path=db_path, query='SELECT COUNT(*) FROM teammate_thumbs') == 1

    def test_split_team_students_can_record_a_presentation_rating(self, demo_env):
        slug, db_path = _start_demo(app_module.app.test_client(), demo_env)
        presenter = app_module.app.test_client()
        rater = app_module.app.test_client()
        instructor = app_module.app.test_client()
        _enter_student(presenter, slug, 1)
        _enter_student(rater, slug, 2)
        teams = presenter.get('/api/teams').get_json()
        assert presenter.post(
            '/api/join_team', json={'team_id': teams[0]['id']}
        ).status_code == 200
        assert rater.post(
            '/api/join_team', json={'team_id': teams[1]['id']}
        ).status_code == 200
        _enter_instructor(instructor, slug)
        _change_phase(instructor, 'competition')

        connection = sqlite3.connect(db_path)
        try:
            question_id = connection.execute(
                "SELECT id FROM questions WHERE source_key = 'presentation:1:1'"
            ).fetchone()[0]
        finally:
            connection.close()
        state = _state(instructor)
        started = instructor.post('/api/start_presentation', json={
            'team_id': teams[0]['id'],
            'question_id': question_id,
            'time_cap': 300,
            'expected_phase': state['phase'],
            'expected_session_key': state['session_key'],
        })
        assert started.status_code == 200, started.get_json()
        presentation_key = started.get_json()['presentation_key']
        state = _state(instructor)
        guard = {
            'expected_phase': state['phase'],
            'expected_session_key': state['session_key'],
            'presentation_key': presentation_key,
        }
        assert instructor.post('/api/start_poll', json=guard).status_code == 200
        rating = rater.post('/api/submit_rating', json={
            'q1_developed': 4,
            'q2_easy': 5,
            'presentation_key': presentation_key,
        })
        assert rating.status_code == 200, rating.get_json()
        assert _database_value(
            db_path, 'SELECT COUNT(*) FROM presentation_ratings'
        ) == 1
