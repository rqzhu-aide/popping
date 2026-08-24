# Complete Course Backups

The normal initialization, reset, and restore safety snapshots live beside the
course database. They help with operator mistakes, but they do not protect
against loss of the persistent disk. Use `scripts/backup-course.py` to create a
complete bundle at an explicitly supplied location outside `DATA_DIR`.

## Create a bundle

From the repository root:

```bash
python scripts/backup-course.py create 432fall2026 /path/on/another-disk/popping-backups
```

The destination can be an encrypted external drive, a trusted network mount,
or a directory synchronized by the storage provider you choose. The command
refuses any destination inside `DATA_DIR`.

The resulting ZIP contains:

- a consistent SQLite snapshot named `popping.db`
- all persistent files under the course's `questions/` directory
- all persistent files under the course's `appendix/` directory
- `manifest.json`, with the course slug, UTC creation time, website version,
  database schema version, export format version, contained data versions,
  unclassified-data status, file sizes, SHA-256 checksums, and successful
  SQLite integrity and foreign-key
  checks

The script briefly holds SQLite's writer lock while it takes the snapshot and
copies the persistent files. Existing reads can continue, but a student or
instructor write may wait briefly. Run it between classes or during a quiet
period. The ZIP is verified before it is atomically given its final filename.

This tool does not encrypt or upload the bundle. Student and instructor PINs
are stored as plaintext in the database, so the destination must be encrypted
or access-controlled. Do not place a bundle in a public link or an
unrestricted shared folder.

## Verify after copying

Verify the exact copy that will be retained:

```bash
python scripts/backup-course.py verify /path/to/popping-432fall2026-YYYYMMDDTHHMMSSZ.zip
```

Verification streams the archived files, checks every size and SHA-256 digest,
then runs SQLite integrity and foreign-key checks on the archived database. It
does not rely on the original course directory.

## Version metadata and compatibility

The manifest's `format` value, `popping-course-backup-v1`, identifies the backup
container layout. It is separate from these semantic version fields:

- `website_version`
- `database_schema_version`
- `export_format_version`
- `contained_data_versions`
- `contains_unclassified_data`

The verifier reads the archived database's schema ledger instead of assuming the
version of the running backup script. A database without a ledger is the explicit
`v1.0.0` baseline. Missing website and export format fields in an older v1
manifest also default to `v1.0.0`. The verifier derives contained data versions
from the archived feedback tables and treats pre-versioned rows as `v1.0.0`.
It also reads presentation-history versions. Missing versions use the baseline.
Malformed durable version strings stay unchanged in `popping.db`, are omitted from
the semantic version list, and set `contains_unclassified_data` to `true`.

## Participation history in backups and exports

Starting with `v1.1.0`, the displayed Team turns and Challenge turns are derived
from durable rows, not stored as independent totals. A complete bundle includes
the `presentation_participants` and `challenge_rounds` tables inside its SQLite
snapshot. Restoring that snapshot on a compatible release therefore restores
the course-wide counts across sessions, including retained zero-rating events.

Current Week Results includes a **Participation Roster** sheet with every active
and archived roster member and the latest course-wide Team turns and Challenge
turns at export time.
In this sheet, `team` is the current team for an active student and the last
known team for an archived student.
It also includes the underlying evidence for the selected week in the
**Presentation Participants** and **Challenge Rounds** sheets. A finalized team
appears in the first evidence sheet even if nobody rated it. A retained
challenger appears in the second evidence sheet even if its
`ratings_submitted` value is zero. The dedicated roster sheet is current when
the ZIP is generated, but one weekly workbook is still not a complete
replacement for a course backup.

The `v1.0.x` schema did not record these events, so its database and exports
cannot provide a trustworthy participation backfill. After future schema-line
changes, incompatible event rows remain available through **Download Legacy
Data** under the normal compatibility policy.

Before restoring, compare the recorded database schema version with the running
release. A backup on the same major/minor compatibility line can be restored
directly. An older compatibility line must go through the supported database
migration so that its record versions remain available through Download Legacy
Data. Verification checks the archive; it does not migrate the database.

For an existing `v1.0.x` database, use the offline boundary described in the
main README. Locally, stop all workers and type `SERVICE STOPPED`. On Render,
deploy the course as `active: false`, use Render Shell, and type
`COURSE OFFLINE`. Then run this from the `v1.1.0` repository root:

```bash
python scripts/migrate-course-db.py 432fall2026
```

The migration command creates its own validated pre-migration snapshot. It does
not infer participation events from old ratings.

## Restore a complete bundle

The restore is intentionally explicit because it replaces live course data.

1. Verify the bundle with the command above.
2. Take the affected course offline. Locally, stop all Flask or Gunicorn
   workers. On Render, deploy that course as `active: false`, confirm it is no
   longer public, and keep the service running for Render Shell and `/data`.
3. Extract the verified bundle into a new, empty recovery directory:

   ```bash
   python -m zipfile -e /path/to/verified-backup.zip /path/to/empty-recovery-directory
   ```

4. Restore the database from the repository root:

   ```bash
   python scripts/restore-course-db.py 432fall2026 /path/to/empty-recovery-directory/popping.db
   ```

5. In `DATA_DIR/432fall2026/`, rename the current `questions/` and `appendix/`
   directories with a `before-restore` suffix. Do not delete them yet.
6. Copy the extracted `questions/` and `appendix/` directories, when present,
   into `DATA_DIR/432fall2026/`. If one is absent from the bundle, leave that
   directory absent because it was empty at backup time.
7. Locally, restart the service. On Render, change the restored course back to
   `active: true` in GitHub and deploy. Then verify instructor login, student
   login, the selected week, and the displayed question count.
8. Keep the renamed pre-restore directories until the restored class has been
   checked. Remove them later according to your retention policy.

The existing database restore command validates the selected database, creates
a recovery snapshot of the current database, and performs an atomic database
replacement. It does not restore question or appendix files, which is why steps
5 and 6 are required.

## External scheduling remains provider-specific

This repository does not choose a cloud provider, create credentials, configure
retention, or add encryption. To automate off-site retention, schedule the
`create` command only after the chosen encrypted or access-controlled
destination is mounted or synchronized on the machine running the command.
Periodically run `verify` against a retained copy and test the complete restore
procedure on a non-production course.
