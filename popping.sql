DROP TABLE IF EXISTS discussion_responses;
DROP TABLE IF EXISTS teammate_thumbs;
DROP TABLE IF EXISTS presentation_ratings;
DROP TABLE IF EXISTS discussion_selections;
DROP TABLE IF EXISTS peer_reviews;
DROP TABLE IF EXISTS login_attempts;
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
    is_active INTEGER NOT NULL DEFAULT 1,
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
    source_key TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE
);

CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    login_type TEXT NOT NULL CHECK(login_type IN ('student', 'instructor')),
    principal TEXT NOT NULL,
    client_hash TEXT NOT NULL,
    failed_count INTEGER NOT NULL DEFAULT 0,
    window_started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    blocked_until TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    UNIQUE(course_id, login_type, principal, client_hash)
);

CREATE INDEX idx_login_attempts_client
    ON login_attempts(course_id, login_type, client_hash, window_started_at);

CREATE UNIQUE INDEX idx_questions_course_source
    ON questions(course_id, source_key);

CREATE TABLE course_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL UNIQUE,
    phase TEXT DEFAULT 'setup' CHECK(phase IN ('setup', 'discussion', 'competition', 'ended')),
    max_teams INTEGER DEFAULT 6,
    max_members_per_team INTEGER DEFAULT 10,
    teams_locked INTEGER DEFAULT 0,
    discussion_week INTEGER DEFAULT 1,
    session_started_at TIMESTAMP,
    active_team_id INTEGER,
    active_question_id INTEGER,
    current_question TEXT,
    presentation_started_at TIMESTAMP,
    presentation_created_at TIMESTAMP,
    presentation_time_cap INTEGER DEFAULT 300,
    presentation_remaining INTEGER,
    poll_active INTEGER DEFAULT 0,
    poll_question_key TEXT,
    poll_started_at TIMESTAMP,
    presentation_history TEXT DEFAULT '[]',
    roster_version INTEGER DEFAULT 0,
    session_key INTEGER DEFAULT 0,
    state_version INTEGER DEFAULT 0,
    discussion_questions_version INTEGER DEFAULT 0,
    current_discussion_key TEXT,
    current_discussion_source_key TEXT,
    current_discussion_title TEXT,
    current_discussion_content TEXT,
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

CREATE TABLE presentation_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    student_id INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    session_key INTEGER DEFAULT 0,
    week_num INTEGER,
    presenting_team_id INTEGER,
    presenting_team_name TEXT,
    question_id INTEGER,
    question_title TEXT,
    rater_team_id INTEGER,
    rater_team_name TEXT,
    q1_developed INTEGER CHECK(q1_developed >= 1 AND q1_developed <= 5),
    q2_easy INTEGER CHECK(q2_easy >= 1 AND q2_easy <= 5),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY (presenting_team_id) REFERENCES teams (id) ON DELETE SET NULL,
    FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE SET NULL,
    FOREIGN KEY (rater_team_id) REFERENCES teams (id) ON DELETE SET NULL,
    UNIQUE(course_id, student_id, question_key)
);

CREATE INDEX idx_ratings_presentation
    ON presentation_ratings(course_id, question_key);

CREATE TABLE teammate_thumbs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    session_key INTEGER NOT NULL,
    week_num INTEGER,
    question_key TEXT NOT NULL,
    source_question_key TEXT,
    question_title TEXT,
    grader_id INTEGER NOT NULL,
    recipient_id INTEGER NOT NULL,
    grader_team_id INTEGER,
    grader_team_name TEXT,
    recipient_team_id INTEGER,
    recipient_team_name TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    FOREIGN KEY (grader_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY (recipient_id) REFERENCES students (id) ON DELETE CASCADE,
    FOREIGN KEY (grader_team_id) REFERENCES teams (id) ON DELETE SET NULL,
    FOREIGN KEY (recipient_team_id) REFERENCES teams (id) ON DELETE SET NULL,
    UNIQUE(course_id, session_key, question_key, grader_id, recipient_id)
);

CREATE INDEX idx_thumbs_current
    ON teammate_thumbs(course_id, session_key, question_key);

CREATE INDEX idx_thumbs_export_week
    ON teammate_thumbs(course_id, week_num);

CREATE INDEX idx_ratings_export_week
    ON presentation_ratings(course_id, week_num);

-- Per-question visibility for the discussion phase. A row here means the
-- instructor has hidden that question from students. Absence = visible
-- (the default). Bank questions come from week-N-questions.md; appendix
-- questions are instructor-added. Both are addressed by their stable key.
CREATE TABLE hidden_discussion_questions (
    course_id INTEGER NOT NULL,
    week_num INTEGER NOT NULL,
    question_key TEXT NOT NULL,
    FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE CASCADE,
    PRIMARY KEY (course_id, week_num, question_key)
);

-- Auto-increment state_version on every UPDATE of course_state so that no
-- mutation can forget to signal students. The WHEN guard prevents a loop if a
-- statement ever sets state_version explicitly (and also keeps this safe if
-- recursive_triggers is ever turned on).
CREATE TRIGGER course_state_bump_version
    AFTER UPDATE ON course_state
    WHEN NEW.state_version = OLD.state_version
BEGIN
    UPDATE course_state
        SET state_version = OLD.state_version + 1
        WHERE id = NEW.id;
END;
