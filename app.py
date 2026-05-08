import os
import csv
import io
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash, g
)

import config
from database import init_app, query_db, execute_db, get_db, init_db

app = Flask(__name__)
app.config.from_object(config)
init_app(app)

PHASES = ['setup', 'discussion', 'competition', 'grading', 'ended']


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
    """Scan CONFIG_DIR for course configs and DATA_DIR for databases."""
    courses = []
    if not os.path.isdir(config.CONFIG_DIR):
        return courses
    for slug in sorted(os.listdir(config.CONFIG_DIR)):
        config_dir = os.path.join(config.CONFIG_DIR, slug)
        db_path = os.path.join(config.DATA_DIR, slug, 'popping.db')
        json_path = os.path.join(config_dir, 'course.json')
        if not os.path.isdir(config_dir) or not os.path.exists(json_path):
            continue
        try:
            import json
            with open(json_path) as f:
                cfg = json.load(f)
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
        if not student_id or not pin:
            flash('Please enter both ID and PIN.', 'error')
            return render_template('login.html', slug=slug)
        student = query_db(slug,
            'SELECT * FROM students WHERE student_id = ? AND pin = ?',
            [student_id, pin], one=True
        )
        if student:
            session['student_id'] = student['student_id']
            session['name'] = student['name']
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


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@student_login_required
def dashboard():
    slug = session['slug']
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
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY name', [course['id']]
    )
    state = query_db(slug,
        'SELECT * FROM course_state WHERE course_id = ?', [course['id']], one=True
    )
    return render_template(
        'dashboard.html',
        student=student, team=team, teams=teams,
        state=state, course=course, phases=PHASES
    )


@app.route('/instructor/<slug>')
@instructor_login_required
def instructor_course(slug):
    if session.get('slug') != slug:
        flash('Unauthorized.', 'error')
        return redirect(url_for('index'))

    course = query_db(slug, 'SELECT * FROM courses WHERE slug = ?', [slug], one=True)
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY name', [course['id']]
    )
    students = query_db(slug,
        '''SELECT s.*, t.name as team_name, t.color as team_color
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           WHERE s.course_id = ? ORDER BY s.name''',
        [course['id']]
    )
    state = query_db(slug,
        'SELECT * FROM course_state WHERE course_id = ?', [course['id']], one=True
    )
    return render_template(
        'instructor.html',
        course=course, teams=teams, students=students,
        state=state, phases=PHASES
    )


# ---------------------------------------------------------------------------
# Student API
# ---------------------------------------------------------------------------

@app.route('/api/teams', methods=['GET'])
@student_login_required
def api_teams():
    slug = session['slug']
    course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)
    teams = query_db(slug,
        'SELECT * FROM teams WHERE course_id = ? ORDER BY name', [course['id']]
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
    data = request.get_json()
    team_id = data.get('team_id')
    if not team_id:
        return jsonify({'error': 'Team ID required'}), 400
    student = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    state = query_db(slug, 'SELECT phase FROM course_state LIMIT 1', one=True)
    if state['phase'] != 'setup':
        return jsonify({'error': 'Team selection is closed'}), 403
    execute_db(slug,
        'UPDATE students SET team_id = ? WHERE id = ?',
        [team_id, student['id']]
    )
    return jsonify({'success': True})


@app.route('/api/state', methods=['GET'])
@student_login_required
def api_state():
    slug = session['slug']
    state = query_db(slug, 'SELECT * FROM course_state LIMIT 1', one=True)
    active_team = None
    if state and state['active_team_id']:
        active_team = query_db(slug,
            'SELECT * FROM teams WHERE id = ?', [state['active_team_id']], one=True
        )
    me = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    my_team = None
    if me and me['team_id']:
        my_team = query_db(slug,
            'SELECT * FROM teams WHERE id = ?', [me['team_id']], one=True
        )
    return jsonify({
        'phase': state['phase'] if state else 'setup',
        'active_team': dict(active_team) if active_team else None,
        'my_team': dict(my_team) if my_team else None,
        'current_question': state['current_question'] if state else None
    })


@app.route('/api/grade_peer', methods=['POST'])
@student_login_required
def grade_peer():
    slug = session['slug']
    data = request.get_json()
    recipient_id = data.get('recipient_id')
    criterion = data.get('criterion')
    score = data.get('score')
    if recipient_id is None or criterion is None or score is None:
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
    if grader['id'] == recipient_id:
        return jsonify({'error': 'Cannot grade yourself'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        '''INSERT INTO peer_reviews (course_id, grader_id, recipient_id, criterion, score)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, grader_id, recipient_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [course['id'], grader['id'], recipient_id, criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/grade_team', methods=['POST'])
@student_login_required
def grade_team():
    slug = session['slug']
    data = request.get_json()
    recipient_team_id = data.get('recipient_team_id')
    criterion = data.get('criterion')
    score = data.get('score')
    if recipient_team_id is None or criterion is None or score is None:
        return jsonify({'error': 'Missing fields'}), 400
    try:
        score = float(score)
    except ValueError:
        return jsonify({'error': 'Invalid score'}), 400
    grader = query_db(slug,
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not grader or not grader['team_id']:
        return jsonify({'error': 'You must be in a team to grade'}), 403
    if grader['team_id'] == recipient_team_id:
        return jsonify({'error': 'Cannot grade your own team'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        '''INSERT INTO team_reviews
           (course_id, grader_team_id, recipient_team_id, criterion, score)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, grader_team_id, recipient_team_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [course['id'], grader['team_id'], recipient_team_id, criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/submit_discussion', methods=['POST'])
@student_login_required
def submit_discussion():
    slug = session['slug']
    data = request.get_json()
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
    data = request.get_json()
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
    data = request.get_json()
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
    data = request.get_json()
    question = data.get('question', '')
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    execute_db(slug,
        'UPDATE course_state SET current_question = ? WHERE course_id = ?',
        [question, course['id']]
    )
    return jsonify({'success': True})


@app.route('/api/add_student', methods=['POST'])
@instructor_login_required
def add_student():
    slug = session['slug']
    data = request.get_json()
    student_id = data.get('student_id', '').strip()
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    if not student_id or not name or not pin:
        return jsonify({'error': 'All fields required'}), 400
    course = query_db(slug, 'SELECT id FROM courses LIMIT 1', one=True)
    try:
        execute_db(slug,
            'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
            [course['id'], student_id, name, pin]
        )
    except Exception:
        return jsonify({'error': 'Student ID already exists in this course'}), 400
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
    execute_db(slug, 'UPDATE students SET team_id = NULL WHERE course_id = ?', [course['id']])
    execute_db(slug,
        'UPDATE course_state SET phase = ?, active_team_id = NULL, current_question = NULL WHERE course_id = ?',
        ['setup', course['id']]
    )
    return jsonify({'success': True})


@app.route('/export/<slug>')
@instructor_login_required
def export_data(slug):
    if session.get('slug') != slug:
        flash('Unauthorized', 'error')
        return redirect(url_for('index'))

    course = query_db(slug, 'SELECT * FROM courses LIMIT 1', one=True)

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['=== POPPING COURSE EXPORT ==='])
    writer.writerow(['Course', course['name']])
    writer.writerow(['Code', course['code'] or ''])
    writer.writerow(['Exported at', datetime.now().isoformat()])
    writer.writerow([])

    writer.writerow(['--- STUDENTS ---'])
    writer.writerow(['student_id', 'name', 'team'])
    for row in query_db(slug,
        '''SELECT s.student_id, s.name, t.name as team
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           ORDER BY s.name'''
    ):
        writer.writerow([row['student_id'], row['name'], row['team'] or ''])
    writer.writerow([])

    writer.writerow(['--- PEER REVIEWS ---'])
    writer.writerow(['grader', 'recipient', 'criterion', 'score', 'time'])
    for row in query_db(slug,
        '''SELECT g.name as grader, r.name as recipient,
                  p.criterion, p.score, p.created_at
           FROM peer_reviews p
           JOIN students g ON p.grader_id = g.id
           JOIN students r ON p.recipient_id = r.id
           ORDER BY p.created_at'''
    ):
        writer.writerow([row['grader'], row['recipient'], row['criterion'],
                         row['score'], row['created_at']])
    writer.writerow([])

    writer.writerow(['--- TEAM REVIEWS ---'])
    writer.writerow(['grader_team', 'recipient_team', 'criterion', 'score', 'time'])
    for row in query_db(slug,
        '''SELECT gt.name as grader, rt.name as recipient,
                  t.criterion, t.score, t.created_at
           FROM team_reviews t
           JOIN teams gt ON t.grader_team_id = gt.id
           JOIN teams rt ON t.recipient_team_id = rt.id
           ORDER BY t.created_at'''
    ):
        writer.writerow([row['grader'], row['recipient'], row['criterion'],
                         row['score'], row['created_at']])
    writer.writerow([])

    writer.writerow(['--- DISCUSSION RESPONSES ---'])
    writer.writerow(['student', 'question', 'response', 'time'])
    for row in query_db(slug,
        '''SELECT s.name, d.question, d.response, d.created_at
           FROM discussion_responses d
           JOIN students s ON d.student_id = s.id
           ORDER BY d.created_at'''
    ):
        writer.writerow([row['name'], row['question'], row['response'],
                         row['created_at']])

    output.seek(0)
    filename = f"popping_{course['code'] or slug}_export.csv"
    return (
        output.getvalue(),
        200,
        {
            'Content-Type': 'text/csv',
            'Content-Disposition': f'attachment; filename={filename}'
        }
    )
