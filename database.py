import sqlite3
from flask import g
import config


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(config.DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(config.DATABASE)
    with open(config.DATABASE.replace('.db', '.sql'), 'r') as f:
        db.executescript(f.read())
    db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv


def execute_db(query, args=()):
    db = get_db()
    cur = db.execute(query, args)
    db.commit()
    return cur.lastrowid
