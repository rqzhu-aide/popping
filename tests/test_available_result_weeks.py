"""Saved-week downloads stay available when the selected lecture week changes."""

import json

import pytest

from test_versioning_exports import (
    APP_VERSION,
    SCHEMA_VERSION,
    SESSION_KEY,
    _connect,
    _instructor_client,
    _set_state,
    app_module,
    versioned_course_env,
)


def _seed_activity(env, source, *, week=2, data_version="1.2.5"):
    key = f"{source}-{week}-{data_version}"
    with _connect(env) as db:
        common = [env["course_id"], SESSION_KEY - 1, week]
        if source == "presentation_participant":
            db.execute(
                """INSERT INTO presentation_participants
                   (course_id, session_key, week_num, presentation_key,
                    student_id, student_identifier, student_name, data_version)
                   VALUES (?, ?, ?, ?, ?, 's1', 'Alice', ?)""",
                [*common, key, env["students"]["s1"], data_version],
            )
        elif source == "presentation_rating":
            db.execute(
                """INSERT INTO presentation_ratings
                   (course_id, session_key, week_num, question_key,
                    student_id, q1_developed, q2_easy, data_version)
                   VALUES (?, ?, ?, ?, ?, 4, 5, ?)""",
                [*common, key, env["students"]["s1"], data_version],
            )
        elif source == "teammate_thumb":
            db.execute(
                """INSERT INTO teammate_thumbs
                   (course_id, session_key, week_num, question_key,
                    grader_id, recipient_id, data_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [*common, key, env["students"]["s1"],
                 env["students"]["s2"], data_version],
            )
        elif source == "challenge_round":
            db.execute(
                """INSERT INTO challenge_rounds
                   (course_id, session_key, week_num, presentation_key,
                    challenge_key, challenge_num, challenger_id, data_version)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                [*common, key, key, env["students"]["s1"], data_version],
            )
        elif source == "challenge_rating":
            db.execute(
                """INSERT INTO challenge_ratings
                   (course_id, session_key, week_num, presentation_key,
                    challenge_key, challenger_id, rater_id, score, data_version)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 4, ?)""",
                [*common, key, key, env["students"]["s1"],
                 env["students"]["s2"], data_version],
            )
        elif source == "weekly_hero":
            db.execute(
                """INSERT INTO weekly_hero_summaries
                   (course_id, week_num, calculation_version,
                    source_schema_version, source_data_versions,
                    source_fingerprint, source_presentation_rating_count,
                    source_challenge_rating_count, source_participant_count,
                    source_history_item_count, data_version)
                   VALUES (?, ?, '1.0.0', ?, '[]', ?, 0, 0, 0, 0, ?)""",
                [env["course_id"], week, SCHEMA_VERSION, "a" * 64, APP_VERSION],
            )
        elif source == "history":
            db.execute(
                "UPDATE course_state SET presentation_history = ? WHERE course_id = ?",
                [json.dumps([{
                    "presentation_key": key,
                    "session_key": SESSION_KEY - 1,
                    "week_num": week,
                    "team": "Team 1",
                    "team_id": env["teams"]["Team 1"],
                    "title": "Earlier session presentation",
                    "data_version": data_version,
                }]), env["course_id"]],
            )
        else:
            raise AssertionError(f"Unknown fixture source: {source}")
        db.commit()


@pytest.mark.parametrize("source", [
    "teammate_thumb", "presentation_rating", "presentation_participant",
    "challenge_round", "challenge_rating", "weekly_hero", "history",
])
def test_saved_higher_week_is_listed_and_downloadable(versioned_course_env, source):
    env = versioned_course_env
    _seed_activity(env, source)
    client = _instructor_client(env)

    response = client.get(f"/instructor/{env['slug']}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Current Week (1)" in html
    assert f'href="/export/{env["slug"]}?week=2"' in html
    # This covers history-only weeks and zero-rated participation events too.
    assert client.get(f"/export/{env['slug']}?week=2").status_code == 200


def test_result_weeks_merge_supported_versions_and_keep_empty_past_weeks(
        versioned_course_env):
    env = versioned_course_env
    _seed_activity(env, "presentation_participant", week=4)
    _seed_activity(env, "presentation_participant", week=4, data_version=APP_VERSION)
    _set_state(env, discussion_week=3)
    html = _instructor_client(env).get(
        f"/instructor/{env['slug']}"
    ).get_data(as_text=True)
    assert "Current Week (3)" in html
    for week in (1, 2, 4):
        assert html.count(f'href="/export/{env["slug"]}?week={week}"') == 1
    assert f'?week=5"' not in html


@pytest.mark.parametrize("data_version", [None, "2.0.0", "broken"])
def test_future_week_without_supported_saved_activity_is_unavailable(
        versioned_course_env, data_version):
    env = versioned_course_env
    if data_version is not None:
        _seed_activity(env, "presentation_participant", data_version=data_version)
    client = _instructor_client(env)
    html = client.get(f"/instructor/{env['slug']}").get_data(as_text=True)
    assert f'?week=2"' not in html
    assert client.get(f"/export/{env['slug']}?week=2").status_code == 400


@pytest.mark.parametrize("phase", ["discussion", "competition"])
def test_live_phase_keeps_saved_week_download_disabled(versioned_course_env, phase):
    env = versioned_course_env
    _seed_activity(env, "challenge_round")
    _set_state(env, phase=phase)
    client = _instructor_client(env)
    html = client.get(f"/instructor/{env['slug']}").get_data(as_text=True)
    assert "Week 2 (available after End Session)" in html
    assert f'href="/export/{env["slug"]}?week=2"' not in html
    assert client.get(f"/export/{env['slug']}?week=2").status_code == 409


def test_saved_week_discovery_is_not_part_of_polling(versioned_course_env, monkeypatch):
    env = versioned_course_env
    _seed_activity(env, "presentation_participant")
    calls = []

    def unexpected_week_discovery(*args, **kwargs):
        calls.append(True)
        raise AssertionError("Result-week discovery must only run for page renders")

    monkeypatch.setattr(app_module, "_available_result_weeks", unexpected_week_discovery)
    assert _instructor_client(env).get("/api/poll").status_code == 200
    assert calls == []
