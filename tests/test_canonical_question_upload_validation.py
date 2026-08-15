"""Boundary and rollback tests for canonical question uploads."""

from concurrent.futures import ThreadPoolExecutor
import io
import sqlite3
import zipfile
import threading

import pytest

import app as app_module
from tests.test_canonical_question_upload import (
    _instructor_client,
    _question_rows,
    _upload,
    _weekly_source,
    upload_env,
)


def _persistent_path(env, week=1):
    return (
        env["data_dir"] / env["slug"] / "questions"
        / f"week-{week}-questions.md"
    )


def test_upload_rejects_non_utf8_too_many_questions_and_oversize_file(
        upload_env):
    client = _instructor_client(upload_env)

    non_utf8 = _upload(client, b"\xff\xfe")
    assert non_utf8.status_code == 422
    assert "UTF-8" in non_utf8.get_json()["error"]

    too_many = _weekly_source(*(
        (f"q-{number}", f"Question {number}", f"Body {number}.")
        for number in range(app_module.MAX_WEEK_QUESTIONS + 1)
    ))
    excessive_count = _upload(client, too_many)
    assert excessive_count.status_code == 422
    assert "at most 100" in excessive_count.get_json()["error"]

    oversized = b"x" * (app_module.MAX_QUESTION_UPLOAD_BYTES + 1)
    excessive_size = _upload(client, oversized)
    assert excessive_size.status_code == 413

    assert not _persistent_path(upload_env).exists()
    assert _question_rows(upload_env) == []


def test_preview_token_is_bound_to_exact_file_and_week(upload_env):
    client = _instructor_client(upload_env)
    original = _weekly_source(("alpha", "Alpha", "Alpha body."))
    changed = _weekly_source(("beta", "Beta", "Beta body."))
    preview = _upload(client, original)
    assert preview.status_code == 200
    token = preview.get_json()["preview_token"]

    changed_file = _upload(
        client, changed, confirm="true", preview_token=token
    )
    changed_week = _upload(
        client, original, week=2, confirm="true", preview_token=token
    )

    assert changed_file.status_code == 409
    assert changed_week.status_code == 409
    assert not _persistent_path(upload_env, 1).exists()
    assert not _persistent_path(upload_env, 2).exists()
    assert _question_rows(upload_env, 1) == []
    assert _question_rows(upload_env, 2) == []


def test_sync_failure_restores_file_and_rolls_back_database(
        upload_env, monkeypatch):
    client = _instructor_client(upload_env)
    payload = _weekly_source(("alpha", "Alpha", "Alpha body."))
    preview = _upload(client, payload)
    token = preview.get_json()["preview_token"]

    def fail_sync(*_args, **_kwargs):
        raise RuntimeError("simulated question sync failure")

    monkeypatch.setattr(
        app_module, "sync_presentation_questions", fail_sync
    )

    response = _upload(
        client,
        payload,
        confirm="true",
        preview_token=token,
    )

    assert response.status_code == 500
    assert not _persistent_path(upload_env).exists()
    assert _question_rows(upload_env) == []


def test_upload_requires_markdown_extension(upload_env):
    client = _instructor_client(upload_env)
    payload = _weekly_source(("alpha", "Alpha", "Alpha body."))

    response = client.post(
        "/api/upload_questions",
        data={
            "week": "1",
            "file": (io.BytesIO(payload), "questions.txt"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 400
    assert ".md" in response.get_json()["error"]
    assert not _persistent_path(upload_env).exists()



def test_owned_sync_holds_write_lock_before_reading_week_file(
        upload_env, monkeypatch):
    original = _weekly_source(
        ("original", "Original question", "Original body."),
    )
    replacement = _weekly_source(
        ("replacement", "Replacement question", "Replacement body."),
    )
    initial_client = _instructor_client(upload_env)
    original_preview = _upload(initial_client, original).get_json()
    original_confirmed = _upload(
        initial_client,
        original,
        confirm="true",
        preview_token=original_preview["preview_token"],
    )
    assert original_confirmed.status_code == 200
    replacement_preview = _upload(initial_client, replacement).get_json()

    original_reader = app_module.read_presentation_question_index
    reader_entered = threading.Event()
    release_reader = threading.Event()
    upload_started = threading.Event()

    def paused_reader(slug, week_num):
        questions = original_reader(slug, week_num)
        if not reader_entered.is_set():
            reader_entered.set()
            if not release_reader.wait(timeout=10):
                raise AssertionError("test did not release the paused reader")
        return questions

    monkeypatch.setattr(
        app_module, "read_presentation_question_index", paused_reader
    )

    def run_stale_sync():
        with app_module.app.app_context():
            return app_module.sync_presentation_questions(
                upload_env["slug"], upload_env["course_id"], 1
            )

    def confirm_replacement():
        upload_started.set()
        return _upload(
            _instructor_client(upload_env),
            replacement,
            confirm="true",
            preview_token=replacement_preview["preview_token"],
        )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        stale_sync = executor.submit(run_stale_sync)
        assert reader_entered.wait(timeout=5)

        probe = sqlite3.connect(
            upload_env["db_path"], timeout=0, isolation_level=None
        )
        try:
            probe.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                probe.execute("BEGIN IMMEDIATE")
        finally:
            probe.rollback()
            probe.close()

        confirmed_upload = executor.submit(confirm_replacement)
        assert upload_started.wait(timeout=5)
        release_reader.set()
        assert stale_sync.result(timeout=10) == 1
        response = confirmed_upload.result(timeout=10)
    finally:
        release_reader.set()
        executor.shutdown(wait=True)

    assert response.status_code == 200, response.get_json()
    assert _persistent_path(upload_env).read_bytes() == replacement
    rows = _question_rows(upload_env)
    assert [
        (row["title"], row["content"], row["source_key"])
        for row in rows
    ] == [
        (
            "Replacement question",
            "Replacement body.",
            "week-1-q-replacement",
        )
    ]


def test_export_captures_question_bytes_inside_its_database_snapshot(
        upload_env, monkeypatch):
    original = _weekly_source(
        ("original", "Original question", "Original body."),
    )
    replacement = _weekly_source(
        ("replacement", "Replacement question", "Replacement body."),
    )
    initial_client = _instructor_client(upload_env)
    original_preview = _upload(initial_client, original).get_json()
    original_confirmed = _upload(
        initial_client,
        original,
        confirm="true",
        preview_token=original_preview["preview_token"],
    )
    assert original_confirmed.status_code == 200
    replacement_preview = _upload(initial_client, replacement).get_json()

    original_query_db = app_module.query_db
    export_paused = threading.Event()
    release_export = threading.Event()
    upload_started = threading.Event()
    thread_role = threading.local()

    def paused_query_db(slug, query, args=(), one=False):
        result = original_query_db(slug, query, args, one)
        if (
            getattr(thread_role, "name", None) == "export"
            and "AS students" in query
            and not export_paused.is_set()
        ):
            export_paused.set()
            if not release_export.wait(timeout=10):
                raise AssertionError("test did not release the paused export")
        return result

    monkeypatch.setattr(app_module, "query_db", paused_query_db)

    def run_export():
        thread_role.name = "export"
        return _instructor_client(upload_env).get(
            f"/export/{upload_env['slug']}"
        )

    def confirm_replacement():
        upload_started.set()
        return _upload(
            _instructor_client(upload_env),
            replacement,
            confirm="true",
            preview_token=replacement_preview["preview_token"],
        )

    executor = ThreadPoolExecutor(max_workers=2)
    try:
        pending_export = executor.submit(run_export)
        assert export_paused.wait(timeout=5)

        probe = sqlite3.connect(
            upload_env["db_path"], timeout=0, isolation_level=None
        )
        try:
            probe.execute("PRAGMA busy_timeout = 0")
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                probe.execute("BEGIN IMMEDIATE")
        finally:
            probe.rollback()
            probe.close()

        confirmed_upload = executor.submit(confirm_replacement)
        assert upload_started.wait(timeout=5)
        release_export.set()
        export_response = pending_export.result(timeout=15)
        upload_response = confirmed_upload.result(timeout=10)
    finally:
        release_export.set()
        executor.shutdown(wait=True)

    assert export_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(export_response.data)) as archive:
        exported_questions = archive.read("questions/week-1-questions.md")
    assert exported_questions == original

    assert upload_response.status_code == 200, upload_response.get_json()
    assert _persistent_path(upload_env).read_bytes() == replacement
    rows = _question_rows(upload_env)
    assert [
        (row["title"], row["content"], row["source_key"])
        for row in rows
    ] == [
        (
            "Replacement question",
            "Replacement body.",
            "week-1-q-replacement",
        )
    ]


def test_older_question_preview_cannot_overwrite_newer_confirmation(upload_env):
    first_payload = _weekly_source(
        ("first", "First preview", "First body."),
    )
    second_payload = _weekly_source(
        ("second", "Second preview", "Second body."),
    )
    first_preview = _upload(
        _instructor_client(upload_env), first_payload
    ).get_json()
    second_preview = _upload(
        _instructor_client(upload_env), second_payload
    ).get_json()

    newer = _upload(
        _instructor_client(upload_env),
        second_payload,
        confirm="true",
        preview_token=second_preview["preview_token"],
    )
    assert newer.status_code == 200

    stale = _upload(
        _instructor_client(upload_env),
        first_payload,
        confirm="true",
        preview_token=first_preview["preview_token"],
    )
    assert stale.status_code == 409
    assert "preview" in stale.get_json()["error"].lower()
    assert _persistent_path(upload_env).read_bytes() == second_payload
    assert [row["source_key"] for row in _question_rows(upload_env)] == [
        "week-1-q-second"
    ]
