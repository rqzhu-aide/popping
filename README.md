# 🍿 Popping

Multi-course interactive classroom team management and peer grading system.

## Features

- **Multi-Course Support**: One app hosts many courses, each with its own instructor, students, teams, and data.
- **Team Selection**: Students log in with ID + PIN and pick a team within their course.
- **Live Roster**: Real-time view of who joined which team (3-second polling).
- **Discussion Phase**: Students respond to instructor questions.
- **Peer Grading**: Students grade teammates during discussion.
- **Competition Mode**: Teams present; instructor selects active team per course.
- **Team Grading**: Non-presenting teams grade the presenting team.
- **Instructor Panel**: Manage multiple courses, control phases, pick presenting teams.
- **Data Export**: Download per-course grades & responses as CSV.

## Tech Stack

- **Backend**: Python + Flask
- **Database**: SQLite (zero-config, file-based, one DB for all courses)
- **Frontend**: Vanilla JS + Jinja2 templates + modern CSS
- **Auth**: Simple session-based (ID + PIN for students; username + PIN for instructors)

## Quick Start (Local)

```bash
# 1. Clone
git clone https://github.com/rqzhu-aide/popping.git
cd popping
```bash
# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialize database (interactive prompt for instructor credentials)
bash scripts/init-db.sh

# 4. Run
flask --app app run
```

Open http://127.0.0.1:5000

### Login

After running `init-db.sh`, use the username and PIN you entered. Add students via the instructor panel.

### Pre-seeded Courses

Two demo courses are created automatically:

1. **Basics of Statistical Learning** (STAT 432, Fall 2026)
2. **Machine Learning** (STAT 542, Fall 2025)

## Deploy to Render

1. Push this repo to GitHub.
2. In Render, create a new **Web Service** and connect this repo.
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn app:app`
5. Add environment variable: `SECRET_KEY` = (any random string)
6. After first deploy, run in Render Shell:
```bash
bash scripts/init-db.sh
```

It will prompt you for a username, display name, and PIN (hidden). No credentials are hardcoded.

## Project Structure

```
popping/
├── app.py                  # Main Flask app (routes + API)
├── database.py             # SQLite helpers
├── config.py               # Config (DB path, secret key)
├── popping.sql             # Database schema + seed data
├── requirements.txt        # Python dependencies
├── render.yaml             # Render Blueprint (optional)
├── static/
│   ├── css/style.css         # Modern styles
│   └── js/app.js             # Frontend logic + polling
└── templates/
    ├── base.html             # Layout
    ├── index.html            # Landing page (course selection)
    ├── login.html            # Student login for a course
    ├── instructor_login.html # Instructor login
    ├── instructor_courses.html # Instructor's course list
    ├── instructor.html       # Instructor control panel
    └── dashboard.html        # Student view
```

## User Flow

### Students

1. Visit the site → see list of active courses.
2. Click their course → enter Student ID + PIN.
3. Select a team (during **SETUP** phase).
4. Participate in discussion, peer grading, or team grading as phases change.

### Instructors

1. Click **Instructor Login** → enter username + PIN.
2. See list of your courses → click one to manage.
3. Control phases, manage students, set questions, select presenting teams.
4. Export CSV at the end of class.

## Course Flow

1. **SETUP** → Students log in and select teams.
2. **DISCUSSION** → Instructor posts a question; students discuss and peer-grade.
3. **COMPETITION** → Teams present one at a time (instructor selects active team).
4. **GRADING** → Other teams grade the presenting team.
5. **ENDED** → Instructor exports CSV and uploads to Canvas.

## Managing Courses

### Adding a New Course

Insert into the database (or via SQL directly):

```sql
INSERT INTO courses (name, code, semester, instructor_id) VALUES ('New Course', 'STAT 101', 'Spring 2026', 1);
INSERT INTO course_state (course_id, phase, active_team_id) VALUES (3, 'setup', NULL);
INSERT INTO teams (course_id, name, color) VALUES (3, 'Team A', '#ef4444'), (3, 'Team B', '#3b82f6');
```

### Adding Students

Use the instructor panel, or insert directly:

```sql
INSERT INTO students (course_id, student_id, name, pin) VALUES (1, 'netid123', 'Alice', '1234');
```

### Adding Instructors

```sql
INSERT INTO instructors (username, name, pin) VALUES ('prof2', 'Dr. Smith', 'smith2025');
```

## Data Isolation

Each course is fully isolated:
- Students, teams, grades, and responses are scoped to a `course_id`.
- Instructors can only access courses they own.
- Export downloads data for one course at a time.

## License

MIT
