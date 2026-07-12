import os
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
    """Add missing columns/tables to existing databases (migration).
    Runs only once per slug per process — subsequent calls are a no-op."""
    if slug in _schema_checked:
        return
    db = get_db(slug)

    # course_state columns
    cs_cols = [row['name'] for row in db.execute('PRAGMA table_info(course_state)').fetchall()]
    if 'max_teams' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN max_teams INTEGER DEFAULT 8')
    if 'max_members_per_team' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN max_members_per_team INTEGER DEFAULT 5')
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
    if 'poll_active' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_active INTEGER DEFAULT 0')
    if 'poll_question_key' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_question_key TEXT')
    if 'poll_started_at' not in cs_cols:
        db.execute('ALTER TABLE course_state ADD COLUMN poll_started_at TIMESTAMP')
    if 'presentation_history' not in cs_cols:
        db.execute("ALTER TABLE course_state ADD COLUMN presentation_history TEXT DEFAULT '[]'")

    # questions columns
    q_cols = [row['name'] for row in db.execute('PRAGMA table_info(questions)').fetchall()]
    if 'title' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN title TEXT')
    if 'content' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN content TEXT')
    if 'week_num' not in q_cols:
        db.execute('ALTER TABLE questions ADD COLUMN week_num INTEGER DEFAULT 1')

    # students columns
    st_cols = [row['name'] for row in db.execute('PRAGMA table_info(students)').fetchall()]
    if 'last_login_at' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_login_at TIMESTAMP')
    if 'last_team_joined_at' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_team_joined_at TIMESTAMP')
    if 'last_team_id' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_team_id INTEGER')
    if 'last_active_at' not in st_cols:
        db.execute('ALTER TABLE students ADD COLUMN last_active_at TIMESTAMP')

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
    db.execute('''CREATE TABLE IF NOT EXISTS discussion_selections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        student_id INTEGER NOT NULL,
        question_key TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (course_id) REFERENCES courses (id),
        FOREIGN KEY (student_id) REFERENCES students (id),
        UNIQUE(course_id, student_id, question_key)
    )''')

    db.commit()
    _schema_checked.add(slug)


def get_max_teams(slug, course_id):
    """Get max_teams for a course, with fallback for old databases."""
    ensure_schema(slug)
    state = query_db(slug, 'SELECT max_teams FROM course_state WHERE course_id = ?', [course_id], one=True)
    if state and state['max_teams'] is not None:
        return state['max_teams']
    total = query_db(slug, 'SELECT COUNT(*) as c FROM teams WHERE course_id = ?', [course_id], one=True)
    return total['c'] if total else 5


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
