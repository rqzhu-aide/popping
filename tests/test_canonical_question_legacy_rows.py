"""Regressions preventing legacy presentation rows from changing the catalog."""

from tests.test_canonical_question_upload import (
    SESSION_KEY,
    _confirm_upload,
    _connect,
    _instructor_client,
    _student_client,
    _weekly_source,
    upload_env,
)


def test_legacy_rows_neither_render_nor_start_after_canonical_sync(upload_env):
    payload = _weekly_source(
        ("canonical-alpha", "Canonical Alpha", "Canonical alpha body."),
        ("canonical-beta", "Canonical Beta", "Canonical beta body."),
    )
    client = _instructor_client(upload_env)
    _confirm_upload(client, payload)

    with _connect(upload_env) as db:
        stale_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, content,
                week_num, source_key)
               VALUES (?, 99, 'Stale legacy unique', 'Stale legacy unique',
                       'Wrong legacy body.', 1, 'presentation:1:99')""",
            (upload_env["course_id"],),
        ).lastrowid
        unrelated_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, content,
                week_num, source_key)
               VALUES (?, 98, 'Unrelated old unique', 'Unrelated old unique',
                       'Another wrong body.', 1, 'week-1-question-98')""",
            (upload_env["course_id"],),
        ).lastrowid
        db.execute(
            """UPDATE course_state SET phase = 'competition'
               WHERE course_id = ?""",
            (upload_env["course_id"],),
        )
        db.commit()

    page = client.get(f"/instructor/{upload_env['slug']}")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Canonical Alpha" in html
    assert "Canonical Beta" in html
    assert "Stale legacy unique" not in html
    assert "Unrelated old unique" not in html

    for stale_question_id in (stale_id, unrelated_id):
        response = client.post(
            "/api/start_presentation",
            json={
                "expected_phase": "competition",
                "expected_session_key": SESSION_KEY,
                "team_id": 1,
                "question_id": stale_question_id,
                "time_cap": 300,
            },
        )
        assert response.status_code in (404, 409)


def test_active_legacy_row_does_not_substitute_new_or_pre_rendered_content(
        upload_env):
    canonical = _weekly_source((
        "same-title",
        "Shared title",
        "New canonical body that was not active when the session started.",
    ))
    (upload_env["class_dir"] / "week-1-questions.md").write_bytes(canonical)
    legacy_dir = upload_env["class_dir"] / "week1"
    legacy_dir.mkdir()
    (legacy_dir / "q01.html").write_text(
        "<p>Obsolete pre-rendered body.</p>", encoding="utf-8"
    )

    with _connect(upload_env) as db:
        team_id = db.execute(
            "SELECT team_id FROM students WHERE id = ?",
            (upload_env["student_id"],),
        ).fetchone()[0]
        legacy_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, content,
                week_num, source_key)
               VALUES (?, 1, 'Shared title', 'Shared title', NULL, 1,
                       'presentation:1:1')""",
            (upload_env["course_id"],),
        ).lastrowid
        db.execute(
            """UPDATE course_state
               SET phase = 'competition', active_team_id = ?,
                   active_question_id = ?, current_question = 'Shared title'
               WHERE course_id = ?""",
            (team_id, legacy_id, upload_env["course_id"]),
        )
        db.commit()

    active = _student_client(upload_env).get(
        "/api/poll"
    ).get_json()["state"]["active_question"]

    assert active["id"] == legacy_id
    assert active["source_key"] == "presentation:1:1"
    assert active["question_text"] == "Shared title"
    assert active["content"] is None
    assert "html_content" not in active
