#!/usr/bin/env python3
"""Create and verify a complete, provider-neutral course backup bundle."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from database import validate_current_schema  # noqa: E402

from versioning import (  # noqa: E402
    APP_VERSION,
    BASELINE_DATA_VERSION,
    BASELINE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    EXPORT_FORMAT_VERSION,
    parse_version,
    public_version,
)

SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")
BUNDLE_FORMAT = "popping-course-backup-v1"
MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "popping.db"
PERSISTENT_DIRECTORIES = ("questions", "appendix")
VERSIONED_DATA_TABLES = (
    "teammate_thumbs",
    "presentation_ratings",
    "challenge_rounds",
    "challenge_ratings",
    "presentation_participants",
    "weekly_hero_summaries",
)
_SCHEMA_LEDGER_COLUMNS = {
    "id", "schema_version", "applied_by_app_version", "applied_at",
}

DEFAULT_LOCK_TIMEOUT_SECONDS = 30.0


class BackupError(RuntimeError):
    """An actionable backup or verification failure."""


def validate_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise BackupError(
            "Course slug may contain only letters, numbers, underscores, "
            "and hyphens"
        )
    return slug


def resolve_data_dir():
    configured = os.environ.get("DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    render_data = Path("/data")
    if render_data.is_dir():
        return render_data.resolve()
    return (PROJECT_ROOT / "data").resolve()


def _is_within(path, parent):
    path_text = os.path.normcase(str(Path(path).resolve()))
    parent_text = os.path.normcase(str(Path(parent).resolve()))
    try:
        return os.path.commonpath([path_text, parent_text]) == parent_text
    except ValueError:
        return False


def resolve_destination(destination, data_dir):
    destination = Path(destination).expanduser().resolve()
    if _is_within(destination, data_dir):
        raise BackupError(
            "Backup destination must be outside DATA_DIR so the live data "
            "disk and its backup cannot fail together"
        )
    if destination.exists() and not destination.is_dir():
        raise BackupError(f"Backup destination is not a directory: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.is_dir():
        raise BackupError(f"Could not create backup destination: {destination}")
    return destination


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_course_slug(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise BackupError(
                    "SQLite integrity check failed: " + "; ".join(integrity)
                )
            courses = db.execute(
                "SELECT id, slug, instructor_id FROM courses"
            ).fetchall()
            foreign_key_issues = db.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_issues:
                raise BackupError(
                    "SQLite foreign key check failed for "
                    f"{len(foreign_key_issues)} row(s)"
                )
            if len(courses) != 1 or not courses[0][1]:
                raise BackupError(
                    "SQLite snapshot must contain exactly one course record"
                )
            course_id, slug, instructor_id = courses[0]
            instructor_count = db.execute(
                "SELECT COUNT(*) FROM instructors WHERE id = ?",
                (instructor_id,),
            ).fetchone()[0]
            if instructor_count != 1:
                raise BackupError(
                    "SQLite snapshot course must reference one instructor"
                )
            state_rows = db.execute(
                "SELECT course_id FROM course_state"
            ).fetchall()
            if state_rows != [(course_id,)]:
                raise BackupError(
                    "SQLite snapshot must contain exactly one matching "
                    "course_state row"
                )
    except sqlite3.Error as exc:
        raise BackupError(f"Could not validate SQLite snapshot: {exc}") from exc
    return str(slug)


def _manifest_public_version(value, field_name):
    """Validate one public semantic version from a backup manifest."""
    if not isinstance(value, str) or not value.startswith("v"):
        raise BackupError(f"Backup manifest has no valid {field_name}")
    try:
        return public_version(value[1:])
    except ValueError as exc:
        raise BackupError(
            f"Backup manifest has no valid {field_name}"
        ) from exc


def _database_schema_version(path):
    """Return the schema version recorded by the archived database itself."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            has_ledger = db.execute(
                """SELECT 1 FROM sqlite_master
                   WHERE type = 'table' AND name = 'schema_migrations'"""
            ).fetchone()
            if has_ledger is None:
                return public_version(BASELINE_SCHEMA_VERSION)

            columns = {
                row[1] for row in db.execute(
                    "PRAGMA table_info(schema_migrations)"
                )
            }
            if not _SCHEMA_LEDGER_COLUMNS.issubset(columns):
                raise BackupError(
                    "Database schema migration ledger is malformed"
                )
            rows = db.execute(
                """SELECT schema_version, applied_by_app_version
                   FROM schema_migrations ORDER BY id"""
            ).fetchall()
            if not rows:
                raise BackupError("Database schema migration ledger is empty")

            previous = None
            latest = None
            for schema_version, app_version in rows:
                try:
                    parsed = parse_version(schema_version)
                    parse_version(app_version)
                except (TypeError, ValueError) as exc:
                    raise BackupError(
                        "Database schema migration ledger contains an invalid "
                        "version"
                    ) from exc
                if parsed[2] != 0:
                    raise BackupError(
                        "Database schema migration ledger contains a schema "
                        "version with a nonzero patch"
                    )
                if previous is not None and parsed <= previous:
                    raise BackupError(
                        "Database schema migration ledger is not strictly "
                        "increasing"
                    )
                previous = parsed
                latest = schema_version
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError(
            f"Could not inspect SQLite schema version: {exc}"
        ) from exc
    return public_version(latest)


def _validate_current_database_schema(path, schema_version):
    """Apply the app's full contract to backups from its current schema."""
    if schema_version != public_version(SCHEMA_VERSION):
        return
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only = ON")
            validate_current_schema(db)
    except RuntimeError as exc:
        raise BackupError(
            f"Current-schema database structure is invalid: {exc}"
        ) from exc
    except sqlite3.Error as exc:
        raise BackupError(
            f"Could not validate current database structure: {exc}"
        ) from exc


def _quote_sqlite_identifier(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _database_data_inventory(path):
    """Return classified data versions and whether raw data is unclassified.

    Pre-versioned tables, rows, and presentation-history entries are the
    declared v1.0.0 baseline. Malformed durable version strings remain intact
    in the snapshot, are excluded from the semantic version list, and set the
    unclassified-data flag.
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    versions = set()
    contains_unclassified_data = False
    try:
        with closing(sqlite3.connect(uri, uri=True)) as db:
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
                if not str(row[0]).startswith("sqlite_")
            }
            for table in sorted(tables):
                quoted_table = _quote_sqlite_identifier(table)
                columns = {
                    row[1] for row in db.execute(
                        f"PRAGMA table_info({quoted_table})"
                    )
                }
                if (
                    table not in VERSIONED_DATA_TABLES
                    and "data_version" not in columns
                ):
                    continue
                has_rows = db.execute(
                    f"SELECT 1 FROM {quoted_table} LIMIT 1"
                ).fetchone()
                if has_rows is None:
                    continue
                if "data_version" not in columns:
                    versions.add(BASELINE_DATA_VERSION)
                    continue
                for row in db.execute(
                    f"SELECT DISTINCT data_version FROM {quoted_table}"
                ):
                    value = row[0]
                    if value is None:
                        value = BASELINE_DATA_VERSION
                    try:
                        public_version(value)
                    except (TypeError, ValueError):
                        contains_unclassified_data = True
                        continue
                    versions.add(value)

            if "course_state" in tables:
                state_columns = {
                    row[1] for row in db.execute(
                        "PRAGMA table_info(course_state)"
                    )
                }
                if "presentation_history" in state_columns:
                    histories = db.execute(
                        "SELECT presentation_history FROM course_state"
                    ).fetchall()
                    for (raw_history,) in histories:
                        if raw_history is None or not str(raw_history).strip():
                            continue
                        try:
                            history = json.loads(raw_history)
                        except (TypeError, ValueError):
                            contains_unclassified_data = True
                            continue
                        if not isinstance(history, list):
                            contains_unclassified_data = True
                            continue
                        for item in history:
                            if not isinstance(item, dict):
                                contains_unclassified_data = True
                                continue
                            if item.get("data_version") is None:
                                versions.add(BASELINE_DATA_VERSION)
                                continue
                            value = item["data_version"]
                            try:
                                public_version(value)
                            except (TypeError, ValueError):
                                contains_unclassified_data = True
                                continue
                            versions.add(value)
    except sqlite3.Error as exc:
        raise BackupError(
            f"Could not inspect SQLite data versions: {exc}"
        ) from exc

    def version_key(value):
        return tuple(int(part) for part in value.split("."))

    public_versions = [
        public_version(value) for value in sorted(versions, key=version_key)
    ]
    return public_versions, contains_unclassified_data


def _database_data_versions(path):
    """Compatibility wrapper returning only classified public versions."""
    return _database_data_inventory(path)[0]


def _persistent_source_files(course_dir):
    files = []
    for directory_name in PERSISTENT_DIRECTORIES:
        source_root = course_dir / directory_name
        if not source_root.exists():
            continue
        if source_root.is_symlink() or not source_root.is_dir():
            raise BackupError(
                f"Persistent path must be a regular directory: {source_root}"
            )
        for current_root, directory_names, file_names in os.walk(source_root):
            current_root = Path(current_root)
            for directory in directory_names:
                candidate = current_root / directory
                if candidate.is_symlink():
                    raise BackupError(
                        f"Symbolic links are not allowed in backups: {candidate}"
                    )
            for filename in file_names:
                candidate = current_root / filename
                if candidate.is_symlink() or not candidate.is_file():
                    raise BackupError(
                        f"Only regular files may be backed up: {candidate}"
                    )
                relative = candidate.relative_to(course_dir)
                files.append((candidate, relative))
    return sorted(files, key=lambda item: item[1].as_posix())


def _copy_persistent_files(course_dir, staging_dir):
    copied = []
    source_files = _persistent_source_files(course_dir)
    for source, relative in source_files:
        target = staging_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append({
            "path": relative.as_posix(),
            "size": target.stat().st_size,
            "sha256": _sha256_file(target),
        })

    after = _persistent_source_files(course_dir)
    before_names = [relative.as_posix() for _source, relative in source_files]
    after_names = [relative.as_posix() for _source, relative in after]
    if before_names != after_names:
        raise BackupError(
            "Persistent question files changed during backup; run it again"
        )
    for source, relative in after:
        expected = next(item for item in copied if item["path"] == relative.as_posix())
        if source.stat().st_size != expected["size"] or (
            _sha256_file(source) != expected["sha256"]
        ):
            raise BackupError(
                f"Persistent file changed during backup: {relative.as_posix()}"
            )
    return copied


def _sqlite_snapshot(source_path, target_path):
    try:
        with closing(sqlite3.connect(str(source_path))) as source:
            with closing(sqlite3.connect(str(target_path))) as target:
                source.backup(target)
    except sqlite3.Error as exc:
        raise BackupError(f"Could not create SQLite snapshot: {exc}") from exc


def _next_archive_path(destination, slug, created_at):
    stamp = created_at.strftime("%Y%m%dT%H%M%SZ")
    counter = 1
    while True:
        suffix = "" if counter == 1 else f"-{counter}"
        candidate = destination / f"popping-{slug}-{stamp}{suffix}.zip"
        try:
            descriptor = os.open(
                candidate,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            counter += 1
            continue
        os.close(descriptor)
        return candidate


def _write_archive(staging_dir, manifest, temporary_archive):
    manifest_path = staging_dir / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    with zipfile.ZipFile(
        temporary_archive, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        archive.write(manifest_path, MANIFEST_NAME)
        for item in manifest["files"]:
            archive.write(staging_dir / item["path"], item["path"])


def _safe_archive_name(name):
    path = PurePosixPath(name)
    return (
        bool(name)
        and "\\" not in name
        and not path.is_absolute()
        and all(part not in ("", ".", "..") for part in path.parts)
    )


def verify_archive(archive_path):
    archive_path = Path(archive_path).expanduser().resolve()
    if not archive_path.is_file():
        raise BackupError(f"Backup archive not found: {archive_path}")

    with tempfile.TemporaryDirectory(prefix="popping-backup-verify-") as temporary:
        database_path = Path(temporary) / DATABASE_NAME
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise BackupError("Backup archive contains duplicate paths")
                if not all(_safe_archive_name(name) for name in names):
                    raise BackupError("Backup archive contains an unsafe path")
                if MANIFEST_NAME not in names:
                    raise BackupError("Backup archive has no manifest.json")
                manifest_info = archive.getinfo(MANIFEST_NAME)
                if manifest_info.file_size > 1024 * 1024:
                    raise BackupError("Backup manifest is unexpectedly large")
                try:
                    manifest = json.loads(archive.read(MANIFEST_NAME))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise BackupError(f"Backup manifest is invalid: {exc}") from exc
                if not isinstance(manifest, dict):
                    raise BackupError("Backup manifest must be a JSON object")
                if manifest.get("format") != BUNDLE_FORMAT:
                    raise BackupError("Backup archive format is not supported")
                baseline = public_version(BASELINE_DATA_VERSION)
                for field in ("website_version", "export_format_version"):
                    value = manifest.get(field, baseline)
                    manifest[field] = _manifest_public_version(value, field)
                declared_schema_version = manifest.get(
                    "database_schema_version"
                )
                if declared_schema_version is not None:
                    declared_schema_version = _manifest_public_version(
                        declared_schema_version, "database_schema_version"
                    )
                    manifest["database_schema_version"] = (
                        declared_schema_version
                    )
                declared_data_versions = manifest.get("contained_data_versions")
                if declared_data_versions is not None:
                    if not isinstance(declared_data_versions, list):
                        raise BackupError(
                            "Backup manifest contained_data_versions must be a list"
                        )
                    declared_data_versions = [
                        _manifest_public_version(value, "contained data version")
                        for value in declared_data_versions
                    ]
                    if len(declared_data_versions) != len(
                        set(declared_data_versions)
                    ):
                        raise BackupError(
                            "Backup manifest repeats a contained data version"
                        )
                has_declared_unclassified = (
                    "contains_unclassified_data" in manifest
                )
                declared_unclassified = manifest.get(
                    "contains_unclassified_data"
                )
                if (
                    has_declared_unclassified
                    and type(declared_unclassified) is not bool
                ):
                    raise BackupError(
                        "Backup manifest contains_unclassified_data must be "
                        "true or false"
                    )
                slug = validate_slug(manifest.get("course_slug"))
                timestamp = manifest.get("created_at_utc")
                if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
                    raise BackupError("Backup manifest has no valid UTC timestamp")
                try:
                    datetime.fromisoformat(timestamp[:-1] + "+00:00")
                except ValueError as exc:
                    raise BackupError(
                        "Backup manifest has no valid UTC timestamp"
                    ) from exc
                if manifest.get("database_integrity") != "ok" or (
                    manifest.get("database_foreign_key_check") != "ok"
                ):
                    raise BackupError(
                        "Backup manifest does not record successful database checks"
                    )
                files = manifest.get("files")
                if not isinstance(files, list) or not files:
                    raise BackupError("Backup manifest has no files")

                expected_names = {MANIFEST_NAME}
                seen_paths = set()
                database_found = False
                for item in files:
                    if not isinstance(item, dict):
                        raise BackupError(
                            "Backup manifest contains an invalid file entry"
                        )
                    path = item.get("path")
                    if not isinstance(path, str) or not _safe_archive_name(path):
                        raise BackupError(
                            "Backup manifest contains an unsafe file path"
                        )
                    parts = PurePosixPath(path).parts
                    if path != DATABASE_NAME and parts[0] not in PERSISTENT_DIRECTORIES:
                        raise BackupError(
                            f"Backup manifest contains an unexpected path: {path}"
                        )
                    if path in seen_paths:
                        raise BackupError(
                            f"Backup manifest repeats file path: {path}"
                        )
                    declared_size = item.get("size")
                    declared_digest = item.get("sha256")
                    if not isinstance(declared_size, int) or declared_size < 0:
                        raise BackupError(
                            f"Backup manifest has an invalid size for {path}"
                        )
                    if not isinstance(declared_digest, str) or not re.fullmatch(
                        r"[0-9a-f]{64}", declared_digest
                    ):
                        raise BackupError(
                            f"Backup manifest has an invalid checksum for {path}"
                        )
                    seen_paths.add(path)
                    expected_names.add(path)
                    digest = hashlib.sha256()
                    actual_size = 0
                    target = database_path.open("wb") if path == DATABASE_NAME else None
                    try:
                        try:
                            source = archive.open(path, "r")
                        except KeyError as exc:
                            raise BackupError(
                                f"Backup archive is missing {path}"
                            ) from exc
                        with source:
                            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                                actual_size += len(chunk)
                                digest.update(chunk)
                                if target is not None:
                                    target.write(chunk)
                    finally:
                        if target is not None:
                            target.close()
                    if actual_size != declared_size:
                        raise BackupError(
                            f"Backup file size does not match: {path}"
                        )
                    if digest.hexdigest() != declared_digest:
                        raise BackupError(
                            f"Backup checksum does not match: {path}"
                        )
                    if path == DATABASE_NAME:
                        database_found = True

                if set(names) != expected_names:
                    raise BackupError("Backup archive contains unlisted files")
                if not database_found:
                    raise BackupError("Backup archive has no popping.db snapshot")
            archived_slug = _database_course_slug(database_path)
            actual_schema_version = _database_schema_version(database_path)
            _validate_current_database_schema(
                database_path, actual_schema_version
            )
            if declared_schema_version is None:
                manifest["database_schema_version"] = actual_schema_version
            elif declared_schema_version != actual_schema_version:
                raise BackupError(
                    "Backup manifest database schema version does not match "
                    "the archived database"
                )
            actual_data_versions, actual_unclassified = (
                _database_data_inventory(database_path)
            )
            if declared_data_versions is None:
                # Version fields were not present in early v1 bundle manifests.
                manifest["contained_data_versions"] = actual_data_versions
            elif set(declared_data_versions) != set(actual_data_versions):
                raise BackupError(
                    "Backup manifest contained data versions do not match "
                    "the archived database"
                )
            else:
                manifest["contained_data_versions"] = actual_data_versions
            if not has_declared_unclassified:
                manifest["contains_unclassified_data"] = actual_unclassified
            elif declared_unclassified != actual_unclassified:
                raise BackupError(
                    "Backup manifest unclassified-data flag does not match "
                    "the archived database"
                )
        except BackupError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise BackupError(f"Could not read backup archive: {exc}") from exc

    if archived_slug != slug:
        raise BackupError(
            "Backup database course slug does not match its manifest"
        )
    return manifest


def create_backup(slug, destination, lock_timeout=DEFAULT_LOCK_TIMEOUT_SECONDS):
    slug = validate_slug(slug)
    data_dir = resolve_data_dir()
    course_dir = (data_dir / slug).resolve()
    database_path = course_dir / DATABASE_NAME
    if not database_path.is_file():
        raise BackupError(f"Course database not found: {database_path}")
    requested_destination = Path(destination).expanduser().resolve()
    if _is_within(requested_destination, course_dir):
        raise BackupError(
            "Backup destination must be outside DATA_DIR and the resolved live "
            "course data directory"
        )
    destination = resolve_destination(requested_destination, data_dir)

    created_at = datetime.now(timezone.utc)
    final_archive = None
    temporary_archive = None
    with tempfile.TemporaryDirectory(
        prefix=".popping-course-backup-", dir=destination
    ) as temporary:
        staging_dir = Path(temporary)
        snapshot_path = staging_dir / DATABASE_NAME
        try:
            with closing(sqlite3.connect(
                str(database_path), timeout=float(lock_timeout), isolation_level=None
            )) as lock:
                lock.execute(f"PRAGMA busy_timeout = {int(float(lock_timeout) * 1000)}")
                lock.execute("BEGIN IMMEDIATE")
                try:
                    _sqlite_snapshot(database_path, snapshot_path)
                    persistent_files = _copy_persistent_files(
                        course_dir, staging_dir
                    )
                finally:
                    lock.rollback()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).casefold() or "busy" in str(exc).casefold():
                raise BackupError(
                    "Course database stayed busy; retry when classroom activity is quiet"
                ) from exc
            raise BackupError(f"Could not lock course database: {exc}") from exc

        archived_slug = _database_course_slug(snapshot_path)
        if archived_slug != slug:
            raise BackupError(
                f"Database belongs to course {archived_slug}, not {slug}"
            )
        archived_schema_version = _database_schema_version(snapshot_path)
        _validate_current_database_schema(
            snapshot_path, archived_schema_version
        )
        contained_data_versions, contains_unclassified_data = (
            _database_data_inventory(snapshot_path)
        )
        database_item = {
            "path": DATABASE_NAME,
            "size": snapshot_path.stat().st_size,
            "sha256": _sha256_file(snapshot_path),
        }
        manifest = {
            "format": BUNDLE_FORMAT,
            "created_at_utc": created_at.isoformat().replace("+00:00", "Z"),
            "course_slug": slug,
            "website_version": public_version(APP_VERSION),
            "database_schema_version": archived_schema_version,
            "export_format_version": public_version(EXPORT_FORMAT_VERSION),
            "contained_data_versions": contained_data_versions,
            "contains_unclassified_data": contains_unclassified_data,
            "database_integrity": "ok",
            "database_foreign_key_check": "ok",
            "files": [database_item, *persistent_files],
        }

        handle = tempfile.NamedTemporaryFile(
            prefix=".popping-course-backup-",
            suffix=".tmp.zip",
            dir=destination,
            delete=False,
        )
        temporary_archive = Path(handle.name)
        handle.close()
        try:
            _write_archive(staging_dir, manifest, temporary_archive)
            verify_archive(temporary_archive)
            final_archive = _next_archive_path(destination, slug, created_at)
            installed = False
            try:
                os.replace(temporary_archive, final_archive)
                installed = True
            finally:
                if not installed:
                    final_archive.unlink(missing_ok=True)
            temporary_archive = None
        finally:
            if temporary_archive and temporary_archive.exists():
                temporary_archive.unlink()
    return final_archive


def build_parser():
    parser = argparse.ArgumentParser(
        description="Create or verify a complete Popping course backup bundle."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a verified backup ZIP")
    create.add_argument("course_slug")
    create.add_argument(
        "destination",
        help="directory outside DATA_DIR, such as an external or synced drive",
    )
    create.add_argument(
        "--lock-timeout",
        type=float,
        default=DEFAULT_LOCK_TIMEOUT_SECONDS,
        help="seconds to wait for current database writes (default: 30)",
    )
    verify = commands.add_parser("verify", help="verify a backup ZIP")
    verify.add_argument("archive")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            if args.lock_timeout < 0:
                raise BackupError("Lock timeout cannot be negative")
            archive = create_backup(
                args.course_slug, args.destination, args.lock_timeout
            )
            print(f"Verified backup created: {archive}")
        else:
            manifest = verify_archive(args.archive)
            print(
                "Backup verified: "
                f"{args.archive} ({manifest['course_slug']}, "
                f"{len(manifest['files'])} files)"
            )
        return 0
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
