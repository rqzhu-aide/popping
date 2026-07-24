import os
import json
import re
import sqlite3
from flask import g
import config

SLUG_RE = re.compile(r'^[A-Za-z0-9_-]+$')

# Process-local cache: slugs whose schema has already been verified/migrated.
# Without this, ensure_schema() runs ~10 PRAGMA queries on every API call.
_schema_checked = set()


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
        # timeout=30 sets a 30s busy_timeout so writers wait on lock contention
        # instead of raising "database is locked" immediately.
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        # WAL: readers don't block the writer or each other — essential for the
        # ~3s student polling load. foreign_keys: actually enforce the schema's
        # FK constraints (off by default in SQLite).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
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
    slug = validate_slug(slug)
    course_dir = os.path.join(config.DATA_DIR, slug)
    os.makedirs(course_dir, exist_ok=True)
    db_path = os.path.join(course_dir, 'popping.db')
    conn = sqlite3.connect(db_path)
    with open(config.DATABASE_SCHEMA, 'r') as f:
        conn.executescript(f.read())
    conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def ensure_schema(slug):
    """Run idempotent migrations once per process and safely across workers."""
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


def _ensure_schema_locked(db):
    """Add missing columns/tables to existing databases (migration).
    Runs only once per slug per process; subsequent calls are a no-op."""
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
    db.execute('''CREATE INDEX IF NOT EXISTS idx_ratings_export_week
                  ON presentation_ratings(course_id, week_num)''')

    # One-time, data-preserving cleanup for databases created before numbered
    # default team names were introduced.  Custom team names are untouched.
    greek_defaults = {
        'Alpha': 'Team 1', 'Beta': 'Team 2', 'Gamma': 'Team 3',
        'Delta': 'Team 4', 'Epsilon': 'Team 5', 'Zeta': 'Team 6',
        'Eta': 'Team 7', 'Theta': 'Team 8', 'Iota': 'Team 9',
        'Kappa': 'Team 10',
    }
    for course in db.execute('SELECT id FROM courses').fetchall():
        rows = db.execute(
            'SELECT id, name FROM teams WHERE course_id = ?', [course['id']]
        ).fetchall()
        by_name = {row['name']: row['id'] for row in rows}
        renames = [
            (row['id'], row['name'], greek_defaults[row['name']])
            for row in rows if row['name'] in greek_defaults
            and greek_defaults[row['name']] not in by_name
        ]
        for team_id, _old_name, _new_name in renames:
            db.execute(
                'UPDATE teams SET name = ? WHERE id = ?',
                [f'__default_team_{team_id}__', team_id]
            )
        for team_id, _old_name, new_name in renames:
            db.execute('UPDATE teams SET name = ? WHERE id = ?', [new_name, team_id])
        if not renames:
            continue

        rename_by_old = {
            old_name: (team_id, new_name)
            for team_id, old_name, new_name in renames
        }
        state = db.execute(
            '''SELECT id, presentation_history FROM course_state
               WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        try:
            history = json.loads(state['presentation_history'] or '[]') \
                if state else []
        except (TypeError, ValueError):
            history = []
        history_changed = False
        for item in history:
            old_name = item.get('team')
            if old_name not in rename_by_old:
                continue
            team_id, new_name = rename_by_old[old_name]
            item['team'] = new_name
            item['team_id'] = team_id
            history_changed = True
            question_key = item.get('presentation_key') or (
                f"pres-{item.get('started_at', '')}" if item.get('started_at') else None
            )
            if question_key:
                db.execute(
                    '''UPDATE presentation_ratings
                       SET presenting_team_id = COALESCE(presenting_team_id, ?),
                           presenting_team_name = ?
                       WHERE course_id = ? AND question_key = ?
                         AND (presenting_team_name IS NULL OR presenting_team_name = ?)''',
                    [team_id, new_name, course['id'], question_key, old_name]
                )
        if history_changed:
            db.execute(
                'UPDATE course_state SET presentation_history = ? WHERE id = ?',
                [json.dumps(history), state['id']]
            )
        for team_id, old_name, new_name in renames:
            db.execute(
                '''UPDATE presentation_ratings
                   SET presenting_team_id = COALESCE(presenting_team_id, ?),
                       presenting_team_name = ?
                   WHERE course_id = ? AND presenting_team_name = ?''',
                [team_id, new_name, course['id'], old_name]
            )
            db.execute(
                '''UPDATE presentation_ratings
                   SET rater_team_id = COALESCE(rater_team_id, ?),
                       rater_team_name = ?
                   WHERE course_id = ? AND rater_team_name = ?''',
                [team_id, new_name, course['id'], old_name]
            )

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
