DROP TABLE IF EXISTS discussion_responses;
DROP TABLE IF EXISTS team_reviews;
DROP TABLE IF EXISTS peer_reviews;
DROP TABLE IF EXISTS course_state;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS teams;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS instructors;

CREATE TABLE instructors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    pin TEXT NOT NULL
);

CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT,
    semester TEXT,
    slug TEXT NOT NULL UNIQUE,
    instructor_id INTEGER NOT NULL,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (instructor_id) REFERENCES instructors (id)
);

CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    color TEXT DEFAULT '#4f46e5',
    FOREIGN KEY (course_id) REFERENCES courses (id),
    UNIQUE(course_id, name)
);

CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    student_id TEXT NOT NULL,
    name TEXT,
    pin TEXT NOT NULL,
    team_id INTEGER,
    last_login_at TIMESTAMP,
    last_active_at TIMESTAMP,
    last_team_joined_at TIMESTAMP,
    last_team_id INTEGER,
    FOREIGN KEY (course_id) REFERENCES courses (id),
    FOREIGN KEY (team_id) REFERENCES teams (id),
    UNIQUE(course_id, student_id)
);

CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    question_num INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id)
);

CREATE TABLE course_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL UNIQUE,
    phase TEXT DEFAULT 'setup' CHECK(phase IN ('setup', 'discussion', 'competition', 'grading', 'ended')),
    max_teams INTEGER DEFAULT 5,
    max_members_per_team INTEGER DEFAULT 10,
    teams_locked INTEGER DEFAULT 0,
    discussion_week INTEGER DEFAULT 1,
    session_started_at TIMESTAMP,
    active_team_id INTEGER,
    active_question_id INTEGER,
    current_question TEXT,
    presentation_started_at TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id),
    FOREIGN KEY (active_team_id) REFERENCES teams (id),
    FOREIGN KEY (active_question_id) REFERENCES questions (id)
);

CREATE TABLE peer_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    grader_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    criterion TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id),
    FOREIGN KEY (grader_id) REFERENCES students (id),
    FOREIGN KEY (recipient_id) REFERENCES students (id),
    UNIQUE(course_id, grader_id, recipient_id, criterion)
);

CREATE TABLE team_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    grader_team_id INTEGER NOT NULL,
    recipient_team_id INTEGER NOT NULL,
    criterion TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id),
    FOREIGN KEY (grader_team_id) REFERENCES teams (id),
    FOREIGN KEY (recipient_team_id) REFERENCES teams (id),
    UNIQUE(course_id, grader_team_id, recipient_team_id, criterion)
);

CREATE TABLE discussion_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id),
    FOREIGN KEY (student_id) REFERENCES students (id)
);
