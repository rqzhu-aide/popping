#!/usr/bin/env python3
"""Initialize or reset the demo course database.

Creates a self-contained 'demo' course with:
  - 1 instructor (no real password needed — demo bypasses login)
  - 12 pre-assigned students across 4 teams
  - 10 sample questions
  - Course state in 'setup' phase

Usage:
    python3 scripts/init-demo-db.py           # create or reset
    python3 scripts/init-demo-db.py --check   # exit 0 if exists, 1 if not
"""
import sys
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE_DIR, 'popping.sql')

if os.path.isdir('/data'):
    DATA_DIR = '/data'
else:
    DATA_DIR = os.path.join(BASE_DIR, 'data')

DB_DIR = os.path.join(DATA_DIR, 'demo')
DB_PATH = os.path.join(DB_DIR, 'popping.db')


def init_demo_db():
    os.makedirs(DB_DIR, exist_ok=True)

    # Remove old DB
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Load schema
    with open(SCHEMA) as f:
        conn.executescript(f.read())

    # Instructor
    conn.execute(
        "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
        ('demo_instructor', 'Demo Instructor', 'demo')
    )
    instructor_id = conn.execute(
        "SELECT id FROM instructors WHERE username = 'demo_instructor'"
    ).fetchone()['id']

    # Course
    conn.execute(
        "INSERT INTO courses (name, code, semester, slug, instructor_id, is_active) "
        "VALUES (?, ?, ?, ?, ?, 0)",  # is_active=0 so it doesn't show on landing page
        ('Popping Demo Course', 'DEMO 101', 'Always', 'demo', instructor_id)
    )
    course_id = conn.execute(
        "SELECT id FROM courses WHERE slug = 'demo'"
    ).fetchone()['id']

    # Teams (4 teams)
    teams_data = [
        ('Team Alpha', '#ef4444'),
        ('Team Beta', '#3b82f6'),
        ('Team Gamma', '#10b981'),
        ('Team Delta', '#f59e0b'),
    ]
    for name, color in teams_data:
        conn.execute(
            "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
            (course_id, name, color)
        )

    # 20 students, 5 per team
    students_data = [
        # (student_id, name, team_index)
        ('demo001', 'Alice Chen',     0),
        ('demo002', 'Bob Garcia',     0),
        ('demo003', 'Cara Singh',     0),
        ('demo004', 'Derek Wright',   0),
        ('demo005', 'Eva Müller',     0),
        ('demo006', 'Finn O\'Brien',  1),
        ('demo007', 'Gina Rossi',     1),
        ('demo008', 'Hiro Tanaka',    1),
        ('demo009', 'Iris Novak',     1),
        ('demo010', 'Jasper Lee',     1),
        ('demo011', 'Kira Patel',     2),
        ('demo012', 'Leo Silva',      2),
        ('demo013', 'Mara Cohen',     2),
        ('demo014', 'Nico Bauer',     2),
        ('demo015', 'Omar Haddad',    2),
        ('demo016', 'Priya Nair',     3),
        ('demo017', 'Quinn Foster',   3),
        ('demo018', 'Rosa Mendez',    3),
        ('demo019', 'Sven Eriksson',  3),
        ('demo020', 'Tara Brooks',    3),
    ]
    for sid, name, team_idx in students_data:
        team = conn.execute(
            "SELECT id FROM teams WHERE course_id = ? ORDER BY id LIMIT 1 OFFSET ?",
            (course_id, team_idx)
        ).fetchone()
        conn.execute(
            "INSERT INTO students (course_id, student_id, name, pin, team_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (course_id, sid, name, 'demo', team['id'])
        )

    # 10 questions
    questions = [
        "Explain the key differences between bagging and boosting. How does each method reduce variance and bias?",
        "A random forest reports high feature importance for X1 and X2. Discuss why this does not imply causality.",
        "As the number of trees B increases in a random forest: does bias change? Does variance change? Justify.",
        "In gradient boosting, what happens when the learning rate is very small (0.001) vs very large (1.0)?",
        "XGBoost adds regularization. How does the tree penalty parameter control model complexity?",
        "In AdaBoost, misclassified examples get higher weights. Why does this focus on 'hard' examples?",
        "Compare stacking and blending: how are base predictions combined? What is the role of the holdout set?",
        "Explain why out-of-bag (OOB) error is an unbiased estimate. What are its limitations vs k-fold CV?",
        "The universal approximation theorem says a single hidden layer can approximate any function. Why prefer deep networks?",
        "A neural network has 99% training accuracy but 72% validation. Propose 3 regularization strategies and explain.",
    ]
    for i, q in enumerate(questions, 1):
        conn.execute(
            "INSERT INTO questions (course_id, question_num, question_text) VALUES (?, ?, ?)",
            (course_id, i, q)
        )

    # Course state — start in setup
    conn.execute(
        "INSERT INTO course_state (course_id, phase, max_teams, max_members_per_team) "
        "VALUES (?, 'setup', 4, 5)",
        (course_id,)
    )

    conn.commit()
    conn.close()
    print(f"Demo database created at {DB_PATH}")
    print(f"  Instructor: demo_instructor")
    print(f"  Students:   20 (demo001-demo020, PIN='demo')")
    print(f"  Teams:      4 (Alpha, Beta, Gamma, Delta)")
    print(f"  Questions:  10")


if __name__ == '__main__':
    if '--check' in sys.argv:
        sys.exit(0 if os.path.exists(DB_PATH) else 1)
    init_demo_db()
