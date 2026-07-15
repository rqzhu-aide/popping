#!/usr/bin/env python3
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile


SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
BACKUP_RETENTION = 3
OFFLINE_CONFIRMATION = 'SERVICE STOPPED'
DATABASE_SIDECARS = ('-wal', '-shm', '-journal')
REQUIRED_SCHEMA = {
    'instructors': {'id', 'username', 'name', 'pin'},
    'courses': {
        'id', 'name', 'code', 'semester', 'slug', 'instructor_id', 'is_active'
    },
    'teams': {'id', 'course_id', 'name', 'color'},
    'students': {'id', 'course_id', 'student_id', 'name', 'pin', 'team_id'},
    'questions': {'id', 'course_id', 'question_num', 'question_text'},
    'course_state': {
        'id', 'course_id', 'phase', 'active_team_id', 'active_question_id',
        'current_question', 'presentation_started_at'
    },
    'peer_reviews': {
        'course_id', 'grader_id', 'recipient_id', 'criterion', 'score',
        'created_at'
    },
    'presentation_ratings': {
        'course_id', 'student_id', 'question_key', 'q1_developed', 'q2_easy'
    },
    'teammate_thumbs': {
        'course_id', 'session_key', 'question_key', 'grader_id', 'recipient_id'
    },
}
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def validate_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise ValueError(
            "Course slug may contain only letters, numbers, underscores, and hyphens"
        )
    return slug


def resolve_data_dir():
    configured = os.environ.get('DATA_DIR')
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if os.path.isdir('/data'):
        return '/data'
    return os.path.join(PROJECT_ROOT, 'data')


def open_read_only(path):
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    return sqlite3.connect(uri, uri=True)


def validate_course_database(path, expected_slug):
    if not os.path.isfile(path):
        raise ValueError(f"Database file not found: {path}")

    conn = open_read_only(path)
    try:
        integrity = [row[0] for row in conn.execute('PRAGMA integrity_check')]
        if integrity != ['ok']:
            raise RuntimeError(
                f"SQLite integrity check failed: {'; '.join(integrity)}"
            )

        foreign_key_errors = conn.execute('PRAGMA foreign_key_check').fetchall()
        if foreign_key_errors:
            raise RuntimeError(
                f"SQLite foreign key check found {len(foreign_key_errors)} error(s)"
            )

        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing_tables = sorted(set(REQUIRED_SCHEMA) - tables)
        if missing_tables:
            raise RuntimeError(
                "Database is missing required application table(s): "
                + ', '.join(missing_tables)
            )
        for table, required_columns in REQUIRED_SCHEMA.items():
            columns = {
                row[1] for row in conn.execute(f'PRAGMA table_info({table})')
            }
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                raise RuntimeError(
                    f"Database table {table} is missing required column(s): "
                    + ', '.join(missing_columns)
                )

        courses = conn.execute(
            'SELECT id, slug, instructor_id FROM courses'
        ).fetchall()
        if len(courses) != 1 or courses[0][1] != expected_slug:
            raise RuntimeError(
                "Database course slug does not match the requested course"
            )
        course_id, _slug, instructor_id = courses[0]
        instructor_count = conn.execute(
            'SELECT COUNT(*) FROM instructors WHERE id = ?', [instructor_id]
        ).fetchone()[0]
        if instructor_count != 1:
            raise RuntimeError(
                "Database course does not reference one matching instructor"
            )
        state_rows = conn.execute(
            'SELECT course_id FROM course_state'
        ).fetchall()
        if state_rows != [(course_id,)]:
            raise RuntimeError(
                "Database must contain exactly one matching course_state row"
            )
    finally:
        conn.close()


def sqlite_backup(source_path, target_path):
    source = target = None
    try:
        source = open_read_only(source_path)
        target = sqlite3.connect(target_path)
        source.backup(target)
        target.close()
        target = None
        source.close()
        source = None
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()


def prune_backups(backup_dir, keep=BACKUP_RETENTION):
    """Keep only the newest recovery snapshots and their known sidecars."""
    backups = []
    if os.path.isdir(backup_dir):
        for name in os.listdir(backup_dir):
            if (name.startswith('popping-before-restore-') and
                    name.endswith('.db')):
                path = os.path.join(backup_dir, name)
                if os.path.isfile(path):
                    backups.append(path)
    backups.sort(key=lambda path: (os.path.getmtime(path), os.path.basename(path)))
    for old_path in backups[:-keep]:
        for candidate in (old_path,) + tuple(
                old_path + suffix for suffix in DATABASE_SIDECARS):
            try:
                os.remove(candidate)
            except FileNotFoundError:
                pass


def remove_database_sidecars(db_path):
    """Remove only WAL sidecars belonging to the named database."""
    for suffix in DATABASE_SIDECARS:
        sidecar_path = db_path + suffix
        try:
            os.remove(sidecar_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(
                f"Could not remove SQLite sidecar {sidecar_path}: {exc}"
            ) from exc


def prepare_database_for_replacement(db_path, allow_unverified=False):
    """Checkpoint WAL and leave no stale sidecars before file replacement."""
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.execute('PRAGMA busy_timeout=5000')
            checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint and checkpoint[0]:
                raise RuntimeError(
                    "Could not checkpoint the live database. Confirm the web "
                    "service is fully stopped and try again"
                )
            journal_mode = conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
            if str(journal_mode).lower() != 'delete':
                raise RuntimeError(
                    "Could not leave WAL mode. Confirm the web service is fully "
                    "stopped and try again"
                )
        finally:
            conn.close()
    except RuntimeError:
        raise
    except sqlite3.Error as exc:
        error_code = getattr(exc, 'sqlite_errorcode', None)
        primary_error_code = (
            error_code & 0xFF if isinstance(error_code, int) else None
        )
        if (not allow_unverified or
                isinstance(exc, sqlite3.OperationalError) or
                primary_error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)):
            raise RuntimeError(
                "Could not prepare the live database for replacement. Confirm "
                "the web service is fully stopped and try again"
            ) from exc
        remove_database_sidecars(db_path)
        return False
    remove_database_sidecars(db_path)
    return True


def copy_raw_database_snapshot(live_db_path, backup_path):
    """Preserve a database and any sidecars without claiming validity."""
    sources = [live_db_path] + [
        live_db_path + suffix for suffix in DATABASE_SIDECARS
        if os.path.isfile(live_db_path + suffix)
    ]
    targets = [backup_path] + [
        backup_path + source[len(live_db_path):] for source in sources[1:]
    ]
    staged = []
    completed = []
    try:
        for source, target in zip(sources, targets):
            handle, temporary_path = tempfile.mkstemp(
                prefix='.popping-raw-', suffix='.tmp',
                dir=os.path.dirname(backup_path)
            )
            os.close(handle)
            shutil.copy2(source, temporary_path)
            staged.append((temporary_path, target))
        for temporary_path, target in staged:
            os.replace(temporary_path, target)
            completed.append(target)
        staged.clear()
    except Exception:
        for temporary_path, _target in staged:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass
        for target in completed:
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
        raise


def remove_snapshot_files(snapshot_path):
    """Remove one snapshot group after a verified backup supersedes it."""
    for candidate in (snapshot_path,) + tuple(
            snapshot_path + suffix for suffix in DATABASE_SIDECARS):
        try:
            os.remove(candidate)
        except FileNotFoundError:
            pass


def create_live_backup(live_db_path, expected_slug):
    backup_dir = os.path.join(os.path.dirname(live_db_path), 'restore-backups')
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    verified_path = os.path.join(
        backup_dir, f'popping-before-restore-{stamp}.db'
    )
    unverified_path = os.path.join(
        backup_dir, f'popping-before-restore-unverified-{stamp}.db'
    )
    handle, temporary_path = tempfile.mkstemp(
        prefix='.popping-backup-', suffix='.tmp.db', dir=backup_dir
    )
    os.close(handle)

    try:
        # Copy raw bytes first. Opening a corrupt WAL database can rebuild its
        # shared-memory sidecar, so preservation must precede any SQLite call.
        copy_raw_database_snapshot(live_db_path, unverified_path)
        try:
            sqlite_backup(live_db_path, temporary_path)
        except Exception as exc:
            prune_backups(backup_dir)
            return unverified_path, False, str(exc)

        try:
            validate_course_database(temporary_path, expected_slug)
        except Exception as exc:
            prune_backups(backup_dir)
            return unverified_path, False, str(exc)

        os.replace(temporary_path, verified_path)
        remove_snapshot_files(unverified_path)
        prune_backups(backup_dir)
        return verified_path, True, None
    finally:
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            "Usage: python3 restore-course-db.py "
            "<course_slug> <backup_db_path>"
        )
        return 1

    temporary_path = None
    try:
        slug = validate_slug(args[0].strip())
        source_path = os.path.abspath(os.path.expanduser(args[1]))
        data_dir = resolve_data_dir()
        db_dir = os.path.join(data_dir, slug)
        live_db_path = os.path.join(db_dir, 'popping.db')

        if not os.path.isfile(source_path):
            raise ValueError(f"Backup database not found: {source_path}")
        if not os.path.isfile(live_db_path):
            raise ValueError(f"Live database not found: {live_db_path}")
        if os.path.samefile(source_path, live_db_path):
            raise ValueError("Backup database must be different from the live database")

        print("=== Restore Course Database ===")
        print(f"Course: {slug}")
        print(f"Source: {source_path}")
        print(f"Live DB: {live_db_path}")
        print("")

        print("Validating backup database...")
        validate_course_database(source_path, slug)

        confirmation = input(
            f"Type {slug} to replace the live database: "
        ).strip()
        if confirmation != slug:
            print("Cancelled: course slug did not match.")
            return 1
        offline_confirmation = input(
            f"Type {OFFLINE_CONFIRMATION} to confirm all web workers are stopped: "
        ).strip()
        if offline_confirmation != OFFLINE_CONFIRMATION:
            print("Cancelled: web service stop was not confirmed.")
            return 1

        print("Backing up current live database...")
        current_backup_path, backup_verified, backup_error = create_live_backup(
            live_db_path, slug
        )
        if backup_verified:
            print(f"Verified current database backup: {current_backup_path}")
        else:
            print(
                "WARNING: The current live database could not be validated. "
                f"An unverified recovery snapshot was saved at {current_backup_path}"
            )
            if backup_error:
                print(f"Validation detail: {backup_error}")

        handle, temporary_path = tempfile.mkstemp(
            prefix='.popping-restore-', suffix='.tmp.db', dir=db_dir
        )
        os.close(handle)
        sqlite_backup(source_path, temporary_path)
        validate_course_database(temporary_path, slug)

        checkpointed = prepare_database_for_replacement(
            live_db_path, allow_unverified=not backup_verified
        )
        if not checkpointed:
            print(
                "WARNING: The unverified live database could not be checkpointed. "
                "Its raw files were preserved before stale sidecars were removed."
            )

        os.replace(temporary_path, live_db_path)
        temporary_path = None

        print("")
        print("=== Restore complete! ===")
        label = 'verified backup' if backup_verified else 'unverified snapshot'
        print(f"Previous live database {label}: {current_backup_path}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)


if __name__ == '__main__':
    sys.exit(main())
