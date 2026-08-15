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


def test_preserved_legacy_guide_starts_with_a_do_not_use_warning():
    legacy = read("question-guide/LEGACY_PRE_RENDERED_HTML_GUIDE.md")
    opening = "\n".join(legacy.splitlines()[:7])
    assert "Historical reference only" in opening
    assert "current application ignores" in opening
    assert "Do not follow it for" in opening
    assert "README.md" in opening


def test_checked_in_legacy_asset_directories_are_marked_unused():
    for relative in (
        "classes/432fall2026/week1/LEGACY_UNUSED.md",
        "classes/demo/week1/LEGACY_UNUSED.md",
        "question-guide/examples/LEGACY_UNUSED.md",
    ):
        marker = " ".join(read(relative).split())
        assert "current application does not read" in marker
        assert "canonical weekly file" in marker


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
