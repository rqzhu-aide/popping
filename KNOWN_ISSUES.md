# Known Issues and Improvement Backlog

Last reviewed: 2026-07-27

## Current status

There are no known P1 or P2 functional defects in the current working tree.

The previously listed workflow, export, migration, polling, timer, and demo
issues have been repaired and removed from this backlog. The full automated
suite passes, including:

- 80 students making an initial full poll and sustained compact polls
- 60 students submitting presentation ratings concurrently
- a rating arriving before the deadline but waiting for the database lock
- transient presence-write and demo-marker failures
- stable discussion-question visibility across edits
- current-week exports and a separate unknown-week legacy export

## P3: Production-equivalent load validation is not automated

**Why this remains**

The automated load tests use concurrent Flask test clients against an isolated
SQLite database. They exercise the main request, presence, polling, and rating
paths, but they do not reproduce Render's three Gunicorn workers, persistent
disk latency, or a real school network.

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
