DROP TABLE IF EXISTS discussion_responses;
DROP TABLE IF EXISTS team_reviews;
DROP TABLE IF EXISTS peer_reviews;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS teams;
DROP TABLE IF EXISTS course_state;

CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT DEFAULT '#4f46e5'
);

CREATE TABLE students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    pin TEXT NOT NULL,
    team_id INTEGER,
    is_instructor INTEGER DEFAULT 0,
    FOREIGN KEY (team_id) REFERENCES teams (id)
);

CREATE TABLE course_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    phase TEXT DEFAULT 'setup',
    active_team_id INTEGER,
    current_question TEXT,
    FOREIGN KEY (active_team_id) REFERENCES teams (id)
);

CREATE TABLE peer_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grader_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    criterion TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (grader_id) REFERENCES students (id),
    FOREIGN KEY (recipient_id) REFERENCES students (id),
    UNIQUE(grader_id, recipient_id, criterion)
);

CREATE TABLE team_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grader_team_id INTEGER NOT NULL,
    recipient_team_id INTEGER NOT NULL,
    criterion TEXT NOT NULL,
    score REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (grader_team_id) REFERENCES teams (id),
    FOREIGN KEY (recipient_team_id) REFERENCES teams (id),
    UNIQUE(grader_team_id, recipient_team_id, criterion)
);

CREATE TABLE discussion_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students (id)
);

INSERT INTO course_state (id, phase, active_team_id) VALUES (1, 'setup', NULL);

INSERT INTO teams (name, color) VALUES
    ('Team Alpha', '#ef4444'),
    ('Team Beta', '#3b82f6'),
    ('Team Gamma', '#10b981'),
    ('Team Delta', '#f59e0b'),
    ('Team Epsilon', '#8b5cf6');
