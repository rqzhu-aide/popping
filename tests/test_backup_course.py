"""Regression tests for complete, off-disk course backup bundles."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sqlite3
from threading import Barrier
import zipfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "backup-course.py"
SCHEMA_PATH = PROJECT_ROOT / "popping.sql"


@pytest.fixture
def backup_course_module():
    spec = importlib.util.spec_from_file_location("backup_course", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_course(data_dir, slug="safe101", directory_slug=None):
    course_dir = data_dir / (directory_slug or slug)
    course_dir.mkdir(parents=True)
    database = course_dir / "popping.db"
    db = sqlite3.connect(database)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, value TEXT)")
    instructor_id = db.execute(
        "INSERT INTO instructors (username, name, pin) VALUES ('teacher', 'Teacher', '2468')"
    ).lastrowid
    course_id = db.execute(
        """INSERT INTO courses (name, code, semester, slug, instructor_id)
           VALUES ('Backup Test', 'SAFE 101', 'Test 2026', ?, ?)""",
        (slug, instructor_id),
    ).lastrowid
    db.execute(
        "INSERT INTO course_state (course_id, phase) VALUES (?, 'setup')",
        (course_id,),
    )
    db.execute("INSERT INTO notes (value) VALUES ('captured')")
    db.commit()
    db.close()
    questions = course_dir / "questions" / "week-1-questions.md"
    questions.parent.mkdir()
    questions.write_text("weekly questions\n", encoding="utf-8")
    appendix = course_dir / "appendix" / "week-1-appendix.md"
    appendix.parent.mkdir()
    appendix.write_text("appendix questions\n", encoding="utf-8")
    return course_dir


def test_create_bundle_captures_database_and_persistent_question_files(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    archive_path = backup_course_module.create_backup("safe101", destination)

    assert archive_path.parent == destination.resolve()
    manifest = backup_course_module.verify_archive(archive_path)
    assert manifest["format"] == "popping-course-backup-v1"
    assert manifest["course_slug"] == "safe101"
    assert manifest["website_version"] == "v1.0.0"
    assert manifest["database_schema_version"] == "v1.0.0"
    assert manifest["export_format_version"] == "v1.0.0"
    assert manifest["contained_data_versions"] == []
    assert manifest["contains_unclassified_data"] is False
    assert manifest["database_integrity"] == "ok"
    assert manifest["database_foreign_key_check"] == "ok"
    by_path = {item["path"]: item for item in manifest["files"]}
    assert set(by_path) == {
        "popping.db",
        "questions/week-1-questions.md",
        "appendix/week-1-appendix.md",
    }
    assert all(len(item["sha256"]) == 64 for item in by_path.values())

    with zipfile.ZipFile(archive_path) as archive:
        assert archive.read("questions/week-1-questions.md").replace(
            b"\r\n", b"\n"
        ) == b"weekly questions\n"
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(archive.read("popping.db"))
    with sqlite3.connect(snapshot) as db:
        assert db.execute("SELECT value FROM notes").fetchone()[0] == "captured"


def test_bundle_manifest_uses_archived_database_schema_ledger(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    course_dir = create_course(data_dir)
    with sqlite3.connect(course_dir / "popping.db") as db:
        db.execute(
            """UPDATE schema_migrations
               SET schema_version = '1.2.0',
                   applied_by_app_version = '1.2.4'"""
        )

    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    archive_path = backup_course_module.create_backup("safe101", destination)

    manifest = backup_course_module.verify_archive(archive_path)
    assert manifest["website_version"] == "v1.0.0"
    assert manifest["database_schema_version"] == "v1.2.0"


def test_create_bundle_rejects_destination_inside_live_data(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    with pytest.raises(backup_course_module.BackupError, match="outside DATA_DIR"):
        backup_course_module.create_backup(
            "safe101", data_dir / "safe101" / "backup"
        )

    assert not (data_dir / "safe101" / "backup").exists()


def test_create_bundle_requires_exact_case_sensitive_course_slug(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir, slug="safe101", directory_slug="SAFE101")
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    with pytest.raises(
        backup_course_module.BackupError,
        match="belongs to course safe101, not SAFE101",
    ):
        backup_course_module.create_backup("SAFE101", destination)


def test_concurrent_backups_create_distinct_valid_archives(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))

    original_next_archive_path = backup_course_module._next_archive_path
    selection_barrier = Barrier(2)

    def synchronized_next_archive_path(*args, **kwargs):
        archive_path = original_next_archive_path(*args, **kwargs)
        selection_barrier.wait(timeout=10)
        return archive_path

    monkeypatch.setattr(
        backup_course_module,
        "_next_archive_path",
        synchronized_next_archive_path,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                backup_course_module.create_backup,
                "safe101",
                destination,
            )
            for _ in range(2)
        ]
        archives = [future.result() for future in futures]

    assert archives[0] != archives[1]
    assert all(path.is_file() for path in archives)
    assert all(
        backup_course_module.verify_archive(path)["course_slug"] == "safe101"
        for path in archives
    )


def test_verify_rejects_tampered_file(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    tampered = destination / "tampered.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "questions/week-1-questions.md":
                contents = b"changed after the manifest was written\n"
            target.writestr(name, contents)

    with pytest.raises(
        backup_course_module.BackupError,
        match="(size|checksum) does not match",
    ):
        backup_course_module.verify_archive(tampered)


def test_verify_rejects_manifest_database_slug_mismatch(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    tampered = destination / "wrong-slug.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(contents)
                manifest["course_slug"] = "other101"
                contents = json.dumps(manifest).encode("utf-8")
            target.writestr(name, contents)

    with pytest.raises(
        backup_course_module.BackupError,
        match="course slug does not match",
    ):
        backup_course_module.verify_archive(tampered)


def test_verify_requires_exact_case_sensitive_course_slug(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    tampered = destination / "wrong-slug-case.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(contents)
                manifest["course_slug"] = "SAFE101"
                contents = json.dumps(manifest).encode("utf-8")
            target.writestr(name, contents)

    with pytest.raises(
        backup_course_module.BackupError,
        match="course slug does not match",
    ):
        backup_course_module.verify_archive(tampered)


def test_cli_verify_reports_success(
    backup_course_module, tmp_path, monkeypatch, capsys
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    archive_path = backup_course_module.create_backup("safe101", destination)

    assert backup_course_module.main(["verify", str(archive_path)]) == 0
    assert "Backup verified" in capsys.readouterr().out


def test_verify_rejects_non_object_manifest(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    tampered = destination / "manifest-list.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                contents = b"[]"
            target.writestr(name, contents)

    with pytest.raises(
        backup_course_module.BackupError,
        match="JSON object",
    ):
        backup_course_module.verify_archive(tampered)


def test_data_version_inventory_handles_versioned_and_preversioned_tables(
    backup_course_module, tmp_path
):
    database = tmp_path / "versions.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE teammate_thumbs (data_version TEXT)")
        db.executemany(
            "INSERT INTO teammate_thumbs (data_version) VALUES (?)",
            [("1.0.3",), (None,), ("not-a-version",)],
        )
        db.execute("CREATE TABLE presentation_ratings (value TEXT)")
        db.execute("INSERT INTO presentation_ratings (value) VALUES ('legacy')")
        db.execute("CREATE TABLE challenge_rounds (data_version TEXT)")
        db.execute(
            "INSERT INTO challenge_rounds (data_version) VALUES ('1.0.10')"
        )
        db.execute("CREATE TABLE challenge_ratings (data_version TEXT)")
        db.execute("CREATE TABLE course_state (presentation_history TEXT)")
        db.execute(
            "INSERT INTO course_state (presentation_history) VALUES (?)",
            (json.dumps([{"data_version": "1.0.8"}, {}]),),
        )

    versions, unclassified = backup_course_module._database_data_inventory(
        database
    )
    assert versions == [
        "v1.0.0",
        "v1.0.3",
        "v1.0.8",
        "v1.0.10",
    ]
    assert unclassified is True


def test_bundle_manifest_lists_versions_present_in_feedback_rows(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    course_dir = create_course(data_dir)
    with sqlite3.connect(course_dir / "popping.db") as db:
        db.execute("PRAGMA foreign_keys=ON")
        course_id = db.execute("SELECT id FROM courses").fetchone()[0]
        team_id = db.execute(
            "INSERT INTO teams (course_id, name) VALUES (?, 'Team 1')",
            (course_id,),
        ).lastrowid
        first = db.execute(
            """INSERT INTO students
               (course_id, student_id, name, pin, team_id)
               VALUES (?, 's1', 'One', '1111', ?)""",
            (course_id, team_id),
        ).lastrowid
        second = db.execute(
            """INSERT INTO students
               (course_id, student_id, name, pin, team_id)
               VALUES (?, 's2', 'Two', '2222', ?)""",
            (course_id, team_id),
        ).lastrowid
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, week_num, question_key,
                grader_id, recipient_id, data_version)
               VALUES (?, 1, 1, 'thumb', ?, ?, '1.0.3')""",
            (course_id, first, second),
        )
        db.execute(
            """INSERT INTO teammate_thumbs
               (course_id, session_key, week_num, question_key,
                grader_id, recipient_id, data_version)
               VALUES (?, 1, 1, 'malformed', ?, ?,
                       'not-a-version')""",
            (course_id, first, second),
        )
        db.execute(
            """INSERT INTO presentation_ratings
               (course_id, student_id, question_key, week_num,
                q1_developed, q2_easy)
               VALUES (?, ?, 'rating', 1, 4, 5)""",
            (course_id, first),
        )
        db.execute(
            "UPDATE course_state SET presentation_history = ?",
            (json.dumps([
                {"data_version": "1.0.6"},
                {"presentation_key": "pre-versioned"},
            ]),),
        )

    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    archive_path = backup_course_module.create_backup("safe101", destination)

    manifest = backup_course_module.verify_archive(archive_path)
    assert manifest["contained_data_versions"] == [
        "v1.0.0", "v1.0.3", "v1.0.6",
    ]
    assert manifest["contains_unclassified_data"] is True

    with zipfile.ZipFile(archive_path) as archive:
        snapshot = tmp_path / "versioned-snapshot.db"
        snapshot.write_bytes(archive.read("popping.db"))
    with sqlite3.connect(snapshot) as db:
        assert db.execute(
            """SELECT data_version FROM teammate_thumbs
               WHERE question_key = 'malformed'"""
        ).fetchone()[0] == "not-a-version"


def test_verify_interprets_early_v1_manifest_as_version_1_0_0(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    course_dir = create_course(data_dir)
    with sqlite3.connect(course_dir / "popping.db") as db:
        db.execute("DROP TABLE schema_migrations")
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    legacy = destination / "early-v1.zip"

    version_fields = {
        "website_version",
        "database_schema_version",
        "export_format_version",
        "contained_data_versions",
        "contains_unclassified_data",
    }
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(
        legacy, "w"
    ) as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(contents)
                for field in version_fields:
                    manifest.pop(field)
                contents = json.dumps(manifest).encode("utf-8")
            target.writestr(name, contents)

    manifest = backup_course_module.verify_archive(legacy)
    assert manifest["website_version"] == "v1.0.0"
    assert manifest["database_schema_version"] == "v1.0.0"
    assert manifest["export_format_version"] == "v1.0.0"
    assert manifest["contained_data_versions"] == []
    assert manifest["contains_unclassified_data"] is False


def test_verify_rejects_contained_versions_that_do_not_match_database(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    tampered = destination / "wrong-data-versions.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(
        tampered, "w"
    ) as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(contents)
                manifest["contained_data_versions"] = ["v1.0.3"]
                contents = json.dumps(manifest).encode("utf-8")
            target.writestr(name, contents)

    with pytest.raises(
        backup_course_module.BackupError,
        match="contained data versions do not match",
    ):
        backup_course_module.verify_archive(tampered)

def test_verify_rejects_unclassified_flag_that_disagrees_with_database(
    backup_course_module, tmp_path, monkeypatch
):
    data_dir = tmp_path / "live-data"
    create_course(data_dir)
    destination = tmp_path / "offsite"
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    original = backup_course_module.create_backup("safe101", destination)
    tampered = destination / "wrong-unclassified-flag.zip"

    with zipfile.ZipFile(original) as source, zipfile.ZipFile(
        tampered, "w"
    ) as target:
        for name in source.namelist():
            contents = source.read(name)
            if name == "manifest.json":
                manifest = json.loads(contents)
                manifest["contains_unclassified_data"] = True
                contents = json.dumps(manifest).encode("utf-8")
            target.writestr(name, contents)

    with pytest.raises(
        backup_course_module.BackupError,
        match="unclassified-data flag does not match",
    ):
        backup_course_module.verify_archive(tampered)
