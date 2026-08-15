import json
import os
import re
import sqlite3
import tempfile
import threading
from flask import g
import config
from versioning import (
    APP_VERSION,
    BASELINE_DATA_VERSION,
    BASELINE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SCHEMA_VERSION_HISTORY,
    parse_version,
    sqlite_versions_compatible,
    versions_compatible,
)

SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
SQLITE_BUSY_TIMEOUT_SECONDS = 8
SQLITE_BUSY_TIMEOUT_MS = SQLITE_BUSY_TIMEOUT_SECONDS * 1000
SQLITE_BUSY_RETRY_AFTER_SECONDS = 2

# Process-local cache: slugs whose schema has already been verified/migrated.
# Without this, ensure_schema() runs ~10 PRAGMA queries on every API call.
_schema_checked = set()
_schema_check_locks = {}
_schema_check_locks_guard = threading.Lock()


def _schema_lock_for(slug):
    with _schema_check_locks_guard:
        return _schema_check_locks.setdefault(slug, threading.Lock())


_VERSIONED_DATA_TABLES = (
    'teammate_thumbs',
    'presentation_ratings',
    'challenge_rounds',
    'challenge_ratings',
)
_SCHEMA_LEDGER_COLUMNS = {
    'id', 'schema_version', 'applied_by_app_version', 'applied_at'
}
_SCHEMA_MIGRATIONS = {}


def _register_version_functions(connection):
    """Register fail-closed version predicates on one SQLite connection."""
    try:
        connection.create_function(
            'popping_version_compatible',
            2,
            sqlite_versions_compatible,
            deterministic=True,
        )
    except TypeError:  # pragma: no cover - older supported SQLite bindings
        connection.create_function(
            'popping_version_compatible', 2, sqlite_versions_compatible
        )


def _schema_ledger_exists(db):
    return db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type = 'table' AND name = 'schema_migrations'"""
    ).fetchone() is not None


def _row_value(row, name, index):
    """Read either a sqlite3.Row/mapping field or a tuple position."""
    try:
        return row[name]
    except (TypeError, IndexError, KeyError):
        return row[index]


def _pragma_columns(db, table):
    return {
        _row_value(row, 'name', 1): row
        for row in db.execute(f'PRAGMA table_info({table})').fetchall()
    }


def _read_schema_ledger(db):
    """Return the latest recorded schema version, or None for a legacy DB.

    A present ledger is authoritative.  It must contain an exact ordered
    prefix of the schema versions known to this application.  The latest row
    may be older than the current schema so migration planning can decide
    whether an explicit upgrade path exists.
    """
    if not _schema_ledger_exists(db):
        return None

    columns = set(_pragma_columns(db, 'schema_migrations'))
    if not _SCHEMA_LEDGER_COLUMNS.issubset(columns):
        raise RuntimeError('Database schema migration ledger is malformed')

    rows = db.execute(
        """SELECT id, schema_version, applied_by_app_version
           FROM schema_migrations ORDER BY id"""
    ).fetchall()
    if not rows:
        raise RuntimeError('Database schema migration ledger is empty')

    current = parse_version(SCHEMA_VERSION)
    ledger_versions = []
    parsed_versions = []
    for row in rows:
        value = _row_value(row, 'schema_version', 1)
        app_version = _row_value(row, 'applied_by_app_version', 2)
        try:
            parsed = parse_version(value)
            parse_version(app_version)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                'Database schema migration ledger contains an invalid version'
            ) from exc
        if parsed[2] != 0:
            raise RuntimeError(
                'Database schema migration ledger contains a patch-level '
                'schema version'
            )
        ledger_versions.append(value)
        parsed_versions.append(parsed)

    if any(
        current_version <= previous_version
        for previous_version, current_version in zip(
            parsed_versions, parsed_versions[1:]
        )
    ):
        raise RuntimeError(
            'Database schema migration ledger is not strictly ordered'
        )

    expected = tuple(SCHEMA_VERSION_HISTORY[:len(ledger_versions)])
    if tuple(ledger_versions) != expected:
        mismatch = next(
            (
                value for index, value in enumerate(ledger_versions)
                if index >= len(SCHEMA_VERSION_HISTORY)
                or value != SCHEMA_VERSION_HISTORY[index]
            ),
            ledger_versions[-1],
        )
        relation = (
            'newer' if parse_version(mismatch) > current else 'unknown'
        )
        raise RuntimeError(
            f'Database schema version {mismatch} is {relation} and is not '
            f'supported by this application (supports {SCHEMA_VERSION})'
        )

    return ledger_versions[-1]


def inspect_schema_version(db, allow_unversioned=True):
    """Inspect the schema ledger without migrating or otherwise writing.

    Missing ledgers return None when ``allow_unversioned`` is true so the
    recognized pre-versioning baseline can reach ``ensure_schema``.  A present
    malformed, unknown, or newer ledger always raises ``RuntimeError``.
    """
    version = _read_schema_ledger(db)
    if version is None and not allow_unversioned:
        raise RuntimeError('Database schema migration ledger is missing')
    return version


def _schema_migration_plan(source_version):
    """Resolve every required migration before any schema mutation begins."""
    if not SCHEMA_VERSION_HISTORY or SCHEMA_VERSION_HISTORY[-1] != (
            SCHEMA_VERSION):
        raise RuntimeError('Application schema version history is invalid')
    try:
        source_index = SCHEMA_VERSION_HISTORY.index(source_version)
    except ValueError as exc:
        raise RuntimeError(
            f'Database schema version {source_version} is not supported'
        ) from exc

    plan = []
    remaining = SCHEMA_VERSION_HISTORY[source_index:]
    for source, target in zip(remaining, remaining[1:]):
        migration = _SCHEMA_MIGRATIONS.get((source, target))
        if migration is None:
            raise RuntimeError(
                f'Database schema version {source} cannot be migrated to '
                f'{target}: no migration path is registered'
            )
        plan.append((source, target, migration))
    return tuple(plan)


SCHEMA_MIGRATION_WINDOW_ERROR = (
    'Database schema upgrades must run between class sessions. End or fully '
    'reset the active session before deploying this schema version.'
)


def _validate_schema_migration_window(db, migration_plan):
    """Reject a schema-line upgrade while ephemeral session state is active."""
    if not migration_plan:
        return
    if not db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type = 'table' AND name = 'course_state'"""
    ).fetchone():
        return

    columns = set(_pragma_columns(db, 'course_state'))
    if 'phase' not in columns:
        raise RuntimeError('Database course_state schema is malformed')
    candidate_fields = (
        'phase',
        'session_started_at',
        'active_team_id',
        'active_question_id',
        'current_question',
        'current_discussion_key',
        'presentation_started_at',
        'presentation_created_at',
        'poll_active',
        'active_challenges_json',
    )
    selected = [field for field in candidate_fields if field in columns]
    rows = db.execute(
        f"SELECT {', '.join(selected)} FROM course_state"
    ).fetchall()
    active_fields = set(selected) - {'phase', 'active_challenges_json'}
    for row in rows:
        values = {
            field: _row_value(row, field, index)
            for index, field in enumerate(selected)
        }
        if values['phase'] not in ('setup', 'ended'):
            raise RuntimeError(SCHEMA_MIGRATION_WINDOW_ERROR)
        if any(values[field] not in (None, '', 0) for field in active_fields):
            raise RuntimeError(SCHEMA_MIGRATION_WINDOW_ERROR)

        raw_challenges = values.get('active_challenges_json')
        if raw_challenges not in (None, ''):
            try:
                active_challenges = json.loads(raw_challenges)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(SCHEMA_MIGRATION_WINDOW_ERROR) from exc
            if not isinstance(active_challenges, list) or active_challenges:
                raise RuntimeError(SCHEMA_MIGRATION_WINDOW_ERROR)


def validate_schema_compatibility(db, allow_unversioned=True):
    """Validate ledger, migration path, and a safe upgrade window.

    This helper is read-only.  It returns None only for the recognized
    pre-versioning baseline when ``allow_unversioned`` is true.
    """
    recorded = inspect_schema_version(db, allow_unversioned=allow_unversioned)
    migration_plan = _schema_migration_plan(
        recorded or BASELINE_SCHEMA_VERSION
    )
    _validate_schema_migration_window(db, migration_plan)
    return recorded


def _install_baseline_schema_ledger(db):
    db.execute('''CREATE TABLE schema_migrations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        schema_version TEXT NOT NULL UNIQUE,
        applied_by_app_version TEXT NOT NULL,
        applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    db.execute(
        """INSERT INTO schema_migrations
           (schema_version, applied_by_app_version) VALUES (?, ?)""",
        [BASELINE_SCHEMA_VERSION, APP_VERSION],
    )


def _ensure_data_version_column(db, table):
    columns = set(_pragma_columns(db, table))
    if 'data_version' not in columns:
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN data_version TEXT NOT NULL "
            f"DEFAULT '{BASELINE_DATA_VERSION}'"
        )


def _versionable_presentation_histories(db):
    """Return parsed legacy histories that can receive baseline provenance."""
    if 'presentation_history' not in _pragma_columns(db, 'course_state'):
        return []

    rows = db.execute(
        'SELECT id, presentation_history FROM course_state'
    ).fetchall()
    parsed_histories = []
    for row in rows:
        raw_history = _row_value(row, 'presentation_history', 1)
        if raw_history is None or not str(raw_history).strip():
            continue
        try:
            history = json.loads(raw_history)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                'Cannot version malformed presentation history'
            ) from exc
        if not isinstance(history, list):
            raise RuntimeError('Cannot version malformed presentation history')
        if any(not isinstance(item, dict) for item in history):
            raise RuntimeError('Cannot version malformed presentation history')
        parsed_histories.append((_row_value(row, 'id', 0), history))
    return parsed_histories


def validate_legacy_adoption_candidate(db):
    """Read-only preflight for a database without a schema ledger.

    Missing provenance columns are safe because baseline adoption adds them.
    A preexisting provenance column must already satisfy the same contract
    enforced after adoption, or the database would be advertised as ready and
    then fail on its first request.
    """
    if _schema_ledger_exists(db):
        raise RuntimeError(
            'Legacy adoption validation requires an unversioned database'
        )

    _versionable_presentation_histories(db)
    tables = {
        _row_value(row, 'name', 0)
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for table in _VERSIONED_DATA_TABLES:
        if table not in tables:
            continue
        version_column = _pragma_columns(db, table).get('data_version')
        if version_column is None:
            continue
        if _row_value(version_column, 'notnull', 3) != 1:
            raise RuntimeError(
                f'Database table {table} lacks required data-version metadata'
            )
        default = _row_value(version_column, 'dflt_value', 4)
        normalized_default = (
            default.strip("'\"") if isinstance(default, str) else None
        )
        if normalized_default != BASELINE_DATA_VERSION:
            raise RuntimeError(
                f'Database table {table} has an invalid data-version default'
            )
        if db.execute(
            f'SELECT 1 FROM {table} WHERE data_version IS NULL LIMIT 1'
        ).fetchone():
            raise RuntimeError(
                f'Database table {table} contains unversioned data'
            )


def _backfill_presentation_history_versions(db):
    for row_id, history in _versionable_presentation_histories(db):
        changed = False
        for item in history:
            if item.get('data_version') is None:
                item['data_version'] = BASELINE_DATA_VERSION
                changed = True
        if changed:
            db.execute(
                'UPDATE course_state SET presentation_history = ? WHERE id = ?',
                [json.dumps(history), row_id],
            )


def validate_data_version_schema(db):
    """Read-only validation of durable row-provenance columns.

    The fixed v1.0 baseline keeps its required ``1.0.0`` default for legacy
    adoption.  Later schema series may remove the default, which forces every
    insert to stamp provenance explicitly, or use a default from their own
    compatible series.  A later schema can never retain the v1.0 fallback and
    silently mislabel omitted inserts.
    """
    recorded_schema = (
        inspect_schema_version(db, allow_unversioned=True)
        or BASELINE_SCHEMA_VERSION
    )
    uses_baseline_default = recorded_schema == BASELINE_SCHEMA_VERSION

    for table in _VERSIONED_DATA_TABLES:
        columns = _pragma_columns(db, table)
        version_column = columns.get('data_version')
        if version_column is None or _row_value(
                version_column, 'notnull', 3) != 1:
            raise RuntimeError(
                f'Database table {table} lacks required data-version metadata'
            )

        default = _row_value(version_column, 'dflt_value', 4)
        if default is None:
            if uses_baseline_default:
                raise RuntimeError(
                    f'Database table {table} has an invalid data-version '
                    'default'
                )
        else:
            normalized_default = (
                default.strip("'\"") if isinstance(default, str) else None
            )
            try:
                default_is_valid = (
                    normalized_default == BASELINE_DATA_VERSION
                    if uses_baseline_default
                    else versions_compatible(
                        normalized_default, recorded_schema
                    )
                )
            except (TypeError, ValueError):
                default_is_valid = False
            if not default_is_valid:
                raise RuntimeError(
                    f'Database table {table} has an invalid data-version '
                    'default'
                )

        if db.execute(
            f'SELECT 1 FROM {table} WHERE data_version IS NULL LIMIT 1'
        ).fetchone():
            raise RuntimeError(
                f'Database table {table} contains unversioned data'
            )


def _apply_schema_migration_plan(db, migration_plan):
    for _source, target, migration in migration_plan:
        migration(db)
        db.execute(
            """INSERT INTO schema_migrations
               (schema_version, applied_by_app_version) VALUES (?, ?)""",
            [target, APP_VERSION],
        )


def upgrade_schema_connection(db):
    """Upgrade a baseline-initialized connection to the current schema.

    Fresh databases are always created from the fixed SQL baseline.  This
    helper derives the complete ledger prefix from registered migrations and
    applies it atomically.  A missing ledger or migration path is rejected
    before any upgrade statement runs.
    """
    recorded = validate_schema_compatibility(db, allow_unversioned=False)
    validate_data_version_schema(db)
    migration_plan = _schema_migration_plan(recorded)
    if not migration_plan:
        return recorded

    savepoint = 'popping_schema_upgrade'
    db.execute(f'SAVEPOINT {savepoint}')
    try:
        _apply_schema_migration_plan(db, migration_plan)
        validate_data_version_schema(db)
        if inspect_schema_version(db, allow_unversioned=False) != (
                SCHEMA_VERSION):
            raise RuntimeError('Schema upgrade did not reach the current version')
    except Exception:
        db.execute(f'ROLLBACK TO {savepoint}')
        db.execute(f'RELEASE {savepoint}')
        raise
    db.execute(f'RELEASE {savepoint}')
    return SCHEMA_VERSION


def is_sqlite_busy_error(error):
    """Return whether an OperationalError represents lock contention."""
    error_code = getattr(error, 'sqlite_errorcode', None)
    primary_error_code = (
        error_code & 0xFF if isinstance(error_code, int) else None
    )
    if primary_error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
        return True
    message = str(error).casefold()
    return ('database is locked' in message or
            'database table is locked' in message)


def validate_slug(slug):
    if not isinstance(slug, str) or not SLUG_RE.fullmatch(slug):
        raise RuntimeError(f"Invalid course slug: {slug!r}")
    return slug


def get_db(slug):
    slug = validate_slug(slug)
    db_key = f'db_{slug}'
    if not hasattr(g, db_key):
        db_path = os.path.join(config.DATA_DIR, slug, 'popping.db')
        if not os.path.exists(db_path):
            raise RuntimeError(f"Database not found for course: {slug}")
        # Bound each ordinary SQLite lock wait below the browser's 15-second
        # request deadline. Persistent contention can then surface as a
        # retryable 503 instead of one 30-second lock wait.
        conn = sqlite3.connect(db_path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        _register_version_functions(conn)
        # WAL mode persists. Check before requesting it because repeating the
        # write form of this pragma on concurrent requests can itself need a
        # database lock. A busy connection can retry the upgrade later.
        conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(journal_mode).lower() != "wal":
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if not is_sqlite_busy_error(exc):
                    conn.close()
                    raise
        setattr(g, db_key, conn)
    return getattr(g, db_key)


def close_db(e=None):
    for key in list(vars(g).keys()):
        if key.startswith('db_'):
            db = getattr(g, key, None)
            if db is not None:
                db.close()
                delattr(g, key)


def init_db(slug):
    """Build and atomically publish a fresh database for one course."""
    slug = validate_slug(slug)
    course_dir = os.path.join(config.DATA_DIR, slug)
    os.makedirs(course_dir, exist_ok=True)
    db_path = os.path.join(course_dir, 'popping.db')
    temporary_fd, temporary_path = tempfile.mkstemp(
        prefix='.popping-init-', suffix='.tmp.db', dir=course_dir
    )
    os.close(temporary_fd)
    try:
        conn = sqlite3.connect(temporary_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            with open(config.DATABASE_SCHEMA, 'r') as f:
                conn.executescript(f.read())
            upgrade_schema_connection(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        os.replace(temporary_path, db_path)
    finally:
        for candidate in (
            temporary_path,
            temporary_path + '-wal',
            temporary_path + '-shm',
        ):
            try:
                os.remove(candidate)
            except FileNotFoundError:
                pass


def init_app(app):
    app.teardown_appcontext(close_db)


def ensure_schema(slug):
    """Run idempotent migrations once per process and safely across workers."""
    if slug in _schema_checked:
        return
    with _schema_lock_for(slug):
        if slug in _schema_checked:
            return
        db = get_db(slug)
        db.execute('BEGIN IMMEDIATE')
        try:
            _ensure_schema_locked(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        _schema_checked.add(slug)


def forget_schema(slug):
    """Forget a removed short-lived database from the process cache."""
    _schema_checked.discard(slug)
    with _schema_check_locks_guard:
        _schema_check_locks.pop(slug, None)


def _ensure_schema_locked(db):
    """Add missing columns/tables to existing databases (migration).
    Runs only once per slug per process; subsequent calls are a no-op."""
    recorded_schema_version = validate_schema_compatibility(db)
    adopting_baseline = recorded_schema_version is None
    migration_plan = _schema_migration_plan(
        recorded_schema_version or BASELINE_SCHEMA_VERSION
    )
    if adopting_baseline:
        validate_legacy_adoption_candidate(db)

    # course_state columns
    cs_cols = [row['name'] for row in db.execute('PRAGMA table_info(course_state)').fetchall()]
    if 'max_teams' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN max_teams INTEGER DEFAULT 6')
    if 'max_members_per_team' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN max_members_per_team INTEGER DEFAULT 10')
    if 'teams_locked' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN teams_locked INTEGER DEFAULT 0')
    if 'discussion_week' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN discussion_week INTEGER DEFAULT 1')
    if 'session_started_at' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN session_started_at TIMESTAMP')
    if 'presentation_time_cap' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN presentation_time_cap INTEGER DEFAULT 300')
    if 'presentation_remaining' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN presentation_remaining INTEGER')
    if 'presentation_created_at' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN presentation_created_at TIMESTAMP')
    if 'poll_active' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_active INTEGER DEFAULT 0')
    if 'poll_question_key' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_question_key TEXT')
    if 'poll_started_at' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_started_at TIMESTAMP')
    if 'poll_closed_at' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_closed_at TIMESTAMP')
    if 'challenge_ratings_closed_at' not in cs_cols:
        db.execute(
            'ALTER TABLE course_state '
            'ADD COLUMN challenge_ratings_closed_at TIMESTAMP'
        )
    if 'presentation_history' not in cs_cols:
        db.execute("ALTER TABLE course_state ADD COLUMN presentation_history TEXT DEFAULT '[]'")
    if 'roster_version' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN roster_version INTEGER DEFAULT 0')
    if 'session_key' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN session_key INTEGER DEFAULT 0')
    if 'current_discussion_key' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN current_discussion_key TEXT')
    if 'current_discussion_source_key' not in cs_cols:
        db.execute(
            'ALTER TABLE course_state ADD COLUMN current_discussion_source_key TEXT'
        )
    db.execute(
        '''UPDATE course_state
           SET current_discussion_source_key = current_discussion_key
           WHERE current_discussion_source_key IS NULL
             AND current_discussion_key IS NOT NULL'''
    )
    if 'current_discussion_title' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN current_discussion_title TEXT')
    if 'current_discussion_content' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN current_discussion_content TEXT')
    if 'state_version' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN state_version INTEGER DEFAULT 0')
    if 'discussion_questions_version' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN discussion_questions_version INTEGER DEFAULT 0')
    if 'active_challenges_json' not in cs_cols:
        db.execute("ALTER TABLE course_state ADD COLUMN active_challenges_json TEXT DEFAULT '[]'")
    # Auto-bump state_version on every course_state UPDATE so students polling
    # with ?since=<version> learn about any mutation within one poll. The WHEN
    # guard keeps it loop-free even if recursive_triggers is enabled.
    db.execute(
        '''CREATE TRIGGER IF NOT EXISTS course_state_bump_version
             AFTER UPDATE ON course_state
             WHEN NEW.state_version = OLD.state_version
           BEGIN
               UPDATE course_state
                   SET state_version = OLD.state_version + 1
                   WHERE id = NEW.id;
           END'''
    )

    # Discussion-phase question visibility overlay (hide/show per question).
    db.execute('''CREATE TABLE IF NOT EXISTS hidden_discussion_questions (
        course_id INTEGER NOT NULL,
        week_num INTEGER NOT NULL,
        question_key TEXT NOT NULL,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        PRIMARY KEY (course_id, week_num, question_key)
    )''')

    # Challenge feature: one row per challenger selected during a presentation.
    db.execute('''CREATE TABLE IF NOT EXISTS challenge_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        session_key INTEGER NOT NULL,
        week_num INTEGER,
        presentation_key TEXT NOT NULL,
        challenge_key TEXT NOT NULL UNIQUE,
        challenge_num INTEGER NOT NULL,
        challenger_id INTEGER NOT NULL,
        challenger_name TEXT,
        challenger_team_id INTEGER,
        challenger_team_name TEXT,
        presenting_team_id INTEGER,
        presenting_team_name TEXT,
        question_id INTEGER,
        question_title TEXT,
        data_version TEXT NOT NULL DEFAULT '1.0.0',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        FOREIGN KEY (challenger_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (challenger_team_id) REFERENCES teams (id) ON DELETE SET NULL,
        FOREIGN KEY (presenting_team_id) REFERENCES teams (id) ON DELETE SET NULL,
        UNIQUE(course_id, presentation_key, challenge_num)
    )''')

    # Challenge feature: ephemeral raised hands wanting to challenge.
    db.execute('''CREATE TABLE IF NOT EXISTS challenge_hands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        session_key INTEGER NOT NULL,
        presentation_key TEXT NOT NULL,
        student_id INTEGER NOT NULL,
        student_name TEXT,
        student_team_id INTEGER,
        student_team_name TEXT,
        raised_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
        UNIQUE(course_id, presentation_key, student_id)
    )''')

    # Challenge feature: 1-5 peer ratings of challenger question quality.
    db.execute('''CREATE TABLE IF NOT EXISTS challenge_ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        session_key INTEGER NOT NULL,
        week_num INTEGER,
        challenge_key TEXT NOT NULL,
        presentation_key TEXT NOT NULL,
        challenger_id INTEGER NOT NULL,
        challenger_name TEXT,
        challenger_team_id INTEGER,
        challenger_team_name TEXT,
        rater_id INTEGER NOT NULL,
        rater_name TEXT,
        rater_team_id INTEGER,
        rater_team_name TEXT,
        data_version TEXT NOT NULL DEFAULT '1.0.0',
        score INTEGER CHECK(score >= 1 AND score <= 5),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        FOREIGN KEY (challenger_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (rater_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (challenger_team_id) REFERENCES teams (id) ON DELETE SET NULL,
        FOREIGN KEY (rater_team_id) REFERENCES teams (id) ON DELETE SET NULL,
        UNIQUE(course_id, challenge_key, rater_id)
    )''')

    db.execute('''CREATE INDEX IF NOT EXISTS idx_challenge_rounds_pres
                  ON challenge_rounds(course_id, presentation_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_challenge_rounds_session
                  ON challenge_rounds(course_id, session_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_challenge_hands_pres
                  ON challenge_hands(course_id, presentation_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_challenge_ratings_challenge
                  ON challenge_ratings(course_id, challenge_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_challenge_ratings_session
                  ON challenge_ratings(course_id, session_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_challenge_ratings_export_week
                  ON challenge_ratings(course_id, week_num)''')

    # questions columns
    q_cols = [row['name'] for row in db.execute('PRAGMA table_info(questions)').fetchall()]
    if 'title' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN title TEXT')
    if 'content' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN content TEXT')
    if 'week_num' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN week_num INTEGER DEFAULT 1')
    if 'source_key' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN source_key TEXT')
    db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_course_source
                  ON questions(course_id, source_key)''')

    # students columns
    st_cols = [row['name'] for row in db.execute('PRAGMA table_info(students)').fetchall()]
    if 'last_login_at' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_login_at TIMESTAMP')
    if 'last_team_joined_at' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_team_joined_at TIMESTAMP')
    if 'last_team_id' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_team_id INTEGER')
    db.execute(
        '''UPDATE students SET last_team_id = team_id
           WHERE last_team_id IS NULL AND team_id IS NOT NULL'''
    )
    if 'last_active_at' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_active_at TIMESTAMP')
    if 'is_active' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1')

    db.execute('''CREATE TABLE IF NOT EXISTS login_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        login_type TEXT NOT NULL CHECK(login_type IN ('student', 'instructor')),
        principal TEXT NOT NULL,
        client_hash TEXT NOT NULL,
        failed_count INTEGER NOT NULL DEFAULT 0,
        window_started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        blocked_until TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        UNIQUE(course_id, login_type, principal, client_hash)
    )''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_login_attempts_client
                  ON login_attempts(course_id, login_type, client_hash, window_started_at)''')

    # New tables for existing DBs
    db.execute('''CREATE TABLE IF NOT EXISTS presentation_ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        question_key TEXT NOT NULL,
        q1_developed INTEGER CHECK(q1_developed >= 1 AND q1_developed <= 5),
        q2_easy INTEGER CHECK(q2_easy >= 1 AND q2_easy <= 5),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id),
        FOREIGN KEY (student_id) REFERENCES students (id),
        UNIQUE(course_id, student_id, question_key)
    )''')

    # Snapshot presentation attribution on each rating.  IDs support joins;
    # names/titles keep exports meaningful if a team or question is renamed.
    rating_cols = [row['name'] for row in db.execute(
        'PRAGMA table_info(presentation_ratings)'
    ).fetchall()]
    for column, definition in (
        ('session_key', 'INTEGER DEFAULT 0'),
        ('week_num', 'INTEGER'),
        ('presenting_team_id', 'INTEGER'),
        ('presenting_team_name', 'TEXT'),
        ('question_id', 'INTEGER'),
        ('question_title', 'TEXT'),
        ('rater_team_id', 'INTEGER'),
        ('rater_team_name', 'TEXT'),
    ):
        if column not in rating_cols:
            db.execute(
                f'ALTER TABLE presentation_ratings ADD COLUMN {column} {definition}'
            )

    db.execute('''CREATE TABLE IF NOT EXISTS teammate_thumbs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        session_key INTEGER NOT NULL,
        week_num INTEGER,
        question_key TEXT NOT NULL,
        source_question_key TEXT,
        question_title TEXT,
        grader_id INTEGER NOT NULL,
        recipient_id INTEGER NOT NULL,
        grader_team_id INTEGER,
        grader_team_name TEXT,
        recipient_team_id INTEGER,
        recipient_team_name TEXT,
        data_version TEXT NOT NULL DEFAULT '1.0.0',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        FOREIGN KEY (grader_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (recipient_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (grader_team_id) REFERENCES teams (id) ON DELETE SET NULL,
        FOREIGN KEY (recipient_team_id) REFERENCES teams (id) ON DELETE SET NULL,
        UNIQUE(course_id, session_key, question_key, grader_id, recipient_id)
    )''')
    thumb_cols = [row['name'] for row in db.execute(
        'PRAGMA table_info(teammate_thumbs)'
    ).fetchall()]
    for column, definition in (
        ('week_num', 'INTEGER'),
        ('source_question_key', 'TEXT'),
        ('question_title', 'TEXT'),
        ('grader_team_id', 'INTEGER'),
        ('grader_team_name', 'TEXT'),
        ('recipient_team_id', 'INTEGER'),
        ('recipient_team_name', 'TEXT'),
    ):
        if column not in thumb_cols:
            db.execute(
                f'ALTER TABLE teammate_thumbs ADD COLUMN {column} {definition}'
            )
    for table in _VERSIONED_DATA_TABLES:
        _ensure_data_version_column(db, table)

    db.execute(
        '''UPDATE teammate_thumbs SET source_question_key = question_key
           WHERE source_question_key IS NULL'''
    )
    db.execute(
        '''UPDATE presentation_ratings
           SET week_num = (
               SELECT COALESCE(q.week_num, 1)
               FROM questions q
               WHERE q.id = presentation_ratings.question_id
                 AND q.course_id = presentation_ratings.course_id
           )
           WHERE week_num IS NULL AND question_id IS NOT NULL'''
    )
    unknown_thumb_weeks = db.execute(
        '''SELECT id, source_question_key FROM teammate_thumbs
           WHERE week_num IS NULL'''
    ).fetchall()
    for thumb in unknown_thumb_weeks:
        match = re.match(r'^week-(\d+)-', thumb['source_question_key'] or '')
        if match:
            db.execute(
                'UPDATE teammate_thumbs SET week_num = ? WHERE id = ?',
                [int(match.group(1)), thumb['id']],
            )
    db.execute('''CREATE INDEX IF NOT EXISTS idx_thumbs_current
                  ON teammate_thumbs(course_id, session_key, question_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_thumbs_export_week
                  ON teammate_thumbs(course_id, week_num)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_ratings_presentation
                  ON presentation_ratings(course_id, question_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_ratings_session
                  ON presentation_ratings(course_id, session_key)''')
    db.execute('''CREATE INDEX IF NOT EXISTS idx_ratings_export_week
                  ON presentation_ratings(course_id, week_num)''')

    # Preserve thumbs from databases created before teammate_thumbs replaced
    # peer_reviews. The standard legacy schema had no lecture week, so those
    # rows remain explicitly unknown-week (week_num NULL) and must not appear
    # in a normal week-specific export. A few transitional databases may have
    # an explicit positive INTEGER week_num; that is the only week evidence
    # reliable enough to retain here.
    has_peer_reviews = db.execute(
        '''SELECT 1 FROM sqlite_master
           WHERE type = 'table' AND name = 'peer_reviews' '''
    ).fetchone()
    if has_peer_reviews:
        peer_review_cols = {
            row['name'] for row in db.execute(
                'PRAGMA table_info(peer_reviews)'
            ).fetchall()
        }
        legacy_week_sql = (
            '''CASE WHEN typeof(week_num) = 'integer' AND week_num > 0
                    THEN week_num ELSE NULL END'''
            if 'week_num' in peer_review_cols else 'NULL'
        )
        db.execute(
            f'''INSERT INTO teammate_thumbs
                (course_id, session_key, week_num, question_key,
                 source_question_key, grader_id, recipient_id,
                 created_at, updated_at, data_version)
                SELECT course_id, 0, {legacy_week_sql}, 'legacy', 'legacy',
                       grader_id, recipient_id, created_at, created_at, ?
                FROM peer_reviews WHERE score > 0
                ON CONFLICT(course_id, session_key, question_key,
                            grader_id, recipient_id)
                DO UPDATE SET
                    week_num = COALESCE(
                        excluded.week_num, teammate_thumbs.week_num
                    ),
                    source_question_key = 'legacy' ''',
            [BASELINE_DATA_VERSION],
        )

    if adopting_baseline:
        _backfill_presentation_history_versions(db)
    validate_data_version_schema(db)
    if adopting_baseline:
        _install_baseline_schema_ledger(db)
    _apply_schema_migration_plan(db, migration_plan)
    if migration_plan:
        validate_data_version_schema(db)


def migrate_schema_connection(db):
    """Adopt or migrate an existing DB within the caller's transaction."""
    _ensure_schema_locked(db)


def get_max_teams(slug, course_id):
    """Get max_teams for a course, with fallback for old databases."""
    ensure_schema(slug)
    state = query_db(slug, 'SELECT max_teams FROM course_state WHERE course_id = ?', [course_id], one=True)
    if state and state['max_teams'] is not None:
        return state['max_teams']
    total = query_db(slug, 'SELECT COUNT(*) as c FROM teams WHERE course_id = ?', [course_id], one=True)
    return min(total['c'], 6) if total else 6


def get_max_members_per_team(slug, course_id):
    """Get max_members_per_team for a course."""
    ensure_schema(slug)
    state = query_db(slug, 'SELECT max_members_per_team FROM course_state WHERE course_id = ?', [course_id], one=True)
    if state and state['max_members_per_team'] is not None:
        return state['max_members_per_team']
    return 10

def query_db(slug, query, args=(), one=False):
    cur = get_db(slug).execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(slug, query, args=()):
    db = get_db(slug)
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid
