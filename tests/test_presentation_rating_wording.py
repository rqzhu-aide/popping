"""Per-course presentation wording keeps the existing two-score contract."""

from html import unescape
import io
import json
from pathlib import Path
import zipfile

from openpyxl import load_workbook
import pytest
import yaml

from test_versioning_exports import (
    _connect, _instructor_client, _set_state, _student_client,
    _workbook_rows, app_module, config, versioned_course_env,
)


def _configure(env, source_slug):
    source = Path(__file__).resolve().parents[1] / "classes" / source_slug / "course.yaml"
    settings = yaml.safe_load(source.read_text(encoding="utf-8"))
    path = Path(config.CLASSES_DIR) / env["slug"] / "course.yaml"
    course = yaml.safe_load(path.read_text(encoding="utf-8"))
    if "presentation_rating_questions" in settings:
        course["presentation_rating_questions"] = settings["presentation_rating_questions"]
    else:
        course.pop("presentation_rating_questions", None)
    path.write_text(yaml.safe_dump(course, allow_unicode=True), encoding="utf-8")
    app_module._clear_course_availability_cache(env["slug"])
    return app_module._presentation_rating_questions(settings)


@pytest.mark.parametrize("source_slug", ["432fall2026", "546fall2026"])
@pytest.mark.parametrize("week", [1, 7])
def test_course_wording_applies_to_every_week(versioned_course_env, source_slug, week):
    env = versioned_course_env
    questions = _configure(env, source_slug)
    _set_state(env, discussion_week=week, phase="competition")
    response = _student_client(env, "s1").get("/dashboard")
    assert response.status_code == 200
    html = unescape(response.get_data(as_text=True))
    for index, question in enumerate(questions, 1):
        assert f'id="presentation-rating-question-{index}">{question}</p>' in html
    assert html.count('data-question="q1"') == 1
    assert html.count('data-question="q2"') == 1
    if source_slug == "546fall2026":
        assert questions == [
            "Did the team explain the paper’s research problem, main method, and contribution accurately and clearly?",
            "Did the team conduct a convincing investigation, using numerical experiments or other appropriate approaches, to evaluate the method?",
        ]
    else:
        assert questions == list(app_module.DEFAULT_PRESENTATION_RATING_QUESTIONS)


def test_export_keeps_saved_scores_and_identifies_only_current_wording(versioned_course_env):
    env = versioned_course_env
    questions = _configure(env, "546fall2026")
    with _connect(env) as db:
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, week_num, q1_developed,
                q2_easy, data_version)
               VALUES (?, ?, 'saved-old-rating', 1, 4, 2, '1.2.5')""",
            [env["course_id"], env["students"]["s3"]],
        )
        db.commit()
        before = tuple(db.execute("SELECT * FROM presentation_ratings").fetchone())
        schema_before = [tuple(row) for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )]
    response = _instructor_client(env).get(f"/export/{env['slug']}?week=1")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        metadata = json.loads(archive.read("manifest.json"))["current_presentation_evaluation"]
        assert metadata["questions"] == questions
        assert metadata["score_columns"] == ["developed_1to5", "easy_1to5"]
        assert "at export time" in metadata["scope"]
        workbook = load_workbook(io.BytesIO(archive.read("course_data.xlsx")))
        try:
            row = _workbook_rows(workbook, "Presentation Ratings")[0]
            assert (row["developed_1to5"], row["easy_1to5"]) == (4, 2)
            assert row["data_version"] == "v1.2.5"
        finally:
            workbook.close()
    with _connect(env) as db:
        assert tuple(db.execute("SELECT * FROM presentation_ratings").fetchone()) == before
        assert [tuple(row) for row in db.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )] == schema_before
