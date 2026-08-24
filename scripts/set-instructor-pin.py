#!/usr/bin/env python3
"""Set the instructor PIN from the command line (e.g. Render Shell).

This is the credential recovery and rotation path for instructors: it works
without logging into the website, so a forgotten PIN can be reset here.

Usage:
    python scripts/set-instructor-pin.py              # every course database
    python scripts/set-instructor-pin.py <slug>       # one course database

Prompts for the new PIN twice (hidden input). The PIN must contain only 4-32
ASCII digits, matching the instructor login form. Each course database is
updated in its own immediate transaction; a live web service picks up the new
PIN immediately. Existing instructor sessions are rejected on their next
authenticated request, so no restart is required.
"""

import getpass
import os
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from database import (  # noqa: E402
    inspect_schema_version,
    validate_slug,
)
from pin_policy import is_valid_instructor_pin  # noqa: E402
from versioning import SCHEMA_VERSION, public_version  # noqa: E402
DATABASE_SIDECARS = ("-wal", "-shm", "-journal")


def find_course_databases(data_dir, only_slug=None):
    """Return {slug: db_path} for course databases under the data dir."""
    found = {}
    if only_slug is not None:
        path = Path(data_dir) / only_slug / "popping.db"
        if not path.is_file():
            raise ValueError(f"Course database not found: {path}")
        found[only_slug] = path
        return found
    for entry in sorted(Path(data_dir).iterdir()):
        candidate = entry / "popping.db"
        if entry.is_dir() and candidate.is_file():
            found[entry.name] = candidate
    return found


def prompt_new_pin():
    """Prompt twice for a 4-32 digit PIN; return it once confirmed."""
    while True:
        pin = getpass.getpass("New instructor PIN (4-32 digits): ")
        if not is_valid_instructor_pin(pin):
            print("PIN must contain only 4 to 32 digits (0-9). Try again.")
            continue
        confirm = getpass.getpass("Confirm new instructor PIN: ")
        if pin != confirm:
            print("The two PIN entries do not match. Try again.")
            continue
        return pin


def set_instructor_pin(database_path, slug, pin):
    """Update every instructor row in one course database atomically."""
    if not is_valid_instructor_pin(pin):
        raise ValueError(
            "Instructor PIN must contain only 4 to 32 digits (0-9)"
        )
    connection = sqlite3.connect(str(database_path), timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("BEGIN IMMEDIATE")
        try:
            recorded = inspect_schema_version(
                connection, allow_unversioned=True
            )
            if recorded != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Course '{slug}' uses schema "
                    f"{public_version(recorded) if recorded else 'unversioned'}"
                    f"; expected {public_version(SCHEMA_VERSION)}. Run "
                    "scripts/migrate-course-db.py first."
                )
            slugs = [
                row[0]
                for row in connection.execute("SELECT slug FROM courses")
            ]
            if slugs != [slug]:
                raise RuntimeError(
                    "Database course slug does not match the folder name"
                )
            updated = connection.execute(
                "UPDATE instructors SET pin = ?", [pin]
            ).rowcount
            connection.commit()
            return updated
        except Exception:
            connection.rollback()
            raise
    finally:
        connection.close()


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        print("Usage: python scripts/set-instructor-pin.py [<course_slug>]")
        return 1

    try:
        only_slug = validate_slug(args[0].strip()) if args else None
        databases = find_course_databases(config.DATA_DIR, only_slug)
        if not databases:
            print(f"No course databases found under {config.DATA_DIR}")
            return 1

        print("=== Set Instructor PIN ===")
        print(f"Courses: {', '.join(databases)}")
        print(
            "Every listed course will accept the new PIN immediately."
        )
        print(
            "Existing instructor sessions will be signed out on their "
            "next request."
        )
        pin = prompt_new_pin()

        failures = 0
        for slug, path in databases.items():
            try:
                updated = set_instructor_pin(path, slug, pin)
                print(f"OK   {slug}: updated {updated} instructor row(s)")
            except Exception as exc:
                failures += 1
                print(f"FAIL {slug}: {exc}")
        if failures:
            print(f"{failures} course(s) failed; rerun to retry them.")
            return 1
        print("Done. The new PIN is active immediately.")
        return 0
    except Exception as exc:
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
