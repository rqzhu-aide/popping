# Changelog

All notable changes to Popping are recorded here. Releases use semantic
versions as defined in `VERSIONING.md`.

## [v1.2.4] - 2026-08-27

### Added

- Added an instructor-only Tools download for the complete active student
  roster in the directly re-uploadable `student_id,name,pin` CSV format.

## [v1.2.3] - 2026-08-27

### Changed

- Student-roster CSV uploads now merge only the listed IDs. Existing students
  keep their database identity, team, self-provided display name, and
  participation history; new students are added unassigned; and students
  omitted from the file remain unchanged.
- The upload preview now distinguishes new, updated, restored, unchanged, and
  omitted students and identifies PIN changes that require affected students
  to sign in again.
- Clarified the instructor controls for adding or updating one student and for
  the exact four-digit student PIN policy.
- Kept blank names non-destructive for existing students and hardened roster
  confirmation against mixed-version servers and ambiguous legacy IDs.

## [v1.2.2] - 2026-08-25

### Added

- Added an instructor-only floating Quick Roll tool that draws a uniformly
  random number from 1 through an instructor-supplied limit. It runs entirely
  in the browser and does not save or transmit the range or result.

## [v1.2.1] - 2026-08-25

### Added

- Added a read-only server-shell command for looking up one student's PIN
  without exposing PINs through the browser API.

### Changed

- Allowed an instructor to start Group Discussion or Present and Challenge
  with absent students left unassigned after an explicit confirmation.
- Limited random team assignment to unassigned students active within the
  previous minute, while leaving offline unassigned roster members untouched.
- Added each current team's summed historical presentation turns to the team
  selector in Present and Challenge.

### Fixed

- Kept the confirmation retry protected by the current phase, session, and
  roster versions so an intervening roster change cannot be accepted silently.

## [v1.2.0] - 2026-08-24

### Added

- Added a nullable `students.display_name` field so a student can choose how
  classmates address them without changing the instructor-uploaded roster name.
- Added an explicit login control for clearing a saved display name.

### Changed

- Student views now show one name, resolved from display name, roster name, then
  student ID. Instructor views show the same resolved name with the student ID.
- Student Management searches both name fields and the student ID, while roster
  edits and CSV replacement preserve student-provided display names.
- Replaced the popcorn emoji with the supplied Popping brand artwork, added a
  rounded favicon, and set consistent platform titles for shared links.
- Advanced the website, database schema, and export format to `v1.2.0`.
  Activity recorded by an older compatibility line remains available through
  the legacy download.

## [v1.1.8] - 2026-08-23

### Changed

- Renamed the third classroom phase from Group Presentation to Present and
  Challenge across instructor and student views, phase guidance, and related
  validation messages.

## [v1.1.7] - 2026-08-23

### Changed

- Kept unchanged student polling compact throughout active rating windows while
  preserving local countdowns, instructor vote totals, and authoritative close
  signals.

## [v1.1.6] - 2026-08-23

### Changed

- Improved instructor layout hierarchy, responsive overflow, form labels,
  keyboard focus, and question accordion accessibility.

### Fixed

- Kept instructor and student questions pinned to the exact requested or saved
  week and exposed clear readiness details when its canonical file is missing.
- Hardened credential-bound presence, response security headers, rating input
  validation, legacy appendix migration, and simulator rejection detection.
- Stabilized live Student Management refreshes, clamped filtered pagination,
  and retried instructor actions safely after rating settlement.

## [v1.1.5] - 2026-08-23

### Changed

- Made Clear All Teams atomically lock student team joining while it empties
  every current assignment, so cleared teams stay empty until the instructor
  unlocks them.
- Reorganized Setup roster cards with student ID and team turns on the first
  row and the student name on the second row.

### Fixed

- Restored team filtering in Student Management for every team name.
- Added a separator above Reset Course Data in Tools and styled the available
  reset action in red.


## [v1.1.4] - 2026-08-23

### Changed

- Removed individual challenger-turn counts from the instructor Setup roster
  while retaining them in Student Management and presentation controls.
- Moved the course reset control into Tools and kept it available only during
  Setup or after End Session.
- Made presentation selectors show the share of team members with prior turns,
  use compact completed labels, and give team and question choices more space.

### Fixed

- Kept live Setup roster refreshes consistent with the initial roster display.

## [v1.1.3] - 2026-08-23

### Fixed

- Restored readable instructor Setup rosters by placing participation badges
  below each student name instead of compressing names inside four-column team
  cards.
- Enforced one active challenge per student and exposed a clear selected status
  on the student page. Clearing now removes the targeted round, its ratings,
  and any stale hand together, then allows that student to raise again.
- Made the Clear control explain that an active poll must be stopped and its
  final ratings saved before destructive challenge removal.

## [v1.1.2] - 2026-08-23

### Fixed

- Kept Discussion and Presentation on the exact same ordered canonical weekly
  question set, rejected malformed whole-file updates, and refreshed bundled
  GitHub questions safely after deployment.
- Hardened student credential changes, login request checks and throttling,
  legacy-export limits, historical-week exports, and private demo reuse.
- Made instructor and student polling recover cleanly, preserved failed
  appendix submissions without duplicates, and reduced redundant participation
  requests while keeping compact vote totals.
- Improved multiline display-math rendering, narrow-screen Markdown and menus,
  keyboard team assignment, modal focus handling, and blocked-storage fallback.
- Made `/healthz` verify every active course using the same database identity and
  schema requirements as login.
- Required exact single-course backup identity, validated every bundled question
  week during initialization, hardened course scaffolding, and documented a
  Render-safe inactive-course maintenance workflow.

## [v1.1.1] - 2026-08-23

### Fixed

- Instructor PIN changes now invalidate existing instructor sessions on their
  next authenticated request.
- Enforced one instructor PIN policy across login, course initialization, and
  command-line PIN recovery: 4-32 ASCII digits (`0-9`) with no whitespace or
  Unicode-digit variants.
- Removed the remaining runtime compatibility reader for obsolete pre-rendered
  question HTML. Canonical `week-N-questions.md` files are now the sole source
  for weekly question content.

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
  canonical `week-N-questions.md` workflow replaced these assets; v1.1.1
  removes the remaining runtime compatibility reader.

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
