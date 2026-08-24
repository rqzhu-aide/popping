"""Small, isolated demo-course instances.

Each public demo gets a random course slug and its own SQLite database.  The
normal classroom routes can therefore serve the demo without any demo-only
grading or phase rules, while visitors never share identities or responses.
"""

import os
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from database import migrate_schema_connection, upgrade_schema_connection
from question_catalog import read_week_questions


DEMO_INSTANCE_RE = re.compile(r'^demo_[0-9a-f]{32}$')
DEMO_SEED_VERSION = 3
DEMO_INSTANCE_TTL_SECONDS = 2 * 60 * 60
MAX_DEMO_INSTANCES = 4
MAX_EXPIRED_REMOVALS_PER_START = 8
DEMO_RESET_COOLDOWN_SECONDS = 10
DEMO_LIFECYCLE_DB = '.demo-lifecycle.sqlite3'


class DemoLifecycleBusy(RuntimeError):
    """Another worker is creating or cleaning up a demo instance."""


class DemoResetCooldown(RuntimeError):
    """The same private demo was reset too recently."""

    def __init__(self, retry_after):
        super().__init__('Please wait before resetting again.')
        self.retry_after = max(0.0, float(retry_after))


def is_demo_instance_slug(slug):
    return isinstance(slug, str) and DEMO_INSTANCE_RE.fullmatch(slug) is not None


def canonical_class_slug(slug):
    return 'demo' if is_demo_instance_slug(slug) else slug


def course_class_dir(classes_dir, slug):
    return os.path.join(classes_dir, canonical_class_slug(slug))


def demo_instance_dir(data_dir, slug):
    if not is_demo_instance_slug(slug):
        raise ValueError('Invalid demo instance')
    return os.path.join(data_dir, slug)


def demo_database_path(data_dir, slug):
    return os.path.join(demo_instance_dir(data_dir, slug), 'popping.db')


@contextmanager
def _demo_lifecycle_lock(data_dir, timeout=0.0):
    """Serialize demo creation across threads and Gunicorn workers."""
    os.makedirs(data_dir, exist_ok=True)
    lock_path = os.path.join(data_dir, DEMO_LIFECYCLE_DB)
    connection = sqlite3.connect(lock_path, timeout=max(0.0, float(timeout)))
    try:
        connection.execute(
            f'PRAGMA busy_timeout = {max(0, int(float(timeout) * 1000))}'
        )
        try:
            connection.execute('BEGIN IMMEDIATE')
        except sqlite3.OperationalError as exc:
            error_code = getattr(exc, 'sqlite_errorcode', None)
            if (error_code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                    or 'locked' in str(exc).lower()):
                raise DemoLifecycleBusy() from exc
            raise
        try:
            yield
        finally:
            connection.rollback()
    finally:
        connection.close()


def _read_week_questions(classes_dir):
    question_path = os.path.join(classes_dir, 'demo', 'week-1-questions.md')
    return read_week_questions(question_path, week_num=1)


def _populate_demo(conn, slug, classes_dir):
    conn.execute(
        'INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)',
        ('demo_instructor', 'Demo Instructor', 'demo'),
    )
    instructor_id = conn.execute(
        "SELECT id FROM instructors WHERE username = 'demo_instructor'"
    ).fetchone()[0]
    course_id = conn.execute(
        '''INSERT INTO courses
           (name, code, semester, slug, instructor_id, is_active)
           VALUES (?, ?, ?, ?, ?, 1)''',
        ('Private Popping Demo', 'DEMO 101', 'Private sandbox', slug, instructor_id),
    ).lastrowid

    for name, color in (
        ('Team 1', '#ef4444'),
        ('Team 2', '#3b82f6'),
    ):
        conn.execute(
            'INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)',
            (course_id, name, color),
        )

    # Start unassigned so the visitor can put the two students together to
    # demonstrate teammate thumbs, or on separate teams to demonstrate ratings.
    for student_id, name in (
        ('demo001', 'Demo Student 1'),
        ('demo002', 'Demo Student 2'),
    ):
        conn.execute(
            '''INSERT INTO students
               (course_id, student_id, name, pin, team_id, is_active)
               VALUES (?, ?, ?, 'demo', NULL, 1)''',
            (course_id, student_id, name),
        )

    for question in _read_week_questions(classes_dir):
        conn.execute(
            '''INSERT INTO questions
               (course_id, question_num, question_text, title, content,
                week_num, source_key)
               VALUES (?, ?, ?, ?, ?, 1, ?)''',
            (
                course_id,
                question['num'],
                question['title'][:200],
                question['title'],
                question['content'],
                question['source_key'],
            ),
        )

    conn.execute(
        '''INSERT INTO course_state
           (course_id, phase, max_teams, max_members_per_team)
           VALUES (?, 'setup', 2, 2)''',
        (course_id,),
    )
    conn.execute(f'PRAGMA user_version = {DEMO_SEED_VERSION}')


def _validate_demo(conn, slug):
    if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise RuntimeError('Demo database integrity check failed')
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('Demo database foreign key check failed')
    shape = conn.execute(
        '''SELECT
             (SELECT COUNT(*) FROM instructors),
             (SELECT COUNT(*) FROM students),
             (SELECT COUNT(*) FROM teams),
             (SELECT COUNT(*) FROM courses WHERE slug = ?)''',
        (slug,),
    ).fetchone()
    if tuple(shape) != (1, 2, 2, 1):
        raise RuntimeError('Demo database does not have the expected 1+2 shape')


def _restore_appendix_files(data_dir, classes_dir, slug):
    appendix_dir = os.path.join(demo_instance_dir(data_dir, slug), 'appendix')
    if os.path.isdir(appendix_dir):
        shutil.rmtree(appendix_dir)
    os.makedirs(appendix_dir, exist_ok=True)
    source_dir = os.path.join(classes_dir, 'demo')
    if not os.path.isdir(source_dir):
        return
    for name in os.listdir(source_dir):
        if re.fullmatch(r'week-\d+-appendix\.md', name):
            shutil.copyfile(
                os.path.join(source_dir, name),
                os.path.join(appendix_dir, name),
            )


def touch_demo_instance(data_dir, slug, now=None):
    instance_dir = demo_instance_dir(data_dir, slug)
    marker = os.path.join(instance_dir, '.last-used')
    Path(marker).touch(exist_ok=True)
    timestamp = time.time() if now is None else now
    os.utime(marker, (timestamp, timestamp))


def reusable_demo_instance(data_dir, slug, now=None):
    """Return whether a browser's remembered private demo is still live."""
    if not is_demo_instance_slug(slug):
        return False
    instance_dir = demo_instance_dir(data_dir, slug)
    database_path = os.path.join(instance_dir, 'popping.db')
    marker_path = os.path.join(instance_dir, '.last-used')
    if (not os.path.isfile(database_path)
            or not os.path.isfile(marker_path)):
        return False
    try:
        last_used = os.path.getmtime(marker_path)
    except OSError:
        return False
    checked_at = time.time() if now is None else float(now)
    return checked_at - last_used < DEMO_INSTANCE_TTL_SECONDS


def create_demo_instance(data_dir, classes_dir, schema_path, slug=None):
    slug = slug or f'demo_{uuid.uuid4().hex}'
    instance_dir = demo_instance_dir(data_dir, slug)
    if os.path.exists(instance_dir):
        raise FileExistsError('Demo instance already exists')
    os.makedirs(instance_dir, exist_ok=False)
    database_path = os.path.join(instance_dir, 'popping.db')
    temporary_path = os.path.join(instance_dir, f'.candidate-{uuid.uuid4().hex}.db')
    conn = None
    try:
        conn = sqlite3.connect(temporary_path)
        conn.execute('PRAGMA foreign_keys = ON')
        with open(schema_path, 'r', encoding='utf-8') as schema_file:
            conn.executescript(schema_file.read())
        upgrade_schema_connection(conn)
        _populate_demo(conn, slug, classes_dir)
        conn.commit()
        _validate_demo(conn, slug)
        conn.close()
        conn = None
        os.replace(temporary_path, database_path)
        _restore_appendix_files(data_dir, classes_dir, slug)
        touch_demo_instance(data_dir, slug)
        return slug
    except Exception:
        if conn is not None:
            conn.close()
        shutil.rmtree(instance_dir, ignore_errors=True)
        raise


def _cleanup_incomplete_demo_instances(data_dir):
    """Remove validly named demo directories left by an interrupted creation."""
    removed = []
    if not os.path.isdir(data_dir):
        return removed
    for name in os.listdir(data_dir):
        if not is_demo_instance_slug(name):
            continue
        path = os.path.join(data_dir, name)
        if not os.path.isdir(path):
            continue
        database_path = os.path.join(path, 'popping.db')
        marker_path = os.path.join(path, '.last-used')
        if os.path.isfile(database_path) and os.path.isfile(marker_path):
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed.append(name)
    return removed


def create_bounded_demo_instance(
        data_dir, classes_dir, schema_path, lock_timeout=0.0,
        reuse_slug=None):
    """Create or reuse one demo while atomically enforcing the shared cap."""
    with _demo_lifecycle_lock(data_dir, timeout=lock_timeout):
        removed = _cleanup_incomplete_demo_instances(data_dir)
        if reusable_demo_instance(data_dir, reuse_slug):
            removed.extend(cleanup_expired_demo_instances(
                data_dir, exclude=(reuse_slug,)
            ))
            touch_demo_instance(data_dir, reuse_slug)
            return reuse_slug, removed
        removed.extend(cleanup_expired_demo_instances(data_dir))
        if count_demo_instances(data_dir) >= MAX_DEMO_INSTANCES:
            return None, removed
        slug = create_demo_instance(data_dir, classes_dir, schema_path)
        return slug, removed


def reset_demo_instance(
        data_dir, classes_dir, slug,
        cooldown_seconds=DEMO_RESET_COOLDOWN_SECONDS, now=None):
    database_path = demo_database_path(data_dir, slug)
    conn = sqlite3.connect(database_path, timeout=1)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('BEGIN IMMEDIATE')
        migrate_schema_connection(conn)
        conn.execute(
            '''CREATE TABLE IF NOT EXISTS demo_metadata (
                   key TEXT PRIMARY KEY,
                   value REAL NOT NULL
               )'''
        )
        reset_at = time.time() if now is None else float(now)
        last_reset = conn.execute(
            "SELECT value FROM demo_metadata WHERE key = 'last_reset_at'"
        ).fetchone()
        if last_reset and cooldown_seconds:
            retry_after = float(cooldown_seconds) - (
                reset_at - float(last_reset[0])
            )
            if retry_after > 0:
                raise DemoResetCooldown(retry_after)
        for table in (
            'teammate_thumbs', 'presentation_ratings', 'peer_reviews',
            'login_attempts', 'course_state', 'questions', 'students', 'teams',
            'courses', 'instructors',
        ):
            conn.execute(f'DELETE FROM {table}')
        conn.execute('DELETE FROM sqlite_sequence')
        _populate_demo(conn, slug, classes_dir)
        _validate_demo(conn, slug)
        conn.execute(
            '''INSERT INTO demo_metadata (key, value)
               VALUES ('last_reset_at', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value''',
            (reset_at,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _restore_appendix_files(data_dir, classes_dir, slug)
    touch_demo_instance(data_dir, slug)


def cleanup_expired_demo_instances(data_dir, now=None, exclude=()):
    now = time.time() if now is None else now
    excluded = set(exclude)
    candidates = []
    if not os.path.isdir(data_dir):
        return []
    for name in os.listdir(data_dir):
        if name in excluded or not is_demo_instance_slug(name):
            continue
        path = os.path.join(data_dir, name)
        if not os.path.isdir(path):
            continue
        marker = os.path.join(path, '.last-used')
        try:
            last_used = os.path.getmtime(marker if os.path.exists(marker) else path)
        except OSError:
            continue
        if now - last_used >= DEMO_INSTANCE_TTL_SECONDS:
            candidates.append((last_used, name, path))

    removed = []
    for _last_used, name, path in sorted(candidates)[:MAX_EXPIRED_REMOVALS_PER_START]:
        shutil.rmtree(path, ignore_errors=True)
        if not os.path.exists(path):
            removed.append(name)
    return removed


def count_demo_instances(data_dir):
    if not os.path.isdir(data_dir):
        return 0
    return sum(
        1 for name in os.listdir(data_dir)
        if is_demo_instance_slug(name)
        and os.path.isdir(os.path.join(data_dir, name))
    )
