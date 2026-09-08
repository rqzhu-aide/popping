"""A late arrival can join after an active session returns to Setup."""

import pytest

from test_workflow_safety import (
    SESSION_KEY,
    _activate_current_schema_presentation,
    _connect,
    _instructor_client,
    _seed_current_session_thumb,
    _set_live_team_recovery_state,
    _state_row,
    app_module,
    course_env,
)


def _saved_activity(env):
    with _connect(env) as db:
        return {
            table: [dict(row) for row in db.execute(
                f"SELECT * FROM {table} ORDER BY id"
            )]
            for table in (
                "teammate_thumbs", "presentation_ratings",
                "presentation_participants", "challenge_rounds",
                "challenge_ratings",
            )
        }


def test_http_login_allows_first_join_in_setup_with_saved_session_activity(
        course_env):
    env = course_env
    instructor = _instructor_client(env)
    _activate_current_schema_presentation(env)
    _seed_current_session_thumb(env)
    finished = instructor.post(
        "/api/next_presentation", json={"presentation_key": "pres-current"},
    )
    assert finished.status_code == 200
    returned_to_setup = instructor.post(
        "/api/set_phase",
        json={
            "phase": "setup",
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert returned_to_setup.status_code == 200
    state_before = _state_row(env)
    assert state_before["phase"] == "setup"
    assert state_before["session_key"] == SESSION_KEY
    assert not state_before["teams_locked"]
    saved_before = _saved_activity(env)
    assert len(saved_before["teammate_thumbs"]) == 1
    assert len(saved_before["presentation_participants"]) == 2

    student = app_module.app.test_client()
    login_page = student.get(f"/login/{env['slug']}")
    assert login_page.status_code == 200
    logged_in = student.post(
        f"/login/{env['slug']}",
        data={"student_id": "s3", "pin": "3333"},
        follow_redirects=True,
    )
    assert logged_in.status_code == 200
    assert logged_in.request.path == "/dashboard"
    assert "Select Your Team" in logged_in.get_data(as_text=True)

    team_id = env["teams"]["Team 1"]
    joined = student.post("/api/join_team", json={"team_id": team_id})
    assert joined.status_code == 200, joined.get_json()
    for target in (env["teams"]["Team 2"], 0):
        blocked = student.post("/api/join_team", json={"team_id": target})
        assert blocked.status_code in (403, 409)

    with _connect(env) as db:
        late_student = db.execute(
            "SELECT team_id, last_team_id FROM students WHERE student_id = 's3'"
        ).fetchone()
    assert dict(late_student) == {"team_id": team_id, "last_team_id": team_id}
    assert _saved_activity(env) == saved_before
    state_after = _state_row(env)
    assert state_after["phase"] == "setup"
    assert state_after["session_key"] == SESSION_KEY
    assert state_after["presentation_history"] == state_before[
        "presentation_history"
    ]


@pytest.mark.parametrize("target_team", ("Team 1", "Team 2"))
def test_saved_setup_activity_allows_restoration_only_to_recorded_team(
        course_env, target_team):
    env = course_env
    assert _instructor_client(env).get("/api/state").status_code == 200
    _seed_current_session_thumb(env)
    _set_live_team_recovery_state(env, phase="setup")
    saved_before = _saved_activity(env)

    student = app_module.app.test_client()
    logged_in = student.post(
        f"/login/{env['slug']}",
        data={"student_id": "s1", "pin": "1111"},
        follow_redirects=True,
    )
    assert logged_in.status_code == 200
    assert logged_in.request.path == "/dashboard"
    response = student.post(
        "/api/join_team", json={"team_id": env["teams"][target_team]},
    )
    if target_team == "Team 1":
        assert response.status_code == 200, response.get_json()
        expected_team = env["teams"]["Team 1"]
    else:
        assert response.status_code in (403, 409)
        expected_team = None

    with _connect(env) as db:
        restored_student = db.execute(
            "SELECT team_id, last_team_id FROM students WHERE student_id = 's1'"
        ).fetchone()
    assert dict(restored_student) == {
        "team_id": expected_team,
        "last_team_id": env["teams"]["Team 1"],
    }
    assert _saved_activity(env) == saved_before
