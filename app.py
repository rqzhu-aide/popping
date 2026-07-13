import os
import sys
import csv
import gzip
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash
)

import config
from database import init_app, query_db, execute_db, get_db, init_db, get_max_teams, get_max_members_per_team, ensure_schema

app = Flask(__name__)
app.config.from_object(config)
init_app(app)

PHASES = ['setup', 'discussion', 'competition', 'ended']

PHASE_LABELS = {
    'setup': 'Setup',
    'discussion': 'Group Discussion',
    'competition': 'Group Presentation',
    'ended': 'End Session'
}

# Duration (seconds) of the "Start Poll" highlight window. Grading itself is
# always available during a presentation; the poll is just a synced 30s nudge
# shown as a gliding bar (instructor) and a pulsing countdown (students).
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


def parse_question_blocks(content):
    """Parse repeated YAML-frontmatter question blocks from a markdown file."""
    content = content.strip()
    if content.startswith('---'):
        content = content[3:].lstrip()
    blocks = content.split('\n---\n')
    entries = []
    i = 0
    while i + 1 < len(blocks):
        fm_block = blocks[i].strip()
        body_block = blocks[i + 1].strip()
        if fm_block:
            entries.append((fm_block, body_block))
        i += 2
    return entries


def read_presentation_question_index(slug, week_num):
    week_dir = os.path.join(config.CLASSES_DIR, slug, f'week{week_num}')
    index_path = os.path.join(week_dir, 'index.md')
    if not os.path.exists(index_path):
        return None

    questions = []
    with open(index_path, 'r', encoding='utf-8') as f:
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


def sync_presentation_questions(slug, course_id, week_num):
    """Sync a week's pre-rendered questions without changing stable row IDs."""
    questions = read_presentation_question_index(slug, week_num)
    if questions is None:
        return None

    ensure_schema(slug)
    db = get_db(slug)
    existing = db.execute(
        '''SELECT * FROM questions
           WHERE course_id = ? AND COALESCE(week_num, 1) = ?
             AND (source_key IS NULL OR source_key LIKE 'presentation:%')''',
        [course_id, week_num]
    ).fetchall()
    by_source = {row['source_key']: row for row in existing if row['source_key']}
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
                   (course_id, question_num, question_text, title, week_num, source_key)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                [course_id, q['num'], question_text, q['title'], week_num, source_key]
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

    if changed:
        db.commit()
    return len(questions)


def _read_appendix_question_rows(slug, week_num):
    """Read valid appendix blocks and assign stable presentation source keys."""
    appendix_path = _appendix_path(slug, week_num)
    if not os.path.exists(appendix_path):
        return []

    with open(appendix_path, 'r', encoding='utf-8') as f:
        entries = parse_question_blocks(f.read())

    import yaml
    rows = []
    seen_numbers = set()
    for position, (fm_block, body_block) in enumerate(entries, 1):
        try:
            metadata = yaml.safe_load(fm_block) or {}
        except Exception:
            continue
        title = str(metadata.get('title') or '').strip()
        if not title:
            continue

        body = body_block.strip()
        label_match = re.match(r'^A(\d+)\s*:', title, re.IGNORECASE)
        if not label_match:
            app.logger.warning(
                'Skipping unlabeled appendix question %s for %s week %s',
                position, slug, week_num
            )
            continue
        question_num = int(label_match.group(1))
        if question_num in seen_numbers:
            app.logger.warning(
                'Skipping duplicate appendix label A%s for %s week %s',
                question_num, slug, week_num
            )
            continue
        seen_numbers.add(question_num)
        rows.append({
            'source_key': f'appendix:{week_num}:A{question_num}',
            'question_num': question_num,
            'question_text': title[:200],
            'title': title,
            'content': body,
        })
    return rows


def sync_appendix_questions(slug, course_id, week_num):
    """Make a week's appendix questions selectable during presentations."""
    ensure_schema(slug)
    desired = _read_appendix_question_rows(slug, week_num)
    db = get_db(slug)
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
                [course_id, question['question_num'], question['question_text'],
                 question['title'], question['content'], week_num,
                 question['source_key']]
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

    if changed:
        db.commit()
    return len(desired)


# In-memory cache for pre-rendered question HTML files.
# Without this, /api/state reads the same .html file from disk ~100 times/sec
# (once per student per poll).  Key: (slug, week_num, question_num).
_question_html_cache = {}


def load_question_html(slug, week_num, question_num):
    """Read pre-rendered HTML for a question from the week folder.

    Path: classes/<slug>/week<N>/q<NN>.html  (zero-padded, e.g. q01.html)
    Returns HTML string or None if file not found.
    Results are cached in-process since question files don't change mid-session.
    """
    cache_key = (slug, week_num, question_num)
    if cache_key in _question_html_cache:
        return _question_html_cache[cache_key]

    filepath = os.path.join(config.CLASSES_DIR, slug, f'week{week_num}', f'q{question_num:02d}.html')
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()
        _question_html_cache[cache_key] = html
        return html
    except (FileNotFoundError, IOError):
        return None


@app.before_request
def track_student_activity():
    if 'student_id' in session and 'slug' in session:
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
                    "UPDATE students SET last_active_at = CURRENT_TIMESTAMP WHERE student_id = ?",
                    [session['student_id']]
                )
                session['last_active_synced_at'] = now.isoformat()
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


def student_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'student_id' not in session or 'slug' not in session:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def instructor_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'instructor_id' not in session or 'slug' not in session:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def _scan_courses():
    """Scan CLASSES_DIR for course configs, only return active courses."""
    courses = []
    if not os.path.isdir(config.CLASSES_DIR):
        return courses
    for slug in sorted(os.listdir(config.CLASSES_DIR)):
        class_dir = os.path.join(config.CLASSES_DIR, slug)
        db_path = os.path.join(config.DATA_DIR, slug, 'popping.db')
        yaml_path = os.path.join(class_dir, 'course.yaml')
        if not os.path.isdir(class_dir) or not os.path.exists(yaml_path):
            continue
        try:
            import yaml
            with open(yaml_path) as f:
                cfg = yaml.safe_load(f)
            # Only show active courses on landing page
            if not cfg.get('active', False):
                continue
            instructor_name = ''
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path)
                try:
                    conn.row_factory = sqlite3.Row
                    instructor = conn.execute('SELECT name FROM instructors LIMIT 1').fetchone()
                    if instructor:
                        instructor_name = instructor['name']
                finally:
                    conn.close()
            courses.append({
                'id': cfg['slug'],
                'slug': cfg['slug'],
                'name': cfg['name'],
                'code': cfg.get('code'),
                'semester': cfg.get('semester'),
                'url': cfg.get('url'),
                'has_db': os.path.exists(db_path),
                'instructor_name': instructor_name
            })
        except Exception as e:
            import logging
            logging.warning(f'_scan_courses: skipped "{slug}": {e}')
    return courses


@app.route('/')
def index():
    if 'student_id' in session and 'slug' in session:
        return redirect(url_for('dashboard'))
    if 'instructor_id' in session and 'slug' in session:
        return redirect(url_for('instructor_course', slug=session['slug']))
    courses = _scan_courses()
    return render_template('index.html', courses=courses)


@app.route('/login/<slug>', methods=['GET', 'POST'])
def login(slug):
    db_path = course_db_path(slug)
    if not db_path or not os.path.exists(db_path):
        flash('Course not found.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        pin = request.form.get('pin', '').strip()
        if not student_id or not pin:
            flash('Please enter both ID and PIN.', 'error')
            return render_template('login.html', slug=slug)
        student = query_db(slug,
            'SELECT * FROM students WHERE student_id = ? AND pin = ?',
            [student_id, pin], one=True
        )
        if student:
            execute_db(slug,
                'UPDATE students SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?',
                [student['id']]
            )
            session['student_id'] = student['student_id']
            session['name'] = student['name'] or student['student_id']
            session['slug'] = slug
            return redirect(url_for('dashboard'))
        flash('Invalid ID or PIN for this course.', 'error')

    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)
    return render_template('login.html', course=course, slug=slug)


@app.route('/instructor_login/<slug>', methods=['GET', 'POST'])
def instructor_login(slug):
    db_path = course_db_path(slug)
    if not db_path or not os.path.exists(db_path):
        flash('Course not found.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        pin = request.form.get('pin', '').strip()
        if not username or not pin:
            flash('Please enter both username and PIN.', 'error')
            return render_template('instructor_login.html', slug=slug)
        instructor = query_db(slug,
            'SELECT * FROM instructors WHERE username = ? AND pin = ?',
            [username, pin], one=True
        )
        if instructor:
            session['instructor_id'] = instructor['id']
            session['instructor_name'] = instructor['name']
            session['slug'] = slug
            return redirect(url_for('instructor_course', slug=slug))
        flash('Invalid username or PIN.', 'error')

    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)
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
    session.clear()
    # Pick the first student (Alice Chen, Team Alpha)
    student = query_db('demo',
        'SELECT * FROM students ORDER BY id LIMIT 1', one=True)
    if not student:
        flash('Demo data not found.', 'error')
        return redirect(url_for('demo'))
    session['student_id'] = student['student_id']
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
        'SELECT * FROM students WHERE student_id = ?',
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
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY id LIMIT ?', [course['id'], max_teams]
    )
    teams_locked = state['teams_locked'] if state and 'teams_locked' in state.keys() else 0
    teammates = []
    if team:
        teammates = query_db(slug,
            'SELECT student_id, name FROM students WHERE team_id = ? AND id != ? ORDER BY name',
            [team['id'], student['id']]
        )
    return render_template(
        'dashboard.html',
        student=student, team=team, teams=teams,
        state=state, course=course, phases=PHASES,
        teams_locked=teams_locked, teammates=teammates
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
    selected_week = state['discussion_week'] if state and state['discussion_week'] else 1
    if (not state or state['phase'] != 'competition' or
            not state['active_question_id']):
        sync_presentation_questions(slug, course['id'], selected_week)
        sync_appendix_questions(slug, course['id'], selected_week)
    max_teams = get_max_teams(slug, course['id'])
    teams_locked = state['teams_locked'] if state and 'teams_locked' in state.keys() else 0
    students = query_db(slug,
        '''SELECT s.*, t.name as team_name, t.color as team_color
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           WHERE s.course_id = ? ORDER BY s.name''',
        [course['id']]
    )
    questions = query_db(slug,
        '''SELECT * FROM questions
           WHERE course_id = ? AND COALESCE(week_num, 1) = ?
           ORDER BY CASE WHEN source_key LIKE 'appendix:%' THEN 1 ELSE 0 END,
                    question_num, id''',
        [course['id'], selected_week]
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
        # Total participants (have team or have activity)
        participants = query_db(slug,
            '''SELECT COUNT(*) as c FROM students
               WHERE course_id = ? AND (team_id IS NOT NULL
                   OR last_login_at IS NOT NULL
                   OR last_active_at IS NOT NULL)''',
            [course['id']], one=True)
        # Top students by thumbs-up count
        thumbs = query_db(slug,
            '''SELECT s.name, s.student_id, COUNT(*) as thumbs
               FROM peer_reviews p
               JOIN students s ON p.recipient_id = s.id
               WHERE p.course_id = ? AND p.score > 0
               GROUP BY p.recipient_id
               ORDER BY thumbs DESC''',
            [course['id']])
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
        team_ratings = _compute_top_teams(slug, course['id'], hist)
        end_stats = {
            'participants': participants['c'] if participants else 0,
            'top_students': top_students,
            'top_teams': team_ratings
        }

    # Track which questions have already been presented
    presented_question_ids = set()
    if state and state['presentation_history']:
        try:
            for h in json.loads(state['presentation_history']):
                if 'question_id' in h:
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


def _delete_student(slug, db_id):
    """Delete a student together with their dependent rows.

    Existing live databases predate the ON DELETE CASCADE schema, so dependents
    are removed explicitly first — otherwise, with PRAGMA foreign_keys=ON,
    deleting a student who has activity raises a constraint error. On databases
    created from the current schema the explicit deletes are simply redundant.
    """
    _delete_students(slug, [db_id])


def _delete_students(slug, db_ids, bump_roster=True):
    """Batch delete multiple students and their dependent rows in one commit."""
    if not db_ids:
        return
    db = get_db(slug)
    ph = ','.join('?' * len(db_ids))
    course_ids = [row['course_id'] for row in db.execute(
        f'SELECT DISTINCT course_id FROM students WHERE id IN ({ph})', db_ids
    ).fetchall()]
    db.execute(f'DELETE FROM peer_reviews WHERE grader_id IN ({ph}) OR recipient_id IN ({ph})',
               db_ids + db_ids)
    db.execute(f'DELETE FROM presentation_ratings WHERE student_id IN ({ph})', db_ids)
    # Old course databases may still contain retired student-input tables.
    legacy_tables = {
        row['name'] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('discussion_responses', 'discussion_selections')"
        ).fetchall()
    }
    if 'discussion_responses' in legacy_tables:
        db.execute(f'DELETE FROM discussion_responses WHERE student_id IN ({ph})', db_ids)
    if 'discussion_selections' in legacy_tables:
        db.execute(f'DELETE FROM discussion_selections WHERE student_id IN ({ph})', db_ids)
    db.execute(f'DELETE FROM students WHERE id IN ({ph})', db_ids)
    if bump_roster:
        for course_id in course_ids:
            _bump_roster_version(slug, course_id, db=db)
    db.commit()


# ---------------------------------------------------------------------------
# Shared helpers for state/teams computation (used by /api/state, /api/teams,
# and the consolidated /api/poll endpoint).
# ---------------------------------------------------------------------------

def _compute_top_teams(slug, course_id, history):
    """Compute team rankings by average presentation rating.

    ``history`` is the already-parsed presentation_history list (each item
    is a dict with 'started_at' and 'team' keys).  Pass the parsed list
    directly to avoid a redundant course_state re-query on the poll path.
    Returns a list of ``{'name': str, 'avg_score': float}`` sorted descending.
    """
    import collections

    history_json = history or []
    key_to_team = {}
    for h in history_json:
        qkey = f"pres-{h.get('started_at', '')}"
        key_to_team[qkey] = h.get('team', 'Unknown')

    all_ratings = query_db(slug,
        '''SELECT question_key, q1_developed, q2_easy
           FROM presentation_ratings WHERE course_id = ?''',
        [course_id])

    team_scores = collections.defaultdict(list)
    for r in all_ratings:
        tname = key_to_team.get(r['question_key'])
        if tname:
            team_scores[tname].append(r['q1_developed'] + r['q2_easy'])

    team_ratings = []
    for tname, scores in team_scores.items():
        avg = sum(scores) / len(scores) if scores else 0
        team_ratings.append({'name': tname, 'avg_score': round(avg, 2)})
    team_ratings.sort(key=lambda x: x['avg_score'], reverse=True)
    return team_ratings


def _compute_state(slug, include_poll_count=True, known_question_id=None):
    """Compute the course-state dict — shared by /api/state and /api/poll.

    This is the single source of truth for presentation timer, poll status,
    active team/question, and the student's own team. Question content is
    omitted when ``known_question_id`` confirms that the client already has it.
    """
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    if state:
        state = dict(state)  # mutable copy — avoids re-query on poll auto-close

    active_team = None
    if state and state['active_team_id']:
        active_team = query_db(slug,
            'SELECT * FROM teams WHERE id = ?', [state['active_team_id']], one=True)

    my_team = None
    if 'student_id' in session:
        me = query_db(slug,
            'SELECT * FROM students WHERE student_id = ?',
            [session['student_id']], one=True)
        if me and me['team_id']:
            my_team = query_db(slug,
                'SELECT * FROM teams WHERE id = ?', [me['team_id']], one=True)

    active_question = None
    if state and state['active_question_id']:
        if known_question_id == state['active_question_id']:
            active_question = {
                'id': state['active_question_id'],
                'content_unchanged': True,
            }
        else:
            aq = query_db(slug,
                'SELECT * FROM questions WHERE id = ?', [state['active_question_id']], one=True)
            if aq:
                active_question = dict(aq)
                week = active_question.get(
                    'week_num',
                    state['discussion_week'] if state and state['discussion_week'] else 1
                )
                source_key = active_question.get('source_key') or ''
                if not source_key.startswith('appendix:'):
                    html = load_question_html(slug, week, active_question['question_num'])
                    if html:
                        active_question['html_content'] = html

    # Compute presentation remaining seconds
    presentation_remaining = state['presentation_remaining'] if state else None
    if state and state['presentation_started_at'] and state['presentation_time_cap']:
        try:
            started = _parse_db_datetime(state['presentation_started_at'])
            elapsed = (datetime.utcnow() - started).total_seconds()
            cap = state['presentation_time_cap'] or 300
            presentation_remaining = max(0, int(cap - elapsed))
        except Exception:
            pass

    # Auto-close the poll highlight once POLL_DURATION has elapsed
    poll_active_bool = bool(state['poll_active']) if state else False
    if state and state['poll_active'] and state['poll_started_at']:
        try:
            poll_started = _parse_db_datetime(state['poll_started_at'])
            if (datetime.utcnow() - poll_started).total_seconds() >= POLL_DURATION:
                execute_db(slug,
                    'UPDATE course_state SET poll_active = 0, poll_started_at = NULL '
                    'WHERE course_id = ?', [state['course_id']])
                state['poll_active'] = 0
                state['poll_started_at'] = None
                poll_active_bool = False
        except Exception:
            pass

    # Poll count — use course_id from the already-fetched state row
    # (no separate SELECT needed)
    poll_count = None
    pres_key = active_presentation_key(state)
    if include_poll_count and state and pres_key:
        cid = state['course_id']
        cnt = query_db(slug,
            'SELECT COUNT(DISTINCT student_id) as c FROM presentation_ratings '
            'WHERE course_id = ? AND question_key = ?',
            [cid, pres_key], one=True)
        poll_count = cnt['c'] if cnt else 0

    result = {
        'phase': state['phase'] if state else 'setup',
        'active_team': dict(active_team) if active_team else None,
        'active_question': active_question,
        'my_team': dict(my_team) if my_team else None,
        'current_question': state['current_question'] if state else None,
        'presentation_started_at': state['presentation_started_at'] if state else None,
        'presentation_time_cap': state['presentation_time_cap'] if state else 300,
        'presentation_remaining': presentation_remaining,
        'teams_locked': bool(state['teams_locked']) if state else False,
        'poll_active': poll_active_bool,
        'poll_started_at': state['poll_started_at'] if state else None,
        'poll_duration': POLL_DURATION,
        'poll_question_key': state['poll_question_key'] if state else None,
        'roster_version': state.get('roster_version', 0) if state else 0,
        'presentation_history': json.loads(state['presentation_history'])
                                if state and state['presentation_history'] else [],
        # Internal metadata used by api_poll for ended-phase ranking.
        '_course_id': state['course_id'] if state else None,
    }
    if include_poll_count:
        result['poll_count'] = poll_count or 0
    return result


def _compute_teams(slug, course_id, max_teams=None):
    """Compute the teams + members list for the versioned roster endpoint.

    Uses 2 queries total (teams + all members) instead of N+1.
    Pass ``max_teams`` when the caller already has the visible-team limit.
    """
    if max_teams is None:
        max_teams = get_max_teams(slug, course_id)
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
        [course_id, max_teams])
    if not teams:
        return []
    team_ids = [t['id'] for t in teams]
    # Single query for ALL members across ALL teams, grouped in Python
    placeholders = ','.join('?' * len(team_ids))
    all_members = query_db(slug,
        f'SELECT student_id, name, team_id FROM students WHERE team_id IN ({placeholders})',
        team_ids)
    members_by_team = {}
    for m in all_members:
        members_by_team.setdefault(m['team_id'], []).append({'student_id': m['student_id'], 'name': m['name']})
    return [
        {'id': t['id'], 'name': t['name'], 'color': t['color'],
         'members': members_by_team.get(t['id'], [])}
        for t in teams
    ]


@app.route('/api/teams', methods=['GET'])
def api_teams():
    slug = _get_slug_from_session()
    if not slug or ('student_id' not in session and 'instructor_id' not in session):
        return jsonify({'error': 'Not logged in'}), 401
    course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
    return jsonify(_compute_teams(slug, course['id']))


@app.route('/api/join_team', methods=['POST'])
@student_login_required
def join_team():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    team_id = data.get('team_id')
    if team_id is None:
        return jsonify({'error': 'Team ID required'}), 400
    student = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    state = query_db(slug, 'SELECT course_id, phase, teams_locked FROM course_state LIMIT 1', one=True)
    if not state or state['phase'] != 'setup':
        return jsonify({'error': 'Team selection is closed'}), 403
    if state['teams_locked']:
        return jsonify({'error': 'Teams are currently locked by the instructor'}), 403

    # Leaving team (team_id = 0 means unassign)
    if not team_id:
        execute_db(slug, 'UPDATE students SET team_id = NULL WHERE id = ?', [student['id']])
        _bump_roster_version(slug, state['course_id'])
        return jsonify({'success': True})
    try:
        team_id = int(team_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid team ID'}), 400

    # Check team capacity
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    max_teams = get_max_teams(slug, course['id'])
    visible_teams = query_db(slug,
        'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
        [course['id'], max_teams]
    )
    if team_id not in {t['id'] for t in visible_teams}:
        return jsonify({'error': 'Team is not available'}), 400

    max_members = get_max_members_per_team(slug, course['id'])
    member_count = query_db(slug,
        'SELECT COUNT(*) as c FROM students WHERE team_id = ?', [team_id], one=True
    )
    if member_count and member_count['c'] >= max_members:
        # LIFO: kick the member who joined most recently
        last_joiner = query_db(slug,
            '''SELECT id FROM students
               WHERE team_id = ? AND last_team_joined_at IS NOT NULL
               ORDER BY last_team_joined_at DESC LIMIT 1''',
            [team_id], one=True
        )
        if last_joiner:
            execute_db(slug,
                'UPDATE students SET team_id = NULL WHERE id = ?',
                [last_joiner['id']]
            )
        else:
            # Fallback: no join timestamps, kick any member
            any_member = query_db(slug,
                'SELECT id FROM students WHERE team_id = ? LIMIT 1',
                [team_id], one=True
            )
            if any_member:
                execute_db(slug,
                    'UPDATE students SET team_id = NULL WHERE id = ?',
                    [any_member['id']]
                )

    execute_db(slug,
        '''UPDATE students
           SET team_id = ?, last_team_id = ?, last_team_joined_at = CURRENT_TIMESTAMP
           WHERE id = ?''',
        [team_id, team_id, student['id']]
    )
    _bump_roster_version(slug, course['id'])
    return jsonify({'success': True})


@app.route('/api/state', methods=['GET'])
def api_state():
    """Course state — accessible to both students and instructors."""
    slug = session.get('slug')
    if not slug:
        return jsonify({'error': 'Not logged in'}), 401
    if 'student_id' not in session and 'instructor_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    is_instructor = 'instructor_id' in session
    state_data = _compute_state(
        slug,
        include_poll_count=is_instructor,
        known_question_id=request.args.get('known_question_id', type=int),
    )
    # Strip internal + grading metadata from student responses
    state_data.pop('_course_id', None)
    if 'student_id' in session:
        state_data.pop('presentation_history', None)
    return jsonify(state_data)


@app.route('/api/poll', methods=['GET'])
def api_poll():
    """Lightweight polling endpoint returning frequently changing state.

    Roster data is fetched separately only when ``roster_version`` changes.
    Clients may send ``known_question_id`` so unchanged question bodies are
    omitted. ``poll_interval`` lets clients adapt to the current phase.
    """
    slug = session.get('slug')
    if not slug:
        return jsonify({'error': 'Not logged in'}), 401
    if 'student_id' not in session and 'instructor_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401

    ensure_schema(slug)

    is_instructor = 'instructor_id' in session

    # --- State ---
    state_data = _compute_state(
        slug,
        include_poll_count=is_instructor,
        known_question_id=request.args.get('known_question_id', type=int),
    )
    course_id = state_data.pop('_course_id')

    # Strip grading metadata from student responses — students never see
    # poll counts or presentation history (rating counts per team).
    # Save history first — needed for top-teams computation below.
    pres_history = state_data.get('presentation_history', [])
    if 'student_id' in session:
        state_data.pop('presentation_history', None)

    # --- Top 3 teams (only when session has ended, students only) ---
    top_teams = None
    if state_data['phase'] == 'ended' and 'student_id' in session:
        all_ranked = _compute_top_teams(slug, course_id, pres_history)
        # Students see only names — no scores
        top_teams = [{'name': t['name']} for t in all_ranked[:3]]

    # --- Adaptive interval hint ---
    if state_data['phase'] == 'competition':
        poll_interval = 4000
    elif state_data['phase'] == 'discussion':
        poll_interval = 5000
    else:
        poll_interval = 8000

    return jsonify({
        'state': state_data,
        'top_teams': top_teams,
        'poll_interval': poll_interval
    })


@app.route('/api/grade_peer', methods=['POST'])
@student_login_required
def grade_peer():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    recipient_sid = data.get('recipient_id')
    if recipient_sid is None:
        return jsonify({'error': 'Recipient is required'}), 400
    grader = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not grader:
        return jsonify({'error': 'Grader not found'}), 404
    # Resolve recipient_id from student_id string to DB id
    recipient = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [str(recipient_sid)], one=True
    )
    if not recipient:
        return jsonify({'error': 'Recipient not found'}), 404
    if grader['id'] == recipient['id']:
        return jsonify({'error': 'Cannot grade yourself'}), 400
    state = query_db(slug, 'SELECT phase FROM course_state LIMIT 1', one=True)
    if not state or state['phase'] != 'discussion':
        return jsonify({'error': 'Teammate thumbs are only open during discussion'}), 403
    if not grader['team_id'] or recipient['team_id'] != grader['team_id']:
        return jsonify({'error': 'You can only grade teammates'}), 403
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        '''INSERT INTO peer_reviews (course_id, grader_id, recipient_id, criterion, score)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, grader_id, recipient_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [course['id'], grader['id'], recipient['id'], 'overall', 1]
    )
    return jsonify({'success': True})



# ---------------------------------------------------------------------------
# Instructor API
# ---------------------------------------------------------------------------

@app.route('/api/set_phase', methods=['POST'])
@instructor_login_required
def set_phase():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    phase = data.get('phase')
    if phase not in PHASES:
        return jsonify({'error': 'Invalid phase'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    state = query_db(slug, 'SELECT phase FROM course_state LIMIT 1', one=True)
    old_phase = state['phase'] if state else 'setup'
    # When leaving competition, clean up all in-flight presentation state
    # to prevent ghost timers/polls from haunting the next phase.
    if old_phase == 'competition' and phase != 'competition':
        execute_db(slug,
            '''UPDATE course_state
               SET phase = ?,
                   active_team_id = NULL, active_question_id = NULL,
                   current_question = NULL,
                   presentation_started_at = NULL, presentation_remaining = NULL,
                   poll_active = 0, poll_question_key = NULL, poll_started_at = NULL
               WHERE course_id = ?''',
            [phase, course['id']]
        )
    else:
        execute_db(slug,
            'UPDATE course_state SET phase = ? WHERE course_id = ?',
            [phase, course['id']]
        )
    return jsonify({'success': True, 'phase': phase})


@app.route('/api/set_question', methods=['POST'])
@instructor_login_required
def set_question():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    question = data.get('question', '')
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE course_state SET current_question = ? WHERE course_id = ?',
        [question, course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/set_max_teams', methods=['POST'])
@instructor_login_required
def set_max_teams():
    slug = session['slug']
    ensure_schema(slug)
    data = request.get_json(silent=True) or {}
    new_max = data.get('max_teams')
    if not isinstance(new_max, int) or new_max < 1 or new_max > 20:
        return jsonify({'error': 'Team count must be between 1 and 20'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    current_max = get_max_teams(slug, course['id'])
    if new_max < current_max:
        # Unassign students in teams beyond the new limit
        teams_beyond = query_db(slug,
            'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT -1 OFFSET ?',
            [course['id'], new_max]
        )
        if teams_beyond:
            ids = [t['id'] for t in teams_beyond]
            placeholders = ','.join(['?'] * len(ids))
            execute_db(slug,
                f"UPDATE students SET team_id = NULL WHERE team_id IN ({placeholders})",
                ids
            )
    execute_db(slug,
        '''UPDATE course_state
           SET max_teams = ?, roster_version = COALESCE(roster_version, 0) + 1
           WHERE course_id = ?''',
        [new_max, course['id']]
    )
    return jsonify({'success': True, 'max_teams': new_max})


@app.route('/api/random_assign', methods=['POST'])
@instructor_login_required
def random_assign():
    slug = session['slug']
    import random as rnd
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    max_teams = get_max_teams(slug, course['id'])
    max_members = get_max_members_per_team(slug, course['id'])

    teams = query_db(slug,
        'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
        [course['id'], max_teams]
    )
    if not teams:
        return jsonify({'error': 'No teams available'}), 400

    team_ids = [t['id'] for t in teams]
    unassigned = query_db(slug,
        'SELECT id FROM students WHERE course_id = ? AND team_id IS NULL',
        [course['id']]
    )
    if not unassigned:
        return jsonify({'success': True, 'assigned': 0})

    student_ids = [s['id'] for s in unassigned]
    rnd.shuffle(student_ids)

    # Count current members per team (single query, not N+1)
    placeholders = ','.join('?' * len(team_ids))
    count_rows = query_db(slug,
        f'SELECT team_id, COUNT(*) as c FROM students WHERE team_id IN ({placeholders}) GROUP BY team_id',
        team_ids)
    counts = {r['team_id']: r['c'] for r in count_rows}
    for tid in team_ids:
        counts.setdefault(tid, 0)

    # Assign each student to the smallest team (fill evenly).
    # Compute all assignments in Python first, then batch the UPDATEs
    # grouped by team_id (at most N_teams queries instead of N_students).
    assignments = {}  # student_db_id -> team_id
    for sid in student_ids:
        min_count = min(counts.values())
        candidates = [tid for tid in team_ids if counts[tid] == min_count and counts[tid] < max_members]
        if not candidates:
            candidates = [tid for tid in team_ids if counts[tid] < max_members]
        if not candidates:
            break
        tid = rnd.choice(candidates)
        assignments[sid] = tid
        counts[tid] += 1

    # Batch: one UPDATE per team_id
    by_team = {}
    for sid, tid in assignments.items():
        by_team.setdefault(tid, []).append(sid)
    for tid, sids in by_team.items():
        placeholders = ','.join('?' * len(sids))
        execute_db(slug,
            f'UPDATE students SET team_id = ?, last_team_id = ?, '
            f'last_team_joined_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})',
            [tid, tid] + sids
        )

    if assignments:
        _bump_roster_version(slug, course['id'])

    return jsonify({'success': True, 'assigned': len(assignments)})


@app.route('/api/start_session_timer', methods=['POST'])
@instructor_login_required
def start_session_timer():
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        "UPDATE course_state SET session_started_at = CURRENT_TIMESTAMP WHERE course_id = ?",
        [course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/stop_session_timer', methods=['POST'])
@instructor_login_required
def stop_session_timer():
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        "UPDATE course_state SET session_started_at = NULL WHERE course_id = ?",
        [course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/set_discussion_week', methods=['POST'])
@instructor_login_required
def set_discussion_week():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    week = data.get('week')
    if not isinstance(week, int) or week < 1:
        return jsonify({'error': 'Invalid week'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    ensure_schema(slug)
    execute_db(slug,
        'UPDATE course_state SET discussion_week = ? WHERE course_id = ?',
        [week, course['id']]
    )
    synced_count = sync_presentation_questions(slug, course['id'], week)
    sync_appendix_questions(slug, course['id'], week)
    total = query_db(
        slug,
        '''SELECT COUNT(*) AS c FROM questions
           WHERE course_id = ? AND COALESCE(week_num, 1) = ?''',
        [course['id'], week],
        one=True
    )
    return jsonify({
        'success': True,
        'question_count': total['c'] if total else 0,
        'question_sync': 'not_found' if synced_count is None else 'synced'
    })


@app.route('/api/toggle_lock_teams', methods=['POST'])
@instructor_login_required
def toggle_lock_teams():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    locked = 1 if data.get('locked') else 0
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE course_state SET teams_locked = ? WHERE course_id = ?',
        [locked, course['id']]
    )
    return jsonify({'success': True, 'locked': bool(locked)})


@app.route('/api/set_max_members', methods=['POST'])
@instructor_login_required
def set_max_members():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    new_max = data.get('max_members')
    if not isinstance(new_max, int) or new_max < 1 or new_max > 99:
        return jsonify({'error': 'Max members must be between 1 and 99'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)

    # If reducing, unassign excess members from affected teams
    current_max = get_max_members_per_team(slug, course['id'])
    excess_ids = []
    if new_max < current_max:
        # Find teams with more than new_max members, collect all excess IDs
        full_teams = query_db(slug,
            '''SELECT team_id, COUNT(*) as cnt FROM students
               WHERE team_id IS NOT NULL GROUP BY team_id HAVING cnt > ?''',
            [new_max]
        )
        for ft in full_teams:
            # LIFO: remove the most recent joiners first
            excess = query_db(slug,
                '''SELECT id FROM students WHERE team_id = ?
                   ORDER BY last_team_joined_at DESC NULLS LAST LIMIT ?''',
                [ft['team_id'], ft['cnt'] - new_max]
            )
            excess_ids.extend(s['id'] for s in excess)
        if excess_ids:
            placeholders = ','.join('?' * len(excess_ids))
            execute_db(slug,
                f'UPDATE students SET team_id = NULL WHERE id IN ({placeholders})',
                excess_ids)

    execute_db(slug,
        'UPDATE course_state SET max_members_per_team = ? WHERE course_id = ?',
        [new_max, course['id']]
    )
    if excess_ids:
        _bump_roster_version(slug, course['id'])
    return jsonify({'success': True, 'max_members': new_max})


# ---------------------------------------------------------------------------
# Question Bank API
# ---------------------------------------------------------------------------

@app.route('/api/discussion_questions', methods=['GET'])
def discussion_questions():
    """Load questions from weekly .md + appendix files. Accessible to both roles."""
    slug = session.get('slug')
    if not slug:
        return jsonify({'error': 'Not logged in'}), 401
    import glob as glob_mod

    class_dir = os.path.join(config.CLASSES_DIR, slug)
    question_files = sorted(
        [os.path.basename(f) for f in glob_mod.glob(os.path.join(class_dir, 'week-*-questions.md'))]
    )

    week_param = request.args.get('week')
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    ensure_schema(slug)
    state = query_db(
        slug,
        'SELECT discussion_week, current_question FROM course_state WHERE course_id = ?',
        [course['id']],
        one=True,
    )
    saved_week = state['discussion_week'] if state and state['discussion_week'] else 1

    questions_list = []
    weeks = []

    for qf in question_files:
        m = re.match(r'week-(\d+)-questions\.md', qf)
        wnum = int(m.group(1)) if m else 0
        weeks.append({'num': wnum, 'file': qf})

    if week_param and weeks:
        target = next((w for w in weeks if str(w['num']) == str(week_param)), weeks[0])
    elif weeks:
        target = next((w for w in weeks if w['num'] == saved_week), weeks[0])
    else:
        target = None

    def _load_md(filepath, prefix=''):
        """Load questions from a markdown file with YAML frontmatter."""
        out = []
        if not os.path.exists(filepath):
            return out
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            import yaml
            for i, (fm_block, body_block) in enumerate(parse_question_blocks(content)):
                try:
                    fm = yaml.safe_load(fm_block)
                    if fm and fm.get('title'):
                        key = f"{prefix}{i}"
                        out.append({
                            'key': key,
                            'title': fm.get('title', 'Untitled'),
                            'content': body_block
                        })
                except Exception:
                    pass
        except Exception:
            pass
        return out

    if target:
        q_path = os.path.join(class_dir, target['file'])
        questions_list = _load_md(q_path, prefix=f"week-{target['num']}-q")

    # Load appendix from the persistent data disk (survives deploys)
    appendix_week = target['num'] if target else saved_week
    appendix_path = _appendix_path(slug, appendix_week)
    appendix = _load_md(appendix_path, prefix=f"week-{appendix_week}-a")
    questions_list.extend(appendix)

    return jsonify({
        'weeks': weeks,
        'current_week': target['num'] if target else None,
        'current_question': state['current_question'] if state else None,
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
    """Add an appendix question — stored as a file on the persistent data disk."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    title = data.get('title', '').strip()
    content = data.get('content', '').strip()
    if not title or not content:
        return jsonify({'error': 'Title and content required'}), 400

    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    ensure_schema(slug)
    state = query_db(slug, 'SELECT discussion_week FROM course_state WHERE course_id = ?',
                     [course['id']], one=True)
    week = state['discussion_week'] if state and state['discussion_week'] else 1

    appendix_path = _appendix_path(slug, week)

    # Continue after the highest existing A-number so deletions cannot create
    # duplicate labels (for example, deleting A2 must not reuse A3).
    highest_label = 0
    if os.path.exists(appendix_path):
        with open(appendix_path, 'r', encoding='utf-8') as f:
            existing_entries = parse_question_blocks(f.read())
        import yaml
        for fm_block, _ in existing_entries:
            try:
                metadata = yaml.safe_load(fm_block) or {}
            except Exception:
                continue
            match = re.match(
                r'^A(\d+)\s*:', str(metadata.get('title') or ''),
                re.IGNORECASE
            )
            if match:
                highest_label = max(highest_label, int(match.group(1)))
    label = f'A{highest_label + 1}'
    frontmatter_title = json.dumps(f"{label}: {title}")

    block = (
        f"---\ntitle: {frontmatter_title}\n---\n\n"
        f"{content}\n"
    )
    with open(appendix_path, 'a', encoding='utf-8') as f:
        f.write(block)
    sync_appendix_questions(slug, course['id'], week)
    return jsonify({
        'success': True,
        'label': label,
    })


@app.route('/api/delete_appendix_question', methods=['POST'])
@instructor_login_required
def delete_appendix_question():
    """Delete an appendix question by index (0-based) from the persistent data disk."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    index = data.get('index')
    if index is None:
        return jsonify({'error': 'Index required'}), 400
    try:
        index = int(index)
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid index'}), 400

    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    state = query_db(slug, 'SELECT discussion_week FROM course_state WHERE course_id = ?',
                     [course['id']], one=True)
    week = state['discussion_week'] if state and state['discussion_week'] else 1
    appendix_path = _appendix_path(slug, week)
    if not os.path.exists(appendix_path):
        return jsonify({'error': 'Appendix file not found'}), 404

    with open(appendix_path, 'r', encoding='utf-8') as f:
        content = f.read()

    entries = parse_question_blocks(content)

    if index < 0 or index >= len(entries):
        return jsonify({'error': 'Index out of range'}), 400

    del entries[index]

    if not entries:
        os.remove(appendix_path)
    else:
        # Rebuild file
        new_content = ''
        for fm, body in entries:
            new_content += f"---\n{fm}\n---\n\n{body}\n\n"
        with open(appendix_path, 'w', encoding='utf-8') as f:
            f.write(new_content.strip() + '\n')

    sync_appendix_questions(slug, course['id'], week)
    return jsonify({'success': True})


@app.route('/api/unassign_all', methods=['POST'])
@instructor_login_required
def unassign_all():
    """Clear all team assignments."""
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE students SET team_id = NULL WHERE course_id = ?',
        [course['id']])
    _bump_roster_version(slug, course['id'])
    count = query_db(slug,
        'SELECT COUNT(*) as c FROM students WHERE course_id = ?',
        [course['id']], one=True)
    return jsonify({'success': True, 'count': count['c'] if count else 0})


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
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return jsonify({'error': 'Please upload a CSV file'}), 400
    try:
        content = file.read().decode('utf-8')
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
    except Exception:
        return jsonify({'error': 'Failed to read CSV file'}), 400

    if not rows:
        return jsonify({'error': 'Empty CSV'}), 400

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
        if not row or not row[sid_col].strip():
            continue
        sid = row[sid_col].strip()
        name = row[name_col].strip() if len(row) > name_col else ''
        pin = row[pin_col].strip() if len(row) > pin_col else sid[-4:]
        if sid in seen_ids:
            errors.append(f'Line {i}: duplicate student ID "{sid}"')
            continue
        seen_ids.add(sid)
        if not re.match(r'^\d{4}$', pin):
            errors.append(f'Line {i}: PIN must be exactly 4 digits for "{sid}"')
            continue
        parsed.append({'student_id': sid, 'name': name or None, 'pin': pin})

    if errors:
        return jsonify({'error': 'Validation failed', 'details': errors}), 400

    # Get existing students
    existing_rows = query_db(slug,
        'SELECT id, student_id FROM students WHERE course_id = ?',
        [course['id']])
    existing_by_sid = {r['student_id']: r['id'] for r in existing_rows}
    csv_sids = {p['student_id'] for p in parsed}

    added, updated, removed = 0, 0, 0
    # Batch delete students not in CSV
    to_remove = [db_id for sid, db_id in existing_by_sid.items() if sid not in csv_sids]
    if to_remove:
        _delete_students(slug, to_remove, bump_roster=False)
        removed = len(to_remove)

    # Batch updates and inserts
    to_update = []
    to_insert = []
    for p in parsed:
        if p['student_id'] in existing_by_sid:
            to_update.append((p['name'] or None, p['pin'], existing_by_sid[p['student_id']]))
        else:
            to_insert.append((course['id'], p['student_id'], p['name'] or None, p['pin']))

    if to_update:
        db = get_db(slug)
        db.executemany('UPDATE students SET name = ?, pin = ? WHERE id = ?', to_update)
        db.commit()
        updated = len(to_update)
    if to_insert:
        db = get_db(slug)
        db.executemany(
            'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
            to_insert)
        db.commit()
        added = len(to_insert)

    if added or updated or removed:
        _bump_roster_version(slug, course['id'])

    return jsonify({'success': True, 'added': added, 'updated': updated, 'removed': removed})


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
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    max_teams = get_max_teams(slug, course['id'])
    visible_teams = query_db(slug,
        'SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT ?',
        [course['id'], max_teams]
    )
    if team_id not in {t['id'] for t in visible_teams}:
        return jsonify({'error': 'Team is not available'}), 400
    # Look up the canonical question text from the DB (the client sends truncated display text)
    q = query_db(slug,
        'SELECT question_text FROM questions WHERE id = ? AND course_id = ?',
        [question_id, course['id']], one=True)
    if not q:
        return jsonify({'error': 'Question not found'}), 404
    question_text = q['question_text']
    started_at = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    execute_db(slug,
        '''UPDATE course_state
           SET phase = 'competition', active_team_id = ?, active_question_id = ?,
               current_question = ?,
               presentation_started_at = ?,
               presentation_time_cap = ?, presentation_remaining = NULL,
               poll_active = 0, poll_question_key = ?,
               poll_started_at = NULL
           WHERE course_id = ?''',
        [team_id, question_id, question_text, started_at, time_cap,
         f'pres-{started_at}', course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/stop_presentation', methods=['POST'])
@instructor_login_required
def stop_presentation():
    """Pause the presentation timer — save remaining time."""
    slug = session['slug']
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    if not state or not state['presentation_started_at']:
        return jsonify({'error': 'No active presentation'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    # Compute elapsed and remaining
    started = _parse_db_datetime(state['presentation_started_at'])
    elapsed = (datetime.utcnow() - started).total_seconds()
    cap = state['presentation_time_cap'] or 300
    remaining = max(0, int(cap - elapsed))
    execute_db(slug,
        '''UPDATE course_state
           SET presentation_started_at = NULL, presentation_remaining = ?
           WHERE course_id = ?''',
        [remaining, course['id']]
    )
    return jsonify({'success': True, 'remaining': remaining})


@app.route('/api/resume_presentation', methods=['POST'])
@instructor_login_required
def resume_presentation():
    """Resume a paused presentation — keep original cap, shift started_at."""
    slug = session['slug']
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    if not state:
        return jsonify({'error': 'No state'}), 400
    remaining = state['presentation_remaining']
    if remaining is None or remaining <= 0:
        return jsonify({'error': 'No remaining time to resume'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    cap = state['presentation_time_cap'] or 300
    # Shift started_at back so that cap - elapsed == remaining
    # elapsed = cap - remaining, so started_at = now - (cap - remaining)
    consumed = cap - remaining
    shifted_start = datetime.utcnow() - timedelta(seconds=consumed)
    execute_db(slug,
        '''UPDATE course_state
           SET presentation_started_at = ?,
               presentation_remaining = NULL
           WHERE course_id = ?''',
        [shifted_start.strftime('%Y-%m-%d %H:%M:%S'), course['id']]
    )
    return jsonify({'success': True, 'remaining': remaining})


@app.route('/api/reset_presentation_timer', methods=['POST'])
@instructor_login_required
def reset_presentation_timer():
    """Reset the timer to the original time cap (paused state)."""
    slug = session['slug']
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    if not state:
        return jsonify({'error': 'No state'}), 400
    if not state['active_team_id']:
        return jsonify({'error': 'No active presentation'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    cap = state['presentation_time_cap'] or 300
    execute_db(slug,
        '''UPDATE course_state
           SET presentation_started_at = NULL,
               presentation_remaining = ?
           WHERE course_id = ?''',
        [cap, course['id']]
    )
    return jsonify({'success': True, 'cap': cap})


@app.route('/api/next_presentation', methods=['POST'])
@instructor_login_required
def next_presentation():
    """Stop current presentation, save to history, clear for next."""
    slug = session['slug']
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    # Save to presentation history
    history = []
    if state and state['presentation_history']:
        try:
            history = json.loads(state['presentation_history'])
        except Exception:
            history = []
    # Build history entry
    if state and state['active_team_id'] and state['active_question_id']:
        team = query_db(slug, 'SELECT name FROM teams WHERE id = ?',
                        [state['active_team_id']], one=True)
        q_text = state['current_question'] or ''
        if len(q_text) > 80:
            q_text = q_text[:77] + '...'
        # Count ratings for this presentation using its stable key. The timer's
        # started_at can shift on resume, so do not derive the key from it here.
        pres_key = active_presentation_key(state)
        count = query_db(slug,
            'SELECT COUNT(DISTINCT student_id) as c FROM presentation_ratings '
            'WHERE course_id = ? AND question_key = ?',
            [course['id'], pres_key], one=True)
        started_at = pres_key[5:] if pres_key and pres_key.startswith('pres-') else ''
        history.append({
            'title': q_text,
            'team': team['name'] if team else 'Unknown',
            'responses': count['c'] if count else 0,
            'started_at': started_at,
            'question_id': state['active_question_id']
        })
        # Keep last 20
        history = history[-20:]
    execute_db(slug,
        '''UPDATE course_state
           SET active_team_id = NULL, active_question_id = NULL,
               current_question = NULL,
               presentation_started_at = NULL, presentation_time_cap = 300,
               presentation_remaining = NULL,
               poll_active = 0, poll_question_key = NULL, poll_started_at = NULL,
               presentation_history = ?
           WHERE course_id = ?''',
        [json.dumps(history), course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/start_poll', methods=['POST'])
@instructor_login_required
def start_poll():
    """Start a 30-second rating-poll highlight for the active presentation."""
    slug = session['slug']
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    if not state:
        return jsonify({'error': 'No course state'}), 400
    if not state['active_team_id'] or not state['active_question_id']:
        return jsonify({'error': 'No active presentation'}), 400
    # Derive the rating key from server state — never trust client input.
    question_key = active_presentation_key(state)
    if not question_key:
        return jsonify({'error': 'No active presentation'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        '''UPDATE course_state
           SET poll_active = 1, poll_question_key = ?, poll_started_at = CURRENT_TIMESTAMP
           WHERE course_id = ?''',
        [question_key, course['id']]
    )
    fresh = query_db(slug, 'SELECT poll_started_at FROM course_state LIMIT 1', one=True)
    return jsonify({
        'success': True,
        'poll_started_at': fresh['poll_started_at'] if fresh else None,
        'poll_duration': POLL_DURATION
    })


@app.route('/api/stop_poll', methods=['POST'])
@instructor_login_required
def stop_poll():
    """Stop the active rating poll highlight."""
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE course_state SET poll_active = 0, poll_started_at = NULL WHERE course_id = ?',
        [course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/submit_rating', methods=['POST'])
@student_login_required
def submit_rating():
    """Student submits star ratings (1–5) for the current presentation."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    q1 = data.get('q1_developed')
    q2 = data.get('q2_easy')
    if q1 is None or q2 is None:
        return jsonify({'error': 'Both ratings required'}), 400
    try:
        q1 = int(q1)
        q2 = int(q2)
    except (ValueError, TypeError):
        return jsonify({'error': 'Ratings must be integers'}), 400
    if not (1 <= q1 <= 5 and 1 <= q2 <= 5):
        return jsonify({'error': 'Ratings must be 1–5'}), 400

    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    # Grading is always open during an active presentation — the poll is just a
    # 30s highlight, not a gate. Key on the active presentation's start time.
    if not state or state['phase'] != 'competition' or \
       not state['active_team_id'] or not state['active_question_id']:
        return jsonify({'error': 'No active presentation to rate'}), 403

    student = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not student:
        return jsonify({'error': 'Student not found'}), 404

    # Block self-grading — the presenting team cannot rate their own presentation.
    if student['team_id'] and state['active_team_id'] and \
       student['team_id'] == state['active_team_id']:
        return jsonify({'error': 'You cannot rate your own presentation'}), 403
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    question_key = active_presentation_key(state)
    if not question_key:
        return jsonify({'error': 'No active presentation to rate'}), 403
    execute_db(slug,
        '''INSERT INTO presentation_ratings
           (course_id, student_id, question_key, q1_developed, q2_easy)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, student_id, question_key)
           DO UPDATE SET q1_developed=excluded.q1_developed,
                         q2_easy=excluded.q2_easy''',
        [course['id'], student['id'], question_key, q1, q2]
    )
    return jsonify({'success': True})


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
        f'SELECT COUNT(*) as c FROM students s WHERE s.course_id = ? {where_clause}',
        params, one=True
    )
    total = count['c']
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page

    rows = query_db(slug,
        f'''SELECT s.id, s.student_id, s.name, s.pin, s.team_id,
                   t.name as team_name, t.color as team_color,
                   s.last_login_at, s.last_active_at, s.last_team_joined_at
            FROM students s LEFT JOIN teams t ON s.team_id = t.id
            WHERE s.course_id = ? {where_clause}
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
    if not re.match(r'^\d{4}$', pin):
        return jsonify({'error': 'PIN must be exactly 4 digits'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    existing = query_db(slug,
        'SELECT id FROM students WHERE course_id = ? AND student_id = ?',
        [course['id'], student_id], one=True)
    if existing:
        execute_db(slug,
            'UPDATE students SET name = ?, pin = ? WHERE id = ?',
            [name or None, pin, existing['id']]
        )
        _bump_roster_version(slug, course['id'])
        return jsonify({'success': True, 'updated': True})
    execute_db(slug,
        'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
        [course['id'], student_id, name or None, pin]
    )
    _bump_roster_version(slug, course['id'])
    return jsonify({'success': True, 'added': True})


@app.route('/api/assign_student', methods=['POST'])
@instructor_login_required
def assign_student():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')  # DB row id
    team_id = data.get('team_id')  # None or '' to unassign
    if not student_id:
        return jsonify({'error': 'Student ID required'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)

    if team_id:
        team = query_db(slug, 'SELECT id FROM teams WHERE id = ?', [team_id], one=True)
        if not team:
            return jsonify({'error': 'Team not found'}), 404

    execute_db(slug,
        '''UPDATE students
           SET team_id = ?, last_team_id = ?, last_team_joined_at = CURRENT_TIMESTAMP
           WHERE id = ?''',
        [team_id or None, team_id or None, student_id]
    )
    _bump_roster_version(slug, course['id'])
    return jsonify({'success': True})


@app.route('/api/remove_student/<int:student_db_id>', methods=['DELETE'])
@instructor_login_required
def remove_student(student_db_id):
    slug = session['slug']
    _delete_student(slug, student_db_id)
    return jsonify({'success': True})


@app.route('/api/reset_data', methods=['POST'])
@instructor_login_required
def reset_data():
    slug = session['slug']
    ensure_schema(slug)
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug, 'DELETE FROM peer_reviews WHERE course_id = ?', [course['id']])
    execute_db(slug, 'DELETE FROM presentation_ratings WHERE course_id = ?', [course['id']])
    db = get_db(slug)
    legacy_tables = {
        row['name'] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('discussion_responses', 'discussion_selections')"
        ).fetchall()
    }
    if 'discussion_responses' in legacy_tables:
        execute_db(slug, 'DELETE FROM discussion_responses WHERE course_id = ?', [course['id']])
    if 'discussion_selections' in legacy_tables:
        execute_db(slug, 'DELETE FROM discussion_selections WHERE course_id = ?', [course['id']])
    execute_db(slug, 'UPDATE students SET team_id = NULL WHERE course_id = ?', [course['id']])
    execute_db(slug,
        '''UPDATE course_state SET
               phase = 'setup',
               active_team_id = NULL,
               active_question_id = NULL,
               current_question = NULL,
               presentation_started_at = NULL,
               presentation_time_cap = 300,
               presentation_remaining = NULL,
               poll_active = 0,
               poll_question_key = NULL,
               presentation_history = '[]',
               teams_locked = 0,
               session_started_at = NULL,
               roster_version = COALESCE(roster_version, 0) + 1
           WHERE course_id = ?''',
        [course['id']]
    )
    return jsonify({'success': True})


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

    try:
        course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
        if not course:
            flash('Course not found.', 'error')
            return redirect(url_for('index'))

        cid = course['id']

        # ── gather all data ──

        students = query_db(slug,
            '''SELECT s.*, t.name as team_name
               FROM students s LEFT JOIN teams t ON s.team_id = t.id
               WHERE s.course_id = ? ORDER BY s.name''', [cid])

        teams = query_db(slug,
            '''SELECT t.*,
                    (SELECT COUNT(*) FROM students s WHERE s.team_id = t.id) as member_count
               FROM teams t WHERE t.course_id = ? ORDER BY t.id''', [cid])

        peer_reviews = query_db(slug,
            '''SELECT p.grader_id, p.recipient_id,
                      g.student_id as grader_sid, g.name as grader_name,
                      r.student_id as recipient_sid, r.name as recipient_name,
                      g.team_id as grader_team_id,
                      p.criterion, p.score, p.created_at
               FROM peer_reviews p
               JOIN students g ON p.grader_id = g.id
               JOIN students r ON p.recipient_id = r.id
               WHERE p.course_id = ? AND p.score > 0
               ORDER BY p.created_at''', [cid])

        ratings = query_db(slug,
            '''SELECT pr.question_key, pr.student_id as rater_db_id,
                      s.student_id as rater_sid, s.name as rater_name,
                      s.team_id as rater_team_id,
                      pr.q1_developed, pr.q2_easy, pr.created_at
               FROM presentation_ratings pr
               JOIN students s ON pr.student_id = s.id
               WHERE pr.course_id = ?
               ORDER BY pr.question_key, pr.created_at''', [cid])

        # Map question_key → presenting team from presentation_history
        state_row = query_db(slug, 'SELECT * FROM course_state WHERE course_id = ?', [cid], one=True)
        key_to_team = {}
        if state_row and state_row['presentation_history']:
            for h in json.loads(state_row['presentation_history']):
                qkey = f"pres-{h.get('started_at', '')}"
                key_to_team[qkey] = h.get('team', 'Unknown')

        # Team id → name lookup
        team_id_to_name = {t['id']: t['name'] for t in teams}

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
            ('Total Students', len(students)),
            ('Total Teams', len(teams)),
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
        for rt in ratings:
            tname = key_to_team.get(rt['question_key'], 'Unknown')
            if rt['q1_developed'] is not None:
                team_scores[tname]['dev'].append(rt['q1_developed'])
            if rt['q2_easy'] is not None:
                team_scores[tname]['easy'].append(rt['q2_easy'])

        def combined_avg(sc):
            all_vals = sc['dev'] + sc['easy']
            return round(sum(all_vals) / len(all_vals), 2) if all_vals else 0

        team_sorted = sorted(team_scores.items(), key=lambda x: combined_avg(x[1]), reverse=True)
        for rank, (tname, sc) in enumerate(team_sorted, 1):
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
            'student_id', 'name', 'team',
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
        t_headers = ['team_id', 'team_name', 'member_count', 'presentations',
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
                t['id'], tname, t['member_count'], pres_cnt,
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
        pr_headers = ['grader_id', 'grader_name', 'recipient_id', 'recipient_name',
                      'criterion', 'score', 'time']
        style_header(ws4, pr_headers)

        pr_rows = []
        for pr in peer_reviews:
            pr_rows.append([
                pr['grader_sid'], pr['grader_name'],
                pr['recipient_sid'], pr['recipient_name'],
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
        rt_headers = ['question_key', 'presenting_team', 'rater_id', 'rater_name',
                      'rater_team', 'developed_1to5', 'easy_1to5', 'time']
        style_header(ws5, rt_headers)

        rt_rows = []
        for rt in ratings:
            rt_rows.append([
                rt['question_key'],
                key_to_team.get(rt['question_key'], 'Unknown'),
                rt['rater_sid'], rt['rater_name'],
                team_id_to_name.get(rt['rater_team_id'], '') if rt['rater_team_id'] else '',
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

        # Build a ZIP containing the Excel file + all question content files
        import zipfile
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Excel interaction data
            zf.writestr('course_data.xlsx', xlsx_buf.getvalue())

            # Original question files from classes/<slug>/
            class_dir = os.path.join(config.CLASSES_DIR, slug)
            if os.path.isdir(class_dir):
                for root, dirs, files in os.walk(class_dir):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        relpath = os.path.relpath(fpath, class_dir)
                        # Skip course config — only bundle question content
                        if fname in ('course.yaml', 'course.json'):
                            continue
                        try:
                            with open(fpath, 'rb') as f:
                                zf.writestr(f'questions/{relpath}', f.read())
                        except (IOError, OSError):
                            pass

            # Appendix questions from the persistent data disk
            appendix_dir = os.path.join(config.DATA_DIR, slug, 'appendix')
            if os.path.isdir(appendix_dir):
                for fname in os.listdir(appendix_dir):
                    fpath = os.path.join(appendix_dir, fname)
                    try:
                        with open(fpath, 'rb') as f:
                            zf.writestr(f'appendix/{fname}', f.read())
                    except (IOError, OSError):
                        pass

        zip_buf.seek(0)
        filename = f"popping_{course['code'] or slug}_export.zip"
        return (
            zip_buf.getvalue(),
            200,
            {
                'Content-Type': 'application/zip',
                'Content-Disposition': f'attachment; filename={filename}'
            }
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f'Export failed: {e}', 'error')
        return redirect(url_for('instructor_course', slug=slug))
