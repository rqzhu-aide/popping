import os
import sys
import csv
import copy
import gzip
import hashlib
import io
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from functools import wraps
from fractions import Fraction
from pathlib import Path

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash, g, make_response, send_file
)
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

import config
from database import init_app, query_db, execute_db, get_db, init_db, get_max_teams, get_max_members_per_team, ensure_schema
from question_catalog import validate_question_catalog

app = Flask(__name__)
app.config.from_object(config)
if os.environ.get('RENDER'):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
init_app(app)


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Request is too large'}), 413
    return 'Request is too large', 413

PHASES = ['setup', 'discussion', 'competition', 'ended']

PHASE_LABELS = {
    'setup': 'Setup',
    'discussion': 'Group Discussion',
    'competition': 'Group Presentation',
    'ended': 'End Session'
}

# Duration (seconds) of the presentation rating window.
POLL_DURATION = 30

# Throttle window for the last_active_at DB write. Students poll /api/poll
# frequently, but the "online" indicator uses a three-minute window, so one
# write per minute is sufficient (last write time is tracked in the session
# cookie). This keeps presence writes low under a full classroom.
ACTIVITY_WRITE_INTERVAL = 60

# Compress JSON responses large enough to benefit. Poll responses are the
# dominant classroom traffic; gzip reduces repeated keys and question HTML
# substantially without adding a third-party dependency.
JSON_COMPRESSION_MIN_BYTES = 500
LOGIN_FAILURE_LIMIT = 8
LOGIN_WINDOW_SECONDS = 600
LOGIN_CLIENT_FAILURE_LIMIT = 500
MAX_ROSTER_BYTES = 1024 * 1024
MAX_ROSTER_ROWS = 500
MAX_EXPORT_ROWS = 100000
MAX_EXPORT_BYTES = 50 * 1024 * 1024
COURSE_AVAILABILITY_TTL = 30
REQUIRED_COURSE_SCHEMA = {
    'instructors': {'id', 'username', 'name', 'pin'},
    'courses': {
        'id', 'name', 'code', 'semester', 'slug', 'instructor_id', 'is_active'
    },
    'teams': {'id', 'course_id', 'name', 'color'},
    'students': {
        'id', 'course_id', 'student_id', 'name', 'pin', 'team_id'
    },
    'questions': {'id', 'course_id', 'question_num', 'question_text'},
    'course_state': {
        'id', 'course_id', 'phase', 'active_team_id', 'active_question_id',
        'current_question', 'presentation_started_at'
    },
}
REQUIRED_COURSE_TABLES = frozenset(REQUIRED_COURSE_SCHEMA)

_course_availability_cache = {}
_course_availability_lock = threading.RLock()


def is_valid_slug(slug):
    return isinstance(slug, str) and re.fullmatch(r'[A-Za-z0-9_-]+', slug)


def _parse_db_datetime(dt_str):
    """Parse a SQLite datetime string (space or T separator) to a UTC datetime."""
    if not dt_str:
        return None
    return datetime.fromisoformat(str(dt_str).replace(' ', 'T'))


def course_db_path(slug):
    if not is_valid_slug(slug):
        return None
    return os.path.join(config.DATA_DIR, slug, 'popping.db')


def active_presentation_key(state):
    """Stable key for the current presentation, even if its timer is paused."""
    if not state:
        return None
    if 'poll_question_key' in state.keys() and state['poll_question_key']:
        return state['poll_question_key']
    if 'presentation_started_at' in state.keys() and state['presentation_started_at']:
        return f"pres-{state['presentation_started_at']}"
    return None


def active_discussion_source_key(state):
    """Return the reusable question-bank key for the current discussion post."""
    if not state:
        return None
    if ('current_discussion_source_key' in state.keys() and
            state['current_discussion_source_key']):
        return state['current_discussion_source_key']
    # Existing databases used the live discussion key as the source key.
    return state['current_discussion_key']


def _presentation_guard(data, state):
    """Validate that an instructor action still targets the displayed presentation."""
    expected_key = str(data.get('presentation_key') or '').strip()
    if not expected_key:
        return 'Presentation key is required', 400
    current_key = active_presentation_key(state)
    if (not state or state['phase'] != 'competition' or
            not state['active_team_id'] or not state['active_question_id'] or
            not current_key):
        return 'No active presentation', 409
    if expected_key != current_key:
        return 'This instructor page is stale; reload before controlling the presentation', 409
    return None


def _expected_state_guard(data, state):
    """Reject a state-changing request sent from a stale instructor page."""
    expected_phase = data.get('expected_phase')
    expected_session_key = data.get('expected_session_key')
    if expected_phase is None or expected_session_key is None:
        return 'Expected phase and session key are required', 400
    try:
        expected_session_key = int(expected_session_key)
    except (TypeError, ValueError):
        return 'Invalid session key', 400
    if (not state or expected_phase != state['phase'] or
            expected_session_key != (state['session_key'] or 0)):
        return 'This instructor page is stale; reload before changing the course state', 409
    return None


def _expected_roster_state_guard(data, state):
    """Reject roster changes based on an out-of-date instructor roster."""
    guard = _expected_state_guard(data, state)
    if guard:
        return guard
    expected_version = data.get('expected_roster_version')
    if expected_version is None:
        return 'Expected roster version is required', 400
    try:
        expected_version = int(expected_version)
    except (TypeError, ValueError):
        return 'Invalid roster version', 400
    current_version = state['roster_version'] or 0
    if expected_version != current_version:
        return 'The roster changed in another page; reload before continuing', 409
    return None


def _expected_discussion_state_guard(data, state):
    """Reject a post or unpost that targets an older discussion state."""
    guard = _expected_state_guard(data, state)
    if guard:
        return guard
    if 'expected_discussion_key' not in data:
        return 'Expected discussion key is required', 400
    expected_key = str(data.get('expected_discussion_key') or '')
    current_key = str(state['current_discussion_key'] or '')
    if expected_key != current_key:
        return 'The posted discussion question changed; reload before continuing', 409
    return None


def _create_reset_backup(slug):
    """Create a consistent SQLite backup immediately before destructive reset."""
    source_path = course_db_path(slug)
    backup_dir = os.path.join(config.DATA_DIR, slug, 'reset-backups')
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%S%f')
    backup_path = os.path.join(backup_dir, f'popping-before-reset-{stamp}.db')
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
        integrity = [row[0] for row in target.execute(
            'PRAGMA integrity_check'
        ).fetchall()]
        foreign_keys = target.execute('PRAGMA foreign_key_check').fetchall()
        if integrity != ['ok'] or foreign_keys:
            raise RuntimeError('Reset backup validation failed')
    except Exception:
        target.close()
        source.close()
        try:
            os.remove(backup_path)
        except OSError:
            pass
        raise
    else:
        target.close()
        source.close()
    backups = sorted(
        os.path.join(backup_dir, name)
        for name in os.listdir(backup_dir)
        if name.startswith('popping-before-reset-') and name.endswith('.db')
    )
    for old_path in backups[:-3]:
        try:
            os.remove(old_path)
        except OSError:
            app.logger.warning('Could not remove old reset backup %s', old_path)
    return os.path.basename(backup_path)


def _poll_is_open(state, now=None):
    """Return whether the persisted poll window is currently accepting ratings."""
    if not state or not state['poll_active'] or not state['poll_started_at']:
        return False
    try:
        started = _parse_db_datetime(state['poll_started_at'])
        return (now or datetime.utcnow()) < started + timedelta(seconds=POLL_DURATION)
    except (TypeError, ValueError):
        return False


def _derive_timing_state(state, now=None):
    """Return server-authoritative timer values for one shared UTC instant."""
    if not state:
        return {
            'presentation_remaining': None,
            'poll_remaining': 0,
            'session_elapsed': None,
        }
    now = now or datetime.utcnow()
    presentation_remaining = state['presentation_remaining']
    if state['presentation_started_at'] and state['presentation_time_cap']:
        try:
            started = _parse_db_datetime(state['presentation_started_at'])
            remaining = (state['presentation_time_cap'] or 300) - (
                now - started
            ).total_seconds()
            presentation_remaining = max(0, math.ceil(remaining))
        except (TypeError, ValueError):
            pass

    poll_remaining = 0
    if state['poll_active'] and state['poll_started_at']:
        try:
            started = _parse_db_datetime(state['poll_started_at'])
            remaining = POLL_DURATION - (now - started).total_seconds()
            poll_remaining = max(0, math.ceil(remaining))
        except (TypeError, ValueError):
            pass

    session_elapsed = None
    if state['session_started_at']:
        try:
            started = _parse_db_datetime(state['session_started_at'])
            session_elapsed = max(0, math.floor((now - started).total_seconds()))
        except (TypeError, ValueError):
            pass
    return {
        'presentation_remaining': presentation_remaining,
        'poll_remaining': poll_remaining,
        'session_elapsed': session_elapsed,
    }


class QuestionParseError(ValueError):
    pass


def parse_question_blocks(content):
    """Parse repeated YAML-frontmatter blocks without splitting body Markdown."""
    import yaml

    normalized = content.replace('\r\n', '\n').replace('\r', '\n')
    if not normalized.strip():
        return []
    lines = normalized.split('\n')
    delimiters = []
    fence_char = None
    fence_length = 0
    fence_line = None
    for index, line in enumerate(lines):
        fence = re.match(r'^\s*(`{3,}|~{3,})', line)
        if fence:
            marker = fence.group(1)
            if fence_char is None:
                fence_char = marker[0]
                fence_length = len(marker)
                fence_line = index
            elif marker[0] == fence_char and len(marker) >= fence_length:
                fence_char = None
                fence_length = 0
                fence_line = None
            continue
        if fence_char is None and re.fullmatch(r'\s*---\s*', line):
            delimiters.append(index)

    if fence_char is not None:
        raise QuestionParseError(
            f'Unclosed fenced code block near line {fence_line + 1}'
        )

    headers = []
    position = 0
    while position + 1 < len(delimiters):
        opening = delimiters[position]
        closing = delimiters[position + 1]
        frontmatter = '\n'.join(lines[opening + 1:closing]).strip()
        try:
            metadata = yaml.safe_load(frontmatter) if frontmatter else None
        except yaml.YAMLError:
            position += 1
            continue
        if isinstance(metadata, dict) and str(metadata.get('title') or '').strip():
            headers.append((opening, closing, frontmatter))
            position += 2
        else:
            position += 1

    if not headers:
        raise QuestionParseError('No valid question frontmatter found')
    used_delimiters = {
        delimiter for opening, closing, _frontmatter in headers
        for delimiter in (opening, closing)
    }
    for delimiter_position, delimiter in enumerate(delimiters):
        if delimiter in used_delimiters:
            continue
        next_delimiter = delimiters[delimiter_position + 1] \
            if delimiter_position + 1 < len(delimiters) else len(lines)
        candidate = '\n'.join(lines[delimiter + 1:next_delimiter]).strip()
        first_line = candidate.split('\n', 1)[0] if candidate else ''
        if re.match(r'^\s*title\s*:', first_line):
            if next_delimiter == len(lines):
                raise QuestionParseError(
                    f'Unclosed question frontmatter near line {delimiter + 1}'
                )
            raise QuestionParseError(
                f'Invalid question frontmatter near line {delimiter + 1}'
            )
    if any(line.strip() for line in lines[:headers[0][0]]):
        raise QuestionParseError('Unexpected content before the first question')

    entries = []
    for index, (_opening, closing, frontmatter) in enumerate(headers):
        body_end = headers[index + 1][0] if index + 1 < len(headers) else len(lines)
        body = '\n'.join(lines[closing + 1:body_end]).strip()
        entries.append((frontmatter, body))
    return entries


def _serialize_question_blocks(entries):
    if not entries:
        return ''
    return ''.join(
        f"---\n{frontmatter.strip()}\n---\n\n{body.strip()}\n\n"
        for frontmatter, body in entries
    ).rstrip() + '\n'


def _write_text_atomic(path, content):
    """Replace a text file atomically on the same filesystem."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f'{path}.tmp-{uuid.uuid4().hex}'
    try:
        with open(temporary, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def read_presentation_question_index(slug, week_num):
    week_dir = os.path.join(config.CLASSES_DIR, slug, f'week{week_num}')
    index_path = os.path.join(week_dir, 'index.md')
    if not os.path.exists(index_path):
        return None

    questions = []
    with open(index_path, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d+)\.\s+(.+)$', line)
            if m:
                qnum = int(m.group(1))
                title = m.group(2).strip()
                questions.append({'num': qnum, 'title': title})
    return questions


def sync_presentation_questions(
        slug, course_id, week_num, db=None, commit=True):
    """Sync a week's pre-rendered questions without changing stable row IDs."""
    questions = read_presentation_question_index(slug, week_num)
    if questions is None:
        return None

    owns_transaction = db is None
    if owns_transaction:
        ensure_schema(slug)
        db = get_db(slug)
        db.execute('BEGIN IMMEDIATE')
    try:
        existing = db.execute(
            '''SELECT * FROM questions
               WHERE course_id = ? AND COALESCE(week_num, 1) = ?
                 AND (source_key IS NULL OR source_key LIKE 'presentation:%')''',
            [course_id, week_num]
        ).fetchall()
        by_source = {
            row['source_key']: row for row in existing if row['source_key']
        }
        legacy_by_num = {}
        for row in existing:
            if not row['source_key']:
                legacy_by_num.setdefault(row['question_num'], []).append(row)

        retained_ids = set()
        changed = False
        for q in questions:
            source_key = f"presentation:{week_num}:{q['num']}"
            row = by_source.get(source_key)
            if row is None:
                candidates = legacy_by_num.get(q['num'], [])
                row = next((candidate for candidate in candidates
                            if candidate['id'] not in retained_ids), None)

            question_text = q['title'][:200]
            if row is None:
                cursor = db.execute(
                    '''INSERT INTO questions
                       (course_id, question_num, question_text, title, week_num,
                        source_key)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    [course_id, q['num'], question_text, q['title'], week_num,
                     source_key]
                )
                retained_ids.add(cursor.lastrowid)
                changed = True
            else:
                retained_ids.add(row['id'])
                if (row['question_num'] != q['num'] or
                        row['question_text'] != question_text or
                        row['title'] != q['title'] or
                        row['week_num'] != week_num or
                        row['source_key'] != source_key):
                    db.execute(
                        '''UPDATE questions
                           SET question_num = ?, question_text = ?, title = ?,
                               week_num = ?, source_key = ?
                           WHERE id = ?''',
                        [q['num'], question_text, q['title'], week_num,
                         source_key, row['id']]
                    )
                    changed = True

        for row in existing:
            if row['id'] not in retained_ids:
                db.execute('DELETE FROM questions WHERE id = ?', [row['id']])
                changed = True

        if commit:
            db.commit()
        return len(questions)
    except Exception:
        if owns_transaction:
            db.rollback()
        raise


def _read_appendix_question_rows(slug, week_num):
    """Read valid appendix blocks and assign stable presentation source keys."""
    appendix_path = _appendix_path(slug, week_num)
    if not os.path.exists(appendix_path):
        return []

    with open(appendix_path, 'r', encoding='utf-8-sig') as f:
        entries = parse_question_blocks(f.read())

    import yaml
    rows = []
    seen_numbers = set()
    for position, (fm_block, body_block) in enumerate(entries, 1):
        try:
            metadata = yaml.safe_load(fm_block) or {}
        except yaml.YAMLError as exc:
            raise QuestionParseError(
                f'Invalid appendix frontmatter in question {position}'
            ) from exc
        title = str(metadata.get('title') or '').strip()
        if not title:
            raise QuestionParseError(
                f'Appendix question {position} has no title'
            )

        body = body_block.strip()
        label_match = re.match(r'^A(\d+)\s*:', title, re.IGNORECASE)
        if not label_match:
            raise QuestionParseError(
                f'Appendix question {position} must start with an A-number label'
            )
        question_num = int(label_match.group(1))
        if question_num in seen_numbers:
            raise QuestionParseError(
                f'Duplicate appendix label A{question_num}'
            )
        seen_numbers.add(question_num)
        rows.append({
            'source_key': f'appendix:{week_num}:A{question_num}',
            'question_num': question_num,
            'question_text': title[:200],
            'title': title,
            'content': body,
        })
    return rows


def sync_appendix_questions(slug, course_id, week_num, db=None, commit=True):
    """Make a week's appendix questions selectable during presentations."""
    owns_transaction = db is None
    if owns_transaction:
        ensure_schema(slug)
        db = get_db(slug)
        db.execute('BEGIN IMMEDIATE')
    try:
        desired = _read_appendix_question_rows(slug, week_num)
        source_prefix = f'appendix:{week_num}:%'
        existing = db.execute(
            '''SELECT * FROM questions
               WHERE course_id = ? AND source_key LIKE ?''',
            [course_id, source_prefix]
        ).fetchall()
        by_source = {row['source_key']: row for row in existing}
        retained_ids = set()
        changed = False

        for question in desired:
            row = by_source.get(question['source_key'])
            if row is None:
                # Preserve IDs from the short-lived content-hash key scheme.
                row = next((candidate for candidate in existing
                            if candidate['id'] not in retained_ids and
                            not candidate['source_key'].startswith(
                                f'appendix:{week_num}:A') and
                            candidate['title'] == question['title'] and
                            candidate['content'] == question['content']), None)
            if row is None:
                cursor = db.execute(
                    '''INSERT INTO questions
                       (course_id, question_num, question_text, title, content,
                        week_num, source_key)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    [course_id, question['question_num'],
                     question['question_text'], question['title'],
                     question['content'], week_num, question['source_key']]
                )
                retained_ids.add(cursor.lastrowid)
                changed = True
            else:
                retained_ids.add(row['id'])
                if (row['question_num'] != question['question_num'] or
                        row['question_text'] != question['question_text'] or
                        row['title'] != question['title'] or
                        row['content'] != question['content'] or
                        row['week_num'] != week_num or
                        row['source_key'] != question['source_key']):
                    db.execute(
                        '''UPDATE questions
                           SET question_num = ?, question_text = ?, title = ?,
                               content = ?, week_num = ?, source_key = ?
                           WHERE id = ?''',
                        [question['question_num'], question['question_text'],
                         question['title'], question['content'], week_num,
                         question['source_key'], row['id']]
                    )
                    changed = True

        for row in existing:
            if row['id'] not in retained_ids:
                db.execute('DELETE FROM questions WHERE id = ?', [row['id']])
                changed = True

        if commit:
            db.commit()
        return len(desired)
    except Exception:
        if owns_transaction:
            db.rollback()
        raise


# In-memory cache for pre-rendered question HTML files.
# Without this, /api/state reads the same .html file from disk ~100 times/sec
# (once per student per poll).  Key: (slug, week_num, question_num).
_question_html_cache = {}


def load_question_html(slug, week_num, question_num):
    """Read pre-rendered HTML for a question from the week folder.

    Path: classes/<slug>/week<N>/q<NN>.html  (zero-padded, e.g. q01.html)
    Returns HTML string or None if file not found.
    Cached results are invalidated when the file changes.
    """
    cache_key = (slug, week_num, question_num)
    filepath = os.path.join(config.CLASSES_DIR, slug, f'week{week_num}', f'q{question_num:02d}.html')
    try:
        stat = os.stat(filepath)
        signature = (stat.st_mtime_ns, stat.st_size)
        cached = _question_html_cache.get(cache_key)
        if cached and cached['signature'] == signature:
            return cached['html']
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            html = f.read()
        _question_html_cache[cache_key] = {
            'signature': signature,
            'html': html,
        }
        return html
    except (FileNotFoundError, IOError):
        _question_html_cache.pop(cache_key, None)
        return None


@app.before_request
def track_student_activity():
    if _exclusive_session_role() == 'student':
        now = datetime.utcnow()
        last_synced = None
        last_iso = session.get('last_active_synced_at')
        if last_iso:
            try:
                last_synced = datetime.fromisoformat(last_iso)
            except ValueError:
                last_synced = None
        # Throttle: write at most every ACTIVITY_WRITE_INTERVAL seconds per
        # student, instead of on every (3s) poll.
        if last_synced is None or (now - last_synced).total_seconds() >= ACTIVITY_WRITE_INTERVAL:
            try:
                execute_db(session['slug'],
                    "UPDATE students SET last_active_at = CURRENT_TIMESTAMP WHERE student_id = ? AND is_active = 1",
                    [session['student_id']]
                )
                session['last_active_synced_at'] = now.isoformat()
                state = query_db(
                    session['slug'], 'SELECT session_key FROM course_state LIMIT 1',
                    one=True
                )
                if state:
                    session['activity_session_key'] = state['session_key'] or 0
            except Exception:
                pass  # db might not exist yet on first request


@app.after_request
def compress_json_response(response):
    """Gzip sizeable JSON responses when the client advertises support."""
    if response.direct_passthrough or response.status_code < 200 or response.status_code >= 300:
        return response
    if response.mimetype != 'application/json' or response.headers.get('Content-Encoding'):
        return response
    response.vary.add('Accept-Encoding')
    if 'gzip' not in request.headers.get('Accept-Encoding', '').lower():
        return response

    data = response.get_data()
    if len(data) < JSON_COMPRESSION_MIN_BYTES:
        return response
    compressed = gzip.compress(data, compresslevel=5)
    if len(compressed) >= len(data):
        return response

    response.set_data(compressed)
    response.headers['Content-Encoding'] = 'gzip'
    response.headers['Content-Length'] = len(compressed)
    return response


@app.template_filter('phase_label')
def phase_label_filter(phase):
    return PHASE_LABELS.get(phase, phase.upper())


@app.template_filter('display_name')
def display_name_filter(student):
    """Format student display as 'Name (id)' or just 'id' if no name."""
    if hasattr(student, 'keys'):
        # sqlite3.Row
        keys = student.keys()
        name = student['name'] if 'name' in keys else None
        sid = student['student_id'] if 'student_id' in keys else None
    elif isinstance(student, dict):
        name = student.get('name')
        sid = student.get('student_id')
    else:
        name = getattr(student, 'name', None)
        sid = getattr(student, 'student_id', None)
    if name and sid and name != sid:
        return f"{name} ({sid})"
    return sid or name or 'Unknown'


def _auth_failure():
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not logged in'}), 401
    return redirect(url_for('index'))


def _login_client_hash():
    address = request.remote_addr or 'unknown'
    return hashlib.sha256(address.encode('utf-8')).hexdigest()


def _login_retry_after(slug, course_id, login_type, principal, client_hash):
    """Return seconds until another login may be tried, or zero."""
    now = datetime.utcnow()
    window_start = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)
    row = query_db(
        slug,
        '''SELECT failed_count, window_started_at, blocked_until
           FROM login_attempts
           WHERE course_id = ? AND login_type = ? AND principal = ?
             AND client_hash = ?''',
        [course_id, login_type, principal, client_hash], one=True
    )
    if row:
        started_at = _parse_db_datetime(row['window_started_at'])
        blocked_until = _parse_db_datetime(row['blocked_until'])
        if blocked_until and blocked_until > now:
            return max(1, int((blocked_until - now).total_seconds()) + 1)
        if (started_at and started_at > window_start and
                row['failed_count'] >= LOGIN_FAILURE_LIMIT):
            return max(
                1,
                int((started_at + timedelta(seconds=LOGIN_WINDOW_SECONDS) - now)
                    .total_seconds()) + 1,
            )

    client_failures = query_db(
        slug,
        '''SELECT COALESCE(SUM(failed_count), 0) AS c,
                  MIN(window_started_at) AS oldest
           FROM login_attempts
           WHERE course_id = ? AND login_type = ? AND client_hash = ?
             AND window_started_at > ?''',
        [course_id, login_type, client_hash,
         window_start.strftime('%Y-%m-%d %H:%M:%S')], one=True
    )
    if client_failures and client_failures['c'] >= LOGIN_CLIENT_FAILURE_LIMIT:
        oldest = _parse_db_datetime(client_failures['oldest']) or now
        return max(
            1,
            int((oldest + timedelta(seconds=LOGIN_WINDOW_SECONDS) - now)
                .total_seconds()) + 1,
        )
    return 0


def _record_login_failure(slug, course_id, login_type, principal, client_hash):
    """Record one failed login atomically across application workers."""
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        now = datetime.utcnow()
        db.execute(
            '''DELETE FROM login_attempts
               WHERE window_started_at <= datetime('now', ?)
                 AND (blocked_until IS NULL OR blocked_until <= CURRENT_TIMESTAMP)''',
            [f'-{LOGIN_WINDOW_SECONDS} seconds']
        )
        row = db.execute(
            '''SELECT * FROM login_attempts
               WHERE course_id = ? AND login_type = ? AND principal = ?
                 AND client_hash = ?''',
            [course_id, login_type, principal, client_hash]
        ).fetchone()
        window_start = now - timedelta(seconds=LOGIN_WINDOW_SECONDS)
        if not row or _parse_db_datetime(row['window_started_at']) <= window_start:
            db.execute(
                '''INSERT INTO login_attempts
                   (course_id, login_type, principal, client_hash, failed_count,
                    window_started_at, blocked_until)
                   VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, NULL)
                   ON CONFLICT(course_id, login_type, principal, client_hash)
                   DO UPDATE SET failed_count = 1,
                       window_started_at = CURRENT_TIMESTAMP,
                       blocked_until = NULL''',
                [course_id, login_type, principal, client_hash]
            )
        else:
            failed_count = row['failed_count'] + 1
            blocked_until = None
            if failed_count >= LOGIN_FAILURE_LIMIT:
                blocked_until = (
                    now + timedelta(seconds=LOGIN_WINDOW_SECONDS)
                ).strftime('%Y-%m-%d %H:%M:%S')
            db.execute(
                '''UPDATE login_attempts
                   SET failed_count = ?, blocked_until = ?
                   WHERE id = ?''',
                [failed_count, blocked_until, row['id']]
            )
        db.commit()
    except Exception:
        db.rollback()
        raise


def _clear_login_failures(slug, course_id, login_type, principal, client_hash):
    execute_db(
        slug,
        '''DELETE FROM login_attempts
           WHERE course_id = ? AND login_type = ? AND principal = ?
             AND client_hash = ?''',
        [course_id, login_type, principal, client_hash]
    )


def _rate_limited_login_response(template, slug, course, retry_after):
    flash('Too many failed login attempts. Please try again later.', 'error')
    response = make_response(
        render_template(template, course=course, slug=slug), 429
    )
    response.headers['Retry-After'] = str(retry_after)
    return response


def _exclusive_session_role():
    """Return the role only when exactly one authenticated role is present."""
    role = session.get('role')
    has_student = bool(session.get('student_id'))
    has_instructor = bool(session.get('instructor_id'))
    if not session.get('slug') or has_student == has_instructor:
        return None
    if role == 'student' and has_student:
        return 'student'
    if role == 'instructor' and has_instructor:
        return 'instructor'
    return None


def student_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        slug = session.get('slug')
        if _exclusive_session_role() != 'student':
            return _auth_failure()
        if _course_availability(slug)['status'] != 'ready':
            session.clear()
            return _auth_failure()
        ensure_schema(slug)
        student = query_db(
            slug,
            '''SELECT s.* FROM students s JOIN courses c ON s.course_id = c.id
               WHERE s.student_id = ? AND c.slug = ? AND s.is_active = 1
                 AND c.is_active = 1''',
            [session['student_id'], slug], one=True
        )
        if not student:
            session.clear()
            return _auth_failure()
        g.current_student = student
        return f(*args, **kwargs)
    return decorated


def instructor_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        slug = session.get('slug')
        route_slug = kwargs.get('slug')
        if (_exclusive_session_role() != 'instructor' or
                (route_slug is not None and route_slug != slug)):
            return _auth_failure()
        if _course_availability(slug)['status'] != 'ready':
            session.clear()
            return _auth_failure()
        instructor = query_db(
            slug,
            '''SELECT i.id FROM instructors i JOIN courses c ON c.instructor_id = i.id
               WHERE i.id = ? AND c.slug = ? AND c.is_active = 1''',
            [session['instructor_id'], slug], one=True
        )
        if not instructor:
            session.clear()
            return _auth_failure()
        ensure_schema(slug)
        return f(*args, **kwargs)
    return decorated


def _inspect_course_availability(slug):
    """Verify one configured course without modifying its database."""
    result = {
        'slug': slug,
        'status': 'invalid',
        'message': 'Course setup could not be verified.',
        'configured_active': False,
        'config': None,
        'course': None,
        'has_db': False,
    }
    if not is_valid_slug(slug):
        return result

    class_dir = os.path.join(config.CLASSES_DIR, slug)
    yaml_path = os.path.join(class_dir, 'course.yaml')
    if not os.path.isdir(class_dir) or not os.path.isfile(yaml_path):
        return result

    try:
        import yaml
    except ImportError:
        return result
    try:
        with open(yaml_path, encoding='utf-8') as config_file:
            course_config = yaml.safe_load(config_file)
    except (OSError, UnicodeError, yaml.YAMLError):
        return result
    if not isinstance(course_config, dict):
        return result

    result['config'] = course_config
    result['configured_active'] = course_config.get('active') is True
    if course_config.get('slug') != slug:
        return result
    if not result['configured_active']:
        result.update({
            'status': 'inactive',
            'message': 'Course is not currently active.',
        })
        return result

    db_path = course_db_path(slug)
    result['has_db'] = bool(db_path and os.path.isfile(db_path))
    if not result['has_db']:
        result.update({
            'status': 'missing',
            'message': 'Course database has not been initialized.',
        })
        return result

    connection = None
    try:
        db_uri = f'{Path(db_path).resolve().as_uri()}?mode=ro'
        connection = sqlite3.connect(db_uri, uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only = ON')
        quick_check = connection.execute('PRAGMA quick_check').fetchall()
        if [row[0] for row in quick_check] != ['ok']:
            return result
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not REQUIRED_COURSE_TABLES.issubset(tables):
            return result
        for table, required_columns in REQUIRED_COURSE_SCHEMA.items():
            columns = {
                row[1] for row in connection.execute(
                    f'PRAGMA table_info({table})'
                ).fetchall()
            }
            if not required_columns.issubset(columns):
                return result
        rows = connection.execute(
            '''SELECT c.*, i.name AS instructor_name
               FROM courses c
               JOIN instructors i ON i.id = c.instructor_id
               LIMIT 2''',
        ).fetchall()
        if (len(rows) != 1 or rows[0]['slug'] != slug or
                rows[0]['is_active'] != 1):
            return result
        state_rows = connection.execute(
            'SELECT course_id FROM course_state LIMIT 2'
        ).fetchall()
        if (len(state_rows) != 1 or
                state_rows[0]['course_id'] != rows[0]['id']):
            return result
        result.update({
            'status': 'ready',
            'message': '',
            'course': dict(rows[0]),
        })
    except (OSError, sqlite3.Error):
        return result
    finally:
        if connection is not None:
            connection.close()
    return result


def _clear_course_availability_cache(slug=None):
    """Clear cached validation results after an in-process setup change."""
    with _course_availability_lock:
        if slug is None:
            _course_availability_cache.clear()
            return
        prefix = (os.path.abspath(config.CLASSES_DIR),
                  os.path.abspath(config.DATA_DIR), slug)
        _course_availability_cache.pop(prefix, None)


def _course_availability(slug):
    """Return a short-lived, process-wide validation result for one course."""
    cache_key = (
        os.path.abspath(config.CLASSES_DIR),
        os.path.abspath(config.DATA_DIR),
        slug,
    )
    now = time.monotonic()
    with _course_availability_lock:
        cached = _course_availability_cache.get(cache_key)
        if cached and now - cached['checked_at'] < COURSE_AVAILABILITY_TTL:
            return copy.deepcopy(cached['result'])
        result = _inspect_course_availability(slug)
        _course_availability_cache[cache_key] = {
            'checked_at': time.monotonic(),
            'result': copy.deepcopy(result),
        }
        return result


def _scan_courses():
    """Return active configured courses with their verified availability."""
    courses = []
    if not os.path.isdir(config.CLASSES_DIR):
        return courses
    for slug in sorted(os.listdir(config.CLASSES_DIR)):
        availability = _course_availability(slug)
        cfg = availability.get('config')
        if not availability['configured_active'] or not isinstance(cfg, dict):
            continue
        verified_course = availability.get('course') or {}
        courses.append({
            'id': slug,
            'slug': slug,
            'name': cfg.get('name') or slug,
            'code': cfg.get('code'),
            'semester': cfg.get('semester'),
            'url': cfg.get('url'),
            'has_db': availability['has_db'],
            'instructor_name': verified_course.get('instructor_name', ''),
            'availability_status': availability['status'],
            'availability_message': availability['message'],
        })
    return courses


@app.route('/')
def index():
    role = _exclusive_session_role()
    if role == 'student':
        return redirect(url_for('dashboard'))
    if role == 'instructor':
        return redirect(url_for('instructor_course', slug=session['slug']))
    if session.get('student_id') or session.get('instructor_id') or session.get('role'):
        session.clear()
    courses = _scan_courses()
    return render_template('index.html', courses=courses)


@app.route('/login/<slug>', methods=['GET', 'POST'])
def login(slug):
    availability = _course_availability(slug)
    if availability['status'] != 'ready':
        flash(availability['message'], 'error')
        return redirect(url_for('index'))
    ensure_schema(slug)
    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)

    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        pin = request.form.get('pin', '').strip()
        if not student_id or not pin:
            flash('Please enter both ID and PIN.', 'error')
            return render_template('login.html', course=course, slug=slug)
        principal = student_id.casefold()[:200]
        client_hash = _login_client_hash()
        retry_after = _login_retry_after(
            slug, course['id'], 'student', principal, client_hash
        )
        if retry_after:
            return _rate_limited_login_response(
                'login.html', slug, course, retry_after
            )
        student = query_db(slug,
            '''SELECT s.* FROM students s JOIN courses c ON c.id = s.course_id
               WHERE s.student_id = ? AND s.pin = ? AND c.slug = ? AND s.is_active = 1''',
            [student_id, pin, slug], one=True
        )
        if student:
            _clear_login_failures(
                slug, course['id'], 'student', principal, client_hash
            )
            execute_db(slug,
                'UPDATE students SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?',
                [student['id']]
            )
            session.clear()
            session['role'] = 'student'
            session['student_id'] = student['student_id']
            session['name'] = student['name'] or student['student_id']
            session['slug'] = slug
            return redirect(url_for('dashboard'))
        _record_login_failure(
            slug, course['id'], 'student', principal, client_hash
        )
        flash('Invalid login for this course.', 'error')

    return render_template('login.html', course=course, slug=slug)


@app.route('/instructor_login/<slug>', methods=['GET', 'POST'])
def instructor_login(slug):
    availability = _course_availability(slug)
    if availability['status'] != 'ready':
        flash(availability['message'], 'error')
        return redirect(url_for('index'))
    ensure_schema(slug)
    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        pin = request.form.get('pin', '').strip()
        if not username or not pin:
            flash('Please enter both username and PIN.', 'error')
            return render_template('instructor_login.html', course=course, slug=slug)
        principal = username.casefold()[:200]
        client_hash = _login_client_hash()
        retry_after = _login_retry_after(
            slug, course['id'], 'instructor', principal, client_hash
        )
        if retry_after:
            return _rate_limited_login_response(
                'instructor_login.html', slug, course, retry_after
            )
        instructor = query_db(slug,
            '''SELECT i.* FROM instructors i JOIN courses c ON c.instructor_id = i.id
               WHERE i.username = ? AND i.pin = ? AND c.slug = ?''',
            [username, pin, slug], one=True
        )
        if instructor:
            _clear_login_failures(
                slug, course['id'], 'instructor', principal, client_hash
            )
            session.clear()
            session['role'] = 'instructor'
            session['instructor_id'] = instructor['id']
            session['instructor_name'] = instructor['name']
            session['slug'] = slug
            return redirect(url_for('instructor_course', slug=slug))
        _record_login_failure(
            slug, course['id'], 'instructor', principal, client_hash
        )
        flash('Invalid login for this course.', 'error')

    return render_template('instructor_login.html', course=course, slug=slug)


def _ensure_demo_db():
    """Create the demo database if it doesn't exist yet."""
    db_path = os.path.join(config.DATA_DIR, 'demo', 'popping.db')
    if os.path.exists(db_path):
        return True
    try:
        import subprocess
        script = os.path.join(os.path.dirname(__file__), 'scripts', 'init-demo-db.py')
        subprocess.run([sys.executable, script], check=True, capture_output=True, timeout=30)
        return os.path.exists(db_path)
    except Exception:
        return False


@app.route('/demo')
def demo():
    demo_exists = _ensure_demo_db()
    return render_template('demo.html', demo_exists=demo_exists)


@app.route('/demo/instructor')
def demo_instructor():
    """Log in as the demo instructor — no password needed."""
    db_path = os.path.join(config.DATA_DIR, 'demo', 'popping.db')
    if not os.path.exists(db_path):
        flash('Demo is not available right now. Please try again later.', 'error')
        return redirect(url_for('demo'))
    session.clear()
    instructor = query_db('demo',
        'SELECT * FROM instructors LIMIT 1', one=True)
    if not instructor:
        flash('Demo data not found.', 'error')
        return redirect(url_for('demo'))
    session['instructor_id'] = instructor['id']
    session['role'] = 'instructor'
    session['instructor_name'] = instructor['name']
    session['slug'] = 'demo'
    session['is_demo'] = True
    return redirect(url_for('instructor_course', slug='demo'))


@app.route('/demo/student')
def demo_student():
    """Log in as a demo student — no password needed."""
    db_path = os.path.join(config.DATA_DIR, 'demo', 'popping.db')
    if not os.path.exists(db_path):
        flash('Demo is not available right now. Please try again later.', 'error')
        return redirect(url_for('demo'))
    ensure_schema('demo')
    session.clear()
    # Pick the first student (Alice Chen, Team 1)
    student = query_db('demo',
        'SELECT * FROM students WHERE is_active = 1 ORDER BY id LIMIT 1', one=True)
    if not student:
        flash('Demo data not found.', 'error')
        return redirect(url_for('demo'))
    session['student_id'] = student['student_id']
    session['role'] = 'student'
    session['name'] = student['name'] or student['student_id']
    session['slug'] = 'demo'
    session['is_demo'] = True
    return redirect(url_for('dashboard'))


@app.route('/demo/exit')
def demo_exit():
    """Exit demo mode — clear session and return to the main site."""
    session.clear()
    return redirect(url_for('index'))


@app.route('/demo/reset')
def demo_reset():
    """Reset demo data back to initial state."""
    import subprocess
    script = os.path.join(os.path.dirname(__file__), 'scripts', 'init-demo-db.py')
    try:
        subprocess.run([sys.executable, script], check=True, capture_output=True)
        flash('Demo has been reset to its initial state.', 'success')
    except Exception as e:
        flash(f'Could not reset demo: {e}', 'error')
    session.clear()
    return redirect(url_for('demo'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@student_login_required
def dashboard():
    slug = session['slug']
    from database import ensure_schema
    ensure_schema(slug)
    student = query_db(slug,
        'SELECT * FROM students WHERE student_id = ? AND is_active = 1',
        [session['student_id']], one=True
    )
    team = None
    if student and student['team_id']:
        team = query_db(slug,
            'SELECT * FROM teams WHERE id = ?', [student['team_id']], one=True
        )
    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)
    state = query_db(slug,
        'SELECT * FROM course_state WHERE course_id = ?', [course['id']], one=True
    )
    max_teams = get_max_teams(slug, course['id'])
    teams = query_db(
        slug,
        '''SELECT team.*, COUNT(student.id) AS member_count
           FROM teams team
           LEFT JOIN students student
             ON student.team_id = team.id AND student.is_active = 1
           WHERE team.course_id = ?
           GROUP BY team.id ORDER BY team.id LIMIT ?''',
        [course['id'], max_teams]
    )
    teams_locked = state['teams_locked'] if state and 'teams_locked' in state.keys() else 0
    teammates = []
    if team:
        teammates = query_db(slug,
            '''SELECT student_id, name FROM students
               WHERE team_id = ? AND id != ? AND is_active = 1 ORDER BY name''',
            [team['id'], student['id']]
        )
    return render_template(
        'dashboard.html',
        student=student, team=team, teams=teams,
        state=state, course=course, phases=PHASES,
        teams_locked=teams_locked, teammates=teammates,
        max_teams=max_teams,
        max_members=get_max_members_per_team(slug, course['id'])
    )


@app.route('/instructor/<slug>')
@instructor_login_required
def instructor_course(slug):
    if session.get('slug') != slug:
        flash('Unauthorized.', 'error')
        return redirect(url_for('index'))

    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)
    ensure_schema(slug)
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY id', [course['id']]
    )
    state = query_db(slug,
        'SELECT * FROM course_state WHERE course_id = ?', [course['id']], one=True
    )
    if state:
        state = dict(state)
        now = datetime.utcnow()
        state['poll_active'] = _poll_is_open(state, now=now)
        state.update(_derive_timing_state(state, now=now))
        try:
            full_history = json.loads(state.get('presentation_history') or '[]')
        except (TypeError, ValueError):
            full_history = []
        state['presentation_history'] = json.dumps([
            item for item in full_history
            if item.get('session_key', 0) == (state.get('session_key') or 0)
        ])
    selected_week = state['discussion_week'] if state and state['discussion_week'] else 1
    try:
        catalog_week = validate_question_catalog(
            os.path.join(config.CLASSES_DIR, slug),
            weeks=[selected_week],
        ).get_week(selected_week)
    except (OSError, ValueError):
        catalog_week = None
    presentation_catalog_ready = bool(
        catalog_week and catalog_week.presentation.ready
    )
    if state is not None:
        state['presentation_catalog_ready'] = presentation_catalog_ready
    if (not state or state['phase'] != 'competition' or
            not state['active_question_id']):
        if presentation_catalog_ready:
            sync_presentation_questions(slug, course['id'], selected_week)
        try:
            sync_appendix_questions(slug, course['id'], selected_week)
        except QuestionParseError as exc:
            flash(f'Appendix question file needs attention: {exc}', 'error')
    max_teams = get_max_teams(slug, course['id'])
    teams_locked = state['teams_locked'] if state and 'teams_locked' in state.keys() else 0
    students = query_db(slug,
        '''SELECT s.*, t.name as team_name, t.color as team_color
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           WHERE s.course_id = ? AND s.is_active = 1 ORDER BY s.name''',
        [course['id']]
    )
    questions = query_db(slug,
        '''SELECT * FROM questions
           WHERE course_id = ? AND COALESCE(week_num, 1) = ?
             AND (source_key LIKE 'appendix:%' OR ? = 1 OR id = ?)
           ORDER BY CASE WHEN source_key LIKE 'appendix:%' THEN 1 ELSE 0 END,
                    question_num, id''',
        [course['id'], selected_week, int(presentation_catalog_ready),
         state['active_question_id'] if state else None]
    )
    cutoff = (datetime.utcnow() - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
    students_enhanced = []
    for s in students:
        d = dict(s)
        d['is_online'] = s['last_active_at'] and s['last_active_at'] > cutoff
        students_enhanced.append(d)

    # End session stats
    end_stats = None
    if state and state['phase'] == 'ended':
        # Participants are students who submitted a response in this session.
        participants = query_db(slug,
            '''SELECT COUNT(DISTINCT student_id) AS c FROM (
                   SELECT grader_id AS student_id FROM teammate_thumbs
                   WHERE course_id = ? AND session_key = ?
                   UNION
                   SELECT student_id FROM presentation_ratings
                   WHERE course_id = ? AND session_key = ?
               )''',
            [course['id'], state['session_key'] or 0,
             course['id'], state['session_key'] or 0], one=True)
        # Top students by thumbs-up count
        thumbs = query_db(slug,
            '''SELECT s.name, s.student_id, COUNT(*) as thumbs
               FROM teammate_thumbs p
               JOIN students s ON p.recipient_id = s.id
               WHERE p.course_id = ? AND p.session_key = ?
               GROUP BY p.recipient_id
               ORDER BY thumbs DESC''',
            [course['id'], state['session_key'] or 0])
        # Group into tiers by distinct thumb counts
        top_students = []
        seen_counts = set()
        for t in thumbs:
            if t['thumbs'] not in seen_counts:
                if len(seen_counts) >= 3:
                    break
                seen_counts.add(t['thumbs'])
            top_students.append({'name': t['name'], 'student_id': t['student_id'], 'thumbs': t['thumbs']})
        # Top teams by avg presentation rating (extracted to shared helper)
        hist = json.loads(state['presentation_history']) if state and state['presentation_history'] else []
        hist = [
            item for item in hist
            if item.get('session_key', 0) == (state['session_key'] or 0)
        ]
        team_ratings = _compute_top_teams(
            slug, course['id'], hist, state['session_key'] or 0
        )
        end_stats = {
            'participants': participants['c'] if participants else 0,
            'top_students': top_students,
            'top_teams': [team for team in team_ratings if team['rank'] <= 3]
        }

    # Track which questions have already been presented
    presented_question_ids = set()
    if state and state['presentation_history']:
        try:
            for h in json.loads(state['presentation_history']):
                if (h.get('session_key', 0) == (state['session_key'] or 0)
                        and 'question_id' in h):
                    presented_question_ids.add(h['question_id'])
        except Exception:
            pass

    return render_template(
        'instructor.html',
        course=course, teams=teams, students=students_enhanced,
        state=state, phases=PHASES, questions=questions,
        max_teams=max_teams,
        max_members=get_max_members_per_team(slug, course['id']),
        teams_locked=teams_locked,
        session_started_at=state['session_started_at'] if state and 'session_started_at' in state.keys() else None,
        end_stats=end_stats,
        presented_question_ids=list(presented_question_ids),
        POLL_DURATION=POLL_DURATION
    )


# ---------------------------------------------------------------------------
# Student API
# ---------------------------------------------------------------------------

def _get_slug_from_session():
    if 'slug' in session:
        return session['slug']
    return None


def _authenticated_role(slug):
    """Return the role only when the session principal belongs to this course."""
    if not slug or session.get('slug') != slug:
        return None
    role = _exclusive_session_role()
    if role and _course_availability(slug)['status'] != 'ready':
        session.clear()
        return None
    if role:
        ensure_schema(slug)
    if role == 'student' and session.get('student_id'):
        row = query_db(
            slug,
            '''SELECT s.* FROM students s JOIN courses c ON c.id = s.course_id
               WHERE s.student_id = ? AND c.slug = ? AND s.is_active = 1
                 AND c.is_active = 1''',
            [session['student_id'], slug], one=True
        )
        if row:
            g.current_student = row
            return 'student'
        session.clear()
        return None
    if role == 'instructor' and session.get('instructor_id'):
        row = query_db(
            slug,
            '''SELECT id FROM courses
               WHERE slug = ? AND instructor_id = ? AND is_active = 1''',
            [slug, session['instructor_id']], one=True
        )
        if row:
            return 'instructor'
        session.clear()
        return None
    return None


def _bump_roster_version(slug, course_id, db=None):
    """Mark roster/team membership data as changed for polling clients."""
    ensure_schema(slug)
    sql = ('UPDATE course_state '
           'SET roster_version = COALESCE(roster_version, 0) + 1 '
           'WHERE course_id = ?')
    if db is None:
        execute_db(slug, sql, [course_id])
    else:
        db.execute(sql, [course_id])


def _archive_students(slug, db_ids, bump_roster=True, db=None, commit=True):
    """Remove students from the live roster while preserving historical responses."""
    if not db_ids:
        return
    db = db or get_db(slug)
    ph = ','.join('?' * len(db_ids))
    course_ids = [row['course_id'] for row in db.execute(
        f'SELECT DISTINCT course_id FROM students WHERE id IN ({ph})', db_ids
    ).fetchall()]
    db.execute(
        f'''UPDATE students
            SET is_active = 0,
                last_team_id = COALESCE(last_team_id, team_id),
                team_id = NULL
            WHERE id IN ({ph})''',
        db_ids
    )
    if bump_roster:
        for course_id in course_ids:
            _bump_roster_version(slug, course_id, db=db)
    if commit:
        db.commit()


# ---------------------------------------------------------------------------
# Shared helpers for state/teams computation (used by /api/state, /api/teams,
# and the consolidated /api/poll endpoint).
# ---------------------------------------------------------------------------

def _compute_top_teams(slug, course_id, history, session_key):
    """Compute team rankings by average presentation rating.

    ``history`` is the already-parsed presentation_history list (each item
    is a dict with 'started_at' and 'team' keys).  Pass the parsed list
    directly to avoid a redundant course_state re-query on the poll path.
    Returns ranked team averages with the number of submitted rating forms.
    """
    history_json = history or []
    key_to_team = {}
    for h in history_json:
        qkey = h.get('presentation_key') or f"pres-{h.get('started_at', '')}"
        key_to_team[qkey] = {
            'id': h.get('team_id'),
            'name': h.get('team', 'Unknown'),
        }

    all_ratings = query_db(slug,
        '''SELECT question_key, presenting_team_id, presenting_team_name,
                  q1_developed, q2_easy
           FROM presentation_ratings WHERE course_id = ? AND session_key = ?''',
        [course_id, session_key])

    team_scores = {}
    for r in all_ratings:
        historical = key_to_team.get(r['question_key']) or {}
        team_id = r['presenting_team_id'] or historical.get('id')
        team_name = r['presenting_team_name'] or historical.get('name')
        if (not team_name or r['q1_developed'] is None or
                r['q2_easy'] is None):
            continue
        identity = ('id', team_id) if team_id is not None else (
            'name', team_name.casefold()
        )
        score = team_scores.setdefault(identity, {
            'id': team_id, 'name': team_name, 'score_sum': 0,
            'score_count': 0, 'rating_count': 0,
        })
        score['score_sum'] += r['q1_developed'] + r['q2_easy']
        score['score_count'] += 2
        score['rating_count'] += 1

    team_ratings = list(team_scores.values())
    for score in team_ratings:
        score['_fraction'] = Fraction(score['score_sum'], score['score_count'])
    team_ratings.sort(key=lambda score: (
        -score['_fraction'], score['name'].casefold(), score['id'] or 0
    ))
    prior_fraction = None
    for position, score in enumerate(team_ratings, 1):
        if prior_fraction is None or score['_fraction'] != prior_fraction:
            rank = position
        score['rank'] = rank
        score['avg_score'] = round(float(score['_fraction']), 2)
        prior_fraction = score['_fraction']
        score.pop('_fraction')
    return team_ratings


def _compute_state(slug, include_poll_count=True, known_question_id=None,
                   known_question_revision=None, known_discussion_key=None):
    """Compute the course-state dict — shared by /api/state and /api/poll.

    This is the single source of truth for presentation timer, poll status,
    active team/question, and the student's own team. Question content is
    omitted only when the client confirms both its ID and content revision.
    """
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    if state:
        state = dict(state)  # mutable copy — avoids re-query on poll auto-close

    active_team = None
    if state and state['active_team_id']:
        active_team = query_db(slug,
            'SELECT * FROM teams WHERE id = ?', [state['active_team_id']], one=True)

    my_team = None
    if session.get('role') == 'student' and 'student_id' in session:
        me = getattr(g, 'current_student', None)
        if me is None:
            me = query_db(slug,
                'SELECT * FROM students WHERE student_id = ? AND is_active = 1',
                [session['student_id']], one=True)
        if me and me['team_id']:
            my_team = query_db(slug,
                'SELECT * FROM teams WHERE id = ?', [me['team_id']], one=True)

    active_question = None
    if state and state['active_question_id']:
        aq = query_db(slug,
            'SELECT * FROM questions WHERE id = ?', [state['active_question_id']], one=True)
        if aq:
            full_question = dict(aq)
            week = full_question.get(
                'week_num',
                state['discussion_week'] if state and state['discussion_week'] else 1
            )
            source_key = full_question.get('source_key') or ''
            html = None
            if not source_key.startswith('appendix:'):
                html = load_question_html(slug, week, full_question['question_num'])
                if html:
                    full_question['html_content'] = html
            revision_source = '\0'.join(str(value or '') for value in (
                full_question.get('title'), full_question.get('question_text'),
                full_question.get('content'), html,
            ))
            revision = hashlib.sha256(
                revision_source.encode('utf-8')
            ).hexdigest()
            if (known_question_id == full_question['id'] and
                    known_question_revision == revision):
                active_question = {
                    'id': full_question['id'],
                    'revision': revision,
                    'content_unchanged': True,
                }
            else:
                full_question['revision'] = revision
                active_question = full_question

    now = datetime.utcnow()
    timing = _derive_timing_state(state, now=now)
    presentation_remaining = timing['presentation_remaining']

    # Expiry is derived, not written by every polling client.  The instructor's
    # next start/stop action persists the next transition without a write storm.
    poll_active_bool = _poll_is_open(state, now=now)

    # Poll count — use course_id from the already-fetched state row
    # (no separate SELECT needed)
    poll_count = 0
    pres_key = active_presentation_key(state)
    active_cutoff = (now - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')
    if (include_poll_count and state and pres_key
            and state.get('active_team_id')):
        cid = state['course_id']
        cnt = query_db(slug,
            '''SELECT COUNT(DISTINCT student_id) AS c
               FROM presentation_ratings
               WHERE course_id = ? AND session_key = ? AND question_key = ?''',
            [cid, state.get('session_key', 0), pres_key], one=True)
        poll_count = cnt['c'] if cnt else 0

    needs_history = bool(include_poll_count or (state and state['phase'] == 'ended'))
    try:
        all_history = json.loads(state['presentation_history']) \
            if needs_history and state and state['presentation_history'] else []
    except (TypeError, ValueError):
        all_history = []
    current_session_key = state.get('session_key', 0) if state else 0
    history = [
        item for item in all_history
        if item.get('session_key', 0) == current_session_key
    ]

    discussion_question = None
    if state and state.get('current_discussion_key'):
        discussion_question = {
            'key': state['current_discussion_key'],
            'source_key': active_discussion_source_key(state),
            'title': state.get('current_discussion_title') or '',
        }
        if known_discussion_key == state['current_discussion_key']:
            discussion_question['content_unchanged'] = True
        else:
            discussion_question['content'] = state.get('current_discussion_content') or ''

    result = {
        'phase': state['phase'] if state else 'setup',
        'active_team': dict(active_team) if active_team else None,
        'active_question': active_question,
        'my_team': dict(my_team) if my_team else None,
        'current_question': state['current_question'] if state else None,
        'discussion_question': discussion_question,
        'presentation_started_at': state['presentation_started_at'] if state else None,
        'session_started_at': state['session_started_at'] if state else None,
        'presentation_time_cap': state['presentation_time_cap'] if state else 300,
        'presentation_remaining': presentation_remaining,
        'teams_locked': bool(state['teams_locked']) if state else False,
        'max_teams': state.get('max_teams', 8) if state else 8,
        'max_members': state.get('max_members_per_team', 5) if state else 5,
        'discussion_week': state.get('discussion_week', 1) if state else 1,
        'poll_active': poll_active_bool,
        'poll_started_at': state['poll_started_at'] if state else None,
        'poll_duration': POLL_DURATION,
        'poll_remaining': timing['poll_remaining'],
        'poll_question_key': state['poll_question_key'] if state else None,
        'roster_version': state.get('roster_version', 0) if state else 0,
        'session_key': state.get('session_key', 0) if state else 0,
        'session_elapsed': timing['session_elapsed'],
        'presentation_history': history,
        # Internal metadata used by api_poll for ended-phase ranking.
        '_course_id': state['course_id'] if state else None,
    }
    if include_poll_count:
        result['poll_count'] = poll_count or 0
        cid = state['course_id'] if state else None
        if cid:
            unassigned = query_db(
                slug,
                '''SELECT COUNT(*) AS c FROM students
                   WHERE course_id = ? AND team_id IS NULL AND is_active = 1''',
                [cid], one=True
            )
            result['unassigned_count'] = unassigned['c'] if unassigned else 0

            poll_eligible = 0
            poll_online_eligible = 0
            if state.get('active_team_id'):
                eligible = query_db(
                    slug,
                    '''SELECT COUNT(*) AS c,
                              SUM(CASE WHEN last_active_at >= ? THEN 1 ELSE 0 END)
                                  AS online
                       FROM students
                       WHERE course_id = ? AND team_id IS NOT NULL AND team_id != ?
                         AND is_active = 1''',
                    [active_cutoff, cid, state['active_team_id']], one=True
                )
                poll_eligible = eligible['c'] if eligible else 0
                poll_online_eligible = eligible['online'] if eligible else 0
            result['poll_eligible_count'] = poll_eligible
            result['poll_online_eligible_count'] = poll_online_eligible or 0

            thumb_participants = 0
            thumb_eligible = 0
            thumb_online_eligible = 0
            if state.get('current_discussion_key'):
                participants = query_db(
                    slug,
                    '''SELECT COUNT(DISTINCT thumb.grader_id) AS c
                       FROM teammate_thumbs thumb
                       WHERE thumb.course_id = ? AND thumb.session_key = ?
                         AND thumb.question_key = ?''',
                    [cid, state.get('session_key', 0),
                     state['current_discussion_key']], one=True
                )
                thumb_participants = participants['c'] if participants else 0
                eligible = query_db(
                    slug,
                    '''SELECT COUNT(*) AS c,
                              SUM(CASE WHEN s.last_active_at >= ? THEN 1 ELSE 0 END)
                                  AS online
                       FROM students s
                       WHERE s.course_id = ? AND s.is_active = 1 AND s.team_id IN (
                           SELECT team_id FROM students
                            WHERE course_id = ? AND team_id IS NOT NULL
                              AND is_active = 1
                           GROUP BY team_id HAVING COUNT(*) > 1
                       )''',
                    [active_cutoff, cid, cid], one=True
                )
                thumb_eligible = eligible['c'] if eligible else 0
                thumb_online_eligible = eligible['online'] if eligible else 0
            result['thumb_participant_count'] = thumb_participants
            result['thumb_eligible_count'] = thumb_eligible
            result['thumb_online_eligible_count'] = thumb_online_eligible or 0
        else:
            result.update({
                'unassigned_count': 0, 'poll_eligible_count': 0,
                'poll_online_eligible_count': 0,
                'thumb_participant_count': 0, 'thumb_eligible_count': 0,
                'thumb_online_eligible_count': 0,
            })
        result['completed_presentation_count'] = len(history)
        result['presentation_number'] = len(history) + 1 \
            if active_team and active_question else None
    return result


def _compute_teams(slug, course_id, max_teams=None, member_team_id=None,
                   include_all_members=True):
    """Compute the teams + members list for the versioned roster endpoint.

    Uses 2 queries total (teams + all members) instead of N+1.
    Pass ``max_teams`` when the caller already has the visible-team limit.
    """
    if max_teams is None:
        max_teams = get_max_teams(slug, course_id)
    teams = query_db(slug,
        '''SELECT team.*, COUNT(student.id) AS member_count
           FROM teams team
           LEFT JOIN students student
             ON student.team_id = team.id AND student.is_active = 1
           WHERE team.course_id = ?
           GROUP BY team.id ORDER BY team.id LIMIT ?''',
        [course_id, max_teams])
    if not teams:
        return []
    team_ids = [team['id'] for team in teams]
    visible_member_team_ids = team_ids if include_all_members else (
        [member_team_id] if member_team_id in team_ids else []
    )
    all_members = []
    if visible_member_team_ids:
        placeholders = ','.join('?' * len(visible_member_team_ids))
        all_members = query_db(slug,
            f'''SELECT student_id, name, team_id FROM students
                WHERE team_id IN ({placeholders}) AND is_active = 1''',
            visible_member_team_ids)
    members_by_team = {}
    for m in all_members:
        members_by_team.setdefault(m['team_id'], []).append({'student_id': m['student_id'], 'name': m['name']})
    return [
        {'id': team['id'], 'name': team['name'], 'color': team['color'],
         'member_count': team['member_count'],
         'members_visible': include_all_members or team['id'] == member_team_id,
         'members': members_by_team.get(team['id'], [])}
        for team in teams
    ]


@app.route('/api/teams', methods=['GET'])
def api_teams():
    slug = _get_slug_from_session()
    role = _authenticated_role(slug)
    if not role:
        return jsonify({'error': 'Not logged in'}), 401
    course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
    if role == 'instructor':
        return jsonify(_compute_teams(slug, course['id']))
    student = query_db(
        slug,
        '''SELECT team_id FROM students
           WHERE course_id = ? AND student_id = ? AND is_active = 1''',
        [course['id'], session['student_id']], one=True
    )
    return jsonify(_compute_teams(
        slug, course['id'],
        member_team_id=student['team_id'] if student else None,
        include_all_members=False,
    ))


@app.route('/api/join_team', methods=['POST'])
@student_login_required
def join_team():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    team_id = data.get('team_id')
    if team_id is None:
        return jsonify({'error': 'Team ID required'}), 400
    if team_id:
        try:
            team_id = int(team_id)
        except (TypeError, ValueError):
            return jsonify({'error': 'Invalid team ID'}), 400

    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        student = db.execute(
            '''SELECT s.* FROM students s JOIN courses c ON c.id = s.course_id
               WHERE s.student_id = ? AND c.slug = ? AND s.is_active = 1''',
            [session['student_id'], slug]
        ).fetchone()
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?',
            [student['course_id'] if student else -1]
        ).fetchone()
        if not student:
            db.rollback()
            return jsonify({'error': 'Student not found'}), 404
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Team selection is closed'}), 403
        if state['teams_locked']:
            db.rollback()
            return jsonify({'error': 'Teams are currently locked by the instructor'}), 403

        # Leaving team (team_id = 0 means unassign).
        if not team_id:
            if student['team_id'] is not None:
                db.execute(
                    '''UPDATE students
                       SET last_team_id = COALESCE(last_team_id, team_id),
                           team_id = NULL
                       WHERE id = ?''',
                    [student['id']]
                )
                _bump_roster_version(slug, state['course_id'], db=db)
            db.commit()
            return jsonify({'success': True})
        if student['team_id'] == team_id:
            db.commit()
            return jsonify({'success': True})

        visible = db.execute(
            'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
            [state['course_id'], state['max_teams'] or 8]
        ).fetchall()
        if team_id not in {row['id'] for row in visible}:
            db.rollback()
            return jsonify({'error': 'Team is not available'}), 400

        member_count = db.execute(
            'SELECT COUNT(*) AS c FROM students WHERE team_id = ? AND is_active = 1', [team_id]
        ).fetchone()['c']
        if member_count >= (state['max_members_per_team'] or 5):
            db.rollback()
            return jsonify({'error': 'That team is full'}), 409

        db.execute(
            '''UPDATE students
               SET team_id = ?, last_team_id = ?, last_team_joined_at = CURRENT_TIMESTAMP
               WHERE id = ?''',
            [team_id, team_id, student['id']]
        )
        _bump_roster_version(slug, state['course_id'], db=db)
        db.commit()
        return jsonify({'success': True})
    except Exception:
        db.rollback()
        raise


@app.route('/api/state', methods=['GET'])
def api_state():
    """Course state — accessible to both students and instructors."""
    slug = session.get('slug')
    if not slug:
        return jsonify({'error': 'Not logged in'}), 401
    role = _authenticated_role(slug)
    if not role:
        return jsonify({'error': 'Not logged in'}), 401
    is_instructor = role == 'instructor'
    state_data = _compute_state(
        slug,
        include_poll_count=is_instructor,
        known_question_id=request.args.get('known_question_id', type=int),
        known_question_revision=request.args.get('known_question_revision'),
        known_discussion_key=request.args.get('known_discussion_key'),
    )
    # Strip internal + grading metadata from student responses
    state_data.pop('_course_id', None)
    if role == 'student':
        state_data.pop('presentation_history', None)
    return jsonify(state_data)


@app.route('/api/poll', methods=['GET'])
def api_poll():
    """Lightweight polling endpoint returning frequently changing state.

    Roster data is fetched separately only when ``roster_version`` changes.
    Clients may send a known question ID and revision so unchanged question
    bodies are omitted. ``poll_interval`` lets clients adapt to the phase.
    """
    slug = session.get('slug')
    if not slug:
        return jsonify({'error': 'Not logged in'}), 401
    role = _authenticated_role(slug)
    if not role:
        return jsonify({'error': 'Not logged in'}), 401

    ensure_schema(slug)

    is_instructor = role == 'instructor'

    # --- State ---
    state_data = _compute_state(
        slug,
        include_poll_count=is_instructor,
        known_question_id=request.args.get('known_question_id', type=int),
        known_question_revision=request.args.get('known_question_revision'),
        known_discussion_key=request.args.get('known_discussion_key'),
    )
    if (role == 'student' and
            session.get('activity_session_key') != state_data.get('session_key')):
        execute_db(
            slug,
            '''UPDATE students SET last_active_at = CURRENT_TIMESTAMP
               WHERE student_id = ? AND is_active = 1''',
            [session['student_id']]
        )
        session['activity_session_key'] = state_data.get('session_key')
        session['last_active_synced_at'] = datetime.utcnow().isoformat()
    course_id = state_data.pop('_course_id')

    # Strip grading metadata from student responses — students never see
    # poll counts or presentation history (rating counts per team).
    # Save history first — needed for top-teams computation below.
    pres_history = state_data.get('presentation_history', [])
    if role == 'student':
        state_data.pop('presentation_history', None)

    # --- Top 3 teams (only when session has ended, students only) ---
    top_teams = None
    if state_data['phase'] == 'ended' and role == 'student':
        all_ranked = _compute_top_teams(
            slug, course_id, pres_history, state_data.get('session_key', 0)
        )
        # Students see names and tied medal ranks, but no scores.
        top_teams = [
            {'name': team['name'], 'rank': team['rank']}
            for team in all_ranked if team['rank'] <= 3
        ]

    # --- Adaptive interval hint ---
    # 1s during all active phases so instructor actions (phase changes, posting
    # questions, starting polls, selecting teams) appear within 1 second.
    # Keep a low-frequency recovery poll after End Session so another session
    # can start without requiring every student to reload manually.
    stop_polling = False
    if state_data['phase'] == 'ended':
        poll_interval = 30000
    elif role == 'instructor':
        poll_interval = 1000
    else:
        poll_interval = {
            'setup': 5000,
            'discussion': 3000,
            'competition': 2000,
        }.get(state_data['phase'], 5000)

    return jsonify({
        'state': state_data,
        'top_teams': top_teams,
        'poll_interval': poll_interval,
        'stop_polling': stop_polling,
    })


@app.route('/api/grade_peer', methods=['POST'])
@student_login_required
def grade_peer():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    recipient_sid = data.get('recipient_id')
    selected = data.get('selected')
    question_key = str(data.get('question_key') or '').strip()
    if recipient_sid is None:
        return jsonify({'error': 'Recipient is required'}), 400
    if not isinstance(selected, bool):
        return jsonify({'error': 'Selected must be true or false'}), 400

    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        grader = db.execute(
            'SELECT * FROM students WHERE student_id = ? AND is_active = 1',
            [session['student_id']]
        ).fetchone()
        recipient = db.execute(
            '''SELECT * FROM students
               WHERE student_id = ? AND course_id = ? AND is_active = 1''',
            [str(recipient_sid), grader['course_id'] if grader else -1]
        ).fetchone()
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?',
            [grader['course_id'] if grader else -1]
        ).fetchone()
        if not grader:
            db.rollback()
            return jsonify({'error': 'Grader not found'}), 404
        if not recipient:
            db.rollback()
            return jsonify({'error': 'Recipient not found'}), 404
        if grader['id'] == recipient['id']:
            db.rollback()
            return jsonify({'error': 'Cannot grade yourself'}), 400
        if not state or state['phase'] != 'discussion':
            db.rollback()
            return jsonify({'error': 'Teammate thumbs are only open during discussion'}), 403
        if not question_key or question_key != state['current_discussion_key']:
            db.rollback()
            return jsonify({
                'error': 'The discussion question changed; refresh before saving'
            }), 409
        if not grader['team_id'] or recipient['team_id'] != grader['team_id']:
            db.rollback()
            return jsonify({'error': 'You can only grade teammates'}), 403

        identity_params = [
            grader['course_id'], state['session_key'] or 0,
            state['current_discussion_key'], grader['id'], recipient['id'],
        ]
        if selected:
            team_rows = db.execute(
                '''SELECT id, name FROM teams
                   WHERE course_id = ? AND id IN (?, ?)''',
                [grader['course_id'], grader['team_id'], recipient['team_id']]
            ).fetchall()
            team_names = {row['id']: row['name'] for row in team_rows}
            db.execute(
                '''INSERT INTO teammate_thumbs
                   (course_id, session_key, question_key, source_question_key,
                    question_title, grader_id, recipient_id,
                    grader_team_id, grader_team_name,
                    recipient_team_id, recipient_team_name)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(course_id, session_key, question_key, grader_id, recipient_id)
                   DO UPDATE SET
                       source_question_key = COALESCE(
                           teammate_thumbs.source_question_key,
                           excluded.source_question_key
                       ),
                       question_title = COALESCE(
                           teammate_thumbs.question_title,
                           excluded.question_title
                       ),
                       grader_team_id = COALESCE(
                           teammate_thumbs.grader_team_id,
                           excluded.grader_team_id
                       ),
                       grader_team_name = COALESCE(
                           teammate_thumbs.grader_team_name,
                           excluded.grader_team_name
                       ),
                       recipient_team_id = COALESCE(
                           teammate_thumbs.recipient_team_id,
                           excluded.recipient_team_id
                       ),
                       recipient_team_name = COALESCE(
                           teammate_thumbs.recipient_team_name,
                           excluded.recipient_team_name
                       ),
                       updated_at = CURRENT_TIMESTAMP''',
                [
                    grader['course_id'], state['session_key'] or 0,
                    state['current_discussion_key'],
                    active_discussion_source_key(state),
                    state['current_discussion_title'] or '',
                    grader['id'], recipient['id'],
                    grader['team_id'], team_names.get(grader['team_id'], ''),
                    recipient['team_id'], team_names.get(recipient['team_id'], ''),
                ]
            )
        else:
            db.execute(
                '''DELETE FROM teammate_thumbs
                   WHERE course_id = ? AND session_key = ? AND question_key = ?
                     AND grader_id = ? AND recipient_id = ?''',
                identity_params
            )
        db.commit()
        return jsonify({'success': True, 'selected': selected})
    except Exception:
        db.rollback()
        raise


@app.route('/api/my_responses', methods=['GET'])
@student_login_required
def my_responses():
    """Return only this student's saved controls for the current live context."""
    slug = session['slug']
    ensure_schema(slug)
    student = query_db(
        slug, 'SELECT * FROM students WHERE student_id = ? AND is_active = 1',
        [session['student_id']], one=True
    )
    state = query_db(
        slug, 'SELECT * FROM course_state WHERE course_id = ?',
        [student['course_id']], one=True
    )
    discussion_key = state['current_discussion_key'] if state else None
    thumb_recipient_ids = []
    if discussion_key:
        rows = query_db(
            slug,
            '''SELECT recipient.student_id FROM teammate_thumbs thumb
               JOIN students recipient ON recipient.id = thumb.recipient_id
               WHERE thumb.course_id = ? AND thumb.session_key = ?
                 AND thumb.question_key = ? AND thumb.grader_id = ?
               ORDER BY recipient.student_id''',
            [student['course_id'], state['session_key'] or 0,
             discussion_key, student['id']]
        )
        thumb_recipient_ids = [row['student_id'] for row in rows]

    presentation_key = active_presentation_key(state)
    rating = None
    if presentation_key:
        saved = query_db(
            slug,
            '''SELECT q1_developed, q2_easy FROM presentation_ratings
               WHERE course_id = ? AND student_id = ? AND question_key = ?''',
            [student['course_id'], student['id'], presentation_key], one=True
        )
        if saved:
            rating = {
                'q1_developed': saved['q1_developed'],
                'q2_easy': saved['q2_easy'],
            }
    return jsonify({
        'phase': state['phase'] if state else 'setup',
        'discussion_question_key': discussion_key,
        'thumb_recipient_ids': thumb_recipient_ids,
        'presentation_key': presentation_key,
        'rating': rating,
    })



# ---------------------------------------------------------------------------
# Instructor API
# ---------------------------------------------------------------------------

def _finalize_active_presentation(slug, course_id, db=None):
    """Append the active presentation to history and clear it in one transaction."""
    owns_transaction = db is None
    if db is None:
        db = get_db(slug)
        db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?', [course_id]
        ).fetchone()
        if not state:
            if owns_transaction:
                db.commit()
            return False

        try:
            history = json.loads(state['presentation_history'] or '[]')
        except (TypeError, ValueError):
            history = []

        if state['active_team_id'] and state['active_question_id']:
            team = db.execute(
                'SELECT name FROM teams WHERE id = ? AND course_id = ?',
                [state['active_team_id'], course_id]
            ).fetchone()
            question = db.execute(
                'SELECT question_text, title FROM questions WHERE id = ? AND course_id = ?',
                [state['active_question_id'], course_id]
            ).fetchone()
            presentation_key = active_presentation_key(state)
            count = db.execute(
                '''SELECT COUNT(DISTINCT student_id) AS c FROM presentation_ratings
                   WHERE course_id = ? AND question_key = ?''',
                [course_id, presentation_key]
            ).fetchone()
            title = (question['title'] or question['question_text']) \
                if question else (state['current_question'] or '')
            started_at = state['presentation_created_at'] or state['presentation_started_at'] or ''
            history.append({
                'presentation_key': presentation_key,
                'session_key': state['session_key'] or 0,
                'title': title,
                'team_id': state['active_team_id'],
                'team': team['name'] if team else 'Unknown',
                'responses': count['c'] if count else 0,
                'started_at': started_at,
                'question_id': state['active_question_id'],
                'ended_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
            })

        _clear_active_presentation(db, course_id, json.dumps(history))
        if owns_transaction:
            db.commit()
        return bool(state['active_team_id'] and state['active_question_id'])
    except Exception:
        if owns_transaction:
            db.rollback()
        raise


def _clear_active_presentation(db, course_id, history_json=None):
    """Clear live presentation state, optionally replacing stored history."""
    assignments = [
        'active_team_id = NULL', 'active_question_id = NULL',
        'current_question = NULL', 'presentation_started_at = NULL',
        'presentation_created_at = NULL', 'presentation_time_cap = 300',
        'presentation_remaining = NULL', 'poll_active = 0',
        'poll_question_key = NULL', 'poll_started_at = NULL',
    ]
    params = []
    if history_json is not None:
        assignments.append('presentation_history = ?')
        params.append(history_json)
    params.append(course_id)
    db.execute(
        f"UPDATE course_state SET {', '.join(assignments)} WHERE course_id = ?",
        params
    )

@app.route('/api/set_phase', methods=['POST'])
@instructor_login_required
def set_phase():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    phase = data.get('phase')
    if phase not in PHASES:
        return jsonify({'error': 'Invalid phase'}), 400
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?', [course['id']]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        displayed_presentation = str(data.get('presentation_key') or '').strip()
        current_presentation = active_presentation_key(state) or ''
        if displayed_presentation != current_presentation:
            db.rollback()
            return jsonify({
                'error': 'This instructor page is stale; reload before changing the course state'
            }), 409
        old_phase = state['phase'] if state else 'setup'
        if (phase == 'ended' and old_phase != 'ended' and
                data.get('confirm_end_session') is not True):
            db.rollback()
            return jsonify({
                'error': 'Confirm before ending the session',
                'requires_confirmation': True,
            }), 409
        if old_phase == 'competition' and phase != 'competition':
            if state['active_team_id'] or state['active_question_id']:
                guard = _presentation_guard(data, state)
                if guard:
                    db.rollback()
                    return jsonify({'error': guard[0]}), guard[1]
                if _poll_is_open(state):
                    db.rollback()
                    return jsonify({
                        'error': 'Stop the active rating poll before leaving this phase'
                    }), 409
            _finalize_active_presentation(slug, course['id'], db=db)

        session_increment = 1 if old_phase == 'ended' and phase != 'ended' else 0
        if old_phase != phase:
            db.execute(
                '''UPDATE course_state
                   SET phase = ?,
                       session_key = COALESCE(session_key, 0) + ?,
                       current_question = NULL,
                       current_discussion_key = NULL,
                       current_discussion_source_key = NULL,
                       current_discussion_title = NULL,
                       current_discussion_content = NULL,
                       poll_active = CASE WHEN ? = 'competition' THEN poll_active ELSE 0 END,
                       poll_started_at = CASE WHEN ? = 'competition' THEN poll_started_at ELSE NULL END,
                       session_started_at = CASE
                           WHEN ? = 'ended' OR ? > 0 THEN NULL
                           ELSE session_started_at END
                   WHERE course_id = ?''',
                [phase, session_increment, phase, phase, phase,
                 session_increment, course['id']]
            )
        fresh = db.execute(
            'SELECT session_key FROM course_state WHERE course_id = ?', [course['id']]
        ).fetchone()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({
        'success': True,
        'phase': phase,
        'session_key': fresh['session_key'] if fresh else 0,
    })


@app.route('/api/set_question', methods=['POST'])
@instructor_login_required
def set_question():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    supplied = data.get('question')
    if isinstance(supplied, dict):
        question_key = str(supplied.get('key') or '').strip()
        title = str(supplied.get('title') or '').strip()
        content = str(supplied.get('content') or '').strip()
    else:
        question_key = str(data.get('key') or '').strip()
        title = str(data.get('title') or supplied or '').strip()
        content = str(data.get('content') or '').strip()
    clearing = not title and not content
    if not clearing and (not title or not content):
        return jsonify({'error': 'Question title and content are required'}), 400
    if len(title) > 500 or len(content) > 50000:
        return jsonify({'error': 'Question is too long'}), 400
    if not clearing and not question_key:
        digest = hashlib.sha256((title + '\0' + content).encode('utf-8')).hexdigest()[:16]
        question_key = f'manual:{digest}'
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?', [course['id']]
        ).fetchone()
        guard = _expected_discussion_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'discussion':
            db.rollback()
            return jsonify({'error': 'Questions can only be posted during discussion'}), 403
        discussion_instance_key = None if clearing else f'disc-{uuid.uuid4().hex}'
        db.execute(
            '''UPDATE course_state
               SET current_question = ?, current_discussion_key = ?,
                   current_discussion_source_key = ?,
                   current_discussion_title = ?, current_discussion_content = ?
               WHERE course_id = ?''',
            ([None, None, None, None, None, course['id']] if clearing else
             [title, discussion_instance_key, question_key, title, content,
              course['id']])
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    if clearing:
        return jsonify({'success': True, 'discussion_question': None})
    return jsonify({
        'success': True,
        'discussion_question': {
            'key': discussion_instance_key,
            'source_key': question_key,
            'title': title,
            'content': content,
        },
    })


@app.route('/api/set_max_teams', methods=['POST'])
@instructor_login_required
def set_max_teams():
    slug = session['slug']
    ensure_schema(slug)
    data = request.get_json(silent=True) or {}
    new_max = data.get('max_teams')
    if not isinstance(new_max, int) or new_max < 1 or new_max > 20:
        return jsonify({'error': 'Team count must be between 1 and 20'}), 400
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version, max_teams
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Team settings can only change during setup'}), 409
        available = db.execute(
            'SELECT COUNT(*) AS c FROM teams WHERE course_id = ?', [course['id']]
        ).fetchone()['c']
        if new_max > available:
            db.rollback()
            return jsonify({'error': f'Only {available} teams are available'}), 400
        if new_max < (state['max_teams'] or available):
            hidden = db.execute(
                'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT -1 OFFSET ?',
                [course['id'], new_max]
            ).fetchall()
            if hidden:
                ids = [team['id'] for team in hidden]
                placeholders = ','.join('?' * len(ids))
                db.execute(
                    f'''UPDATE students
                        SET last_team_id = COALESCE(last_team_id, team_id),
                            team_id = NULL
                        WHERE course_id = ? AND team_id IN ({placeholders})
                          AND is_active = 1''',
                    [course['id']] + ids
                )
        db.execute(
            '''UPDATE course_state
               SET max_teams = ?, roster_version = COALESCE(roster_version, 0) + 1
               WHERE course_id = ?''',
            [new_max, course['id']]
        )
        db.commit()
        return jsonify({'success': True, 'max_teams': new_max})
    except Exception:
        db.rollback()
        raise


@app.route('/api/random_assign', methods=['POST'])
@instructor_login_required
def random_assign():
    slug = session['slug']
    import random as rnd
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version, max_teams,
                      max_members_per_team
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Random assignment is only available during setup'}), 409
        teams = db.execute(
            'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
            [course['id'], state['max_teams'] or 8]
        ).fetchall()
        if not teams:
            db.rollback()
            return jsonify({'error': 'No teams available'}), 400

        team_ids = [team['id'] for team in teams]
        unassigned = db.execute(
            '''SELECT id FROM students
               WHERE course_id = ? AND team_id IS NULL AND is_active = 1''',
            [course['id']]
        ).fetchall()
        if not unassigned:
            db.commit()
            return jsonify({'success': True, 'assigned': 0, 'remaining': 0})

        student_ids = [student['id'] for student in unassigned]
        rnd.shuffle(student_ids)
        placeholders = ','.join('?' * len(team_ids))
        count_rows = db.execute(
            f'''SELECT team_id, COUNT(*) AS c FROM students
                WHERE team_id IN ({placeholders}) AND is_active = 1
                GROUP BY team_id''',
            team_ids
        ).fetchall()
        counts = {row['team_id']: row['c'] for row in count_rows}
        for team_id in team_ids:
            counts.setdefault(team_id, 0)

        max_members = state['max_members_per_team'] or 5
        assignments = {}
        for student_id in student_ids:
            candidates = [
                team_id for team_id in team_ids if counts[team_id] < max_members
            ]
            if not candidates:
                break
            minimum = min(counts[team_id] for team_id in candidates)
            team_id = rnd.choice([
                candidate for candidate in candidates if counts[candidate] == minimum
            ])
            assignments[student_id] = team_id
            counts[team_id] += 1

        by_team = {}
        for student_id, team_id in assignments.items():
            by_team.setdefault(team_id, []).append(student_id)
        for team_id, assigned_ids in by_team.items():
            placeholders = ','.join('?' * len(assigned_ids))
            db.execute(
                f'''UPDATE students SET team_id = ?, last_team_id = ?,
                    last_team_joined_at = CURRENT_TIMESTAMP
                    WHERE id IN ({placeholders})''',
                [team_id, team_id] + assigned_ids
            )
        if assignments:
            _bump_roster_version(slug, course['id'], db=db)
        db.commit()
        return jsonify({
            'success': True,
            'assigned': len(assignments),
            'remaining': len(student_ids) - len(assignments),
        })
    except Exception:
        db.rollback()
        raise


@app.route('/api/start_session_timer', methods=['POST'])
@instructor_login_required
def start_session_timer():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if state['phase'] not in ('setup', 'discussion'):
            db.rollback()
            return jsonify({'error': 'The session timer is only available before presentations'}), 409
        db.execute(
            '''UPDATE course_state
               SET session_started_at = COALESCE(session_started_at, CURRENT_TIMESTAMP)
               WHERE course_id = ?''',
            [state['course_id']]
        )
        fresh = db.execute(
            'SELECT session_started_at FROM course_state WHERE course_id = ?',
            [state['course_id']]
        ).fetchone()
        db.commit()
        return jsonify({
            'success': True,
            'session_started_at': fresh['session_started_at'] if fresh else None,
        })
    except Exception:
        db.rollback()
        raise


@app.route('/api/stop_session_timer', methods=['POST'])
@instructor_login_required
def stop_session_timer():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        db.execute(
            'UPDATE course_state SET session_started_at = NULL WHERE course_id = ?',
            [state['course_id']]
        )
        db.commit()
        return jsonify({'success': True, 'session_started_at': None})
    except Exception:
        db.rollback()
        raise


@app.route('/api/set_discussion_week', methods=['POST'])
@instructor_login_required
def set_discussion_week():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    week = data.get('week')
    if not isinstance(week, int) or week < 1:
        return jsonify({'error': 'Invalid week'}), 400
    class_dir = os.path.join(config.CLASSES_DIR, slug)
    try:
        catalog_week = validate_question_catalog(
            class_dir, weeks=[week]
        ).get_week(week)
    except (OSError, ValueError) as exc:
        return jsonify({'error': f'Could not validate week {week}: {exc}'}), 422
    if not catalog_week or not catalog_week.discussion.ready:
        issues = [] if not catalog_week else [
            issue.message for issue in catalog_week.discussion.issues
        ]
        detail = f": {issues[0]}" if issues else ''
        return jsonify({
            'error': (
                f'Week {week} discussion questions are not ready{detail}'
            )
        }), 422
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            'SELECT phase, session_key FROM course_state WHERE course_id = ?',
            [course['id']]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'The discussion week can only change during setup'}), 409
        try:
            _read_appendix_question_rows(slug, week)
        except QuestionParseError as exc:
            db.rollback()
            return jsonify({'error': str(exc)}), 422
        if catalog_week.presentation.ready:
            synced_count = sync_presentation_questions(
                slug, course['id'], week, db=db, commit=False
            )
        else:
            db.execute(
                '''DELETE FROM questions
                   WHERE course_id = ? AND COALESCE(week_num, 1) = ?
                     AND (source_key IS NULL
                          OR source_key LIKE 'presentation:%')''',
                [course['id'], week],
            )
            synced_count = None
        sync_appendix_questions(
            slug, course['id'], week, db=db, commit=False
        )
        db.execute(
            '''UPDATE course_state
               SET discussion_week = ?, current_question = NULL,
                   current_discussion_key = NULL,
                   current_discussion_source_key = NULL,
                   current_discussion_title = NULL,
                   current_discussion_content = NULL
               WHERE course_id = ?''',
            [week, course['id']]
        )
        total = db.execute(
            '''SELECT COUNT(*) AS c FROM questions
               WHERE course_id = ? AND COALESCE(week_num, 1) = ?
                 AND (? = 1 OR source_key LIKE 'appendix:%')''',
            [course['id'], week, int(catalog_week.presentation.ready)]
        ).fetchone()['c']
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({
        'success': True,
        'question_count': total,
        'question_sync': 'unavailable' if synced_count is None else 'synced',
        'presentation_ready': catalog_week.presentation.ready,
    })


@app.route('/api/toggle_lock_teams', methods=['POST'])
@instructor_login_required
def toggle_lock_teams():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    locked = 1 if data.get('locked') else 0
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            'SELECT phase, session_key FROM course_state WHERE course_id = ?',
            [course['id']]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Team locking is only available during setup'}), 409
        db.execute(
            'UPDATE course_state SET teams_locked = ? WHERE course_id = ?',
            [locked, course['id']]
        )
        db.commit()
        return jsonify({'success': True, 'locked': bool(locked)})
    except Exception:
        db.rollback()
        raise


@app.route('/api/set_max_members', methods=['POST'])
@instructor_login_required
def set_max_members():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    new_max = data.get('max_members')
    if not isinstance(new_max, int) or new_max < 1 or new_max > 99:
        return jsonify({'error': 'Max members must be between 1 and 99'}), 400
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version, max_members_per_team
               FROM course_state
               WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Team settings can only change during setup'}), 409

        excess_ids = []
        if new_max < (state['max_members_per_team'] or 5):
            full_teams = db.execute(
                '''SELECT team_id, COUNT(*) AS cnt FROM students
                   WHERE course_id = ? AND team_id IS NOT NULL AND is_active = 1
                   GROUP BY team_id HAVING COUNT(*) > ?''',
                [course['id'], new_max]
            ).fetchall()
            for team in full_teams:
                excess = db.execute(
                    '''SELECT id FROM students
                       WHERE course_id = ? AND team_id = ? AND is_active = 1
                       ORDER BY last_team_joined_at DESC NULLS LAST, id DESC LIMIT ?''',
                    [course['id'], team['team_id'], team['cnt'] - new_max]
                ).fetchall()
                excess_ids.extend(student['id'] for student in excess)
            if excess_ids:
                placeholders = ','.join('?' * len(excess_ids))
                db.execute(
                    f'''UPDATE students
                        SET last_team_id = COALESCE(last_team_id, team_id),
                            team_id = NULL
                        WHERE id IN ({placeholders})''',
                    excess_ids
                )

        db.execute(
            '''UPDATE course_state
               SET max_members_per_team = ?,
                   roster_version = COALESCE(roster_version, 0) + 1
               WHERE course_id = ?''',
            [new_max, course['id']]
        )
        db.commit()
        return jsonify({'success': True, 'max_members': new_max})
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Question Bank API
# ---------------------------------------------------------------------------

@app.route('/api/discussion_questions', methods=['GET'])
@instructor_login_required
def discussion_questions():
    """Load the instructor-only weekly discussion question bank."""
    slug = session['slug']
    class_dir = os.path.join(config.CLASSES_DIR, slug)
    try:
        catalog = validate_question_catalog(class_dir)
    except (OSError, ValueError) as exc:
        return jsonify({'error': f'Could not validate question catalog: {exc}'}), 422

    week_param = request.args.get('week')
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    ensure_schema(slug)
    state = query_db(
        slug,
        '''SELECT discussion_week, current_discussion_key,
                  current_discussion_source_key,
                  current_discussion_title, current_discussion_content
           FROM course_state WHERE course_id = ?''',
        [course['id']],
        one=True,
    )
    saved_week = state['discussion_week'] if state and state['discussion_week'] else 1

    questions_list = []
    weeks = [
        {
            'num': week.week,
            'file': os.path.basename(week.discussion.path),
            'ready': week.ready,
            'discussion_ready': week.discussion.ready,
            'presentation_ready': week.presentation.ready,
            'issues': [
                issue.message
                for section in (week.discussion, week.presentation)
                for issue in section.issues
            ],
        }
        for week in catalog.weeks
    ]

    if week_param and weeks:
        target = next((w for w in weeks if str(w['num']) == str(week_param)), weeks[0])
    elif weeks:
        target = next((w for w in weeks if w['num'] == saved_week), weeks[0])
    else:
        target = None

    def _load_md(filepath, prefix='', is_appendix=False):
        """Load questions with keys that survive list reordering/deletions."""
        out = []
        if not os.path.exists(filepath):
            return out
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            content = f.read()
        import yaml
        seen_appendix_ids = set()
        for position, (fm_block, body_block) in enumerate(
                parse_question_blocks(content), 1):
            try:
                fm = yaml.safe_load(fm_block) or {}
            except yaml.YAMLError as exc:
                raise QuestionParseError(
                    f'Invalid question frontmatter in question {position}'
                ) from exc
            title = str(fm.get('title') or '').strip()
            if not title:
                raise QuestionParseError(
                    f'Question {position} has no title'
                )
            digest = hashlib.sha256(
                (title + '\0' + body_block).encode('utf-8')
            ).hexdigest()[:16]
            explicit_id = str(fm.get('id') or '').strip()
            label = re.match(r'^A(\d+)\s*:', title, re.IGNORECASE) \
                if is_appendix else None
            if is_appendix and not label:
                raise QuestionParseError(
                    f'Appendix question {position} must start with an A-number label'
                )
            identity = explicit_id if re.fullmatch(
                r'[A-Za-z0-9_-]+', explicit_id
            ) else (f'A{label.group(1)}' if label else digest)
            item = {
                'key': f"{prefix}{identity}-{digest[:8]}",
                'title': title,
                'content': body_block,
            }
            if is_appendix:
                if identity in seen_appendix_ids:
                    raise QuestionParseError(
                        f'Duplicate appendix label {identity}'
                    )
                seen_appendix_ids.add(identity)
                item['appendix_id'] = identity
            out.append(item)
        return out

    try:
        if target and target['discussion_ready']:
            q_path = os.path.join(class_dir, target['file'])
            questions_list = _load_md(q_path, prefix=f"week-{target['num']}-q-")

        # Load appendix from the persistent data disk (survives deploys)
        appendix_week = target['num'] if target else saved_week
        appendix_path = _appendix_path(slug, appendix_week)
        appendix = _load_md(
            appendix_path, prefix=f"week-{appendix_week}-a-", is_appendix=True
        )
        questions_list.extend(appendix)
    except QuestionParseError as exc:
        return jsonify({'error': str(exc)}), 422

    return jsonify({
        'weeks': weeks,
        'current_week': appendix_week,
        'current_question': ({
            'key': state['current_discussion_key'],
            'source_key': active_discussion_source_key(state),
            'title': state['current_discussion_title'] or '',
            'content': state['current_discussion_content'] or '',
        } if state and state['current_discussion_key'] else None),
        'questions': questions_list
    })


def _appendix_dir(slug):
    """Directory for appendix question files on the persistent data disk."""
    d = os.path.join(config.DATA_DIR, slug, 'appendix')
    os.makedirs(d, exist_ok=True)
    # One-time migration: move old appendix files from classes/ to data disk
    for week in range(1, 20):
        old = os.path.join(config.CLASSES_DIR, slug, f'week-{week}-appendix.md')
        if os.path.exists(old):
            new = os.path.join(d, f'week-{week}-appendix.md')
            if not os.path.exists(new):
                import shutil
                shutil.move(old, new)
    return d


def _appendix_path(slug, week):
    """File path for a given week's appendix questions."""
    return os.path.join(_appendix_dir(slug), f'week-{week}-appendix.md')


@app.route('/api/questions', methods=['POST'])
@instructor_login_required
def add_question():
    """Add an appendix question to the persistent question file."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    title = str(data.get('title') or '').strip()
    content = str(data.get('content') or '').strip()
    try:
        requested_week = int(data.get('week'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Valid week required'}), 400
    if not title or not content:
        return jsonify({'error': 'Title and content required'}), 400
    if len(title) > 500 or len(content) > 50000:
        return jsonify({'error': 'Title or content is too long'}), 400

    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    original_content = ''
    file_written = False
    appendix_path = None
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, discussion_week
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        week = state['discussion_week'] if state and state['discussion_week'] else 1
        if requested_week != week:
            db.rollback()
            return jsonify({'error': 'The displayed appendix week is stale'}), 409
        appendix_path = _appendix_path(slug, week)
        if os.path.exists(appendix_path):
            with open(appendix_path, 'r', encoding='utf-8-sig') as handle:
                original_content = handle.read()
        existing_entries = parse_question_blocks(original_content) \
            if original_content.strip() else []

        import yaml
        highest_label = 0
        for position, (frontmatter, _body) in enumerate(existing_entries, 1):
            metadata = yaml.safe_load(frontmatter) or {}
            match = re.match(
                r'^A(\d+)\s*:', str(metadata.get('title') or ''), re.IGNORECASE
            )
            if not match:
                raise QuestionParseError(
                    f'Appendix question {position} must start with an A-number label'
                )
            highest_label = max(highest_label, int(match.group(1)))

        label = f'A{highest_label + 1}'
        frontmatter = f'title: {json.dumps(f"{label}: {title}")}'
        new_entries = existing_entries + [(frontmatter, content)]
        _write_text_atomic(
            appendix_path, _serialize_question_blocks(new_entries)
        )
        file_written = True
        sync_appendix_questions(
            slug, course['id'], week, db=db, commit=False
        )
        db.commit()
        return jsonify({
            'success': True,
            'label': label,
            'appendix_id': label,
        })
    except QuestionParseError as exc:
        try:
            if file_written:
                _write_text_atomic(appendix_path, original_content)
        finally:
            db.rollback()
        return jsonify({'error': str(exc)}), 422
    except Exception:
        try:
            if file_written:
                _write_text_atomic(appendix_path, original_content)
        finally:
            db.rollback()
        raise


@app.route('/api/delete_appendix_question', methods=['POST'])
@instructor_login_required
def delete_appendix_question():
    """Delete one appendix question by its stable A-number label."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    appendix_id = str(data.get('appendix_id') or '').strip().upper()
    if not re.fullmatch(r'A\d+', appendix_id):
        return jsonify({'error': 'Appendix question ID required'}), 400
    try:
        requested_week = int(data.get('week'))
    except (ValueError, TypeError):
        return jsonify({'error': 'Valid week required'}), 400

    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    original_content = ''
    file_written = False
    appendix_path = None
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT discussion_week, phase, session_key, current_discussion_key,
                      current_discussion_source_key FROM course_state
               WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if state and state['phase'] == 'competition':
            db.rollback()
            return jsonify({'error': 'Appendix questions cannot be deleted during presentations'}), 409
        week = state['discussion_week'] if state and state['discussion_week'] else 1
        if requested_week != week:
            db.rollback()
            return jsonify({'error': 'The displayed appendix week is stale'}), 409
        appendix_path = _appendix_path(slug, week)
        if not os.path.exists(appendix_path):
            db.rollback()
            return jsonify({'error': 'Appendix file not found'}), 404

        _read_appendix_question_rows(slug, week)
        with open(appendix_path, 'r', encoding='utf-8-sig') as handle:
            original_content = handle.read()
        entries = parse_question_blocks(original_content)
        import yaml
        selected_index = None
        selected_key = None
        for index, (frontmatter, body) in enumerate(entries):
            metadata = yaml.safe_load(frontmatter) or {}
            title = str(metadata.get('title') or '').strip()
            label = re.match(r'^A(\d+)\s*:', title, re.IGNORECASE)
            identity = f'A{label.group(1)}' if label else None
            if identity == appendix_id:
                digest = hashlib.sha256(
                    (title + '\0' + body).encode('utf-8')
                ).hexdigest()[:8]
                selected_index = index
                selected_key = f'week-{week}-a-{identity}-{digest}'
                break
        if selected_index is None:
            db.rollback()
            return jsonify({'error': 'Appendix question not found'}), 404

        del entries[selected_index]
        _write_text_atomic(appendix_path, _serialize_question_blocks(entries))
        file_written = True
        sync_appendix_questions(
            slug, course['id'], week, db=db, commit=False
        )
        unposted = active_discussion_source_key(state) == selected_key
        if unposted:
            db.execute(
                '''UPDATE course_state
                   SET current_question = NULL, current_discussion_key = NULL,
                       current_discussion_source_key = NULL,
                       current_discussion_title = NULL,
                       current_discussion_content = NULL
                   WHERE course_id = ?''',
                [course['id']]
            )
        db.commit()
        return jsonify({'success': True, 'unposted': unposted})
    except QuestionParseError as exc:
        try:
            if file_written:
                _write_text_atomic(appendix_path, original_content)
        finally:
            db.rollback()
        return jsonify({'error': str(exc)}), 422
    except Exception:
        try:
            if file_written:
                _write_text_atomic(appendix_path, original_content)
        finally:
            db.rollback()
        raise


@app.route('/api/unassign_all', methods=['POST'])
@instructor_login_required
def unassign_all():
    """Clear all team assignments."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Teams can only be changed during setup'}), 409
        count = db.execute(
            '''SELECT COUNT(*) AS c FROM students
               WHERE course_id = ? AND is_active = 1''', [course['id']]
        ).fetchone()['c']
        db.execute(
            '''UPDATE students
               SET last_team_id = COALESCE(last_team_id, team_id),
                   team_id = NULL
               WHERE course_id = ? AND is_active = 1''', [course['id']]
        )
        _bump_roster_version(slug, course['id'], db=db)
        db.commit()
        return jsonify({'success': True, 'count': count})
    except Exception:
        db.rollback()
        raise


# ---------------------------------------------------------------------------
# Competition / Presentation Control API
# ---------------------------------------------------------------------------

@app.route('/roster_template.csv')
@instructor_login_required
def roster_template():
    """Download a CSV template with example rows for roster upload."""
    csv_content = (
        'student_id,name,pin\n'
        '1001,Alice Chen,4271\n'
        '1002,Bob Garcia,8839\n'
        '1003,Cara Singh,1056\n'
    )
    return (
        csv_content,
        200,
        {
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename=roster_template.csv'
        }
    )


@app.route('/api/upload_roster', methods=['POST'])
@instructor_login_required
def upload_roster():
    """Replace-mode CSV roster upload with validation."""
    slug = session['slug']
    ensure_schema(slug)
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.csv'):
        return jsonify({'error': 'Please upload a CSV file'}), 400
    try:
        raw_content = file.read(MAX_ROSTER_BYTES + 1)
        if len(raw_content) > MAX_ROSTER_BYTES:
            return jsonify({'error': 'Roster file must be 1 MB or smaller'}), 413
        content = raw_content.decode('utf-8-sig')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
    except Exception:
        return jsonify({'error': 'Failed to read CSV file'}), 400

    if not rows:
        return jsonify({'error': 'Empty CSV'}), 400
    if len(rows) - 1 > MAX_ROSTER_ROWS:
        return jsonify({'error': f'Roster cannot contain more than {MAX_ROSTER_ROWS} students'}), 400

    # Detect header format
    header = [h.strip().lower() for h in rows[0]]
    if header[:3] == ['student_id', 'name', 'pin']:
        sid_col, name_col, pin_col = 0, 1, 2
    elif header[:3] == ['id', 'name', 'pin']:
        sid_col, name_col, pin_col = 0, 1, 2
    else:
        return jsonify({'error': 'CSV header must be: student_id, name, pin (or ID, Name, PIN)'}), 400

    # Parse and validate
    parsed = []
    errors = []
    seen_ids = set()
    for i, row in enumerate(rows[1:], start=2):
        if not row or not any(cell.strip() for cell in row):
            continue
        if len(row) <= sid_col or not row[sid_col].strip():
            errors.append(f'Line {i}: student ID is required')
            continue
        sid = row[sid_col].strip()
        name = row[name_col].strip() if len(row) > name_col else ''
        pin = row[pin_col].strip() if len(row) > pin_col else sid[-4:]
        if len(sid) > 100:
            errors.append(f'Line {i}: student ID must be 100 characters or fewer')
            continue
        if len(name) > 200:
            errors.append(f'Line {i}: name must be 200 characters or fewer')
            continue
        if sid in seen_ids:
            errors.append(f'Line {i}: duplicate student ID "{sid}"')
            continue
        seen_ids.add(sid)
        if not re.match(r'^\d{4}$', pin):
            errors.append(f'Line {i}: PIN must be exactly 4 digits for "{sid}"')
            continue
        parsed.append({'student_id': sid, 'name': name or None, 'pin': pin})

    if errors:
        return jsonify({
            'error': 'Validation failed',
            'details': errors[:20],
            'error_count': len(errors),
        }), 400
    if not parsed:
        return jsonify({'error': 'Roster must contain at least one student'}), 400

    state = query_db(
        slug,
        '''SELECT phase, session_key, roster_version
           FROM course_state WHERE course_id = ?''',
        [course['id']], one=True
    )
    form_state = {
        'expected_phase': request.form.get('expected_phase'),
        'expected_session_key': request.form.get('expected_session_key'),
        'expected_roster_version': request.form.get('expected_roster_version'),
    }
    guard = _expected_roster_state_guard(form_state, state)
    if guard:
        return jsonify({'error': guard[0]}), guard[1]
    if not state or state['phase'] != 'setup':
        return jsonify({'error': 'The roster can only be replaced during setup'}), 409
    preview_token = hashlib.sha256(
        raw_content + (
            f"\0{slug}:{state['session_key'] or 0}:{state['roster_version'] or 0}"
        ).encode('utf-8')
    ).hexdigest()
    existing_rows = query_db(slug,
        '''SELECT id, student_id, name, pin, is_active FROM students
           WHERE course_id = ?''',
        [course['id']])
    existing_by_sid = {r['student_id']: r for r in existing_rows}
    active_by_sid = {
        sid: row for sid, row in existing_by_sid.items() if row['is_active']
    }
    csv_sids = {p['student_id'] for p in parsed}
    preview_reactivated = sum(
        1 for person in parsed
        if (person['student_id'] in existing_by_sid and
            not existing_by_sid[person['student_id']]['is_active'])
    )
    preview_added = sum(1 for p in parsed if p['student_id'] not in active_by_sid)
    preview_updated = sum(
        1 for p in parsed
        if p['student_id'] in active_by_sid and (
            (active_by_sid[p['student_id']]['name'] or None) != (p['name'] or None)
            or active_by_sid[p['student_id']]['pin'] != p['pin']
        )
    )
    preview_removed = sum(
        1 for sid in active_by_sid if sid not in csv_sids
    )

    confirmed = str(request.form.get('confirm', '')).lower() in ('1', 'true', 'yes')
    if not confirmed:
        return jsonify({
            'success': True,
            'requires_confirmation': True,
            'preview_token': preview_token,
            'added': preview_added,
            'reactivated': preview_reactivated,
            'updated': preview_updated,
            'removed': preview_removed,
            'total': len(parsed),
        })
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        locked_state = db.execute(
            '''SELECT phase, session_key, roster_version
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(form_state, locked_state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not locked_state or locked_state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'The roster can only be replaced during setup'}), 409
        expected_token = hashlib.sha256(
            raw_content + (
                f"\0{slug}:{locked_state['session_key'] or 0}:"
                f"{locked_state['roster_version'] or 0}"
            ).encode('utf-8')
        ).hexdigest()
        if request.form.get('preview_token') != expected_token:
            db.rollback()
            return jsonify({'error': 'Roster changed after preview; preview it again'}), 409
        existing_rows = db.execute(
            '''SELECT id, student_id, name, pin, is_active FROM students
               WHERE course_id = ?''',
            [course['id']]
        ).fetchall()
        existing_by_sid = {row['student_id']: row for row in existing_rows}
        to_remove = [
            row['id'] for sid, row in existing_by_sid.items()
            if row['is_active'] and sid not in csv_sids
        ]
        if to_remove:
            _archive_students(
                slug, to_remove, bump_roster=False, db=db, commit=False
            )

        to_update = []
        to_insert = []
        reactivated = 0
        for person in parsed:
            existing = existing_by_sid.get(person['student_id'])
            if existing:
                if not existing['is_active']:
                    reactivated += 1
                if (not existing['is_active'] or
                        (existing['name'] or None) != (person['name'] or None) or
                        existing['pin'] != person['pin']):
                    to_update.append((
                        person['name'] or None, person['pin'], existing['id']
                    ))
            else:
                to_insert.append((
                    course['id'], person['student_id'], person['name'] or None,
                    person['pin'],
                ))
        if to_update:
            db.executemany(
                'UPDATE students SET name = ?, pin = ?, is_active = 1 WHERE id = ?',
                to_update
            )
        if to_insert:
            db.executemany(
                'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
                to_insert
            )
        if to_remove or to_update or to_insert:
            _bump_roster_version(slug, course['id'], db=db)
        db.commit()
    except Exception:
        db.rollback()
        raise

    return jsonify({
        'success': True,
        'requires_confirmation': False,
        'added': len(to_insert) + reactivated,
        'reactivated': reactivated,
        'updated': len(to_update) - reactivated,
        'removed': len(to_remove),
        'total': len(parsed),
    })


@app.route('/api/start_presentation', methods=['POST'])
@instructor_login_required
def start_presentation():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    team_id = data.get('team_id')
    question_id = data.get('question_id')
    time_cap = data.get('time_cap', 300)
    if not team_id or not question_id:
        return jsonify({'error': 'Team and question required'}), 400
    try:
        team_id = int(team_id)
        question_id = int(question_id)
        time_cap = int(time_cap)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid team, question, or time cap'}), 400
    if time_cap < 10 or time_cap > 3600:
        time_cap = 300
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?', [course['id']]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'competition':
            db.rollback()
            return jsonify({'error': 'Switch to Group Presentation first'}), 409
        if state and (state['active_team_id'] or state['active_question_id']):
            db.rollback()
            return jsonify({'error': 'Finish the active presentation first'}), 409
        max_teams = state['max_teams'] or 8
        visible_teams = db.execute(
            '''SELECT t.id, COUNT(s.id) AS member_count
               FROM teams t
               LEFT JOIN students s ON s.team_id = t.id AND s.is_active = 1
               WHERE t.course_id = ?
               GROUP BY t.id
               ORDER BY t.id LIMIT ?''',
            [course['id'], max_teams]
        ).fetchall()
        selected_team = next(
            (team for team in visible_teams if team['id'] == team_id), None
        )
        if selected_team is None:
            db.rollback()
            return jsonify({'error': 'Team is not available'}), 400
        if not selected_team['member_count']:
            db.rollback()
            return jsonify({'error': 'Cannot start a presentation for an empty team'}), 409
        question = db.execute(
            '''SELECT question_text, title, week_num, source_key
               FROM questions WHERE id = ? AND course_id = ?''',
            [question_id, course['id']]
        ).fetchone()
        if not question:
            db.rollback()
            return jsonify({'error': 'Question not found'}), 404
        selected_week = state['discussion_week'] or 1
        if (question['week_num'] or 1) != selected_week:
            db.rollback()
            return jsonify({
                'error': 'The selected question belongs to a different week'
            }), 409

        is_appendix = str(question['source_key'] or '').startswith('appendix:')
        if is_appendix:
            try:
                sync_appendix_questions(
                    slug, course['id'], selected_week, db=db, commit=False
                )
            except QuestionParseError as exc:
                db.rollback()
                return jsonify({'error': str(exc)}), 422
        else:
            try:
                catalog_week = validate_question_catalog(
                    os.path.join(config.CLASSES_DIR, slug),
                    weeks=[selected_week],
                ).get_week(selected_week)
            except (OSError, ValueError):
                catalog_week = None
            if not catalog_week or not catalog_week.presentation.ready:
                db.rollback()
                return jsonify({
                    'error': (
                        f'Week {selected_week} presentation questions are '
                        'not ready'
                    )
                }), 409
            sync_presentation_questions(
                slug, course['id'], selected_week, db=db, commit=False
            )

        question = db.execute(
            '''SELECT question_text, title FROM questions
               WHERE id = ? AND course_id = ?''',
            [question_id, course['id']]
        ).fetchone()
        if not question:
            db.rollback()
            return jsonify({
                'error': 'The question list changed; reload before starting'
            }), 409

        question_text = question['title'] or question['question_text']
        started_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')
        presentation_key = f'pres-{uuid.uuid4().hex}'
        db.execute(
            '''UPDATE course_state
               SET phase = 'competition', active_team_id = ?, active_question_id = ?,
                   current_question = ?, presentation_started_at = ?,
                   presentation_created_at = ?,
                   presentation_time_cap = ?, presentation_remaining = NULL,
                   poll_active = 0, poll_question_key = ?, poll_started_at = NULL,
                   current_discussion_key = NULL,
                   current_discussion_source_key = NULL,
                   current_discussion_title = NULL,
                   current_discussion_content = NULL
               WHERE course_id = ?''',
            [team_id, question_id, question_text, started_at, started_at, time_cap,
             presentation_key, course['id']]
        )
        db.commit()
        return jsonify({'success': True, 'presentation_key': presentation_key})
    except Exception:
        db.rollback()
        raise


@app.route('/api/stop_presentation', methods=['POST'])
@instructor_login_required
def stop_presentation():
    """Pause the presentation timer and save remaining time."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _presentation_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state['presentation_started_at']:
            db.rollback()
            return jsonify({'error': 'Presentation timer is already paused'}), 409
        started = _parse_db_datetime(state['presentation_started_at'])
        elapsed = (datetime.utcnow() - started).total_seconds()
        cap = state['presentation_time_cap'] or 300
        remaining = max(0, int(cap - elapsed))
        db.execute(
            '''UPDATE course_state
               SET presentation_started_at = NULL, presentation_remaining = ?
               WHERE course_id = ?''',
            [remaining, state['course_id']]
        )
        db.commit()
        return jsonify({'success': True, 'remaining': remaining})
    except Exception:
        db.rollback()
        raise


@app.route('/api/resume_presentation', methods=['POST'])
@instructor_login_required
def resume_presentation():
    """Resume a paused presentation while keeping the original time cap."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _presentation_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        remaining = state['presentation_remaining']
        if remaining is None or remaining <= 0:
            db.rollback()
            return jsonify({'error': 'No remaining time to resume'}), 409
        cap = state['presentation_time_cap'] or 300
        consumed = cap - remaining
        shifted_start = datetime.utcnow() - timedelta(seconds=consumed)
        db.execute(
            '''UPDATE course_state
               SET presentation_started_at = ?, presentation_remaining = NULL
               WHERE course_id = ?''',
            [shifted_start.strftime('%Y-%m-%d %H:%M:%S'), state['course_id']]
        )
        db.commit()
        return jsonify({'success': True, 'remaining': remaining})
    except Exception:
        db.rollback()
        raise


@app.route('/api/reset_presentation_timer', methods=['POST'])
@instructor_login_required
def reset_presentation_timer():
    """Reset the timer to the original time cap (paused state)."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _presentation_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        cap = state['presentation_time_cap'] or 300
        db.execute(
            '''UPDATE course_state
               SET presentation_started_at = NULL, presentation_remaining = ?
               WHERE course_id = ?''',
            [cap, state['course_id']]
        )
        db.commit()
        return jsonify({'success': True, 'cap': cap})
    except Exception:
        db.rollback()
        raise


@app.route('/api/next_presentation', methods=['POST'])
@instructor_login_required
def next_presentation():
    """Stop current presentation, save to history, clear for next."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _presentation_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if _poll_is_open(state):
            db.rollback()
            return jsonify({
                'error': 'Stop the active rating poll before finishing this presentation'
            }), 409
        _finalize_active_presentation(slug, state['course_id'], db=db)
        db.commit()
        return jsonify({'success': True})
    except Exception:
        db.rollback()
        raise


@app.route('/api/start_poll', methods=['POST'])
@instructor_login_required
def start_poll():
    """Open the 30-second rating window for the active presentation."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
    guard = _presentation_guard(data, state)
    if guard:
        db.rollback()
        return jsonify({'error': guard[0]}), guard[1]
    if _poll_is_open(state):
        started_at = state['poll_started_at']
        db.rollback()
        return jsonify({
            'success': True,
            'already_active': True,
            'poll_started_at': started_at,
            'poll_duration': POLL_DURATION,
            'poll_remaining': _derive_timing_state(state)['poll_remaining'],
        })
    # Derive the rating key from server state — never trust client input.
    question_key = active_presentation_key(state)
    if not question_key:
        db.rollback()
        return jsonify({'error': 'No active presentation'}), 400
    db.execute(
        '''UPDATE course_state
           SET poll_active = 1, poll_question_key = ?, poll_started_at = CURRENT_TIMESTAMP
           WHERE course_id = ?''',
        [question_key, state['course_id']]
    )
    fresh = db.execute(
        'SELECT * FROM course_state WHERE course_id = ?',
        [state['course_id']]
    ).fetchone()
    db.commit()
    return jsonify({
        'success': True,
        'poll_started_at': fresh['poll_started_at'] if fresh else None,
        'poll_duration': POLL_DURATION,
        'poll_remaining': _derive_timing_state(fresh)['poll_remaining'],
    })


@app.route('/api/stop_poll', methods=['POST'])
@instructor_login_required
def stop_poll():
    """Close the active rating window."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _presentation_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        db.execute(
            '''UPDATE course_state
               SET poll_active = 0, poll_started_at = NULL
               WHERE course_id = ?''',
            [state['course_id']]
        )
        db.commit()
        return jsonify({'success': True})
    except Exception:
        db.rollback()
        raise


@app.route('/api/cancel_presentation', methods=['POST'])
@instructor_login_required
def cancel_presentation():
    """Clear a mistaken presentation without adding it to history."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
        guard = _presentation_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        presentation_key = active_presentation_key(state)
        count = db.execute(
            '''SELECT COUNT(*) AS c FROM presentation_ratings
               WHERE course_id = ? AND question_key = ?''',
            [state['course_id'], presentation_key]
        ).fetchone()['c']
        if count and not data.get('discard_ratings'):
            db.rollback()
            return jsonify({
                'error': 'This presentation already has ratings',
                'rating_count': count,
                'requires_discard': True,
            }), 409
        if count:
            db.execute(
                '''DELETE FROM presentation_ratings
                   WHERE course_id = ? AND question_key = ?''',
                [state['course_id'], presentation_key]
            )
        _clear_active_presentation(db, state['course_id'])
        db.commit()
        return jsonify({'success': True, 'discarded_ratings': count})
    except Exception:
        db.rollback()
        raise


@app.route('/api/submit_rating', methods=['POST'])
@student_login_required
def submit_rating():
    """Student submits star ratings (1–5) for the current presentation."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    q1 = data.get('q1_developed')
    q2 = data.get('q2_easy')
    expected_key = data.get('presentation_key')
    if q1 is None or q2 is None:
        return jsonify({'error': 'Both ratings required'}), 400
    try:
        q1 = int(q1)
        q2 = int(q2)
    except (ValueError, TypeError):
        return jsonify({'error': 'Ratings must be integers'}), 400
    if not (1 <= q1 <= 5 and 1 <= q2 <= 5):
        return jsonify({'error': 'Ratings must be 1–5'}), 400

    if not isinstance(expected_key, str) or not expected_key:
        return jsonify({'error': 'Presentation key is required'}), 400

    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    state = db.execute('SELECT * FROM course_state LIMIT 1').fetchone()
    # Read and write under one lock so a delayed request cannot cross into the
    # next presentation after it passed validation.
    if not state or state['phase'] != 'competition' or \
       not state['active_team_id'] or not state['active_question_id']:
        db.rollback()
        return jsonify({'error': 'No active presentation to rate'}), 403

    student = db.execute(
        'SELECT * FROM students WHERE student_id = ? AND is_active = 1',
        [session['student_id']]
    ).fetchone()
    if not student:
        db.rollback()
        return jsonify({'error': 'Student not found'}), 404

    # Block self-grading — the presenting team cannot rate their own presentation.
    if student['team_id'] and state['active_team_id'] and \
       student['team_id'] == state['active_team_id']:
        db.rollback()
        return jsonify({'error': 'You cannot rate your own presentation'}), 403
    question_key = active_presentation_key(state)
    if not question_key:
        db.rollback()
        return jsonify({'error': 'No active presentation to rate'}), 403
    if expected_key != question_key:
        db.rollback()
        return jsonify({'error': 'The presentation has changed; refresh and try again'}), 409
    if not _poll_is_open(state):
        db.rollback()
        return jsonify({'error': 'The rating poll is closed'}), 403
    if not student['team_id']:
        db.rollback()
        return jsonify({'error': 'Join a team before rating presentations'}), 403

    presenting_team = db.execute(
        'SELECT name FROM teams WHERE id = ? AND course_id = ?',
        [state['active_team_id'], student['course_id']]
    ).fetchone()
    rater_team = db.execute(
        'SELECT name FROM teams WHERE id = ? AND course_id = ?',
        [student['team_id'], student['course_id']]
    ).fetchone()
    question = db.execute(
        '''SELECT title, question_text FROM questions
           WHERE id = ? AND course_id = ?''',
        [state['active_question_id'], student['course_id']]
    ).fetchone()
    db.execute(
        '''INSERT INTO presentation_ratings
           (course_id, student_id, question_key, session_key,
            presenting_team_id, presenting_team_name, question_id,
            question_title, rater_team_id, rater_team_name,
            q1_developed, q2_easy)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(course_id, student_id, question_key)
           DO UPDATE SET q1_developed = excluded.q1_developed,
                         q2_easy = excluded.q2_easy,
                         rater_team_id = excluded.rater_team_id,
                         rater_team_name = excluded.rater_team_name,
                         created_at = CURRENT_TIMESTAMP''',
        [student['course_id'], student['id'], question_key,
         state['session_key'] or 0, state['active_team_id'],
         presenting_team['name'] if presenting_team else 'Unknown',
         state['active_question_id'],
         (question['title'] or question['question_text']) if question else '',
         student['team_id'], rater_team['name'] if rater_team else 'Unknown',
         q1, q2]
    )
    db.commit()
    return jsonify({'success': True, 'presentation_key': question_key})


@app.route('/api/students', methods=['GET'])
@instructor_login_required
def api_students():
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(1, request.args.get('per_page', 10, type=int)), 100)
    sort_col = request.args.get('sort', 'student_id')
    order = request.args.get('order', 'asc')
    search = request.args.get('search', '').strip()
    team_filter = request.args.get('team', '')

    allowed_sorts = {'student_id': 's.student_id', 'name': 's.name', 'last_active_at': 's.last_active_at'}
    if sort_col not in allowed_sorts:
        sort_col = 'student_id'
    sort_sql = allowed_sorts[sort_col]
    order_sql = 'DESC' if order == 'desc' else 'ASC'

    cutoff = (datetime.utcnow() - timedelta(minutes=3)).strftime('%Y-%m-%d %H:%M:%S')

    where_clause = ''
    params = [course['id']]
    if search:
        where_clause += ' AND s.student_id LIKE ?'
        params.append(f'%{search}%')
    if team_filter == 'none':
        where_clause += ' AND s.team_id IS NULL'
    elif team_filter and team_filter != 'none':
        try:
            where_clause += ' AND s.team_id = ?'
            params.append(int(team_filter))
        except ValueError:
            pass  # invalid team filter — show all

    count = query_db(slug,
        f'''SELECT COUNT(*) as c FROM students s
            WHERE s.course_id = ? AND s.is_active = 1 {where_clause}''',
        params, one=True
    )
    total = count['c']
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    rows = query_db(slug,
        f'''SELECT s.id, s.student_id, s.name, s.team_id,
                   t.name as team_name, t.color as team_color,
                   s.last_login_at, s.last_active_at, s.last_team_joined_at
            FROM students s LEFT JOIN teams t ON s.team_id = t.id
            WHERE s.course_id = ? AND s.is_active = 1 {where_clause}
            ORDER BY {sort_sql} {order_sql}
            LIMIT ? OFFSET ?''',
        params + [per_page, offset]
    )

    students = []
    for r in rows:
        d = dict(r)
        d['is_online'] = r['last_active_at'] and r['last_active_at'] > cutoff
        students.append(d)

    max_teams = get_max_teams(slug, course['id'])
    teams = query_db(slug,
        'SELECT id, name, color FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
        [course['id'], max_teams]
    )

    return jsonify({
        'students': students,
        'teams': [dict(t) for t in teams],
        'page': page,
        'total_pages': total_pages,
        'total': total,
        'sort': sort_col,
        'order': order
    })


@app.route('/api/add_student', methods=['POST'])
@instructor_login_required
def add_student():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id', '').strip()
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    if not student_id or not pin:
        return jsonify({'error': 'Student ID and PIN are required'}), 400
    if len(student_id) > 100 or len(name) > 200:
        return jsonify({'error': 'Student ID or name is too long'}), 400
    if not re.match(r'^\d{4}$', pin):
        return jsonify({'error': 'PIN must be exactly 4 digits'}), 400
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Students can only be changed during setup'}), 409
        existing = db.execute(
            '''SELECT id, is_active FROM students
               WHERE course_id = ? AND student_id = ?''',
            [course['id'], student_id]
        ).fetchone()
        if existing:
            db.execute(
                'UPDATE students SET name = ?, pin = ?, is_active = 1 WHERE id = ?',
                [name or None, pin, existing['id']]
            )
            result = {
                'success': True,
                'updated': bool(existing['is_active']),
                'reactivated': not bool(existing['is_active']),
            }
        else:
            db.execute(
                '''INSERT INTO students (course_id, student_id, name, pin)
                   VALUES (?, ?, ?, ?)''',
                [course['id'], student_id, name or None, pin]
            )
            result = {'success': True, 'added': True}
        _bump_roster_version(slug, course['id'], db=db)
        db.commit()
        return jsonify(result)
    except Exception:
        db.rollback()
        raise


@app.route('/api/assign_student', methods=['POST'])
@instructor_login_required
def assign_student():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')  # DB row id
    team_id = data.get('team_id')  # None or '' to unassign
    if not student_id:
        return jsonify({'error': 'Student ID required'}), 400
    try:
        student_id = int(student_id)
        team_id = int(team_id) if team_id not in (None, '') else None
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid student or team'}), 400

    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version, max_teams,
                      max_members_per_team FROM course_state
               WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Teams can only be changed during setup'}), 409
        student = db.execute(
            '''SELECT id, team_id FROM students
               WHERE id = ? AND course_id = ? AND is_active = 1''',
            [student_id, course['id']]
        ).fetchone()
        if not student:
            db.rollback()
            return jsonify({'error': 'Student not found'}), 404
        if student['team_id'] == team_id:
            db.commit()
            return jsonify({'success': True})

        if team_id is not None:
            visible = db.execute(
                'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
                [course['id'], state['max_teams'] or 8]
            ).fetchall()
            if team_id not in {team['id'] for team in visible}:
                db.rollback()
                return jsonify({'error': 'Team is not available'}), 404
            member_count = db.execute(
                '''SELECT COUNT(*) AS c FROM students
                   WHERE course_id = ? AND team_id = ? AND is_active = 1''',
                [course['id'], team_id]
            ).fetchone()['c']
            if member_count >= (state['max_members_per_team'] or 5):
                db.rollback()
                return jsonify({'error': 'That team is full'}), 409

        db.execute(
            '''UPDATE students
               SET last_team_id = CASE
                       WHEN ? IS NULL THEN COALESCE(last_team_id, team_id)
                       ELSE ?
                   END,
                   team_id = ?,
                   last_team_joined_at = CASE
                       WHEN ? IS NULL THEN last_team_joined_at
                       ELSE CURRENT_TIMESTAMP
                   END
               WHERE id = ?''',
            [team_id, team_id, team_id, team_id, student_id]
        )
        _bump_roster_version(slug, course['id'], db=db)
        db.commit()
        return jsonify({'success': True})
    except Exception:
        db.rollback()
        raise


@app.route('/api/remove_student/<int:student_db_id>', methods=['DELETE'])
@instructor_login_required
def remove_student(student_db_id):
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    ensure_schema(slug)
    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        state = db.execute(
            '''SELECT phase, session_key, roster_version
               FROM course_state WHERE course_id = ?''',
            [course['id']]
        ).fetchone()
        guard = _expected_roster_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if not state or state['phase'] != 'setup':
            db.rollback()
            return jsonify({'error': 'Students can only be changed during setup'}), 409
        exists = db.execute(
            '''SELECT id FROM students
               WHERE id = ? AND course_id = ? AND is_active = 1''',
            [student_db_id, course['id']]
        ).fetchone()
        if not exists:
            db.rollback()
            return jsonify({'error': 'Student not found'}), 404
        _archive_students(
            slug, [student_db_id], bump_roster=True, db=db, commit=False
        )
        db.commit()
        return jsonify({'success': True})
    except Exception:
        db.rollback()
        raise


@app.route('/api/reset_data', methods=['POST'])
@instructor_login_required
def reset_data():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    if data.get('confirm_slug') != slug:
        return jsonify({'error': 'Type the course slug to confirm reset'}), 400
    ensure_schema(slug)
    initial_state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    guard = _expected_state_guard(data, initial_state)
    if guard:
        return jsonify({'error': guard[0]}), guard[1]
    if initial_state['phase'] not in ('setup', 'ended'):
        return jsonify({'error': 'Data can only be reset during Setup or after End Session'}), 409

    db = get_db(slug)
    db.execute('BEGIN IMMEDIATE')
    try:
        course = db.execute('SELECT id FROM courses LIMIT 1').fetchone()
        course_id = course['id']
        state = db.execute(
            'SELECT * FROM course_state WHERE course_id = ?', [course_id]
        ).fetchone()
        guard = _expected_state_guard(data, state)
        if guard:
            db.rollback()
            return jsonify({'error': guard[0]}), guard[1]
        if state['phase'] not in ('setup', 'ended'):
            db.rollback()
            return jsonify({'error': 'Data can only be reset during Setup or after End Session'}), 409
        # The write lock prevents any committed change from landing between
        # this backup snapshot and the destructive reset below.
        backup_name = _create_reset_backup(slug)
        db.execute('DELETE FROM peer_reviews WHERE course_id = ?', [course_id])
        db.execute('DELETE FROM teammate_thumbs WHERE course_id = ?', [course_id])
        db.execute('DELETE FROM presentation_ratings WHERE course_id = ?', [course_id])
        legacy_tables = {
            row['name'] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('discussion_responses', 'discussion_selections')"
            ).fetchall()
        }
        if 'discussion_responses' in legacy_tables:
            db.execute('DELETE FROM discussion_responses WHERE course_id = ?', [course_id])
        if 'discussion_selections' in legacy_tables:
            db.execute('DELETE FROM discussion_selections WHERE course_id = ?', [course_id])
        db.execute(
            '''UPDATE students
               SET team_id = NULL, last_login_at = NULL, last_active_at = NULL,
                   last_team_joined_at = NULL, last_team_id = NULL
               WHERE course_id = ?''',
            [course_id]
        )
        db.execute(
            '''UPDATE course_state SET
               phase = 'setup',
               active_team_id = NULL,
               active_question_id = NULL,
               current_question = NULL,
               current_discussion_key = NULL,
               current_discussion_source_key = NULL,
               current_discussion_title = NULL,
               current_discussion_content = NULL,
               presentation_started_at = NULL,
               presentation_created_at = NULL,
               presentation_time_cap = 300,
               presentation_remaining = NULL,
               poll_active = 0,
               poll_question_key = NULL,
               poll_started_at = NULL,
               presentation_history = '[]',
               teams_locked = 0,
               session_started_at = NULL,
               session_key = COALESCE(session_key, 0) + 1,
               roster_version = COALESCE(roster_version, 0) + 1
               WHERE course_id = ?''',
            [course_id]
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return jsonify({'success': True, 'backup': backup_name})


@app.route('/export/<slug>')
@instructor_login_required
def export_data(slug):
    if session.get('slug') != slug:
        flash('Unauthorized', 'error')
        return redirect(url_for('index'))

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
        from io import BytesIO
        import collections
    except ImportError:
        flash('Export library not available. Contact administrator.', 'error')
        return redirect(url_for('instructor_course', slug=slug))

    db = None
    snapshot_open = False
    try:
        db = get_db(slug)
        db.execute('BEGIN')
        snapshot_open = True
        course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
        if not course:
            db.rollback()
            snapshot_open = False
            flash('Course not found.', 'error')
            return redirect(url_for('index'))

        cid = course['id']
        state_row = query_db(
            slug,
            'SELECT * FROM course_state WHERE course_id = ?',
            [cid],
            one=True,
        )
        max_teams = (state_row['max_teams'] if state_row else None) or 8

        asset_files = []
        asset_bytes = 0
        class_dir = os.path.join(config.CLASSES_DIR, slug)
        if os.path.isdir(class_dir):
            for root, _dirs, files in os.walk(class_dir):
                for fname in files:
                    if fname in ('course.yaml', 'course.json'):
                        continue
                    fpath = os.path.join(root, fname)
                    if not os.path.isfile(fpath):
                        continue
                    relpath = os.path.relpath(
                        fpath, class_dir
                    ).replace('\\', '/')
                    asset_files.append((fpath, f'questions/{relpath}'))
                    asset_bytes += os.path.getsize(fpath)

        appendix_dir = os.path.join(config.DATA_DIR, slug, 'appendix')
        if os.path.isdir(appendix_dir):
            for fname in os.listdir(appendix_dir):
                fpath = os.path.join(appendix_dir, fname)
                if os.path.isfile(fpath):
                    asset_files.append((fpath, f'appendix/{fname}'))
                    asset_bytes += os.path.getsize(fpath)

        if asset_bytes > MAX_EXPORT_BYTES:
            db.rollback()
            snapshot_open = False
            flash('The export files exceed the 50 MB safety limit.', 'error')
            return redirect(url_for('instructor_course', slug=slug))

        row_counts = query_db(
            slug,
            '''SELECT
                   (SELECT COUNT(*) FROM students WHERE course_id = ?) AS students,
                   (SELECT COUNT(*) FROM (
                        SELECT id FROM teams WHERE course_id = ?
                        ORDER BY id LIMIT ?
                    )) AS teams,
                   (SELECT COUNT(*) FROM teammate_thumbs
                    WHERE course_id = ?) AS thumbs,
                   (SELECT COUNT(*) FROM presentation_ratings
                    WHERE course_id = ?) AS ratings''',
            [cid, cid, max_teams, cid, cid],
            one=True
        )
        if sum(row_counts) > MAX_EXPORT_ROWS:
            db.rollback()
            snapshot_open = False
            flash('The export is too large. Please contact the administrator.', 'error')
            return redirect(url_for('instructor_course', slug=slug))

        # ── gather all data ──

        students = query_db(slug,
            '''SELECT s.*, t.name as team_name
               FROM students s LEFT JOIN teams t ON t.id = CASE
                   WHEN s.is_active = 1 THEN s.team_id
                   ELSE COALESCE(s.team_id, s.last_team_id) END
               WHERE s.course_id = ? ORDER BY s.name''', [cid])

        teams = query_db(slug,
            '''SELECT t.*,
                    (SELECT COUNT(*) FROM students s
                     WHERE s.team_id = t.id AND s.is_active = 1) as member_count
               FROM teams t WHERE t.course_id = ? ORDER BY t.id LIMIT ?''',
            [cid, max_teams])

        peer_reviews = query_db(slug,
            '''SELECT p.grader_id, p.recipient_id,
                      g.student_id as grader_sid, g.name as grader_name,
                      r.student_id as recipient_sid, r.name as recipient_name,
                      p.grader_team_id, p.grader_team_name,
                      p.recipient_team_id, p.recipient_team_name,
                      p.session_key, p.question_key, p.source_question_key,
                      p.question_title,
                      'overall' AS criterion, 1 AS score, p.created_at
               FROM teammate_thumbs p
               JOIN students g ON p.grader_id = g.id
               JOIN students r ON p.recipient_id = r.id
               WHERE p.course_id = ?
               ORDER BY p.created_at''', [cid])

        ratings = query_db(slug,
            '''SELECT pr.question_key, pr.session_key,
                      pr.presenting_team_id, pr.presenting_team_name,
                      pr.question_id, pr.question_title,
                      pr.rater_team_id, pr.rater_team_name,
                      pr.student_id as rater_db_id,
                      s.student_id as rater_sid, s.name as rater_name,
                      pr.q1_developed, pr.q2_easy, pr.created_at
               FROM presentation_ratings pr
               JOIN students s ON pr.student_id = s.id
               WHERE pr.course_id = ?
               ORDER BY pr.question_key, pr.created_at''', [cid])

        # Map question_key → presenting team from presentation_history
        key_to_team = {}
        if state_row and state_row['presentation_history']:
            for h in json.loads(state_row['presentation_history']):
                qkey = h.get('presentation_key') or f"pres-{h.get('started_at', '')}"
                key_to_team[qkey] = h.get('team', 'Unknown')

        # Team id → name lookup
        team_id_to_name = {t['id']: t['name'] for t in teams}

        # Release the read transaction after every workbook input is captured.
        db.commit()
        snapshot_open = False

        # ── styles ──

        wb = Workbook()
        header_font = Font(bold=True, color='FFFFFF')
        header_fill = PatternFill(start_color='13294B', end_color='13294B', fill_type='solid')
        header_align = Alignment(horizontal='center', vertical='center')
        bold_font = Font(bold=True)
        freeze_align = Alignment(vertical='top', wrap_text=True)

        def style_header(ws, headers):
            for col, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=col, value=h)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_align
            ws.freeze_panes = 'A2'

        def set_col_widths(ws, headers, rows):
            """Compute column widths from actual content."""
            for col_idx, h in enumerate(headers, 1):
                max_len = len(str(h))
                for row in rows:
                    val = row[col_idx - 1] if col_idx <= len(row) else ''
                    if val is not None:
                        max_len = max(max_len, min(len(str(val)), 50))
                ws.column_dimensions[get_column_letter(col_idx)].width = max(max_len + 3, 12)

        # ════════════════════════════════════════════════════════════════════
        # TAB 1: Summary — quick overview
        # ════════════════════════════════════════════════════════════════════
        ws1 = wb.active
        ws1.title = 'Summary'

        info_rows = [
            ('Course', course['name']),
            ('Code', course['code'] or ''),
            ('Semester', course['semester'] or ''),
            ('Export Date', datetime.now().strftime('%Y-%m-%d %H:%M')),
            ('Active Students', sum(1 for student in students if student['is_active'])),
            ('Archived Students', sum(1 for student in students if not student['is_active'])),
            ('Current Visible Teams', len(teams)),
            ('Total Peer Reviews (thumbs)', len(peer_reviews)),
            ('Total Presentation Ratings', len(ratings)),
        ]
        for r, (label, val) in enumerate(info_rows, 1):
            ws1.cell(row=r, column=1, value=label).font = bold_font
            ws1.cell(row=r, column=2, value=val)

        # Team leaderboard
        r = len(info_rows) + 2
        ws1.cell(row=r, column=1, value='Team Leaderboard').font = bold_font
        r += 1
        lb_headers = ['Rank', 'Team', 'Members', 'Presentations', 'Avg Developed (1-5)', 'Avg Easy (1-5)', 'Combined Avg']
        for col, h in enumerate(lb_headers, 1):
            cell = ws1.cell(row=r, column=col, value=h)
            cell.font = header_font; cell.fill = header_fill; cell.alignment = header_align
        r += 1

        # Build team scores — fix: use combined count for sort denominator
        team_scores = collections.defaultdict(lambda: {'dev': [], 'easy': []})
        visible_team_ids = {team['id'] for team in teams}
        visible_team_names = {team['name'] for team in teams}
        for rt in ratings:
            if rt['presenting_team_id'] in visible_team_ids:
                tname = team_id_to_name[rt['presenting_team_id']]
            else:
                tname = rt['presenting_team_name'] or key_to_team.get(
                    rt['question_key'], 'Unknown'
                )
                if tname not in visible_team_names:
                    continue
            if rt['q1_developed'] is not None:
                team_scores[tname]['dev'].append(rt['q1_developed'])
            if rt['q2_easy'] is not None:
                team_scores[tname]['easy'].append(rt['q2_easy'])

        def combined_fraction(sc):
            all_vals = sc['dev'] + sc['easy']
            return Fraction(sum(all_vals), len(all_vals)) if all_vals else Fraction(0, 1)

        def combined_avg(sc):
            return round(float(combined_fraction(sc)), 2)

        team_sorted = sorted(
            team_scores.items(),
            key=lambda item: (-combined_fraction(item[1]), item[0].casefold())
        )
        rank_by_team = {}
        prior_fraction = None
        for position, (tname, sc) in enumerate(team_sorted, 1):
            fraction = combined_fraction(sc)
            if prior_fraction is None or fraction != prior_fraction:
                rank = position
            rank_by_team[tname] = rank
            avg_d = round(sum(sc['dev']) / len(sc['dev']), 2) if sc['dev'] else ''
            avg_e = round(sum(sc['easy']) / len(sc['easy']), 2) if sc['easy'] else ''
            total = combined_avg(sc)
            member_cnt = next((t['member_count'] for t in teams if t['name'] == tname), '')
            pres_cnt = sum(1 for k, v in key_to_team.items() if v == tname)
            ws1.cell(row=r, column=1, value=rank)
            ws1.cell(row=r, column=2, value=tname)
            ws1.cell(row=r, column=3, value=member_cnt)
            ws1.cell(row=r, column=4, value=pres_cnt)
            ws1.cell(row=r, column=5, value=avg_d)
            ws1.cell(row=r, column=6, value=avg_e)
            ws1.cell(row=r, column=7, value=total if total else '')
            r += 1
            prior_fraction = fraction

        ws1.column_dimensions['A'].width = 28
        ws1.column_dimensions['B'].width = 22
        ws1.column_dimensions['C'].width = 12
        ws1.column_dimensions['D'].width = 16
        ws1.column_dimensions['E'].width = 20
        ws1.column_dimensions['F'].width = 18
        ws1.column_dimensions['G'].width = 14

        # ════════════════════════════════════════════════════════════════════
        # TAB 2: Students — one row per student, gradebook-ready
        # ════════════════════════════════════════════════════════════════════
        ws2 = wb.create_sheet('Students')
        s_headers = [
            'student_id', 'name', 'team', 'status',
            'thumbs_given', 'thumbs_received',
            'presentation_ratings_given',
            'last_login', 'last_active',
        ]
        style_header(ws2, s_headers)

        # Pre-compute per-student counts (keyed by DB id)
        thumbs_given = collections.Counter(pr['grader_id'] for pr in peer_reviews)
        thumbs_recv = collections.Counter(pr['recipient_id'] for pr in peer_reviews)
        ratings_given = collections.Counter(rt['rater_db_id'] for rt in ratings)

        s_rows = []
        for stu in students:
            row = [
                stu['student_id'],
                stu['name'] or '',
                stu['team_name'] or '',
                'active' if stu['is_active'] else 'archived',
                thumbs_given.get(stu['id'], 0),
                thumbs_recv.get(stu['id'], 0),
                ratings_given.get(stu['id'], 0),
                stu['last_login_at'] or '',
                stu['last_active_at'] or '',
            ]
            s_rows.append(row)

        for i, row in enumerate(s_rows, 2):
            for col, val in enumerate(row, 1):
                ws2.cell(row=i, column=col, value=val)
        set_col_widths(ws2, s_headers, s_rows)

        # ════════════════════════════════════════════════════════════════════
        # TAB 3: Teams — one row per team with aggregates
        # ════════════════════════════════════════════════════════════════════
        ws3 = wb.create_sheet('Teams')
        t_headers = ['team_id', 'team_name', 'rank', 'member_count', 'presentations',
                     'avg_developed', 'avg_easy', 'combined_avg']
        style_header(ws3, t_headers)

        t_rows = []
        for t in teams:
            tname = t['name']
            sc = team_scores.get(tname, {'dev': [], 'easy': []})
            avg_d = round(sum(sc['dev']) / len(sc['dev']), 2) if sc['dev'] else ''
            avg_e = round(sum(sc['easy']) / len(sc['easy']), 2) if sc['easy'] else ''
            total = combined_avg(sc)
            pres_cnt = sum(1 for k, v in key_to_team.items() if v == tname)
            t_rows.append([
                t['id'], tname, rank_by_team.get(tname, ''), t['member_count'], pres_cnt,
                avg_d, avg_e, total if total else '',
            ])
        for i, row in enumerate(t_rows, 2):
            for col, val in enumerate(row, 1):
                ws3.cell(row=i, column=col, value=val)
        set_col_widths(ws3, t_headers, t_rows)

        # ════════════════════════════════════════════════════════════════════
        # TAB 4: Peer Reviews — raw thumbs-up data
        # ════════════════════════════════════════════════════════════════════
        ws4 = wb.create_sheet('Peer Reviews')
        pr_headers = [
            'grader_id', 'grader_name', 'grader_team_id', 'grader_team',
            'recipient_id', 'recipient_name', 'recipient_team_id',
            'recipient_team', 'session_key', 'discussion_post_key',
            'source_question_key', 'question_title', 'criterion', 'score',
            'time',
        ]
        style_header(ws4, pr_headers)

        pr_rows = []
        for pr in peer_reviews:
            pr_rows.append([
                pr['grader_sid'], pr['grader_name'], pr['grader_team_id'],
                pr['grader_team_name'], pr['recipient_sid'],
                pr['recipient_name'], pr['recipient_team_id'],
                pr['recipient_team_name'], pr['session_key'],
                pr['question_key'], pr['source_question_key'],
                pr['question_title'],
                pr['criterion'], pr['score'], pr['created_at'],
            ])
        for i, row in enumerate(pr_rows, 2):
            for col, val in enumerate(row, 1):
                ws4.cell(row=i, column=col, value=val)
        set_col_widths(ws4, pr_headers, pr_rows)

        # ════════════════════════════════════════════════════════════════════
        # TAB 5: Presentation Ratings — raw star ratings
        # ════════════════════════════════════════════════════════════════════
        ws5 = wb.create_sheet('Presentation Ratings')
        rt_headers = ['session_key', 'presentation_key', 'question_id',
                      'question_title', 'presenting_team_id', 'presenting_team',
                      'rater_id', 'rater_name', 'rater_team_id', 'rater_team',
                      'developed_1to5', 'easy_1to5', 'time']
        style_header(ws5, rt_headers)

        rt_rows = []
        for rt in ratings:
            rt_rows.append([
                rt['session_key'],
                rt['question_key'],
                rt['question_id'], rt['question_title'] or '',
                rt['presenting_team_id'],
                rt['presenting_team_name'] or key_to_team.get(
                    rt['question_key'], 'Unknown'
                ),
                rt['rater_sid'], rt['rater_name'],
                rt['rater_team_id'],
                rt['rater_team_name'] or (
                    team_id_to_name.get(rt['rater_team_id'], '')
                    if rt['rater_team_id'] else ''
                ),
                rt['q1_developed'], rt['q2_easy'], rt['created_at'],
            ])
        for i, row in enumerate(rt_rows, 2):
            for col, val in enumerate(row, 1):
                ws5.cell(row=i, column=col, value=val)
        set_col_widths(ws5, rt_headers, rt_rows)

        # ── save ──
        # Save the workbook to an in-memory buffer
        xlsx_buf = BytesIO()
        wb.save(xlsx_buf)
        xlsx_buf.seek(0)

        if xlsx_buf.getbuffer().nbytes + asset_bytes > MAX_EXPORT_BYTES:
            flash('The export files exceed the 50 MB safety limit.', 'error')
            return redirect(url_for('instructor_course', slug=slug))

        # Build a ZIP containing the Excel file + all question content files
        import zipfile
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Excel interaction data
            with zf.open('course_data.xlsx', 'w') as workbook_member:
                import shutil
                xlsx_buf.seek(0)
                shutil.copyfileobj(xlsx_buf, workbook_member, 1024 * 1024)

            for fpath, archive_name in asset_files:
                zf.write(fpath, archive_name)

        zip_buf.seek(0)
        filename = f"popping_{course['code'] or slug}_export.zip"
        return send_file(
            zip_buf,
            mimetype='application/zip',
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        if snapshot_open and db is not None:
            db.rollback()
        import traceback
        traceback.print_exc()
        flash(f'Export failed: {e}', 'error')
        return redirect(url_for('instructor_course', slug=slug))
