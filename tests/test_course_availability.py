"""Focused tests for course discovery and login availability checks."""

import builtins
from pathlib import Path
import sqlite3
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app as app_module  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402


@pytest.fixture
def catalog_env(tmp_path, monkeypatch):
    classes_dir = tmp_path / "classes"
    data_dir = tmp_path / "data"
    classes_dir.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(config, "CLASSES_DIR", str(classes_dir))
    monkeypatch.setattr(config, "CONFIG_DIR", str(classes_dir))
    monkeypatch.setattr(config, "DATA_DIR", str(data_dir))
    monkeypatch.setitem(app_module.app.config, "TESTING", True)
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "catalog-test-key")
    app_module._clear_course_availability_cache()
    yield {"classes_dir": classes_dir, "data_dir": data_dir}
    app_module._clear_course_availability_cache()


def _write_config(env, folder_slug, *, declared_slug=None, active=True):
    class_dir = env["classes_dir"] / folder_slug
    class_dir.mkdir(parents=True)
    declared_slug = declared_slug or folder_slug
    (class_dir / "course.yaml").write_text(
        "\n".join(
            (
                f"slug: {declared_slug}",
                f"name: Course {folder_slug}",
                "code: TEST 101",
                "semester: Test",
                f"active: {'true' if active else 'false'}",
                "",
            )
        ),
        encoding="utf-8",
    )


def _write_database(env, slug, *, course_active=1):
    db_dir = env["data_dir"] / slug
    db_dir.mkdir(parents=True)
    db_path = db_dir / "popping.db"
    db = sqlite3.connect(db_path)
    db.executescript(Path(config.DATABASE_SCHEMA).read_text(encoding="utf-8"))
    instructor_id = db.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ("teacher", "Test Teacher", "9999"),
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id, is_active)
           VALUES (?, ?, ?, ?, ?, ?)""",
        ("Test Course", "TEST 101", "Test", slug, instructor_id, course_active),
    ).lastrowid
    db.execute(
        "INSERT INTO course_state (course_id) VALUES (?)",
        (course_id,),
    )
    db.commit()
    db.close()
    database._schema_checked.discard(slug)
    return db_path


def test_ready_course_is_listed_and_both_login_pages_open(catalog_env):
    slug = "ready_course"
    _write_config(catalog_env, slug)
    _write_database(catalog_env, slug)

    availability = app_module._course_availability(slug)
    assert availability["status"] == "ready"
    assert availability["course"]["instructor_name"] == "Test Teacher"
    assert app_module._scan_courses()[0]["availability_status"] == "ready"

    client = app_module.app.test_client()
    index_html = client.get("/").get_data(as_text=True)
    assert f'href="/login/{slug}"' in index_html
    assert f'href="/instructor_login/{slug}"' in index_html
    assert client.get(f"/login/{slug}").status_code == 200
    assert client.get(f"/instructor_login/{slug}").status_code == 200


def test_missing_database_stays_visible_but_login_is_disabled(catalog_env):
    slug = "missing_database"
    _write_config(catalog_env, slug)

    availability = app_module._course_availability(slug)
    courses = app_module._scan_courses()
    assert availability["status"] == "missing"
    assert courses[0]["availability_status"] == "missing"

    client = app_module.app.test_client()
    index_html = client.get("/").get_data(as_text=True)
    assert "Setup required" in index_html
    assert index_html.count(" disabled") == 2
    assert f'href="/login/{slug}"' not in index_html
    assert client.get(f"/login/{slug}").status_code == 302
    assert client.get(f"/instructor_login/{slug}").status_code == 302


def test_yaml_slug_mismatch_is_visible_as_unavailable(catalog_env):
    slug = "folder_slug"
    _write_config(catalog_env, slug, declared_slug="different_slug")
    _write_database(catalog_env, slug)

    availability = app_module._course_availability(slug)
    courses = app_module._scan_courses()
    assert availability["status"] == "invalid"
    assert courses[0]["slug"] == slug
    assert courses[0]["availability_status"] == "invalid"

    client = app_module.app.test_client()
    index_html = client.get("/").get_data(as_text=True)
    assert "Unavailable" in index_html
    assert f'href="/login/{slug}"' not in index_html
    assert client.get(f"/login/{slug}").status_code == 302


def test_inactive_config_is_hidden_and_direct_login_is_rejected(catalog_env):
    slug = "inactive_course"
    _write_config(catalog_env, slug, active=False)
    _write_database(catalog_env, slug)

    availability = app_module._course_availability(slug)
    assert availability["status"] == "inactive"
    assert app_module._scan_courses() == []
    assert app_module.app.test_client().get(f"/login/{slug}").status_code == 302


@pytest.mark.parametrize("database_kind", ("inactive_row", "corrupt"))
def test_unverified_database_is_invalid_and_cannot_be_opened(
        catalog_env, database_kind):
    slug = f"invalid_{database_kind}"
    _write_config(catalog_env, slug)
    if database_kind == "inactive_row":
        _write_database(catalog_env, slug, course_active=0)
    else:
        db_dir = catalog_env["data_dir"] / slug
        db_dir.mkdir()
        (db_dir / "popping.db").write_bytes(b"not a sqlite database")

    availability = app_module._course_availability(slug)
    assert availability["status"] == "invalid"
    assert app_module.app.test_client().get(f"/login/{slug}").status_code == 302


def test_unsafe_slug_is_rejected_before_path_lookup(catalog_env):
    assert app_module._course_availability("../outside")["status"] == "invalid"


def test_multiple_matching_active_course_rows_are_rejected(catalog_env):
    slug = "duplicate_course"
    _write_config(catalog_env, slug)
    db_dir = catalog_env["data_dir"] / slug
    db_dir.mkdir()
    db = sqlite3.connect(db_dir / "popping.db")
    db.executescript(
        """
        CREATE TABLE instructors (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            instructor_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL
        );
        INSERT INTO instructors (id, name) VALUES (1, 'Teacher');
        INSERT INTO courses
            (id, name, slug, instructor_id, is_active)
            VALUES (1, 'First', 'duplicate_course', 1, 1);
        INSERT INTO courses
            (id, name, slug, instructor_id, is_active)
            VALUES (2, 'Second', 'duplicate_course', 1, 1);
        """
    )
    db.commit()
    db.close()

    assert app_module._course_availability(slug)["status"] == "invalid"
    assert app_module.app.test_client().get(f"/login/{slug}").status_code == 302


def test_database_with_an_extra_course_row_is_rejected(catalog_env):
    slug = "extra_course_row"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    db = sqlite3.connect(db_path)
    instructor_id = db.execute("SELECT id FROM instructors").fetchone()[0]
    db.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id, is_active)
           VALUES ('Other', 'OTHER', 'Test', 'other_slug', ?, 1)""",
        (instructor_id,),
    )
    db.commit()
    db.close()

    assert app_module._course_availability(slug)["status"] == "invalid"


def test_structurally_incomplete_database_is_rejected(catalog_env):
    slug = "incomplete_schema"
    _write_config(catalog_env, slug)
    db_dir = catalog_env["data_dir"] / slug
    db_dir.mkdir()
    db = sqlite3.connect(db_dir / "popping.db")
    db.executescript(
        """
        CREATE TABLE instructors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE courses (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT NOT NULL,
            instructor_id INTEGER NOT NULL,
            is_active INTEGER NOT NULL
        );
        CREATE TABLE course_state (course_id INTEGER PRIMARY KEY);
        INSERT INTO instructors (id, name) VALUES (1, 'Teacher');
        INSERT INTO courses
            (id, name, slug, instructor_id, is_active)
            VALUES (1, 'Course', 'incomplete_schema', 1, 1);
        INSERT INTO course_state (course_id) VALUES (1);
        """
    )
    db.commit()
    db.close()

    assert app_module._course_availability(slug)["status"] == "invalid"


def test_database_missing_a_required_column_is_rejected(catalog_env):
    slug = "missing_schema_column"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    with sqlite3.connect(db_path) as db:
        db.execute("ALTER TABLE students DROP COLUMN pin")

    assert app_module._course_availability(slug)["status"] == "invalid"


def test_legacy_database_is_available_then_migrated_on_login(catalog_env):
    slug = "legacy_course"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    with sqlite3.connect(db_path) as db:
        db.execute("DROP TABLE teammate_thumbs")
        db.execute("DROP TABLE presentation_ratings")
        db.execute("ALTER TABLE students DROP COLUMN is_active")
    database._schema_checked.discard(slug)
    app_module._clear_course_availability_cache(slug)

    assert app_module._course_availability(slug)["status"] == "ready"
    assert app_module.app.test_client().get(f"/login/{slug}").status_code == 200

    with sqlite3.connect(db_path) as db:
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        student_columns = {
            row[1] for row in db.execute("PRAGMA table_info(students)")
        }
        thumb_columns = {
            row[1] for row in db.execute("PRAGMA table_info(teammate_thumbs)")
        }
        rating_columns = {
            row[1] for row in db.execute(
                "PRAGMA table_info(presentation_ratings)"
            )
        }
    assert {"teammate_thumbs", "presentation_ratings"}.issubset(tables)
    assert "is_active" in student_columns
    assert "week_num" in thumb_columns
    assert "week_num" in rating_columns


def test_activity_week_migration_backfills_only_inferable_rows(catalog_env):
    slug = "legacy_activity_weeks"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    with sqlite3.connect(db_path) as db:
        course_id = db.execute("SELECT id FROM courses").fetchone()[0]
        team_id = db.execute(
            "INSERT INTO teams (course_id, name) VALUES (?, 'Team 1')",
            (course_id,),
        ).lastrowid
        student_1 = db.execute(
            """INSERT INTO students
               (course_id, student_id, name, pin, team_id)
               VALUES (?, 's1', 'Student 1', '1111', ?)""",
            (course_id, team_id),
        ).lastrowid
        student_2 = db.execute(
            """INSERT INTO students
               (course_id, student_id, name, pin, team_id)
               VALUES (?, 's2', 'Student 2', '2222', ?)""",
            (course_id, team_id),
        ).lastrowid
        question_id = db.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, week_num,
                source_key)
               VALUES (?, 1, 'Week 2', 'Week 2', 2, 'presentation:2:1')""",
            (course_id,),
        ).lastrowid
        db.execute("DROP INDEX idx_thumbs_export_week")
        db.execute("DROP INDEX idx_ratings_export_week")
        db.execute("ALTER TABLE teammate_thumbs DROP COLUMN week_num")
        db.execute("ALTER TABLE presentation_ratings DROP COLUMN week_num")
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, question_key, source_question_key,
                grader_id, recipient_id)
               VALUES (?, 1, 'inferable-thumb', 'week-2-shared-question',
                       ?, ?)""",
            (course_id, student_1, student_2),
        )
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, question_key, source_question_key,
                grader_id, recipient_id)
               VALUES (?, 1, 'unknown-thumb', 'manual:legacy', ?, ?)""",
            (course_id, student_1, student_2),
        )
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key, question_id,
                q1_developed, q2_easy)
               VALUES (?, ?, 'inferable-rating', 1, ?, 4, 5)""",
            (course_id, student_1, question_id),
        )
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, session_key, question_id,
                q1_developed, q2_easy)
               VALUES (?, ?, 'unknown-rating', 1, NULL, 3, 3)""",
            (course_id, student_1),
        )
        db.commit()
    database._schema_checked.discard(slug)
    app_module._clear_course_availability_cache(slug)

    assert app_module.app.test_client().get(f"/login/{slug}").status_code == 200

    with sqlite3.connect(db_path) as db:
        thumb_weeks = dict(db.execute(
            "SELECT question_key, week_num FROM teammate_thumbs"
        ))
        rating_weeks = dict(db.execute(
            "SELECT question_key, week_num FROM presentation_ratings"
        ))
    assert thumb_weeks == {
        "inferable-thumb": 2,
        "unknown-thumb": None,
    }
    assert rating_weeks == {
        "inferable-rating": 2,
        "unknown-rating": None,
    }


def test_availability_validation_is_cached_across_login_burst(
        catalog_env, monkeypatch):
    slug = "cached_course"
    _write_config(catalog_env, slug)
    _write_database(catalog_env, slug)
    app_module._clear_course_availability_cache()

    inspected = 0
    original = app_module._inspect_course_availability

    def counted_inspection(requested_slug):
        nonlocal inspected
        inspected += 1
        return original(requested_slug)

    monkeypatch.setattr(
        app_module, "_inspect_course_availability", counted_inspection
    )
    client = app_module.app.test_client()
    for _ in range(5):
        assert client.get(f"/login/{slug}").status_code == 200

    assert inspected == 1


@pytest.mark.parametrize("failure_kind", ("database", "filesystem"))
def test_transient_course_unavailability_preserves_session_and_recovers(
        catalog_env, monkeypatch, failure_kind):
    slug = f"temporary_{failure_kind}_failure"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    with sqlite3.connect(db_path) as db:
        instructor_id = db.execute(
            "SELECT instructor_id FROM courses WHERE slug = ?", (slug,)
        ).fetchone()[0]

    client = app_module.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["slug"] = slug
        browser_session["role"] = "instructor"
        browser_session["instructor_id"] = instructor_id

    assert client.get("/api/poll").status_code == 200

    outage = {"active": True}
    if failure_kind == "database":
        original_connect = sqlite3.connect

        def flaky_connect(database_path, *args, **kwargs):
            if outage["active"] and kwargs.get("uri") is True:
                raise sqlite3.OperationalError("temporary disk I/O failure")
            return original_connect(database_path, *args, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", flaky_connect)
    else:
        original_open = builtins.open
        config_path = catalog_env["classes_dir"] / slug / "course.yaml"

        def flaky_open(path, *args, **kwargs):
            if outage["active"] and str(path) == str(config_path):
                raise OSError("temporary filesystem failure")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", flaky_open)

    app_module._clear_course_availability_cache(slug)
    assert app_module._course_availability(slug)["status"] == "unavailable"

    unavailable = client.get("/api/poll")
    assert unavailable.status_code == 503
    assert unavailable.get_json() == {
        "error": "Course data is temporarily unavailable. Please try again."
    }
    assert unavailable.headers["Retry-After"] == "5"
    with client.session_transaction() as browser_session:
        assert browser_session["slug"] == slug
        assert browser_session["role"] == "instructor"
        assert browser_session["instructor_id"] == instructor_id

    outage["active"] = False
    app_module._clear_course_availability_cache(slug)
    assert client.get("/api/poll").status_code == 200
    with client.session_transaction() as browser_session:
        assert browser_session["slug"] == slug
        assert browser_session["role"] == "instructor"
        assert browser_session["instructor_id"] == instructor_id


def test_temporary_database_disappearance_preserves_session_and_recovers(
        catalog_env, monkeypatch):
    slug = "temporary_missing_database"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    db = sqlite3.connect(db_path)
    try:
        instructor_id = db.execute(
            "SELECT instructor_id FROM courses WHERE slug = ?", (slug,)
        ).fetchone()[0]
    finally:
        db.close()

    clock = {"now": 100.0}
    monkeypatch.setattr(
        app_module.time, "monotonic", lambda: clock["now"]
    )
    client = app_module.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["slug"] = slug
        browser_session["role"] = "instructor"
        browser_session["instructor_id"] = instructor_id

    assert client.get("/api/poll").status_code == 200

    offline_path = db_path.with_name("popping.db.temporarily-offline")
    db_path.replace(offline_path)
    try:
        # Expire the prior ready result so the missing file is observed.
        clock["now"] += app_module.COURSE_AVAILABILITY_TTL + 1
        unavailable = client.get("/api/poll")
        assert unavailable.status_code == 503
        assert unavailable.get_json() == {
            "error": "Course data is temporarily unavailable. Please try again."
        }
        assert unavailable.headers["Retry-After"] == "5"
        with client.session_transaction() as browser_session:
            assert browser_session["slug"] == slug
            assert browser_session["role"] == "instructor"
            assert browser_session["instructor_id"] == instructor_id

        offline_path.replace(db_path)

        # Missing results use the short outage TTL, then recover without
        # clearing either the browser session or the availability cache.
        clock["now"] += app_module.COURSE_UNAVAILABLE_TTL - 1
        assert client.get("/api/poll").status_code == 503
        clock["now"] += 2
        assert client.get("/api/poll").status_code == 200
        with client.session_transaction() as browser_session:
            assert browser_session["slug"] == slug
            assert browser_session["role"] == "instructor"
            assert browser_session["instructor_id"] == instructor_id
    finally:
        if offline_path.exists():
            offline_path.replace(db_path)


def test_existing_instructor_session_is_revoked_when_course_is_deactivated(
        catalog_env):
    slug = "deactivated_course"
    _write_config(catalog_env, slug)
    db_path = _write_database(catalog_env, slug)
    with sqlite3.connect(db_path) as db:
        instructor_id = db.execute(
            "SELECT instructor_id FROM courses WHERE slug = ?", (slug,)
        ).fetchone()[0]

    client = app_module.app.test_client()
    with client.session_transaction() as browser_session:
        browser_session["slug"] = slug
        browser_session["role"] = "instructor"
        browser_session["instructor_id"] = instructor_id

    assert client.get("/api/poll").status_code == 200
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE courses SET is_active = 0 WHERE slug = ?", (slug,))
        db.commit()

    assert client.get("/api/poll").status_code == 401
    with client.session_transaction() as browser_session:
        assert not browser_session


def test_course_availability_cache_is_bounded(catalog_env):
    slug = "bounded_course"
    _write_config(catalog_env, slug)
    _write_database(catalog_env, slug)
    cache = app_module._course_availability_cache
    for index in range(app_module.COURSE_AVAILABILITY_CACHE_LIMIT):
        cache[("classes", "data", f"bogus-{index}")] = {
            "checked_at": 0,
            "result": {"status": "missing"},
        }

    assert app_module._course_availability(slug)["status"] == "ready"
    assert len(cache) <= app_module.COURSE_AVAILABILITY_CACHE_LIMIT


def test_poll_duration_cache_is_bounded(catalog_env):
    slug = "bounded_poll"
    _write_config(catalog_env, slug)
    cache = app_module._poll_duration_cache
    cache.clear()
    try:
        for index in range(app_module.POLL_DURATION_CACHE_LIMIT):
            cache[f"bogus-{index}"] = (0, app_module.POLL_DURATION)

        assert app_module.get_poll_duration(slug) == app_module.POLL_DURATION
        assert len(cache) <= app_module.POLL_DURATION_CACHE_LIMIT
    finally:
        cache.clear()
