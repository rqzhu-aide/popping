# Changelog

All notable changes to Popping are recorded here. Releases use semantic
versions as defined in `VERSIONING.md`.

## [v1.1.0] - 2026-08-21

### Added

- Added durable course-wide presentation-team and challenger participation
  history for instructors.
- Added sortable Team turns and Challenge turns in Student Management, team
  member history while selecting presenters, and challenger counts beside
  raised hands.
- Added a full-roster **Participation Roster** snapshot plus **Presentation
  Participants** and **Challenge Rounds** evidence to the versioned weekly
  export and complete course backups.
- Added per-week results downloads: **Tools → Download Results** offers the
  current week and every previous week, each with the same workbook layout
  (the course-wide Participation Roster still pools all weeks).
- Added `scripts/set-instructor-pin.py` for command-line instructor PIN
  rotation and recovery (no website login required; updates every course by
  default or one course when given a slug).
- Added `scripts/migrate-course-db.py` for validated, transactional offline
  migration of an existing course database.

### Counting rules

- Finalizing a presentation counts every active member of its presenting team,
  including when zero ratings were submitted. Cancelling it does not count.
- A retained challenger selection counts even with zero ratings. **Clear** or
  **Cancel Presentation** removes the applicable selection, and repeated
  retained selections count separately.
- Counts persist across sessions within the course offering. A course reset or
  reinitialization erases the live counts.

### Upgrade note

- Existing `v1.0.x` participation is not backfilled. Stop all web workers and
  run `python scripts/migrate-course-db.py <course_slug>` for every existing
  course before starting `v1.1.0`.

### Fixed

- Instructor login now accepts numeric PINs of 4-32 digits (previously clipped
  at 4 characters, which locked out instructors who set a longer PIN at
  course initialization). `init-course-db.py` validates the same range.

### Removed

- Deleted the obsolete pre-rendered question assets (`classes/*/weekN/`
  folders, `question-guide/examples/`, and the legacy authoring guide). The
  application never read them; the canonical `week-N-questions.md` workflow is
  now the only documented path.

## [v1.0.0] - 2026-08-15

### Added

- Established `v1.0.0` as the website, database schema, export format, and
  pre-versioned data baseline.
- Added persistent version metadata to course data, result exports, and backup
  manifests.
- Defined patch-level website compatibility and minor-level database schema
  boundaries.
- Displayed the website version beside the theme control in authenticated
  navigation.
