"""Weekly awards keep saved activity usable across supported releases."""

import json

import pytest

from test_weekly_heroes import (
    SESSION_KEY,
    _connect,
    _instructor_client,
    _raw_source_snapshot,
    _seed_ranked_week,
    _set_state,
    hero_env,
)
import database
from versioning import APP_VERSION, SCHEMA_VERSION


def _seed_mixed_week(env):
    _seed_ranked_week(env, data_version="1.2.5")
    with _connect(env) as db:
        db.execute(
            """UPDATE presentation_ratings SET data_version = ?
               WHERE course_id = ? AND question_key = 'week-1-team-1'""",
            [APP_VERSION, env["course_id"]],
        )
        db.execute(
            """UPDATE presentation_participants SET data_version = '1.1.4'
               WHERE course_id = ? AND presentation_key = 'week-1-team-1'""",
            [env["course_id"]],
        )
        db.execute(
            """UPDATE course_state SET presentation_history = ?
               WHERE course_id = ?""",
            [json.dumps([{
                "presentation_key": "week-1-team-1",
                "week_num": 1,
                "team_id": env["teams"]["Team 1"],
                "team": "Team 1",
                "data_version": "1.0.8",
            }]), env["course_id"]],
        )
        db.commit()


def test_supported_history_combines_versions_and_keeps_historical_recipients(
        hero_env):
    _seed_mixed_week(hero_env)
    with _connect(hero_env) as db:
        before = _raw_source_snapshot(db, hero_env["course_id"], 1)
        preview = database.calculate_weekly_hero_preview(
            db, hero_env["course_id"], 1, include_supported_history=True,
        )
        assert preview["source_data_versions"] == [
            "1.0.8", "1.1.4", "1.2.5", APP_VERSION,
        ]
        assert preview["source_presentation_rating_count"] == 4
        assert preview["source_challenge_rating_count"] == 3
        assert preview["source_participant_count"] == 4
        assert preview["source_history_item_count"] == 1
        assert preview["recipient_coverage_complete"]
        gold = next(row for row in preview["results"]
                    if row["award_type"] == "gold")
        assert gold["score_sum"] == 10
        assert [row["student_identifier"] for row in gold["recipients"]] == ["s1"]
        assert gold["recipients"][0]["team_id"] == hero_env["teams"]["Team 1"]
        assert {row["challenger_identifier"] for row in preview["results"]
                if row["award_type"] == "bolt"} == {"s6", "s7"}

        outcome = database.save_weekly_hero_summary(db, preview)
        repeated = database.save_weekly_hero_summary(db, preview)
        assert outcome["status"] == "created"
        assert repeated["status"] == "unchanged"
        assert repeated["summary_id"] == outcome["summary_id"]
        assert _raw_source_snapshot(db, hero_env["course_id"], 1) == before
        saved = db.execute(
            "SELECT source_schema_version, source_data_versions FROM weekly_hero_summaries"
        ).fetchone()
        assert saved["source_schema_version"] == SCHEMA_VERSION
        assert json.loads(saved["source_data_versions"]) == preview["source_data_versions"]


def test_explicit_backfill_still_selects_only_the_requested_series(hero_env):
    _seed_mixed_week(hero_env)
    with _connect(hero_env) as db:
        preview = database.calculate_weekly_hero_preview(
            db, hero_env["course_id"], 1, source_schema_version="1.2.0",
        )
        assert not preview["include_supported_history"]
        assert preview["source_data_versions"] == ["1.2.5"]
        assert preview["source_presentation_rating_count"] == 3
        assert preview["source_participant_count"] == 3
        assert preview["source_history_item_count"] == 0


def test_supported_history_rechecks_older_source_rows_before_saving(hero_env):
    _seed_mixed_week(hero_env)
    with _connect(hero_env) as db:
        preview = database.calculate_weekly_hero_preview(
            db, hero_env["course_id"], 1, include_supported_history=True,
        )
        db.execute(
            """UPDATE challenge_ratings SET score = 1
               WHERE course_id = ? AND challenge_key = 'week-1-challenge-1'""",
            [hero_env["course_id"]],
        )
        with pytest.raises(RuntimeError, match="source data changed"):
            database.save_weekly_hero_summary(db, preview)
        assert db.execute("SELECT COUNT(*) FROM weekly_hero_summaries").fetchone()[0] == 0


def test_supported_history_never_invents_missing_historical_members(hero_env):
    _seed_mixed_week(hero_env)
    with _connect(hero_env) as db:
        db.execute(
            """DELETE FROM presentation_participants
               WHERE course_id = ? AND presentation_key = 'week-1-team-1'""",
            [hero_env["course_id"]],
        )
        preview = database.calculate_weekly_hero_preview(
            db, hero_env["course_id"], 1, include_supported_history=True,
        )
        assert not preview["recipient_coverage_complete"]
        with pytest.raises(RuntimeError, match="lack participant snapshots"):
            database.save_weekly_hero_summary(db, preview)


@pytest.mark.parametrize("unsupported_version", ["1.4.0", "2.0.0", "malformed"])
def test_supported_history_excludes_unknown_contracts(hero_env, unsupported_version):
    _seed_mixed_week(hero_env)
    with _connect(hero_env) as db:
        db.execute(
            """UPDATE presentation_ratings SET data_version = ?
               WHERE course_id = ? AND question_key = 'week-1-team-4'""",
            [unsupported_version, hero_env["course_id"]],
        )
        db.execute(
            """UPDATE presentation_participants SET data_version = ?
               WHERE course_id = ? AND presentation_key = 'week-1-team-4'""",
            [unsupported_version, hero_env["course_id"]],
        )
        preview = database.calculate_weekly_hero_preview(
            db, hero_env["course_id"], 1, include_supported_history=True,
        )
        assert preview["source_presentation_rating_count"] == 3
        assert preview["source_participant_count"] == 3
        assert unsupported_version not in preview["source_data_versions"]
        assert all(row["team_name"] != "Team 4" for row in preview["results"])


def test_ending_mixed_week_saves_all_supported_activity(hero_env):
    _seed_mixed_week(hero_env)
    _set_state(hero_env, phase="competition", discussion_week=1)
    with _connect(hero_env) as db:
        before = _raw_source_snapshot(db, hero_env["course_id"], 1)
    response = _instructor_client(hero_env).post(
        "/api/set_phase",
        json={
            "phase": "ended",
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "presentation_key": "",
            "confirm_end_session": True,
        },
    )
    assert response.status_code == 200, response.get_json()
    with _connect(hero_env) as db:
        summary = db.execute("SELECT * FROM weekly_hero_summaries").fetchone()
        assert summary["source_presentation_rating_count"] == 4
        assert summary["source_challenge_rating_count"] == 3
        assert _raw_source_snapshot(db, hero_env["course_id"], 1) == before
        assert db.execute("SELECT phase FROM course_state").fetchone()[0] == "ended"
