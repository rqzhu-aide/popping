#!/usr/bin/env python3
"""Preview or save one derived Weekly Hero result without changing sources.

Examples:

    # Read-only preview. The source major/minor series is auto-detected.
    python scripts/backfill-weekly-heroes.py 432fall2026 1

    # Save only when Week 1 has no summary yet.
    python scripts/backfill-weekly-heroes.py 432fall2026 1 --apply

    # Deliberately replace a different saved summary.
    python scripts/backfill-weekly-heroes.py 432fall2026 1 --apply --replace

    # Resolve an intentionally mixed-version week by selecting v1.2.x rows.
    python scripts/backfill-weekly-heroes.py 432fall2026 1 \
        --source-schema-version 1.2.0

The default invocation is read-only. ``--replace`` is accepted only together
with ``--apply``. Apply and replace write only the derived weekly-summary
tables plus the roster-version refresh signal; rating, participant, challenge,
student, and team records are never updated or deleted.
"""

import argparse
import os
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from database import (  # noqa: E402
    calculate_weekly_hero_preview,
    detect_weekly_hero_source_schema_version,
    save_weekly_hero_summary,
    validate_current_schema,
    validate_slug,
)
from scripts.maintenance_safety import (  # noqa: E402
    confirmation_prompt,
    validate_confirmation,
)
from versioning import compatibility_label, public_version  # noqa: E402


def course_database_path(slug):
    return os.path.join(config.DATA_DIR, slug, 'popping.db')


def _course_id(db, expected_slug):
    integrity = [row[0] for row in db.execute('PRAGMA integrity_check')]
    if integrity != ['ok']:
        raise RuntimeError(
            'SQLite integrity check failed: ' + '; '.join(integrity)
        )
    rows = db.execute(
        'SELECT id, slug FROM courses ORDER BY id'
    ).fetchall()
    if len(rows) != 1 or rows[0]['slug'] != expected_slug:
        raise RuntimeError(
            'Database course slug does not match the requested course'
        )
    return rows[0]['id']


def _print_preview(preview):
    print('=== Weekly Hero Preview ===')
    print(f"Course database ID: {preview['course_id']}")
    print(f"Lecture week: {preview['week_num']}")
    print(
        'Source compatibility: '
        f"{compatibility_label(preview['source_schema_version'])}"
    )
    versions = preview['source_data_versions']
    print(
        'Source data versions: '
        + (', '.join(public_version(value) for value in versions)
           if versions else 'none')
    )
    print(
        'Source rows: '
        f"{preview['source_presentation_rating_count']} presentation ratings, "
        f"{preview['source_challenge_rating_count']} challenge ratings, "
        f"{preview['source_participant_count']} presentation participants"
    )
    print(f"Source fingerprint: {preview['source_fingerprint']}")

    if not preview['results']:
        print('Awards: none (an empty finalized summary can still be saved)')
    else:
        print('Awards:')
        for result in preview['results']:
            if result['category'] == 'team':
                subject = result['team_name']
            else:
                identifier = result['challenger_identifier']
                subject = result['challenger_name']
                if identifier:
                    subject += f' ({identifier})'
            print(
                f"  {result['award_type']}: {subject}; rank "
                f"{result['rank']}; exact score "
                f"{result['score_sum']}/{result['score_count']}; "
                f"{result['rating_count']} rating(s); "
                f"{len(result['recipients'])} recipient(s)"
            )

    if preview['recipient_coverage_complete']:
        print('Awarded-team participant coverage: complete')
    else:
        print('Awarded-team participant coverage: INCOMPLETE')
        for missing in preview['missing_participant_presentations']:
            print(
                f"  {missing['team_name']}: "
                f"{missing['presentation_key']} has no matching participant "
                'snapshot'
            )


def _read_preview(database_path, slug, week_num, explicit_source_version):
    uri = Path(database_path).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only = ON')
        validate_current_schema(db)
        course_id = _course_id(db, slug)
        source_version = (
            explicit_source_version
            or detect_weekly_hero_source_schema_version(
                db, course_id, week_num
            )
        )
        return calculate_weekly_hero_preview(
            db,
            course_id,
            week_num,
            source_schema_version=source_version,
        )
    finally:
        db.close()


def _apply_preview(database_path, slug, preview, replace):
    db = sqlite3.connect(database_path, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA busy_timeout = 30000')
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('BEGIN IMMEDIATE')
        try:
            validate_current_schema(db)
            course_id = _course_id(db, slug)
            if course_id != preview['course_id']:
                raise RuntimeError(
                    'Course identity changed after preview; preview again'
                )
            outcome = save_weekly_hero_summary(
                db, preview, replace=replace
            )
            if outcome['status'] != 'unchanged':
                updated = db.execute(
                    '''UPDATE course_state
                       SET roster_version = COALESCE(roster_version, 0) + 1
                       WHERE course_id = ?''',
                    [course_id],
                )
                if updated.rowcount != 1:
                    raise RuntimeError(
                        'Course state is missing; weekly result was not saved'
                    )
            db.commit()
            return outcome
        except Exception:
            db.rollback()
            raise
    finally:
        db.close()


def _parser():
    parser = argparse.ArgumentParser(
        description=(
            'Preview or save a derived Weekly Hero summary. The default is '
            'read-only; migrate the course database to the current schema '
            'before running this command.'
        )
    )
    parser.add_argument('course_slug', help='course folder/database slug')
    parser.add_argument('week', type=int, help='positive lecture week number')
    parser.add_argument(
        '--source-schema-version',
        help=(
            'source compatibility series, such as 1.2.0; by default the '
            'single series present in the week is auto-detected'
        ),
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help=(
            'save the displayed preview after course identity and offline '
            'maintenance confirmations (insert-only by default)'
        ),
    )
    parser.add_argument(
        '--replace',
        action='store_true',
        help='replace a different saved summary; requires --apply',
    )
    return parser


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.replace and not args.apply:
        parser.error('--replace requires --apply')
    if args.week <= 0:
        parser.error('week must be a positive integer')

    try:
        slug = validate_slug(args.course_slug.strip())
        database_path = course_database_path(slug)
        if not os.path.isfile(database_path):
            raise ValueError(f'Course database not found: {database_path}')
        preview = _read_preview(
            database_path,
            slug,
            args.week,
            args.source_schema_version,
        )
        _print_preview(preview)
        if not preview['recipient_coverage_complete']:
            if args.apply:
                print('No changes made: participant coverage is incomplete.')
            return 1
        if not args.apply:
            print('Preview only. No database changes were made.')
            return 0

        course_confirmation = input(
            f'Type {slug} to save this course summary: '
        ).strip()
        if course_confirmation != slug:
            print('Cancelled: course slug did not match.')
            return 1
        offline_confirmation = input(confirmation_prompt()).strip()
        try:
            validate_confirmation(
                offline_confirmation,
                PROJECT_ROOT / 'classes' / slug / 'course.yaml',
            )
        except ValueError as exc:
            print(f'Cancelled: {exc}.')
            return 1

        outcome = _apply_preview(
            database_path, slug, preview, replace=args.replace
        )
        if outcome['status'] == 'unchanged':
            print('Weekly Hero summary already matches this source. No change.')
        else:
            print(
                f"Weekly Hero summary {outcome['status']} successfully "
                f"(summary ID {outcome['summary_id']})."
            )
        return 0
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
