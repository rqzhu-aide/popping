# Interactive simulated classroom

This simulator runs a temporary Popping course with synthetic students while a
real person controls the normal instructor page. It is intended for checking
the instructor workflow and live counters before class.

The simulator does not use the public demo or any real course data. It binds
only to `127.0.0.1`, creates a new course and database under the system temporary
directory, and drives only student routes. It never changes instructor state.

## Start the default classroom

Install the simulator dependencies once:

```powershell
python -m pip install -r requirements-load-test.txt
```

Then start 40 students in 8 teams:

```powershell
python scripts\simulate_classroom.py
```

Open the instructor URL printed by the script. With the default port, use:

- URL: `http://127.0.0.1:5100/instructor_login/simulation`
- Username: `sim-instructor`
- PIN: `9999`

Students log in and join their assigned teams gradually. Leave the script
running while you operate the instructor page.

## What the simulated students do

- Setup: all 40 students log in and join 8 balanced teams of 5.
- Discussion: each student gives one thumb to a teammate after Discussion
  begins.
- Presentation: one student from each non-presenting team raises a hand.
- Challenge: every eligible student submits one challenger score after the
  instructor selects a challenger.
- Presentation rating: every eligible student submits both scores while the
  rating poll is open.

With full participation and teams of 5, the expected final live counts are:

- Discussion: 40 participants and 40 thumbs.
- Presentation evaluation: 35 ratings when one team presents.
- Challenge rating: 30 ratings when a challenger comes from a second team.
- Raised hands: 7 when one team presents.

The counts increase over several seconds so the instructor view behaves like a
live class rather than receiving one artificial instant update.

## Check participation history

Each simulator run starts with a new `v1.1.0` database, so every student's Team
turns and Challenge turns begin at zero. Use the normal instructor controls to
verify the new rules:

1. Select a raised-hand student as challenger. That student's Challenge turns
   becomes 1 immediately, even before any ratings arrive.
2. Select **Clear**. The challenge turn returns to zero. A later retained
   selection counts as a new turn, and separate retained selections accumulate.
3. Finalize the presentation. Every active member of the presenting team gains
   one Team turn, including in a run configured so that zero students rate.
4. Instead of finalizing, choose **Cancel Presentation**. No Team turn is added,
   and challenger turns attached to that presentation are removed.
5. End the session and return to Setup within the same simulator run. The
   course-wide counts remain available in Student Management.

The simulator's normal shutdown deletes its temporary database, so counts do
not carry into the next run. Use `--keep-data` only when you want to retain that
temporary database for inspection.

## Observe or stop the classroom

From another terminal:

```powershell
python scripts\simulate_classroom.py --status
python scripts\simulate_classroom.py --watch
python scripts\simulate_classroom.py --stop
```

You can also press `Ctrl+C` in the terminal that started the simulator. Only
processes created by that simulator are stopped.

## Useful options

```powershell
# Keep only 90 percent of eligible students participating
python scripts\simulate_classroom.py --participation-rate 0.9

# Make student actions happen twice as fast
python scripts\simulate_classroom.py --speed 0.5

# Approximate three application processes sharing one SQLite database
python scripts\simulate_classroom.py --workers 3

# Retain the temporary database after stopping
python scripts\simulate_classroom.py --keep-data
```

One local process is the default because it is safer on a memory-constrained
computer and is sufficient for observing the workflow. The three-process mode
is useful for local contention checks, but it does not reproduce Render,
Gunicorn, campus Wi-Fi, TLS, or persistent-disk latency. Use the separate load
test and a disposable Render staging service for deployment capacity testing.

## Safety behavior

The simulator stops if its own process tree exceeds 700 MB of memory, total
system memory reaches 90 percent, CPU remains above 95 percent, a local server
exits unexpectedly, or the configured maximum runtime is reached. The default
maximum runtime is one hour.

On shutdown it checks SQLite integrity and foreign keys. Temporary data is
deleted after a clean run unless `--keep-data` was selected. Failed runs retain
their isolated data and server log for diagnosis.
