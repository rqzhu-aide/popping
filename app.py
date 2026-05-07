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


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'student_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def instructor_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'student_id' not in session:
            return redirect(url_for('login'))
        student = query_db(
            'SELECT is_instructor FROM students WHERE student_id = ?',
            [session['student_id']], one=True
        )
        if not student or not student['is_instructor']:
            flash('Instructor access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    if 'student_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        student_id = request.form.get('student_id', '').strip()
        pin = request.form.get('pin', '').strip()
        if not student_id or not pin:
            flash('Please enter both ID and PIN.', 'error')
            return render_template('login.html')
        student = query_db(
            'SELECT * FROM students WHERE student_id = ? AND pin = ?',
            [student_id, pin], one=True
        )
        if student:
            session['student_id'] = student['student_id']
            session['name'] = student['name']
            session['is_instructor'] = bool(student['is_instructor'])
            return redirect(url_for('dashboard'))
        flash('Invalid ID or PIN.', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/dashboard')
@login_required
def dashboard():
    if session.get('is_instructor'):
        return redirect(url_for('instructor'))
    student = query_db(
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    team = None
    if student and student['team_id']:
        team = query_db(
            'SELECT * FROM teams WHERE id = ?', [student['team_id']], one=True
        )
    teams = query_db('SELECT * FROM teams ORDER BY name')
    state = query_db('SELECT * FROM course_state WHERE id = 1', one=True)
    return render_template(
        'dashboard.html',
        student=student,
        team=team,
        teams=teams,
        state=state,
        phases=PHASES
    )


@app.route('/api/teams', methods=['GET'])
@login_required
def api_teams():
    teams = query_db('SELECT * FROM teams ORDER BY name')
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
@login_required
def join_team():
    if session.get('is_instructor'):
        return jsonify({'error': 'Instructors cannot join teams'}), 403
    data = request.get_json()
    team_id = data.get('team_id')
    if not team_id:
        return jsonify({'error': 'Team ID required'}), 400
    student = query_db(
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    state = query_db('SELECT phase FROM course_state WHERE id = 1', one=True)
    if state['phase'] != 'setup':
        return jsonify({'error': 'Team selection is closed'}), 403
    execute_db(
        'UPDATE students SET team_id = ? WHERE id = ?',
        [team_id, student['id']]
    )
    return jsonify({'success': True})


@app.route('/api/state', methods=['GET'])
@login_required
def api_state():
    state = query_db('SELECT * FROM course_state WHERE id = 1', one=True)
    active_team = None
    if state and state['active_team_id']:
        active_team = query_db(
            'SELECT * FROM teams WHERE id = ?',
            [state['active_team_id']], one=True
        )
    me = query_db(
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
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
@login_required
def grade_peer():
    if session.get('is_instructor'):
        return jsonify({'error': 'Instructors do not grade'}), 403
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
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not grader:
        return jsonify({'error': 'Grader not found'}), 404
    if grader['id'] == recipient_id:
        return jsonify({'error': 'Cannot grade yourself'}), 400
    execute_db(
        '''INSERT INTO peer_reviews (grader_id, recipient_id, criterion, score)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(grader_id, recipient_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [grader['id'], recipient_id, criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/grade_team', methods=['POST'])
@login_required
def grade_team():
    if session.get('is_instructor'):
        return jsonify({'error': 'Instructors do not grade'}), 403
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
        'SELECT * FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    if not grader or not grader['team_id']:
        return jsonify({'error': 'You must be in a team to grade'}), 403
    if grader['team_id'] == recipient_team_id:
        return jsonify({'error': 'Cannot grade your own team'}), 400
    execute_db(
        '''INSERT INTO team_reviews
           (grader_team_id, recipient_team_id, criterion, score)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(grader_team_id, recipient_team_id, criterion)
           DO UPDATE SET score=excluded.score''',
        [grader['team_id'], recipient_team_id, criterion, score]
    )
    return jsonify({'success': True})


@app.route('/api/submit_discussion', methods=['POST'])
@login_required
def submit_discussion():
    data = request.get_json()
    question = data.get('question', '')
    response = data.get('response', '')
    if not question or not response:
        return jsonify({'error': 'Question and response required'}), 400
    student = query_db(
        'SELECT id FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    execute_db(
        'INSERT INTO discussion_responses (student_id, question, response) VALUES (?, ?, ?)',
        [student['id'], question, response]
    )
    return jsonify({'success': True})


@app.route('/api/discussion_responses')
@login_required
def api_discussion_responses():
    rows = query_db(
        '''SELECT d.*, s.name, s.student_id, t.name as team_name
           FROM discussion_responses d
           JOIN students s ON d.student_id = s.id
           LEFT JOIN teams t ON s.team_id = t.id
           ORDER BY d.created_at DESC'''
    )
    return jsonify([dict(r) for r in rows])


@app.route('/api/my_grades')
@login_required
def my_grades():
    student = query_db(
        'SELECT id, team_id FROM students WHERE student_id = ?',
        [session['student_id']], one=True
    )
    peer = query_db(
        '''SELECT criterion, AVG(score) as avg_score, COUNT(*) as count
           FROM peer_reviews WHERE recipient_id = ? GROUP BY criterion''',
        [student['id']]
    )
    team = query_db(
        '''SELECT criterion, AVG(score) as avg_score, COUNT(*) as count
           FROM team_reviews WHERE recipient_team_id = ? GROUP BY criterion''',
        [student['team_id']]
    ) if student['team_id'] else []
    return jsonify({
        'peer': [dict(r) for r in peer],
        'team': [dict(r) for r in team]
    })


@app.route('/instructor')
@instructor_required
def instructor():
    teams = query_db('SELECT * FROM teams ORDER BY name')
    students = query_db(
        '''SELECT s.*, t.name as team_name FROM students s
           LEFT JOIN teams t ON s.team_id = t.id ORDER BY s.name'''
    )
    state = query_db('SELECT * FROM course_state WHERE id = 1', one=True)
    return render_template(
        'instructor.html',
        teams=teams,
        students=students,
        state=state,
        phases=PHASES
    )


@app.route('/api/set_phase', methods=['POST'])
@instructor_required
def set_phase():
    data = request.get_json()
    phase = data.get('phase')
    if phase not in PHASES:
        return jsonify({'error': 'Invalid phase'}), 400
    execute_db('UPDATE course_state SET phase = ? WHERE id = 1', [phase])
    return jsonify({'success': True, 'phase': phase})


@app.route('/api/set_active_team', methods=['POST'])
@instructor_required
def set_active_team():
    data = request.get_json()
    team_id = data.get('team_id')
    if team_id is not None:
        team = query_db('SELECT id FROM teams WHERE id = ?', [team_id], one=True)
        if not team:
            return jsonify({'error': 'Team not found'}), 404
    execute_db(
        'UPDATE course_state SET active_team_id = ? WHERE id = 1',
        [team_id]
    )
    return jsonify({'success': True})


@app.route('/api/set_question', methods=['POST'])
@instructor_required
def set_question():
    data = request.get_json()
    question = data.get('question', '')
    execute_db(
        'UPDATE course_state SET current_question = ? WHERE id = 1', [question]
    )
    return jsonify({'success': True})


@app.route('/api/add_student', methods=['POST'])
@instructor_required
def add_student():
    data = request.get_json()
    student_id = data.get('student_id', '').strip()
    name = data.get('name', '').strip()
    pin = data.get('pin', '').strip()
    if not student_id or not name or not pin:
        return jsonify({'error': 'All fields required'}), 400
    try:
        execute_db(
            'INSERT INTO students (student_id, name, pin) VALUES (?, ?, ?)',
            [student_id, name, pin]
        )
    except Exception:
        return jsonify({'error': 'Student ID already exists'}), 400
    return jsonify({'success': True})


@app.route('/api/remove_student/<int:student_db_id>', methods=['DELETE'])
@instructor_required
def remove_student(student_db_id):
    execute_db('DELETE FROM students WHERE id = ? AND is_instructor = 0', [student_db_id])
    return jsonify({'success': True})


@app.route('/api/reset_data', methods=['POST'])
@instructor_required
def reset_data():
    execute_db('DELETE FROM peer_reviews')
    execute_db('DELETE FROM team_reviews')
    execute_db('DELETE FROM discussion_responses')
    execute_db('UPDATE students SET team_id = NULL WHERE is_instructor = 0')
    execute_db('UPDATE course_state SET phase = ?, active_team_id = NULL, current_question = NULL WHERE id = 1', ['setup'])
    return jsonify({'success': True})


@app.route('/export')
@instructor_required
def export_data():
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(['=== POPPING COURSE EXPORT ==='])
    writer.writerow(['Exported at', datetime.now().isoformat()])
    writer.writerow([])

    writer.writerow(['--- STUDENTS ---'])
    writer.writerow(['student_id', 'name', 'team', 'is_instructor'])
    for row in query_db(
        '''SELECT s.student_id, s.name, t.name as team, s.is_instructor
           FROM students s LEFT JOIN teams t ON s.team_id = t.id
           ORDER BY s.name'''
    ):
        writer.writerow([row['student_id'], row['name'], row['team'] or '', row['is_instructor']])
    writer.writerow([])

    writer.writerow(['--- PEER REVIEWS ---'])
    writer.writerow(['grader', 'recipient', 'criterion', 'score', 'time'])
    for row in query_db(
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
    for row in query_db(
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
    for row in query_db(
        '''SELECT s.name, d.question, d.response, d.created_at
           FROM discussion_responses d
           JOIN students s ON d.student_id = s.id
           ORDER BY d.created_at'''
    ):
        writer.writerow([row['name'], row['question'], row['response'],
                         row['created_at']])

    output.seek(0)
    return (
        output.getvalue(),
        200,
        {
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename=popping_export.csv'
        }
    )


@app.cli.command('init-db')
def init_db_command():
    from database import init_db
    init_db()
    print('Database initialized.')


@app.cli.command('seed')
def seed_command():
    execute_db(
        "INSERT OR IGNORE INTO students (student_id, name, pin, is_instructor) VALUES (?, ?, ?, 1)",
        ['instructor', 'Instructor', 'admin123']
    )
    print('Seeded instructor account: instructor / admin123')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
