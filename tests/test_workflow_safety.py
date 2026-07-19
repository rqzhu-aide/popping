"""Focused regression tests for destructive and live classroom workflows.

These tests intentionally exercise public Flask routes where possible. They use
an isolated SQLite database for every test and never touch checked-in course
data.
"""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import sqlite3
import sys
import uuid
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402


SESSION_KEY = 7


@pytest.fixture
def course_env(tmp_path, monkeypatch):
    """Create a complete course in a per-test data directory."""
    data_dir = tmp_path / "data"
    classes_dir = tmp_path / "classes"
    data_dir.mkdir()
    classes_dir.mkdir()
    slug = f"safety_{uuid.uuid4().hex[:8]}"
    class_dir = classes_dir / slug
    class_dir.mkdir()
    (class_dir / "course.yaml").write_text(
        "\n".join(
            (
                f"slug: {slug}",
                "name: Workflow Safety",
                "code: SAFE101",
                "semester: Test",
                "active: true",
                "",
            )
        ),
        encoding="utf-8",
    )
    course_dir = data_dir / slug
    course_dir.mkdir()
    db_path = course_dir / "popping.db"

    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "CLASSES_DIR", str(classes_dir))
    monkeypatch.setattr(config, "CONFIG_DIR", str(classes_dir))
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "workflow-test-key")
    monkeypatch.setitem(
        app_module.app.config, "MAX_CONTENT_LENGTH", 2 * 1024 * 1024
    )

    database._schema_checked.discard(slug)
    question_cache = getattr(app_module, "_question_html_cache", None)
    if isinstance(question_cache, dict):
        question_cache.clear()

    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(Path(config.DATABASE_SCHEMA).read_text(encoding="utf-8"))

    instructor_id = db.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ("instructor", "Test Instructor", "9999"),
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses (name, code, semester, slug, instructor_id)
           VALUES (?, ?, ?, ?, ?)""",
        ("Workflow Safety", "SAFE101", "Test", slug, instructor_id),
    ).lastrowid

    teams = {}
    for number in range(1, 5):
        name = f"Team {number}"
        teams[name] = db.execute(
            "INSERT INTO teams (course_id, name) VALUES (?, ?)",
            (course_id, name),
        ).lastrowid

    student_specs = (
        ("s1", "Alice", "1111", teams["Team 1"]),
        ("s2", "Bob", "2222", teams["Team 1"]),
        ("s3", "Unassigned", "3333", None),
        ("s4", "Dana", "4444", teams["Team 2"]),
    )
    students = {}
    for student_id, name, pin, team_id in student_specs:
        students[student_id] = db.execute(
            """INSERT INTO students
               (course_id, student_id, name, pin, team_id)
               VALUES (?, ?, ?, ?, ?)""",
            (course_id, student_id, name, pin, team_id),
        ).lastrowid

    question_id = db.execute(
        """INSERT INTO questions
           (course_id, question_num, question_text, title, content, week_num,
            source_key)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            course_id,
            1,
            "Explain the result.",
            "Question One",
            "Explain the result.",
            1,
            "week-1-question-1",
        ),
    ).lastrowid
    db.execute(
        """INSERT INTO course_state
           (course_id, phase, max_teams, max_members_per_team,
            discussion_week, presentation_history, roster_version, session_key)
           VALUES (?, 'setup', 4, 10, 1, '[]', 0, ?)""",
        (course_id, SESSION_KEY),
    )
    db.commit()
    db.close()

    env = {
        "slug": slug,
        "data_dir": data_dir,
        "db_path": db_path,
        "course_id": course_id,
        "instructor_id": instructor_id,
        "teams": teams,
        "students": students,
        "question_id": question_id,
    }
    yield env

    database._schema_checked.discard(slug)


@contextmanager
def _connect(env):
    db = sqlite3.connect(env["db_path"])
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        yield db
    finally:
        db.close()


def _instructor_client(env):
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["role"] = "instructor"
        flask_session["instructor_id"] = env["instructor_id"]
        flask_session["slug"] = env["slug"]
    return client


def _student_client(env, student_id="s1"):
    client = app_module.app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["role"] = "student"
        flask_session["student_id"] = student_id
        flask_session["name"] = student_id
        flask_session["slug"] = env["slug"]
    return client


def _setup_payload(roster_version=0, **extra):
    return {
        "expected_phase": "setup",
        "expected_session_key": SESSION_KEY,
        "expected_roster_version": roster_version,
        **extra,
    }


def _assign_all_active_students(env):
    """Assign active unassigned students and return the new roster version."""
    with _connect(env) as db:
        db.execute(
            '''UPDATE students SET team_id = ?
               WHERE course_id = ? AND is_active = 1 AND team_id IS NULL''',
            [env["teams"]["Team 2"], env["course_id"]],
        )
        db.execute(
            '''UPDATE course_state
               SET roster_version = COALESCE(roster_version, 0) + 1
               WHERE course_id = ?''',
            [env["course_id"]],
        )
        version = db.execute(
            "SELECT roster_version FROM course_state WHERE course_id = ?",
            [env["course_id"]],
        ).fetchone()["roster_version"]
        db.commit()
    return version


def _set_state(env, **fields):
    allowed = {
        "phase",
        "discussion_week",
        "session_key",
        "active_team_id",
        "active_question_id",
        "current_question",
        "presentation_started_at",
        "presentation_created_at",
        "presentation_time_cap",
        "presentation_remaining",
        "poll_active",
        "poll_question_key",
        "poll_started_at",
        "presentation_history",
        "session_started_at",
        "current_discussion_key",
        "current_discussion_source_key",
        "current_discussion_title",
        "current_discussion_content",
    }
    assert fields and set(fields).issubset(allowed)
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with _connect(env) as db:
        db.execute(
            f"UPDATE course_state SET {assignments} WHERE course_id = ?",
            [*fields.values(), env["course_id"]],
        )
        db.commit()


def _state_row(env):
    with _connect(env) as db:
        row = db.execute(
            "SELECT * FROM course_state WHERE course_id = ?",
            (env["course_id"],),
        ).fetchone()
        return dict(row)


PRESENTATION_SNAPSHOT_FIELDS = (
    "phase",
    "session_key",
    "active_team_id",
    "active_question_id",
    "current_question",
    "presentation_started_at",
    "presentation_created_at",
    "presentation_time_cap",
    "presentation_remaining",
    "poll_active",
    "poll_question_key",
    "poll_started_at",
    "presentation_history",
)


def _presentation_snapshot(env):
    state = _state_row(env)
    return {name: state[name] for name in PRESENTATION_SNAPSHOT_FIELDS}


def _activate_presentation(env, **overrides):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")
    fields = {
        "phase": "competition",
        "active_team_id": env["teams"]["Team 1"],
        "active_question_id": env["question_id"],
        "current_question": "Question One",
        "presentation_started_at": now,
        "presentation_created_at": now,
        "presentation_time_cap": 300,
        "presentation_remaining": None,
        "poll_active": 0,
        "poll_question_key": "pres-current",
        "poll_started_at": None,
        "presentation_history": "[]",
    }
    fields.update(overrides)
    _set_state(env, **fields)


def _seed_response_history(env):
    with _connect(env) as db:
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, week_num, question_key,
                grader_id, recipient_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                env["course_id"],
                SESSION_KEY,
                1,
                "discussion-1",
                env["students"]["s2"],
                env["students"]["s1"],
            ),
        )
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key, week_num,
                presenting_team_id, presenting_team_name, question_id,
                question_title, rater_team_id, rater_team_name,
                q1_developed, q2_easy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                env["course_id"],
                env["students"]["s1"],
                "presentation-1",
                SESSION_KEY,
                1,
                env["teams"]["Team 2"],
                "Team 2",
                env["question_id"],
                "Question One",
                env["teams"]["Team 1"],
                "Team 1",
                4,
                5,
            ),
        )
        db.commit()


def _history_counts(env):
    with _connect(env) as db:
        return {
            "thumbs": db.execute(
                "SELECT COUNT(*) FROM teammate_thumbs"
            ).fetchone()[0],
            "ratings": db.execute(
                "SELECT COUNT(*) FROM presentation_ratings"
            ).fetchone()[0],
        }


@pytest.mark.parametrize(
    ("route", "state_overrides"),
    (
        ("/api/stop_presentation", {}),
        (
            "/api/resume_presentation",
            {"presentation_started_at": None, "presentation_remaining": 120},
        ),
        ("/api/reset_presentation_timer", {}),
        ("/api/next_presentation", {}),
        ("/api/start_poll", {"poll_active": 0, "poll_started_at": None}),
        (
            "/api/stop_poll",
            {
                "poll_active": 1,
                "poll_started_at": datetime.utcnow().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            },
        ),
    ),
)
def test_stale_presentation_token_is_rejected_without_mutation(
    course_env, route, state_overrides
):
    _activate_presentation(course_env, **state_overrides)
    before = _presentation_snapshot(course_env)

    response = _instructor_client(course_env).post(
        route, json={"presentation_key": "pres-stale"}
    )

    assert response.status_code == 409
    assert _presentation_snapshot(course_env) == before


def test_stale_presentation_token_blocks_phase_exit(course_env):
    _activate_presentation(course_env)
    before = _presentation_snapshot(course_env)

    response = _instructor_client(course_env).post(
        "/api/set_phase",
        json={
            "phase": "ended",
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "presentation_key": "pres-stale",
            "confirm_end_session": True,
        },
    )

    assert response.status_code == 409
    assert _presentation_snapshot(course_env) == before


def test_phase_navigation_does_not_split_current_session(course_env):
    client = _instructor_client(course_env)
    roster_version = _assign_all_active_students(course_env)
    transitions = (
        ("setup", "discussion"),
        ("discussion", "setup"),
        ("setup", "discussion"),
        ("discussion", "competition"),
        ("competition", "discussion"),
        ("discussion", "ended"),
    )

    for current_phase, next_phase in transitions:
        response = client.post(
            "/api/set_phase",
            json={
                "phase": next_phase,
                "expected_phase": current_phase,
                "expected_session_key": SESSION_KEY,
                "expected_roster_version": roster_version,
                "confirm_end_session": next_phase == "ended",
            },
        )
        assert response.status_code == 200
        assert response.get_json()["session_key"] == SESSION_KEY
        state = _state_row(course_env)
        assert state["phase"] == next_phase
        assert state["session_key"] == SESSION_KEY


def test_end_session_requires_explicit_confirmation(course_env):
    _set_state(course_env, phase="discussion")
    before = _state_row(course_env)

    response = _instructor_client(course_env).post(
        "/api/set_phase",
        json={
            "phase": "ended",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["requires_confirmation"] is True
    assert _state_row(course_env) == before


def test_setup_phase_exit_blocks_unassigned_students_without_mutation(
    course_env,
):
    before = _state_row(course_env)

    response = _instructor_client(course_env).post(
        "/api/set_phase",
        json=_setup_payload(phase="discussion"),
    )

    assert response.status_code == 409
    data = response.get_json()
    assert "not assigned to a team" in data["error"].lower()
    assert data["unassigned_count"] == 1
    assert _state_row(course_env) == before


def test_unassigned_phase_block_cannot_be_bypassed_by_confirmation(course_env):
    before = _state_row(course_env)

    response = _instructor_client(course_env).post(
        "/api/set_phase",
        json=_setup_payload(
            phase="discussion",
            confirm_unassigned_students=True,
        ),
    )

    assert response.status_code == 409
    data = response.get_json()
    assert "not assigned to a team" in data["error"].lower()
    assert data["unassigned_count"] == 1
    assert _state_row(course_env) == before


def test_setup_phase_exit_needs_no_confirmation_when_everyone_is_assigned(
    course_env,
):
    roster_version = _assign_all_active_students(course_env)

    response = _instructor_client(course_env).post(
        "/api/set_phase",
        json=_setup_payload(
            roster_version=roster_version,
            phase="competition",
        ),
    )

    assert response.status_code == 200
    assert response.get_json()["phase"] == "competition"
    assert _state_row(course_env)["phase"] == "competition"


def test_unassigned_phase_exit_rejects_a_stale_roster(course_env):
    instructor = _instructor_client(course_env)
    payload = _setup_payload(phase="discussion")
    blocked = instructor.post("/api/set_phase", json=payload)
    assert blocked.status_code == 409
    assert blocked.get_json()["unassigned_count"] == 1

    roster_change = _student_client(course_env).post(
        "/api/join_team",
        json={"team_id": 0},
    )
    assert roster_change.status_code == 200

    retried = instructor.post("/api/set_phase", json=payload)

    assert retried.status_code == 409
    assert "roster changed" in retried.get_json()["error"].lower()
    state = _state_row(course_env)
    assert state["phase"] == "setup"
    assert state["roster_version"] == 1


def test_next_presentation_waits_for_open_poll(course_env):
    _set_state(course_env, discussion_week=2)
    _activate_presentation(
        course_env,
        poll_active=1,
        poll_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    client = _instructor_client(course_env)
    payload = {"presentation_key": "pres-current"}
    before = _presentation_snapshot(course_env)

    blocked = client.post("/api/next_presentation", json=payload)

    assert blocked.status_code == 409
    assert "active rating poll" in blocked.get_json()["error"]
    assert _presentation_snapshot(course_env) == before

    stopped = client.post("/api/stop_poll", json=payload)
    assert stopped.status_code == 200
    finished = client.post("/api/next_presentation", json=payload)
    assert finished.status_code == 200
    state = _state_row(course_env)
    assert state["active_team_id"] is None
    history = json.loads(state["presentation_history"])
    assert history[-1]["presentation_key"] == "pres-current"
    assert history[-1]["week_num"] == 2


def test_leaving_ended_starts_exactly_one_new_session(course_env):
    _set_state(course_env, phase="ended")
    client = _instructor_client(course_env)
    roster_version = _assign_all_active_students(course_env)

    first = client.post(
        "/api/set_phase",
        json={
            "phase": "setup",
            "expected_phase": "ended",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert first.status_code == 200
    assert first.get_json()["session_key"] == SESSION_KEY + 1

    second = client.post(
        "/api/set_phase",
        json={
            "phase": "discussion",
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY + 1,
            "expected_roster_version": roster_version,
        },
    )
    assert second.status_code == 200
    assert second.get_json()["session_key"] == SESSION_KEY + 1
    assert _state_row(course_env)["session_key"] == SESSION_KEY + 1


def test_archiving_preserves_history_and_reactivation_reuses_identity(course_env):
    _seed_response_history(course_env)
    original_student_db_id = course_env["students"]["s1"]
    history_before = _history_counts(course_env)
    client = _instructor_client(course_env)

    archived = client.delete(
        f"/api/remove_student/{original_student_db_id}",
        json=_setup_payload(),
    )
    assert archived.status_code == 200
    with _connect(course_env) as db:
        student = db.execute(
            """SELECT id, is_active, team_id, last_team_id
               FROM students WHERE student_id = 's1'"""
        ).fetchone()
    assert student["id"] == original_student_db_id
    assert student["is_active"] == 0
    assert student["team_id"] is None
    assert student["last_team_id"] == course_env["teams"]["Team 1"]
    assert _history_counts(course_env) == history_before

    restored = client.post(
        "/api/add_student",
        json=_setup_payload(
            roster_version=1,
            student_id="s1",
            name="Alice Restored",
            pin="5555",
        ),
    )
    assert restored.status_code == 200
    assert restored.get_json()["reactivated"] is True
    with _connect(course_env) as db:
        student = db.execute(
            """SELECT id, name, pin, is_active
               FROM students WHERE student_id = 's1'"""
        ).fetchone()
    assert student["id"] == original_student_db_id
    assert student["name"] == "Alice Restored"
    assert student["pin"] == "5555"
    assert student["is_active"] == 1
    assert _history_counts(course_env) == history_before


def test_setup_roster_mutation_rejects_stale_version_without_changes(course_env):
    client = _instructor_client(course_env)

    stale = client.post(
        "/api/set_max_teams",
        json=_setup_payload(roster_version=9, max_teams=3),
    )

    assert stale.status_code == 409
    with _connect(course_env) as db:
        state = db.execute(
            "SELECT max_teams, roster_version FROM course_state"
        ).fetchone()
    assert dict(state) == {"max_teams": 4, "roster_version": 0}

    current = client.post(
        "/api/set_max_teams",
        json=_setup_payload(max_teams=3),
    )
    assert current.status_code == 200
    with _connect(course_env) as db:
        state = db.execute(
            "SELECT max_teams, roster_version FROM course_state"
        ).fetchone()
    assert dict(state) == {"max_teams": 3, "roster_version": 1}


def test_instructor_unassignment_preserves_last_team(course_env):
    client = _instructor_client(course_env)
    student_id = course_env["students"]["s1"]
    team_1 = course_env["teams"]["Team 1"]
    team_2 = course_env["teams"]["Team 2"]

    unassigned = client.post(
        "/api/assign_student",
        json=_setup_payload(student_id=student_id, team_id=None),
    )
    assert unassigned.status_code == 200
    with _connect(course_env) as db:
        student = db.execute(
            "SELECT team_id, last_team_id FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()
    assert student["team_id"] is None
    assert student["last_team_id"] == team_1

    stale = client.post(
        "/api/assign_student",
        json=_setup_payload(
            roster_version=0,
            student_id=student_id,
            team_id=team_2,
        ),
    )
    assert stale.status_code == 409

    reassigned = client.post(
        "/api/assign_student",
        json=_setup_payload(
            roster_version=1,
            student_id=student_id,
            team_id=team_2,
        ),
    )
    assert reassigned.status_code == 200
    with _connect(course_env) as db:
        student = db.execute(
            "SELECT team_id, last_team_id FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()
    assert student["team_id"] == team_2
    assert student["last_team_id"] == team_2


def test_non_roster_setup_control_rejects_stale_session(course_env):
    response = _instructor_client(course_env).post(
        "/api/toggle_lock_teams",
        json={
            "locked": True,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY - 1,
        },
    )

    assert response.status_code == 409
    with _connect(course_env) as db:
        locked = db.execute(
            "SELECT teams_locked FROM course_state"
        ).fetchone()[0]
    assert locked == 0


def _reset_payload(env, expected_phase):
    return {
        "confirm_slug": env["slug"],
        "expected_phase": expected_phase,
        "expected_session_key": SESSION_KEY,
    }


def test_reset_rejects_live_phase_without_side_effects(course_env):
    _set_state(course_env, phase="discussion")
    _seed_response_history(course_env)
    history_before = _history_counts(course_env)
    state_before = _state_row(course_env)

    response = _instructor_client(course_env).post(
        "/api/reset_data", json=_reset_payload(course_env, "discussion")
    )

    assert response.status_code == 409
    assert _history_counts(course_env) == history_before
    assert _state_row(course_env) == state_before
    assert not (
        Path(course_env["data_dir"])
        / course_env["slug"]
        / "reset-backups"
    ).exists()


def test_reset_rejects_stale_state_without_side_effects(course_env):
    _set_state(course_env, phase="ended")
    _seed_response_history(course_env)
    history_before = _history_counts(course_env)
    state_before = _state_row(course_env)

    response = _instructor_client(course_env).post(
        "/api/reset_data", json=_reset_payload(course_env, "setup")
    )

    assert response.status_code == 409
    assert _history_counts(course_env) == history_before
    assert _state_row(course_env) == state_before
    assert not (
        Path(course_env["data_dir"])
        / course_env["slug"]
        / "reset-backups"
    ).exists()


def test_reset_creates_recoverable_backup_before_clearing(course_env):
    _set_state(course_env, phase="ended")
    _seed_response_history(course_env)

    response = _instructor_client(course_env).post(
        "/api/reset_data", json=_reset_payload(course_env, "ended")
    )

    assert response.status_code == 200
    backup_dir = (
        Path(course_env["data_dir"])
        / course_env["slug"]
        / "reset-backups"
    )
    backup_files = list(backup_dir.glob("popping-before-reset-*.db"))
    assert len(backup_files) == 1
    assert response.get_json()["backup"] == backup_files[0].name

    backup = sqlite3.connect(backup_files[0])
    backup.row_factory = sqlite3.Row
    try:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        backup_state = backup.execute(
            "SELECT phase, session_key FROM course_state"
        ).fetchone()
        assert dict(backup_state) == {
            "phase": "ended",
            "session_key": SESSION_KEY,
        }
        assert backup.execute(
            "SELECT COUNT(*) FROM teammate_thumbs"
        ).fetchone()[0] == 1
        assert backup.execute(
            "SELECT COUNT(*) FROM presentation_ratings"
        ).fetchone()[0] == 1
    finally:
        backup.close()

    assert _history_counts(course_env) == {"thumbs": 0, "ratings": 0}
    live_state = _state_row(course_env)
    assert live_state["phase"] == "setup"
    assert live_state["session_key"] == SESSION_KEY + 1


def test_reset_holds_write_lock_while_creating_backup(course_env, monkeypatch):
    _set_state(course_env, phase="ended")
    original_backup = app_module._create_reset_backup
    lock_observed = []

    def backup_while_contended(slug):
        contender = sqlite3.connect(course_env["db_path"], timeout=0.05)
        try:
            contender.execute(
                "UPDATE course_state SET roster_version = roster_version + 1"
            )
            contender.commit()
        except sqlite3.OperationalError as exc:
            assert "locked" in str(exc).lower()
            lock_observed.append(True)
        finally:
            contender.close()
        return original_backup(slug)

    monkeypatch.setattr(
        app_module, "_create_reset_backup", backup_while_contended
    )
    response = _instructor_client(course_env).post(
        "/api/reset_data", json=_reset_payload(course_env, "ended")
    )

    assert response.status_code == 200
    assert lock_observed == [True]


def test_unassigned_student_sees_posted_discussion_prompt(course_env):
    _set_state(
        course_env,
        phase="discussion",
        current_discussion_key="discussion-visible",
        current_discussion_title="Visible to Everyone",
        current_discussion_content="Discuss this exact classroom prompt.",
    )

    response = _student_client(course_env, "s3").get("/dashboard")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="current-question"' in html
    assert "Visible to Everyone" in html
    assert "Discuss this exact classroom prompt." in html


def test_student_thumb_requires_the_displayed_discussion_question(course_env):
    _set_state(
        course_env,
        phase="discussion",
        discussion_week=2,
        current_discussion_key="discussion-current",
        current_discussion_title="Current question",
        current_discussion_content="Discuss the current question.",
    )
    client = _student_client(course_env, "s1")

    stale = client.post(
        "/api/grade_peer",
        json={
            "recipient_id": "s2",
            "selected": True,
            "question_key": "discussion-stale",
        },
    )
    missing = client.post(
        "/api/grade_peer",
        json={"recipient_id": "s2", "selected": True},
    )

    assert stale.status_code == 409
    assert missing.status_code == 409
    assert _history_counts(course_env)["thumbs"] == 0

    saved = client.post(
        "/api/grade_peer",
        json={
            "recipient_id": "s2",
            "selected": True,
            "question_key": "discussion-current",
        },
    )
    assert saved.status_code == 200
    assert _history_counts(course_env)["thumbs"] == 1
    with _connect(course_env) as db:
        saved_week = db.execute(
            "SELECT week_num FROM teammate_thumbs"
        ).fetchone()[0]
    assert saved_week == 2


def test_student_rating_records_selected_week(course_env):
    with _connect(course_env) as db:
        question_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, week_num,
                source_key)
               VALUES (?, 1, 'Week 2 question', 'Week 2 question', 2,
                       'presentation:2:1')""",
            (course_env["course_id"],),
        ).lastrowid
        db.execute(
            "UPDATE course_state SET discussion_week = 2"
        )
        db.commit()
    _activate_presentation(
        course_env,
        active_question_id=question_id,
        poll_active=1,
        poll_started_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )

    response = _student_client(course_env, "s4").post(
        "/api/submit_rating",
        json={
            "presentation_key": "pres-current",
            "q1_developed": 4,
            "q2_easy": 5,
        },
    )

    assert response.status_code == 200
    with _connect(course_env) as db:
        saved_week = db.execute(
            "SELECT week_num FROM presentation_ratings"
        ).fetchone()[0]
    assert saved_week == 2


def test_set_question_rejects_stale_post_and_unpost(course_env):
    _set_state(course_env, phase="discussion")
    client = _instructor_client(course_env)

    stale_post = client.post(
        "/api/set_question",
        json={
            "key": "discussion-new",
            "title": "New question",
            "content": "Discuss this.",
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": "",
        },
    )
    assert stale_post.status_code == 409
    assert _state_row(course_env)["current_discussion_key"] is None

    posted = client.post(
        "/api/set_question",
        json={
            "key": "discussion-new",
            "title": "New question",
            "content": "Discuss this.",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": "",
        },
    )
    assert posted.status_code == 200
    posted_question = posted.get_json()["discussion_question"]
    assert posted_question["source_key"] == "discussion-new"
    assert posted_question["key"].startswith("disc-")
    posted_instance_key = posted_question["key"]

    stale_unpost = client.post(
        "/api/set_question",
        json={
            "key": "",
            "title": "",
            "content": "",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY - 1,
            "expected_discussion_key": posted_instance_key,
        },
    )
    assert stale_unpost.status_code == 409
    state = _state_row(course_env)
    assert state["current_discussion_key"] == posted_instance_key
    assert state["current_discussion_source_key"] == "discussion-new"

    unposted = client.post(
        "/api/set_question",
        json={
            "key": "",
            "title": "",
            "content": "",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": posted_instance_key,
        },
    )
    assert unposted.status_code == 200
    state = _state_row(course_env)
    assert state["current_discussion_key"] is None
    assert state["current_discussion_source_key"] is None


def test_reposting_same_discussion_question_starts_fresh_thumb_context(course_env):
    _set_state(course_env, phase="discussion")
    instructor = _instructor_client(course_env)
    student = _student_client(course_env, "s1")
    payload = {
        "key": "week-1-shared-question",
        "title": "Shared question",
        "content": "Discuss this question carefully.",
        "expected_phase": "discussion",
        "expected_session_key": SESSION_KEY,
        "expected_discussion_key": "",
    }

    first = instructor.post("/api/set_question", json=payload)
    first_key = first.get_json()["discussion_question"]["key"]
    saved_first = student.post(
        "/api/grade_peer",
        json={
            "recipient_id": "s2",
            "selected": True,
            "question_key": first_key,
        },
    )
    assert saved_first.status_code == 200

    second_payload = {**payload, "expected_discussion_key": first_key}
    second = instructor.post("/api/set_question", json=second_payload)
    second_question = second.get_json()["discussion_question"]
    assert second_question["source_key"] == payload["key"]
    assert second_question["key"] != first_key

    responses = student.get("/api/my_responses").get_json()
    assert responses["discussion_question_key"] == second_question["key"]
    assert responses["thumb_recipient_ids"] == []

    stale = student.post(
        "/api/grade_peer",
        json={
            "recipient_id": "s2",
            "selected": True,
            "question_key": first_key,
        },
    )
    assert stale.status_code == 409
    saved_second = student.post(
        "/api/grade_peer",
        json={
            "recipient_id": "s2",
            "selected": True,
            "question_key": second_question["key"],
        },
    )
    assert saved_second.status_code == 200

    with _connect(course_env) as db:
        rows = db.execute(
            """SELECT question_key, source_question_key, question_title,
                      grader_team_id, grader_team_name, recipient_team_id,
                      recipient_team_name
               FROM teammate_thumbs ORDER BY id"""
        ).fetchall()
    assert [row["question_key"] for row in rows] == [
        first_key,
        second_question["key"],
    ]
    assert {row["source_question_key"] for row in rows} == {payload["key"]}
    assert {row["question_title"] for row in rows} == {payload["title"]}
    assert all(row["grader_team_id"] == course_env["teams"]["Team 1"] for row in rows)
    assert all(row["recipient_team_id"] == course_env["teams"]["Team 1"] for row in rows)
    assert all(row["grader_team_name"] == "Team 1" for row in rows)
    assert all(row["recipient_team_name"] == "Team 1" for row in rows)


def test_delayed_discussion_post_cannot_overwrite_newer_post(course_env):
    _set_state(course_env, phase="discussion")
    client = _instructor_client(course_env)
    first = client.post(
        "/api/set_question",
        json={
            "key": "first",
            "title": "First",
            "content": "First content",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": "",
        },
    )
    first_key = first.get_json()["discussion_question"]["key"]
    newer = client.post(
        "/api/set_question",
        json={
            "key": "newer",
            "title": "Newer",
            "content": "Newer content",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": first_key,
        },
    )
    newer_key = newer.get_json()["discussion_question"]["key"]

    delayed = client.post(
        "/api/set_question",
        json={
            "key": "delayed",
            "title": "Delayed",
            "content": "Delayed content",
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": first_key,
        },
    )

    assert delayed.status_code == 409
    state = _state_row(course_env)
    assert state["current_discussion_key"] == newer_key
    assert state["current_discussion_source_key"] == "newer"


def _seed_tied_team_ratings(env):
    # Insert in reverse name order so the test also checks deterministic tie order.
    rating_specs = (
        ("Team 4", 4),
        ("Team 3", 4),
        ("Team 2", 5),
        ("Team 1", 5),
    )
    with _connect(env) as db:
        for index, (team_name, score) in enumerate(rating_specs, start=1):
            db.execute(
                """INSERT INTO presentation_ratings
                   (course_id, student_id, question_key, session_key,
                    presenting_team_id, presenting_team_name, question_id,
                    question_title, rater_team_id, rater_team_name,
                    q1_developed, q2_easy)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    env["course_id"],
                    env["students"]["s1"],
                    f"rank-{index}",
                    SESSION_KEY,
                    env["teams"][team_name],
                    team_name,
                    env["question_id"],
                    "Question One",
                    env["teams"]["Team 1"],
                    "Team 1",
                    score,
                    score,
                ),
            )
        db.commit()


def test_tied_team_rankings_use_competition_ranks_and_include_cutoff_ties(
    course_env,
):
    _seed_tied_team_ratings(course_env)
    _set_state(course_env, phase="ended")

    with app_module.app.app_context():
        ranked = app_module._compute_top_teams(
            course_env["slug"], course_env["course_id"], [], SESSION_KEY
        )
    assert [team["name"] for team in ranked] == [
        "Team 1",
        "Team 2",
        "Team 3",
        "Team 4",
    ]
    assert [team["rank"] for team in ranked] == [1, 1, 3, 3]
    assert [team["rating_count"] for team in ranked] == [1, 1, 1, 1]

    response = _student_client(course_env).get("/api/poll")
    assert response.status_code == 200
    top_teams = response.get_json()["top_teams"]
    assert [(team["name"], team["rank"]) for team in top_teams] == [
        ("Team 1", 1),
        ("Team 2", 1),
        ("Team 3", 3),
        ("Team 4", 3),
    ]
    assert all("avg_score" not in team for team in top_teams)


def test_recorded_activity_counts_do_not_depend_on_online_presence(course_env):
    old_activity = "2000-01-01 00:00:00"
    with _connect(course_env) as db:
        db.execute(
            "UPDATE students SET last_active_at = ? WHERE course_id = ?",
            (old_activity, course_env["course_id"]),
        )
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key,
                presenting_team_id, presenting_team_name, question_id,
                question_title, rater_team_id, rater_team_name,
                q1_developed, q2_easy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                course_env["course_id"],
                course_env["students"]["s4"],
                "pres-current",
                SESSION_KEY,
                course_env["teams"]["Team 1"],
                "Team 1",
                course_env["question_id"],
                "Question One",
                course_env["teams"]["Team 2"],
                "Team 2",
                4,
                5,
            ),
        )
        db.commit()

    _activate_presentation(course_env)
    presentation_state = _instructor_client(course_env).get("/api/poll").get_json()["state"]
    assert presentation_state["poll_count"] == 1
    assert presentation_state["poll_eligible_count"] == 1
    assert presentation_state["poll_online_eligible_count"] == 0

    _set_state(
        course_env,
        phase="discussion",
        active_team_id=None,
        active_question_id=None,
        current_discussion_key="disc-recorded",
        current_discussion_source_key="week-1-question-1",
        current_discussion_title="Question One",
        current_discussion_content="Explain the result.",
    )
    response = _student_client(course_env, "s1").post(
        "/api/grade_peer",
        json={
            "recipient_id": "s2",
            "selected": True,
            "question_key": "disc-recorded",
        },
    )
    assert response.status_code == 200
    with _connect(course_env) as db:
        db.execute(
            "UPDATE students SET last_active_at = ? WHERE student_id = ?",
            (old_activity, "s1"),
        )
        db.commit()

    discussion_state = _instructor_client(course_env).get("/api/poll").get_json()["state"]
    assert discussion_state["thumb_participant_count"] == 1
    assert discussion_state["thumb_eligible_count"] == 2
    assert discussion_state["thumb_online_eligible_count"] == 0


def test_student_team_api_hides_other_teams_member_identities(course_env):
    student_teams = _student_client(course_env, "s1").get("/api/teams").get_json()
    by_name = {team["name"]: team for team in student_teams}

    assert by_name["Team 1"]["members_visible"] is True
    assert {member["student_id"] for member in by_name["Team 1"]["members"]} == {
        "s1",
        "s2",
    }
    assert by_name["Team 2"]["members_visible"] is False
    assert by_name["Team 2"]["member_count"] == 1
    assert by_name["Team 2"]["members"] == []
    assert "s4" not in str(student_teams)

    instructor_teams = _instructor_client(course_env).get("/api/teams").get_json()
    instructor_team_2 = next(
        team for team in instructor_teams if team["name"] == "Team 2"
    )
    assert instructor_team_2["members_visible"] is True
    assert [member["student_id"] for member in instructor_team_2["members"]] == [
        "s4"
    ]


def _active_student_count(env):
    with _connect(env) as db:
        return db.execute(
            "SELECT COUNT(*) FROM students WHERE is_active = 1"
        ).fetchone()[0]


def test_roster_rejects_more_than_500_rows_without_mutation(course_env):
    rows = ["student_id,name,pin"]
    rows.extend(
        f"upload-{index},Student {index},1234"
        for index in range(app_module.MAX_ROSTER_ROWS + 1)
    )
    payload = ("\n".join(rows) + "\n").encode("utf-8")
    count_before = _active_student_count(course_env)

    response = _instructor_client(course_env).post(
        "/api/upload_roster",
        data={"file": (io.BytesIO(payload), "roster.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert "500" in response.get_json()["error"]
    assert _active_student_count(course_env) == count_before


def test_roster_rejects_file_over_one_megabyte_without_mutation(course_env):
    payload = b"student_id,name,pin\n" + b"x" * app_module.MAX_ROSTER_BYTES
    count_before = _active_student_count(course_env)

    response = _instructor_client(course_env).post(
        "/api/upload_roster",
        data={"file": (io.BytesIO(payload), "roster.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert _active_student_count(course_env) == count_before


def test_global_request_limit_rejects_oversized_upload_without_mutation(course_env):
    payload = b"x" * (app_module.app.config["MAX_CONTENT_LENGTH"] + 1)
    count_before = _active_student_count(course_env)

    response = _instructor_client(course_env).post(
        "/api/upload_roster",
        data={"file": (io.BytesIO(payload), "roster.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert _active_student_count(course_env) == count_before


def test_roster_preview_and_confirmation_are_bound_to_setup_state(course_env):
    payload = (
        "student_id,name,pin\n"
        "s1,Alice Updated,5555\n"
        "new-student,New Student,6666\n"
    ).encode("utf-8")
    client = _instructor_client(course_env)
    expected_state = {
        "expected_phase": "setup",
        "expected_session_key": str(SESSION_KEY),
        "expected_roster_version": "0",
    }

    preview = client.post(
        "/api/upload_roster",
        data={
            **expected_state,
            "file": (io.BytesIO(payload), "roster.csv"),
        },
        content_type="multipart/form-data",
    )
    assert preview.status_code == 200
    preview_data = preview.get_json()
    assert preview_data["requires_confirmation"] is True

    confirmed = client.post(
        "/api/upload_roster",
        data={
            **expected_state,
            "confirm": "true",
            "preview_token": preview_data["preview_token"],
            "file": (io.BytesIO(payload), "roster.csv"),
        },
        content_type="multipart/form-data",
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["requires_confirmation"] is False
    assert _active_student_count(course_env) == 2
    with _connect(course_env) as db:
        roster_version = db.execute(
            "SELECT roster_version FROM course_state"
        ).fetchone()[0]
    assert roster_version == 1


def test_export_returns_a_valid_streamed_zip(course_env):
    response = _instructor_client(course_env).get(
        f"/export/{course_env['slug']}"
    )

    assert response.status_code == 200
    assert response.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert "course_data.xlsx" in archive.namelist()


def test_export_assets_and_filename_use_only_selected_week(course_env):
    _write_catalog_week(course_env, 1)
    _write_catalog_week(course_env, 2)
    appendix_dir = (
        course_env["data_dir"] / course_env["slug"] / "appendix"
    )
    appendix_dir.mkdir()
    (appendix_dir / "week-1-appendix.md").write_text(
        "Appendix week 1", encoding="utf-8"
    )
    legacy_appendix = (
        Path(config.CLASSES_DIR) / course_env["slug"]
        / "week-2-appendix.md"
    )
    legacy_appendix.write_text("Appendix week 2", encoding="utf-8")
    _set_state(course_env, discussion_week=2)

    response = _instructor_client(course_env).get(
        f"/export/{course_env['slug']}"
    )

    assert response.status_code == 200
    assert (
        "filename=popping_SAFE101_week_2_export.zip"
        in response.headers["Content-Disposition"]
    )
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        appendix_text = archive.read(
            "appendix/week-2-appendix.md"
        ).decode("utf-8")
    assert {
        "course_data.xlsx",
        "questions/week-2-questions.md",
        "questions/week2/index.md",
        "questions/week2/q01.html",
        "appendix/week-2-appendix.md",
    }.issubset(names)
    assert appendix_text == "Appendix week 2"
    assert not any("week-1" in name or "week1/" in name for name in names)


def test_export_workbook_activity_is_scoped_to_selected_week(course_env):
    from openpyxl import load_workbook

    with _connect(course_env) as db:
        week_2_question_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, week_num,
                source_key)
               VALUES (?, 1, 'Week 2 question', 'Week 2 question', 2,
                       'presentation:2:1')""",
            (course_env["course_id"],),
        ).lastrowid
        thumb_rows = (
            (1, SESSION_KEY, "week-1-thumb", "week-1-q-one"),
            (2, SESSION_KEY, "week-2-thumb-a", "week-2-q-one"),
            (2, SESSION_KEY + 1, "week-2-thumb-b", "week-2-q-two"),
        )
        for index, (week, session_key, question_key, source_key) in enumerate(
            thumb_rows
        ):
            grader = course_env["students"]["s1" if index != 1 else "s2"]
            recipient = course_env["students"]["s2" if index != 1 else "s1"]
            db.execute(
                """INSERT INTO teammate_thumbs
                   (course_id, session_key, week_num, question_key,
                    source_question_key, question_title, grader_id,
                    recipient_id)
                   VALUES (?, ?, ?, ?, ?, 'Question', ?, ?)""",
                (
                    course_env["course_id"], session_key, week, question_key,
                    source_key, grader, recipient,
                ),
            )

        rating_rows = (
            (
                1, SESSION_KEY, "pres-week-1", course_env["question_id"],
                course_env["students"]["s1"],
                course_env["teams"]["Team 2"], "Team 2", 1, 1,
            ),
            (
                2, SESSION_KEY, "pres-week-2-a", week_2_question_id,
                course_env["students"]["s1"],
                course_env["teams"]["Team 2"], "Team 2", 4, 5,
            ),
            (
                2, SESSION_KEY + 1, "pres-week-2-b", week_2_question_id,
                course_env["students"]["s4"],
                course_env["teams"]["Team 1"], "Team 1", 3, 3,
            ),
        )
        for (
            week, session_key, question_key, question_id, student_id,
            presenting_team_id, presenting_team_name, developed, easy,
        ) in rating_rows:
            db.execute(
                """INSERT INTO presentation_ratings
                   (course_id, student_id, question_key, session_key, week_num,
                    presenting_team_id, presenting_team_name, question_id,
                    question_title, q1_developed, q2_easy)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Question', ?, ?)""",
                (
                    course_env["course_id"], student_id, question_key,
                    session_key, week, presenting_team_id,
                    presenting_team_name, question_id, developed, easy,
                ),
            )
        history = [
            {
                "presentation_key": "pres-week-1",
                "week_num": 1,
                "team": "Team 2",
                "question_id": course_env["question_id"],
            },
            {
                "presentation_key": "pres-week-2-a",
                "team": "Team 2",
                "question_id": week_2_question_id,
            },
            {
                "presentation_key": "pres-week-2-b",
                "team": "Team 1",
                "question_id": None,
            },
        ]
        db.execute(
            """UPDATE course_state
               SET discussion_week = 2, presentation_history = ?""",
            (json.dumps(history),),
        )
        db.commit()

    response = _instructor_client(course_env).get(
        f"/export/{course_env['slug']}"
    )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        workbook = load_workbook(
            io.BytesIO(archive.read("course_data.xlsx")), read_only=True
        )

    summary = {
        row[0]: row[1]
        for row in workbook["Summary"].iter_rows(values_only=True)
        if row[0]
    }
    assert summary["Lecture Week"] == 2
    assert summary["Week Peer Reviews (thumbs)"] == 2
    assert summary["Week Presentation Ratings"] == 2

    def sheet_rows(name):
        values = list(workbook[name].iter_rows(values_only=True))
        return [dict(zip(values[0], row)) for row in values[1:]]

    peer_rows = sheet_rows("Peer Reviews")
    assert {row["week"] for row in peer_rows} == {2}
    assert {row["session_key"] for row in peer_rows} == {
        SESSION_KEY, SESSION_KEY + 1,
    }
    assert {row["discussion_post_key"] for row in peer_rows} == {
        "week-2-thumb-a", "week-2-thumb-b",
    }

    presentation_rows = sheet_rows("Presentation Ratings")
    assert {row["week"] for row in presentation_rows} == {2}
    assert {row["session_key"] for row in presentation_rows} == {
        SESSION_KEY, SESSION_KEY + 1,
    }
    assert {row["presentation_key"] for row in presentation_rows} == {
        "pres-week-2-a", "pres-week-2-b",
    }

    students = {row["student_id"]: row for row in sheet_rows("Students")}
    assert students["s1"]["thumbs_given"] == 1
    assert students["s1"]["presentation_ratings_given"] == 1
    teams = {row["team_name"]: row for row in sheet_rows("Teams")}
    assert teams["Team 1"]["presentations"] == 1
    assert teams["Team 2"]["presentations"] == 1
    assert teams["Team 2"]["combined_avg"] == 4.5


def test_export_reports_question_asset_failure(course_env, monkeypatch):
    class_dir = Path(config.CLASSES_DIR) / course_env["slug"]
    (class_dir / "week-1-questions.md").write_text(
        "question asset", encoding="utf-8"
    )

    def fail_asset_write(_archive, _path, _archive_name):
        raise OSError("asset read failed")

    monkeypatch.setattr(zipfile.ZipFile, "write", fail_asset_write)
    client = _instructor_client(course_env)
    response = client.get(f"/export/{course_env['slug']}")

    assert response.status_code == 302
    assert f"/instructor/{course_env['slug']}" in response.headers["Location"]
    with client.session_transaction() as browser_session:
        messages = [message for _level, message in browser_session["_flashes"]]
    assert any("asset read failed" in message for message in messages)


def test_export_limits_normal_team_views_but_keeps_historical_ratings(course_env):
    from openpyxl import load_workbook

    hidden_team_id = course_env["teams"]["Team 4"]
    with _connect(course_env) as db:
        db.execute("UPDATE course_state SET max_teams = 2")
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key, week_num,
                presenting_team_id, presenting_team_name, question_id,
                question_title, rater_team_id, rater_team_name,
                q1_developed, q2_easy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                course_env["course_id"],
                course_env["students"]["s1"],
                "historical-hidden-team",
                SESSION_KEY,
                1,
                hidden_team_id,
                "Team 4",
                course_env["question_id"],
                "Historical Question",
                course_env["teams"]["Team 1"],
                "Team 1",
                4,
                4,
            ),
        )
        db.commit()

    response = _instructor_client(course_env).get(
        f"/export/{course_env['slug']}"
    )
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        workbook = load_workbook(
            io.BytesIO(archive.read("course_data.xlsx")), read_only=True
        )

    summary = {
        row[0]: row[1]
        for row in workbook["Summary"].iter_rows(values_only=True)
        if row[0]
    }
    assert summary["Current Visible Teams"] == 2
    assert "Total Teams" not in summary
    team_names = [
        row[1]
        for row in workbook["Teams"].iter_rows(min_row=2, values_only=True)
    ]
    assert team_names == ["Team 1", "Team 2"]
    raw_presenting_teams = [
        row[6]
        for row in workbook["Presentation Ratings"].iter_rows(
            min_row=2, values_only=True
        )
    ]
    assert "Team 4" in raw_presenting_teams


def test_session_timer_is_server_authoritative_and_idempotent(course_env):
    client = _instructor_client(course_env)
    payload = {
        "expected_phase": "setup",
        "expected_session_key": SESSION_KEY,
    }

    first = client.post("/api/start_session_timer", json=payload)
    second = client.post("/api/start_session_timer", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    started_at = first.get_json()["session_started_at"]
    assert started_at
    assert second.get_json()["session_started_at"] == started_at

    stopped = client.post("/api/stop_session_timer", json=payload)
    assert stopped.status_code == 200
    assert stopped.get_json()["session_started_at"] is None
    assert _state_row(course_env)["session_started_at"] is None


def test_poll_returns_server_derived_timer_values(course_env):
    now = datetime.utcnow()
    _activate_presentation(
        course_env,
        presentation_started_at=(now - timedelta(seconds=10)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
        presentation_time_cap=300,
        poll_active=1,
        poll_started_at=(now - timedelta(seconds=5)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
    )
    _set_state(
        course_env,
        session_started_at=(now - timedelta(seconds=20)).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        ),
    )

    state = _instructor_client(course_env).get("/api/poll").get_json()["state"]

    assert 288 <= state["presentation_remaining"] <= 291
    assert 23 <= state["poll_remaining"] <= 26
    assert 19 <= state["session_elapsed"] <= 22


def test_discussion_instructor_shows_timer_control_and_connection_status(course_env):
    _set_state(course_env, phase="discussion")

    response = _instructor_client(course_env).get(
        f"/instructor/{course_env['slug']}"
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="btn-session-timer"' in html
    assert 'id="instructor-connection-status"' in html


def test_empty_team_cannot_start_presentation(course_env):
    _set_state(course_env, phase="competition")
    response = _instructor_client(course_env).post(
        "/api/start_presentation",
        json={
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "team_id": course_env["teams"]["Team 3"],
            "question_id": course_env["question_id"],
            "time_cap": 300,
        },
    )

    assert response.status_code == 409
    state = _state_row(course_env)
    assert state["active_team_id"] is None
    assert state["active_question_id"] is None


def test_stale_question_from_another_week_cannot_start(course_env):
    _write_catalog_week(course_env, 1)
    _write_catalog_week(course_env, 2)
    client = _instructor_client(course_env)
    selected = client.post(
        "/api/set_discussion_week",
        json={
            "week": 1,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert selected.status_code == 200
    with _connect(course_env) as db:
        week_one_question = db.execute(
            """SELECT id FROM questions
               WHERE course_id = ? AND source_key = 'presentation:1:1'""",
            (course_env["course_id"],),
        ).fetchone()[0]
    _set_state(course_env, phase="competition", discussion_week=2)

    response = client.post(
        "/api/start_presentation",
        json={
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "team_id": course_env["teams"]["Team 1"],
            "question_id": week_one_question,
            "time_cap": 300,
        },
    )

    assert response.status_code == 409
    assert "different week" in response.get_json()["error"]


def test_missing_presentation_catalog_blocks_stale_base_but_allows_appendix(
        course_env):
    _write_catalog_week(course_env, 2, include_presentation=False)
    client = _instructor_client(course_env)
    selected = client.post(
        "/api/set_discussion_week",
        json={
            "week": 2,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert selected.status_code == 200
    added = client.post(
        "/api/questions",
        json={
            "title": "Instructor follow-up",
            "content": "Discuss this follow-up.",
            "week": 2,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert added.status_code == 200
    with _connect(course_env) as db:
        stale_base_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, week_num,
                source_key)
               VALUES (?, 1, 'Old base', 'Old base', 2,
                       'presentation:2:1')""",
            (course_env["course_id"],),
        ).lastrowid
        appendix_id = db.execute(
            """SELECT id FROM questions
               WHERE course_id = ? AND source_key = 'appendix:2:A1'""",
            (course_env["course_id"],),
        ).fetchone()[0]
        db.commit()
    _set_state(course_env, phase="competition")

    competition_page = client.get(f"/instructor/{course_env['slug']}")
    competition_html = competition_page.get_data(as_text=True)
    assert competition_page.status_code == 200
    assert "Instructor follow-up" in competition_html
    assert "Old base" not in competition_html
    assert "no validated presentation question set" in competition_html

    blocked = client.post(
        "/api/start_presentation",
        json={
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "team_id": course_env["teams"]["Team 1"],
            "question_id": stale_base_id,
            "time_cap": 300,
        },
    )
    assert blocked.status_code == 409
    assert "not ready" in blocked.get_json()["error"]

    started = client.post(
        "/api/start_presentation",
        json={
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "team_id": course_env["teams"]["Team 1"],
            "question_id": appendix_id,
            "time_cap": 300,
        },
    )
    assert started.status_code == 200


def test_cancel_presentation_requires_explicit_rating_discard(course_env):
    _activate_presentation(course_env)
    with _connect(course_env) as db:
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key,
                presenting_team_id, presenting_team_name, question_id,
                question_title, q1_developed, q2_easy)
               VALUES (?, ?, 'pres-current', ?, ?, 'Team 1', ?,
                       'Question One', 4, 4)""",
            (
                course_env["course_id"],
                course_env["students"]["s4"],
                SESSION_KEY,
                course_env["teams"]["Team 1"],
                course_env["question_id"],
            ),
        )
        db.commit()
    client = _instructor_client(course_env)

    protected = client.post(
        "/api/cancel_presentation",
        json={"presentation_key": "pres-current"},
    )
    assert protected.status_code == 409
    assert protected.get_json()["requires_discard"] is True
    assert _state_row(course_env)["active_team_id"] is not None

    cancelled = client.post(
        "/api/cancel_presentation",
        json={"presentation_key": "pres-current", "discard_ratings": True},
    )
    assert cancelled.status_code == 200
    assert _state_row(course_env)["active_team_id"] is None
    assert _state_row(course_env)["presentation_history"] == "[]"
    with _connect(course_env) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM presentation_ratings"
        ).fetchone()[0] == 0


def test_login_throttle_blocks_after_eight_failures_then_expires(course_env):
    client = app_module.app.test_client()
    route = f"/login/{course_env['slug']}"
    for _ in range(app_module.LOGIN_FAILURE_LIMIT):
        response = client.post(
            route, data={"student_id": "s1", "pin": "0000"}
        )
        assert response.status_code == 200

    blocked = client.post(
        route, data={"student_id": "s1", "pin": "1111"}
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0

    with _connect(course_env) as db:
        db.execute(
            """UPDATE login_attempts
               SET window_started_at = '2000-01-01 00:00:00',
                   blocked_until = '2000-01-01 00:00:00'"""
        )
        db.commit()
    success = client.post(
        route, data={"student_id": "s1", "pin": "1111"}
    )
    assert success.status_code == 302


def test_question_revision_refreshes_same_question_id(course_env):
    html_dir = (
        Path(config.CLASSES_DIR) / course_env["slug"] / "week1"
    )
    html_dir.mkdir(parents=True)
    html_path = html_dir / "q01.html"
    html_path.write_text("<p>version one</p>", encoding="utf-8")
    _activate_presentation(course_env)
    client = _student_client(course_env, "s4")

    first = client.get("/api/poll").get_json()["state"]["active_question"]
    revision = first["revision"]
    assert first["html_content"] == "<p>version one</p>"

    compact = client.get(
        "/api/poll",
        query_string={
            "known_question_id": first["id"],
            "known_question_revision": revision,
        },
    ).get_json()["state"]["active_question"]
    assert compact["content_unchanged"] is True
    assert "html_content" not in compact

    html_path.write_text("<p>version two is longer</p>", encoding="utf-8")
    refreshed = client.get(
        "/api/poll",
        query_string={
            "known_question_id": first["id"],
            "known_question_revision": revision,
        },
    ).get_json()["state"]["active_question"]
    assert refreshed["revision"] != revision
    assert refreshed["html_content"] == "<p>version two is longer</p>"


def test_question_parser_preserves_horizontal_rules_and_fenced_delimiters():
    source = """---
title: First
---

Opening paragraph.

---

Still part of the first question.

```markdown
---
title: Not frontmatter
---
```

---
title: Second
---

Second body.
"""
    entries = app_module.parse_question_blocks(source)

    assert len(entries) == 2
    assert "Still part of the first question" in entries[0][1]
    assert "title: Not frontmatter" in entries[0][1]
    assert entries[1][1] == "Second body."


def test_question_parser_rejects_unclosed_fenced_code_block():
    source = """---
title: First
---

Opening paragraph.

```markdown
---
title: Hidden by the broken fence
---
"""

    with pytest.raises(
        app_module.QuestionParseError, match="Unclosed fenced code block"
    ):
        app_module.parse_question_blocks(source)


def _write_catalog_week(env, week_num, include_presentation=True):
    class_dir = Path(config.CLASSES_DIR) / env["slug"]
    (class_dir / f"week-{week_num}-questions.md").write_text(
        f"""---
title: Discussion week {week_num}
id: discussion-{week_num}
---

Discuss week {week_num}.
""",
        encoding="utf-8",
    )
    if include_presentation:
        week_dir = class_dir / f"week{week_num}"
        week_dir.mkdir(exist_ok=True)
        (week_dir / "index.md").write_text(
            f"1. Presentation week {week_num}\n", encoding="utf-8"
        )
        (week_dir / "q01.html").write_text(
            f"<p>Presentation week {week_num}</p>\n", encoding="utf-8"
        )


def test_runtime_question_readers_accept_utf8_bom(course_env):
    class_dir = Path(config.CLASSES_DIR) / course_env["slug"]
    (class_dir / "week-1-questions.md").write_text(
        "---\ntitle: BOM discussion\nid: bom-discussion\n---\n\nDiscuss it.\n",
        encoding="utf-8-sig",
    )
    week_dir = class_dir / "week1"
    week_dir.mkdir()
    (week_dir / "index.md").write_text(
        "1. BOM presentation\n", encoding="utf-8-sig"
    )
    (week_dir / "q01.html").write_text(
        "<p>BOM presentation</p>\n", encoding="utf-8-sig"
    )
    client = _instructor_client(course_env)

    bank = client.get("/api/discussion_questions")
    assert bank.status_code == 200
    assert bank.get_json()["questions"][0]["title"] == "BOM discussion"
    selected = client.post(
        "/api/set_discussion_week",
        json={
            "week": 1,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert selected.status_code == 200
    with _connect(course_env) as db:
        title = db.execute(
            """SELECT title FROM questions
               WHERE course_id = ? AND source_key = 'presentation:1:1'""",
            (course_env["course_id"],),
        ).fetchone()[0]
    assert title == "BOM presentation"
    assert app_module.load_question_html(
        course_env["slug"], 1, 1
    ) == "<p>BOM presentation</p>\n"


def test_week_selector_allows_discussion_only_week(course_env):
    _write_catalog_week(course_env, 1)
    _write_catalog_week(course_env, 2, include_presentation=False)
    client = _instructor_client(course_env)

    catalog = client.get("/api/discussion_questions").get_json()
    by_week = {week["num"]: week for week in catalog["weeks"]}
    assert by_week[1]["ready"] is True
    assert by_week[2]["discussion_ready"] is True
    assert by_week[2]["presentation_ready"] is False
    assert by_week[2]["ready"] is False

    selected_discussion_only = client.post(
        "/api/set_discussion_week",
        json={
            "week": 2,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert selected_discussion_only.status_code == 200
    assert selected_discussion_only.get_json()["presentation_ready"] is False
    assert selected_discussion_only.get_json()["question_sync"] == "unavailable"
    assert _state_row(course_env)["discussion_week"] == 2
    with _connect(course_env) as db:
        base_count = db.execute(
            """SELECT COUNT(*) FROM questions
               WHERE course_id = ? AND COALESCE(week_num, 1) = 2
                 AND (source_key IS NULL
                      OR source_key LIKE 'presentation:%')""",
            (course_env["course_id"],),
        ).fetchone()[0]
    assert base_count == 0

    selected = client.post(
        "/api/set_discussion_week",
        json={
            "week": 1,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert selected.status_code == 200
    assert selected.get_json()["question_sync"] == "synced"


def test_appendix_delete_uses_stable_id_and_unposts_current_question(course_env):
    client = _instructor_client(course_env)
    roster_version = _assign_all_active_students(course_env)
    first_add = client.post(
        "/api/questions",
        json={
            "title": "First",
            "content": "First appendix body.",
            "week": 1,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    second_add = client.post(
        "/api/questions",
        json={
            "title": "Second",
            "content": "Second appendix body.",
            "week": 1,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert first_add.get_json()["appendix_id"] == "A1"
    assert second_add.get_json()["appendix_id"] == "A2"

    phase = client.post(
        "/api/set_phase",
        json={
            "phase": "discussion",
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
            "expected_roster_version": roster_version,
        },
    )
    assert phase.status_code == 200
    bank = client.get("/api/discussion_questions").get_json()
    assert bank["current_week"] == 1
    by_id = {question.get("appendix_id"): question for question in bank["questions"]}
    assert set(by_id) == {"A1", "A2"}

    current = by_id["A2"]
    posted = client.post(
        "/api/set_question",
        json={
            "key": current["key"],
            "title": current["title"],
            "content": current["content"],
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
            "expected_discussion_key": "",
        },
    )
    assert posted.status_code == 200
    posted_question = posted.get_json()["discussion_question"]
    posted_instance_key = posted_question["key"]
    assert posted_question["source_key"] == current["key"]
    with _connect(course_env) as db:
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, question_key, grader_id, recipient_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                course_env["course_id"],
                SESSION_KEY,
                posted_instance_key,
                course_env["students"]["s1"],
                course_env["students"]["s2"],
            ),
        )
        db.commit()

    deleted_first = client.post(
        "/api/delete_appendix_question",
        json={
            "appendix_id": "A1",
            "week": 1,
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert deleted_first.status_code == 200
    assert deleted_first.get_json()["unposted"] is False
    state = _state_row(course_env)
    assert state["current_discussion_key"] == posted_instance_key
    assert state["current_discussion_source_key"] == current["key"]

    stale_repeat = client.post(
        "/api/delete_appendix_question",
        json={
            "appendix_id": "A1",
            "week": 1,
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert stale_repeat.status_code == 404
    remaining = client.get("/api/discussion_questions").get_json()["questions"]
    assert [question.get("appendix_id") for question in remaining] == ["A2"]

    deleted_current = client.post(
        "/api/delete_appendix_question",
        json={
            "appendix_id": "A2",
            "week": 1,
            "expected_phase": "discussion",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert deleted_current.status_code == 200
    assert deleted_current.get_json()["unposted"] is True
    state = _state_row(course_env)
    assert state["current_discussion_key"] is None
    assert state["current_discussion_source_key"] is None
    with _connect(course_env) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM teammate_thumbs"
        ).fetchone()[0] == 1


def test_eighty_student_poll_burst_stays_compact_and_lock_free(course_env):
    with _connect(course_env) as db:
        for index in range(5, 81):
            student_id = f"s{index}"
            db.execute(
                """INSERT INTO students
                   (course_id, student_id, name, pin, team_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    course_env["course_id"],
                    student_id,
                    f"Student {index}",
                    f"{index % 10000:04d}",
                    course_env["teams"][f"Team {((index - 1) % 4) + 1}"],
                ),
            )
        db.commit()
    _set_state(
        course_env,
        phase="discussion",
        current_discussion_key="traffic-q",
        current_discussion_title="Traffic question",
        current_discussion_content="x" * 32000,
    )
    clients = [
        _student_client(course_env, f"s{index}")
        for index in range(1, 81)
    ]

    def poll(client):
        response = client.get(
            "/api/poll",
            query_string={"known_discussion_key": "traffic-q"},
        )
        return response.status_code, len(response.data)

    with ThreadPoolExecutor(max_workers=40) as pool:
        results = list(pool.map(poll, clients))

    assert all(status == 200 for status, _size in results)
    assert max(size for _status, size in results) < 2000


def test_instructor_templates_render_new_controls_in_each_phase(course_env):
    client = _instructor_client(course_env)

    setup_html = client.get(
        f"/instructor/{course_env['slug']}"
    ).get_data(as_text=True)
    assert f'data-session-key="{SESSION_KEY}"' in setup_html
    assert 'id="btn-session-timer"' in setup_html

    _set_state(
        course_env,
        phase="discussion",
        current_discussion_key="discussion-control",
        current_discussion_title="Posted question",
        current_discussion_content="Posted body",
    )
    discussion_html = client.get(
        f"/instructor/{course_env['slug']}"
    ).get_data(as_text=True)
    assert 'id="btn-unpost-question"' in discussion_html
    assert "Currently posted: Posted question" in discussion_html

    _activate_presentation(course_env)
    competition_html = client.get(
        f"/instructor/{course_env['slug']}"
    ).get_data(as_text=True)
    assert 'id="btn-cancel-presentation"' in competition_html
    assert 'id="competition-appendix-form"' in competition_html
