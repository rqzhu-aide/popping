"""Regressions preventing legacy presentation rows from changing the catalog."""

from tests.test_canonical_question_upload import (
    SESSION_KEY,
    _confirm_upload,
    _connect,
    _instructor_client,
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
