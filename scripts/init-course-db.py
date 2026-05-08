#!/usr/bin/env python3
import sys
import os
import json
import sqlite3
import getpass

if len(sys.argv) < 2:
    print("Usage: python3 init-course-db.py <course_dir>")
    sys.exit(1)

course_dir = os.path.abspath(sys.argv[1])
json_path = os.path.join(course_dir, 'course.json')
db_path = os.path.join(course_dir, 'popping.db')

if not os.path.exists(json_path):
    print(f"Error: course.json not found in {course_dir}")
    sys.exit(1)

with open(json_path) as f:
    cfg = json.load(f)

slug = cfg['slug']
name = cfg['name']
code = cfg['code']
semester = cfg['semester']
teams = cfg['teams']

print("=== Create/Reset Course Database ===")
print(f"Course: {name} ({slug})")
print(f"DB path: {db_path}")
print("")

# Prompt for credentials
username = input("Instructor username: ").strip()
display_name = input("Instructor name: ").strip()
pin = getpass.getpass("Instructor PIN: ").strip()

if not username or not display_name or not pin:
    print("Error: all fields are required.")
    sys.exit(1)

# Remove old DB
if os.path.exists(db_path):
    print("Removing existing database...")
    os.remove(db_path)

# Find project root and schema
project_root = os.path.dirname(os.path.dirname(course_dir))
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

# Insert teams
for t in teams:
    conn.execute(
        "INSERT INTO teams (course_id, name, color) VALUES (?, ?, ?)",
        (course_id, t['name'], t['color'])
    )

# Insert course state
conn.execute(
    "INSERT INTO course_state (course_id, phase, active_team_id) VALUES (?, 'setup', NULL)",
    (course_id,)
)

conn.commit()
conn.close()

print("")
print("=== Done! ===")
print(f"Instructor login: {username} / [hidden]")
