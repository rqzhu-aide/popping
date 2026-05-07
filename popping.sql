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
    name TEXT NOT NULL,
    pin TEXT NOT NULL,
    team_id INTEGER,
    FOREIGN KEY (course_id) REFERENCES courses (id),
    FOREIGN KEY (team_id) REFERENCES teams (id),
    UNIQUE(course_id, student_id)
);

CREATE TABLE course_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL UNIQUE,
    phase TEXT DEFAULT 'setup',
    active_team_id INTEGER,
    current_question TEXT,
    FOREIGN KEY (course_id) REFERENCES courses (id)
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

-- Seed data
INSERT INTO instructors (username, name, pin) VALUES ('instructor', 'Instructor', 'admin123');

INSERT INTO courses (name, code, semester, instructor_id) VALUES
    ('Introduction to Data Science', 'STAT 430', 'Fall 2025', 1),
    ('Machine Learning', 'STAT 542', 'Fall 2025', 1);

INSERT INTO teams (course_id, name, color) VALUES
    (1, 'Team Alpha', '#ef4444'),
    (1, 'Team Beta', '#3b82f6'),
    (1, 'Team Gamma', '#10b981'),
    (1, 'Team Delta', '#f59e0b'),
    (1, 'Team Epsilon', '#8b5cf6'),
    (2, 'Team Red', '#ef4444'),
    (2, 'Team Blue', '#3b82f6'),
    (2, 'Team Green', '#10b981');

INSERT INTO course_state (course_id, phase, active_team_id) VALUES
    (1, 'setup', NULL),
    (2, 'setup', NULL);
