# 🍿 Popping

Multi-course interactive classroom team management and peer grading system.

Each course lives in its own folder with its own SQLite database. This keeps course data completely isolated and makes it easy to reset or archive individual courses.

## Features

- **Per-Course Databases**: Each course has its own folder (`data/{slug}/`) with its own `popping.db`.
- **Team Selection**: Students log in with ID + PIN and pick a team.
- **Live Roster**: Real-time view of who joined which team (3-second polling).
- **Discussion Phase**: Students respond to instructor questions.
- **Peer Grading**: Students grade teammates during discussion.
- **Competition Mode**: Teams present; instructor selects active team.
- **Team Grading**: Non-presenting teams grade the presenting team.
- **Instructor Panel**: Control phases, pick presenting team, manage students.
- **Data Export**: Download per-course grades & responses as CSV.

## Tech Stack

- **Backend**: Python + Flask
- **Database**: SQLite (one per course, in its own folder)
- **Frontend**: Vanilla JS + Jinja2 templates + modern CSS
- **Auth**: Simple session-based (ID + PIN for students; username + PIN for instructors)

## Quick Start (Local)

```bash
# 1. Clone
git clone https://github.com/rqzhu-aide/popping.git
cd popping

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create a course
bash scripts/create-course.sh
#   It will ask you for:
#   - Course slug (e.g. 432fall2026)
#   - Course name (e.g. "Basics of Statistical Learning")
#   - Course code (e.g. STAT 432)
#   - Semester (e.g. Fall 2026)
#   Then it creates the folder under data/{slug}/

# 4. Initialize the course database
cd data/432fall2026
bash init-db.sh
#   It will prompt you for instructor credentials

# 5. Run the app
cd ../..
flask --app app run
```

Open http://127.0.0.1:5000

## Course Folder Structure

After creating a course, you get:

```
data/432fall2026/
├── course.json          # Course metadata (name, code, teams, etc.)
├── init-db.sh           # Script to reset/reinitialize this course's DB
└── popping.db           # The SQLite database (created by init-db.sh)
```

### To Reset a Course (change instructor password, wipe student data)

```bash
cd data/432fall2026
bash init-db.sh
```

This will:
- Delete `popping.db`
- Recreate the schema
- Prompt you for new instructor credentials
- Keep the same course name, code, semester, and team structure from `course.json`

### To Create Another Course

```bash
bash scripts/create-course.sh
```

Then `cd data/{new-slug} && bash init-db.sh`

## Deploy to Render

1. Push this repo to GitHub.
2. In Render, create a new **Web Service** and connect this repo.
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn app:app`
5. Add environment variable: `SECRET_KEY` = (any random string)
6. Add a **Disk** (Settings → Disks):
   - **Name:** `data`
   - **Mount Path:** `/data`
   - **Size:** 1 GB
7. After first deploy, open Render Shell and init your courses:
   ```bash
   # STAT 432
   cd data/432fall2026
   bash init-db.sh
   # Enter instructor credentials when prompted

   # STAT 546
   cd ../546fall2026
   bash init-db.sh
   # Enter instructor credentials when prompted
   ```

> **Why the disk?** On Render, anything not committed to git is ephemeral. The disk at `/data` persists across redeploys, so your SQLite databases survive code updates.

## Project Structure

```
popping/
├── app.py                  # Main Flask app (routes + API)
├── config.py               # Config (data dir, secret key)
├── database.py             # SQLite helpers (per-course connections)
├── popping.sql             # Database schema (no seed data)
├── requirements.txt        # Python dependencies
├── render.yaml             # Render Blueprint (optional)
├── scripts/
│   ├── create-course.sh    # Create a new course folder + course.json
│   └── init-course-db.py   # Python helper called by per-course init-db.sh
├── data/                   # Course configs (committed to git)
│   └── 432fall2026/
│       ├── course.json
│       └── init-db.sh
├── static/
│   ├── css/style.css
│   └── js/app.js
└── templates/
    ├── base.html
    ├── index.html
    ├── login.html
    ├── instructor_login.html
    ├── instructor.html
    └── dashboard.html
```

> On Render with a mounted disk, the actual SQLite databases live under `/data/{slug}/popping.db` (persistent), while the course configs (`course.json`, `init-db.sh`) stay in the git repo under `data/{slug}/`.

## User Flow

### Students

1. Visit the site → see list of active courses.
2. Click **Student Login** on your course → enter Student ID + PIN.
3. Select a team (during **SETUP** phase).
4. Participate in discussion, peer grading, or team grading as phases change.

### Instructors

1. Click **Instructor Login** on your course → enter username + PIN.
2. Go directly to the control panel for that course.
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

```bash
bash scripts/create-course.sh
```

Follow the prompts. Then `cd data/{slug}` and `bash init-db.sh`.

### Customizing Teams

Edit the `teams` array in `data/{slug}/course.json`, then run `bash init-db.sh` to recreate the database.

### Adding Students

Use the instructor panel, or insert directly into the course database:

```bash
sqlite3 data/432fall2026/popping.db
```

```sql
INSERT INTO students (course_id, student_id, name, pin) VALUES (1, 'netid123', 'Alice', '1234');
```

## Data Isolation

- Each course folder has its own `popping.db`.
- No global database. Courses are discovered by scanning the `data/` directory.
- Deleting a course folder completely removes that course's data.
- Resetting a course (running `init-db.sh`) only affects that one course.

## License

MIT
