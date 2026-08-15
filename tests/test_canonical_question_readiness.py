"""Readiness tests for one effective weekly question source."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from question_catalog import (  # noqa: E402
    discover_catalog_weeks,
    read_week_questions,
    validate_question_catalog,
)


def _write_week(directory, week, question_id, title, content):
    path = directory / f"week-{week}-questions.md"
    path.write_text(
        "\n".join(
            (
                "---",
                f"id: {question_id}",
                f'title: "{title}"',
                "---",
                "",
                content,
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_one_file_makes_both_phases_ready_with_the_same_status(tmp_path):
    canonical = _write_week(
        tmp_path, 2, "shared", "Shared question", "Shared body."
    )

    report = validate_question_catalog(tmp_path)
    week = report.get_week(2)

    assert report.ready is True
    assert week.ready is True
    assert week.discussion == week.presentation
    assert week.discussion.ready is True
    assert week.discussion.count == 1
    assert Path(week.discussion.path) == canonical


def test_persistent_file_overrides_bundled_file_for_both_phases(tmp_path):
    persistent = tmp_path / "persistent"
    bundled = tmp_path / "bundled"
    persistent.mkdir()
    bundled.mkdir()
    _write_week(bundled, 1, "bundled", "Bundled", "Bundled body.")
    uploaded = _write_week(
        persistent, 1, "uploaded", "Uploaded", "Uploaded body."
    )

    report = validate_question_catalog(
        persistent, fallback_dir=bundled
    )
    week = report.get_week(1)

    assert week.discussion == week.presentation
    assert Path(week.discussion.path) == uploaded
    assert read_week_questions(week.discussion.path) == [
        {
            "id": "uploaded",
            "num": 1,
            "title": "Uploaded",
            "content": "Uploaded body.",
            "source_key": "week-1-q-uploaded",
        }
    ]


def test_legacy_index_and_html_are_ignored_for_readiness_and_discovery(
        tmp_path):
    canonical = _write_week(
        tmp_path, 1, "canonical", "Canonical", "Canonical body."
    )
    legacy = tmp_path / "week1"
    legacy.mkdir()
    (legacy / "index.md").write_text(
        "1. Wrong legacy question\n2. Another wrong question\n",
        encoding="utf-8",
    )
    (legacy / "q01.html").write_text(
        "<p>Wrong legacy body</p>", encoding="utf-8"
    )
    (tmp_path / "week9").mkdir()

    report = validate_question_catalog(tmp_path)
    week = report.get_week(1)

    assert discover_catalog_weeks(tmp_path) == (1,)
    assert week.ready is True
    assert week.discussion == week.presentation
    assert Path(week.discussion.path) == canonical
    assert week.discussion.count == 1


def test_bundled_file_is_used_only_when_persistent_override_is_absent(tmp_path):
    persistent = tmp_path / "persistent"
    bundled = tmp_path / "bundled"
    persistent.mkdir()
    bundled.mkdir()
    bundled_path = _write_week(
        bundled, 3, "fallback", "Fallback", "Fallback body."
    )

    report = validate_question_catalog(
        persistent, fallback_dir=bundled
    )
    week = report.get_week(3)

    assert discover_catalog_weeks(persistent, bundled) == (3,)
    assert week.ready is True
    assert week.discussion == week.presentation
    assert Path(week.discussion.path) == bundled_path
