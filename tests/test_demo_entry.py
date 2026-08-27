"""End-to-end tests for private, per-visitor demo instances."""

import os
import shutil
import sqlite3
import subprocess
import sys
import time
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
from demo_instance import (  # noqa: E402
    DEMO_INSTANCE_TTL_SECONDS,
    cleanup_expired_demo_instances,
    is_demo_instance_slug,
    touch_demo_instance,
)


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
    response = client.post(f'/demo/{slug}/student/{number}')
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')
    assert client.get('/dashboard').status_code == 200


def _enter_instructor(client, slug):
    response = client.post(f'/demo/{slug}/instructor')
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


def test_demo_guidance_does_not_claim_tabs_have_separate_roles():
    template = (PROJECT_ROOT / 'templates' / 'demo.html').read_text(encoding='utf-8')
    assert 'Open in another tab' not in template
    assert 'Tabs in the same browser share one sign-in session' in template
    assert 'one regular and one private browsing window' in template


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
        assert b'id="quick-roll-widget"' in instructor.get(
            f'/instructor/{slug}'
        ).data
        role_page = instructor.get(f'/demo/{slug}')
        assert b'onsubmit="return confirmDemoReset(this)"' in role_page.data

        _enter_student(student_one, slug, 1)
        _enter_student(student_two, slug, 2)
        assert b'id="quick-roll-widget"' not in student_one.get('/dashboard').data

        with instructor.session_transaction() as session_data:
            assert session_data['slug'] == slug
            assert session_data['role'] == 'instructor'
            assert session_data['is_demo'] is True
            assert 'student_id' not in session_data
            assert len(session_data['instructor_auth_token']) == 64
            assert 'pin' not in session_data
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
        discussion_titles = {
            item['title'] for item in bank.get_json()['questions']
            if item['source'] == 'bank'
        }
        presentation_titles = set()
        connection = sqlite3.connect(db_path)
        try:
            presentation_titles = {
                row[0] for row in connection.execute(
                    "SELECT title FROM questions WHERE source_key LIKE 'week-1-q-%'"
                ).fetchall()
            }
        finally:
            connection.close()
        assert discussion_titles == presentation_titles == {
            'Bagging vs Boosting',
            'Bias-Variance Decomposition',
            'Gradient Boosting Parameters',
            'Regularization Analysis',
        }

    def test_invalid_and_legacy_role_urls_do_not_create_shared_demo(
            self, demo_env):
        client = app_module.app.test_client()
        assert client.get('/demo/instructor').status_code == 302
        assert client.get('/demo/student').status_code == 302
        assert client.post('/demo/demo_not_valid/student/1').status_code == 302
        assert list(demo_env['data_dir'].iterdir()) == []

    def test_role_entry_logout_and_exit_are_post_only(self, demo_env):
        client = app_module.app.test_client()
        slug, _db_path = _start_demo(client, demo_env)
        assert client.get(f'/demo/{slug}/instructor').status_code == 405
        assert client.get(f'/demo/{slug}/student/1').status_code == 405
        assert client.get('/logout').status_code == 405

        # GET /demo/exit falls through to the read-only instance page route;
        # it must not clear the demo session.
        _enter_student(client, slug, 1)
        assert client.get('/demo/exit').status_code == 302
        with client.session_transaction() as session_data:
            assert session_data.get('is_demo') is True
        assert client.post('/demo/exit').status_code == 302
        with client.session_transaction() as session_data:
            assert set(session_data) == {"demo_instance_slug"}
            assert session_data["demo_instance_slug"] == slug

    def test_demo_courses_are_hidden_from_normal_landing(self, demo_env):
        client = app_module.app.test_client()
        slug, _db_path = _start_demo(client, demo_env)
        assert slug not in {course['slug'] for course in app_module._scan_courses()}
        landing = app_module.app.test_client().get('/')
        assert landing.status_code == 200
        assert f'/login/{slug}'.encode() not in landing.data
        assert f'/instructor_login/{slug}'.encode() not in landing.data

    def test_demo_roster_is_fixed_in_the_ui_and_all_mutation_routes(
            self, demo_env):
        slug, db_path = _start_demo(app_module.app.test_client(), demo_env)
        instructor = app_module.app.test_client()
        _enter_instructor(instructor, slug)

        page = instructor.get(f'/instructor/{slug}').get_data(as_text=True)
        assert 'data-demo="1"' in page
        assert 'Student Management' in page
        assert 'Add Student' not in page
        assert 'Download Student Roster' not in page
        assert 'Upload Student Roster' not in page
        assert 'Download Roster Template' not in page
        assert 'Reset Course Data' not in page
        assert '<th>Action</th>' not in page
        assert instructor.get(
            f'/export/{slug}/active-roster.csv'
        ).status_code == 403

        state = _state(instructor)
        guard = {
            'expected_phase': state['phase'],
            'expected_session_key': state['session_key'],
            'expected_roster_version': state['roster_version'],
        }
        student_db_id = _database_value(
            db_path,
            "SELECT id FROM students WHERE student_id = 'demo001'",
        )
        assert instructor.post('/api/add_student', json={
            **guard,
            'student_id': 'demo003',
            'name': 'Extra Student',
            'pin': '1234',
        }).status_code == 403
        assert instructor.post('/api/upload_roster').status_code == 403
        assert instructor.delete(
            f'/api/remove_student/{student_db_id}', json=guard
        ).status_code == 403
        assert instructor.post('/api/reset_data', json={
            **guard,
            'confirm_slug': slug,
        }).status_code == 403

        # The slug itself enforces the rule, even if a session did not enter
        # through the private demo role picker.
        with instructor.session_transaction() as session_data:
            session_data.pop('is_demo', None)
        assert instructor.get(
            f'/export/{slug}/active-roster.csv'
        ).status_code == 403
        assert instructor.post('/api/add_student', json={
            **guard,
            'student_id': 'demo003',
            'name': 'Extra Student',
            'pin': '1234',
        }).status_code == 403
        assert _database_value(
            db_path, 'SELECT COUNT(*) FROM students WHERE is_active = 1'
        ) == 2
        assert _database_value(
            db_path,
            """SELECT COUNT(*) FROM students
               WHERE is_active = 1
                 AND student_id NOT IN ('demo001', 'demo002')""",
        ) == 0

    def test_demo_role_buttons_resolve_the_two_seed_ids_exactly(
            self, demo_env):
        slug, db_path = _start_demo(app_module.app.test_client(), demo_env)
        connection = sqlite3.connect(db_path)
        try:
            course_id = connection.execute(
                'SELECT id FROM courses LIMIT 1'
            ).fetchone()[0]
            connection.execute(
                "UPDATE students SET is_active = 0 WHERE student_id = 'demo001'"
            )
            connection.execute(
                '''INSERT INTO students
                   (course_id, student_id, name, pin, is_active)
                   VALUES (?, 'intruder', 'Not a demo role', 'demo', 1)''',
                [course_id],
            )
            connection.commit()
        finally:
            connection.close()

        student_one = app_module.app.test_client()
        unavailable = student_one.post(f'/demo/{slug}/student/1')
        assert unavailable.status_code == 302
        assert unavailable.headers['Location'].endswith(f'/demo/{slug}')

        student_two = app_module.app.test_client()
        _enter_student(student_two, slug, 2)
        with student_two.session_transaction() as session_data:
            assert session_data['student_id'] == 'demo002'


    def test_missing_demo_database_ends_session(self, demo_env):
        client = app_module.app.test_client()
        slug, db_path = _start_demo(client, demo_env)
        _enter_instructor(client, slug)
        assert client.get('/api/poll').status_code == 200

        offline_path = db_path.with_name('popping.db.expired')
        db_path.replace(offline_path)
        try:
            app_module._clear_course_availability_cache(slug)
            response = client.get('/api/poll')

            assert response.status_code == 401
            assert response.get_json() == {'error': 'Not logged in'}
            with client.session_transaction() as session_data:
                assert not session_data
        finally:
            if offline_path.exists():
                offline_path.replace(db_path)
            app_module._clear_course_availability_cache(slug)


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

    def test_reset_cooldown_is_shared_across_browser_sessions(self, demo_env):
        slug, _db_path = _start_demo(app_module.app.test_client(), demo_env)
        first = app_module.app.test_client()
        second = app_module.app.test_client()
        _enter_student(first, slug, 1)
        _enter_student(second, slug, 2)

        assert first.post(f'/demo/{slug}/reset').status_code == 302
        response = second.post(f'/demo/{slug}/reset')
        assert response.status_code == 429
        assert int(response.headers['Retry-After']) >= 1

        assert first.post('/demo/exit').status_code == 302
        _enter_student(first, slug, 1)
        response = first.post(f'/demo/{slug}/reset')
        assert response.status_code == 429


class TestDemoTimeToLive:
    def test_poll_activity_keeps_instance_alive_past_stale_marker(
            self, demo_env):
        slug, _db_path = _start_demo(app_module.app.test_client(), demo_env)
        instructor = app_module.app.test_client()
        _enter_instructor(instructor, slug)

        data_dir = str(demo_env['data_dir'])
        marker = demo_env['data_dir'] / slug / '.last-used'
        stale = time.time() - DEMO_INSTANCE_TTL_SECONDS - 60
        touch_demo_instance(data_dir, slug, now=stale)

        assert instructor.get('/api/poll').status_code == 200
        assert os.path.getmtime(marker) > stale

        removed = cleanup_expired_demo_instances(data_dir, now=time.time())
        assert slug not in removed
        assert (demo_env['data_dir'] / slug).is_dir()

    def test_poll_touch_is_throttled_per_instance(self, demo_env):
        slug, _db_path = _start_demo(app_module.app.test_client(), demo_env)
        instructor = app_module.app.test_client()
        _enter_instructor(instructor, slug)

        data_dir = str(demo_env['data_dir'])
        marker = demo_env['data_dir'] / slug / '.last-used'
        stale = time.time() - DEMO_INSTANCE_TTL_SECONDS - 60
        touch_demo_instance(data_dir, slug, now=stale)

        assert instructor.get('/api/poll').status_code == 200
        assert os.path.getmtime(marker) > stale

        # A second poll within the throttle window must not hit disk again.
        touch_demo_instance(data_dir, slug, now=stale)
        assert instructor.get('/api/poll').status_code == 200
        assert os.path.getmtime(marker) <= stale + 1


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

        # Thumbs are phase-scoped: no question needs to be posted first.
        thumb = student_one.post('/api/grade_peer', json={
            'recipient_id': 'demo002',
            'selected': True,
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
                "SELECT id FROM questions WHERE source_key = 'week-1-q-bagging-vs-boosting'"
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


def test_repeated_demo_start_from_same_browser_reuses_live_instance(demo_env):
    client = app_module.app.test_client()

    first_slug, first_path = _start_demo(client, demo_env)
    second_slug, second_path = _start_demo(client, demo_env)

    assert second_slug == first_slug
    assert second_path == first_path
    live_instances = [
        path.name for path in demo_env["data_dir"].iterdir()
        if path.is_dir() and is_demo_instance_slug(path.name)
    ]
    assert live_instances == [first_slug]
