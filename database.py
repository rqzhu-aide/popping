import os
import sqlite3
from flask import g
import config


def get_db(slug):
    db_key = f'db_{slug}'
    if not hasattr(g, db_key):
        db_path = os.path.join(config.DATA_DIR, slug, 'popping.db')
        if not os.path.exists(db_path):
            raise RuntimeError(f"Database not found for course: {slug}")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
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
    course_dir = os.path.join(config.DATA_DIR, slug)
    os.makedirs(course_dir, exist_ok=True)
    db_path = os.path.join(course_dir, 'popping.db')
    conn = sqlite3.connect(db_path)
    with open(config.DATABASE_SCHEMA, 'r') as f:
        conn.executescript(f.read())
    conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)

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
