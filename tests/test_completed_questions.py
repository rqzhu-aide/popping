"""Question completion follows a lecture week across classroom sessions."""

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
def test_finished_question_remains_marked_after_restarting_same_week(
        versioned_course_env, version):
    env = versioned_course_env
    history = json.dumps([_history(env, data_version=version)])
    _set_state(env, phase="competition", presentation_history=history)
    client = _instructor_client(env)

    html = client.get(f"/instructor/{env['slug']}").get_data(as_text=True)
    assert f'value="{env["question_id"]}" data-completed="1"' in html
    assert "#1: Versioned Question (completed)</option>" in html

    for state in (
        client.get("/api/state").get_json(),
        client.get("/api/poll").get_json()["state"],
    ):
        assert state["completed_question_ids"] == [env["question_id"]]
        assert state["presentation_history"] == []
        assert state["completed_presentation_count"] == 0
    with _connect(env) as db:
        assert db.execute(
            "SELECT presentation_history FROM course_state WHERE course_id = ?",
            [env["course_id"]],
        ).fetchone()[0] == history


def test_question_completion_uses_selected_week_and_clears_removed_history(
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
                 question_id=second_id),
    ]
    _set_state(env, phase="competition", presentation_history=json.dumps(history))
    client = _instructor_client(env)
    assert client.get("/api/state").get_json()["completed_question_ids"] == [
        env["question_id"]
    ]
    _set_state(env, discussion_week=2)
    assert client.get("/api/state").get_json()["completed_question_ids"] == [second_id]
    _set_state(env, presentation_history="[]")
    assert client.get("/api/state").get_json()["completed_question_ids"] == []


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


def test_active_question_is_not_finished_and_completion_is_instructor_only(
        versioned_course_env):
    env = versioned_course_env
    _set_state(env, phase="competition", active_question_id=env["question_id"],
               active_team_id=env["teams"]["Team 1"])
    assert _instructor_client(env).get("/api/state").get_json()[
        "completed_question_ids"
    ] == []
    _set_state(env, presentation_history=json.dumps([_history(env)]))
    student = _student_client(env, "s1")
    assert "completed_question_ids" not in student.get("/api/state").get_json()
    assert "completed_question_ids" not in student.get("/api/poll").get_json()["state"]
