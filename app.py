import os
import sys
import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash, g
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


@app.before_request
def track_student_activity():
    if 'student_id' in session and 'slug' in session:
        try:
            execute_db(session['slug'],
                "UPDATE students SET last_active_at = CURRENT_TIMESTAMP WHERE student_id = ?",
                [session['student_id']]
            )
        except Exception:
            pass  # db might not exist yet on first request


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
                conn.row_factory = sqlite3.Row
                instructor = conn.execute('SELECT name FROM instructors LIMIT 1').fetchone()
                if instructor:
                    instructor_name = instructor['name']
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
        except Exception:
            pass
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
    db_path = os.path.join(config.DATA_DIR, slug, 'popping.db')
    if not os.path.exists(db_path):
        flash('Course not found.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        pin = request.form.get('pin', '').strip()
        display_name = request.form.get('name', '').strip()
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
            # Update display name if provided on login
            if display_name:
                execute_db(slug,
                    'UPDATE students SET name = ? WHERE id = ?',
                    [display_name, student['id']]
                )
                student_name = display_name
            else:
                student_name = student['name'] or student['student_id']
            session['student_id'] = student['student_id']
            session['name'] = student_name
            session['slug'] = slug
            return redirect(url_for('dashboard'))
        flash('Invalid ID or PIN for this course.', 'error')

    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)
    return render_template('login.html', course=course, slug=slug)


@app.route('/instructor_login/<slug>', methods=['GET', 'POST'])
def instructor_login(slug):
    db_path = os.path.join(config.DATA_DIR, slug, 'popping.db')
    if not os.path.exists(db_path):
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
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY id', [course['id']]
    )
    state = query_db(slug,
        'SELECT * FROM course_state WHERE course_id = ?', [course['id']], one=True
    )
    max_teams = get_max_teams(slug, course['id'])
    teams_locked = state['teams_locked'] if state and 'teams_locked' in state.keys() else 0
    students = query_db(slug,
        '''SELECT s.*, t.name as team_name, t.color as team_color
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           WHERE s.course_id = ? ORDER BY s.name''',
        [course['id']]
    )
    questions = query_db(slug,
        'SELECT * FROM questions WHERE course_id = ? ORDER BY question_num', [course['id']]
    )
    from datetime import datetime as dt, timedelta
    cutoff = (dt.utcnow() - timedelta(minutes=3)).isoformat()
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
        # Top teams by avg presentation rating
        # Map question_key -> presenting team via presentation_history
        # (presentation_ratings.student_id is the RATER, not the presenter)
        import collections
        history_json = json.loads(state['presentation_history'] or '[]') if state else []
        key_to_team = {}
        for h in history_json:
            qkey = f"pres-{h.get('started_at', '')}"
            key_to_team[qkey] = h.get('team', 'Unknown')
        all_ratings = query_db(slug,
            '''SELECT question_key, q1_developed, q2_easy
               FROM presentation_ratings WHERE course_id = ?''',
            [course['id']])
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
        end_stats = {
            'participants': participants['c'] if participants else 0,
            'top_students': top_students,
            'top_teams': [{'name': r['name'], 'avg_score': r['avg_score']}
                          for r in team_ratings]
        }

    return render_template(
        'instructor.html',
        course=course, teams=teams, students=students_enhanced,
        state=state, phases=PHASES, questions=questions,
        max_teams=max_teams,
        max_members=get_max_members_per_team(slug, course['id']),
        teams_locked=teams_locked,
        session_started_at=state['session_started_at'] if state and 'session_started_at' in state.keys() else None,
        end_stats=end_stats
    )


# ---------------------------------------------------------------------------
# Student API
# ---------------------------------------------------------------------------

def _get_slug_from_session():
    if 'slug' in session:
        return session['slug']
    return None


@app.route('/api/teams', methods=['GET'])
def api_teams():
    slug = _get_slug_from_session()
    if not slug or ('student_id' not in session and 'instructor_id' not in session):
        return jsonify({'error': 'Not logged in'}), 401
    course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
    max_teams = get_max_teams(slug, course['id'])
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY id LIMIT ?', [course['id'], max_teams]
    )
    result = []
    for t in teams:
        members = query_db(slug,
            'SELECT student_id, name FROM students WHERE team_id = ?', [t['id']]
        )
        result.append({
            'id': t['id'],
            'name': t['name'],
            'color': t['color'],
            'members': [dict(m) for m in members]
        })
    return jsonify(result)


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
    state = query_db(slug, 'SELECT phase, teams_locked FROM course_state LIMIT 1', one=True)
    if state['phase'] != 'setup':
        return jsonify({'error': 'Team selection is closed'}), 403
    if state['teams_locked']:
        return jsonify({'error': 'Teams are currently locked by the instructor'}), 403

    # Leaving team (team_id = 0 means unassign)
    if not team_id:
        execute_db(slug, 'UPDATE students SET team_id = NULL WHERE id = ?', [student['id']])
        return jsonify({'success': True})

    # Check team capacity
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
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
    return jsonify({'success': True})


@app.route('/api/state', methods=['GET'])
def api_state():
    """Course state — accessible to both students and instructors."""
    slug = session.get('slug')
    if not slug:
        return jsonify({'error': 'Not logged in'}), 401
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    active_team = None
    if state and state['active_team_id']:
        active_team = query_db(slug,
            'SELECT * FROM teams WHERE id = ?', [state['active_team_id']], one=True
        )
    my_team = None
    if 'student_id' in session:
        me = query_db(slug,
            'SELECT * FROM students WHERE student_id = ?',
            [session['student_id']], one=True
        )
        if me and me['team_id']:
            my_team = query_db(slug,
                'SELECT * FROM teams WHERE id = ?', [me['team_id']], one=True
            )
    active_question = None
    if state and state['active_question_id']:
        aq = query_db(slug,
            'SELECT * FROM questions WHERE id = ?', [state['active_question_id']], one=True
        )
        if aq:
            active_question = dict(aq)
    # Compute presentation remaining seconds
    presentation_remaining = state['presentation_remaining'] if state else None
    if state and state['presentation_started_at'] and state['presentation_time_cap']:
        try:
            started = datetime.fromisoformat(
                state['presentation_started_at'].replace(' ', 'T')
            )
            elapsed = (datetime.utcnow() - started).total_seconds()
            cap = state['presentation_time_cap'] or 300
            presentation_remaining = max(0, int(cap - elapsed))
        except Exception:
            pass
    # Poll count
    poll_count = 0
    if state and state['poll_active'] and state['poll_question_key']:
        course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
        cnt = query_db(slug,
            'SELECT COUNT(DISTINCT student_id) as c FROM presentation_ratings '
            'WHERE course_id = ? AND question_key = ?',
            [course['id'], state['poll_question_key']], one=True)
        poll_count = cnt['c'] if cnt else 0
    return jsonify({
        'phase': state['phase'] if state else 'setup',
        'active_team': dict(active_team) if active_team else None,
        'active_question': active_question,
        'my_team': dict(my_team) if my_team else None,
        'current_question': state['current_question'] if state else None,
        'presentation_started_at': state['presentation_started_at'] if state else None,
        'presentation_time_cap': state['presentation_time_cap'] if state else 300,
        'presentation_remaining': presentation_remaining,
        'teams_locked': bool(state['teams_locked']) if state else False,
        'poll_active': bool(state['poll_active']) if state else False,
        'poll_question_key': state['poll_question_key'] if state else None,
        'poll_count': poll_count,
        'presentation_history': json.loads(state['presentation_history']) if state and state['presentation_history'] else []
    })


@app.route('/api/grade_peer', methods=['POST'])
@student_login_required
def grade_peer():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    recipient_sid = data.get('recipient_id')
    criterion = data.get('criterion')
    score = data.get('score')
    if recipient_sid is None or criterion is None or score is None:
        return jsonify({'error': 'Missing fields'}), 400
    try:
        score = float(score)
    except ValueError:
        return jsonify({'error': 'Invalid score'}), 400
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
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        '''INSERT INTO peer_reviews (course_id, grader_id, recipient_id, criterion, score)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, grader_id, recipient_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [course['id'], grader['id'], recipient['id'], criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/submit_discussion', methods=['POST'])
@student_login_required
def submit_discussion():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    question = data.get('question', '')
    response = data.get('response', '')
    if not question or not response:
        return jsonify({'error': 'Question and response required'}), 400
    student = query_db(slug,
        'SELECT id FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'INSERT INTO discussion_responses (course_id, student_id, question, response) VALUES (?, ?, ?, ?)',
        [course['id'], student['id'], question, response]
    )
    return jsonify({'success': True})


@app.route('/api/discussion_responses')
@student_login_required
def api_discussion_responses():
    slug = session['slug']
    rows = query_db(slug,
        '''SELECT d.*, s.name, s.student_id, t.name as team_name
           FROM discussion_responses d
           JOIN students s ON d.student_id = s.id
           LEFT JOIN teams t ON s.team_id = t.id
           ORDER BY d.created_at DESC'''
    )
    return jsonify([dict(r) for r in rows])


@app.route('/api/my_grades')
@student_login_required
def my_grades():
    slug = session['slug']
    student = query_db(slug,
        'SELECT id, team_id FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    peer = query_db(slug,
        '''SELECT criterion, AVG(score) as avg_score, COUNT(*) as count
           FROM peer_reviews WHERE recipient_id = ? GROUP BY criterion''',
        [student['id']]
    )
    team = query_db(slug,
        '''SELECT criterion, AVG(score) as avg_score, COUNT(*) as count
           FROM team_reviews WHERE recipient_team_id = ? GROUP BY criterion''',
        [student['team_id']]
    ) if student['team_id'] else []
    return jsonify({
        'peer': [dict(r) for r in peer],
        'team': [dict(r) for r in team]
    })


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
    execute_db(slug,
        'UPDATE course_state SET phase = ? WHERE course_id = ?',
        [phase, course['id']]
    )
    return jsonify({'success': True, 'phase': phase})


@app.route('/api/set_active_team', methods=['POST'])
@instructor_login_required
def set_active_team():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    team_id = data.get('team_id')
    if team_id is not None:
        team = query_db(slug, 'SELECT id FROM teams WHERE id = ?', [team_id], one=True)
        if not team:
            return jsonify({'error': 'Team not found'}), 404
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE course_state SET active_team_id = ? WHERE course_id = ?',
        [team_id, course['id']]
    )
    return jsonify({'success': True})


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
        'UPDATE course_state SET max_teams = ? WHERE course_id = ?',
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

    # Count current members per team
    counts = {}
    for tid in team_ids:
        c = query_db(slug, 'SELECT COUNT(*) as c FROM students WHERE team_id = ?', [tid], one=True)
        counts[tid] = c['c']

    # Assign each student to the smallest team (fill evenly)
    assigned = 0
    for sid in student_ids:
        # Find teams with the fewest members
        min_count = min(counts.values())
        candidates = [tid for tid in team_ids if counts[tid] == min_count and counts[tid] < max_members]
        if not candidates:
            # All teams at max, find any team below max
            candidates = [tid for tid in team_ids if counts[tid] < max_members]
        if not candidates:
            break
        tid = rnd.choice(candidates)
        execute_db(slug,
            'UPDATE students SET team_id = ?, last_team_id = ?, last_team_joined_at = CURRENT_TIMESTAMP WHERE id = ?',
            [tid, tid, sid]
        )
        counts[tid] += 1
        assigned += 1

    return jsonify({'success': True, 'assigned': assigned})


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
    return jsonify({'success': True})


@app.route('/api/toggle_lock_teams', methods=['POST'])
@instructor_login_required
def toggle_lock_teams():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    locked = 1 if data.get('locked') else 0
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    if locked:
        execute_db(slug,
            'UPDATE course_state SET teams_locked = 1 WHERE course_id = ?',
            [course['id']]
        )
    else:
        execute_db(slug,
            'UPDATE course_state SET teams_locked = 0 WHERE course_id = ?',
            [course['id']]
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
    if new_max < current_max:
        # Find teams with more than new_max members
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
            for s in excess:
                execute_db(slug, 'UPDATE students SET team_id = NULL WHERE id = ?', [s['id']])

    execute_db(slug,
        'UPDATE course_state SET max_members_per_team = ? WHERE course_id = ?',
        [new_max, course['id']]
    )
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
    import re

    class_dir = os.path.join(config.CLASSES_DIR, slug)
    question_files = sorted(
        [os.path.basename(f) for f in glob_mod.glob(os.path.join(class_dir, 'week-*-questions.md'))]
    )

    week_param = request.args.get('week')
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    ensure_schema(slug)
    state = query_db(slug, 'SELECT discussion_week FROM course_state WHERE course_id = ?', [course['id']], one=True)
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
            content = content.lstrip('-').lstrip()
            blocks = content.split('\n---\n')
            i = 0
            while i + 1 < len(blocks):
                fm_block = blocks[i].strip()
                body_block = blocks[i + 1].strip()
                if fm_block:
                    try:
                        fm = yaml.safe_load(fm_block)
                        if fm and fm.get('title'):
                            key = f"{prefix}{i//2}"
                            out.append({
                                'key': key,
                                'title': fm.get('title', 'Untitled'),
                                'content': body_block
                            })
                    except Exception:
                        pass
                i += 2
        except Exception:
            pass
        return out

    if target:
        q_path = os.path.join(class_dir, target['file'])
        questions_list = _load_md(q_path, prefix=f"week-{target['num']}-q")
        # Also load appendix
        appendix_path = os.path.join(class_dir, f"week-{target['num']}-appendix.md")
        appendix = _load_md(appendix_path, prefix=f"week-{target['num']}-a")
        questions_list.extend(appendix)
    else:
        # No week files — still try to load appendix for saved_week
        appendix_path = os.path.join(class_dir, f"week-{saved_week}-appendix.md")
        appendix = _load_md(appendix_path, prefix=f"week-{saved_week}-a")
        questions_list.extend(appendix)

    # Add sign-up metadata
    is_instructor = 'instructor_id' in session
    my_student = None
    my_team_id = None
    if 'student_id' in session:
        my_student = query_db(slug,
            'SELECT id, team_id FROM students WHERE student_id = ?',
            [session['student_id']], one=True)
        if my_student:
            my_team_id = my_student['team_id']

    for q in questions_list:
        # Count distinct teams with sign-ups
        teams_presenting = query_db(slug,
            '''SELECT COUNT(DISTINCT s.team_id) as c
               FROM discussion_selections ds
               JOIN students s ON ds.student_id = s.id
               WHERE ds.course_id = ? AND ds.question_key = ?''',
            [course['id'], q['key']], one=True)
        q['teams_presenting'] = teams_presenting['c'] if teams_presenting else 0

        # Count presenters (global for instructor, team-scoped for student)
        if is_instructor:
            presenters = query_db(slug,
                'SELECT COUNT(*) as c FROM discussion_selections '
                'WHERE course_id = ? AND question_key = ?',
                [course['id'], q['key']], one=True)
            q['presenters'] = presenters['c'] if presenters else 0
        elif my_team_id:
            presenters = query_db(slug,
                '''SELECT COUNT(*) as c FROM discussion_selections ds
                   JOIN students s ON ds.student_id = s.id
                   WHERE ds.course_id = ? AND ds.question_key = ? AND s.team_id = ?''',
                [course['id'], q['key'], my_team_id], one=True)
            q['presenters'] = presenters['c'] if presenters else 0
        else:
            q['presenters'] = 0

        # Whether current student has selected this question
        q['i_selected'] = False
        if my_student:
            sel = query_db(slug,
                'SELECT 1 FROM discussion_selections '
                'WHERE course_id = ? AND student_id = ? AND question_key = ?',
                [course['id'], my_student['id'], q['key']], one=True)
            q['i_selected'] = bool(sel)

    return jsonify({
        'weeks': weeks,
        'current_week': target['num'] if target else None,
        'questions': questions_list
    })


@app.route('/api/questions', methods=['GET'])
@instructor_login_required
def get_questions():
    slug = session['slug']
    rows = query_db(slug,
        'SELECT * FROM questions WHERE course_id = (SELECT id FROM courses LIMIT 1) ORDER BY question_num'
    )
    return jsonify([dict(r) for r in rows])


@app.route('/api/questions', methods=['POST'])
@instructor_login_required
def add_question():
    """Add an appendix question — writes to week-N-appendix.md file."""
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

    class_dir = os.path.join(config.CLASSES_DIR, slug)
    os.makedirs(class_dir, exist_ok=True)
    appendix_path = os.path.join(class_dir, f'week-{week}-appendix.md')

    # Count existing appendix entries to auto-label
    existing = 0
    if os.path.exists(appendix_path):
        with open(appendix_path, 'r') as f:
            existing = f.read().count('\n---\n')
    label = f'A{existing + 1}'

    block = f"""---
title: "{label}: {title}"
---

{content}
"""
    with open(appendix_path, 'a', encoding='utf-8') as f:
        f.write(block)
    return jsonify({'success': True})


@app.route('/api/delete_appendix_question', methods=['POST'])
@instructor_login_required
def delete_appendix_question():
    """Delete an appendix question by index (0-based)."""
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
    class_dir = os.path.join(config.CLASSES_DIR, slug)
    os.makedirs(class_dir, exist_ok=True)
    appendix_path = os.path.join(class_dir, f'week-{week}-appendix.md')
    if not os.path.exists(appendix_path):
        return jsonify({'error': 'Appendix file not found'}), 404

    with open(appendix_path, 'r', encoding='utf-8') as f:
        content = f.read()

    import yaml
    content = content.lstrip('-').lstrip()
    blocks = content.split('\n---\n')
    entries = []
    i = 0
    while i + 1 < len(blocks):
        entries.append((blocks[i].strip(), blocks[i + 1].strip()))
        i += 2

    if index < 0 or index >= len(entries):
        return jsonify({'error': 'Index out of range'}), 400

    del entries[index]

    if not entries:
        os.remove(appendix_path)
        return jsonify({'success': True})

    # Rebuild file
    new_content = ''
    for fm, body in entries:
        new_content += f"---\n{fm}\n---\n\n{body}\n\n"
    with open(appendix_path, 'w', encoding='utf-8') as f:
        f.write(new_content.strip() + '\n')
    return jsonify({'success': True})


@app.route('/api/toggle_present', methods=['POST'])
@student_login_required
def toggle_present():
    """Toggle student sign-up for a discussion question."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    question_key = data.get('question_key', '')
    if not question_key:
        return jsonify({'error': 'question_key required'}), 400

    student = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)

    # Check if already selected
    existing = query_db(slug,
        'SELECT id FROM discussion_selections '
        'WHERE course_id = ? AND student_id = ? AND question_key = ?',
        [course['id'], student['id'], question_key], one=True)

    if existing:
        execute_db(slug,
            'DELETE FROM discussion_selections WHERE id = ?',
            [existing['id']])
        return jsonify({'success': True, 'selected': False})
    else:
        execute_db(slug,
            'INSERT INTO discussion_selections (course_id, student_id, question_key) VALUES (?, ?, ?)',
            [course['id'], student['id'], question_key])
        return jsonify({'success': True, 'selected': True})


@app.route('/api/unassign_all', methods=['POST'])
@instructor_login_required
def unassign_all():
    """Clear all team assignments."""
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE students SET team_id = NULL WHERE course_id = ?',
        [course['id']])
    count = query_db(slug,
        'SELECT COUNT(*) as c FROM students WHERE course_id = ?',
        [course['id']], one=True)
    return jsonify({'success': True, 'count': count['c'] if count else 0})


@app.route('/api/questions/<int:qid>', methods=['DELETE'])
@instructor_login_required
def delete_question(qid):
    slug = session['slug']
    execute_db(slug, 'DELETE FROM questions WHERE id = ?', [qid])
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# Competition / Presentation Control API
# ---------------------------------------------------------------------------

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
    # Remove students not in CSV
    for sid, db_id in existing_by_sid.items():
        if sid not in csv_sids:
            execute_db(slug, 'DELETE FROM students WHERE id = ?', [db_id])
            removed += 1

    # Add/update
    for p in parsed:
        if p['student_id'] in existing_by_sid:
            execute_db(slug,
                'UPDATE students SET name = ?, pin = ? WHERE id = ?',
                [p['name'], p['pin'], existing_by_sid[p['student_id']]])
            updated += 1
        else:
            execute_db(slug,
                'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
                [course['id'], p['student_id'], p['name'], p['pin']])
            added += 1

    return jsonify({'success': True, 'added': added, 'updated': updated, 'removed': removed})


@app.route('/api/start_presentation', methods=['POST'])
@instructor_login_required
def start_presentation():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    team_id = data.get('team_id')
    question_id = data.get('question_id')
    time_cap = data.get('time_cap', 300)
    question_text = data.get('question_text', '')
    if not team_id or not question_id:
        return jsonify({'error': 'Team and question required'}), 400
    if not isinstance(time_cap, int) or time_cap < 10 or time_cap > 3600:
        time_cap = 300
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        '''UPDATE course_state
           SET phase = 'competition', active_team_id = ?, active_question_id = ?,
               current_question = ?,
               presentation_started_at = CURRENT_TIMESTAMP,
               presentation_time_cap = ?, presentation_remaining = NULL,
               poll_active = 0, poll_question_key = NULL
           WHERE course_id = ?''',
        [team_id, question_id, question_text, time_cap, course['id']]
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
    from datetime import datetime as dt
    started = dt.fromisoformat(state['presentation_started_at'].replace(' ', 'T'))
    elapsed = (dt.utcnow() - started).total_seconds()
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
    from datetime import timedelta
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
            import json
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
        # Count ratings for this presentation
        count = query_db(slug,
            'SELECT COUNT(DISTINCT student_id) as c FROM presentation_ratings '
            'WHERE course_id = ? AND question_key = ?',
            [course['id'], state['poll_question_key'] or ''], one=True)
        history.append({
            'title': q_text,
            'team': team['name'] if team else 'Unknown',
            'responses': count['c'] if count else 0,
            'started_at': state['presentation_started_at'] or ''
        })
        # Keep last 20
        history = history[-20:]
    execute_db(slug,
        '''UPDATE course_state
           SET active_team_id = NULL, active_question_id = NULL,
               current_question = NULL,
               presentation_started_at = NULL, presentation_time_cap = 300,
               presentation_remaining = NULL,
               poll_active = 0, poll_question_key = NULL,
               presentation_history = ?
           WHERE course_id = ?''',
        [json.dumps(history), course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/start_poll', methods=['POST'])
@instructor_login_required
def start_poll():
    """Start a rating poll for the active presentation."""
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    question_key = data.get('question_key', '')
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    if not question_key:
        # Auto-generate from active presentation
        state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
        if state and state['presentation_started_at']:
            question_key = f"pres-{state['presentation_started_at']}"
        else:
            question_key = f"poll-{datetime.now().isoformat()}"
    execute_db(slug,
        '''UPDATE course_state
           SET poll_active = 1, poll_question_key = ?
           WHERE course_id = ?''',
        [question_key, course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/stop_poll', methods=['POST'])
@instructor_login_required
def stop_poll():
    """Stop the active rating poll."""
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE course_state SET poll_active = 0 WHERE course_id = ?',
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
    if not state or not state['poll_active']:
        return jsonify({'error': 'No active poll'}), 403

    student = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    question_key = state['poll_question_key'] or ''
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
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    sort_col = request.args.get('sort', 'student_id')
    order = request.args.get('order', 'asc')
    search = request.args.get('search', '').strip()
    team_filter = request.args.get('team', '')

    allowed_sorts = {'student_id': 's.student_id', 'name': 's.name', 'last_active_at': 's.last_active_at'}
    if sort_col not in allowed_sorts:
        sort_col = 'student_id'
    sort_sql = allowed_sorts[sort_col]
    order_sql = 'DESC' if order == 'desc' else 'ASC'

    from datetime import datetime as dt, timedelta
    cutoff = (dt.utcnow() - timedelta(minutes=3)).isoformat()

    where_clause = ''
    params = [course['id']]
    if search:
        where_clause += ' AND s.student_id LIKE ?'
        params.append(f'%{search}%')
    if team_filter == 'none':
        where_clause += ' AND s.team_id IS NULL'
    elif team_filter:
        where_clause += ' AND s.team_id = ?'
        params.append(int(team_filter))

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
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    try:
        execute_db(slug,
            'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
            [course['id'], student_id, name or None, pin]
        )
    except Exception:
        return jsonify({'error': 'Student ID already exists in this course'}), 400
    return jsonify({'success': True})


@app.route('/api/assign_student', methods=['POST'])
@instructor_login_required
def assign_student():
    slug = session['slug']
    data = request.get_json(silent=True) or {}
    student_id = data.get('student_id')  # DB row id
    team_id = data.get('team_id')  # None or '' to unassign
    if not student_id:
        return jsonify({'error': 'Student ID required'}), 400

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
    return jsonify({'success': True})


@app.route('/api/remove_student/<int:student_db_id>', methods=['DELETE'])
@instructor_login_required
def remove_student(student_db_id):
    slug = session['slug']
    execute_db(slug, 'DELETE FROM students WHERE id = ?', [student_db_id])
    return jsonify({'success': True})


@app.route('/api/reset_data', methods=['POST'])
@instructor_login_required
def reset_data():
    slug = session['slug']
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug, 'DELETE FROM peer_reviews WHERE course_id = ?', [course['id']])
    execute_db(slug, 'DELETE FROM team_reviews WHERE course_id = ?', [course['id']])
    execute_db(slug, 'DELETE FROM discussion_responses WHERE course_id = ?', [course['id']])
    execute_db(slug, 'DELETE FROM presentation_ratings WHERE course_id = ?', [course['id']])
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
               session_started_at = NULL
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

    course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()

    # Header style
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='13294B', end_color='13294B', fill_type='solid')
    header_align = Alignment(horizontal='center')

    def style_header(ws, headers):
        for col, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=h)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_align

    # Sheet 1: Students
    ws1 = wb.active
    ws1.title = 'Students'
    headers1 = ['student_id', 'name', 'PIN', 'current_team', 'joined_team_at', 'last_login']
    style_header(ws1, headers1)
    for i, row in enumerate(query_db(slug,
        '''SELECT s.student_id, s.name, s.pin, t.name as current_team,
                  s.last_team_joined_at, s.last_login_at
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           ORDER BY s.name'''), start=2):
        ws1.cell(row=i, column=1, value=row['student_id'])
        ws1.cell(row=i, column=2, value=row['name'])
        ws1.cell(row=i, column=3, value=row['pin'])
        ws1.cell(row=i, column=4, value=row['current_team'])
        ws1.cell(row=i, column=5, value=row['last_team_joined_at'])
        ws1.cell(row=i, column=6, value=row['last_login_at'])
    for col in range(1, 7):
        ws1.column_dimensions[chr(64 + col)].auto_size = True

    # Sheet 2: Group Discussion (thumbs-up only)
    ws2 = wb.create_sheet('Group Discussion')
    headers2 = ['grader', 'recipient', 'time']
    style_header(ws2, headers2)
    for i, row in enumerate(query_db(slug,
        '''SELECT g.name as grader, r.name as recipient, p.created_at
           FROM peer_reviews p
           JOIN students g ON p.grader_id = g.id
           JOIN students r ON p.recipient_id = r.id
           WHERE p.score > 0
           ORDER BY p.created_at'''), start=2):
        ws2.cell(row=i, column=1, value=row['grader'])
        ws2.cell(row=i, column=2, value=row['recipient'])
        ws2.cell(row=i, column=3, value=row['created_at'])

    # Per-question sheets for presentation ratings
    q_keys = query_db(slug,
        'SELECT DISTINCT question_key FROM presentation_ratings WHERE course_id = ? ORDER BY question_key',
        [course['id']])
    for qk in q_keys:
        key = qk['question_key'] or 'unknown'
        # Excel sheet names cannot contain: \ / ? * [ ] :
        safe_key = re.sub(r'[\\/?*\[\]:]', '-', key)[:31] or 'unknown'
        ws = wb.create_sheet(safe_key)
        headers_q = ['student', 'developed', 'easy', 'time']
        style_header(ws, headers_q)
        for i, row in enumerate(query_db(slug,
            '''SELECT s.name, pr.q1_developed, pr.q2_easy, pr.created_at
               FROM presentation_ratings pr
               JOIN students s ON pr.student_id = s.id
               WHERE pr.course_id = ? AND pr.question_key = ?
               ORDER BY pr.created_at''',
            [course['id'], key]), start=2):
            ws.cell(row=i, column=1, value=row['name'])
            ws.cell(row=i, column=2, value=row['q1_developed'])
            ws.cell(row=i, column=3, value=row['q2_easy'])
            ws.cell(row=i, column=4, value=row['created_at'])

    # Summary sheet
    ws_sum = wb.create_sheet('Summary')
    ws_sum.cell(row=1, column=1, value='Course').font = Font(bold=True)
    ws_sum.cell(row=1, column=2, value=course['name'])
    ws_sum.cell(row=2, column=1, value='Code').font = Font(bold=True)
    ws_sum.cell(row=2, column=2, value=course['code'] or '')
    ws_sum.cell(row=4, column=1, value='Student Count').font = Font(bold=True)
    cnt = query_db(slug, 'SELECT COUNT(*) as c FROM students WHERE course_id = ?',
                   [course['id']], one=True)
    ws_sum.cell(row=4, column=2, value=cnt['c'] if cnt else 0)
    ws_sum.cell(row=5, column=1, value='Team Count').font = Font(bold=True)
    tcnt = query_db(slug, 'SELECT COUNT(*) as c FROM teams WHERE course_id = ?',
                    [course['id']], one=True)
    ws_sum.cell(row=5, column=2, value=tcnt['c'] if tcnt else 0)

    # Save to bytes
    from io import BytesIO
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"popping_{course['code'] or slug}_export.xlsx"
    return (
        buf.getvalue(),
        200,
        {
            'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'Content-Disposition': f'attachment; filename={filename}'
        }
    )
