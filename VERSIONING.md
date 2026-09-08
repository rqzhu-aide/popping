# Versioning and Data Compatibility

Popping uses semantic versions in the form `vMAJOR.MINOR.PATCH`. The initial
versioned baseline is `v1.0.0` for the website, course database schema, export
format, and all data created before version tracking was introduced.

## Version roles

- **Website version** identifies the deployed application release.
- **Database schema version** identifies the structure and meaning of a course
  database.
- **Data version** is stored with each exportable classroom record and identifies
  the website release that first created that record. Retries and later edits
  preserve this immutable provenance instead of relabeling the record.
- **Export format version** identifies the structure of generated result files.

Internal database values omit the leading `v`. User-facing pages, workbooks,
CSV files, and manifests include it.

## Compatibility rule

The database must have completed the supported schema migrations before the
website uses it. Schema checks and explicit version-specific backfills still
distinguish major/minor versions.

Starting with `v1.3.3`, normal classroom views and exports read saved activity
from every supported migrated series, currently `v1.0.x` through `v1.3.x`.
For example, `v1.2.5` Week 1 activity contributes to the same course-wide turn
counts and weekly downloads as `v1.3.3` Week 2 activity. Normal Weekly Hero
calculation can combine these versions within one week.

Rows retain their original data versions. No reset, reimport, or relabeling is
required. The export manifest reports the accepted range and the actual source
versions included. Unknown or invalid lecture weeks, malformed versions, and
unsupported versions remain in Download Legacy Data.

Download Results lists the selected week, earlier weeks, and every other week
with saved results. Selecting Week 1 does not hide a saved Week 2 download.

## v1.1.0 participation history

`v1.1.0` introduces two course-wide instructor counts. They are derived from
durable events rather than stored as editable totals:

- **Team turns** count each finalized presentation for every student who was an
  active member of the presenting team when the presentation was finalized.
  Finalization records the membership even when the presentation received zero
  ratings. Cancelling an active presentation does not create a team turn.
- **Challenge turns** count retained challenger selections. Selection creates
  the turn immediately, even when the challenge receives zero ratings. **Clear**
  removes that challenge turn. **Cancel Presentation** removes every challenge
  turn belonging to the cancelled presentation.

Each separate finalized presentation and each separate retained challenger
selection counts again, including repeated participation by the same student.
Counts belong to one course offering database and persist across classroom
sessions. Archiving and reactivating the same roster record preserves its
history. A full course-data reset or database reinitialization erases the live
counts, although the safety backup created by the reset retains the old events.

Tracking starts at `v1.1.0`. The `v1.0.x` migration creates empty participation
event tables and performs no historical backfill. Ratings alone cannot prove
the exact team membership at finalization or whether a challenger selection
was later cleared, so inferred counts would not be trustworthy.

Current Week Results includes a **Participation Roster** sheet with every active
and archived student and the latest course-wide Team turns and Challenge turns
at export time.
In this sheet, `team` is the current team for an active student and the last
known team for an archived student.
It also includes finalized membership in **Presentation Participants** and
retained selections in **Challenge Rounds** for the selected
week. Complete course backup bundles include both event tables in `popping.db`.
These records retain their own data versions and stay usable under the
supported-history rule above. Missing historical membership is never inferred
from the current roster.

## v1.2.0 display names

`v1.2.0` separates student-entered display names from instructor-uploaded
roster names:

- `students.display_name` is nullable and may be set or cleared by the student.
- `students.name` remains the roster name managed by the instructor.
- Student views resolve display name, roster name, then ID and show one item.
  Instructor views use the same resolved name and append the student ID.

The migration adds the new column without changing existing roster names.
Existing activity retains its original version and remains readable under the
supported-history rule. Resetting a course creates an empty database.

## v1.3.0 Weekly Hero history

`v1.3.0` adds durable weekly achievements without changing the source events:

- Weekly summaries store the calculation version, accepted source
  compatibility series, exact source-row counts, and a SHA-256 source
  fingerprint.
- Ranked results store exact score numerators and denominators. Presentation
  teams use competition ranks 1 through 3, including ties. Every rank-1
  challenger receives a Best Challenger bolt.
- Recipient rows snapshot the students who belonged to an awarded team when
  each rated presentation was finalized. Saving is refused if an awarded
  rated presentation has no matching participant snapshot.
- Future sessions aggregate prior-week gold, silver, bronze, and bolt counts
  by the stable student database identity.

Ending a week calculates and saves its summary transactionally using supported
saved activity from that week, including earlier versions. For an explicit
version-specific reconstruction, a completed `v1.2.x` week can also be processed
after migration with:

```bash
python scripts/backfill-weekly-heroes.py <course_slug> <week>
python scripts/backfill-weekly-heroes.py <course_slug> <week> --apply
```

The first command is a read-only preview. The apply command requires course
identity and offline-maintenance confirmation, saves an empty finalized
summary when the week has no eligible results, and is idempotent for an
unchanged fingerprint. `--apply --replace` is required to replace a different
saved summary. Source ratings, challenges, participants, teams, and students
are never rewritten by this backfill.

New summary rows use `v1.3.x` provenance. Their source version list and
fingerprint describe the original activity, including `v1.2.x` records.

## Version bump rules

- A website-only change increments PATCH, such as `v1.0.0` to `v1.0.1`.
- Any database structure or stored-data meaning change increments at least
  MINOR, such as `v1.0.x` to `v1.1.0`.
- A deliberately incompatible redesign increments MAJOR.
- An export layout change increments the export format version. It does not by
  itself change the database compatibility line.

## Schema-line deployment window

Deploy a MINOR or MAJOR database-schema release only between class sessions.
Every course must be in Setup or Ended with no running timer, presentation,
poll, or active challenge. End or fully reset each active session before the
deployment. The application refuses a pending schema migration while this
ephemeral state is active, because mixing an old active presentation or
challenge with newly versioned durable rows would make the session internally
inconsistent.

Setup is a safe migration window only when the current session has no saved
presentation history, thumbs, ratings, challenges, or presentation membership.
If it does, end the session or fully reset it before migrating.

If a schema-line release is accidentally deployed during a session, roll the
application back to the previous compatible release, end or reset the session,
then deploy the new release again. Do not bypass the migration-window check.

For any schema transition, including `v1.2.x` to `v1.3.0`, make each
affected course unreachable before running this command once per course:

```bash
python scripts/migrate-course-db.py <course_slug>
```

For local operation, stop every web worker and confirm `SERVICE STOPPED`.
On Render, deploy the affected course with `active: false`, confirm it is absent
from the landing page, keep the service running so the shell and persistent disk
remain available, and confirm `COURSE OFFLINE`. After migration, reactivate the
course in GitHub and deploy again. See the README maintenance procedure.

The command also requires the course slug, creates a validated pre-migration
snapshot, and applies the registered migration transactionally. A normal web
request is not a substitute for this offline step.

## Release checklist

1. Choose the website, database schema, and export format versions before
   changing code.
2. Add a tested migration for every database change. Preserve existing record
   versions. Only previously unversioned data is assigned the `v1.0.0`
   baseline.
3. Test current and legacy selection at the major/minor boundary.
4. Confirm that weekly exports, legacy exports, and backup manifests contain
   the expected versions.
5. Add the release to `CHANGELOG.md` and create the matching Git tag after the
   production release is accepted.

## Backup bundle format

`popping-course-backup-v1` is the backup container format, not the website or
database version. It remains stable while the manifest separately records the
website version, the archived database ledger's actual schema version, the
export format version, contained data versions, and whether malformed durable
version values require legacy classification. A backup preserves those raw values.
