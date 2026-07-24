"""Static regressions for bounded browser API requests."""

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_all_browser_api_requests_use_one_bounded_json_helper():
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    helper_start = source.index("async function fetchJSONWithTimeout")
    helper_end = source.index("async function postJSON")
    helper = source[helper_start:helper_end]

    assert source.count("fetch(") == 1
    assert "const REQUEST_TIMEOUT_MS = 15000;" in source
    assert "const UPLOAD_TIMEOUT_MS = 30000;" in source
    assert source.count("UPLOAD_TIMEOUT_MS") == 3
    assert "new AbortController()" in helper
    assert "signal: controller.signal" in helper
    assert helper.index("await fetch(") < helper.index("await res.json()")
    assert helper.index("await res.json()") < helper.index("clearTimeout(timeout)")


def test_rating_window_close_preserves_unsaved_draft_ui():
    """A dirty rating draft keeps the grading section visible (disabled)
    with an explanation instead of display:none when the window closes."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function showRatingWindowClosed()" in source
    assert "Rating window closed — your rating was not saved." in source
    assert "draftLostWithWindow = !canRate && ratingDraftDirty" in source
    assert "(canRate || draftLostWithWindow) ? 'block' : 'none'" in source


def test_discussion_question_cards_use_stable_keys():
    """Question cards track expansion/numbering by server question key so
    re-renders don't collapse open cards or renumber on hide."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "window.toggleDiscQuestionCard" in source
    assert "function discussionQuestionNumber(" in source
    assert "_discQuestionExpanded.has(key)" in source
    assert "#${i + 1}" not in source


def test_week_selector_offers_preview_before_commit():
    """Instructors can preview a week's questions via the ?week= param and
    only commit through the explicit "Use this week" action."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "'/api/discussion_questions?week='" in source
    assert "window.previewDiscussionWeek" in source
    assert "btn-preview-week" in source
    assert "Use this week" in source
    assert "window.usePreviewedWeek" in source


def test_question_load_surfaces_server_error_message():
    """A 422 from /api/discussion_questions shows the server's error text
    instead of only the generic refresh hint."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "loadError.serverMessage = (questionData && questionData.error) || null;" in source
    assert "serverMessage || 'Could not load discussion questions. Please refresh.'" in source


def test_student_table_page_size_and_picker_guard():
    """The student table page size is selectable/persisted, and the 10s
    auto-refresh skips the tbody re-render while a team-picker is open."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "per_page: studentPerPage" in source
    assert "popping-student-per-page" in source
    assert "window.setStudentPerPage" in source
    assert "!document.querySelector('.team-picker')" in source


def test_history_list_tracks_poll_and_empty_team_labels():
    """renderHistory matches options by their stored base label (empty teams
    read "Team N (empty)") and the poll loop re-renders the list when the
    server history changes."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "option.dataset.baseLabel ||" in source
    assert ".replace(/ \\(empty\\)$/, '')" in source
    assert "JSON.stringify(state.presentation_history)" in source


def test_appendix_edit_and_time_cap_notice_client_hooks():
    """Appendix questions get an Edit button that posts to the edit endpoint,
    and the server's clamped time-cap notice surfaces as a toast now that
    starting a presentation no longer reloads the page."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "window.editAppendixQuestion" in source
    assert "'/api/edit_appendix_question'" in source
    assert "appendix-edit-btn" in source
    assert "appendix-cancel-edit-btn" in source
    assert "showToast(data.notice, 'warning')" in source


def test_appendix_add_edit_rebuilds_comp_question_option_in_place():
    """The competition question select gets the new/edited appendix option
    straight from the API response (question_id + title) instead of a page
    reload; reload remains only as the unreconcilable fallback."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function upsertCompQuestionOption(" in source
    assert "Number(data?.question_id)" in source
    assert "if (!upsertCompQuestionOption(data))" in source
    flow_start = source.index("if (!upsertCompQuestionOption(data))")
    assert "window.location.reload()" in source[flow_start:flow_start + 300]


def test_session_loss_redirect_explains_ended_session():
    """Every silent 401 redirect goes to /?session=ended so the landing page
    can explain the session loss instead of dumping the user on /."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "window.location.href = '/';" not in source
    assert source.count("window.location.href = '/?session=ended';") >= 10


def test_thumbs_follow_discussion_phase_not_question_presence():
    """Thumb buttons are gated on the discussion phase (matching the server),
    not on whether questions have been posted yet."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "Wait for the instructor to post a question" not in source
    assert "updateThumbAvailability(dashboard.dataset.phase === 'discussion')" in source
    assert "updateThumbAvailability(false)" not in source
    assert "updateThumbAvailability(true)" not in source


def test_discussion_accordion_is_keyboard_accessible():
    """The disc-question-header expander exposes button semantics, keyboard
    focus, aria-expanded state, and Enter/Space handling."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "window.discQuestionHeaderKeydown" in source
    assert 'role="button" tabindex="0" aria-expanded=' in source
    assert "header.setAttribute(" in source
    assert ".disc-question-header:focus-visible" in css


def test_star_touch_targets_and_disabled_state():
    """Star buttons are at least 44px touch targets and disabled stars do
    not keep hover styles (sticky hover on iOS)."""
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    star_start = css.index(".star {")
    star_end = css.index("}", star_start)
    star_block = css[star_start:star_end]
    assert "min-width: 44px" in star_block
    assert "min-height: 44px" in star_block
    assert ".star:disabled" in css


def test_drag_area_allows_touch_dragging():
    """Teammate cards opt out of browser touch gestures so touch dragging
    works reliably, and narrow tables use the row layout by default."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    card_start = css.index(".teammate-card {")
    card_end = css.index("}", card_start)
    assert "touch-action: none" in css[card_start:card_end]
    assert "table.clientWidth < 450" in source


def test_expired_timer_distinguishes_open_rating_poll():
    """When the presentation timer hits zero while a rating poll is open,
    the timer shows a calm time-up state instead of the red alarm pulse."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "ratingPollOpenForTimer" in source
    assert "timer-time-up" in source
    assert ".timer-value.timer-time-up" in css
    assert "Time's up — rating open" in css


def test_state_changing_routes_use_post_forms():
    """Logout, demo exit, and demo role entry mutate session state, so the
    templates submit them as POST forms instead of plain GET links."""
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    demo = (PROJECT_ROOT / "templates" / "demo.html").read_text(
        encoding="utf-8"
    )

    assert 'href="{{ url_for(\'logout\') }}"' not in base
    assert base.count('method="post" action="{{ url_for(\'logout\') }}"') == 2
    assert 'href="{{ url_for(\'demo_exit\') }}"' not in base
    assert 'method="post" action="{{ url_for(\'demo_exit\') }}"' in base
    assert 'href="{{ url_for(\'demo_instructor\'' not in demo
    assert 'href="{{ url_for(\'demo_student\'' not in demo
    assert demo.count('<button type="submit" class="demo-role-card') == 3


def test_appendix_draft_survives_structural_reload():
    """Structural reloads (phase change, a second instructor tab mutating
    state) rebuild the page server-side.
    The appendix form stashes its unsaved title/content into sessionStorage
    on every input and restores them after the reload, and a successful
    submit or explicit cancel clears the stash."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "popping-appendix-draft:" in source
    assert "function stashAppendixDraft()" in source
    assert "function restoreAppendixDraft()" in source
    assert "function clearAppendixDraft()" in source
    assert "addEventListener('input', stashAppendixDraft)" in source
    assert "restoreAppendixDraft();" in source
    # Restore is week-scoped so a draft never leaks into another week.
    assert "draft.week !== currentWeek" in source
    # Cleared on successful add/edit and on Cancel Edit (not just on input).
    assert source.count("clearAppendixDraft();") == 2


def test_setup_roster_mutations_refresh_in_place():
    """High-traffic setup mutations (team assign, add/remove student,
    auto-assign, clear-all) no longer reload: the poll loop re-renders the
    roster grid on the roster_version bump and the student table is
    refreshed explicitly. Reload stays only as the no-grid fallback."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function refreshRosterInPlace()" in source
    # Definition + 5 call sites (randomAssign, pickTeam, addStudent,
    # removeStudent, unassignAll).
    assert source.count("refreshRosterInPlace()") == 6
    helper_start = source.index("function refreshRosterInPlace()")
    helper_end = source.index("/* =====", helper_start)
    helper = source[helper_start:helper_end]
    assert "window.location.reload();" in helper  # no-grid fallback
    assert "window.instructorPollOnce()" in helper
    assert "loadStudentTable();" in helper


def test_presentation_transitions_reconcile_blocks_in_place():
    """The competition card renders both the idle block (team/question
    selects) and the active block (timer/poll controls) server-side, so
    start/next/cancel kick the poll loop to swap visibility in place
    instead of reloading. Reload stays only as the fallback when no poll
    loop is running or the block structure is missing."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    template = (PROJECT_ROOT / "templates" / "instructor.html").read_text(
        encoding="utf-8"
    )

    assert 'id="presentation-active"' in template
    assert 'id="presentation-idle"' in template
    assert "function instructorPresentationChanged(" in source
    assert "function reconcilePresentationBlocks(" in source
    for handler in ("window.startPresentation", "window.nextPresentation",
                    "window.cancelPresentation"):
        start = source.index(handler)
        body = source[start:start + 1500]
        assert "window.instructorPollOnce()" in body, handler
        # Reload stays only as the no-poll-loop fallback.
        assert body.count("window.location.reload();") == 1, handler


def test_mathjax_cdn_is_pinned_with_sri():
    """The MathJax CDN script is pinned to an exact version and carries a
    Subresource Integrity hash so a CDN compromise cannot inject JS."""
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )

    assert re.search(r"mathjax@\d+\.\d+\.\d+/", base)
    assert "mathjax@3/" not in base
    assert 'integrity="sha384-' in base
    assert 'crossorigin="anonymous"' in base
