"""Focused contract tests for the instructor's Clear All Teams action."""

from pathlib import Path
import sys


TESTS_ROOT = Path(__file__).resolve().parent
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))

from test_workflow_safety import (  # noqa: E402
    _connect,
    _instructor_client,
    _seed_current_session_thumb,
    _setup_payload,
    _student_client,
    course_env,
)


def _roster_snapshot(env):
    with _connect(env) as db:
        state = db.execute(
            """SELECT teams_locked, roster_version
               FROM course_state WHERE course_id = ?""",
            [env["course_id"]],
        ).fetchone()
        students = db.execute(
            """SELECT student_id, team_id, last_team_id
               FROM students
               WHERE course_id = ? AND is_active = 1
               ORDER BY student_id""",
            [env["course_id"]],
        ).fetchall()
    return dict(state), {
        row["student_id"]: (row["team_id"], row["last_team_id"])
        for row in students
    }


def test_clear_all_teams_clears_preserves_history_and_locks(course_env):
    team_1 = course_env["teams"]["Team 1"]
    team_2 = course_env["teams"]["Team 2"]
    team_3 = course_env["teams"]["Team 3"]
    with _connect(course_env) as db:
        db.execute(
            "UPDATE students SET last_team_id = ? WHERE student_id = 's2'",
            [team_3],
        )
        db.commit()

    response = _instructor_client(course_env).post(
        "/api/unassign_all",
        json=_setup_payload(),
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 3
    assert data["locked"] is True

    state, students = _roster_snapshot(course_env)
    assert state["teams_locked"] == 1
    assert state["roster_version"] == data["roster_version"]
    assert students == {
        "s1": (None, team_1),
        "s2": (None, team_3),
        "s3": (None, None),
        "s4": (None, team_2),
    }

    rejoin = _student_client(course_env, "s1").post(
        "/api/join_team",
        json={"team_id": team_2},
    )
    assert rejoin.status_code == 403
    assert "locked" in rejoin.get_json()["error"].lower()
    assert all(
        team_id is None
        for team_id, _last_team_id in _roster_snapshot(course_env)[1].values()
    )


def test_clear_all_teams_locks_an_already_empty_unlocked_roster(course_env):
    with _connect(course_env) as db:
        db.execute(
            "UPDATE students SET team_id = NULL WHERE course_id = ?",
            [course_env["course_id"]],
        )
        db.commit()

    response = _instructor_client(course_env).post(
        "/api/unassign_all",
        json=_setup_payload(),
    )

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["count"] == 0
    assert data["locked"] is True
    state, students = _roster_snapshot(course_env)
    assert state["teams_locked"] == 1
    assert state["roster_version"] == data["roster_version"]
    assert all(team_id is None for team_id, _last_team_id in students.values())

    rejoin = _student_client(course_env, "s1").post(
        "/api/join_team",
        json={"team_id": course_env["teams"]["Team 1"]},
    )
    assert rejoin.status_code == 403


def test_clear_all_teams_freeze_guard_changes_neither_roster_nor_lock(
        course_env):
    _seed_current_session_thumb(course_env)
    before_state, before_students = _roster_snapshot(course_env)

    response = _instructor_client(course_env).post(
        "/api/unassign_all",
        json=_setup_payload(),
    )

    assert response.status_code == 409
    assert "End Session" in response.get_json()["error"]
    after_state, after_students = _roster_snapshot(course_env)
    assert after_state == before_state
    assert after_students == before_students
