#!/usr/bin/env python3
"""Look up one student's PIN from a course database.

This read-only server utility is intended for an authorized operator using a
terminal such as Render Shell. Student PINs remain excluded from browser APIs.

Usage:
    python3 scripts/check-student-pin.py <course_slug> <student_id>
"""

from contextlib import closing
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from database import validate_current_schema, validate_slug  # noqa: E402


class LookupError(RuntimeError):
    """Raised when a valid lookup has no matching student."""


def _terminal_safe(value):
    """Keep untrusted roster text from emitting terminal control characters."""
    return ''.join(
        character if character.isprintable()
        else f'\\u{ord(character):04x}'
        for character in str(value)
    )


def _open_read_only(database_path):
    """Open an existing SQLite database without permission to change it."""
    uri = Path(database_path).resolve().as_uri() + '?mode=ro'
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only = ON')
    connection.execute('PRAGMA busy_timeout = 10000')
    return connection


def find_student(data_dir, slug, student_id):
    """Return one matching student row from a validated course database."""
    slug = validate_slug(slug)
    student_id = student_id.strip()
    if not student_id:
        raise ValueError('Student ID cannot be empty')

    database_path = Path(data_dir) / slug / 'popping.db'
    if not database_path.is_file():
        raise ValueError(f'Course database not found: {database_path}')

    with closing(_open_read_only(database_path)) as connection:
        validate_current_schema(connection)
        courses = connection.execute(
            'SELECT id, slug FROM courses ORDER BY id'
        ).fetchall()
        if len(courses) != 1 or courses[0]['slug'] != slug:
            raise RuntimeError(
                'Database course slug does not match the folder name'
            )

        rows = connection.execute(
            '''SELECT student_id, pin, is_active
               FROM students
               WHERE course_id = ? AND student_id = ? COLLATE NOCASE
               ORDER BY id''',
            [courses[0]['id'], student_id],
        ).fetchall()
        if not rows:
            raise LookupError(
                f"Student not found in course '{slug}': {student_id}"
            )
        if len(rows) != 1:
            raise RuntimeError(
                'Multiple student records differ only by letter case; '
                'resolve the duplicate before checking a PIN'
            )
        return dict(rows[0])


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print(
            'Usage: python3 scripts/check-student-pin.py '
            '<course_slug> <student_id>'
        )
        return 1

    slug, student_id = args
    try:
        student = find_student(config.DATA_DIR, slug.strip(), student_id)
    except Exception as exc:
        print(f'Error: {_terminal_safe(exc)}')
        return 1

    status = 'active' if student['is_active'] else 'removed (cannot log in)'
    print('=== Student PIN ===')
    print(f"Course: {_terminal_safe(slug.strip())}")
    print(f"Student ID: {_terminal_safe(student['student_id'])}")
    print(f'Status: {status}')
    print(f"PIN: {_terminal_safe(student['pin'])}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
