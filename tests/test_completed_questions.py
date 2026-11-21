"""Presentation completion follows a lecture week across classroom sessions."""

import json

import pytest

from test_versioning_exports import (
    SESSION_KEY,
    _connect,
    _instructor_client,
    _set_state,
    _student_client,
    versioned_course_env,
)


def _history(env, **overrides):
    return {
        "presentation_key": "finished-earlier-session",
        "session_key": SESSION_KEY - 1,
        "week_num": 1,
        "question_id": env["question_id"],
        "team_id": env["teams"]["Team 1"],
        "team": "Team 1",
        "title": "Versioned Question",
        "responses": 0,
        "data_version": "1.2.5",
        **overrides,
    }


@pytest.mark.parametrize("version", ["1.2.5", "1.3.3"])
def test_finished_team_and_question_remain_marked_after_restarting_same_week(
        versioned_course_env, version):
    env = versioned_course_env
    history = json.dumps([_history(env, data_version=version)])
    _set_state(env, phase="competition", presentation_history=history)
    client = _instructor_client(env)

    html = client.get(f"/instructor/{env['slug']}").get_data(as_text=True)
    assert f'value="{env["question_id"]}" data-completed="1"' in html
    assert "#1: Versioned Question (completed)</option>" in html
    assert f'data-completed-team-ids="[{env["teams"]["Team 1"]}]"' in html

    for state in (
        client.get("/api/state").get_json(),
        client.get("/api/poll").get_json()["state"],
    ):
        assert state["completed_question_ids"] == [env["question_id"]]
        assert state["completed_team_ids"] == [env["teams"]["Team 1"]]
        assert state["presentation_history"] == []
        assert state["completed_presentation_count"] == 0
    with _connect(env) as db:
        assert db.execute(
            "SELECT presentation_history FROM course_state WHERE course_id = ?",
            [env["course_id"]],
        ).fetchone()[0] == history


def test_completion_uses_selected_week_and_clears_removed_history(
        versioned_course_env):
    env = versioned_course_env
    with _connect(env) as db:
        second_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, title, question_text, week_num, source_key)
               VALUES (?, 1, 'Week Two', 'Discuss week two.', 2, 'week-2-q-two')""",
            [env["course_id"]],
        ).lastrowid
        db.commit()
    history = [
        _history(env),
        _history(env, presentation_key="week-two", week_num=2,
                 question_id=second_id, team_id=env["teams"]["Team 2"]),
    ]
    _set_state(env, phase="competition", presentation_history=json.dumps(history))
    client = _instructor_client(env)
    state = client.get("/api/state").get_json()
    assert state["completed_question_ids"] == [env["question_id"]]
    assert state["completed_team_ids"] == [env["teams"]["Team 1"]]
    _set_state(env, discussion_week=2)
    state = client.get("/api/state").get_json()
    assert state["completed_question_ids"] == [second_id]
    assert state["completed_team_ids"] == [env["teams"]["Team 2"]]
    _set_state(env, presentation_history="[]")
    state = client.get("/api/state").get_json()
    assert state["completed_question_ids"] == []
    assert state["completed_team_ids"] == []


@pytest.mark.parametrize("overrides,completed", [
    ({"week_num": None}, True),
    ({"week_num": 0}, False),
    ({"week_num": 2}, False),
    ({"data_version": "1.4.0"}, False),
    ({"data_version": "broken"}, False),
    ({"question_id": None}, False),
])
def test_completion_uses_known_saved_question_identity(
        versioned_course_env, overrides, completed):
    env = versioned_course_env
    _set_state(env, phase="competition", presentation_history=json.dumps([
        _history(env, **overrides)
    ]))
    result = _instructor_client(env).get("/api/state").get_json()
    assert result["completed_question_ids"] == (
        [env["question_id"]] if completed else []
    )


def test_active_presentation_is_not_finished_and_completion_is_instructor_only(
        versioned_course_env):
    env = versioned_course_env
    _set_state(env, phase="competition", active_question_id=env["question_id"],
               active_team_id=env["teams"]["Team 1"])
    state = _instructor_client(env).get("/api/state").get_json()
    assert state["completed_question_ids"] == []
    assert state["completed_team_ids"] == []
    _set_state(env, presentation_history=json.dumps([_history(env)]))
    student = _student_client(env, "s1")
    for state in (
        student.get("/api/state").get_json(),
        student.get("/api/poll").get_json()["state"],
    ):
        assert "completed_question_ids" not in state
        assert "completed_team_ids" not in state


def test_team_completion_recovers_legacy_names_and_weeks(versioned_course_env):
    env = versioned_course_env
    history = [
        {
            "team": "Team 1",
            "question_id": env["question_id"],
            "started_at": "2026-01-01 10:00:00",
        },
        _history(env, presentation_key="legacy-rating", week_num=None,
                 question_id=None, team_id=env["teams"]["Team 2"]),
        _history(env, presentation_key="unsupported", data_version="1.4.0",
                 team_id=env["teams"]["Team 3"]),
        _history(env, presentation_key="unknown-week", week_num=None,
                 question_id=None, team_id=env["teams"]["Team 3"]),
        _history(env, presentation_key="unknown-team", team_id=999, team="Team 3"),
    ]
    with _connect(env) as db:
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, week_num, data_version)
               VALUES (?, ?, 'legacy-rating', 1, '1.2.5')""",
            (env["course_id"], env["students"]["s1"]),
        )
        db.commit()
    _set_state(env, phase="competition", presentation_history=json.dumps(history))
    state = _instructor_client(env).get("/api/state").get_json()
    assert state["completed_question_ids"] == [env["question_id"]]
    assert state["completed_team_ids"] == [
        env["teams"]["Team 1"], env["teams"]["Team 2"],
    ]


def test_finished_team_stays_completed_after_classroom_restart(versioned_course_env):
    env = versioned_course_env
    _set_state(env, phase="competition")
    client = _instructor_client(env)
    team_id = env["teams"]["Team 1"]
    selection = {
        "expected_phase": "competition",
        "expected_session_key": SESSION_KEY,
        "team_id": team_id,
        "question_id": env["question_id"],
    }
    started = client.post("/api/start_presentation", json=selection)
    assert started.status_code == 200
    assert client.get("/api/poll").get_json()["state"]["completed_team_ids"] == []
    canceled = client.post("/api/cancel_presentation", json={
        "presentation_key": started.get_json()["presentation_key"],
    })
    assert canceled.status_code == 200
    assert client.get("/api/poll").get_json()["state"]["completed_team_ids"] == []

    restarted = client.post("/api/start_presentation", json=selection)
    assert restarted.status_code == 200
    finished = client.post("/api/next_presentation", json={
        "presentation_key": restarted.get_json()["presentation_key"],
    })
    assert finished.status_code == 200
    state = client.get("/api/poll").get_json()["state"]
    assert state["completed_team_ids"] == [team_id]
    assert len(state["presentation_history"]) == 1

    ended = client.post("/api/set_phase", json={
        "phase": "ended",
        "expected_phase": "competition",
        "expected_session_key": SESSION_KEY,
        "confirm_end_session": True,
    })
    assert ended.status_code == 200
    state = client.get("/api/state").get_json()
    reopened = client.post("/api/set_phase", json={
        "phase": "competition",
        "expected_phase": "ended",
        "expected_session_key": SESSION_KEY,
        "expected_roster_version": state["roster_version"],
    })
    assert reopened.status_code == 200
    state = client.get("/api/poll").get_json()["state"]
    assert state["session_key"] == SESSION_KEY + 1
    assert state["discussion_week"] == 1
    assert state["completed_team_ids"] == [team_id]
    assert state["presentation_history"] == []
