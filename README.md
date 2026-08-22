# 🍿 Popping

Multi-course interactive classroom team management and peer grading system.

Each course lives in its own folder with its own SQLite database. This keeps course data completely isolated and makes it easy to reset or archive individual courses.

## Features

- **Per-Course Databases**: Each course has its own folder (`data/{slug}/`) with its own `popping.db`.
- **Team Selection**: Students log in with ID + PIN and pick a team.
- **Live Roster**: Real-time view of who joined which team (3-second polling).
- **Discussion Phase**: Students discuss instructor questions with their teams.
- **Teammate Recognition**: Students give thumbs-up to teammates during discussion.
- **Competition Mode**: Teams present; instructor selects active team.
- **Presentation Ratings**: Non-presenting students rate the active presentation.
- **Instructor Panel**: Control phases, pick presenting team, manage students.
- **Participation History**: Instructors can compare course-wide presentation-team
  and challenger turn counts when choosing participants.
- **Data Export**: Download versioned results for the current or any previous
  week, a dedicated full-roster participation snapshot, or older and
  unclassified legacy data.

## Tech Stack

- **Backend**: Python + Flask
- **Database**: SQLite (one per course, in its own folder)
- **Frontend**: Vanilla JS + Jinja2 templates + modern CSS
- **Auth**: Simple session-based (ID + PIN for students; username + PIN for instructors)

## Versioning and Data Compatibility

The versioned baseline is `v1.0.0`. Website-only changes increment the patch
number. Any database structure change increments at least the minor number.
Data is current when its major and minor numbers match the database schema;
older compatibility lines remain available through **Download Legacy Data**.
See [VERSIONING.md](VERSIONING.md) for the policy and [CHANGELOG.md](CHANGELOG.md)
for the release history.

`v1.1.0` is the first schema update. It adds durable participation events and
requires an explicit offline migration for every existing `v1.0.x` course
database. Do not start `v1.1.0` web workers against an existing database until
the migration below has succeeded.

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
#   Then it creates the folder under classes/{slug}/

# 4. Initialize the course database
cd classes/432fall2026
bash init-db.sh
#   It will prompt you for instructor credentials

# 5. Publish the initialized course locally
#   Change active: false to active: true in course.yaml

# 6. Run the app
cd ../..
flask --app app run
```

Open http://127.0.0.1:5000

## Interactive Classroom Simulator

To observe the instructor workflow with 40 synthetic students, use the
isolated local simulator. The instructor remains under human control while
the script drives only student actions.

```powershell
python -m pip install -r requirements-load-test.txt
python scripts\simulate_classroom.py
```

See [SIMULATED_CLASSROOM.md](SIMULATED_CLASSROOM.md) for credentials, expected
counts, controls, and safety limits.

## Course Folder Structure

After creating a course, you get:

```
classes/432fall2026/
├── course.yaml          # Course metadata (name, code, active teams, etc.)
├── init-db.sh           # Script to reset/reinitialize this course's DB
└── week-1-questions.md  # Bundled questions for both classroom phases

data/432fall2026/
├── popping.db           # The SQLite database (created by init-db.sh)
└── questions/           # Instructor-uploaded weekly files, when present
    └── week-1-questions.md
```

### Weekly Question File

Before class, upload one UTF-8 Markdown file from the instructor Setup page.
Select a positive week number and choose a `.md` file. Bundled fallback files
use the name `week-N-questions.md`, where `N` is the week number. The discussion
phase and group presentation phase read the exact same ordered set from this
file. The presentation index is the parsed file order, so there is no
separate `index.md` or `qNN.html` source.

Each question is one YAML-frontmatter block followed by its Markdown content:

```markdown
---
id: bagging-vs-boosting
title: "Bagging vs Boosting"
---

Explain the key differences between bagging and boosting.

---
id: bias-variance
title: "Bias-Variance Decomposition"
---

Explain how the number of trees affects random-forest variance.
```

Question order is the block order. Each `id` must be unique and stable, and
each `title` must be unique within the file. The upload is previewed before it
is confirmed. A confirmed upload is saved under
`data/{slug}/questions/week-N-questions.md` and overrides the bundled file with
the same name under `classes/{slug}/`. Legacy `weekN/index.md` and `qNN.html`
files are ignored.

### To Reset a Course (change instructor password, wipe student data)

Stop every Flask or Gunicorn worker before resetting an existing course, then
restart the service after the script finishes. On Render, suspend the web
service first. Do not run the reset while students or instructors are using the
site.

```bash
cd classes/432fall2026
bash init-db.sh
```

This will:

- Build and validate a replacement database before touching the current one
- Prompt you for new instructor credentials
- Require the course slug and `SERVICE STOPPED` confirmations when replacing an existing database
- Save a verified copy of the previous database under `data/{slug}/init-backups/`
- Atomically replace `popping.db` only after the candidate and backup pass validation
- Keep the same course name, code, semester, and team structure from `course.yaml`

### To Create Another Course

```bash
bash scripts/create-course.sh
```

The generated course starts with `active: false`. For local use, initialize its
database, change `active` to `true` in `course.yaml`, then start the app. For
Render, follow the inactive deployment sequence below.

### Upgrade an Existing v1.0 Course to v1.1.0

This is an offline operation. Complete it between classes for every existing
course database:

1. End or reset any active classroom session, then stop every Flask or Gunicorn
   worker. On Render, suspend the web service and wait for all workers to stop.
2. Make the `v1.1.0` code available on the same machine and persistent disk
   while keeping the web workers stopped.
3. From the repository root, run:

   ```bash
   python scripts/migrate-course-db.py 432fall2026
   ```

4. Type the course slug, then type `SERVICE STOPPED` when prompted.
5. Repeat for each course. Start or resume the `v1.1.0` service only after every
   required migration succeeds.

The command validates the course database, creates a verified snapshot under
`data/{slug}/migration-backups/`, and applies the schema change in one
transaction. The migration deliberately does not infer participation from
`v1.0.x` ratings. Presentation-team and challenger counts begin with activity
recorded by `v1.1.0`.

Normal web traffic and `/healthz` only validate an exact current schema. They
never migrate or repair a persistent course database.

## Deploy to Render

1. Keep every course whose database is not yet on the Render disk set to
   `active: false`, then push this repo to GitHub. This includes courses made by
   `scripts/create-course.sh`.
2. In Render, create a new **Web Service** and connect this repo.
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `gunicorn app:app --workers 3 --threads 8 --timeout 120`
5. Add environment variable: `SECRET_KEY` = (any random string)
6. Add a **Disk** (Settings → Disks):
   - **Name:** `data`
   - **Mount Path:** `/data`
   - **Size:** 1 GB
7. After the inactive configuration deploys successfully, open Render Shell and
   initialize each new course database:
   ```bash
   # STAT 432
   cd classes/432fall2026
   bash init-db.sh
   # Enter instructor credentials when prompted

   # STAT 546
   cd ../546fall2026
   bash init-db.sh
   # Enter instructor credentials when prompted
   ```
8. Confirm each initialization succeeds. Change that course's `active` field to
   `true` in GitHub, commit and push the change, and wait for Render to deploy
   again. The course becomes public only after its database is ready.

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
│   ├── create-course.sh    # Create a new course folder + course.yaml
│   └── init-course-db.py   # Python helper called by per-course init-db.sh
├── classes/                # Course configs and authored questions (committed to git)
│   └── 432fall2026/
│       ├── course.yaml
│       ├── init-db.sh
│       └── week-1-questions.md
├── data/                   # Runtime SQLite databases (not committed)
│   └── 432fall2026/
│       ├── popping.db
│       └── questions/
│           └── week-1-questions.md
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

> On Render with a mounted disk, the SQLite database and instructor-uploaded
> question files live under `/data/{slug}/` and persist across deploys. Course
> configs and bundled fallback question files stay in the git repo under
> `classes/{slug}/`.

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
4. Export **Current Week Results** at the end of class. Download legacy data separately when it is available.

## Course Flow

1. **SETUP** → Students log in and select teams.
2. **DISCUSSION** → Instructor posts a question; students discuss and peer-grade.
3. **COMPETITION** → Teams present one at a time (instructor selects active team).
4. **COMPETITION** → Non-presenting teams rate the active presentation.
5. **ENDED** → Instructor exports Current Week Results and uploads the workbook to Canvas.

## Managing Courses

### Adding a New Course

```bash
bash scripts/create-course.sh
```

The scaffold deliberately uses `active: false`. Commit and deploy it in that
state, initialize the database in Render Shell, then change `active` to `true`
and deploy again. For local use, initialize first and activate the course before
starting the app.

### Customizing Teams

Edit the `teams` array in `classes/{slug}/course.yaml`, then run `bash init-db.sh` to recreate the database.

### Tuning the Rating Window

By default each presentation rating poll stays open for **40 seconds**. Add an optional `poll_duration` field to `course.yaml` to change it for a course (clamped to 5 to 300 seconds):

```yaml
poll_duration: 45
```

This is read live (no `init-db.sh` needed), so you can lengthen it for a harder question or a larger class mid-session.

When the timer expires or the instructor selects **Stop Poll**, student rating
controls close immediately. The server then allows three seconds for requests
that already arrived to finish committing. During that short interval, the
instructor sees **Saving final ratings...** and presentation-changing controls
stay disabled. The same close protects active challenger ratings.

### Adding Students

Use **Upload Student Roster** or the student-management controls in the
instructor panel. Do not add students with the `sqlite3` command-line tool.

### Safe Database Maintenance

Never edit, replace, initialize, or restore a course's `popping.db` while the
web service is running. The running workers keep database connections open, so
changing the file underneath them can cause failed requests, stale data, or
data loss.

For an initialization, reset, or restore:

1. Stop all local Flask or Gunicorn processes. On Render, suspend the web
   service and wait for all workers to stop.
2. Run the appropriate command. For a restore from the repository root, use:

```bash
python scripts/restore-course-db.py 432fall2026 /path/to/backup.db
```

For the required `v1.0.x` to `v1.1.0` schema migration, use:

```bash
python scripts/migrate-course-db.py 432fall2026
```

3. Restart the service and verify instructor and student login before class.

The initialization and restore scripts ask you to confirm that the service is
stopped. That confirmation is a safety check, not a substitute for actually
stopping every worker.

### Complete Off-Disk Backups

Create a verified course bundle in an explicitly supplied directory outside
`DATA_DIR`:

```bash
python scripts/backup-course.py create 432fall2026 /path/on/another-disk/popping-backups
```

The bundle contains a consistent SQLite snapshot, persistent uploaded question
and appendix files, and a SHA-256 manifest. The manifest records the website,
database schema, export format, contained data versions, and unclassified-data
status. Verify any retained
copy with:

```bash
python scripts/backup-course.py verify /path/to/popping-432fall2026-YYYYMMDDTHHMMSSZ.zip
```

The tool does not provide encryption or upload to a storage provider. Because
the database contains plaintext PINs, use an encrypted or access-controlled
destination. See [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) for bundle
contents, exact recovery steps, and the remaining provider-specific setup.

## Data Isolation

- Each course folder has its own `popping.db`.
- No global database. Courses are discovered by scanning the `classes/` directory for active `course.yaml` files.
- Deleting a course folder completely removes that course's data.
- Resetting a course (running `init-db.sh`) only affects that one course.

## License

AGPLv3. See [LICENSE](LICENSE) (GNU Affero General Public License v3.0).
