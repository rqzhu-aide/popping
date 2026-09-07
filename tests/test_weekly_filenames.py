"""Weekly filename transitions preserve effective content and question identity."""

import io
import zipfile

import app as app_module
from tests.test_canonical_question_upload import (
    SESSION_KEY,
    _confirm_upload,
    _instructor_client,
    _question_rows,
    _upload,
    _weekly_source,
    upload_env,
)


def _apply_week(client, week=1):
    response = client.post("/api/set_discussion_week", json={
        "week": week, "expected_phase": "setup",
        "expected_session_key": SESSION_KEY,
    })
    assert response.status_code == 200


def test_padded_week_two_can_be_selected_for_both_question_phases(upload_env):
    payload = _weekly_source(("two", "Week two", "Discuss $C_p$."))
    path = upload_env["class_dir"] / "week-02-questions.md"
    path.write_bytes(payload)
    client = _instructor_client(upload_env)

    _apply_week(client, 2)
    discussion = client.get("/api/discussion_questions").get_json()["questions"]
    assert [(q["title"], q["content"]) for q in discussion] == [
        ("Week two", "Discuss $C_p$.")
    ]
    rows = _question_rows(upload_env, week=2)
    assert [(q["title"], q["source_key"]) for q in rows] == [
        ("Week two", "week-2-q-two")
    ]


def test_renaming_legacy_bundled_file_preserves_question_identity(upload_env):
    padded = upload_env["class_dir"] / "week-01-questions.md"
    legacy = padded.rename(upload_env["class_dir"] / "week-1-questions.md")
    client = _instructor_client(upload_env)
    _apply_week(client)
    assert client.get("/api/discussion_questions").status_code == 200
    before = _question_rows(upload_env)
    assert before

    legacy.rename(padded)
    _apply_week(client)
    assert client.get("/api/discussion_questions").status_code == 200
    assert _question_rows(upload_env) == before


def test_new_upload_replaces_effective_legacy_file_without_erasing_it(upload_env):
    folder = upload_env["data_dir"] / upload_env["slug"] / "questions"
    folder.mkdir()
    legacy = folder / "week-1-questions.md"
    original = _weekly_source(("same", "Original", "Original body."))
    legacy.write_bytes(original)
    client = _instructor_client(upload_env)
    _apply_week(client)
    assert client.get("/api/discussion_questions").status_code == 200
    original_id = _question_rows(upload_env)[0]["id"]

    revised = _weekly_source(("same", "Revised", "Revised body."))
    _confirm_upload(client, revised)
    assert (folder / "week-01-questions.md").read_bytes() == revised
    assert legacy.read_bytes() == original
    row = _question_rows(upload_env)[0]
    assert (row["id"], row["title"], row["source_key"]) == (
        original_id, "Revised", "week-1-q-same"
    )


def test_preview_rechecks_legacy_upload_before_publishing_padded_file(upload_env):
    folder = upload_env["data_dir"] / upload_env["slug"] / "questions"
    folder.mkdir()
    legacy = folder / "week-1-questions.md"
    legacy.write_bytes(_weekly_source(("old", "Old", "Old body.")))
    client = _instructor_client(upload_env)
    payload = _weekly_source(("new", "New", "New body."))
    preview = _upload(client, payload).get_json()
    intervening = _weekly_source(("other", "Other", "Other body."))
    legacy.write_bytes(intervening)

    response = _upload(
        client, payload, confirm="true", preview_token=preview["preview_token"]
    )
    assert response.status_code == 409
    assert legacy.read_bytes() == intervening
    assert not (folder / "week-01-questions.md").exists()


def test_export_uses_padded_names_for_existing_legacy_material(upload_env):
    folder = upload_env["data_dir"] / upload_env["slug"]
    questions = folder / "questions"
    appendix = folder / "appendix"
    questions.mkdir()
    appendix.mkdir()
    payload = _weekly_source(("old", "Old", "Old body."))
    extra = b"---\ntitle: 'A1: Legacy appendix'\n---\n\nExtra body.\n"
    (questions / "week-1-questions.md").write_bytes(payload)
    (appendix / "week-1-appendix.md").write_bytes(extra)

    response = _instructor_client(upload_env).get(f"/export/{upload_env['slug']}")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        assert archive.read("questions/week-01-questions.md") == payload
        assert archive.read("appendix/week-01-appendix.md") == extra
        assert "questions/week-1-questions.md" not in archive.namelist()
        assert "appendix/week-1-appendix.md" not in archive.namelist()


def test_appendix_update_carries_legacy_content_to_padded_file(upload_env):
    folder = upload_env["data_dir"] / upload_env["slug"] / "appendix"
    folder.mkdir()
    legacy = folder / "week-1-appendix.md"
    original = b"---\ntitle: 'A1: Existing'\n---\n\nKeep this body.\n"
    legacy.write_bytes(original)
    # A bundled seed must not displace the existing uploaded appendix.
    (upload_env["class_dir"] / "week-01-appendix.md").write_bytes(
        b"---\ntitle: 'A1: Bundled'\n---\n\nWrong source.\n"
    )
    response = _instructor_client(upload_env).post("/api/questions", json={
        "week": 1, "title": "New", "content": "New body.",
        "expected_phase": "setup", "expected_session_key": SESSION_KEY,
    })
    assert response.status_code == 200
    assert response.get_json()["appendix_id"] == "A2"
    assert legacy.read_bytes() == original
    padded = folder / "week-01-appendix.md"
    assert "Keep this body." in padded.read_text(encoding="utf-8")
    assert "A2: New" in padded.read_text(encoding="utf-8")
    assert app_module._appendix_path(upload_env["slug"], 1) == str(padded)
