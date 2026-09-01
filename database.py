import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
from fractions import Fraction
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

# Process-local cache: slugs whose current schema has already been verified.
# Without this, ensure_schema() runs several PRAGMA queries on every API call.
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
    'presentation_participants',
    'weekly_hero_summaries',
)
_PARTICIPATION_SCHEMA_VERSION = '1.1.0'
_STUDENT_PROFILE_SCHEMA_VERSION = '1.2.0'
_WEEKLY_HERO_SCHEMA_VERSION = '1.3.0'
WEEKLY_HERO_CALCULATION_VERSION = '1.0.0'
_VERSIONED_DATA_TABLE_INTRODUCED = {
    'teammate_thumbs': BASELINE_SCHEMA_VERSION,
    'presentation_ratings': BASELINE_SCHEMA_VERSION,
    'challenge_rounds': BASELINE_SCHEMA_VERSION,
    'challenge_ratings': BASELINE_SCHEMA_VERSION,
    'presentation_participants': _PARTICIPATION_SCHEMA_VERSION,
    'weekly_hero_summaries': _WEEKLY_HERO_SCHEMA_VERSION,
}
_SCHEMA_LEDGER_COLUMNS = {
    'id', 'schema_version', 'applied_by_app_version', 'applied_at'
}
_SCHEMA_MIGRATIONS = {}

SCHEMA_MIGRATION_REQUIRED_ERROR = (
    'This course database requires an offline schema migration before this '
    'website version can use it.'
)

_MIGRATION_SESSION_ACTIVITY_TABLES = (
    'teammate_thumbs',
    'presentation_ratings',
    'challenge_rounds',
    'challenge_ratings',
    'presentation_participants',
)

_PARTICIPATION_REQUIRED_COLUMNS = {
    'teammate_thumbs': {
        'id', 'course_id', 'session_key', 'week_num', 'question_key',
        'source_question_key', 'question_title', 'grader_id', 'recipient_id',
        'grader_team_id', 'grader_team_name', 'recipient_team_id',
        'recipient_team_name', 'data_version', 'created_at', 'updated_at',
    },
    'presentation_ratings': {
        'id', 'course_id', 'student_id', 'question_key', 'session_key',
        'week_num', 'presenting_team_id', 'presenting_team_name',
        'question_id', 'question_title', 'rater_team_id', 'rater_team_name',
        'data_version', 'q1_developed', 'q2_easy', 'created_at',
    },
    'challenge_rounds': {
        'id', 'course_id', 'session_key', 'week_num', 'presentation_key',
        'challenge_key', 'challenge_num', 'challenger_id', 'challenger_name',
        'challenger_team_id', 'challenger_team_name', 'presenting_team_id',
        'presenting_team_name', 'question_id', 'question_title',
        'data_version', 'created_at',
    },
    'challenge_ratings': {
        'id', 'course_id', 'session_key', 'week_num', 'challenge_key',
        'presentation_key', 'challenger_id', 'challenger_name',
        'challenger_team_id', 'challenger_team_name', 'rater_id',
        'rater_name', 'rater_team_id', 'rater_team_name', 'data_version',
        'score', 'created_at',
    },
    'presentation_participants': {
        'id', 'course_id', 'session_key', 'week_num', 'presentation_key',
        'student_id', 'student_identifier', 'student_name', 'team_id',
        'team_name', 'data_version', 'created_at',
    },
}

_PARTICIPATION_REQUIRED_UNIQUE_KEYS = {
    'teammate_thumbs': {
        ('course_id', 'session_key', 'question_key', 'grader_id',
         'recipient_id'),
    },
    'presentation_ratings': {
        ('course_id', 'student_id', 'question_key'),
    },
    'challenge_rounds': {
        ('challenge_key',),
        ('course_id', 'presentation_key', 'challenge_num'),
    },
    'challenge_ratings': {
        ('course_id', 'challenge_key', 'rater_id'),
    },
    'presentation_participants': {
        ('course_id', 'presentation_key', 'student_id'),
    },
}

_PARTICIPATION_REQUIRED_FOREIGN_KEYS = {
    'teammate_thumbs': {
        ('course_id', 'courses', 'id'),
        ('grader_id', 'students', 'id'),
        ('recipient_id', 'students', 'id'),
        ('grader_team_id', 'teams', 'id'),
        ('recipient_team_id', 'teams', 'id'),
    },
    'presentation_ratings': {
        ('course_id', 'courses', 'id'),
        ('student_id', 'students', 'id'),
    },
    'challenge_rounds': {
        ('course_id', 'courses', 'id'),
        ('challenger_id', 'students', 'id'),
        ('challenger_team_id', 'teams', 'id'),
        ('presenting_team_id', 'teams', 'id'),
    },
    'challenge_ratings': {
        ('course_id', 'courses', 'id'),
        ('challenger_id', 'students', 'id'),
        ('rater_id', 'students', 'id'),
        ('challenger_team_id', 'teams', 'id'),
        ('rater_team_id', 'teams', 'id'),
    },
    'presentation_participants': {
        ('course_id', 'courses', 'id'),
        ('student_id', 'students', 'id'),
        ('team_id', 'teams', 'id'),
    },
}

_PARTICIPATION_REQUIRED_INDEXES = {
    'idx_thumbs_current': (
        'teammate_thumbs', ('course_id', 'session_key', 'question_key')
    ),
    'idx_thumbs_export_week': (
        'teammate_thumbs', ('course_id', 'week_num')
    ),
    'idx_ratings_presentation': (
        'presentation_ratings', ('course_id', 'question_key')
    ),
    'idx_ratings_session': (
        'presentation_ratings', ('course_id', 'session_key')
    ),
    'idx_ratings_export_week': (
        'presentation_ratings', ('course_id', 'week_num')
    ),
    'idx_challenge_rounds_pres': (
        'challenge_rounds', ('course_id', 'presentation_key')
    ),
    'idx_challenge_rounds_session': (
        'challenge_rounds', ('course_id', 'session_key')
    ),
    'idx_challenge_rounds_challenger': (
        'challenge_rounds', ('course_id', 'challenger_id')
    ),
    'idx_challenge_ratings_challenge': (
        'challenge_ratings', ('course_id', 'challenge_key')
    ),
    'idx_challenge_ratings_session': (
        'challenge_ratings', ('course_id', 'session_key')
    ),
    'idx_challenge_ratings_export_week': (
        'challenge_ratings', ('course_id', 'week_num')
    ),
    'idx_presentation_participants_student': (
        'presentation_participants', ('course_id', 'student_id')
    ),
    'idx_presentation_participants_session': (
        'presentation_participants', ('course_id', 'session_key')
    ),
    'idx_presentation_participants_export_week': (
        'presentation_participants', ('course_id', 'week_num')
    ),
}

_WEEKLY_HERO_REQUIRED_COLUMNS = {
    'weekly_hero_summaries': {
        'id', 'course_id', 'week_num', 'calculation_version',
        'source_schema_version', 'source_data_versions',
        'source_fingerprint', 'source_presentation_rating_count',
        'source_challenge_rating_count', 'source_participant_count',
        'source_history_item_count', 'data_version', 'calculated_at',
    },
    'weekly_hero_results': {
        'id', 'summary_id', 'result_key', 'category', 'award_type', 'rank',
        'score_sum', 'score_count', 'rating_count',
        'developed_score_sum', 'developed_score_count', 'easy_score_sum',
        'easy_score_count', 'team_id', 'team_name', 'challenger_id',
        'challenger_identifier', 'challenger_name', 'created_at',
    },
    'weekly_hero_recipients': {
        'id', 'result_id', 'recipient_key', 'student_id',
        'student_identifier', 'student_name', 'team_id', 'team_name',
        'created_at',
    },
}

_WEEKLY_HERO_REQUIRED_UNIQUE_KEYS = {
    'weekly_hero_summaries': {('course_id', 'week_num')},
    'weekly_hero_results': {('summary_id', 'result_key')},
    'weekly_hero_recipients': {('result_id', 'recipient_key')},
}

_WEEKLY_HERO_REQUIRED_FOREIGN_KEYS = {
    'weekly_hero_summaries': {('course_id', 'courses', 'id')},
    'weekly_hero_results': {
        ('summary_id', 'weekly_hero_summaries', 'id'),
        ('team_id', 'teams', 'id'),
        ('challenger_id', 'students', 'id'),
    },
    'weekly_hero_recipients': {
        ('result_id', 'weekly_hero_results', 'id'),
        ('student_id', 'students', 'id'),
        ('team_id', 'teams', 'id'),
    },
}

_WEEKLY_HERO_REQUIRED_INDEXES = {
    'idx_weekly_hero_results_summary_rank': (
        'weekly_hero_results', ('summary_id', 'category', 'rank')
    ),
    'idx_weekly_hero_recipients_student': (
        'weekly_hero_recipients', ('student_id', 'result_id')
    ),
}


def _versioned_data_tables_for(schema_version):
    target = parse_version(schema_version)
    return tuple(
        table for table in _VERSIONED_DATA_TABLES
        if parse_version(_VERSIONED_DATA_TABLE_INTRODUCED.get(
            table, BASELINE_SCHEMA_VERSION
        )) <= target
    )


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


def _history_has_current_session_activity(raw_history, session_key):
    if raw_history is None or not str(raw_history).strip():
        return False
    try:
        history = json.loads(raw_history)
    except (TypeError, ValueError):
        return True
    if not isinstance(history, list):
        return True
    for item in history:
        if not isinstance(item, dict):
            return True
        item_session = item.get('session_key')
        if item_session is None:
            if session_key == 0:
                return True
            continue
        try:
            item_session = int(item_session)
        except (TypeError, ValueError):
            return True
        if item_session == session_key:
            return True
    return False


def _setup_has_current_session_activity(
        db, course_id, session_key, raw_history):
    """Fail closed when Setup still contains durable current-session work."""
    session_key = session_key or 0
    if _history_has_current_session_activity(raw_history, session_key):
        return True

    tables = {
        _row_value(row, 'name', 0)
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for table in _MIGRATION_SESSION_ACTIVITY_TABLES:
        if table not in tables:
            continue
        columns = set(_pragma_columns(db, table))
        if 'course_id' not in columns:
            return True
        if 'session_key' not in columns:
            if session_key == 0 and db.execute(
                f'SELECT 1 FROM {table} WHERE course_id = ? LIMIT 1',
                [course_id],
            ).fetchone():
                return True
            continue
        if db.execute(
            f'''SELECT 1 FROM {table}
                WHERE course_id = ?
                  AND (session_key = ? OR session_key IS NULL
                       OR typeof(session_key) != 'integer')
                LIMIT 1''',
            [course_id, session_key],
        ).fetchone():
            return True

    # The oldest supported databases may contain only peer_reviews. Such rows
    # have no session key, so they can belong to the current session only while
    # the course itself is still on the baseline session key.
    if session_key == 0 and 'peer_reviews' in tables:
        peer_columns = set(_pragma_columns(db, 'peer_reviews'))
        if 'course_id' not in peer_columns:
            return True
        if db.execute(
            'SELECT 1 FROM peer_reviews WHERE course_id = ? LIMIT 1',
            [course_id],
        ).fetchone():
            return True
    return False


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
    if not {'course_id', 'phase'}.issubset(columns):
        raise RuntimeError('Database course_state schema is malformed')
    candidate_fields = (
        'course_id',
        'phase',
        'session_key',
        'presentation_history',
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
    active_fields = set(selected) - {
        'course_id', 'phase', 'session_key', 'presentation_history',
        'active_challenges_json',
    }
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

        if values['phase'] == 'setup' and (
            _setup_has_current_session_activity(
                db,
                values['course_id'],
                values.get('session_key') or 0,
                values.get('presentation_history'),
            )
        ):
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
    for table in _versioned_data_tables_for(BASELINE_SCHEMA_VERSION):
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

    for table in _versioned_data_tables_for(recorded_schema):
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


def _quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _table_index_signatures(db, table):
    signatures = set()
    for index in db.execute(
            f'PRAGMA index_list({_quote_identifier(table)})').fetchall():
        if _row_value(index, 'partial', 4):
            continue
        index_name = _row_value(index, 'name', 1)
        columns = tuple(
            _row_value(row, 'name', 2)
            for row in db.execute(
                f'PRAGMA index_info({_quote_identifier(index_name)})'
            ).fetchall()
        )
        signatures.add((columns, bool(_row_value(index, 'unique', 2))))
    return signatures


def _table_foreign_key_signatures(db, table):
    return {
        (
            _row_value(row, 'from', 3),
            _row_value(row, 'table', 2),
            _row_value(row, 'to', 4),
        )
        for row in db.execute(
            f'PRAGMA foreign_key_list({_quote_identifier(table)})'
        ).fetchall()
    }


def _ensure_participation_indexes(db, repair_indexes):
    for index_name, (table, expected_columns) in (
            _PARTICIPATION_REQUIRED_INDEXES.items()):
        index_row = db.execute(
            """SELECT tbl_name FROM sqlite_master
               WHERE type = 'index' AND name = ?""",
            [index_name],
        ).fetchone()
        if index_row is None:
            if not repair_indexes:
                raise RuntimeError(
                    f'Database is missing required index {index_name}'
                )
            columns_sql = ', '.join(
                _quote_identifier(column) for column in expected_columns
            )
            db.execute(
                f'CREATE INDEX {_quote_identifier(index_name)} '
                f'ON {_quote_identifier(table)} ({columns_sql})'
            )
            index_row = db.execute(
                """SELECT tbl_name FROM sqlite_master
                   WHERE type = 'index' AND name = ?""",
                [index_name],
            ).fetchone()

        actual_table = _row_value(index_row, 'tbl_name', 0)
        actual_columns = tuple(
            _row_value(row, 'name', 2)
            for row in db.execute(
                f'PRAGMA index_info({_quote_identifier(index_name)})'
            ).fetchall()
        )
        index_list = {
            _row_value(row, 'name', 1): row
            for row in db.execute(
                f'PRAGMA index_list({_quote_identifier(table)})'
            ).fetchall()
        }
        metadata = index_list.get(index_name)
        if (actual_table != table or actual_columns != expected_columns
                or metadata is None
                or bool(_row_value(metadata, 'unique', 2))
                or bool(_row_value(metadata, 'partial', 4))):
            raise RuntimeError(
                f'Database index {index_name} has an invalid definition'
            )


def _validate_participation_schema(db, repair_indexes=False):
    for table, required_columns in _PARTICIPATION_REQUIRED_COLUMNS.items():
        columns = set(_pragma_columns(db, table))
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            raise RuntimeError(
                f'Database table {table} is missing required column(s): '
                + ', '.join(missing_columns)
            )

        index_signatures = _table_index_signatures(db, table)
        for unique_key in _PARTICIPATION_REQUIRED_UNIQUE_KEYS[table]:
            if (unique_key, True) not in index_signatures:
                raise RuntimeError(
                    f'Database table {table} is missing required unique key '
                    f"({', '.join(unique_key)})"
                )

        foreign_keys = _table_foreign_key_signatures(db, table)
        missing_foreign_keys = (
            _PARTICIPATION_REQUIRED_FOREIGN_KEYS[table] - foreign_keys
        )
        if missing_foreign_keys:
            raise RuntimeError(
                f'Database table {table} is missing required foreign key(s)'
            )

    _ensure_participation_indexes(db, repair_indexes)


def _ensure_weekly_hero_indexes(db, repair_indexes):
    for index_name, (table, expected_columns) in (
            _WEEKLY_HERO_REQUIRED_INDEXES.items()):
        index_row = db.execute(
            """SELECT tbl_name FROM sqlite_master
               WHERE type = 'index' AND name = ?""",
            [index_name],
        ).fetchone()
        if index_row is None:
            if not repair_indexes:
                raise RuntimeError(
                    f'Database is missing required index {index_name}'
                )
            columns_sql = ', '.join(
                _quote_identifier(column) for column in expected_columns
            )
            db.execute(
                f'CREATE INDEX {_quote_identifier(index_name)} '
                f'ON {_quote_identifier(table)} ({columns_sql})'
            )
            index_row = db.execute(
                """SELECT tbl_name FROM sqlite_master
                   WHERE type = 'index' AND name = ?""",
                [index_name],
            ).fetchone()

        actual_table = _row_value(index_row, 'tbl_name', 0)
        actual_columns = tuple(
            _row_value(row, 'name', 2)
            for row in db.execute(
                f'PRAGMA index_info({_quote_identifier(index_name)})'
            ).fetchall()
        )
        index_list = {
            _row_value(row, 'name', 1): row
            for row in db.execute(
                f'PRAGMA index_list({_quote_identifier(table)})'
            ).fetchall()
        }
        metadata = index_list.get(index_name)
        if (actual_table != table or actual_columns != expected_columns
                or metadata is None
                or bool(_row_value(metadata, 'unique', 2))
                or bool(_row_value(metadata, 'partial', 4))):
            raise RuntimeError(
                f'Database index {index_name} has an invalid definition'
            )


def _validate_weekly_hero_schema(db, repair_indexes=False):
    for table, required_columns in _WEEKLY_HERO_REQUIRED_COLUMNS.items():
        columns = set(_pragma_columns(db, table))
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            raise RuntimeError(
                f'Database table {table} is missing required column(s): '
                + ', '.join(missing_columns)
            )

        index_signatures = _table_index_signatures(db, table)
        for unique_key in _WEEKLY_HERO_REQUIRED_UNIQUE_KEYS[table]:
            if (unique_key, True) not in index_signatures:
                raise RuntimeError(
                    f'Database table {table} is missing required unique key '
                    f"({', '.join(unique_key)})"
                )

        foreign_keys = _table_foreign_key_signatures(db, table)
        missing_foreign_keys = (
            _WEEKLY_HERO_REQUIRED_FOREIGN_KEYS[table] - foreign_keys
        )
        if missing_foreign_keys:
            raise RuntimeError(
                f'Database table {table} is missing required foreign key(s)'
            )

    _ensure_weekly_hero_indexes(db, repair_indexes)


def _validate_student_profile_schema(db):
    columns = _pragma_columns(db, 'students')
    display_name = columns.get('display_name')
    if display_name is None:
        raise RuntimeError(
            'Database table students is missing required column(s): '
            'display_name'
        )
    declared_type = str(
        _row_value(display_name, 'type', 2) or ''
    ).strip().upper()
    if (declared_type != 'TEXT'
            or bool(_row_value(display_name, 'notnull', 3))):
        raise RuntimeError(
            'Database table students has an invalid display_name column'
        )


def validate_current_schema(db, repair_indexes=False):
    """Validate the complete contract for the schema served by this app."""
    recorded = inspect_schema_version(db, allow_unversioned=False)
    if recorded != SCHEMA_VERSION:
        raise RuntimeError(SCHEMA_MIGRATION_REQUIRED_ERROR)
    if parse_version(recorded) >= parse_version(
            _PARTICIPATION_SCHEMA_VERSION):
        _validate_participation_schema(
            db, repair_indexes=repair_indexes
        )
    if parse_version(recorded) >= parse_version(
            _STUDENT_PROFILE_SCHEMA_VERSION):
        _validate_student_profile_schema(db)
    if parse_version(recorded) >= parse_version(
            _WEEKLY_HERO_SCHEMA_VERSION):
        _validate_weekly_hero_schema(
            db, repair_indexes=repair_indexes
        )
    validate_data_version_schema(db)
    return recorded


def _rebuild_versioned_table_without_default(db, table):
    """Preserve one v1.0 table while requiring explicit future provenance."""
    table_row = db.execute(
        """SELECT sql FROM sqlite_master
           WHERE type = 'table' AND name = ?""",
        [table],
    ).fetchone()
    create_sql = _row_value(table_row, 'sql', 0) if table_row else None
    if not create_sql:
        raise RuntimeError(f'Database table {table} is missing')

    schema_objects = db.execute(
        """SELECT type, name, sql FROM sqlite_master
           WHERE tbl_name = ? AND type IN ('index', 'trigger')
             AND sql IS NOT NULL
           ORDER BY type, name""",
        [table],
    ).fetchall()
    temporary_table = f'__popping_{table}_v1_1'
    if db.execute(
        """SELECT 1 FROM sqlite_master
           WHERE type = 'table' AND name = ?""",
        [temporary_table],
    ).fetchone():
        raise RuntimeError(
            f'Database contains unexpected migration table {temporary_table}'
        )

    escaped_table = re.escape(table)
    table_token = (
        rf'(?:"{escaped_table}"|\[{escaped_table}\]|{escaped_table})'
    )
    migrated_sql, table_replacements = re.subn(
        rf'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table_token}',
        f'CREATE TABLE {_quote_identifier(temporary_table)}',
        create_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    migrated_sql, default_replacements = re.subn(
        (
            r'(\bdata_version\s+TEXT\s+NOT\s+NULL)\s+DEFAULT\s+'
            r'(?:\([^)]*\)|\x27[^\x27]*\x27|"[^"]*"|[^\s,)]+)'
        ),
        r'\1',
        migrated_sql,
        count=1,
        flags=re.IGNORECASE,
    )
    if table_replacements != 1 or default_replacements != 1:
        raise RuntimeError(
            f'Database table {table} cannot be upgraded safely'
        )

    db.execute(migrated_sql)
    column_names = list(_pragma_columns(db, table))
    quoted_columns = ', '.join(
        _quote_identifier(column) for column in column_names
    )
    db.execute(
        f'INSERT INTO {_quote_identifier(temporary_table)} '
        f'({quoted_columns}) SELECT {quoted_columns} '
        f'FROM {_quote_identifier(table)}'
    )
    db.execute(f'DROP TABLE {_quote_identifier(table)}')
    db.execute(
        f'ALTER TABLE {_quote_identifier(temporary_table)} '
        f'RENAME TO {_quote_identifier(table)}'
    )
    for schema_object in schema_objects:
        db.execute(_row_value(schema_object, 'sql', 2))


def _migrate_1_0_0_to_1_1_0(db):
    """Add trustworthy participation events and explicit data provenance."""
    for table in _versioned_data_tables_for(BASELINE_SCHEMA_VERSION):
        _rebuild_versioned_table_without_default(db, table)

    db.execute('''CREATE TABLE presentation_participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        session_key INTEGER NOT NULL,
        week_num INTEGER,
        presentation_key TEXT NOT NULL,
        student_id INTEGER NOT NULL,
        student_identifier TEXT NOT NULL,
        student_name TEXT,
        team_id INTEGER,
        team_name TEXT,
        data_version TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
        FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL,
        UNIQUE(course_id, presentation_key, student_id)
    )''')
    db.execute(
        '''CREATE INDEX idx_presentation_participants_student
           ON presentation_participants(course_id, student_id)'''
    )
    db.execute(
        '''CREATE INDEX idx_presentation_participants_session
           ON presentation_participants(course_id, session_key)'''
    )
    db.execute(
        '''CREATE INDEX idx_presentation_participants_export_week
           ON presentation_participants(course_id, week_num)'''
    )
    db.execute(
        '''CREATE INDEX idx_challenge_rounds_challenger
           ON challenge_rounds(course_id, challenger_id)'''
    )
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError(
            'Database foreign keys are invalid after schema migration'
        )


_SCHEMA_MIGRATIONS[(
    BASELINE_SCHEMA_VERSION, _PARTICIPATION_SCHEMA_VERSION
)] = _migrate_1_0_0_to_1_1_0


def _migrate_1_1_0_to_1_2_0(db):
    """Add a nullable student-entered display name beside the roster name."""
    db.execute('ALTER TABLE students ADD COLUMN display_name TEXT')


_SCHEMA_MIGRATIONS[(
    _PARTICIPATION_SCHEMA_VERSION, _STUDENT_PROFILE_SCHEMA_VERSION
)] = _migrate_1_1_0_to_1_2_0


def _migrate_1_2_0_to_1_3_0(db):
    """Add immutable weekly result snapshots and their award recipients."""
    db.execute('''CREATE TABLE weekly_hero_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        week_num INTEGER NOT NULL CHECK(week_num > 0),
        calculation_version TEXT NOT NULL,
        source_schema_version TEXT NOT NULL,
        source_data_versions TEXT NOT NULL,
        source_fingerprint TEXT NOT NULL
            CHECK(length(source_fingerprint) = 64
                  AND source_fingerprint NOT GLOB '*[^0-9a-f]*'),
        source_presentation_rating_count INTEGER NOT NULL
            CHECK(source_presentation_rating_count >= 0),
        source_challenge_rating_count INTEGER NOT NULL
            CHECK(source_challenge_rating_count >= 0),
        source_participant_count INTEGER NOT NULL
            CHECK(source_participant_count >= 0),
        source_history_item_count INTEGER NOT NULL
            CHECK(source_history_item_count >= 0),
        data_version TEXT NOT NULL,
        calculated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
        UNIQUE(course_id, week_num)
    )''')
    db.execute('''CREATE TABLE weekly_hero_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        summary_id INTEGER NOT NULL,
        result_key TEXT NOT NULL,
        category TEXT NOT NULL CHECK(category IN ('team', 'challenger')),
        award_type TEXT NOT NULL
            CHECK(award_type IN ('gold', 'silver', 'bronze', 'bolt')),
        rank INTEGER NOT NULL CHECK(rank >= 1 AND rank <= 3),
        score_sum INTEGER NOT NULL CHECK(score_sum >= 0),
        score_count INTEGER NOT NULL CHECK(score_count > 0),
        rating_count INTEGER NOT NULL CHECK(rating_count > 0),
        developed_score_sum INTEGER,
        developed_score_count INTEGER,
        easy_score_sum INTEGER,
        easy_score_count INTEGER,
        team_id INTEGER,
        team_name TEXT,
        challenger_id INTEGER,
        challenger_identifier TEXT,
        challenger_name TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (summary_id) REFERENCES weekly_hero_summaries (id)
            ON DELETE CASCADE,
        FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL,
        FOREIGN KEY (challenger_id) REFERENCES students (id)
            ON DELETE SET NULL,
        UNIQUE(summary_id, result_key),
        CHECK(
            (category = 'team' AND award_type IN ('gold', 'silver', 'bronze')
             AND rank BETWEEN 1 AND 3 AND team_name IS NOT NULL
             AND challenger_id IS NULL AND challenger_identifier IS NULL
             AND challenger_name IS NULL
             AND developed_score_sum IS NOT NULL
             AND developed_score_count > 0
             AND easy_score_sum IS NOT NULL AND easy_score_count > 0)
            OR
            (category = 'challenger' AND award_type = 'bolt' AND rank = 1
             AND team_id IS NULL AND team_name IS NULL
             AND challenger_name IS NOT NULL
             AND developed_score_sum IS NULL
             AND developed_score_count IS NULL
             AND easy_score_sum IS NULL AND easy_score_count IS NULL)
        )
    )''')
    db.execute('''CREATE TABLE weekly_hero_recipients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        result_id INTEGER NOT NULL,
        recipient_key TEXT NOT NULL,
        student_id INTEGER,
        student_identifier TEXT,
        student_name TEXT,
        team_id INTEGER,
        team_name TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (result_id) REFERENCES weekly_hero_results (id)
            ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE SET NULL,
        FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL,
        UNIQUE(result_id, recipient_key)
    )''')
    db.execute(
        '''CREATE INDEX idx_weekly_hero_results_summary_rank
           ON weekly_hero_results(summary_id, category, rank)'''
    )
    db.execute(
        '''CREATE INDEX idx_weekly_hero_recipients_student
           ON weekly_hero_recipients(student_id, result_id)'''
    )
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError(
            'Database foreign keys are invalid after schema migration'
        )


_SCHEMA_MIGRATIONS[(
    _STUDENT_PROFILE_SCHEMA_VERSION, _WEEKLY_HERO_SCHEMA_VERSION
)] = _migrate_1_2_0_to_1_3_0


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
        return validate_current_schema(db, repair_indexes=True)

    savepoint = 'popping_schema_upgrade'
    db.execute(f'SAVEPOINT {savepoint}')
    try:
        _apply_schema_migration_plan(db, migration_plan)
        validate_current_schema(db, repair_indexes=True)
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
    """Read-only verification of the current schema, once per process."""
    if slug in _schema_checked:
        return
    with _schema_lock_for(slug):
        if slug in _schema_checked:
            return
        db = get_db(slug)
        recorded = inspect_schema_version(db, allow_unversioned=True)
        if recorded != SCHEMA_VERSION:
            raise RuntimeError(SCHEMA_MIGRATION_REQUIRED_ERROR)
        validate_current_schema(db)
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
    versioned_tables = _versioned_data_tables_for(
        recorded_schema_version or BASELINE_SCHEMA_VERSION
    )
    for table in versioned_tables:
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
    validate_current_schema(db, repair_indexes=True)


def migrate_schema_connection(db):
    """Adopt or migrate an existing DB within the caller's transaction."""
    _ensure_schema_locked(db)


def _weekly_hero_week(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Lecture week must be a positive integer')
    return value


def _weekly_hero_source_schema_version(value):
    parsed = parse_version(value)
    if parsed[2] != 0:
        raise ValueError(
            'Source schema version must have a zero patch number'
        )
    return value


def _weekly_hero_text(value):
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _weekly_hero_query_dicts(db, sql, params=()):
    cursor = db.execute(sql, params)
    columns = tuple(description[0] for description in cursor.description)
    return [
        {
            column: _row_value(row, column, index)
            for index, column in enumerate(columns)
        }
        for row in cursor.fetchall()
    ]


def _weekly_hero_history_key(item):
    return (
        item.get('presentation_key')
        or f"pres-{item.get('started_at', '')}"
    )


def _weekly_hero_history_version(item):
    value = item.get('data_version')
    return BASELINE_DATA_VERSION if value is None else value


def _weekly_hero_version_sort_key(value):
    try:
        return parse_version(value)
    except (TypeError, ValueError):
        return (float('inf'), float('inf'), float('inf'))


def detect_weekly_hero_source_schema_version(db, course_id, week_num):
    """Detect one data compatibility series for a week's source rows.

    Patch releases in one major/minor series are intentionally combined.  A
    week containing more than one series is ambiguous and requires the CLI's
    explicit ``--source-schema-version`` option.
    """
    week_num = _weekly_hero_week(week_num)
    series = set()
    malformed = set()
    for table in (
            'presentation_ratings', 'challenge_ratings',
            'presentation_participants'):
        rows = db.execute(
            f'''SELECT DISTINCT data_version FROM {table}
                WHERE course_id = ? AND week_num = ?''',
            [course_id, week_num],
        ).fetchall()
        for row in rows:
            value = _row_value(row, 'data_version', 0)
            try:
                major, minor, _patch = parse_version(value)
            except (TypeError, ValueError):
                malformed.add(repr(value))
                continue
            series.add((major, minor))

    if malformed:
        raise RuntimeError(
            'Cannot auto-detect weekly result source version because the '
            'week contains malformed data_version value(s): '
            + ', '.join(sorted(malformed))
        )
    if len(series) > 1:
        labels = ', '.join(
            f'v{major}.{minor}.x' for major, minor in sorted(series)
        )
        raise RuntimeError(
            'Week data spans multiple compatibility series '
            f'({labels}); provide --source-schema-version explicitly'
        )
    if not series:
        return SCHEMA_VERSION
    major, minor = next(iter(series))
    return f'{major}.{minor}.0'


def _weekly_hero_source_rows(
        db, course_id, week_num, source_schema_version):
    _register_version_functions(db)
    params = [course_id, week_num, source_schema_version]
    presentation_ratings = _weekly_hero_query_dicts(
        db,
        '''SELECT id, session_key, week_num, question_key,
                  presenting_team_id, presenting_team_name, question_id,
                  question_title, q1_developed, q2_easy, data_version,
                  created_at
           FROM presentation_ratings
           WHERE course_id = ? AND week_num = ?
             AND popping_version_compatible(data_version, ?) = 1
           ORDER BY id''',
        params,
    )
    challenge_ratings = _weekly_hero_query_dicts(
        db,
        '''SELECT id, session_key, week_num, challenge_key,
                  presentation_key, challenger_id, challenger_name,
                  challenger_team_id, challenger_team_name, score,
                  data_version, created_at
           FROM challenge_ratings
           WHERE course_id = ? AND week_num = ?
             AND popping_version_compatible(data_version, ?) = 1
           ORDER BY id''',
        params,
    )
    participants = _weekly_hero_query_dicts(
        db,
        '''SELECT id, session_key, week_num, presentation_key, student_id,
                  student_identifier, student_name, team_id, team_name,
                  data_version, created_at
           FROM presentation_participants
           WHERE course_id = ? AND week_num = ?
             AND popping_version_compatible(data_version, ?) = 1
           ORDER BY id''',
        params,
    )

    challenger_ids = sorted({
        row['challenger_id'] for row in challenge_ratings
        if row['challenger_id'] is not None
    })
    student_profiles = []
    if challenger_ids:
        placeholders = ','.join('?' * len(challenger_ids))
        student_profiles = _weekly_hero_query_dicts(
            db,
            f'''SELECT id, student_id, name, display_name
                FROM students
                WHERE course_id = ? AND id IN ({placeholders})
                ORDER BY id''',
            [course_id] + challenger_ids,
        )

    rating_keys = {
        row['question_key'] for row in presentation_ratings
        if _weekly_hero_text(row['question_key'])
    }
    state = db.execute(
        '''SELECT presentation_history FROM course_state
           WHERE course_id = ?''',
        [course_id],
    ).fetchone()
    raw_history = _row_value(state, 'presentation_history', 0) if state else None
    if raw_history is None or not str(raw_history).strip():
        parsed_history = []
    else:
        try:
            parsed_history = json.loads(raw_history)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                'Cannot calculate weekly results from malformed '
                'presentation history'
            ) from exc
        if (not isinstance(parsed_history, list)
                or any(not isinstance(item, dict)
                       for item in parsed_history)):
            raise RuntimeError(
                'Cannot calculate weekly results from malformed '
                'presentation history'
            )

    history = []
    for item in parsed_history:
        key = _weekly_hero_history_key(item)
        if key not in rating_keys:
            continue
        try:
            compatible = versions_compatible(
                _weekly_hero_history_version(item), source_schema_version
            )
        except (TypeError, ValueError):
            compatible = False
        if compatible:
            history.append(item)
    history.sort(key=lambda item: (
        _weekly_hero_history_key(item),
        str(item.get('started_at') or ''),
    ))

    return {
        'presentation_ratings': presentation_ratings,
        'challenge_ratings': challenge_ratings,
        'participants': participants,
        'student_profiles': student_profiles,
        'history': history,
    }


def _weekly_hero_score(value, label):
    if type(value) is not int or not 1 <= value <= 5:
        raise RuntimeError(
            f'Cannot calculate weekly results: invalid {label} score'
        )
    return value


def _weekly_hero_team_matches(participant, team_id, team_name):
    participant_team_id = participant.get('team_id')
    participant_team_name = _weekly_hero_text(participant.get('team_name'))
    if team_id is not None:
        if participant_team_id == team_id:
            return True
        return (
            participant_team_id is None
            and participant_team_name is not None
            and participant_team_name.casefold() == team_name.casefold()
        )
    return (
        participant_team_name is not None
        and participant_team_name.casefold() == team_name.casefold()
    )


def _weekly_hero_competition_ranks(rows, name_field, id_field):
    for row in rows:
        row['_fraction'] = Fraction(row['score_sum'], row['score_count'])
    rows.sort(key=lambda row: (
        -row['_fraction'],
        (row.get(name_field) or '').casefold(),
        row.get(id_field) or 0,
    ))
    prior_fraction = None
    rank = None
    for position, row in enumerate(rows, 1):
        if prior_fraction is None or row['_fraction'] != prior_fraction:
            rank = position
        row['rank'] = rank
        row['average_score'] = round(float(row['_fraction']), 2)
        prior_fraction = row['_fraction']
        row.pop('_fraction')
    return rows


def _weekly_hero_team_results(source):
    participant_identity = {}
    for participant in source['participants']:
        key = _weekly_hero_text(participant['presentation_key'])
        if key is None or key in participant_identity:
            continue
        name = _weekly_hero_text(participant['team_name'])
        if name is not None:
            participant_identity[key] = {
                'id': participant['team_id'], 'name': name,
            }

    history_identity = {}
    for item in source['history']:
        key = _weekly_hero_history_key(item)
        name = _weekly_hero_text(item.get('team'))
        if key and name is not None and key not in history_identity:
            history_identity[key] = {
                'id': item.get('team_id'), 'name': name,
            }

    grouped = {}
    for rating in source['presentation_ratings']:
        q1 = rating['q1_developed']
        q2 = rating['q2_easy']
        if q1 is None or q2 is None:
            continue
        q1 = _weekly_hero_score(q1, 'presentation-developed')
        q2 = _weekly_hero_score(q2, 'presentation-easy')
        presentation_key = _weekly_hero_text(rating['question_key'])
        if presentation_key is None:
            continue
        fallback = (
            participant_identity.get(presentation_key)
            or history_identity.get(presentation_key)
            or {}
        )
        team_id = (
            rating['presenting_team_id']
            if rating['presenting_team_id'] is not None
            else fallback.get('id')
        )
        team_name = (
            _weekly_hero_text(rating['presenting_team_name'])
            or fallback.get('name')
        )
        if team_name is None:
            continue
        identity = (
            f'team-id:{team_id}' if team_id is not None
            else f'team-name:{team_name.casefold()}'
        )
        result = grouped.setdefault(identity, {
            'result_key': identity,
            'category': 'team',
            'team_id': team_id,
            'team_name': team_name,
            'challenger_id': None,
            'challenger_identifier': None,
            'challenger_name': None,
            'score_sum': 0,
            'score_count': 0,
            'rating_count': 0,
            'developed_score_sum': 0,
            'developed_score_count': 0,
            'easy_score_sum': 0,
            'easy_score_count': 0,
            'presentation_keys': set(),
        })
        result['score_sum'] += q1 + q2
        result['score_count'] += 2
        result['rating_count'] += 1
        result['developed_score_sum'] += q1
        result['developed_score_count'] += 1
        result['easy_score_sum'] += q2
        result['easy_score_count'] += 1
        result['presentation_keys'].add(presentation_key)

    ranked = _weekly_hero_competition_ranks(
        list(grouped.values()), 'team_name', 'team_id'
    )
    awards = []
    missing_coverage = []
    medal_by_rank = {1: 'gold', 2: 'silver', 3: 'bronze'}
    participants_by_presentation = {}
    for participant in source['participants']:
        participants_by_presentation.setdefault(
            participant['presentation_key'], []
        ).append(participant)

    for result in ranked:
        if result['rank'] > 3:
            continue
        result['award_type'] = medal_by_rank[result['rank']]
        recipients = {}
        for presentation_key in sorted(result['presentation_keys']):
            matching = [
                participant
                for participant in participants_by_presentation.get(
                    presentation_key, []
                )
                if _weekly_hero_team_matches(
                    participant, result['team_id'], result['team_name']
                )
            ]
            if not matching:
                missing_coverage.append({
                    'result_key': result['result_key'],
                    'team_id': result['team_id'],
                    'team_name': result['team_name'],
                    'presentation_key': presentation_key,
                })
                continue
            for participant in matching:
                student_identifier = _weekly_hero_text(
                    participant['student_identifier']
                )
                recipient_key = (
                    f'student-identifier:{student_identifier}'
                    if student_identifier is not None
                    else f"student-db:{participant['student_id']}"
                )
                recipients.setdefault(recipient_key, {
                    'recipient_key': recipient_key,
                    'student_id': participant['student_id'],
                    'student_identifier': student_identifier,
                    'student_name': (
                        _weekly_hero_text(participant['student_name'])
                        or student_identifier
                    ),
                    'team_id': participant['team_id'],
                    'team_name': (
                        _weekly_hero_text(participant['team_name'])
                        or result['team_name']
                    ),
                })
        result['recipients'] = sorted(
            recipients.values(),
            key=lambda recipient: (
                recipient['student_identifier'] or '',
                recipient['student_id'] or 0,
            ),
        )
        result['presentation_keys'] = sorted(result['presentation_keys'])
        awards.append(result)
    return awards, missing_coverage


def _weekly_hero_challenger_identity(rating, profiles):
    challenger_id = rating['challenger_id']
    profile = profiles.get(challenger_id) or {}
    student_identifier = _weekly_hero_text(profile.get('student_id'))
    challenger_name = (
        _weekly_hero_text(rating['challenger_name'])
        or _weekly_hero_text(profile.get('display_name'))
        or _weekly_hero_text(profile.get('name'))
        or student_identifier
    )
    return challenger_id, student_identifier, challenger_name


def _weekly_hero_challenger_results(source):
    profiles = {row['id']: row for row in source['student_profiles']}
    grouped = {}
    for rating in source['challenge_ratings']:
        if rating['score'] is None:
            continue
        score_value = _weekly_hero_score(
            rating['score'], 'challenger'
        )
        (
            challenger_id,
            student_identifier,
            challenger_name,
        ) = _weekly_hero_challenger_identity(
            rating, profiles
        )
        if challenger_id is None or challenger_name is None:
            continue
        result_key = f'challenger-id:{challenger_id}'
        result = grouped.setdefault(result_key, {
            'result_key': result_key,
            'category': 'challenger',
            'team_id': None,
            'team_name': None,
            'challenger_id': challenger_id,
            'challenger_identifier': student_identifier,
            'challenger_name': challenger_name,
            'score_sum': 0,
            'score_count': 0,
            'rating_count': 0,
            'developed_score_sum': None,
            'developed_score_count': None,
            'easy_score_sum': None,
            'easy_score_count': None,
        })
        result['score_sum'] += score_value
        result['score_count'] += 1
        result['rating_count'] += 1

    ranked = _weekly_hero_competition_ranks(
        list(grouped.values()), 'challenger_name', 'challenger_id'
    )
    awards = []
    for result in ranked:
        if result['rank'] != 1:
            continue
        result['award_type'] = 'bolt'
        recipient_key = (
            f"student-identifier:{result['challenger_identifier']}"
            if result['challenger_identifier'] is not None
            else f"student-db:{result['challenger_id']}"
        )
        result['recipients'] = [{
            'recipient_key': recipient_key,
            'student_id': result['challenger_id'],
            'student_identifier': result['challenger_identifier'],
            'student_name': result['challenger_name'],
            'team_id': None,
            'team_name': None,
        }]
        awards.append(result)
    return awards


def _weekly_hero_fingerprint_source(source, challenger_results):
    """Return only source evidence that can affect a weekly summary.

    Database row IDs, timestamps, and current profile names that are shadowed
    by a recorded challenger name are storage details, not calculation
    inputs. Query order is retained because it determines the historical
    fallback selected when malformed legacy rows disagree on an identity.
    """
    return {
        'presentation_ratings': [
            {
                'question_key': rating['question_key'],
                'presenting_team_id': rating['presenting_team_id'],
                'presenting_team_name': _weekly_hero_text(
                    rating['presenting_team_name']
                ),
                'q1_developed': rating['q1_developed'],
                'q2_easy': rating['q2_easy'],
                'data_version': rating['data_version'],
            }
            for rating in source['presentation_ratings']
        ],
        'challenge_ratings': [
            {
                'challenge_key': rating['challenge_key'],
                'presentation_key': rating['presentation_key'],
                'challenger_id': rating['challenger_id'],
                'challenger_name': _weekly_hero_text(
                    rating['challenger_name']
                ),
                'score': rating['score'],
                'data_version': rating['data_version'],
            }
            for rating in source['challenge_ratings']
        ],
        'awarded_challenger_identities': [
            {
                'challenger_id': result['challenger_id'],
                'challenger_identifier': result['challenger_identifier'],
                'challenger_name': result['challenger_name'],
            }
            for result in challenger_results
        ],
        'participants': [
            {
                'presentation_key': participant['presentation_key'],
                'student_id': participant['student_id'],
                'student_identifier': _weekly_hero_text(
                    participant['student_identifier']
                ),
                'student_name': _weekly_hero_text(
                    participant['student_name']
                ),
                'team_id': participant['team_id'],
                'team_name': _weekly_hero_text(participant['team_name']),
                'data_version': participant['data_version'],
            }
            for participant in source['participants']
        ],
        'history': [
            {
                'presentation_key': _weekly_hero_history_key(item),
                'team_id': item.get('team_id'),
                'team_name': _weekly_hero_text(item.get('team')),
                'data_version': _weekly_hero_history_version(item),
            }
            for item in source['history']
        ],
    }


def calculate_weekly_hero_preview(
        db, course_id, week_num, source_schema_version=SCHEMA_VERSION):
    """Calculate one read-only, deterministic weekly award preview."""
    week_num = _weekly_hero_week(week_num)
    source_schema_version = _weekly_hero_source_schema_version(
        source_schema_version
    )
    source = _weekly_hero_source_rows(
        db, course_id, week_num, source_schema_version
    )
    team_results, missing_coverage = _weekly_hero_team_results(source)
    challenger_results = _weekly_hero_challenger_results(source)
    fingerprint_payload = {
        'calculation_version': WEEKLY_HERO_CALCULATION_VERSION,
        'course_id': course_id,
        'week_num': week_num,
        'source_schema_version': source_schema_version,
        'source': _weekly_hero_fingerprint_source(
            source, challenger_results
        ),
    }
    fingerprint_json = json.dumps(
        fingerprint_payload,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
        default=str,
    )
    source_fingerprint = hashlib.sha256(
        fingerprint_json.encode('utf-8')
    ).hexdigest()

    source_versions = {
        row['data_version']
        for group in (
            source['presentation_ratings'], source['challenge_ratings'],
            source['participants']
        )
        for row in group
    }
    source_versions.update(
        _weekly_hero_history_version(item) for item in source['history']
    )
    source_versions = sorted(
        source_versions, key=_weekly_hero_version_sort_key
    )
    return {
        'course_id': course_id,
        'week_num': week_num,
        'calculation_version': WEEKLY_HERO_CALCULATION_VERSION,
        'source_schema_version': source_schema_version,
        'source_data_versions': source_versions,
        'source_fingerprint': source_fingerprint,
        'source_presentation_rating_count': len(
            source['presentation_ratings']
        ),
        'source_challenge_rating_count': len(source['challenge_ratings']),
        'source_participant_count': len(source['participants']),
        'source_history_item_count': len(source['history']),
        'results': team_results + challenger_results,
        'missing_participant_presentations': missing_coverage,
        'recipient_coverage_complete': not missing_coverage,
    }


def save_weekly_hero_summary(db, preview, replace=False):
    """Persist a verified preview without changing any source activity rows.

    The caller owns the transaction.  Source rows are recalculated inside
    that transaction, making the preview fingerprint a stale-write guard.
    Existing identical summaries are an idempotent no-op.
    """
    if not isinstance(preview, dict):
        raise ValueError('Weekly result preview must be a mapping')
    course_id = preview.get('course_id')
    week_num = _weekly_hero_week(preview.get('week_num'))
    source_schema_version = _weekly_hero_source_schema_version(
        preview.get('source_schema_version')
    )
    expected_fingerprint = preview.get('source_fingerprint')
    if (not isinstance(expected_fingerprint, str)
            or not re.fullmatch(r'[0-9a-f]{64}', expected_fingerprint)):
        raise ValueError('Weekly result preview has an invalid fingerprint')
    if inspect_schema_version(db, allow_unversioned=False) != SCHEMA_VERSION:
        raise RuntimeError(SCHEMA_MIGRATION_REQUIRED_ERROR)
    foreign_keys = db.execute('PRAGMA foreign_keys').fetchone()
    if not foreign_keys or not _row_value(foreign_keys, 'foreign_keys', 0):
        raise RuntimeError(
            'Weekly result persistence requires SQLite foreign keys'
        )

    current = calculate_weekly_hero_preview(
        db,
        course_id,
        week_num,
        source_schema_version=source_schema_version,
    )
    if current['source_fingerprint'] != expected_fingerprint:
        raise RuntimeError(
            'Weekly result source data changed after preview; preview again'
        )
    if not current['recipient_coverage_complete']:
        missing = ', '.join(
            item['presentation_key']
            for item in current['missing_participant_presentations']
        )
        raise RuntimeError(
            'Cannot save weekly results because awarded presentation(s) '
            f'lack participant snapshots: {missing}'
        )

    existing = db.execute(
        '''SELECT id, calculation_version, source_fingerprint
           FROM weekly_hero_summaries
           WHERE course_id = ? AND week_num = ?''',
        [course_id, week_num],
    ).fetchone()
    if existing:
        existing_id = _row_value(existing, 'id', 0)
        existing_calculation = _row_value(
            existing, 'calculation_version', 1
        )
        existing_fingerprint = _row_value(
            existing, 'source_fingerprint', 2
        )
        if (existing_calculation == current['calculation_version']
                and existing_fingerprint == current['source_fingerprint']):
            return {
                'status': 'unchanged',
                'summary_id': existing_id,
                'preview': current,
            }
        if not replace:
            raise RuntimeError(
                'A different weekly result summary already exists; preview '
                'the new calculation and explicitly allow replacement'
            )
        db.execute(
            'DELETE FROM weekly_hero_summaries WHERE id = ?', [existing_id]
        )
        status = 'replaced'
    else:
        status = 'created'

    summary_id = db.execute(
        '''INSERT INTO weekly_hero_summaries
           (course_id, week_num, calculation_version,
            source_schema_version, source_data_versions,
            source_fingerprint, source_presentation_rating_count,
            source_challenge_rating_count, source_participant_count,
            source_history_item_count, data_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        [
            course_id,
            week_num,
            current['calculation_version'],
            current['source_schema_version'],
            json.dumps(
                current['source_data_versions'],
                ensure_ascii=False,
                separators=(',', ':'),
            ),
            current['source_fingerprint'],
            current['source_presentation_rating_count'],
            current['source_challenge_rating_count'],
            current['source_participant_count'],
            current['source_history_item_count'],
            APP_VERSION,
        ],
    ).lastrowid

    for result in current['results']:
        result_id = db.execute(
            '''INSERT INTO weekly_hero_results
               (summary_id, result_key, category, award_type, rank,
                score_sum, score_count, rating_count,
                developed_score_sum, developed_score_count,
                easy_score_sum, easy_score_count, team_id, team_name,
                challenger_id, challenger_identifier, challenger_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [
                summary_id,
                result['result_key'],
                result['category'],
                result['award_type'],
                result['rank'],
                result['score_sum'],
                result['score_count'],
                result['rating_count'],
                result['developed_score_sum'],
                result['developed_score_count'],
                result['easy_score_sum'],
                result['easy_score_count'],
                result['team_id'],
                result['team_name'],
                result['challenger_id'],
                result['challenger_identifier'],
                result['challenger_name'],
            ],
        ).lastrowid
        for recipient in result['recipients']:
            db.execute(
                '''INSERT INTO weekly_hero_recipients
                   (result_id, recipient_key, student_id,
                    student_identifier, student_name, team_id, team_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                [
                    result_id,
                    recipient['recipient_key'],
                    recipient['student_id'],
                    recipient['student_identifier'],
                    recipient['student_name'],
                    recipient['team_id'],
                    recipient['team_name'],
                ],
            )

    return {
        'status': status,
        'summary_id': summary_id,
        'preview': current,
    }


def get_weekly_hero_badge_counts(
        db, course_id, before_week=None, student_ids=None):
    """Return owned badge counts keyed by current internal student ID."""
    target_ids = []
    if student_ids is not None:
        for student_id in student_ids:
            if student_id is None:
                continue
            try:
                target_ids.append(int(student_id))
            except (TypeError, ValueError):
                continue
        target_ids = list(dict.fromkeys(target_ids))
        if not target_ids:
            return {}

    _register_version_functions(db)
    where = [
        'summary.course_id = ?',
        'recipient.student_id IS NOT NULL',
        'popping_version_compatible(summary.data_version, ?) = 1',
    ]
    params = [course_id, SCHEMA_VERSION]
    if before_week is not None:
        before_week = _weekly_hero_week(before_week)
        where.append('summary.week_num < ?')
        params.append(before_week)
    if target_ids:
        placeholders = ','.join('?' * len(target_ids))
        where.append(f'recipient.student_id IN ({placeholders})')
        params.extend(target_ids)

    rows = _weekly_hero_query_dicts(
        db,
        f'''SELECT recipient.student_id, result.award_type,
                   COUNT(*) AS badge_count
            FROM weekly_hero_summaries summary
            JOIN weekly_hero_results result
              ON result.summary_id = summary.id
            JOIN weekly_hero_recipients recipient
              ON recipient.result_id = result.id
            WHERE {' AND '.join(where)}
            GROUP BY recipient.student_id, result.award_type
            ORDER BY recipient.student_id, result.award_type''',
        params,
    )
    badges = {}
    for row in rows:
        count = int(row['badge_count'] or 0)
        award_type = row['award_type']
        if count > 0 and award_type in ('gold', 'silver', 'bronze', 'bolt'):
            badges.setdefault(row['student_id'], {})[award_type] = count
    return badges


def get_weekly_hero_rows(db, course_id, week_num):
    """Return one flat export row per saved weekly award recipient."""
    week_num = _weekly_hero_week(week_num)
    _register_version_functions(db)
    rows = _weekly_hero_query_dicts(
        db,
        '''SELECT summary.week_num, summary.calculation_version,
                  summary.source_schema_version,
                  summary.source_data_versions,
                  summary.source_fingerprint, summary.data_version,
                  summary.calculated_at, result.category,
                  result.award_type, result.rank, result.score_sum,
                  result.score_count, result.rating_count,
                  result.developed_score_sum,
                  result.developed_score_count, result.easy_score_sum,
                  result.easy_score_count, result.team_id,
                  result.team_name, result.challenger_id,
                  result.challenger_identifier, result.challenger_name,
                  recipient.student_id, recipient.student_identifier,
                  recipient.student_name, recipient.team_id
                      AS recipient_team_id,
                  recipient.team_name AS recipient_team_name
           FROM weekly_hero_summaries summary
           JOIN weekly_hero_results result ON result.summary_id = summary.id
           JOIN weekly_hero_recipients recipient
             ON recipient.result_id = result.id
           WHERE summary.course_id = ? AND summary.week_num = ?
             AND popping_version_compatible(summary.data_version, ?) = 1
           ORDER BY CASE result.category WHEN 'team' THEN 0 ELSE 1 END,
                    result.rank, COALESCE(result.team_name,
                                          result.challenger_name),
                    COALESCE(recipient.student_identifier, ''),
                    recipient.student_id''',
        [course_id, week_num, SCHEMA_VERSION],
    )
    for row in rows:
        row['average_score'] = round(
            row['score_sum'] / row['score_count'], 2
        )
        try:
            row['source_data_versions'] = json.loads(
                row['source_data_versions']
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                'Saved weekly result has malformed source-version metadata'
            ) from exc
    return rows


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
