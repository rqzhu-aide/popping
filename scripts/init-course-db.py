#!/usr/bin/env python3
from datetime import datetime
import getpass
import os
import re
import sqlite3
import sys
import tempfile

import yaml


COLORS = [
    '#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
    '#14b8a6', '#e11d48', '#0ea5e9', '#a855f7', '#22c55e',
    '#eab308', '#dc2626', '#2563eb', '#059669', '#d97706'
]
SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')
PRESENTATION_HTML_RE = re.compile(r'^q\d+\.html$')
MAX_TEAM_POOL_SIZE = 100
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
SCHEMA_PATH = os.path.join(PROJECT_ROOT, 'popping.sql')
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from question_catalog import validate_question_catalog


def build_team_rows(config):
    configured = config.get('teams') or []
    if not isinstance(configured, list):
        raise ValueError("'teams' must be a list")

    try:
        pool_size = int(config.get('team_pool_size', max(20, len(configured))))
    except (TypeError, ValueError) as exc:
        raise ValueError("'team_pool_size' must be an integer") from exc
    if len(configured) > MAX_TEAM_POOL_SIZE:
        raise ValueError(
            f"No more than {MAX_TEAM_POOL_SIZE} configured teams are allowed"
        )
    if not 1 <= pool_size <= MAX_TEAM_POOL_SIZE:
        raise ValueError(
            f"'team_pool_size' must be between 1 and {MAX_TEAM_POOL_SIZE}"
        )

    rows = []
    for i, team in enumerate(configured):
        if not isinstance(team, dict):
            raise ValueError(f"Team {i + 1} must be a mapping")
        rows.append({
            'name': team.get('name') or f"Team {i + 1}",
            'color': team.get('color') or COLORS[i % len(COLORS)]
        })

    while len(rows) < pool_size:
        i = len(rows)
        rows.append({
            'name': f"Team {i + 1}",
            'color': COLORS[i % len(COLORS)]
        })

    names = [row['name'] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("Team names must be unique")
    return rows


def read_presentation_question_index(config_dir, week_num):
    index_path = os.path.join(config_dir, f'week{week_num}', 'index.md')
    if not os.path.exists(index_path):
        return []

    questions = []
    with open(index_path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            match = re.match(r'^(\d+)\.\s+(.+)$', line)
            if match:
                questions.append({
                    'num': int(match.group(1)),
                    'title': match.group(2).strip()
                })
    return questions


def load_course_config(config_dir):
    yaml_path = os.path.join(config_dir, 'course.yaml')
    if not os.path.isfile(yaml_path):
        raise ValueError(f"course.yaml not found in {config_dir}")

    with open(yaml_path, 'r', encoding='utf-8-sig') as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError("course.yaml must contain a mapping")

    for field in ('slug', 'name', 'code', 'semester'):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError(f"course.yaml field '{field}' is required")
        config[field] = config[field].strip()

    if not SLUG_RE.fullmatch(config['slug']):
        raise ValueError(
            "Course slug may contain only letters, numbers, underscores, and hyphens"
        )
    return config


def resolve_data_dir():
    configured = os.environ.get('DATA_DIR')
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if os.path.isdir('/data'):
        return '/data'
    return os.path.join(PROJECT_ROOT, 'data')


def course_defaults(config, team_rows):
    configured_teams = config.get('teams') or []
    fallback_max = min(len(configured_teams) or 5, len(team_rows))
    try:
        max_teams = int(config.get('max_teams', fallback_max))
        max_members = int(config.get('max_members_per_team', 10))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "'max_teams' and 'max_members_per_team' must be integers"
        ) from exc
    if not 1 <= max_teams <= len(team_rows):
        raise ValueError(
            f"'max_teams' must be between 1 and {len(team_rows)}"
        )
    if not 1 <= max_members <= 99:
        raise ValueError("'max_members_per_team' must be between 1 and 99")
    return max_teams, max_members


def validate_initial_question_catalog(config_dir, week_num=1):
    """Validate any initial-week material while allowing a blank new course."""
    discussion_path = os.path.join(
        config_dir, f'week-{week_num}-questions.md'
    )
    week_dir = os.path.join(config_dir, f'week{week_num}')
    index_path = os.path.join(week_dir, 'index.md')
    discussion_present = os.path.exists(discussion_path)
    presentation_present = os.path.exists(index_path)
    if os.path.exists(week_dir) and not os.path.isdir(week_dir):
        presentation_present = True
    elif os.path.isdir(week_dir):
        try:
            presentation_present = presentation_present or any(
                PRESENTATION_HTML_RE.fullmatch(name)
                for name in os.listdir(week_dir)
            )
        except OSError as exc:
            raise ValueError(
                f"Could not inspect question catalog week {week_num}: {exc}"
            ) from exc

    if not discussion_present and not presentation_present:
        return None

    week = validate_question_catalog(
        config_dir, weeks=[week_num]
    ).get_week(week_num)
    sections = []
    if discussion_present and week:
        sections.append(week.discussion)
    if presentation_present and week:
        sections.append(week.presentation)
    if week and sections and all(section.ready for section in sections):
        return week
    issues = [] if not week else [
        issue.message for section in sections for issue in section.issues
    ]
    detail = f": {issues[0]}" if issues else ''
    raise ValueError(f"Question catalog week {week_num} is not ready{detail}")


def build_candidate_database(
    path,
    config_dir,
    config,
    team_rows,
    max_teams,
    max_members,
    username,
    display_name,
    pin,
):
    conn = sqlite3.connect(path)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        with open(SCHEMA_PATH, 'r', encoding='utf-8-sig') as f:
            conn.executescript(f.read())

        instructor_id = conn.execute(
            "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
            (username, display_name, pin),
        ).lastrowid
        course_id = conn.execute(
            """INSERT INTO courses
               (name, code, semester, slug, instructor_id)
               VALUES (?, ?, ?, ?, ?)""",
            (
                config['name'],
                config['code'],
                config['semester'],
                config['slug'],
                instructor_id,
            ),
        ).lastrowid

        for team in team_rows:
            conn.execute(
                "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
                (course_id, team['name'], team['color']),
            )

        conn.execute(
            """INSERT INTO course_state
               (course_id, phase, max_teams, max_members_per_team)
               VALUES (?, 'setup', ?, ?)""",
            (course_id, max_teams, max_members),
        )

        for question in read_presentation_question_index(config_dir, 1):
            conn.execute(
                """INSERT INTO questions
                   (course_id, question_num, question_text, title, week_num)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    course_id,
                    question['num'],
                    question['title'][:200],
                    question['title'],
                    1,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def validate_course_database(path, expected_slug):
    if not os.path.isfile(path):
        raise ValueError(f"Database file not found: {path}")
    conn = sqlite3.connect(path)
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


def prune_backups(backup_dir, prefix, keep=BACKUP_RETENTION):
    """Keep only the newest backup files and their known sidecars."""
    backups = []
    if os.path.isdir(backup_dir):
        for name in os.listdir(backup_dir):
            if name.startswith(prefix) and name.endswith('.db'):
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


def prepare_database_for_replacement(db_path):
    """Checkpoint WAL and leave no stale sidecars before file replacement."""
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.execute('PRAGMA busy_timeout=5000')
        checkpoint = conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint and checkpoint[0]:
            raise RuntimeError(
                "Could not checkpoint the live database. Confirm the web service "
                "is fully stopped and try again"
            )
        journal_mode = conn.execute('PRAGMA journal_mode=DELETE').fetchone()[0]
        if str(journal_mode).lower() != 'delete':
            raise RuntimeError(
                "Could not leave WAL mode. Confirm the web service is fully "
                "stopped and try again"
            )
    except sqlite3.Error as exc:
        raise RuntimeError(
            "Could not prepare the live database for replacement. Confirm the "
            "web service is fully stopped and try again"
        ) from exc
    finally:
        conn.close()
    remove_database_sidecars(db_path)


def create_sqlite_backup(db_path, expected_slug):
    backup_dir = os.path.join(os.path.dirname(db_path), 'init-backups')
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup_path = os.path.join(
        backup_dir, f'popping-before-init-{stamp}.db'
    )
    handle, temporary_path = tempfile.mkstemp(
        prefix='.popping-backup-', suffix='.tmp.db', dir=backup_dir
    )
    os.close(handle)

    source = target = None
    try:
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(temporary_path)
        source.backup(target)
        target.close()
        target = None
        source.close()
        source = None
        validate_course_database(temporary_path, expected_slug)
        os.replace(temporary_path, backup_path)
        prune_backups(backup_dir, 'popping-before-init-')
        return backup_path
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if os.path.exists(temporary_path):
            os.remove(temporary_path)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("Usage: python3 init-course-db.py <course_config_dir>")
        print(
            "  <course_config_dir> is the folder containing course.yaml "
            "(e.g. classes/432fall2026)"
        )
        return 1

    temporary_path = None
    try:
        config_dir = os.path.abspath(args[0])
        config = load_course_config(config_dir)
        team_rows = build_team_rows(config)
        max_teams, max_members = course_defaults(config, team_rows)
        validate_initial_question_catalog(config_dir)
        if not os.path.isfile(SCHEMA_PATH):
            raise ValueError(f"Database schema not found at {SCHEMA_PATH}")

        data_dir = resolve_data_dir()
        db_dir = os.path.join(data_dir, config['slug'])
        db_path = os.path.join(db_dir, 'popping.db')

        print("=== Create/Reset Course Database ===")
        print(f"Course: {config['name']} ({config['slug']})")
        print(f"Config:  {config_dir}")
        print(f"DB path: {db_path}")
        print("")

        username = input("Instructor username: ").strip()
        display_name = input("Instructor name: ").strip()
        pin = getpass.getpass("Instructor PIN: ").strip()
        if not username or not display_name or not pin:
            raise ValueError("All instructor fields are required")

        os.makedirs(db_dir, exist_ok=True)
        handle, temporary_path = tempfile.mkstemp(
            prefix='.popping-candidate-', suffix='.tmp.db', dir=db_dir
        )
        os.close(handle)

        print("Building and validating replacement database...")
        build_candidate_database(
            temporary_path,
            config_dir,
            config,
            team_rows,
            max_teams,
            max_members,
            username,
            display_name,
            pin,
        )
        validate_course_database(temporary_path, config['slug'])

        backup_path = None
        if os.path.exists(db_path):
            confirmation = input(
                f"Type {config['slug']} to replace the existing database: "
            ).strip()
            if confirmation != config['slug']:
                print("Cancelled: course slug did not match.")
                return 1
            offline_confirmation = input(
                f"Type {OFFLINE_CONFIRMATION} to confirm all web workers are stopped: "
            ).strip()
            if offline_confirmation != OFFLINE_CONFIRMATION:
                print("Cancelled: web service stop was not confirmed.")
                return 1
            backup_path = create_sqlite_backup(db_path, config['slug'])
            prepare_database_for_replacement(db_path)

        os.replace(temporary_path, db_path)
        temporary_path = None

        print("")
        print("=== Done! ===")
        if backup_path:
            print(f"Previous database backup: {backup_path}")
        print(f"Instructor login: {username} / [hidden]")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)


if __name__ == '__main__':
    sys.exit(main())
