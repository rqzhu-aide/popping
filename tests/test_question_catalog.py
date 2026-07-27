"""Focused tests for filesystem question catalog validation."""

from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from question_catalog import (  # noqa: E402
    discover_catalog_weeks,
    validate_discussion_week,
    validate_presentation_week,
    validate_question_catalog,
)


def _codes(status):
    return {issue.code for issue in status.issues}


def _write_valid_week(course_dir, week=1):
    (course_dir / f"week-{week}-questions.md").write_text(
        """---
id: bias_variance
title: Bias Variance
---

Explain the tradeoff.

---
id: cross-validation
title: Cross Validation
---

Compare common validation schemes.
""",
        encoding="utf-8",
    )
    presentation_dir = course_dir / f"week{week}"
    presentation_dir.mkdir()
    (presentation_dir / "index.md").write_text(
        "# Week Questions\n\n1. First question\n2. Second question\n",
        encoding="utf-8",
    )
    (presentation_dir / "q01.html").write_text(
        "<p>First question</p>", encoding="utf-8"
    )
    (presentation_dir / "q02.html").write_text(
        "<p>Second question</p>", encoding="utf-8"
    )


def test_valid_catalog_reports_each_section_ready(tmp_path):
    _write_valid_week(tmp_path)

    report = validate_question_catalog(tmp_path)

    assert report.ready is True
    assert discover_catalog_weeks(tmp_path) == (1,)
    assert report.get_week(1).discussion.ready is True
    assert report.get_week(1).discussion.count == 2
    assert report.get_week(1).presentation.ready is True
    assert report.get_week(1).presentation.count == 2
    assert report.as_dict()["weeks"][0]["ready"] is True


def test_catalog_reports_discussion_and_presentation_separately(tmp_path):
    (tmp_path / "week-2-questions.md").write_text(
        "---\nid: available-discussion\n"
        "title: Available discussion\n---\n\nDiscuss this.\n",
        encoding="utf-8",
    )

    report = validate_question_catalog(tmp_path, weeks=[2])
    week = report.get_week(2)

    assert week.discussion.ready is True
    assert week.presentation.ready is False
    assert _codes(week.presentation) == {"presentation_index_missing"}
    assert report.ready is False


def test_discussion_rejects_invalid_and_duplicate_ids_and_titles(tmp_path):
    (tmp_path / "week-1-questions.md").write_text(
        """---
id: Question One
title: Repeated title
---

First body.

---
id: duplicate
title: Repeated title
---

Second body.

---
id: DUPLICATE
title: 17
---

Third body.
""",
        encoding="utf-8",
    )

    status = validate_discussion_week(tmp_path, 1)

    assert status.ready is False
    assert status.count == 3
    assert {
        "discussion_id_invalid",
        "discussion_id_duplicate",
        "discussion_title_duplicate",
        "discussion_title_invalid",
    } <= _codes(status)


def test_discussion_requires_stable_question_ids(tmp_path):
    (tmp_path / "week-1-questions.md").write_text(
        "---\ntitle: Missing ID\n---\n\nDiscuss this.\n",
        encoding="utf-8",
    )

    status = validate_discussion_week(tmp_path, 1)

    assert status.ready is False
    assert "discussion_id_missing" in _codes(status)


@pytest.mark.parametrize(
    ("contents", "expected_code"),
    [
        (b"", "discussion_file_empty"),
        (b"\xff\xfe", "discussion_file_encoding"),
        (
            b"---\ntitle: [broken\n---\n\nBody\n",
            "discussion_frontmatter_invalid",
        ),
        (
            b"---\ntitle: Empty body\n---\n",
            "discussion_body_empty",
        ),
    ],
)
def test_discussion_rejects_empty_non_utf8_and_malformed_files(
        tmp_path, contents, expected_code):
    (tmp_path / "week-1-questions.md").write_bytes(contents)

    status = validate_discussion_week(tmp_path, 1)

    assert status.ready is False
    assert expected_code in _codes(status)


def test_presentation_rejects_duplicate_numbers_bad_lines_and_missing_html(
        tmp_path):
    presentation_dir = tmp_path / "week3"
    presentation_dir.mkdir()
    (presentation_dir / "index.md").write_text(
        """# Week 3

1. First question
1. Duplicate number
this line is malformed
2. Missing HTML
""",
        encoding="utf-8",
    )
    (presentation_dir / "q01.html").write_text(
        "<p>First question</p>", encoding="utf-8"
    )

    status = validate_presentation_week(tmp_path, 3)

    assert status.ready is False
    assert status.count == 2
    assert {
        "presentation_number_duplicate",
        "presentation_index_line_invalid",
        "presentation_html_missing",
    } <= _codes(status)


def test_presentation_rejects_unindexed_question_html(tmp_path):
    _write_valid_week(tmp_path)
    orphan_path = tmp_path / "week1" / "q03.html"
    orphan_path.write_text("<p>Not in the index</p>", encoding="utf-8")

    status = validate_presentation_week(tmp_path, 1)

    assert status.ready is False
    assert status.count == 2
    assert _codes(status) == {"presentation_html_unindexed"}
    issue = status.issues[0]
    assert Path(issue.path) == orphan_path
    assert issue.message == "q03.html is not listed in index.md"


@pytest.mark.parametrize(
    ("index_bytes", "html_bytes", "expected_code"),
    [
        (b"", None, "presentation_index_empty"),
        (b"\xff", None, "presentation_index_encoding"),
        (b"1. Question\n", b"", "presentation_html_empty"),
        (b"1. Question\n", b"\xff", "presentation_html_encoding"),
    ],
)
def test_presentation_rejects_empty_and_non_utf8_files(
        tmp_path, index_bytes, html_bytes, expected_code):
    presentation_dir = tmp_path / "week1"
    presentation_dir.mkdir()
    (presentation_dir / "index.md").write_bytes(index_bytes)
    if html_bytes is not None:
        (presentation_dir / "q01.html").write_bytes(html_bytes)

    status = validate_presentation_week(tmp_path, 1)

    assert status.ready is False
    assert expected_code in _codes(status)


def test_catalog_accepts_utf8_bom_question_sources(tmp_path):
    (tmp_path / "week-1-questions.md").write_text(
        "---\nid: bom-discussion\n"
        "title: BOM discussion\n---\n\nDiscuss this.\n",
        encoding="utf-8-sig",
    )
    presentation_dir = tmp_path / "week1"
    presentation_dir.mkdir()
    (presentation_dir / "index.md").write_text(
        "1. BOM presentation\n",
        encoding="utf-8-sig",
    )
    (presentation_dir / "q01.html").write_text(
        "<p>BOM presentation</p>",
        encoding="utf-8-sig",
    )

    report = validate_question_catalog(tmp_path)

    assert report.ready is True
    assert report.get_week(1).discussion.count == 1
    assert report.get_week(1).presentation.count == 1


def test_discovery_uses_discussion_files_and_presentation_directories(tmp_path):
    (tmp_path / "week-2-questions.md").write_text("placeholder", encoding="utf-8")
    (tmp_path / "week4").mkdir()
    (tmp_path / "week-0-questions.md").write_text("ignored", encoding="utf-8")
    (tmp_path / "weekx").mkdir()

    assert discover_catalog_weeks(tmp_path) == (2, 4)


def test_explicit_weeks_must_be_positive_integers(tmp_path):
    with pytest.raises(ValueError, match="positive integers"):
        validate_question_catalog(tmp_path, weeks=[1, 0])


def test_checked_in_course_exposes_missing_presentation_weeks():
    course_dir = PROJECT_ROOT / "classes" / "432fall2026"

    report = validate_question_catalog(course_dir)

    assert tuple(week.week for week in report.weeks) == (1, 2, 3)
    assert report.get_week(1).ready is True
    assert report.get_week(2).discussion.ready is True
    assert report.get_week(2).presentation.ready is False
    assert report.get_week(3).discussion.ready is True
    assert report.get_week(3).presentation.ready is False


def test_submission_validator_rejects_duplicate_ids_and_titles(tmp_path):
    question_file = tmp_path / "week-1-questions.md"
    question_file.write_text(
        """---
id: Same-ID
title: Repeated   Title
author: Alice Example (alice1)
---

Explain the first question.

---
id: same-id
title: repeated title
author: Bob Example (bob2)
---

Explain the second question.
""",
        encoding="utf-8",
    )
    validator = (
        PROJECT_ROOT / "classes" / "templates" / "validate-question.py"
    )

    result = subprocess.run(
        [sys.executable, str(validator), str(question_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Duplicate 'id' field: matches question #1" in result.stdout
    assert "Duplicate title: matches question #1" in result.stdout
