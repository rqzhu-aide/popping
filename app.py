import os
import csv
import io
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash, g
)

import config
from database import init_app, query_db, execute_db, get_db

app = Flask(__name__)
app.config.from_object(config)
init_app(app)

PHASES = ['setup', 'discussion', 'competition', 'grading', 'ended']


def student_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'student_id' not in session or 'course_id' not in session:
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated


def instructor_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'instructor_id' not in session:
            return redirect(url_for('instructor_login'))
        return f(*args, **kwargs)
    return decorated


def instructor_course_access(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'instructor_id' not in session:
            return redirect(url_for('instructor_login'))
        course_id = kwargs.get('course_id')
        if course_id:
            course = query_db(
                'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
                [course_id, session['instructor_id']], one=True
            )
            if not course:
                flash('You do not have access to this course.', 'error')
                return redirect(url_for('instructor'))
            g.current_course = course
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    if 'student_id' in session and 'course_id' in session:
        return redirect(url_for('dashboard'))
    if 'instructor_id' in session:
        return redirect(url_for('instructor'))
    courses = query_db(
        'SELECT c.*, i.name as instructor_name FROM courses c '
        'JOIN instructors i ON c.instructor_id = i.id '
        'WHERE c.is_active = 1 ORDER BY c.name'
    )
    return render_template('index.html', courses=courses)


@app.route('/login/<int:course_id>', methods=['GET', 'POST'])
def login(course_id):
    course = query_db('SELECT * FROM courses WHERE id = ?', [course_id], one=True)
    if not course:
        flash('Course not found.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        pin = request.form.get('pin', '').strip()
        if not student_id or not pin:
            flash('Please enter both ID and PIN.', 'error')
            return render_template('login.html', course=course)
        student = query_db(
            'SELECT * FROM students WHERE course_id = ? AND student_id = ? AND pin = ?',
            [course_id, student_id, pin], one=True
        )
        if student:
            session['student_id'] = student['student_id']
            session['name'] = student['name']
            session['course_id'] = course_id
            return redirect(url_for('dashboard'))
        flash('Invalid ID or PIN for this course.', 'error')
    return render_template('login.html', course=course)


@app.route('/instructor_login', methods=['GET', 'POST'])
def instructor_login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        pin = request.form.get('pin', '').strip()
        if not username or not pin:
            flash('Please enter both username and PIN.', 'error')
            return render_template('instructor_login.html')
        instructor = query_db(
            'SELECT * FROM instructors WHERE username = ? AND pin = ?',
            [username, pin], one=True
        )
        if instructor:
            session['instructor_id'] = instructor['id']
            session['instructor_name'] = instructor['name']
            return redirect(url_for('instructor'))
        flash('Invalid username or PIN.', 'error')
    return render_template('instructor_login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@student_login_required
def dashboard():
    course_id = session['course_id']
    student = query_db(
        'SELECT * FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    team = None
    if student and student['team_id']:
        team = query_db(
            'SELECT * FROM teams WHERE id = ?', [student['team_id']], one=True
        )
    teams = query_db(
        'SELECT * FROM teams WHERE course_id = ? ORDER BY name', [course_id]
    )
    state = query_db(
        'SELECT * FROM course_state WHERE course_id = ?', [course_id], one=True
    )
    course = query_db('SELECT * FROM courses WHERE id = ?', [course_id], one=True)
    return render_template(
        'dashboard.html',
        student=student,
        team=team,
        teams=teams,
        state=state,
        course=course,
        phases=PHASES
    )


@app.route('/instructor')
@instructor_login_required
def instructor():
    courses = query_db(
        'SELECT c.*, COUNT(DISTINCT s.id) as student_count, '
        'COUNT(DISTINCT t.id) as team_count '
        'FROM courses c '
        'LEFT JOIN students s ON s.course_id = c.id '
        'LEFT JOIN teams t ON t.course_id = c.id '
        'WHERE c.instructor_id = ? '
        'GROUP BY c.id ORDER BY c.name',
        [session['instructor_id']]
    )
    return render_template('instructor_courses.html', courses=courses)


@app.route('/instructor/course/<int:course_id>')
@instructor_course_access
def instructor_course(course_id):
    course = g.current_course
    teams = query_db(
        'SELECT * FROM teams WHERE course_id = ? ORDER BY name', [course_id]
    )
    students = query_db(
        '''SELECT s.*, t.name as team_name, t.color as team_color
           FROM students s
           LEFT JOIN teams t ON s.team_id = t.id
           WHERE s.course_id = ? ORDER BY s.name''',
        [course_id]
    )
    state = query_db(
        'SELECT * FROM course_state WHERE course_id = ?', [course_id], one=True
    )
    return render_template(
        'instructor.html',
        course=course,
        teams=teams,
        students=students,
        state=state,
        phases=PHASES
    )


# ---------------------------------------------------------------------------
# API endpoints (all course-scoped via session)
# ---------------------------------------------------------------------------

@app.route('/api/teams', methods=['GET'])
@student_login_required
def api_teams():
    course_id = session['course_id']
    teams = query_db(
        'SELECT * FROM teams WHERE course_id = ? ORDER BY name', [course_id]
    )
    result = []
    for t in teams:
        members = query_db(
            'SELECT student_id, name FROM students WHERE team_id = ?',
            [t['id']]
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
    course_id = session['course_id']
    data = request.get_json()
    team_id = data.get('team_id')
    if not team_id:
        return jsonify({'error': 'Team ID required'}), 400
    student = query_db(
        'SELECT * FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    state = query_db(
        'SELECT phase FROM course_state WHERE course_id = ?', [course_id], one=True
    )
    if state['phase'] != 'setup':
        return jsonify({'error': 'Team selection is closed'}), 403
    execute_db(
        'UPDATE students SET team_id = ? WHERE id = ?',
        [team_id, student['id']]
    )
    return jsonify({'success': True})


@app.route('/api/state', methods=['GET'])
@student_login_required
def api_state():
    course_id = session['course_id']
    state = query_db(
        'SELECT * FROM course_state WHERE course_id = ?', [course_id], one=True
    )
    active_team = None
    if state and state['active_team_id']:
        active_team = query_db(
            'SELECT * FROM teams WHERE id = ?',
            [state['active_team_id']], one=True
        )
    me = query_db(
        'SELECT * FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    my_team = None
    if me and me['team_id']:
        my_team = query_db(
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
    course_id = session['course_id']
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
    grader = query_db(
        'SELECT * FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    if not grader:
        return jsonify({'error': 'Grader not found'}), 404
    if grader['id'] == recipient_id:
        return jsonify({'error': 'Cannot grade yourself'}), 400
    execute_db(
        '''INSERT INTO peer_reviews (course_id, grader_id, recipient_id, criterion, score)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, grader_id, recipient_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [course_id, grader['id'], recipient_id, criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/grade_team', methods=['POST'])
@student_login_required
def grade_team():
    course_id = session['course_id']
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
    grader = query_db(
        'SELECT * FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    if not grader or not grader['team_id']:
        return jsonify({'error': 'You must be in a team to grade'}), 403
    if grader['team_id'] == recipient_team_id:
        return jsonify({'error': 'Cannot grade your own team'}), 400
    execute_db(
        '''INSERT INTO team_reviews
           (course_id, grader_team_id, recipient_team_id, criterion, score)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(course_id, grader_team_id, recipient_team_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [course_id, grader['team_id'], recipient_team_id, criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/submit_discussion', methods=['POST'])
@student_login_required
def submit_discussion():
    course_id = session['course_id']
    data = request.get_json()
    question = data.get('question', '')
    response = data.get('response', '')
    if not question or not response:
        return jsonify({'error': 'Question and response required'}), 400
    student = query_db(
        'SELECT id FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    execute_db(
        'INSERT INTO discussion_responses (course_id, student_id, question, response) VALUES (?, ?, ?, ?)',
        [course_id, student['id'], question, response]
    )
    return jsonify({'success': True})


@app.route('/api/discussion_responses')
@student_login_required
def api_discussion_responses():
    course_id = session['course_id']
    rows = query_db(
        '''SELECT d.*, s.name, s.student_id, t.name as team_name
           FROM discussion_responses d
           JOIN students s ON d.student_id = s.id
           LEFT JOIN teams t ON s.team_id = t.id
           WHERE d.course_id = ?
           ORDER BY d.created_at DESC''',
        [course_id]
    )
    return jsonify([dict(r) for r in rows])


@app.route('/api/my_grades')
@student_login_required
def my_grades():
    course_id = session['course_id']
    student = query_db(
        'SELECT id, team_id FROM students WHERE course_id = ? AND student_id = ?',
        [course_id, session['student_id']], one=True
    )
    peer = query_db(
        '''SELECT criterion, AVG(score) as avg_score, COUNT(*) as count
           FROM peer_reviews WHERE course_id = ? AND recipient_id = ? GROUP BY criterion''',
        [course_id, student['id']]
    )
    team = query_db(
        '''SELECT criterion, AVG(score) as avg_score, COUNT(*) as count
           FROM team_reviews WHERE course_id = ? AND recipient_team_id = ? GROUP BY criterion''',
        [course_id, student['team_id']]
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
    data = request.get_json()
    course_id = data.get('course_id')
    phase = data.get('phase')
    if not course_id or phase not in PHASES:
        return jsonify({'error': 'Invalid course or phase'}), 400
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [course_id, session['instructor_id']], one=True
    )
    if not course:
        return jsonify({'error': 'Unauthorized'}), 403
    execute_db(
        'UPDATE course_state SET phase = ? WHERE course_id = ?',
        [phase, course_id]
    )
    return jsonify({'success': True, 'phase': phase})


@app.route('/api/set_active_team', methods=['POST'])
@instructor_login_required
def set_active_team():
    data = request.get_json()
    course_id = data.get('course_id')
    team_id = data.get('team_id')
    if not course_id:
        return jsonify({'error': 'Course ID required'}), 400
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [course_id, session['instructor_id']], one=True
    )
    if not course:
        return jsonify({'error': 'Unauthorized'}), 403
    if team_id is not None:
        team = query_db(
            'SELECT id FROM teams WHERE id = ? AND course_id = ?',
            [team_id, course_id], one=True
        )
        if not team:
            return jsonify({'error': 'Team not found'}), 404
    execute_db(
        'UPDATE course_state SET active_team_id = ? WHERE course_id = ?',
        [team_id, course_id]
    )
    return jsonify({'success': True})


@app.route('/api/set_question', methods=['POST'])
@instructor_login_required
def set_question():
    data = request.get_json()
    course_id = data.get('course_id')
    question = data.get('question', '')
    if not course_id:
        return jsonify({'error': 'Course ID required'}), 400
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [course_id, session['instructor_id']], one=True
    )
    if not course:
        return jsonify({'error': 'Unauthorized'}), 403
    execute_db(
        'UPDATE course_state SET current_question = ? WHERE course_id = ?',
        [question, course_id]
    )
    return jsonify({'success': True})


@app.route('/api/add_student', methods=['POST'])
@instructor_login_required
def add_student():
    data = request.get_json()
    course_id = data.get('course_id')
    student_id = data.get('student_id', '').strip()
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    if not course_id or not student_id or not name or not pin:
        return jsonify({'error': 'All fields required'}), 400
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [course_id, session['instructor_id']], one=True
    )
    if not course:
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        execute_db(
            'INSERT INTO students (course_id, student_id, name, pin) VALUES (?, ?, ?, ?)',
            [course_id, student_id, name, pin]
        )
    except Exception:
        return jsonify({'error': 'Student ID already exists in this course'}), 400
    return jsonify({'success': True})


@app.route('/api/remove_student/<int:student_db_id>', methods=['DELETE'])
@instructor_login_required
def remove_student(student_db_id):
    student = query_db('SELECT course_id FROM students WHERE id = ?', [student_db_id], one=True)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [student['course_id'], session['instructor_id']], one=True
    )
    if not course:
        return jsonify({'error': 'Unauthorized'}), 403
    execute_db('DELETE FROM students WHERE id = ?', [student_db_id])
    return jsonify({'success': True})


@app.route('/api/reset_data', methods=['POST'])
@instructor_login_required
def reset_data():
    data = request.get_json()
    course_id = data.get('course_id')
    if not course_id:
        return jsonify({'error': 'Course ID required'}), 400
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [course_id, session['instructor_id']], one=True
    )
    if not course:
        return jsonify({'error': 'Unauthorized'}), 403
    execute_db('DELETE FROM peer_reviews WHERE course_id = ?', [course_id])
    execute_db('DELETE FROM team_reviews WHERE course_id = ?', [course_id])
    execute_db('DELETE FROM discussion_responses WHERE course_id = ?', [course_id])
    execute_db('UPDATE students SET team_id = NULL WHERE course_id = ?', [course_id])
    execute_db(
        'UPDATE course_state SET phase = ?, active_team_id = NULL, current_question = NULL WHERE course_id = ?',
        ['setup', course_id]
    )
    return jsonify({'success': True})


@app.route('/export/<int:course_id>')
@instructor_login_required
def export_data(course_id):
    course = query_db(
        'SELECT * FROM courses WHERE id = ? AND instructor_id = ?',
        [course_id, session['instructor_id']], one=True
    )
    if not course:
        flash('Unauthorized', 'error')
        return redirect(url_for('instructor'))

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['=== POPPING COURSE EXPORT ==='])
    writer.writerow(['Course', course['name']])
    writer.writerow(['Code', course['code'] or ''])
    writer.writerow(['Exported at', datetime.now().isoformat()])
    writer.writerow([])

    writer.writerow(['--- STUDENTS ---'])
    writer.writerow(['student_id', 'name', 'team', 'is_instructor'])
    for row in query_db(
        '''SELECT s.student_id, s.name, t.name as team
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           WHERE s.course_id = ? ORDER BY s.name''',
        [course_id]
    ):
        writer.writerow([row['student_id'], row['name'], row['team'] or ''])
    writer.writerow([])

    writer.writerow(['--- PEER REVIEWS ---'])
    writer.writerow(['grader', 'recipient', 'criterion', 'score', 'time'])
    for row in query_db(
        '''SELECT g.name as grader, r.name as recipient,
                  p.criterion, p.score, p.created_at
           FROM peer_reviews p
           JOIN students g ON p.grader_id = g.id
           JOIN students r ON p.recipient_id = r.id
           WHERE p.course_id = ? ORDER BY p.created_at''',
        [course_id]
    ):
        writer.writerow([row['grader'], row['recipient'], row['criterion'],
                         row['score'], row['created_at']])
    writer.writerow([])

    writer.writerow(['--- TEAM REVIEWS ---'])
    writer.writerow(['grader_team', 'recipient_team', 'criterion', 'score', 'time'])
    for row in query_db(
        '''SELECT gt.name as grader, rt.name as recipient,
                  t.criterion, t.score, t.created_at
           FROM team_reviews t
           JOIN teams gt ON t.grader_team_id = gt.id
           JOIN teams rt ON t.recipient_team_id = rt.id
           WHERE t.course_id = ? ORDER BY t.created_at''',
        [course_id]
    ):
        writer.writerow([row['grader'], row['recipient'], row['criterion'],
                         row['score'], row['created_at']])
    writer.writerow([])

    writer.writerow(['--- DISCUSSION RESPONSES ---'])
    writer.writerow(['student', 'question', 'response', 'time'])
    for row in query_db(
        '''SELECT s.name, d.question, d.response, d.created_at
           FROM discussion_responses d
           JOIN students s ON d.student_id = s.id
           WHERE d.course_id = ? ORDER BY d.created_at''',
        [course_id]
    ):
        writer.writerow([row['name'], row['question'], row['response'],
                         row['created_at']])

    output.seek(0)
    filename = f"popping_{course['code'] or course_id}_export.csv"
    return (
        output.getvalue(),
        200,
        {
            'Content-Type': 'text/csv',
            'Content-Disposition': f'attachment; filename={filename}'
        }
    )


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

@app.cli.command('init-db')
def init_db_command():
    from database import init_db
    init_db()
    print('Database initialized.')


@app.cli.command('seed')
def seed_command():
    execute_db(
        "INSERT OR IGNORE INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ['instructor', 'Instructor', 'admin123']
    )
    print('Seeded instructor account: instructor / admin123')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
