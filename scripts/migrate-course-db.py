#!/usr/bin/env python3
"""Safely migrate one persistent course database to the current schema."""

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from database import (  # noqa: E402
    inspect_schema_version,
    migrate_schema_connection,
    validate_data_version_schema,
    validate_current_schema,
    validate_legacy_adoption_candidate,
    validate_schema_compatibility,
    validate_slug,
)
from scripts.maintenance_safety import (  # noqa: E402
    SERVICE_STOPPED_CONFIRMATION,
    confirmation_prompt,
    validate_confirmation,
)
from versioning import (  # noqa: E402
    BASELINE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    public_version,
)


BACKUP_RETENTION = 3
DATABASE_SIDECARS = ("-wal", "-shm", "-journal")
OFFLINE_CONFIRMATION = SERVICE_STOPPED_CONFIRMATION


def course_database_path(slug):
    return os.path.join(config.DATA_DIR, slug, "popping.db")


def _validate_database_identity(connection, expected_slug):
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise RuntimeError(
            "SQLite integrity check failed: " + "; ".join(integrity)
        )
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise RuntimeError(
            "SQLite foreign key check found "
            f"{len(foreign_key_errors)} error(s)"
        )
    course_slugs = [
        row[0] for row in connection.execute("SELECT slug FROM courses")
    ]
    if course_slugs != [expected_slug]:
        raise RuntimeError(
            "Database course slug does not match the requested course"
        )


def _is_repairable_index_error(error):
    return str(error).startswith("Database is missing required index ")


def inspect_migration_source(path, expected_slug):
    """Return the source version and whether current indexes need repair."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        _validate_database_identity(connection, expected_slug)
        recorded = validate_schema_compatibility(
            connection, allow_unversioned=True
        )
        if recorded is None:
            validate_legacy_adoption_candidate(connection)
            repair_required = False
        elif recorded == SCHEMA_VERSION:
            try:
                validate_current_schema(connection)
                repair_required = False
            except RuntimeError as exc:
                if not _is_repairable_index_error(exc):
                    raise
                repair_required = True
        else:
            validate_data_version_schema(connection)
            repair_required = False
        return recorded, repair_required
    finally:
        connection.close()


def _prune_backups(backup_dir, keep=BACKUP_RETENTION):
    backups = sorted(
        (
            path for path in Path(backup_dir).glob("popping-before-migration-*.db")
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime, path.name),
    )
    for path in backups[:-keep]:
        for candidate in (path,) + tuple(
            Path(str(path) + suffix) for suffix in DATABASE_SIDECARS
        ):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def create_migration_backup(database_path, expected_slug):
    """Create and validate a SQLite snapshot immediately before migration."""
    backup_dir = Path(database_path).parent / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    final_path = backup_dir / f"popping-before-migration-{timestamp}.db"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".popping-migration-backup-",
        suffix=".tmp.db",
        dir=backup_dir,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    source = target = None
    try:
        source = sqlite3.connect(database_path, timeout=30)
        target = sqlite3.connect(temporary_path)
        source.backup(target)
        target.close()
        target = None
        source.close()
        source = None

        snapshot = sqlite3.connect(temporary_path)
        try:
            _validate_database_identity(snapshot, expected_slug)
            validate_schema_compatibility(snapshot, allow_unversioned=True)
        finally:
            snapshot.close()
        os.replace(temporary_path, final_path)
        _prune_backups(backup_dir)
        return final_path
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def migrate_database(database_path, expected_slug):
    """Apply the registered migration plan in one immediate transaction."""
    connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            _validate_database_identity(connection, expected_slug)
            recorded = inspect_schema_version(
                connection, allow_unversioned=True
            )
            if recorded == SCHEMA_VERSION:
                validate_current_schema(connection, repair_indexes=True)
            else:
                migrate_schema_connection(connection)
            recorded = inspect_schema_version(
                connection, allow_unversioned=False
            )
            if recorded != SCHEMA_VERSION:
                raise RuntimeError(
                    "Migration did not reach schema version " + SCHEMA_VERSION
                )
            validate_current_schema(connection)
            _validate_database_identity(connection, expected_slug)
            connection.commit()
            return recorded
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()


def _source_label(recorded):
    if recorded is None:
        return (
            "unversioned baseline "
            f"(treated as {public_version(BASELINE_SCHEMA_VERSION)})"
        )
    return public_version(recorded)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python scripts/migrate-course-db.py <course_slug>")
        return 1

    try:
        slug = validate_slug(args[0].strip())
        database_path = course_database_path(slug)
        if not os.path.isfile(database_path):
            raise ValueError(f"Course database not found: {database_path}")

        recorded, repair_required = inspect_migration_source(
            database_path, slug
        )
        print("=== Migrate Course Database ===")
        print(f"Course: {slug}")
        print(f"Database: {database_path}")
        print(f"Current schema: {_source_label(recorded)}")
        print(f"Target schema: {public_version(SCHEMA_VERSION)}")

        if recorded == SCHEMA_VERSION and not repair_required:
            print("Database already uses the current schema. No changes made.")
            return 0
        if repair_required:
            print("Current schema is missing required indexes and needs repair.")

        confirmation = input(
            f"Type {slug} to migrate this course database: "
        ).strip()
        if confirmation != slug:
            print("Cancelled: course slug did not match.")
            return 1
        offline_confirmation = input(confirmation_prompt()).strip()
        try:
            validate_confirmation(
                offline_confirmation,
                PROJECT_ROOT / "classes" / slug / "course.yaml",
            )
        except ValueError as exc:
            print(f"Cancelled: {exc}.")
            return 1

        backup_path = create_migration_backup(database_path, slug)
        migrated = migrate_database(database_path, slug)
        print(f"Backup: {backup_path}")
        print(f"Migration complete: {public_version(migrated)}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
