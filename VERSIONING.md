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

Data is current when its major and minor numbers match the database schema's
major and minor numbers. Patch differences are compatible. For example,
`v1.0.3` data is compatible with every `v1.0.x` website and database release.

When the database schema advances to `v1.1.0`, data from `v1.0.x` is retained
but excluded from Current Week Results. It remains available through Download
Legacy Data. Records whose lecture week is unknown also remain legacy,
independently of their data version.

A single database may therefore contain several data versions. The database
schema version must not be used as a replacement for per-record data versions.

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

If a schema-line release is accidentally deployed during a session, roll the
application back to the previous compatible release, end or reset the session,
then deploy the new release again. Do not bypass the migration-window check.

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
