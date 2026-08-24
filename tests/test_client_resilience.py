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
    assert source.count("UPLOAD_TIMEOUT_MS") == 5
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
    """Question cards track expansion and numbering by server question key so
    re-renders do not collapse open cards or change their order."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "window.toggleDiscQuestionCard" in source
    assert "function discussionQuestionNumber(" in source
    assert "_discQuestionExpanded.has(key)" in source
    assert "#${i + 1}" not in source


def test_instructor_vote_progress_uses_compact_counts():
    """Live presentation and challenge progress omit individual names."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "`${count} out of ${eligible} students voted`" in source
    assert "`${submitted} out of ${eligible} students voted`" in source
    assert "poll_non_raters" not in source
    assert "poll_online_eligible_count" not in source
    assert "thumb_online_eligible_count" not in source
    assert "challenge_rating_counts" not in source
    assert "online_eligible_count" not in source
    assert "active in last 3 minutes" not in source
    assert "Still to rate:" not in source
    assert 'class="challenge-active-details"' in source
    challenge_renderer = source[
        source.index("function renderInstructorChallenge"):
        source.index("window.selectChallenger")
    ]
    assert "Challenge rating:" not in challenge_renderer
    assert "flex-wrap:wrap" not in challenge_renderer

    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )
    assert ".challenge-active-row {" in css
    assert "display: flex;" in css
    assert ".challenge-active-details {" in css
    assert "flex: 1;" in css
    assert "min-width: 0;" in css
    assert ".challenge-active-row .challenge-mutating-btn {" in css
    assert "flex: 0 0 auto;" in css


def test_instructor_roster_keeps_identity_and_team_turns_readable():
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    assert ".instructor .setup-roster-member {" in css
    assert ".setup-roster-member-topline {" in css
    assert ".setup-roster-member-identity {" in css
    roster_rules = css[
        css.index(".instructor .setup-roster-member {"):
        css.index(".participation-badge {", css.index(
            ".instructor .setup-roster-member {"
        ))
    ]
    assert "flex-wrap: nowrap;" in roster_rules
    assert "overflow-wrap: anywhere;" in roster_rules


def test_instructor_setup_and_presentation_controls_are_contextual_and_compact():
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    template = (
        PROJECT_ROOT / "templates" / "instructor.html"
    ).read_text(encoding="utf-8")
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    setup_roster = template[
        template.index("<h2>Team and Members</h2>"):
        template.index("<h2>Student Management</h2>")
    ]
    assert "Team turns:" in setup_roster
    assert "Challenge turns:" not in setup_roster
    assert "Challenge turns" in template
    assert "setupRosterGrid, rosterData, null," in source
    assert "{ instructorSetup: true }" in source
    assert "applyCompetitionParticipationRoster(rosterData)" in source

    assert 'id="reset-course-data-menu-item"' in base
    assert "state.phase in ['setup', 'ended']" in base
    assert "Reset Course Data" not in template
    assert ".nav-dropdown-action {" in css

    assert (
        "{{ (presentation_members | length) - participation.never }}"
        in template
    )
    assert "no prior turn" not in template
    assert (
        "`${teamName} · ${priorCount}/${memberCount}${completed}`"
        in source
    )
    assert "previously presented" not in template
    assert "previously presented" not in source
    assert "' (completed)'" in source
    assert "data-completed=" in template

    assert 'class="form-group time-cap-group"' in template
    assert ".form-row .time-cap-group {" in css

    assert "flex: 0 0 140px;" in css
    assert "min-width: 120px;" in css


def test_selected_challenger_state_blocks_repeat_raise_and_clear_is_explicit():
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "state.is_active_challenger === true" in source
    assert "✓ Selected as Challenger" in source
    assert "You have been selected as the challenger." in source
    assert "lastReceivedState?.is_active_challenger === true" in source
    assert "'|state=' + stateVersion" in source
    assert "Stop Poll, wait for final ratings to save" in source
    assert "Challenger cleared. The student may raise their hand again." in source


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


def test_weekly_question_upload_previews_then_resends_same_file_on_confirm():
    """The Setup upload validates first, then resends the same File object
    with the preview token before replacing the common weekly question bank."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    template = (PROJECT_ROOT / "templates" / "instructor.html").read_text(
        encoding="utf-8"
    )

    assert source.count("'/api/upload_questions'") == 2
    assert "window.previewWeeklyQuestionUpload" in source
    assert "window.confirmWeeklyQuestionUpload" in source
    assert "formData.append('week', String(week));" in source
    assert "pending.file, pending.week, pending.token" in source
    assert "formData.append('confirm', 'true');" in source
    assert "formData.append('preview_token', token);" in source
    assert "appendInstructorStateForm(formData);" in source
    assert "const trackInstructorSave = beginInstructorSave();" in source
    assert "finishInstructorSave(trackInstructorSave, confirmedSuccess);" in source
    assert "uploaded with ${count} shared questions for both question-based phases" in source

    card_position = template.index('id="weekly-question-upload-card"')
    demo_guard = template.rfind(
        "{% if not session.get('is_demo') %}", 0, card_position
    )
    assert demo_guard >= 0
    assert 'accept=".md,text/markdown,text/plain"' in template
    assert 'id="weekly-question-upload-preview" hidden' in template
    assert "The two question-based phases use the exact same questions" in template
    assert "Group Discussion and Present and Challenge" in template


def test_question_load_surfaces_server_error_message():
    """A 422 from /api/discussion_questions shows the server's error text
    instead of only the generic refresh hint."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "loadError.serverMessage =" in source
    assert "(questionData && questionData.error) || null;" in source
    assert (
        "serverMessage ||\n"
        "                        'Could not load discussion questions. "
        "Retrying automatically.'"
    ) in source
    assert "scheduleQuestionLoadRetry();" in source
    assert "let renderedDiscQuestionsVersion = null;" in source


def test_student_table_page_size_and_picker_guard():
    """The student table page size is selectable/persisted, and the 5s
    auto-refresh skips only an open per-student assignment picker."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    template = (PROJECT_ROOT / "templates" / "instructor.html").read_text(
        encoding="utf-8"
    )

    assert "per_page: studentPerPage" in source
    assert "popping-student-per-page" in source
    assert "window.setStudentPerPage" in source
    assert 'for="team-filter-select"' in template
    assert 'id="team-filter-select"' in template
    assert 'onchange="setTeamFilterFromSelect(this)"' in template
    assert 'class="team-picker filter-picker"' not in template
    assert "window.setTeamFilterFromSelect = function(select)" in source
    assert "!document.querySelector('.team-picker:not(.filter-picker)')" in source
    assert "!document.querySelector('.team-picker')" not in source
    assert "Date.now() - _lastStudentTableRefresh >= 5000" in source


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
    assert "function redirectToSessionEnded()" in source
    assert source.count("window.location.href = '/?session=ended';") == 1
    assert source.count("redirectToSessionEnded();") >= 10


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
    """Discussion question expanders use native buttons with visible focus and
    synchronized aria-expanded state."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )
    header_start = source.index("function discussionQuestionHeaderHtml(")
    header_end = source.index("function normalizedIdentityText(", header_start)
    header = source[header_start:header_end]

    assert '<button type="button" class="disc-question-toggle"' in header
    assert "aria-expanded=\"${expanded ? 'true' : 'false'}\"" in header
    assert 'aria-controls="${escapeAttrValue(bodyId)}"' in header
    assert "window.toggleDiscQuestionCard = function(toggle)" in source
    assert "toggle.setAttribute(" in source
    assert ".disc-question-toggle:focus-visible" in css


def test_response_controls_reconcile_every_fifteen_seconds():
    """Student response controls periodically reconcile with server truth."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "const RESPONSE_RECONCILE_INTERVAL_MS = 15000;" in source
    assert "Date.now() - _lastResponsesSyncAt >=" in source
    assert "if (periodicDue) responseNeedsRefresh = true;" in source


def test_hidden_tabs_back_off_student_and_instructor_polling():
    """Both dashboards avoid rapid polling while their tab is hidden."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "document.hidden ? Math.max(7500, interval) : interval" in source
    assert "const minimum = document.hidden ? 7500 : 1000;" in source


def test_question_readiness_is_rendered_from_the_server_contract():
    """A missing weekly file remains visible to the instructor."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function updateQuestionReadinessWarning(data)")
    end = source.index("function competitionQuestionLabel", start)
    readiness = source[start:end]

    assert "const ready = data?.ready !== false;" in readiness
    assert "weekly questions are not ready" in readiness
    assert "data?.issues" in readiness
    assert "updateQuestionReadinessWarning(data);" in source


def test_competition_question_rebuild_never_infers_ids_by_position():
    """Competition options use server IDs or stable keys, never list position."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function rebuildCompetitionQuestionOptions(data)")
    end = source.index("// Populate Setup controls", start)
    rebuild = source[start:end]

    assert "Number(question?.question_id)" in rebuild
    assert "existingById.get" in rebuild
    assert "existingByKey.get" in rebuild
    assert "existing[position]" not in rebuild
    assert "existing.length === questions.length" not in rebuild
    assert "rebuildCompetitionQuestionOptions(data);" in source


def test_instructor_mutation_error_keeps_extended_roster_details():
    """Detailed roster errors extend, but never replace, another server error."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    start = source.index("function showInstructorMutationError(data, fallback)")
    end = source.index("function applyRosterMutationVersion", start)
    helper = source[start:end]

    assert "fallbackMessage.startsWith(`${serverMessage}:`)" in helper
    assert "? fallbackMessage" in helper
    assert ": (serverMessage || fallbackMessage);" in helper


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
        end = source.index("\n};", start) + len("\n};")
        body = source[start:end]
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


def test_mathjax_cdn_failure_has_a_plain_text_fallback_notice():
    """A blocked school-network CDN must not fail silently."""
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'onerror="showMathJaxLoadWarning()"' in base
    assert "id = 'mathjax-load-warning'" in base
    assert "Questions and controls still work" in base

def test_discussion_question_ui_cannot_diverge_from_presentation():
    """The instructor cannot hide a question only from Discussion."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    template = (PROJECT_ROOT / "templates" / "instructor.html").read_text(
        encoding="utf-8"
    )
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )

    assert "toggleDiscussionQuestion" not in source
    assert "/api/toggle_discussion_question" not in source
    assert "hidden-question-badge" not in source
    assert "hidden-question-badge" not in css
    assert "disc-question-card.is-hidden" not in css
    assert "same questions, in this order" in template


def test_active_markdown_and_download_menu_fit_small_screens():
    """Long question content and the nested download menu stay usable on mobile."""
    css = (PROJECT_ROOT / "static" / "css" / "style.css").read_text(
        encoding="utf-8"
    )
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )

    assert ".markdown-content pre {" in css
    assert ".markdown-content table {" in css
    assert ".markdown-content img {" in css
    assert "overflow-x: auto;" in css
    assert "@media (max-width: 640px)" in css
    assert ".nav-submenu-menu {" in css
    assert "position: static;" in css
    assert 'id="tools-dropdown-trigger"' in base
    assert 'id="download-results-trigger"' in base
    assert 'aria-controls="download-results-menu"' in base


def test_frontend_dialogs_restore_focus_and_close_with_escape():
    """Both instructor dialogs expose modal semantics and keyboard recovery."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    base = (PROJECT_ROOT / "templates" / "base.html").read_text(
        encoding="utf-8"
    )

    assert 'id="roster-preview-dialog" role="dialog" aria-modal="true"' in base
    assert "function handleModalKeydown(" in source
    assert "event.key === 'Escape'" in source
    assert "rosterPreviewReturnFocus" in source
    assert "weekPreviewReturnFocus" in source
    assert "overlay.setAttribute('role', 'dialog')" in source
    assert "overlay.setAttribute('aria-modal', 'true')" in source
    assert "handleModalKeydown(event, overlay, closeWeekPreviewModal)" in source
    assert "week, questions, returnFocus = document.activeElement" in source
    assert "data.questions || [], previewButton" in source


def test_appendix_add_uses_retry_safe_request_identity():
    """The same unsaved add payload keeps one key across an uncertain retry."""
    source = (PROJECT_ROOT / "static" / "js" / "app.js").read_text(
        encoding="utf-8"
    )

    assert "function ensureAppendixCreateRequest(" in source
    assert "client_request_id: createRequestId" in source
    assert "clientRequestId:" in source
    assert "clientRequestFingerprint:" in source
    assert "appendixCreateRequestFingerprint !== fingerprint" in source
    assert "resetAppendixCreateRequest();" in source
