"""Supported historical activity remains available across source versions."""

import csv
from datetime import datetime, timezone
from html.parser import HTMLParser
import io
import json
import zipfile

from openpyxl import load_workbook
import pytest

from test_versioning_exports import (
    SESSION_KEY,
    _connect,
    _instructor_client,
    _set_state,
    _student_client,
    _workbook_rows,
    app_module,
    database,
    versioned_course_env,
)


ACTIVITY_TABLES = (
    "teammate_thumbs", "presentation_ratings", "presentation_participants",
    "challenge_rounds", "challenge_ratings",
)


def _seed_activity(env, *, key, week, version, session_key):
    """One presenting member, one challenge, and one of each feedback type."""
    cid = env["course_id"]
    students = env["students"]
    teams = env["teams"]
    with _connect(env) as db:
        db.execute(
            """INSERT INTO presentation_participants
               (course_id, session_key, week_num, presentation_key,
                student_id, student_identifier, student_name,
                team_id, team_name, data_version)
               VALUES (?, ?, ?, ?, ?, 's1', 'Alice', ?, 'Team 1', ?)""",
            (cid, session_key, week, key, students["s1"],
             teams["Team 1"], version),
        )
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, session_key, week_num, question_key, student_id,
                presenting_team_id, presenting_team_name, question_id,
                question_title, rater_team_id, rater_team_name,
                q1_developed, q2_easy, data_version)
               VALUES (?, ?, ?, ?, ?, ?, 'Team 1', ?, ?, ?, 'Team 2', 4, 5, ?)""",
            (cid, session_key, week, key, students["s3"], teams["Team 1"],
             env["question_id"], key, teams["Team 2"], version),
        )
        db.execute(
            """INSERT INTO challenge_rounds
               (course_id, session_key, week_num, presentation_key,
                challenge_key, challenge_num, challenger_id, challenger_name,
                challenger_team_id, challenger_team_name, presenting_team_id,
                presenting_team_name, question_id, question_title, data_version)
               VALUES (?, ?, ?, ?, ?, 1, ?, 'Eve', ?, 'Team 3', ?,
                       'Team 1', ?, ?, ?)""",
            (cid, session_key, week, key, f"challenge-{key}", students["s5"],
             teams["Team 3"], teams["Team 1"], env["question_id"], key, version),
        )
        db.execute(
            """INSERT INTO challenge_ratings
               (course_id, session_key, week_num, presentation_key,
                challenge_key, challenger_id, challenger_name,
                challenger_team_id, challenger_team_name, rater_id,
                rater_name, rater_team_id, rater_team_name, score, data_version)
               VALUES (?, ?, ?, ?, ?, ?, 'Eve', ?, 'Team 3', ?, 'Cara', ?,
                       'Team 2', 4, ?)""",
            (cid, session_key, week, key, f"challenge-{key}", students["s5"],
             teams["Team 3"], students["s3"], teams["Team 2"], version),
        )
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, week_num, question_key,
                source_question_key, grader_id, recipient_id,
                grader_team_id, grader_team_name, recipient_team_id,
                recipient_team_name, data_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Team 1', ?, 'Team 1', ?)""",
            (cid, session_key, week, key, key, students["s1"], students["s2"],
             teams["Team 1"], teams["Team 1"], version),
        )
        if isinstance(week, int) and week > 0:
            history = json.loads(db.execute(
                "SELECT presentation_history FROM course_state WHERE course_id = ?",
                (cid,),
            ).fetchone()[0])
            history.append({
                "presentation_key": key, "session_key": session_key,
                "week_num": week, "team_id": teams["Team 1"],
                "team": "Team 1", "question_id": env["question_id"],
                "title": key, "responses": 1, "data_version": version,
            })
            db.execute(
                "UPDATE course_state SET presentation_history = ? WHERE course_id = ?",
                (json.dumps(history), cid),
            )
        db.commit()


@pytest.fixture
def mixed_history_env(versioned_course_env):
    env = versioned_course_env
    for key, week, version, session_key in (
        ("week-one", 1, "1.2.5", SESSION_KEY),
        ("week-two", 2, "1.3.3", SESSION_KEY + 1),
        ("unknown-version", 1, "1.4.0", SESSION_KEY + 10),
        ("malformed-version", 1, "broken", SESSION_KEY + 11),
        ("unknown-week", None, "1.2.5", SESSION_KEY + 12),
        ("zero-week", 0, "1.3.3", SESSION_KEY + 13),
        ("fractional-week", 1.5, "1.2.5", SESSION_KEY + 14),
    ):
        _seed_activity(env, key=key, week=week, version=version,
                       session_key=session_key)
    _set_state(env, phase="setup", discussion_week=3,
               session_key=SESSION_KEY + 2)
    return env


class _ParticipationMembers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.members = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li" and "data-presentation-count" in attrs:
            self.members[attrs["data-student-id"]] = (
                int(attrs["data-presentation-count"]),
                int(attrs["data-challenger-count"]),
            )


def _export(instructor, env, week):
    response = instructor.get(f"/export/{env['slug']}?week={week}")
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        workbook = load_workbook(io.BytesIO(archive.read("course_data.xlsx")))
    return workbook, manifest


def _stored_activity(env):
    with _connect(env) as db:
        return {
            table: [tuple(row) for row in db.execute(
                f"SELECT * FROM {table} ORDER BY id"
            )]
            for table in ACTIVITY_TABLES
        }


def test_week_three_instructor_counts_include_supported_earlier_weeks(
        mixed_history_env):
    env = mixed_history_env
    instructor = _instructor_client(env)
    teams = instructor.get("/api/teams").get_json()
    members = {s["student_id"]: s for team in teams for s in team["members"]}
    assert members["s1"]["presentation_count"] == 2
    assert members["s5"]["challenger_count"] == 2
    roster = instructor.get("/api/students?per_page=100").get_json()
    students = {s["student_id"]: s for s in roster["students"]}
    assert students["s1"]["presentation_count"] == 2
    assert students["s5"]["challenger_count"] == 2

    timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(" ")
    _set_state(
        env, phase="competition", active_team_id=env["teams"]["Team 2"],
        active_question_id=env["question_id"], current_question="Current question",
        poll_question_key="week-three-current", presentation_created_at=timestamp,
        presentation_started_at=timestamp,
    )
    for sid in ("s1", "s5"):
        raised = _student_client(env, sid).post(
            "/api/raise_hand", json={"presentation_key": "week-three-current"},
        )
        assert raised.status_code == 200
    state = instructor.get("/api/state").get_json()
    hands = {row["student_id"]: row for row in state["challenge_hands"]}
    assert hands[env["students"]["s1"]]["presentation_count"] == 2
    assert hands[env["students"]["s5"]]["challenger_count"] == 2

    response = instructor.get(f"/instructor/{env['slug']}")
    assert response.status_code == 200
    parsed = _ParticipationMembers()
    parsed.feed(response.get_data(as_text=True))
    assert parsed.members[str(env["students"]["s1"])] == (2, 0)
    assert parsed.members[str(env["students"]["s5"])] == (0, 2)


def test_normal_week_one_export_includes_old_activity_without_rewriting_it(
        mixed_history_env):
    env = mixed_history_env
    before = _stored_activity(env)
    instructor = _instructor_client(env)
    workbook, manifest = _export(instructor, env, 1)
    try:
        for sheet, key_column, expected_key in (
            ("Peer Reviews", "discussion_post_key", "week-one"),
            ("Presentation Ratings", "presentation_key", "week-one"),
            ("Presentation Participants", "presentation_key", "week-one"),
            ("Challenge Rounds", "challenge_key", "challenge-week-one"),
            ("Challenge Ratings", "challenge_key", "challenge-week-one"),
        ):
            rows = _workbook_rows(workbook, sheet)
            assert len(rows) == 1
            assert rows[0][key_column] == expected_key
            assert rows[0]["data_version"] == "v1.2.5"
        round_row = _workbook_rows(workbook, "Challenge Rounds")[0]
        assert round_row["ratings_submitted"] == 1
        assert round_row["average_score_1to5"] == 4
        assert round_row["challenger_id"] == "s5"
        for sheet in ("Students", "Participation Roster"):
            students = {r["student_id"]: r for r in _workbook_rows(workbook, sheet)}
            assert students["s1"]["course_presentation_team_turns"] == 2
            assert students["s5"]["course_challenger_turns"] == 2
        assert manifest["website_version"] == "v1.3.3"
        assert manifest["database_schema_version"] == "v1.3.0"
        assert "v1.2.5" in manifest["data_versions"]
    finally:
        workbook.close()

    legacy = instructor.get(f"/export/{env['slug']}/legacy-feedback.csv")
    assert legacy.status_code == 200
    legacy_rows = list(csv.DictReader(io.StringIO(legacy.data.decode("utf-8-sig"))))
    assert legacy_rows
    assert all(r["question_key"] not in {"week-one", "week-two"}
               for r in legacy_rows)
    assert {r["legacy_reason"] for r in legacy_rows} >= {
        "unknown_week", "malformed_data_version",
    }
    assert _stored_activity(env) == before


def test_saved_week_two_export_works_while_week_one_is_selected(mixed_history_env):
    env = mixed_history_env
    _set_state(env, discussion_week=1)
    workbook, _manifest = _export(_instructor_client(env), env, 2)
    try:
        participants = _workbook_rows(workbook, "Presentation Participants")
        assert [r["presentation_key"] for r in participants] == ["week-two"]
        assert participants[0]["data_version"] == "v1.3.3"
        rounds = _workbook_rows(workbook, "Challenge Rounds")
        assert [r["challenge_key"] for r in rounds] == ["challenge-week-two"]
    finally:
        workbook.close()


@pytest.mark.parametrize("source_version,expected", (
    ("1.0.0", 1), ("1.1.9", 1), ("1.2.5", 1), ("1.3.3", 1),
    ("1.3.99", 1), ("0.9.9", 0), ("1.4.0", 0), ("2.0.0", 0),
    ("v1.2.5", 0), ("1.2", 0), ("broken", 0), (None, 0),
))
def test_activity_reader_support_does_not_relax_schema_compatibility(
        versioned_course_env, source_version, expected):
    with app_module.app.app_context():
        db = database.get_db(versioned_course_env["slug"])
        assert db.execute(
            "SELECT popping_activity_supported(?)", [source_version],
        ).fetchone()[0] == expected
        assert db.execute(
            "SELECT popping_version_compatible('1.2.5', '1.3.0')"
        ).fetchone()[0] == 0
