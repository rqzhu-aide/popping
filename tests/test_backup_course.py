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
