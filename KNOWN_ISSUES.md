# Known Issues and Improvement Backlog

Issues identified during the 2026-07-24 review of the "show all discussion
questions at once" change set (commit `798fcc1`). Each entry has a
description, the affected code, and a concrete fix plan. Priority: **P1**
(user-visible or data risk), **P2** (code quality / latent bug), **P3**
(test hygiene / minor).

---

## Bugs

### 1. Contradictory time-cap clamp in `start_presentation` — P1

**Description.** When `time_cap` is out of range (e.g. 9999), the endpoint
resets it to the 300s default, but the response notice tells the instructor
the "allowed range [is] 10–3600s". Clamping 9999 into a 10–3600 range
should yield 3600, not 300 — the message and the behavior contradict each
other, and `test_start_presentation_reports_clamped_time_cap` currently
pins the ambiguous behavior (`payload["time_cap"] == 300`).

**Where.** `app.py` (`/api/start_presentation`, the `time_cap_clamped`
branch); `tests/test_workflow_safety.py::test_start_presentation_reports_clamped_time_cap`.

**Fix plan.**
1. Decide the intended semantics: clamp-to-nearest-bound (9999 → 3600)
   vs. reset-to-default (→ 300).
2. If reset-to-default is intended (simpler, current behavior): change the
   notice text to "reset to the 300s default (allowed range 10–3600s)" and
   update the test assertion message accordingly.
3. If clamp-to-bound is intended: clamp with
   `time_cap = max(10, min(3600, time_cap))`, keep the notice, update the
   test to expect 3600.
4. While there, rename the boolean `time_cap_clamped` to
   `time_cap_was_clamped` — it currently reads like it holds a clamped
   value.

### 2. Dropped `peer_reviews` → `teammate_thumbs` migration — P1 (data risk)

**Description.** `database.py` removed the one-time migration that copies
legacy `peer_reviews` rows (score > 0) into `teammate_thumbs`. Any course
DB created before that migration shipped but not opened since (so
`_ensure_schema_locked` never ran with it) will now never have its legacy
thumbs migrated — silent data loss in exports and analytics.

**Where.** `database.py`, schema-setup path.

**Fix plan.**
1. Check production/demo course DBs under `data/`: run a quick script that
   opens each DB and reports whether `peer_reviews` has un-migrated rows
   (`score > 0` with no matching `teammate_thumbs` row).
2. If any are found: restore the migration block (revert that hunk of the
   commit), run init/restore once per affected course, and only then remove
   the migration.
3. If none are found: add a comment in `database.py` recording the cutover
   assumption ("all course DBs migrated as of <date>; migration removed in
   <commit>") so a future restore of an old backup doesn't silently lose
   data.
4. Optional belt-and-braces: keep the migration but gate it behind a
   cheap `SELECT 1 ... LIMIT 1` existence check so it costs nothing on
   migrated DBs.

### 3. "Time's up — rating open" note doesn't wrap on student dashboards — P1

**Description.** The calmer timer state added for open rating polls uses a
`.timer-time-up::after` pseudo-element with `flex-basis: 100%` to force the
note onto its own line. `.timer-box` (instructor) has `flex-wrap: wrap`,
but `.student-timer` (style.css:1738) does not — on student dashboards the
note squeezes inline and compresses the timer value instead of wrapping.

**Where.** `static/css/style.css` (`.student-timer` rule, ~line 1738);
compare `.timer-box` (~line 1088).

**Fix plan.**
1. Add `flex-wrap: wrap;` to `.student-timer`.
2. Manually verify on the student dashboard: start a presentation with a
   short timer, open the rating poll, let the timer expire — the note
   should appear on its own line below the timer value.
3. Update `tests/test_client_resilience.py` (the star/timer static
   assertions) if the CSS rule shape is asserted there.

### 4. Appendix "Edit" button offered during competition phase, but the server always rejects it — P1

**Description.** `renderDiscussionQuestions` renders the ✏️ Edit button
unconditionally, but `/api/edit_appendix_question` returns
`409 'Appendix questions cannot be edited during presentations'` in the
competition phase. Clicking it in that phase can only fail with a toast,
and the competition-phase `upsertCompQuestionOption(data)` edit branch in
`addAppendixQuestion` is unreachable dead code.

**Where.** `static/js/app.js` (`renderDiscussionQuestions`, the
`appendix-edit-btn` markup; `addAppendixQuestion` edit branch); server gate
in `app.py` `/api/edit_appendix_question`.

**Fix plan.**
1. In `renderDiscussionQuestions`, omit the edit button (or render it
   disabled with `title="Editing is disabled during presentations"`) when
   `instructor.dataset.phase === 'competition'`.
2. Remove the dead edit branch from `addAppendixQuestion`'s
   `upsertCompQuestionOption` path, keeping the add branch.
3. Adjust the static source-assertion tests in
   `tests/test_client_resilience.py` that count references to these
   symbols.

---

## Code quality / latent bugs

### 5. `previewDiscussionWeek` has no error handling around its fetch — P2

**Description.** The week-preview handler calls `fetchJSONWithTimeout`
without try/catch. A network timeout or offline condition throws out of the
function → unhandled promise rejection and silent failure (no toast).
Sibling handlers (e.g. `initWeekSelector`, `syncDiscussionQuestions`'s
caller) guard this correctly.

**Where.** `static/js/app.js` (`previewDiscussionWeek`, ~line 2410).

**Fix plan.** Wrap the fetch in try/catch; on failure toast
"Could not load week preview — check your connection and try again." and
leave the modal closed. Mirror the pattern used in `initWeekSelector`.

### 6. Discarded `_read_appendix_question_rows` call in `edit_appendix_question` — P2

**Description.** The route calls `_read_appendix_question_rows(slug, week)`
and throws the result away, then immediately re-reads and re-parses the
same file with `parse_question_blocks(original_content)`. The appendix file
is parsed twice for no effect (unless it was meant as an early validation
pass, which `parse_question_blocks` already performs).

**Where.** `app.py` `/api/edit_appendix_question` (~line 3562).

**Fix plan.** Delete the discarded call. If the intent was validation,
assert on the result instead — but `parse_question_blocks` already raises
`QuestionParseError` on malformed content, so deletion is the likely fix.

### 7. `edit_appendix_question` doesn't clear legacy posted state — P2

**Description.** `delete_appendix_question` nulls the legacy
`current_discussion_*` columns when the deleted question was the posted
one. Editing a question that is still recorded in those columns leaves a
stale posted title/content in `course_state`. Low impact now that
`/api/set_question` is gone, but pre-existing DBs can hold such rows and
exports/restore paths still read them.

**Where.** `app.py` `/api/edit_appendix_question`; compare
`delete_appendix_question` (~lines 3497–3499) which inlines the old
`active_discussion_source_key` fallback.

**Fix plan.** Mirror the delete route's cleanup: if
`(state['current_discussion_source_key'] or state['current_discussion_key'])`
matches the edited question's `source_key`, update the posted copy with the
new title/content (or null it). Add a test next to
`test_appendix_delete_uses_stable_id_and_unposts_current_question`.

### 8. First instructor poll always re-renders presentation history — P2

**Description.** The template seeds `data-presentation-history` with
Python `json.dumps` output (spaces after separators) while the poll
compares `JSON.stringify(state.presentation_history)` (compact). The
signatures never match on the first poll, so the history list re-renders
once unnecessarily on every page load. Cosmetic, but the comparison is
fragile to server-side formatting changes.

**Where.** `templates/instructor.html` (the `data-presentation-history`
attribute) and `static/js/app.js` (poll comparison against
`instructor.dataset.presentationHistory`).

**Fix plan.** Normalize one side: either `JSON.parse` the dataset once at
init and compare parsed arrays, or seed the attribute via `| tojson` of the
already-filtered list and compare against `JSON.stringify` of the same
filtering client-side. Prefer parsing both sides to arrays and comparing
with `JSON.stringify` on canonical objects.

### 9. Restored appendix drafts can resurrect a stale edit target — P2

**Description.** `restoreAppendixDraft` reinstates `editingAppendixId` from
sessionStorage without checking `appendixQuestionsById`. If the question
was deleted (another tab or instructor) before the reload, saving the
restored draft 404s. Self-correcting but confusing.

**Where.** `static/js/app.js` (`restoreAppendixDraft`).

**Fix plan.** Only restore `draft.editingId` when
`appendixQuestionsById.has(draft.editingId)`; otherwise restore the text
fields as a plain "add question" draft.

### 10. `_discQuestionNumbers` never resets — P3

**Description.** The stable card-numbering map grows monotonically for the
page lifetime and is never cleared on week change. Week changes currently
reload the page so this is harmless, but it will silently misnumber cards
if week switching ever becomes reload-free.

**Where.** `static/js/app.js` (`_discQuestionNumbers`, used by
`discussionQuestionNumber`).

**Fix plan.** Clear `_discQuestionNumbers` in `renderDiscussionQuestions`
when `data.current_week` changes (track the last-seen week in a module
variable). No test change needed until week switching is reload-free.

### 11. `_touch_demo_instance_throttled` marks failures as touched — P3

**Description.** If `touch_demo_instance` raises `OSError`, the slug is
still recorded in `_demo_instance_touch_last`, so a genuinely writable
instance won't retry for up to 60s. Harmless in practice (next poll after
the window retries), but easy to make exact.

**Where.** `app.py` (`_touch_demo_instance_throttled`, ~line 1319).

**Fix plan.** On `OSError`, delete the slug from `_demo_instance_touch_last`
(or only record the timestamp after a successful touch) so a transient
filesystem error doesn't suppress retries for a full minute.

### 12. Minor cleanups — P3

- `question_catalog._parse_discussion_blocks` parses each YAML frontmatter
  block twice: once in `_pair_question_headers`, again per block when
  building the result. Have `_pair_question_headers` return
  `(opening, closing, frontmatter, metadata)` tuples and reuse the parsed
  metadata. (Perf nit, only matters for large discussion files.)
- `index()` flashes the `?session=ended` notice with the `'success'`
  category, and any crafted link can trigger it. Use `'info'`/`'warning'`
  and only flash when a session was actually cleared. Update
  `test_session_ended_notice_renders_on_landing` accordingly.
- GET `/demo/exit` returns 302 to a read-only page that looks like it
  exited but didn't (pinned by `test_role_entry_logout_and_exit_are_post_only`).
  Consider rendering a small "use the exit button" interstitial instead.
- Logout/demo POST forms carry no CSRF token, consistent with the app's
  token-less JSON APIs, but the new POST forms make cross-site logout
  CSRF trivially possible. Low impact; if CSRF tokens are ever introduced,
  cover these forms first.

---

## Test hygiene

### 13. Cache-bounds test pollutes module state — P3

**Description.** `test_poll_duration_cache_is_bounded` wraps its mutations
in `try/finally: cache.clear()`, but `test_course_availability_cache_is_bounded`
does not — it leaves `COURSE_AVAILABILITY_CACHE_LIMIT` bogus entries plus
the real slug in the module-level cache. Passes today, but it's
order-dependent and could flake if a later test inspects cache size or the
same slug after mutating its DB.

**Where.** `tests/test_course_availability.py`.

**Fix plan.** Add `finally: app_module._course_availability_cache.clear()`
(or a shared autouse fixture that clears both caches) to match the sibling
test.

### 14. Static source-count assertions are brittle — P3

**Description.** `tests/test_client_resilience.py` asserts exact source
strings and counts — `source.count("refreshRosterInPlace()") == 6`,
`source.count("clearAppendixDraft();") == 2`,
`source.count("window.location.href = '/?session=ended';") >= 10`,
verbatim code like
`loadError.serverMessage = (questionData && questionData.error) || null;`.
Any formatting change, added call site, or renamed variable breaks the
suite without a real regression; the `>= 10` redirect count will silently
need bumping as routes are added. These pin implementation, not behavior.

**Where.** `tests/test_client_resilience.py` (~15 tests reading
`static/js/app.js` / `static/css/style.css`).

**Fix plan.**
1. Replace raw `source.count(...) == N` checks with scoped body extraction —
   the file already demonstrates the better pattern in
   `test_setup_roster_mutations_refresh_in_place` (slice from the function
   definition to the next `/* =====` marker). Apply it to the
   `clearAppendixDraft();` and per-handler `window.location.reload()`
   counts.
2. Extract a module-level `APP_JS_SOURCE` / `STYLE_CSS` fixture (read once)
   instead of repeating `read_text` in ~15 tests.
3. Fix the CSS parsing (`index(".star {")` + first `"}"`) to tolerate
   comments/nested braces, or assert against a regex over the rule body.
4. Longer term, prefer behavior tests (DOM via a headless browser, or
   server-rendered HTML assertions) over source-string pinning for new UI
   invariants.

### 15. Coverage gaps in new tests — P3

- `test_student_search_escapes_like_wildcards` only proves escaping on the
  `student_id` match path. Also insert a student whose *name* contains `%`
  or `_` and search for it.
- `test_poll_touch_is_throttled_per_instance` compares real mtimes with a
  1s tolerance — fine on NTFS/ext4, flaky on FAT/network mounts. Consider
  injecting a fake clock into `touch_demo_instance` instead of comparing
  filesystem mtimes.
