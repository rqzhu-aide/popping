"""Focused tests for legacy peer-review preservation.

These tests use an in-memory database and do not depend on the large workflow
fixture. Legacy rows without trustworthy week evidence must remain explicitly
unknown-week so a normal lecture-week export cannot claim them.
"""

from pathlib import Path
import sqlite3
import sys
import threading


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import database  # noqa: E402


def _legacy_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        (PROJECT_ROOT / "popping.sql").read_text(encoding="utf-8")
    )
    instructor_id = db.execute(
        """INSERT INTO instructors (username, name, pin)
           VALUES ('teacher', 'Teacher', '1234')"""
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses
           (name, code, semester, slug, instructor_id, is_active)
           VALUES ('Legacy', 'LEGACY', 'Test', 'legacy', ?, 1)""",
        (instructor_id,),
    ).lastrowid
    student_ids = []
    for number in range(1, 5):
        student_ids.append(
            db.execute(
                """INSERT INTO students
                   (course_id, student_id, name, pin)
                   VALUES (?, ?, ?, '1111')""",
                (course_id, f"s{number}", f"Student {number}"),
            ).lastrowid
        )
    db.commit()
    return db, course_id, student_ids


def test_legacy_peer_reviews_without_week_stay_unknown_and_idempotent():
    db, course_id, students = _legacy_db()
    try:
        db.execute(
            """INSERT INTO peer_reviews
               (course_id, grader_id, recipient_id, criterion, score)
               VALUES (?, ?, ?, 'overall', 1)""",
            (course_id, students[0], students[1]),
        )
        db.execute(
            """INSERT INTO peer_reviews
               (course_id, grader_id, recipient_id, criterion, score)
               VALUES (?, ?, ?, 'overall', 0)""",
            (course_id, students[2], students[3]),
        )
        db.commit()

        database._ensure_schema_locked(db)
        database._ensure_schema_locked(db)
        rows = db.execute(
            """SELECT week_num, question_key, source_question_key,
                      grader_id, recipient_id
               FROM teammate_thumbs
               WHERE source_question_key = 'legacy'"""
        ).fetchall()

        assert len(rows) == 1
        assert rows[0]["week_num"] is None
        assert rows[0]["question_key"] == "legacy"
        assert rows[0]["grader_id"] == students[0]
        assert rows[0]["recipient_id"] == students[1]

        db.execute("DROP TABLE peer_reviews")
        database._ensure_schema_locked(db)
        assert db.execute(
            """SELECT COUNT(*) FROM teammate_thumbs
               WHERE source_question_key = 'legacy'"""
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_explicit_positive_legacy_week_is_retained_and_upgrades_unknown_row():
    db, course_id, students = _legacy_db()
    try:
        db.execute("ALTER TABLE peer_reviews ADD COLUMN week_num INTEGER")
        review_id = db.execute(
            """INSERT INTO peer_reviews
               (course_id, grader_id, recipient_id, criterion, score, week_num)
               VALUES (?, ?, ?, 'overall', 1, NULL)""",
            (course_id, students[0], students[1]),
        ).lastrowid
        db.execute(
            """INSERT INTO peer_reviews
               (course_id, grader_id, recipient_id, criterion, score, week_num)
               VALUES (?, ?, ?, 'overall', 1, 0)""",
            (course_id, students[2], students[3]),
        )
        db.commit()

        database._ensure_schema_locked(db)
        unknown = db.execute(
            """SELECT week_num FROM teammate_thumbs
               WHERE source_question_key = 'legacy' AND grader_id = ?""",
            (students[0],),
        ).fetchone()
        assert unknown["week_num"] is None

        db.execute(
            "UPDATE peer_reviews SET week_num = 4 WHERE id = ?",
            (review_id,),
        )
        database._ensure_schema_locked(db)
        rows = db.execute(
            """SELECT grader_id, week_num FROM teammate_thumbs
               WHERE source_question_key = 'legacy'
               ORDER BY grader_id"""
        ).fetchall()

        assert [(row["grader_id"], row["week_num"]) for row in rows] == [
            (students[0], 4),
            (students[2], None),
        ]
    finally:
        db.close()


def test_schema_checks_for_different_courses_do_not_block_each_other(
        monkeypatch):
    slow_slug = "slow_demo"
    fast_slug = "real_class"
    slow_started = threading.Event()
    release_slow = threading.Event()
    fast_finished = threading.Event()

    class FakeConnection:
        def __init__(self, slug):
            self.slug = slug


    connections = {
        slow_slug: FakeConnection(slow_slug),
        fast_slug: FakeConnection(fast_slug),
    }

    def fake_inspection(connection, allow_unversioned=True):
        assert connection.slug in connections
        assert allow_unversioned is True
        return database.SCHEMA_VERSION

    def fake_validation(connection, repair_indexes=False):
        assert repair_indexes is False
        if connection.slug == slow_slug:
            slow_started.set()
            assert release_slow.wait(timeout=2)
        else:
            fast_finished.set()
        return database.SCHEMA_VERSION

    database.forget_schema(slow_slug)
    database.forget_schema(fast_slug)
    monkeypatch.setattr(database, "get_db", connections.get)
    monkeypatch.setattr(database, "inspect_schema_version", fake_inspection)
    monkeypatch.setattr(
        database, "validate_current_schema", fake_validation
    )

    slow_thread = threading.Thread(
        target=database.ensure_schema, args=(slow_slug,)
    )
    fast_thread = threading.Thread(
        target=database.ensure_schema, args=(fast_slug,)
    )
    slow_thread.start()
    assert slow_started.wait(timeout=1)
    fast_thread.start()
    try:
        assert fast_finished.wait(timeout=1)
    finally:
        release_slow.set()
        slow_thread.join(timeout=2)
        fast_thread.join(timeout=2)
        database.forget_schema(slow_slug)
        database.forget_schema(fast_slug)

    assert not slow_thread.is_alive()
    assert not fast_thread.is_alive()
