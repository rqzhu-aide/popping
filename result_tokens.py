"""One revocable results credential per course, alongside its persistent data."""
from contextlib import closing
import hashlib
import re
import secrets
import sqlite3
from pathlib import Path

import config


class TokenChanged(Exception):
    """The instructor submitted a form for an earlier token."""


def _token_db(slug):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', slug):
        raise ValueError('Invalid course')
    return Path(config.DATA_DIR, slug, 'results-token.db')


def _digest(slug, token):
    return hashlib.sha256(f'{slug}\0{token}'.encode('utf-8')).digest()


def _stored_digest(slug):
    path = _token_db(slug)
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        # A concurrent first issuance may still be creating the table.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='credential'").fetchone():
            return None
        row = db.execute('SELECT digest FROM credential WHERE slot=1').fetchone()
        return row[0] if row else None


def _revision(digest):
    # This opaque value lets a stale settings page detect a replacement.
    return hashlib.sha256(digest).hexdigest() if digest is not None else None


def token_revision(slug):
    return _revision(_stored_digest(slug))


def valid_result_token(slug, authorization_header):
    match = re.fullmatch(r'(?i:Bearer) +(popres_[A-Za-z0-9_-]{43})', authorization_header or '')
    if match is None:
        return False
    stored = _stored_digest(slug)
    return stored is not None and secrets.compare_digest(stored, _digest(slug, match[1]))


def change_result_token(slug, action, revision):
    if action not in ('create', 'replace', 'revoke'):
        raise ValueError('Choose a valid token action.')
    with closing(sqlite3.connect(_token_db(slug))) as db, db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE TABLE IF NOT EXISTS credential (
            slot INTEGER PRIMARY KEY CHECK (slot=1), digest BLOB NOT NULL
        )''')
        row = db.execute('SELECT digest FROM credential WHERE slot=1').fetchone()
        if action == 'create':
            if row:
                raise TokenChanged('This course already has a token. Reload its settings.')
        elif not row or revision != _revision(row[0]):
            raise TokenChanged('The course token has changed. Reload its settings.')
        if action == 'revoke':
            db.execute('DELETE FROM credential WHERE slot=1')
            return None, None

        token = 'popres_' + secrets.token_urlsafe(32)
        digest = _digest(slug, token)
        db.execute('INSERT OR REPLACE INTO credential(slot, digest) VALUES (1, ?)', (digest,))
        return token, _revision(digest)
