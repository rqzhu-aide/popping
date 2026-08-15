#!/usr/bin/env python3
"""Initialize or reset the demo course database.

Creates a self-contained 'demo' course with:
  - 1 instructor (the web demo bypasses login)
  - 2 unassigned students and 2 teams
  - Sample questions read from classes/demo/week-1-questions.md
  - Course state in 'setup' phase

Usage:
    python3 scripts/init-demo-db.py           # create or reset
    python3 scripts/init-demo-db.py --ensure  # create only when missing
    python3 scripts/init-demo-db.py --check   # exit 0 if exists, 1 if not
"""
import sys
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE_DIR, 'popping.sql')
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from question_catalog import read_week_questions


def resolve_data_dir():
    """Same resolution logic as init-course-db.py and restore-course-db.py."""
    configured = os.environ.get('DATA_DIR')
    if configured:
        return os.path.abspath(os.path.expanduser(configured))
    if os.path.isdir('/data'):
        return '/data'
    return os.path.join(BASE_DIR, 'data')


DATA_DIR = resolve_data_dir()
DB_DIR = os.path.join(DATA_DIR, 'demo')
DB_PATH = os.path.join(DB_DIR, 'popping.db')
LOCK_PATH = os.path.join(DB_DIR, '.demo-init-lock.sqlite3')
DEMO_SEED_VERSION = 3

_RESET_TABLES = (
    'discussion_responses',
    'discussion_selections',
    'peer_reviews',
    'teammate_thumbs',
    'presentation_ratings',
    'login_attempts',
    'course_state',
    'students',
    'questions',
    'teams',
    'courses',
    'instructors',
)


def _validate_demo_connection(conn, require_seeded_shape=False):
    """Validate a demo database, including uncommitted reset data."""
    ok = conn.execute('PRAGMA integrity_check').fetchone()[0]
    if ok != 'ok':
        raise RuntimeError(f'integrity_check failed: {ok}')
    fk_errors = conn.execute('PRAGMA foreign_key_check').fetchall()
    if fk_errors:
        raise RuntimeError(f'foreign_key_check found {len(fk_errors)} violation(s)')
    seed_version = conn.execute('PRAGMA user_version').fetchone()[0]
    if seed_version != DEMO_SEED_VERSION:
        raise RuntimeError(
            f'Demo seed version {seed_version} is not {DEMO_SEED_VERSION}'
        )

    required_tables = (
        'instructors', 'courses', 'teams', 'students', 'questions', 'course_state'
    )
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table in required_tables:
        if table not in existing_tables:
            raise RuntimeError(f'Missing required table: {table}')

    course_rows = conn.execute(
        "SELECT id, instructor_id FROM courses WHERE slug = 'demo'"
    ).fetchall()
    if len(course_rows) != 1:
        raise RuntimeError(f'Expected 1 demo course, found {len(course_rows)}')

    course_id = course_rows[0]['id']
    state = conn.execute(
        """SELECT phase, max_teams, max_members_per_team
           FROM course_state WHERE course_id = ?""", (course_id,)
    ).fetchone()
    if not state:
        raise RuntimeError('Demo course state is missing')

    if not require_seeded_shape:
        return

    instructor_id = course_rows[0]['instructor_id']
    instructor = conn.execute(
        "SELECT username FROM instructors WHERE id = ?", (instructor_id,)
    ).fetchone()
    if not instructor or instructor['username'] != 'demo_instructor':
        raise RuntimeError('Demo instructor is missing or invalid')
    if state['max_teams'] != 2 or state['max_members_per_team'] != 2:
        raise RuntimeError('Demo team limits must be 2 teams with 2 seats each')

    team_rows = conn.execute(
        '''SELECT t.id, count(s.id) AS student_count
           FROM teams t
           LEFT JOIN students s ON s.team_id = t.id AND s.is_active = 1
           WHERE t.course_id = ?
           GROUP BY t.id
           ORDER BY t.id''',
        (course_id,)
    ).fetchall()
    unassigned_count = conn.execute(
        '''SELECT count(*) FROM students
           WHERE course_id = ? AND team_id IS NULL AND is_active = 1''',
        (course_id,)
    ).fetchone()[0]
    if (len(team_rows) != 2 or
            any(row['student_count'] != 0 for row in team_rows) or
            unassigned_count != 2):
        raise RuntimeError('Demo must have 2 teams and 2 unassigned students')

    question_count = conn.execute(
        "SELECT count(*) FROM questions WHERE course_id = ?", (course_id,)
    ).fetchone()[0]
    if question_count < 1:
        raise RuntimeError('Demo must have at least one question')


def _validate_demo_db(path, require_seeded_shape=False):
    """Open and validate a completed demo database."""
    uri = f'file:{path}?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        _validate_demo_connection(conn, require_seeded_shape)
        return conn.execute(
            '''SELECT count(*) FROM questions q
               JOIN courses c ON c.id = q.course_id
               WHERE c.slug = 'demo' '''
        ).fetchone()[0]
    finally:
        conn.close()


def _read_demo_seed_version(path):
    """Read the lightweight seed version without changing the database."""
    uri = f'file:{path}?mode=ro'
    conn = sqlite3.connect(uri, uri=True)
    try:
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def _populate(conn):
    """Insert instructor, course, teams, students, questions, and state."""
    # Instructor
    conn.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ('demo_instructor', 'Demo Instructor', 'demo')
    )
    instructor_id = conn.execute(
        "SELECT id FROM instructors WHERE username = 'demo_instructor'"
    ).fetchone()['id']

    # Course
    conn.execute(
        "INSERT INTO courses (name, code, semester, slug, instructor_id, is_active) "
        "VALUES (?, ?, ?, ?, ?, 1)",  # is_active=1: required by course availability
        # checks; the demo is kept off the landing list by slug in _scan_courses().
        ('Popping Demo Course', 'DEMO 101', 'Always', 'demo', instructor_id)
    )
    course_id = conn.execute(
        "SELECT id FROM courses WHERE slug = 'demo'"
    ).fetchone()['id']

    # Two small teams are enough for the private demo.
    teams_data = [
        ('Team 1', '#ef4444'),
        ('Team 2', '#3b82f6'),
    ]
    for name, color in teams_data:
        conn.execute(
            "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
            (course_id, name, color)
        )

    # Both students start unassigned so the visitor can choose the interaction.
    students_data = [
        ('demo001', 'Demo Student 1'),
        ('demo002', 'Demo Student 2'),
    ]
    for sid, name in students_data:
        conn.execute(
            "INSERT INTO students (course_id, student_id, name, pin, team_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (course_id, sid, name, 'demo', None)
        )

    question_path = os.path.join(
        BASE_DIR, 'classes', 'demo', 'week-1-questions.md'
    )
    questions = read_week_questions(question_path, week_num=1)

    for q in questions:
        conn.execute(
            """INSERT INTO questions
               (course_id, question_num, question_text, title, content,
                week_num, source_key)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (
                course_id,
                q['num'],
                q['title'][:200],
                q['title'],
                q['content'],
                q['source_key'],
            )
        )

    # Course state — start in setup
    conn.execute(
        "INSERT INTO course_state (course_id, phase, max_teams, max_members_per_team) "
        "VALUES (?, 'setup', 2, 2)",
        (course_id,)
    )
    conn.execute(f'PRAGMA user_version = {DEMO_SEED_VERSION}')

    return len(questions)


@contextmanager
def _initialization_lock():
    """Serialize demo creation and reset across web worker processes."""
    lock = sqlite3.connect(LOCK_PATH, timeout=30)
    try:
        lock.execute('PRAGMA busy_timeout = 30000')
        lock.execute(
            'CREATE TABLE IF NOT EXISTS lock_state (id INTEGER PRIMARY KEY)'
        )
        lock.commit()
        lock.execute('BEGIN IMMEDIATE')
        yield
    finally:
        lock.rollback()
        lock.close()


def _build_candidate(path):
    """Build and validate a new demo database at *path*."""
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        with open(SCHEMA, encoding='utf-8') as f:
            conn.executescript(f.read())
        q_count = _populate(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _validate_demo_db(path, require_seeded_shape=True)
    return q_count


def _create_demo_db():
    """Create the first demo database and publish it after validation."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix='.demo-candidate-', suffix='.db', dir=DB_DIR
    )
    os.close(tmp_fd)
    try:
        q_count = _build_candidate(tmp_path)
        os.replace(tmp_path, DB_PATH)
        return q_count
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def _reset_demo_db():
    """Reset an existing demo in one transaction without replacing its file."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA busy_timeout = 30000')
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('PRAGMA user_version').fetchone()[0] < DEMO_SEED_VERSION:
            if BASE_DIR not in sys.path:
                sys.path.insert(0, BASE_DIR)
            from database import _ensure_schema_locked
            _ensure_schema_locked(conn)

        existing_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in _RESET_TABLES:
            if table in existing_tables:
                conn.execute(f'DELETE FROM "{table}"')
        if 'sqlite_sequence' in existing_tables:
            conn.execute('DELETE FROM sqlite_sequence')

        q_count = _populate(conn)
        _validate_demo_connection(conn, require_seeded_shape=True)
        conn.commit()
        return q_count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _reset_demo_appendix():
    """Restore the demo appendix files shipped with this version."""
    appendix_dir = os.path.join(DB_DIR, 'appendix')
    os.makedirs(appendix_dir, exist_ok=True)
    for week in range(1, 20):
        target = os.path.join(appendix_dir, f'week-{week}-appendix.md')
        try:
            os.remove(target)
        except FileNotFoundError:
            pass
        source = os.path.join(
            BASE_DIR, 'classes', 'demo', f'week-{week}-appendix.md'
        )
        if os.path.isfile(source):
            shutil.copyfile(source, target)


def init_demo_db(ensure_only=False):
    """Create, upgrade, or explicitly reset the demo database."""
    os.makedirs(DB_DIR, exist_ok=True)

    with _initialization_lock():
        if os.path.exists(DB_PATH):
            if ensure_only:
                if _read_demo_seed_version(DB_PATH) < DEMO_SEED_VERSION:
                    q_count = _reset_demo_db()
                    action = 'upgraded'
                else:
                    q_count = _validate_demo_db(
                        DB_PATH, require_seeded_shape=False
                    )
                    action = 'ready'
            else:
                q_count = _reset_demo_db()
                action = 'reset'
        else:
            q_count = _create_demo_db()
            action = 'created'
        if action != 'ready':
            _reset_demo_appendix()

    print(f"Demo database {action} at {DB_PATH}")
    print(f"  Instructor: demo_instructor")
    print(f"  Students:   2 (demo001-demo002, PIN='demo')")
    print(f"  Teams:      2 (Team 1-2)")
    print(f"  Questions:  {q_count}")
    return q_count


if __name__ == '__main__':
    if '--check' in sys.argv:
        sys.exit(0 if os.path.exists(DB_PATH) else 1)
    init_demo_db(ensure_only='--ensure' in sys.argv)
