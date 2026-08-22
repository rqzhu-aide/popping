# Known Issues and Improvement Backlog

Last reviewed: 2026-08-21

## Current status

There are no known P1 or P2 functional defects in the current working tree.

The full automated suite passes. Current coverage includes:

- 80 students making an initial full poll and sustained compact polls
- 60 students submitting presentation ratings concurrently
- 60 students submitting and replaying challenger ratings concurrently
- presentation and challenger ratings arriving before a close cutoff
- transient presence-write and demo-marker failures
- retry and read-back behavior after interrupted vote responses
- stable discussion-question visibility across edits
- current-week presentation, challenge, and teammate-thumb exports
- v1.0 to v1.1 schema migration, backup, restore, and export routing
- presentation-team finalization and cancellation idempotency
- challenger selection, clearing, cancellation, and reselection counts
- participation persistence across sessions and roster archive/reactivation
- participation removal only through confirmed course reset
- case-insensitive roster identity protection
- safe discard confirmation for saved presentation and challenger ratings

## P3: Production-equivalent load validation is not automated

**Why this remains**

The automated suite uses concurrent Flask test clients against isolated SQLite
databases. The optional local real-HTTP harness adds multiple application
processes and sustained sessions, but it still does not reproduce Render's
Gunicorn workers, persistent-disk latency, TLS proxy, or a school network.

This is an operational validation gap, not a confirmed application defect.

**Recommended validation before the first large live class**

Run a staging deployment with production-like settings and a copied,
non-sensitive course database:

1. Hold 80 simulated student sessions for at least 10 minutes.
2. Poll at the same interval used by the browser clients.
3. Trigger one synchronized presence refresh.
4. Submit at least 60 ratings during one open poll, including requests close
   to the deadline.
5. Change phases and question visibility while the clients continue polling.

**Acceptance checks**

- No HTTP 500 responses or SQLite lock errors
- No unexpected logout or unrecoverable stale student screen
- Every accepted rating is stored exactly once
- Poll traffic remains compact after the initial state load
- Instructor counts match the stored student activity
- CPU, memory, and disk latency remain within the selected Render plan

Do not run this test against the active production class service.

## Operational limitation: v1.0 participation cannot be backfilled

The v1.1 migration preserves all existing feedback, but initializes the new
presentation-team participation history as empty. Old rating rows cannot prove
which students were assigned to the presenting team when a presentation was
finalized. Cleared challenger selections also leave no durable v1.0 record.

Treat the displayed Team turns and Challenge turns as tracked since v1.1.0.
The original v1.0 feedback remains available through the legacy export.

## P3: Equation formatting still uses one external CDN

MathJax is pinned and loads asynchronously, so a blocked CDN cannot blank the
site. The page now shows a plain-language warning and the class controls still
work, but equations may remain as raw notation on a network that blocks
jsDelivr.

Self-host the pinned MathJax browser bundle if fully offline equation rendering
is required.

## P3: Broad login abuse can rotate submitted IDs

The login throttle now serializes concurrent attempts for the same course,
role, submitted identity, and source. A deliberate client can still rotate
submitted IDs to avoid that per-identity limit.

A low application-wide source limit could lock out a class whose students
share one campus network address. Add this protection at a trusted network edge,
or add a trusted-proxy-aware source limit calibrated with real campus traffic.

## P3: Off-site backups are not scheduled automatically

`scripts/backup-course.py` creates and verifies a complete bundle containing the
database plus persistent weekly questions and appendix files at a destination
outside the live data directory.

The repository does not choose a storage provider or configure credentials,
encryption, retention, or scheduling. Configure those operationally, and
periodically verify a retained bundle and rehearse a non-production restore.
