"""Regression checks for question-authoring and backup operator guidance."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (PROJECT_ROOT / relative).read_text(encoding="utf-8")


def test_question_guide_documents_only_the_canonical_weekly_source():
    guide = read("question-guide/README.md")
    assert "one UTF-8 Markdown file" in guide
    assert "exact same ordered question set" in guide
    assert "week-N-questions.md" in guide
    assert "Legacy files are not used" in guide
    assert "does not read" in guide
    assert "server reads `index.md`" not in guide


def test_legacy_pre_rendered_assets_are_fully_removed():
    """The obsolete pre-rendered workflow must not creep back in."""
    removed_paths = (
        "question-guide/LEGACY_PRE_RENDERED_HTML_GUIDE.md",
        "question-guide/examples",
        "classes/432fall2026/week1",
        "classes/demo/week1",
    )
    for relative in removed_paths:
        assert not (PROJECT_ROOT / relative).exists(), relative
    # No tracked file may point future authors at the legacy workflow.
    guide = read("question-guide/README.md")
    assert "LEGACY_PRE_RENDERED_HTML_GUIDE" not in guide
    assert "LEGACY_UNUSED" not in guide


def test_backup_guide_covers_security_verification_and_complete_recovery():
    guide = read("BACKUP_AND_RECOVERY.md")
    readme = read("README.md")
    assert "backup-course.py create" in readme
    assert "backup-course.py verify" in readme
    assert "outside `DATA_DIR`" in guide
    assert "SHA-256" in guide
    assert "foreign-key checks" in guide
    assert "plaintext" in guide
    assert "does not encrypt or upload" in guide
    assert "restore-course-db.py" in guide
    assert "questions/` and `appendix/`" in guide
