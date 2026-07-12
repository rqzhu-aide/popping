DROP TABLE IF EXISTS discussion_responses;
DROP TABLE IF EXISTS presentation_ratings;
DROP TABLE IF EXISTS discussion_selections;
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
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
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
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL,
    UNIQUE(course_id, student_id)
);

CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    question_num INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    title TEXT,
    content TEXT,
    week_num INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
);

CREATE TABLE course_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL UNIQUE,
    phase TEXT DEFAULT 'setup' CHECK(phase IN ('setup', 'discussion', 'competition', 'ended')),
    max_teams INTEGER DEFAULT 8,
    max_members_per_team INTEGER DEFAULT 5,
    teams_locked INTEGER DEFAULT 0,
    discussion_week INTEGER DEFAULT 1,
    session_started_at TIMESTAMP,
    active_team_id INTEGER,
    active_question_id INTEGER,
    current_question TEXT,
    presentation_started_at TIMESTAMP,
    presentation_time_cap INTEGER DEFAULT 300,
    presentation_remaining INTEGER,
    poll_active INTEGER DEFAULT 0,
    poll_question_key TEXT,
    poll_started_at TIMESTAMP,
    presentation_history TEXT DEFAULT '[]',
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (active_team_id) REFERENCES teams (id) ON DELETE SET NULL,
    FOREIGN KEY (active_question_id) REFERENCES questions (id) ON DELETE SET NULL
);

CREATE TABLE peer_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    grader_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    criterion TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (grader_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES students (id) ON DELETE CASCADE,
    UNIQUE(course_id, grader_id, recipient_id, criterion)
);

CREATE TABLE discussion_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE
);

CREATE TABLE presentation_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    q1_developed INTEGER CHECK(q1_developed >= 1 AND q1_developed <= 5),
    q2_easy INTEGER CHECK(q2_easy >= 1 AND q2_easy <= 5),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    UNIQUE(course_id, student_id, question_key)
);

CREATE TABLE discussion_selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    UNIQUE(course_id, student_id, question_key)
);

CREATE TABLE appendix_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    week_num INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
);
