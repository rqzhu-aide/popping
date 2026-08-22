"""Regression tests for the single-source weekly question workflow."""

from contextlib import contextmanager
import io
import sqlite3
from pathlib import Path
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


SESSION_KEY = 11


def _weekly_source(*questions):
    return "".join(
        (
            "---\n"
            f"id: {question_id}\n"
            f'title: "{title}"\n'
            "---\n\n"
            f"{content}\n\n"
        )
        for question_id, title, content in questions
    ).encode("utf-8")


@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    classes_dir = tmp_path / "classes"
    data_dir.mkdir()
    classes_dir.mkdir()
    slug = f"questions_{uuid.uuid4().hex[:8]}"
    class_dir = classes_dir / slug
    class_dir.mkdir()
    (class_dir / "course.yaml").write_text(
        "\n".join(
            (
                f"slug: {slug}",
                "name: Canonical Questions",
                "code: Q101",
                "semester: Test",
                "active: true",
                "",
            )
        ),
        encoding="utf-8",
    )
    bundled_source = _weekly_source(
        ("bundled", "Bundled question", "Bundled content."),
    )
    (class_dir / "week-1-questions.md").write_bytes(bundled_source)

    course_dir = data_dir / slug
    course_dir.mkdir()
    db_path = course_dir / "popping.db"

    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(config, "CLASSES_DIR", str(classes_dir))
    monkeypatch.setattr(config, "CONFIG_DIR", str(classes_dir))
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(
        app_module.app.config, "SECRET_KEY", "canonical-question-test-key"
    )
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
        ("instructor", "Question Instructor", "9999"),
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id)
           VALUES (?, ?, ?, ?, ?)""",
        ("Canonical Questions", "Q101", "Test", slug, instructor_id),
    ).lastrowid
    team_id = db.execute(
        "INSERT INTO teams (course_id, name) VALUES (?, 'Team 1')",
        (course_id,),
    ).lastrowid
    student_id = db.execute(
        """INSERT INTO students
           (course_id, student_id, name, pin, team_id)
           VALUES (?, 's1', 'Student One', '1111', ?)""",
        (course_id, team_id),
    ).lastrowid
    db.execute(
        """INSERT INTO course_state
           (course_id, phase, discussion_week, session_key,
            presentation_history)
           VALUES (?, 'setup', 1, ?, '[]')""",
        (course_id, SESSION_KEY),
    )
    db.commit()
    db.execute("BEGIN IMMEDIATE")
    database.migrate_schema_connection(db)
    db.commit()
    db.close()

    env = {
        "slug": slug,
        "class_dir": class_dir,
        "data_dir": data_dir,
        "db_path": db_path,
        "course_id": course_id,
        "instructor_id": instructor_id,
        "student_id": student_id,
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
    with client.session_transaction() as session:
        session["role"] = "instructor"
        session["instructor_id"] = env["instructor_id"]
        session["instructor_name"] = "Question Instructor"
        session["slug"] = env["slug"]
    return client


def _student_client(env):
    client = app_module.app.test_client()
    with client.session_transaction() as session:
        session["role"] = "student"
        session["student_id"] = "s1"
        session["name"] = "Student One"
        session["slug"] = env["slug"]
    return client


def _upload(client, payload, week=1, **fields):
    return client.post(
        "/api/upload_questions",
        data={
            "week": str(week),
            "expected_phase": "setup",
            "expected_session_key": str(SESSION_KEY),
            **fields,
            "file": (io.BytesIO(payload), f"week-{week}-questions.md"),
        },
        content_type="multipart/form-data",
    )


def _confirm_upload(client, payload, week=1):
    preview = _upload(client, payload, week)
    assert preview.status_code == 200, preview.get_data(as_text=True)
    preview_data = preview.get_json()
    confirmed = _upload(
        client,
        payload,
        week,
        confirm="true",
        preview_token=preview_data["preview_token"],
    )
    assert confirmed.status_code == 200, confirmed.get_data(as_text=True)
    return preview_data, confirmed.get_json()


def _question_rows(env, week=1):
    with _connect(env) as db:
        return [
            dict(row) for row in db.execute(
                """SELECT id, question_num, title, content, source_key
                   FROM questions
                   WHERE course_id = ? AND COALESCE(week_num, 1) = ?
                   ORDER BY CASE WHEN source_key LIKE 'appendix:%'
                                      THEN 1 ELSE 0 END,
                            question_num, id""",
                (env["course_id"], week),
            ).fetchall()
        ]


def test_preview_is_read_only_and_confirmed_override_feeds_both_catalogs(
        upload_env):
    payload = _weekly_source(
        ("alpha", "Alpha title", "Alpha body with $x^2$."),
        ("beta", "Beta title", "```python\nprint('beta')\n```"),
    )
    client = _instructor_client(upload_env)
    persistent_path = (
        upload_env["data_dir"] / upload_env["slug"] / "questions"
        / "week-1-questions.md"
    )

    preview = _upload(client, payload)

    assert preview.status_code == 200
    preview_data = preview.get_json()
    assert preview_data["requires_confirmation"] is True
    assert preview_data["week"] == 1
    assert preview_data["question_count"] == 2
    assert preview_data["questions"] == [
        {"id": "alpha", "title": "Alpha title"},
        {"id": "beta", "title": "Beta title"},
    ]
    assert preview_data["preview_token"]
    assert not persistent_path.exists()
    assert _question_rows(upload_env) == []

    confirmed = _upload(
        client,
        payload,
        confirm="true",
        preview_token=preview_data["preview_token"],
    )

    assert confirmed.status_code == 200
    confirmed_data = confirmed.get_json()
    assert confirmed_data["success"] is True
    assert confirmed_data["week"] == 1
    assert confirmed_data["question_count"] == 2
    assert confirmed_data["questions"] == preview_data["questions"]
    assert persistent_path.read_bytes() == payload

    discussion = client.get("/api/discussion_questions").get_json()["questions"]
    assert [
        (item["title"], item["content"])
        for item in discussion
    ] == [
        ("Alpha title", "Alpha body with $x^2$."),
        ("Beta title", "```python\nprint('beta')\n```"),
    ]
    rows = _question_rows(upload_env)
    assert [
        (row["question_num"], row["title"], row["content"])
        for row in rows
    ] == [
        (1, "Alpha title", "Alpha body with $x^2$."),
        (2, "Beta title", "```python\nprint('beta')\n```"),
    ]
    assert all(row["source_key"] for row in rows)

    # A bundled-file edit cannot override the confirmed persistent source.
    (upload_env["class_dir"] / "week-1-questions.md").write_bytes(
        _weekly_source(("wrong", "Wrong source", "Wrong body."))
    )
    refreshed = client.get("/api/discussion_questions").get_json()["questions"]
    assert [item["title"] for item in refreshed] == [
        "Alpha title", "Beta title",
    ]


def test_reorder_and_edit_preserve_identity_visibility_and_saved_rating(
        upload_env):
    original = _weekly_source(
        ("alpha", "Alpha", "Original alpha."),
        ("beta", "Beta", "Original beta."),
    )
    client = _instructor_client(upload_env)
    _confirm_upload(client, original)
    original_rows = _question_rows(upload_env)
    ids_by_source = {
        row["source_key"]: row["id"] for row in original_rows
    }
    alpha_row = next(row for row in original_rows if row["title"] == "Alpha")
    discussion = client.get("/api/discussion_questions").get_json()["questions"]
    beta_key = next(item["key"] for item in discussion if item["title"] == "Beta")

    with _connect(upload_env) as db:
        db.execute(
            """INSERT INTO hidden_discussion_questions
               (course_id, week_num, question_key) VALUES (?, 1, ?)""",
            (upload_env["course_id"], beta_key),
        )
        db.execute(
            f"""INSERT INTO presentation_ratings
               (data_version, course_id, student_id, question_key, session_key, week_num,
                question_id, question_title, q1_developed, q2_easy)
               VALUES ('{app_module.APP_VERSION}', ?, ?, 'historic-presentation', ?, 1, ?, 'Alpha', 4, 5)""",
            (
                upload_env["course_id"], upload_env["student_id"],
                SESSION_KEY, alpha_row["id"],
            ),
        )
        db.commit()

    revised = _weekly_source(
        ("beta", "Beta revised", "Revised beta."),
        ("alpha", "Alpha revised", "Revised alpha."),
    )
    _, confirmed = _confirm_upload(client, revised)

    assert confirmed["questions"] == [
        {"id": "beta", "title": "Beta revised"},
        {"id": "alpha", "title": "Alpha revised"},
    ]
    revised_rows = _question_rows(upload_env)
    assert {
        row["source_key"]: row["id"] for row in revised_rows
    } == ids_by_source
    assert [row["title"] for row in revised_rows] == [
        "Beta revised", "Alpha revised",
    ]
    with _connect(upload_env) as db:
        rating = db.execute(
            """SELECT question_id, question_title
               FROM presentation_ratings
               WHERE question_key = 'historic-presentation'"""
        ).fetchone()
    assert dict(rating) == {
        "question_id": alpha_row["id"],
        "question_title": "Alpha",
    }
    catalog = client.get("/api/discussion_questions").get_json()["questions"]
    beta = next(item for item in catalog if item["title"] == "Beta revised")
    assert beta["key"] == beta_key
    assert beta["hidden"] is True


def test_appendix_items_are_the_same_in_discussion_and_presentation_rows(
        upload_env):
    payload = _weekly_source(
        ("alpha", "Alpha", "Alpha body."),
        ("beta", "Beta", "Beta body."),
    )
    client = _instructor_client(upload_env)
    _confirm_upload(client, payload)

    for title, content in (("Extra one", "First extra."),
                           ("Extra two", "Second extra.")):
        response = client.post(
            "/api/questions",
            json={
                "title": title,
                "content": content,
                "week": 1,
                "expected_phase": "setup",
                "expected_session_key": SESSION_KEY,
            },
        )
        assert response.status_code == 200

    discussion = client.get("/api/discussion_questions").get_json()["questions"]
    rows = _question_rows(upload_env)

    assert [
        (item["title"], item["content"])
        for item in discussion
    ] == [
        (row["title"], row["content"])
        for row in rows
    ] == [
        ("Alpha", "Alpha body."),
        ("Beta", "Beta body."),
        ("A1: Extra one", "First extra."),
        ("A2: Extra two", "Second extra."),
    ]


def test_invalid_token_and_invalid_source_leave_file_and_database_unchanged(
        upload_env):
    original = _weekly_source(("alpha", "Alpha", "Alpha body."))
    client = _instructor_client(upload_env)
    _confirm_upload(client, original)
    persistent_path = (
        upload_env["data_dir"] / upload_env["slug"] / "questions"
        / "week-1-questions.md"
    )
    rows_before = _question_rows(upload_env)
    with _connect(upload_env) as db:
        state_before = dict(db.execute(
            """SELECT state_version, discussion_questions_version
               FROM course_state WHERE course_id = ?""",
            (upload_env["course_id"],),
        ).fetchone())

    changed = _weekly_source(("beta", "Beta", "Beta body."))
    wrong_token = _upload(
        client,
        changed,
        confirm="true",
        preview_token="not-the-preview-token",
    )
    assert wrong_token.status_code in (400, 409)

    duplicate_ids = _weekly_source(
        ("same", "First", "First body."),
        ("same", "Second", "Second body."),
    )
    invalid = _upload(client, duplicate_ids)
    assert invalid.status_code in (400, 422)
    assert invalid.is_json

    assert persistent_path.read_bytes() == original
    assert _question_rows(upload_env) == rows_before
    with _connect(upload_env) as db:
        state_after = dict(db.execute(
            """SELECT state_version, discussion_questions_version
               FROM course_state WHERE course_id = ?""",
            (upload_env["course_id"],),
        ).fetchone())
    assert state_after == state_before


def test_upload_requires_instructor_setup_and_is_disabled_for_demo(
        upload_env, monkeypatch):
    payload = _weekly_source(("alpha", "Alpha", "Alpha body."))

    anonymous = app_module.app.test_client()
    assert _upload(anonymous, payload).status_code == 401
    assert _upload(_student_client(upload_env), payload).status_code == 401

    instructor = _instructor_client(upload_env)
    with _connect(upload_env) as db:
        db.execute(
            "UPDATE course_state SET phase = 'discussion' WHERE course_id = ?",
            (upload_env["course_id"],),
        )
        db.commit()
    active = _upload(instructor, payload)
    assert active.status_code == 409

    with _connect(upload_env) as db:
        db.execute(
            "UPDATE course_state SET phase = 'setup' WHERE course_id = ?",
            (upload_env["course_id"],),
        )
        db.commit()
    monkeypatch.setattr(app_module, "is_demo_instance_slug", lambda _slug: True)
    with instructor.session_transaction() as session:
        session["is_demo"] = True
    demo = _upload(instructor, payload)
    assert demo.status_code == 403

    persistent_path = (
        upload_env["data_dir"] / upload_env["slug"] / "questions"
        / "week-1-questions.md"
    )
    assert not persistent_path.exists()


def test_export_contains_persistent_canonical_source_not_legacy_assets(
        upload_env):
    payload = _weekly_source(
        ("week-two-a", "Week two A", "Week two first body."),
        ("week-two-b", "Week two B", "Week two second body."),
    )
    legacy_dir = upload_env["class_dir"] / "week2"
    legacy_dir.mkdir()
    (legacy_dir / "index.md").write_text(
        "1. Wrong legacy question\n", encoding="utf-8"
    )
    (legacy_dir / "q01.html").write_text(
        "<p>Wrong legacy content</p>", encoding="utf-8"
    )
    client = _instructor_client(upload_env)
    _confirm_upload(client, payload, week=2)
    selected = client.post(
        "/api/set_discussion_week",
        json={
            "week": 2,
            "expected_phase": "setup",
            "expected_session_key": SESSION_KEY,
        },
    )
    assert selected.status_code == 200

    response = client.get(f"/export/{upload_env['slug']}")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        exported = archive.read("questions/week-2-questions.md")
    assert exported == payload
    assert "questions/week2/index.md" not in names
    assert "questions/week2/q01.html" not in names



def test_uploaded_question_starts_and_polls_with_exact_markdown(upload_env):
    body = (
        "Explain **why** the estimate changes.\n\n"
        "```python\nresult = model.fit(data)\n```"
    )
    payload = _weekly_source(
        ("uploaded-live", "Uploaded live question", body),
    )
    instructor = _instructor_client(upload_env)
    _confirm_upload(instructor, payload)

    with _connect(upload_env) as db:
        question = db.execute(
            """SELECT id, title, content, source_key FROM questions
               WHERE course_id = ?
                 AND source_key = 'week-1-q-uploaded-live'""",
            (upload_env["course_id"],),
        ).fetchone()
        team_id = db.execute(
            "SELECT team_id FROM students WHERE id = ?",
            (upload_env["student_id"],),
        ).fetchone()[0]
        db.execute(
            "UPDATE course_state SET phase = 'competition' WHERE course_id = ?",
            (upload_env["course_id"],),
        )
        db.commit()
    assert dict(question) == {
        "id": question["id"],
        "title": "Uploaded live question",
        "content": body,
        "source_key": "week-1-q-uploaded-live",
    }

    started = instructor.post(
        "/api/start_presentation",
        json={
            "expected_phase": "competition",
            "expected_session_key": SESSION_KEY,
            "team_id": team_id,
            "question_id": question["id"],
            "time_cap": 300,
        },
    )
    assert started.status_code == 200, started.get_json()

    active = _student_client(upload_env).get(
        "/api/poll"
    ).get_json()["state"]["active_question"]
    assert active["id"] == question["id"]
    assert active["title"] == "Uploaded live question"
    assert active["content"] == body
    assert active["source_key"] == "week-1-q-uploaded-live"
    assert "html_content" not in active
