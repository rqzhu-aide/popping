#!/usr/bin/env python3
import sys
import os
import re
import yaml
import sqlite3
import getpass

if len(sys.argv) < 2:
    print("Usage: python3 init-course-db.py <course_config_dir>")
    print("  <course_config_dir> is the folder containing course.yaml (e.g. classes/432fall2026)")
    sys.exit(1)

config_dir = os.path.abspath(sys.argv[1])
yaml_path = os.path.join(config_dir, 'course.yaml')

if not os.path.exists(yaml_path):
    print(f"Error: course.yaml not found in {config_dir}")
    sys.exit(1)

with open(yaml_path) as f:
    cfg = yaml.safe_load(f)

slug = cfg['slug']
name = cfg['name']
code = cfg['code']
semester = cfg['semester']

COLORS = [
    '#ef4444', '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6',
    '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#6366f1',
    '#14b8a6', '#e11d48', '#0ea5e9', '#a855f7', '#22c55e',
    '#eab308', '#dc2626', '#2563eb', '#059669', '#d97706'
]


def build_team_rows(config):
    configured = config.get('teams') or []
    pool_size = int(config.get('team_pool_size', max(20, len(configured) or 0)))
    rows = []

    for i, team in enumerate(configured):
        rows.append({
            'name': team.get('name') or f"Team {i + 1}",
            'color': team.get('color') or COLORS[i % len(COLORS)]
        })

    while len(rows) < pool_size:
        i = len(rows)
        rows.append({
            'name': f"Team {i + 1}",
            'color': COLORS[i % len(COLORS)]
        })

    return rows


def read_presentation_question_index(project_root, slug, week_num):
    index_path = os.path.join(project_root, 'classes', slug, f'week{week_num}', 'index.md')
    if not os.path.exists(index_path):
        return []

    questions = []
    with open(index_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(\d+)\.\s+(.+)$', line)
            if m:
                questions.append({'num': int(m.group(1)), 'title': m.group(2).strip()})
    return questions


team_rows = build_team_rows(cfg)
default_max_teams = int(cfg.get('max_teams', min(len(cfg.get('teams') or []) or 5, len(team_rows))))
default_max_members = int(cfg.get('max_members_per_team', 10))

# Determine where to write the database
# Priority: /data (Render disk) > local data/ folder
if os.path.isdir('/data'):
    DATA_DIR = '/data'
else:
    project_root = os.path.dirname(os.path.dirname(config_dir))
    DATA_DIR = os.path.join(project_root, 'data')

db_dir = os.path.join(DATA_DIR, slug)
db_path = os.path.join(db_dir, 'popping.db')

print("=== Create/Reset Course Database ===")
print(f"Course: {name} ({slug})")
print(f"Config:  {config_dir}")
print(f"DB path: {db_path}")
print("")

# Prompt for credentials
username = input("Instructor username: ").strip()
display_name = input("Instructor name: ").strip()
pin = getpass.getpass("Instructor PIN: ").strip()

if not username or not display_name or not pin:
    print("Error: all fields are required.")
    sys.exit(1)

# Ensure DB directory exists
os.makedirs(db_dir, exist_ok=True)

# Remove old DB
if os.path.exists(db_path):
    print("Removing existing database...")
    os.remove(db_path)

# Find project root and schema
project_root = os.path.dirname(os.path.dirname(config_dir))
schema_path = os.path.join(project_root, 'popping.sql')

print("Initializing schema...")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
with open(schema_path) as f:
    conn.executescript(f.read())

# Insert instructor
cur = conn.execute(
    "INSERT INTO instructors (username, name, pin) VALUES (?, ?, ?)",
    (username, display_name, pin)
)
instructor_id = cur.lastrowid

# Insert course
cur = conn.execute(
    "INSERT INTO courses (name, code, semester, slug, instructor_id) VALUES (?, ?, ?, ?, ?)",
    (name, code, semester, slug, instructor_id)
)
course_id = cur.lastrowid

# Insert configured teams, plus spare generated teams for in-class expansion.
for team in team_rows:
    conn.execute(
        "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
        (course_id, team['name'], team['color'])
    )

# Insert course state
conn.execute(
    "INSERT INTO course_state (course_id, phase, max_teams, max_members_per_team) VALUES (?, 'setup', 5, 10)",
    (course_id,)
)
conn.execute(
    "UPDATE course_state SET max_teams = ?, max_members_per_team = ? WHERE course_id = ?",
    (default_max_teams, default_max_members, course_id)
)

# Seed initial presentation questions from week1/index.md when available.
for q in read_presentation_question_index(project_root, slug, 1):
    conn.execute(
        "INSERT INTO questions (course_id, question_num, question_text, title, week_num) VALUES (?, ?, ?, ?, ?)",
        (course_id, q['num'], q['title'][:200], q['title'], 1)
    )

conn.commit()
conn.close()

print("")
print("=== Done! ===")
print(f"Instructor login: {username} / [hidden]")
