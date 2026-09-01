# 🍿 Popping

Multi-course interactive classroom team management and peer grading system.

Each course lives in its own folder with its own SQLite database. This keeps course data completely isolated and makes it easy to reset or archive individual courses.

## Features

- **Per-Course Databases**: Each course has its own folder (`data/{slug}/`) with its own `popping.db`.
- **Team Selection**: Students log in with ID + PIN and pick a team.
- **Live Roster**: Real-time view of who joined which team (3-second polling).
- **Group Discussion**: Students discuss instructor questions with their teams.
- **Teammate Recognition**: Students give thumbs-up to teammates during discussion.
- **Present and Challenge**: Teams present, peers challenge, and the instructor
  coordinates both.
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

`v1.1.0` added durable participation events. `v1.2.0` added a separate student
display name while preserving the instructor-uploaded roster name. `v1.3.0`
adds normalized Weekly Hero summaries and award recipients. Each schema update
requires an explicit offline migration or a course reset. Website releases
`v1.3.x` require database schema `v1.3.0`; do not start those web workers against
an older course database until its migration has succeeded.

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
└── week-1-questions.md  # Questions for both question-based phases

data/432fall2026/
├── popping.db           # The SQLite database (created by init-db.sh)
└── questions/           # Instructor-uploaded weekly files, when present
    └── week-1-questions.md
```

### Weekly Question File

For each week, provide one UTF-8 Markdown file named
`week-N-questions.md`, where `N` is the week number. You can commit it under
`classes/{slug}/` and deploy it through GitHub, or upload it from the instructor
Setup page. The application loads a bundled GitHub file automatically, and the
browser formats its Markdown, equations, and fenced code blocks. The two
question-based phases read the exact same ordered set from this file: Group
Discussion and Present and Challenge. The presentation index is the parsed file
order, so there is no separate
`index.md` or `qNN.html` source.

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
each `title` must be unique within the file. Keep one Markdown file per week.
Do not split questions into separate files and do not provide raw HTML. The
single Markdown file gives the server one atomic, ordered document to validate
and gives the browser safe Markdown, equation, and code rendering.

The same strict parser drives upload preview, saving, readiness, and the two
question-based phases: Group Discussion and Present and Challenge. If any block
is malformed, the whole upload is rejected and the previous valid source
remains in place. A confirmed valid upload is saved
under `data/{slug}/questions/week-N-questions.md` and overrides the bundled
file with the same name under `classes/{slug}/`, including after later GitHub
deploys. Do not use both sources for the same week unless that override is
intentional. Legacy `weekN/index.md` and `qNN.html` files are ignored.

## Server Terminal Operations

Run these commands from the deployed repository root, which is the directory
containing `app.py`. In Render Shell, this is normally
`/opt/render/project/src`:

```bash
cd /opt/render/project/src
printf 'DATA_DIR=%s\n' "$DATA_DIR"
```

On Render, `DATA_DIR` must print `/data`. Stop if it does not. Only files under
that mounted disk persist across restarts and deploys. Course definitions and
bundled weekly questions under `classes/` must come from GitHub.

> Do not run `scripts/create-course.sh` only in Render Shell. It writes to the
> deployed source checkout, so those changes disappear on the next deploy.

### Set Up a New Course

1. In a local, source-controlled checkout, run:

   ```bash
   bash scripts/create-course.sh
   ```

   Use a simple course slug made from letters, digits, hyphens, or underscores,
   such as `546fall2026`.
2. Keep the generated course set to `active: false`. Add any bundled
   `week-N-questions.md` files, commit the course folder to GitHub, and wait for
   the inactive course to deploy.
3. From the server shell at the repository root, initialize the persistent
   database:

   ```bash
   bash classes/546fall2026/init-db.sh
   ```

4. Enter the instructor username, display name, and PIN when prompted. The PIN
   must contain 4 to 32 ASCII digits (`0-9`).
5. After initialization succeeds, change `active` to `true` in GitHub, commit
   and push, and wait for the next deploy. Add students through the instructor
   panel after signing in.

Do not rerun `init-db.sh` just to change a forgotten PIN. That command resets
the entire course database. Use the PIN command below instead.

### To Change or Recover the Instructor PIN

The instructor password is called a PIN in the application. No website login is
required, so this also works when the PIN is forgotten. From the server shell,
run the single-course form by default:

```bash
python3 scripts/set-instructor-pin.py 432fall2026
```

The script prompts twice for the new PIN (hidden input, 4-32 digits using only
`0-9`, matching the login form) and updates each course database in its own
transaction. The new PIN is active immediately; no restart is needed. Existing
instructor sessions for a changed course are signed out on their next request
and must log in with the new PIN.

Omitting the course slug changes every course database to the same new PIN. Use
that form only when this is intentional:

```bash
python3 scripts/set-instructor-pin.py
```

Any older instructor PIN outside this policy, including a 1-3 digit PIN, must
be replaced with this script before the instructor can log in. The application
does not pad or otherwise transform an existing PIN.

### To Look Up a Student PIN

Student PINs are stored in the course database but are intentionally not sent
to either the instructor or student browser. To look up one student from
Render Shell, run:

```bash
python3 scripts/check-student-pin.py 546fall2026 STUDENT_ID
```

Replace the course slug and student ID as needed. Put quotation marks around a
student ID containing spaces. ASCII letter case is ignored, matching website
login behavior. The result shows the canonical student ID, whether the account
is active or removed, and the PIN. A removed account cannot log in, but its PIN
and participation history remain available.

The script is strictly read-only. It does not change the account, participation
history, PIN, or active sessions, so the course can remain live and no restart
is needed. It can show only one student per command. Because the plaintext PIN
appears in terminal scrollback, do not run it while sharing your screen or copy
its output into logs or messages. The PIN itself is not a command argument, so
it is not added to shell command history.

### To Reset a Course (wipe student data and start over)

Do not run a reset while students or instructors can reach that course.

For local operation, stop every Flask or Gunicorn worker, run the command, and
type `SERVICE STOPPED` when prompted:

```bash
bash classes/432fall2026/init-db.sh
```

For Render, keep the service running so Render Shell and `/data` remain
available:

1. Change only the affected course to `active: false` in GitHub and deploy.
2. Confirm the course is absent from the landing page.
3. Open Render Shell, run the command above, and type `COURSE OFFLINE` when
   prompted. The script verifies that the deployed `course.yaml` is inactive.
4. After the reset succeeds, change the course back to `active: true`, commit,
   and deploy again.

The reset will:

- Build and validate a replacement database before touching the current one
- Prompt you for new instructor credentials
- Require the course slug and an offline-safety confirmation
- Save a verified copy of the previous database under `data/{slug}/init-backups/`
- Atomically replace `popping.db` only after the candidate and backup pass validation
- Keep the same course name, code, semester, and team structure from `course.yaml`

### Upgrade an Existing Course Database to v1.3.0

This is an offline operation for each affected course. Complete it between
classes. If the database contains a completed Week 1 that must be preserved,
confirm that its session is in **Ended** before taking the course offline. Do
not use **Reset Course Data** or `init-db.sh` for this upgrade because a reset
removes the Week 1 source records needed for the Weekly Hero backfill.

For a local deployment, end any active session, stop every Flask or Gunicorn
worker, run the command below, and type `SERVICE STOPPED`.

For Render:

1. While the old code is live, verify that each completed session to preserve
   is in **Ended**. If the application instead shows Setup with saved activity,
   stop and investigate rather than resetting or forcing the migration.
2. Change every course that needs migration to `active: false` and deploy.
   Confirm those courses disappear from the landing page.
3. Deploy the new code while those courses remain inactive. This keeps the
   schema health check from blocking the deployment before migration.
4. Open Render Shell and run, for each course:

   ```bash
   python3 scripts/migrate-course-db.py 432fall2026
   ```

5. Type the course slug, then type `COURSE OFFLINE`. The script verifies that
   the deployed course configuration is inactive.
6. Preview and apply the Weekly Hero backfill as described below.
7. After every required migration and backfill succeeds, reactivate the courses
   in GitHub and deploy again.

The command validates the course database, creates a verified snapshot under
`data/{slug}/migration-backups/`, and applies every required schema change in
one transaction. A `v1.0.x` database is upgraded through all migration steps.
The `v1.1.0` migration deliberately does not infer participation from older
ratings. The `v1.2.0` migration adds a nullable `students.display_name` column.
The `v1.3.0` migration adds normalized weekly result summaries and award
recipients without rewriting ratings or participation records.

Website releases `v1.3.x` use database schema and export format `v1.3.0`.
Records written by an older compatibility line remain available through
**Download Legacy Data** after the upgrade. A full course reset starts with an
empty current-version database instead.

After migration, preview and save a Weekly Hero summary for each completed
older week. For example, these commands reconstruct Week 1 from its preserved
source rows, then save only the derived summary:

```bash
python3 scripts/backfill-weekly-heroes.py 432fall2026 1
python3 scripts/backfill-weekly-heroes.py 432fall2026 1 --apply
```

The first command is read-only. Review its exact scores, recipients, source
versions, fingerprint, and participant-coverage result before running
`--apply`. Apply only when participant coverage is complete and the calculated
team rankings and challenger recipients are plausible for that week. Apply
requires the course slug and the same `COURSE OFFLINE` or `SERVICE STOPPED`
confirmation used by other database maintenance commands. It is idempotent
when the saved fingerprint already matches. Use `--apply --replace` only after
reviewing a changed preview. The command never updates or deletes the source
ratings, challenges, participants, roster, or teams. It writes the weekly
summary and increments the roster refresh version.

Stop without applying, reinitializing, or resetting if migration or preview
reports a schema or activity-state error, mixed source versions, incomplete
participant coverage, an integrity failure, or results you cannot reconcile.
Resolve the cause first so the preserved Week 1 records remain available.

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
   bash classes/432fall2026/init-db.sh
   # Enter instructor credentials when prompted

   # STAT 546
   bash classes/546fall2026/init-db.sh
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
2. Click **Student Login** on your course, enter Student ID and PIN, and
   optionally choose a display name. Leaving it blank keeps the saved display
   name. Student pages show one name only. Instructor pages show that name with
   the student ID.
3. Select a team during **Setup**.
4. Participate in discussion, peer grading, or team grading as phases change.

### Instructors

1. Click **Instructor Login** on your course → enter username + PIN.
2. Go directly to the control panel for that course.
3. Control phases, manage students, set questions, select presenting teams.
4. Export **Current Week Results** at the end of class. Download legacy data separately when it is available.

## Course Flow

1. **Setup** → Students log in and select teams.
2. **Group Discussion** → Instructor posts a question; students discuss and peer-grade.
3. **Present and Challenge** → Teams present one at a time; students from other
   teams may challenge.
4. **Present and Challenge** → Non-presenting students rate the active
   presentation and selected challengers.
5. **End Session** → Instructor exports Current Week Results and uploads the workbook
   to Canvas.

## Managing Courses

### Adding a New Course

Follow [Set Up a New Course](#set-up-a-new-course). The two-stage inactive
deployment prevents the website from advertising a course before its persistent
database exists.

### Customizing Teams

Edit the `teams` array in `classes/{slug}/course.yaml`. Applying the new team
structure requires a complete course reset with `init-db.sh`, which wipes the
roster, sessions, ratings, and participation history. Follow
[Safe Database Maintenance](#safe-database-maintenance) before running it.

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

### Managing Students

Use **Tools → Download Student Roster** to download every active student's
uploaded ID, uploaded name, and PIN in the same three-column CSV format used
for roster uploads. Removed students are not included, and self-provided
display names are kept separate from this administrative roster. The download
contains plaintext PINs, so store and share it securely. You can edit this file
and upload either the complete roster or only the rows that need changes.

During **Setup**, use **Tools → Upload Student Roster** to merge a CSV into the
course roster. The file may contain the complete roster or only students who
need to be added or updated. It must use this header:

```csv
student_id,name,pin
```

Each listed row behaves as follows:

- An existing student ID updates that student's PIN and any nonblank uploaded
  roster name. A blank name leaves the current uploaded name unchanged. The
  database identity, current team, self-provided display name, and all
  participation history are preserved.
- A new student ID creates an active student with no team assignment.
- A student who is not listed is left completely unchanged. A partial file
  therefore does not remove students and does not need to repeat the full
  roster.
- Each listed row must contain a student PIN of exactly four digits. Changing
  a PIN signs that student out, and the student must sign in with the new PIN.

Review the confirmation preview before selecting **Apply Updates**. To remove
a student, use **Remove** in **Student Management** instead of omitting the
student from a CSV. Removal prevents login and clears the current team while
retaining participation history. The **Add or Update Student** controls in the
same section are suitable for one student at a time.

Do not add, update, or remove students with the `sqlite3` command-line tool.

### Safe Database Maintenance

Never edit, replace, initialize, migrate, or restore a course's `popping.db`
while that course is reachable. Concurrent requests can otherwise cause failed
requests, stale data, or data loss.

For local maintenance, stop all Flask or Gunicorn processes, run the command,
type `SERVICE STOPPED`, restart the service, and verify both logins.

For Render maintenance, do not suspend the service before using Render Shell.
A shell attaches to a running instance, and the persistent disk is available
to that running instance. Instead:

1. Set the affected course to `active: false` in GitHub and deploy.
2. Confirm that the course is absent from the landing page.
3. Open Render Shell and run the appropriate command:

   ```bash
   python scripts/restore-course-db.py 432fall2026 /path/to/backup.db
   python scripts/migrate-course-db.py 432fall2026
   python scripts/backfill-weekly-heroes.py 432fall2026 1
   python scripts/backfill-weekly-heroes.py 432fall2026 1 --apply
   ```

4. Type `COURSE OFFLINE`. The script checks the deployed inactive setting.
5. Reactivate the course in GitHub, deploy, and verify instructor and student
   login before class.

The confirmation is a safety boundary, not a substitute for the corresponding
service stop or inactive-course deployment. Render Maintenance Mode alone is
not sufficient: it blocks public traffic at the edge, but it does not make the
application reject that course or exclude its database from readiness checks.

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

- Each course has its own `data/{slug}/popping.db`.
- No global database. Courses are discovered by scanning the `classes/` directory for active `course.yaml` files.
- Deleting `classes/{slug}/` removes the course definition after deployment,
  but it does not delete the persistent database under `data/{slug}/` or
  `/data/{slug}/` on Render.
- Completely deleting a course requires separately removing both its GitHub
  course folder and its persistent data directory.
- Resetting a course (running `init-db.sh`) only affects that one course.

## License

AGPLv3. See [LICENSE](LICENSE) (GNU Affero General Public License v3.0).
