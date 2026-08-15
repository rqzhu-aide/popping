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
- `manifest.json`, with the course slug, UTC creation time, file sizes, SHA-256
  checksums, and successful SQLite integrity and foreign-key checks

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

## Restore a complete bundle

The restore is intentionally explicit because it replaces live course data.

1. Verify the bundle with the command above.
2. Stop every Flask or Gunicorn worker. On Render, suspend the web service and
   wait for all workers to stop.
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
7. Restart the service. Verify instructor login, student login, the selected
   week, and the displayed question count.
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
