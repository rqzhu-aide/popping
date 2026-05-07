# 🍿 Popping

Interactive classroom team management and peer grading system.

## Features

- **Team Selection**: Students log in with ID + PIN and pick a team.
- **Live Roster**: Real-time view of who joined which team (3-second polling).
- **Discussion Phase**: Students respond to instructor questions.
- **Peer Grading**: Students grade teammates during discussion.
- **Competition Mode**: Teams present; instructor selects active team.
- **Team Grading**: Non-presenting teams grade the presenting team.
- **Instructor Panel**: Control phases, pick presenting team, manage students.
- **Data Export**: Download all grades & responses as CSV.

## Tech Stack

- **Backend**: Python + Flask
- **Database**: SQLite (zero-config, file-based)
- **Frontend**: Vanilla JS + Jinja2 templates + modern CSS
- **Auth**: Simple session-based (ID + PIN)

## Quick Start (Local)

```bash
# 1. Clone
git clone https://github.com/rqzhu-aide/popping.git
cd popping

# 2. Install dependencies
pip install -r requirements.txt

# 3. Initialize database
flask --app app init-db

# 4. Seed instructor account
flask --app app seed

# 5. Run
flask --app app run
```

Open http://127.0.0.1:5000 and log in as:
- **Instructor**: `instructor` / `admin123`

## Deploy to Render

1. Push this repo to GitHub.
2. In Render, create a new **Web Service** and connect this repo.
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn app:app`
5. Add environment variable: `SECRET_KEY` = (any random string)
6. After first deploy, run in Render Shell:
   ```bash
   flask --app app init-db
   flask --app app seed
   ```

## Project Structure

```
popping/
├── app.py              # Main Flask app (routes + API)
├── database.py         # SQLite helpers
├── config.py           # Config (DB path, secret key)
├── popping.sql         # Database schema + seed data
├── requirements.txt    # Python dependencies
├── render.yaml         # Render Blueprint (optional)
├── static/
│   ├── css/style.css     # Modern styles
│   └── js/app.js         # Frontend logic + polling
└── templates/
    ├── base.html         # Layout
    ├── login.html        # Login page
    ├── dashboard.html    # Student view
    └── instructor.html   # Instructor control panel
```

## Course Flow

1. **SETUP** → Students log in and select teams.
2. **DISCUSSION** → Instructor posts a question; students discuss and peer-grade.
3. **COMPETITION** → Teams present one at a time (instructor selects active team).
4. **GRADING** → Other teams grade the presenting team.
5. **ENDED** → Instructor exports CSV and uploads to Canvas.

## Adding Students

Use the instructor panel or insert directly into SQLite:

```sql
INSERT INTO students (student_id, name, pin) VALUES ('netid123', 'Alice', '1234');
```

## License

MIT
