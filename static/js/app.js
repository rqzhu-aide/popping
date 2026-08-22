const REQUEST_TIMEOUT_MS = 15000;
const UPLOAD_TIMEOUT_MS = 30000;
const VOTE_RETRY_OPTIONS = Object.freeze({ retryVoteOnce: true });
let pageNavigationInProgress = false;
let forcedPageNavigationInProgress = false;

function redirectToSessionEnded() {
    if (pageNavigationInProgress) return;
    pageNavigationInProgress = true;
    forcedPageNavigationInProgress = true;
    window.location.href = '/?session=ended';
}

function beginPageNavigation(form) {
    if (pageNavigationInProgress) return false;
    pageNavigationInProgress = true;
    form?.querySelectorAll('button[type="submit"]').forEach(button => {
        button.disabled = true;
    });
    return true;
}
window.beginPageNavigation = beginPageNavigation;

async function fetchJSONWithTimeout(url, options = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
        const res = await fetch(url, { ...options, signal: controller.signal });
        const data = await res.json().catch(() => ({}));
        return { res, data };
    } finally {
        window.clearTimeout(timeout);
    }
}

let _instructorSaveCount = 0;
let _instructorSaveFailed = false;
let _instructorSaveStatusTimer = null;

function beginInstructorSave() {
    const status = document.getElementById('instructor-save-status');
    if (!status) return false;
    if (_instructorSaveCount === 0) _instructorSaveFailed = false;
    _instructorSaveCount += 1;
    if (_instructorSaveStatusTimer) {
        window.clearTimeout(_instructorSaveStatusTimer);
        _instructorSaveStatusTimer = null;
    }
    status.dataset.visible = 'true';
    status.dataset.state = 'saving';
    status.textContent = 'Saving';
    return true;
}

function finishInstructorSave(tracked, success) {
    if (!tracked) return;
    if (!success) _instructorSaveFailed = true;
    _instructorSaveCount = Math.max(0, _instructorSaveCount - 1);
    if (_instructorSaveCount > 0) return;
    const status = document.getElementById('instructor-save-status');
    if (!status) return;
    if (_instructorSaveFailed) {
        status.textContent = '';
        delete status.dataset.state;
        delete status.dataset.visible;
        return;
    }
    status.dataset.visible = 'true';
    status.dataset.state = 'saved';
    status.textContent = 'Saved';
    _instructorSaveStatusTimer = window.setTimeout(() => {
        status.textContent = '';
        delete status.dataset.state;
        delete status.dataset.visible;
        _instructorSaveStatusTimer = null;
    }, 2500);
}

function busyRetryDelayMs(data) {
    const seconds = Number(data?.retry_after);
    if (!Number.isFinite(seconds) || seconds <= 0) return null;
    return Math.round(Math.min(5, Math.max(1, seconds)) * 1000);
}

async function postJSON(url, body, options = {}) {
    const trackInstructorSave = beginInstructorSave();
    let confirmedSuccess = false;
    const serializedBody = JSON.stringify(body);
    const maxAttempts = options.retryVoteOnce === true ? 2 : 1;
    let networkOutcomeUnknown = false;
    try {
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
            let result;
            try {
                result = await fetchJSONWithTimeout(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: serializedBody
                });
            } catch (error) {
                networkOutcomeUnknown = true;
                if (attempt === 0 && maxAttempts === 2) {
                    await new Promise(resolve =>
                        window.setTimeout(resolve, 2000));
                    continue;
                }
                return {
                    success: false,
                    error: 'Could not confirm the change because of a network error. Refresh to check.',
                    _network_error: true,
                    _status: 0
                };
            }
            const { res, data } = result;
            if (res.status === 401) {
                redirectToSessionEnded();
                return { success: false, error: 'Session expired' };
            }
            data._status = res.status;
            if (!res.ok && !data.error) data.error = 'Request failed';
            const retryDelay = busyRetryDelayMs(data);
            if (attempt === 0 && maxAttempts === 2 &&
                    res.status === 503 && retryDelay !== null) {
                await new Promise(resolve => window.setTimeout(resolve, retryDelay));
                continue;
            }
            confirmedSuccess = res.ok && data.success === true;
            if (!confirmedSuccess && networkOutcomeUnknown) {
                data._network_error = true;
                data.error =
                    'Could not confirm the vote because the connection was interrupted.';
            }
            return data;
        }
    } finally {
        finishInstructorSave(trackInstructorSave, confirmedSuccess);
    }
}

async function deleteJSON(url, body = {}) {
    const trackInstructorSave = beginInstructorSave();
    let confirmedSuccess = false;
    try {
        let result;
        try {
            result = await fetchJSONWithTimeout(url, {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
        } catch (error) {
            return {
                success: false,
                error: 'Could not confirm the change because of a network error. Refresh to check.',
                _network_error: true,
                _status: 0
            };
        }
        const { res, data } = result;
        if (res.status === 401) {
            redirectToSessionEnded();
            return { success: false, error: 'Session expired' };
        }
        data._status = res.status;
        if (!res.ok && !data.error) data.error = 'Request failed';
        confirmedSuccess = res.ok && data.success === true;
        return data;
    } finally {
        finishInstructorSave(trackInstructorSave, confirmedSuccess);
    }
}
function escapeHtmlValue(value) {
    const div = document.createElement('div');
    div.textContent = value == null ? '' : String(value);
    return div.innerHTML;
}

function escapeAttrValue(value) {
    return escapeHtmlValue(value)
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Discussion question cards keep expansion state by stable question key.
// Display numbers come from the server so instructors and students share one
// canonical sequence even when some questions are hidden.
const _discQuestionExpanded = new Set();

function discussionQuestionKey(q) {
    return (q && (q.key || q._key)) || '';
}

function discussionQuestionNumber(q, position) {
    const displayNumber = Number(q?.display_number);
    if (Number.isInteger(displayNumber) && displayNumber > 0) {
        return displayNumber;
    }
    // Compatibility fallback for an older or malformed response.
    return position + 1;
}

window.toggleDiscQuestionCard = function(header) {
    const card = header.parentElement;
    card.classList.toggle('expanded');
    header.setAttribute(
        'aria-expanded',
        card.classList.contains('expanded') ? 'true' : 'false'
    );
    const key = card.dataset.questionKey;
    if (!key) return;
    if (card.classList.contains('expanded')) {
        _discQuestionExpanded.add(key);
    } else {
        _discQuestionExpanded.delete(key);
    }
};

// The accordion header is a div (it can contain real action buttons), so it
// needs explicit keyboard support: Enter/Space toggles it like a click.
window.discQuestionHeaderKeydown = function(event) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (event.target !== event.currentTarget) return;
    event.preventDefault();
    event.currentTarget.click();
};

/** Format a student as 'Name (id)' or just 'id' if no name / name equals id. */
function formatStudentDisplay(name, studentId) {
    return (name && name !== studentId)
        ? `${name} (${studentId})`
        : studentId;
}

function normalizedParticipationCount(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
}

function participationBadgesHtml(record) {
    if (!record ||
            !Object.prototype.hasOwnProperty.call(record, 'presentation_count') ||
            !Object.prototype.hasOwnProperty.call(record, 'challenger_count')) {
        return '';
    }
    const presentationCount = normalizedParticipationCount(record.presentation_count);
    const challengerCount = normalizedParticipationCount(record.challenger_count);
    return `<span class="participation-badges" aria-label="Participation history">
        <span class="participation-badge${presentationCount === 0 ? ' is-zero' : ''}">Team turns: <strong>${presentationCount}</strong></span>
        <span class="participation-badge${challengerCount === 0 ? ' is-zero' : ''}">Challenge turns: <strong>${challengerCount}</strong></span>
    </span>`;
}

/**
 * Rebuild a roster grid from a teams array.  Skips rebuild if data hasn't
 * changed (signature comparison).  Shared by student + instructor panels.
 * @param myId  student_id of current user (for 'You' highlight), or null
 */
function renderRosterGrid(container, teams, myId) {
    if (!container) return;
    const sig = JSON.stringify(teams);
    if (sig === container.dataset.lastRoster) return;
    container.dataset.lastRoster = sig;
    container.innerHTML = '';
    teams.forEach(team => {
        const div = document.createElement('div');
        div.className = 'roster-team';
        const members = Array.isArray(team.members) ? team.members : [];
        const membersHtml = members.map(m => {
            const display = formatStudentDisplay(m.name, m.student_id);
            const isYou = myId && m.student_id === myId;
            const badgesHtml = participationBadgesHtml(m);
            return `<div class="roster-member">
                <span class="team-dot" style="background:${escapeAttrValue(team.color)}"></span>
                <span class="roster-member-name ${isYou ? 'you' : ''}">${escapeHtmlValue(display)}${isYou ? ' (You)' : ''}</span>
                ${badgesHtml}
            </div>`;
        }).join('');
        const memberCount = Number(team.member_count) || members.length;
        const memberSummary = team.members_visible === false
            ? `<span class="hint">${memberCount} member${memberCount === 1 ? '' : 's'}</span>`
            : (membersHtml || '<em>No members</em>');
        div.innerHTML = `<h3><span class="team-dot" style="background:${escapeAttrValue(team.color)}"></span>${escapeHtmlValue(team.name)}</h3>${memberSummary}`;
        container.appendChild(div);
    });
}

/** Parse Markdown → sanitized HTML (XSS-safe via DOMPurify). */
function renderMarkdown(md) {
    if (!window.marked) {
        const fallback = document.createElement('div');
        fallback.textContent = md || '';
        return fallback.innerHTML;
    }
    const raw = marked.parse(md || '');
    if (window.DOMPurify) return DOMPurify.sanitize(raw);
    // Fail-closed: escape HTML if DOMPurify failed to load (CDN issue)
    const div = document.createElement('div');
    div.textContent = raw;
    return div.innerHTML;
}

function sanitizeHtml(html) {
    if (window.DOMPurify) return DOMPurify.sanitize(html || '');
    const div = document.createElement('div');
    div.textContent = html || '';
    return div.innerHTML;
}

function renderPresentationQuestion(question) {
    const sourceKey = question.source_key || '';
    const title = question.title || '';
    const heading = sourceKey.startsWith('appendix:')
        ? `Appendix: ${title}`
        : `#${question.question_num}: ${title}`;
    const body = question.content || question.question_text || '';
    return renderMarkdown(`${heading}\n\n${body}`);
}

/** Safely run MathJax typeset if the library has loaded. */
function safeTypeset(el) {
    if (window.MathJax && typeof window.MathJax.typesetPromise === 'function') {
        window.MathJax.typesetPromise([el]);
    }
}

/** Highlight code blocks within an element (scoped, not whole page). */
function safeHighlight(container) {
    if (window.hljs) {
        container.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));
    }
}

/* ===== TOAST NOTIFICATIONS ===== */
function showToast(msg, type) {
    let container = document.querySelector('.toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'toast-container';
        container.setAttribute('role', 'status');
        container.setAttribute('aria-live', 'polite');
        document.body.appendChild(container);
    }
    const toast = document.createElement('div');
    toast.className = 'toast' + (type ? ' toast-' + type : '');
    toast.textContent = msg;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 2700);
}

/* ===== NAV DROPDOWN ===== */
window.toggleToolsDropdown = function() {
    const menu = document.getElementById('tools-menu');
    if (menu) menu.classList.toggle('open');
};

/* Touch/keyboard fallback for the Download Results flyout (hover opens it
   on pointer devices via CSS). */
window.toggleDownloadResults = function(e) {
    e.preventDefault();
    e.stopPropagation();
    const menu = document.getElementById('download-results-menu');
    if (menu) menu.classList.toggle('open');
};

document.addEventListener('click', function(e) {
    const dropdown = document.getElementById('tools-dropdown');
    if (dropdown && !dropdown.contains(e.target)) {
        const menu = document.getElementById('tools-menu');
        if (menu) menu.classList.remove('open');
    }
});

window.uploadRoster = function(e) {
    e.preventDefault();
    const menu = document.getElementById('tools-menu');
    if (menu) menu.classList.remove('open');
    document.getElementById('roster-file-input').click();
};

let pendingRosterUpload = null;

function appendInstructorStateForm(formData) {
    const panel = document.querySelector('.instructor[data-slug]');
    if (!panel) return;
    formData.append('expected_phase', panel.dataset.phase || '');
    formData.append('expected_session_key', panel.dataset.sessionKey || '0');
    formData.append('expected_roster_version', panel.dataset.rosterVersion || '0');
}

const flashMessage = sessionStorage.getItem('popping-flash-message');
if (flashMessage) {
    sessionStorage.removeItem('popping-flash-message');
    window.setTimeout(() => showToast(flashMessage, 'success'), 0);
}

function rosterErrorMessage(data, fallback) {
    const allDetails = Array.isArray(data?.details) ? data.details : [];
    const details = allDetails.slice(0, 3);
    if (allDetails.length > details.length) details.push(`and ${allDetails.length - details.length} more`);
    return [data?.error || fallback, ...details].filter(Boolean).join(': ');
}

function closeRosterPreview() {
    const dialog = document.getElementById('roster-preview-dialog');
    if (dialog) dialog.hidden = true;
    pendingRosterUpload = null;
    const input = document.getElementById('roster-file-input');
    if (input) input.value = '';
}

function showRosterPreview(file, data) {
    pendingRosterUpload = { file, token: data.preview_token };
    const dialog = document.getElementById('roster-preview-dialog');
    const summary = document.getElementById('roster-preview-summary');
    if (!dialog || !summary) return;

    summary.innerHTML = `
        <p><strong>${escapeHtmlValue(file.name)}</strong> contains ${Number(data.total) || 0} students.</p>
        <ul>
            <li>${Math.max(0, (Number(data.added) || 0) - (Number(data.reactivated) || 0))} new students</li>
            <li>${Number(data.reactivated) || 0} archived students reactivated</li>
            <li>${Number(data.updated) || 0} existing students updated</li>
            <li class="${data.removed ? 'text-danger' : ''}">${Number(data.removed) || 0} students archived</li>
        </ul>
        <p class="roster-replace-warning">This replaces the active roster. Archived students cannot log in, but their response history is retained and re-adding the same ID reactivates it.</p>
    `;
    dialog.hidden = false;
    document.getElementById('btn-confirm-roster-upload')?.focus();
}

window.handleRosterFile = async function(e) {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const formData = new FormData();
        formData.append('file', file);
        appendInstructorStateForm(formData);
        const { res, data } = await fetchJSONWithTimeout(
            '/api/upload_roster',
            { method: 'POST', body: formData },
            UPLOAD_TIMEOUT_MS
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (res.ok && data.requires_confirmation && data.preview_token) {
            showRosterPreview(file, data);
        } else {
            data._status = res.status;
            showInstructorMutationError(
                data,
                rosterErrorMessage(data, 'Could not validate the roster file')
            );
            e.target.value = '';
        }
    } catch (error) {
        showToast('Network error while validating the roster', 'error');
        e.target.value = '';
    }
};

window.cancelRosterUpload = closeRosterPreview;

window.confirmRosterUpload = async function() {
    if (!pendingRosterUpload) return;
    const btn = document.getElementById('btn-confirm-roster-upload');
    const trackInstructorSave = beginInstructorSave();
    let confirmedSuccess = false;
    if (btn) { btn.disabled = true; btn.textContent = 'Replacing...'; }
    try {
        const formData = new FormData();
        formData.append('file', pendingRosterUpload.file);
        formData.append('confirm', 'true');
        formData.append('preview_token', pendingRosterUpload.token);
        appendInstructorStateForm(formData);
        const { res, data } = await fetchJSONWithTimeout(
            '/api/upload_roster',
            { method: 'POST', body: formData },
            UPLOAD_TIMEOUT_MS
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (!res.ok || !data.success) {
            data._status = res.status;
            showInstructorMutationError(
                data,
                rosterErrorMessage(data, 'Roster replacement failed')
            );
            return;
        }
        showToast(`Roster replaced: ${data.added} added, ${data.updated} updated, ${data.removed || 0} removed.`, 'success');
        confirmedSuccess = true;
        closeRosterPreview();
        window.location.reload();
    } catch (error) {
        showToast('Network error while replacing the roster', 'error');
    } finally {
        finishInstructorSave(trackInstructorSave, confirmedSuccess);
        if (btn) { btn.disabled = false; btn.textContent = 'Replace Roster'; }
    }
};

/* ===== WEEKLY QUESTION UPLOAD ===== */

let pendingWeeklyQuestionUpload = null;

function clearWeeklyQuestionUploadPreview(resetFile = false) {
    pendingWeeklyQuestionUpload = null;
    const preview = document.getElementById('weekly-question-upload-preview');
    if (preview) preview.hidden = true;
    const title = document.getElementById('weekly-question-upload-preview-title');
    if (title) title.textContent = '';
    const list = document.getElementById('weekly-question-upload-preview-list');
    if (list) list.innerHTML = '';
    if (resetFile) {
        const fileInput = document.getElementById('weekly-question-file');
        if (fileInput) fileInput.value = '';
    }
}

window.cancelWeeklyQuestionUpload = function() {
    clearWeeklyQuestionUploadPreview(false);
};

function weeklyQuestionUploadForm(file, week, token = null) {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('week', String(week));
    if (token) {
        formData.append('confirm', 'true');
        formData.append('preview_token', token);
    }
    appendInstructorStateForm(formData);
    return formData;
}

function showWeeklyQuestionUploadPreview(file, requestedWeek, data) {
    const week = Number(data.week) || requestedWeek;
    const questions = Array.isArray(data.questions) ? data.questions : [];
    const count = Number(data.question_count) || questions.length;
    pendingWeeklyQuestionUpload = {
        file,
        week,
        token: data.preview_token
    };

    const title = document.getElementById('weekly-question-upload-preview-title');
    if (title) {
        title.textContent = `Week ${week}: ${count} question${count === 1 ? '' : 's'}`;
    }
    const list = document.getElementById('weekly-question-upload-preview-list');
    if (list) {
        list.innerHTML = questions.map(question => {
            const id = escapeHtmlValue(question?.id || '');
            const questionTitle = escapeHtmlValue(question?.title || 'Untitled');
            return `<li>${id ? `<code>${id}</code>: ` : ''}${questionTitle}</li>`;
        }).join('');
    }
    const preview = document.getElementById('weekly-question-upload-preview');
    if (preview) preview.hidden = false;
    document.getElementById('btn-confirm-question-upload')?.focus();
}

window.previewWeeklyQuestionUpload = async function() {
    const weekInput = document.getElementById('weekly-question-week');
    const fileInput = document.getElementById('weekly-question-file');
    const button = document.getElementById('btn-preview-question-upload');
    const week = Number(weekInput?.value);
    const file = fileInput?.files?.[0];
    if (!Number.isInteger(week) || week < 1) {
        showToast('Enter a positive lecture week number', 'warning');
        weekInput?.focus();
        return;
    }
    if (!file) {
        showToast('Choose a Markdown question file', 'warning');
        fileInput?.focus();
        return;
    }
    if (!file.name.toLowerCase().endsWith('.md')) {
        showToast('Weekly questions must be uploaded as a .md file', 'warning');
        return;
    }

    clearWeeklyQuestionUploadPreview(false);
    if (button) {
        button.disabled = true;
        button.textContent = 'Validating...';
    }
    try {
        const { res, data } = await fetchJSONWithTimeout(
            '/api/upload_questions',
            { method: 'POST', body: weeklyQuestionUploadForm(file, week) },
            UPLOAD_TIMEOUT_MS
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (res.ok && data.requires_confirmation && data.preview_token) {
            showWeeklyQuestionUploadPreview(file, week, data);
            return;
        }
        data._status = res.status;
        showInstructorMutationError(data, 'Could not validate the weekly question file');
    } catch (error) {
        showToast(
            error?.name === 'AbortError'
                ? 'Question upload preview timed out. Please try again.'
                : 'Network error while validating the weekly questions',
            'error'
        );
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = 'Preview Upload';
        }
    }
};

window.confirmWeeklyQuestionUpload = async function() {
    if (!pendingWeeklyQuestionUpload) return;
    const pending = pendingWeeklyQuestionUpload;
    const confirmButton = document.getElementById('btn-confirm-question-upload');
    const previewButton = document.getElementById('btn-preview-question-upload');
    const trackInstructorSave = beginInstructorSave();
    let confirmedSuccess = false;
    if (confirmButton) {
        confirmButton.disabled = true;
        confirmButton.textContent = 'Replacing...';
    }
    if (previewButton) previewButton.disabled = true;
    try {
        const { res, data } = await fetchJSONWithTimeout(
            '/api/upload_questions',
            {
                method: 'POST',
                body: weeklyQuestionUploadForm(
                    pending.file, pending.week, pending.token
                )
            },
            UPLOAD_TIMEOUT_MS
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (!res.ok || !data.success) {
            data._status = res.status;
            showInstructorMutationError(data, 'Weekly question replacement failed');
            return;
        }

        confirmedSuccess = true;
        const week = Number(data.week) || pending.week;
        const count = Number(data.question_count) || 0;
        clearWeeklyQuestionUploadPreview(true);
        const weekInput = document.getElementById('weekly-question-week');
        if (weekInput) weekInput.value = String(week);
        await initWeekSelector().catch(() => null);
        const weekSelect = document.getElementById('setup-week-select');
        if (weekSelect && Array.from(weekSelect.options).some(
                option => option.value === String(week))) {
            weekSelect.value = String(week);
        }
        showToast(
            `Week ${week} uploaded. Discussion and Presentation will use these exact ${count} questions. Select Apply Week when ready.`,
            'success'
        );
    } catch (error) {
        showToast(
            error?.name === 'AbortError'
                ? 'Question replacement timed out. Refresh the question list before trying again.'
                : 'Could not confirm the question replacement because of a network error',
            'error'
        );
    } finally {
        finishInstructorSave(trackInstructorSave, confirmedSuccess);
        if (confirmButton) {
            confirmButton.disabled = false;
            confirmButton.textContent = 'Confirm Replacement';
        }
        if (previewButton) previewButton.disabled = false;
    }
};

['weekly-question-week', 'weekly-question-file'].forEach(id => {
    document.getElementById(id)?.addEventListener(
        id === 'weekly-question-file' ? 'change' : 'input',
        () => clearWeeklyQuestionUploadPreview(false)
    );
});

/* ===== STUDENT DASHBOARD ===== */
const PHASE_LABELS = {
    setup: 'Setup',
    discussion: 'Group Discussion',
    competition: 'Group Presentation',
    ended: 'End Session'
};

const PHASES_WITH_DISCUSSION = ['discussion'];
const PHASES_WITH_TEAM = ['competition'];

// Track poll state
let pollSelections = { q1: 0, q2: 0 };
let savedPollSelections = null;
let lastRatedPresentationKey = null; // track active presentation to reset the rating UI
let lastRenderedQuestionKey = null; // track active question to avoid re-rendering same HTML
let lastKnownQuestionId = null;    // lets the server omit unchanged question bodies
let lastKnownQuestionRevision = null;
let lastKnownStateVersion = null;  // lets the server skip _compute_state when unchanged
let activePollOpen = false;
let ratingPollOpenForTimer = false;  // presentation timer shows "time's up" instead of alarm while a rating poll is open
let ratingSubmissionInProgress = false;
let unconfirmedPresentationRating = null;
let ratingDraftDirty = false;
let _responseRequestId = 0;
let responseNeedsRefresh = false;
let _studentVoteInFlight = 0;
let _studentVoteBatchFailure = null;
let _studentVoteKnownFailure = false;
let _studentVoteLastSuccess = 'Vote submitted';
let _studentVoteStatusTimer = null;
const _unconfirmedThumbVotes = new Map();
const _unconfirmedChallengeRatings = new Map();
let _allowAutomaticVoteNavigation = false;
let _pendingDashboardReloadPhase = null;
let _pendingDashboardReloadSessionKey = null;
let _pendingDashboardReloadStartedAt = 0;
let _unconfirmedThumbSessionKey = null;
const DASHBOARD_RELOAD_READBACK_GRACE_MS = 5000;
const _unconfirmedVoteWarningKey =
    'popping-unconfirmed-teammate-vote-warning';
const _unsavedVoteDraftWarningKey =
    'popping-unsaved-rating-draft-warning';

function setStudentVoteStatus(message, state) {
    const status = document.getElementById('student-vote-status');
    if (!status) return;
    if (_studentVoteStatusTimer) {
        window.clearTimeout(_studentVoteStatusTimer);
        _studentVoteStatusTimer = null;
    }
    status.dataset.visible = 'true';
    status.dataset.state = state;
    status.textContent = message;
    if (state === 'submitted') {
        _studentVoteStatusTimer = window.setTimeout(() => {
            status.textContent = '';
            delete status.dataset.state;
            delete status.dataset.visible;
            _studentVoteStatusTimer = null;
        }, 4000);
    }
}

function beginStudentVote() {
    if (_studentVoteInFlight === 0 &&
            _unconfirmedThumbVotes.size === 0 &&
            _unconfirmedChallengeRatings.size === 0 &&
            !unconfirmedPresentationRating) {
        _studentVoteBatchFailure = null;
        _studentVoteKnownFailure = false;
        _studentVoteLastSuccess = 'Vote submitted';
    }
    _studentVoteInFlight += 1;
    setStudentVoteStatus('Submitting your vote ...', 'submitting');
}

function finishStudentVote(
        success, successMessage, failureMessage, unconfirmed = false) {
    if (success) {
        _studentVoteLastSuccess = successMessage || 'Vote submitted';
    } else {
        const message = failureMessage ||
            'Vote not submitted. Please try again.';
        if (!unconfirmed) _studentVoteKnownFailure = true;
        if (!_studentVoteBatchFailure || !unconfirmed) {
            _studentVoteBatchFailure = message;
        }
    }
    _studentVoteInFlight = Math.max(0, _studentVoteInFlight - 1);
    if (_studentVoteInFlight > 0) {
        setStudentVoteStatus('Submitting your vote ...', 'submitting');
        return;
    }
    if (_studentVoteBatchFailure) {
        setStudentVoteStatus(_studentVoteBatchFailure, 'error');
    } else {
        setStudentVoteStatus(_studentVoteLastSuccess, 'submitted');
    }
}

function confirmSuccessfulThumbVote(recipientId, retryingUnconfirmed) {
    _unconfirmedThumbVotes.delete(String(recipientId));
    if (_unconfirmedThumbVotes.size === 0) {
        _unconfirmedThumbSessionKey = null;
    }
    if (retryingUnconfirmed && _unconfirmedThumbVotes.size === 0 &&
            !_studentVoteKnownFailure) {
        _studentVoteBatchFailure = null;
    }
}

function reconcileUnconfirmedThumbVotes(selectedRecipientIds) {
    if (_studentVoteInFlight > 0 || _unconfirmedThumbVotes.size === 0) return;
    const pending = Array.from(_unconfirmedThumbVotes.entries());
    const confirmed = pending.every(([recipientId, selected]) =>
        selectedRecipientIds.has(recipientId) === selected
    );
    if (!confirmed) {
        responseNeedsRefresh = true;
        if (!_studentVoteKnownFailure) {
            setStudentVoteStatus(
                'Could not confirm your vote. Click the thumb again to retry.',
                'submitting'
            );
        }
        return false;
    }
    _unconfirmedThumbVotes.clear();
    _unconfirmedThumbSessionKey = null;
    if (_studentVoteKnownFailure) return true;
    const successMessage = pending.length === 1 && pending[0][1] === false
        ? 'Vote removed' : 'Vote submitted';
    setStudentVoteStatus(successMessage, 'submitted');
    return true;
}

function rememberStudentVoteWarnings(outcomeUnknown, hasUnsavedDraft) {
    try {
        if (outcomeUnknown) {
            sessionStorage.setItem(_unconfirmedVoteWarningKey, '1');
        }
        if (hasUnsavedDraft) {
            sessionStorage.setItem(_unsavedVoteDraftWarningKey, '1');
        }
    } catch (error) {}
}

function normalizedSessionKey(source) {
    const value = Number(source?.session_key);
    return Number.isInteger(value) && value >= 0 ? value : 0;
}

function discardOldSessionThumbUncertainty(state) {
    if (_unconfirmedThumbVotes.size === 0) {
        _unconfirmedThumbSessionKey = null;
        return false;
    }
    const currentSessionKey = normalizedSessionKey(state);
    if (_unconfirmedThumbSessionKey == null ||
            _unconfirmedThumbSessionKey === currentSessionKey) {
        return false;
    }
    _unconfirmedThumbVotes.clear();
    _unconfirmedThumbSessionKey = null;
    rememberStudentVoteWarnings(true, false);
    setStudentVoteStatus(
        'Could not confirm a vote from the previous session. Ask the instructor to verify it.',
        'error'
    );
    return true;
}

function queueDashboardReload(state) {
    const phase = state?.phase || null;
    const sessionKey = normalizedSessionKey(state);
    if (_pendingDashboardReloadPhase !== phase ||
            _pendingDashboardReloadSessionKey !== sessionKey) {
        _pendingDashboardReloadStartedAt = Date.now();
    }
    _pendingDashboardReloadPhase = phase;
    _pendingDashboardReloadSessionKey = sessionKey;
}

function completePendingDashboardReload() {
    if (!_pendingDashboardReloadPhase || _studentVoteInFlight > 0 ||
            ratingSubmissionInProgress) {
        return false;
    }
    const pendingThumbsMatchTargetSession =
        _unconfirmedThumbVotes.size > 0 &&
        (_unconfirmedThumbSessionKey == null ||
         _unconfirmedThumbSessionKey === _pendingDashboardReloadSessionKey);
    if (_pendingDashboardReloadPhase === 'discussion' &&
            pendingThumbsMatchTargetSession &&
            Date.now() - _pendingDashboardReloadStartedAt <
                DASHBOARD_RELOAD_READBACK_GRACE_MS) {
        responseNeedsRefresh = true;
        window.requestDashboardPoll?.();
        return false;
    }
    const outcomeUnknown = _unconfirmedThumbVotes.size > 0 ||
        _unconfirmedChallengeRatings.size > 0 ||
        Boolean(unconfirmedPresentationRating);
    const hasUnsavedDraft = ratingDraftDirty ||
        challengeRatingDrafts.size > 0;
    rememberStudentVoteWarnings(outcomeUnknown, hasUnsavedDraft);
    _allowAutomaticVoteNavigation = true;
    window.location.reload();
    return true;
}

window.addEventListener('beforeunload', event => {
    if (_allowAutomaticVoteNavigation) return;
    const outcomeUnknown = _studentVoteInFlight > 0 ||
        ratingSubmissionInProgress ||
        Boolean(unconfirmedPresentationRating) ||
        _unconfirmedThumbVotes.size > 0 ||
        _unconfirmedChallengeRatings.size > 0;
    const hasUnsavedDraft = ratingDraftDirty ||
        challengeRatingDrafts.size > 0;
    if (!outcomeUnknown && !hasUnsavedDraft) return;
    rememberStudentVoteWarnings(outcomeUnknown, hasUnsavedDraft);
    // A 401 means this page can no longer recover. Preserve the warning for
    // the landing page without leaving the expired page permanently stopped.
    if (forcedPageNavigationInProgress) return;
    event.preventDefault();
    event.returnValue = '';
});

try {
    if (document.getElementById('student-vote-status')) {
        const hasUnconfirmedVote =
            sessionStorage.getItem(_unconfirmedVoteWarningKey) === '1';
        const hasUnsavedDraft =
            sessionStorage.getItem(_unsavedVoteDraftWarningKey) === '1';
        if (hasUnconfirmedVote) {
            sessionStorage.removeItem(_unconfirmedVoteWarningKey);
        }
        if (hasUnsavedDraft) {
            sessionStorage.removeItem(_unsavedVoteDraftWarningKey);
        }
        if (hasUnconfirmedVote || hasUnsavedDraft) {
            let message;
            if (hasUnconfirmedVote && hasUnsavedDraft) {
                message = (
                    'Could not confirm one earlier vote, and another selected ' +
                    'rating was not submitted. Ask the instructor to verify ' +
                    'the uncertain vote.'
                );
            } else if (hasUnconfirmedVote) {
                message = (
                    'Could not confirm your earlier vote. ' +
                    'Ask the instructor to verify it.'
                );
            } else {
                message = (
                    'Your selected rating was not submitted before ' +
                    'the activity changed.'
                );
            }
            setStudentVoteStatus(message, 'error');
        }
    }
} catch (error) {}
// --- Challenge feature state ---
let challengeHandRaised = false;
let challengeRatings = {}; // { challenge_key: selected_score }
let savedChallengeRatings = {}; // confirmed server values for this presentation
let activeChallengePresentationKey = null;
let activeChallengeRatingsOpen = false;
const challengeRatingDrafts = new Set();
const challengeRatingSubmissions = new Map(); // challenge_key -> presentation_key
const _activeChallengeIdentities = new Map();
let lastReceivedState = null; // latest state snapshot for the raise-hand toggle
let _challengeToggleInFlight = false;
let challengeHandConfirmationPending = null;
// { presentationKey, desiredRaised } while server outcome is uncertain
let _lastChallengeCardsSig = null;

const dashboard = document.querySelector('.dashboard');
if (dashboard) {
    let _pollInProgress = false;
    let _pollTimer = null;
    let _rosterVersion = null;
    let _pollFailures = 0;
    let _pollStopped = false;
    let _responseContext = null;
    let _connectionStatusTimer = null;
    let _connectionInterrupted = false;
    let _pollAgainImmediately = false;
    let _studentDiscQuestionsVersion = null;
    // Pending secondary syncs are tracked independently of the main state
    // version so a failed roster/questions/responses request is retried even
    // while the cheap poll keeps returning changed:false. Each resource has
    // its own bounded backoff so a persistent outage cannot turn 1s polling
    // into a request storm.
    let _lastState = null;
    let _rosterSyncPending = false;
    let _rosterSyncVersion = 0;
    let _rosterRetryAt = 0;
    let _rosterRetryFailures = 0;
    let _rosterSyncInFlight = null;
    let _discQuestionsSyncPending = false;
    let _discQuestionsSyncContext = null;
    let _discQuestionsSyncVersion = 0;
    let _discQuestionsRetryAt = 0;
    let _discQuestionsRetryFailures = 0;
    let _discQuestionsSyncInFlight = null;
    let _responsesRetryAt = 0;
    let _responsesRetryFailures = 0;
    let _responsesSyncInFlight = null;

    function initDashboard() {
        initStarRows();
        updateRatingControlAvailability();
        // Thumb buttons are only rendered during the discussion phase; the
        // server gates them on the phase, not on posted questions.
        updateThumbAvailability(dashboard.dataset.phase === 'discussion');
        pollOnce();
    }

    function jitteredDelay(interval, failures) {
        const base = Math.max(1000, Number(interval) || 3000);
        const isLive = dashboard.dataset.phase === 'discussion' ||
            dashboard.dataset.phase === 'competition';
        const backedOff = Math.min(isLive ? 10000 : 30000, base * Math.pow(2, failures));
        return Math.round(backedOff * (1 + Math.random() * 0.2));
    }

    function showConnectionStatus(message, recovered = false) {
        const status = document.getElementById('connection-status');
        if (!status) return;
        if (_connectionStatusTimer) clearTimeout(_connectionStatusTimer);
        _connectionStatusTimer = null;
        status.textContent = message;
        status.classList.toggle('recovered', recovered);
        status.hidden = false;
        _connectionInterrupted = !recovered;
        if (recovered) {
            _connectionStatusTimer = setTimeout(() => {
                status.hidden = true;
                _connectionStatusTimer = null;
            }, 3000);
        }
    }

    function requestImmediatePoll() {
        if (_pollStopped) return;
        if (_pollInProgress) {
            _pollAgainImmediately = true;
            return;
        }
        if (_pollTimer !== null) clearTimeout(_pollTimer);
        _pollTimer = null;
        pollOnce();
    }

    // Bounded backoff for secondary sync retries: 1s, 2s, 4s … capped at 30s.
    function secondaryRetryDelay(failures) {
        return Math.min(30000, 1000 * Math.pow(2, Math.max(0, failures - 1)));
    }

    function rosterVersionFromState(state) {
        return Number.isInteger(state?.roster_version) ? state.roster_version : 0;
    }

    function discussionSyncContext(state) {
        if (!state || state.phase !== 'discussion') return null;
        return [
            Number(state.discussion_week) || 1,
            Number(state.discussion_questions_version) || 0
        ].join(':');
    }

    function responseSyncContext(state) {
        if (!state) return null;
        if (state.phase === 'discussion') {
            return 'discussion:' + normalizedSessionKey(state) + ':' +
                (Number(state.discussion_week) || 1);
        }
        if (state.phase === 'competition' && state.poll_question_key) {
            const challengeKeys = (state.active_challenges || [])
                .map(challenge => String(challenge.challenge_key))
                .sort()
                .join(',');
            return 'competition:' + state.poll_question_key +
                '|challenges=' + challengeKeys;
        }
        return null;
    }

    function responsePrimaryContext(context) {
        if (!context) return null;
        return context.split('|challenges=', 1)[0];
    }

    function queueRosterSync(state) {
        const version = rosterVersionFromState(state);
        if (_rosterSyncVersion !== version) {
            _rosterSyncVersion = version;
            _rosterRetryFailures = 0;
            _rosterRetryAt = 0;
        }
        _rosterSyncPending = _rosterVersion !== version;
    }

    function queueDiscussionQuestionsSync(state) {
        const context = discussionSyncContext(state);
        if (!context) {
            _discQuestionsSyncContext = null;
            _discQuestionsSyncPending = false;
            _discQuestionsRetryFailures = 0;
            _studentDiscQuestionsVersion = null;
            return;
        }
        const version = Number(state.discussion_questions_version) || 0;
        if (_discQuestionsSyncContext !== context) {
            _discQuestionsSyncContext = context;
            _discQuestionsSyncVersion = version;
            _discQuestionsRetryFailures = 0;
            _discQuestionsRetryAt = 0;
        }
        _discQuestionsSyncPending = _studentDiscQuestionsVersion !== version;
    }

    function queueResponsesSync(state) {
        discardOldSessionThumbUncertainty(state);
        const context = responseSyncContext(state);
        const contextChanged = context !== _responseContext;
        if (contextChanged) {
        const primaryContextChanged = responsePrimaryContext(context) !==
            responsePrimaryContext(_responseContext);
            _responseContext = context;
            ++_responseRequestId;
            _responsesRetryFailures = 0;
            _responsesRetryAt = 0;
            if (primaryContextChanged) resetResponseControls();
            responseNeedsRefresh = Boolean(context);
        }
        if (!context) {
            responseNeedsRefresh = false;
            _responsesRetryFailures = 0;
        }
    }

    function runRosterSync() {
        if (!_rosterSyncPending || Date.now() < _rosterRetryAt) {
            return Promise.resolve(false);
        }
        if (_rosterSyncInFlight) return _rosterSyncInFlight;
        const requestedVersion = _rosterSyncVersion;
        const request = (async () => {
            try {
                const applied = await syncRoster(requestedVersion);
                if (applied && requestedVersion === _rosterSyncVersion) {
                    _rosterSyncPending = false;
                    _rosterRetryFailures = 0;
                }
                return applied;
            } catch (e) {
                if (requestedVersion === _rosterSyncVersion) {
                    _rosterRetryFailures = Math.min(_rosterRetryFailures + 1, 5);
                    _rosterRetryAt = Date.now() +
                        secondaryRetryDelay(_rosterRetryFailures);
                }
                return false;
            } finally {
                if (_rosterSyncInFlight === request) {
                    _rosterSyncInFlight = null;
                }
                if (_rosterSyncPending && requestedVersion !== _rosterSyncVersion) {
                    requestImmediatePoll();
                }
            }
        })();
        _rosterSyncInFlight = request;
        return request;
    }

    function runDiscussionQuestionsSync() {
        if (!_discQuestionsSyncPending || !_lastState ||
                Date.now() < _discQuestionsRetryAt) {
            return Promise.resolve(false);
        }
        if (_discQuestionsSyncInFlight) return _discQuestionsSyncInFlight;
        const requestedContext = _discQuestionsSyncContext;
        const requestedVersion = _discQuestionsSyncVersion;
        const state = _lastState;
        const request = (async () => {
            try {
                const applied = await syncDiscussionQuestions(
                    state, requestedContext, requestedVersion
                );
                if (applied && requestedContext === _discQuestionsSyncContext) {
                    _discQuestionsSyncPending = false;
                    _discQuestionsRetryFailures = 0;
                }
                return applied;
            } catch (e) {
                if (requestedContext === _discQuestionsSyncContext) {
                    _discQuestionsRetryFailures = Math.min(
                        _discQuestionsRetryFailures + 1, 5
                    );
                    _discQuestionsRetryAt = Date.now() +
                        secondaryRetryDelay(_discQuestionsRetryFailures);
                }
                return false;
            } finally {
                if (_discQuestionsSyncInFlight === request) {
                    _discQuestionsSyncInFlight = null;
                }
                if (_discQuestionsSyncPending &&
                        requestedContext !== _discQuestionsSyncContext) {
                    requestImmediatePoll();
                }
            }
        })();
        _discQuestionsSyncInFlight = request;
        return request;
    }

    function runResponsesSync() {
        discardStalePresentationUncertainty(_lastState);
        if (!responseNeedsRefresh || !_lastState ||
                _studentVoteInFlight > 0 || Date.now() < _responsesRetryAt) {
            return Promise.resolve(false);
        }
        if (_responsesSyncInFlight) return _responsesSyncInFlight;
        const requestedContext = _responseContext;
        const state = _lastState;
        const requestId = ++_responseRequestId;
        const request = (async () => {
            try {
                const applied = await syncMyResponses(
                    state, requestedContext, requestId
                );
                if (applied && requestedContext === _responseContext) {
                    responseNeedsRefresh = _unconfirmedThumbVotes.size > 0 ||
                        _unconfirmedChallengeRatings.size > 0 ||
                        Boolean(unconfirmedPresentationRating) ||
                        Boolean(challengeHandConfirmationPending);
                    _responsesRetryFailures = responseNeedsRefresh
                        ? Math.min(_responsesRetryFailures + 1, 5) : 0;
                    _responsesRetryAt = responseNeedsRefresh
                        ? Date.now() +
                            secondaryRetryDelay(_responsesRetryFailures)
                        : 0;
                }
                return applied;
            } catch (e) {
                if (requestedContext === _responseContext &&
                        responseNeedsRefresh) {
                    _responsesRetryFailures = Math.min(
                        _responsesRetryFailures + 1, 5
                    );
                    _responsesRetryAt = Date.now() +
                        secondaryRetryDelay(_responsesRetryFailures);
                }
                return false;
            } finally {
                if (_responsesSyncInFlight === request) {
                    _responsesSyncInFlight = null;
                }
                const reloadStarted = completePendingDashboardReload();
                if (!reloadStarted && responseNeedsRefresh &&
                        (requestedContext !== _responseContext ||
                         requestId !== _responseRequestId)) {
                    requestImmediatePoll();
                }
            }
        })();
        _responsesSyncInFlight = request;
        return request;
    }

    // Start only pending secondary resources. The returned promise is useful
    // to tests and explicit callers, while the main poll intentionally does
    // not await it: slow secondary requests must not block live state polling.
    function retryPendingSyncs() {
        return Promise.all([
            runRosterSync(),
            runDiscussionQuestionsSync(),
            runResponsesSync()
        ]);
    }

    function applyCompactPollClosedSignal(data) {
        if (data?.poll_closed !== true) return;
        activePollOpen = false;
        ratingPollOpenForTimer = false;
        stopPollPulse();
        updateRatingControlAvailability();
        activeChallengeRatingsOpen = false;
        if (lastReceivedState) {
            lastReceivedState.challenge_ratings_open = false;
            renderChallengeUI(lastReceivedState);
        }
        if (_lastState && _lastState !== lastReceivedState) {
            _lastState.challenge_ratings_open = false;
        }

        const draftLostWithWindow =
            ratingDraftDirty && !!lastRatedPresentationKey;
        const gradingSection = document.getElementById('team-grading-section');
        const pollSection = document.getElementById('poll-section');
        if (gradingSection) {
            gradingSection.style.display = draftLostWithWindow ? 'block' : 'none';
        }
        if (pollSection) {
            pollSection.style.display = draftLostWithWindow ? 'block' : 'none';
        }
        if (draftLostWithWindow) {
            showRatingWindowClosed();
        } else {
            hideRatingWindowClosed();
        }
    }

    async function pollOnce() {
        if (_pollInProgress || _pollStopped) return;
        _pollInProgress = true;
        let interval = 3000;
        try {
            const pollParams = new URLSearchParams();
            if (lastKnownStateVersion != null) {
                pollParams.set('since', lastKnownStateVersion);
            }
            if (lastKnownQuestionId != null) {
                pollParams.set('known_question_id', lastKnownQuestionId);
            }
            if (lastKnownQuestionRevision) {
                pollParams.set('known_question_revision', lastKnownQuestionRevision);
            }
            const questionQuery = pollParams.toString();
            const { res, data } = await fetchJSONWithTimeout(
                '/api/poll' + (questionQuery ? `?${questionQuery}` : '')
            );
            if (pageNavigationInProgress) {
                _pollStopped = true;
                return;
            }
            if (res.status === 401) {
                _pollStopped = true;
                redirectToSessionEnded();
                return;
            }
            if (!res.ok) throw new Error('poll failed');
            if (_connectionInterrupted) showConnectionStatus('Live updates restored.', true);
            _pollFailures = 0;
            // Remember the version we just saw so the next poll can take the
            // server's cheap "nothing changed" short-circuit.
            if (data.state_version != null) lastKnownStateVersion = data.state_version;
            applyCompactPollClosedSignal(data);
            if (data.changed === false) {
                // Nothing in course_state changed since the version we sent:
                // skip rendering and keep polling fast. The student's local
                // timers (presentation countdown, poll pulse) keep ticking.
                // Secondary resources whose last sync failed still retry here
                // so they are not stuck until the next state change.
                retryPendingSyncs();
            } else {
                // State is frequent; secondary resources are queued by their
                // own version/context and run without blocking the main poll.
                const pageReloading = await loadState(data.state);
                if (!pageReloading) {
                    queueRosterSync(data.state);
                    retryPendingSyncs();
                }
                if (data.top_teams) {
                    renderTopTeams(data.top_teams);
                }
                if (data.top_challengers) {
                    renderTopChallengers(data.top_challengers);
                }
            }
            // The server keeps Ended pages on a low-frequency recovery poll.
            if (data.poll_interval) interval = data.poll_interval;
            if (data.stop_polling) _pollStopped = true;
        } catch (e) {
            _pollFailures = Math.min(_pollFailures + 1, 5);
            showConnectionStatus('Live updates interrupted. Information may be out of date.');
        } finally {
            _pollInProgress = false;
            if (!_pollStopped && !pageNavigationInProgress) {
                const delay = _pollAgainImmediately
                    ? 0
                    : jitteredDelay(interval, _pollFailures);
                _pollAgainImmediately = false;
                _pollTimer = setTimeout(pollOnce, delay);
            }
        }
    }

    window.addEventListener('offline', () => {
        showConnectionStatus('Live updates interrupted. Information may be out of date.');
    });
    window.addEventListener('online', requestImmediatePoll);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) requestImmediatePoll();
    });

    let teamSelectionInProgress = false;
    let teamPickerLoading = false;

    window.joinTeam = async function(teamId) {
        if (teamSelectionInProgress) return;
        teamSelectionInProgress = true;
        const controls = [
            ...document.querySelectorAll('.team-card'),
            document.getElementById('btn-change-team')
        ].filter(Boolean);
        const previousDisabled = controls.map(control => control.disabled);
        controls.forEach(control => { control.disabled = true; });
        let navigating = false;
        try {
            const data = await postJSON('/api/join_team', { team_id: teamId });
            if (data.success) {
                navigating = true;
                showToast(teamId ? 'Team joined!' : 'Left team');
                setTimeout(() => { window.location.href = '/dashboard'; }, 350);
            } else {
                showToast(data.error || 'Failed to join team', 'error');
            }
        } finally {
            if (!navigating) {
                controls.forEach((control, index) => {
                    control.disabled = previousDisabled[index];
                });
                teamSelectionInProgress = false;
            }
        }
    };

    window.showTeamPicker = async function() {
        if (teamPickerLoading || teamSelectionInProgress) return;
        teamPickerLoading = true;
        const changeButton = document.getElementById('btn-change-team');
        if (changeButton) changeButton.style.display = 'none';
        const panel = document.getElementById('team-picker-panel');
        panel.style.display = 'block';
        const grid = document.getElementById('team-picker-grid');
        grid.innerHTML = '<p class="loading">Loading teams...</p>';
        try {
            const { res, data: teams } = await fetchJSONWithTimeout('/api/teams');
            if (res.status === 401) {
                redirectToSessionEnded();
                return;
            }
            if (!res.ok) throw new Error('team load failed');
            if (!Array.isArray(teams)) throw new Error('invalid team response');
            const maxMembers = Number(dashboard.dataset.maxMembers) || null;
            grid.innerHTML = `
                <button class="team-card unassigned-card" onclick="joinTeam(0)">
                    <span class="team-name" style="color:var(--text-muted)">Remove me from my team</span>
                </button>
            ` + teams.map(t => {
                const memberCount = Number(t.member_count ?? t.members?.length ?? 0);
                const isFull = maxMembers != null && memberCount >= maxMembers && t.id !== MY_TEAM_ID;
                return `
                <button class="team-card" data-team-id="${t.id}"
                        style="--team-color: ${escapeAttrValue(t.color)}"
                        onclick="joinTeam(${t.id})" ${isFull ? 'disabled title="Team is full"' : ''}>
                    <span class="team-dot" style="background: ${escapeAttrValue(t.color)}"></span>
                    <span class="team-name">${escapeHtmlValue(t.name)}</span>
                    ${maxMembers != null ? `<span class="team-capacity">${memberCount}/${maxMembers}${isFull ? ' · Full' : ''}</span>` : ''}
                </button>
            `;
            }).join('');
        } catch (error) {
            panel.style.display = 'none';
            if (changeButton) changeButton.style.display = '';
            showToast('Could not load teams. Please try again.', 'error');
        } finally {
            teamPickerLoading = false;
        }
    };

    window.hideTeamPicker = function() {
        document.getElementById('btn-change-team').style.display = '';
        document.getElementById('team-picker-panel').style.display = 'none';
    };

    function updateTeamCapacityCards(teams) {
        const maxMembers = Number(dashboard.dataset.maxMembers) || null;
        if (maxMembers == null) return;
        const teamById = new Map(teams.map(team => [Number(team.id), team]));
        const myTeamId = Number(dashboard.dataset.teamId) || null;
        document.querySelectorAll('.team-card[data-team-id]').forEach(card => {
            const teamId = Number(card.dataset.teamId);
            const team = teamById.get(teamId);
            if (!team) return;
            const memberCount = Number(
                team.member_count ?? team.members?.length ?? 0
            );
            const isFull = memberCount >= maxMembers && teamId !== myTeamId;
            const capacity = card.querySelector('.team-capacity');
            if (capacity) {
                capacity.textContent = `${memberCount}/${maxMembers}${isFull ? ' · Full' : ''}`;
            }
            card.disabled = isFull;
            if (isFull) {
                card.title = 'Team is full';
            } else if (card.title === 'Team is full') {
                card.removeAttribute('title');
            }
        });
    }

    async function loadRoster(teams) {
        renderRosterGrid(document.getElementById('roster-table'), teams, dashboard.dataset.you);
        updateTeamCapacityCards(teams);
    }

    async function syncRoster(version) {
        const normalizedVersion = Number.isInteger(version) ? version : 0;
        if (_rosterVersion === normalizedVersion) return true;
        const { res, data: teams } = await fetchJSONWithTimeout(
            `/api/teams?version=${encodeURIComponent(normalizedVersion)}`
        );
        if (!res.ok) throw new Error('roster refresh failed');
        if (normalizedVersion !== _rosterSyncVersion ||
                rosterVersionFromState(_lastState) !== normalizedVersion) {
            return false;
        }
        await loadRoster(teams);
        _rosterVersion = normalizedVersion;
        return true;
    }

    function resetResponseControls() {
        if (unconfirmedPresentationRating) {
            unconfirmedPresentationRating = null;
            setStudentVoteStatus(
                'Could not confirm an earlier rating. Ask the instructor to verify it.',
                'error'
            );
        }
        document.querySelectorAll('.thumb-btn').forEach(btn => {
            btn.classList.remove('active-green');
            btn.setAttribute('aria-pressed', 'false');
        });
        pollSelections = { q1: 0, q2: 0 };
        savedPollSelections = null;
        ratingDraftDirty = false;
        setStarRowValue('q1', 0);
        setStarRowValue('q2', 0);
        const status = document.getElementById('rating-status');
        if (status) status.style.display = 'none';
    }

    function renderStudentDiscussionQuestions(questions) {
        const container = document.getElementById('student-disc-questions-list');
        if (!container) return;
        if (!questions || questions.length === 0) {
            container.innerHTML = '<p class="hint">Waiting for the instructor to post questions...</p>';
            return;
        }
        container.innerHTML = questions.map((q, i) => {
            const key = discussionQuestionKey(q);
            const expanded = key && _discQuestionExpanded.has(key) ? ' expanded' : '';
            const keyAttr = key ? ` data-question-key="${escapeAttrValue(key)}"` : '';
            return `
        <div class="disc-question-card${expanded}"${keyAttr}>
            <div class="disc-question-header" role="button" tabindex="0" aria-expanded="${expanded ? 'true' : 'false'}" onclick="toggleDiscQuestionCard(this)" onkeydown="discQuestionHeaderKeydown(event)">
                <span class="disc-question-num">#${discussionQuestionNumber(q, i)}</span>
                <span class="disc-question-title">${escapeHtmlValue(q.title || 'Untitled')}</span>
            </div>
            <div class="disc-question-body">
                <div class="markdown-content">${renderMarkdown(q.content || '')}</div>
            </div>
        </div>
    `}).join('');
        safeTypeset(container);
        safeHighlight(container);
    }

    async function syncDiscussionQuestions(state, requestedContext, requestedVersion) {
        if (!document.getElementById('student-disc-questions-list')) return true;
        if (state.phase !== 'discussion') return true;
        const version = Number(requestedVersion) || 0;
        if (_studentDiscQuestionsVersion === version) return true;
        const { res, data } = await fetchJSONWithTimeout('/api/discussion_questions');
        if (res.status === 401) {
            _pollStopped = true;
            redirectToSessionEnded();
            return false;
        }
        if (!res.ok) throw new Error('discussion questions load failed');
        if (requestedContext !== _discQuestionsSyncContext ||
                discussionSyncContext(_lastState) !== requestedContext ||
                (Number(data.version) || 0) !== version) {
            return false;
        }
        _studentDiscQuestionsVersion = version;
        renderStudentDiscussionQuestions(data.questions || []);
        return true;
    }

    function updateThumbAvailability(thumbsAvailable) {
        // Thumbs are keyed server-side to the whole discussion phase, not to
        // any single posted question, so availability follows the phase.
        document.querySelectorAll('.thumb-btn').forEach(btn => {
            btn.dataset.thumbsAvailable = thumbsAvailable ? '1' : '0';
            btn.disabled = !thumbsAvailable ||
                btn.dataset.submitting === '1';
            if (thumbsAvailable) {
                if (btn.title === 'Thumbs are only available during the discussion phase') {
                    btn.removeAttribute('title');
                }
            } else {
                btn.title = 'Thumbs are only available during the discussion phase';
            }
        });
    }

    async function syncMyResponses(state, context, requestId) {
        // Thumbs are recorded for the whole discussion phase (keyed by a
        // constant server-side), independent of any single posted question.
        const inDiscussion = state.phase === 'discussion';
        const presentationKey = state.phase === 'competition'
            ? state.poll_question_key : null;
        const { res, data } = await fetchJSONWithTimeout('/api/my_responses');
        if (res.status === 401) {
            _pollStopped = true;
            redirectToSessionEnded();
            return false;
        }
        if (!res.ok) throw new Error('saved responses load failed');
        if (requestId !== _responseRequestId ||
                context !== _responseContext ||
                responseSyncContext(_lastState) !== context) {
            return false;
        }
        if (inDiscussion) {
            if (data.phase !== 'discussion' ||
                    normalizedSessionKey(data) !== normalizedSessionKey(state)) {
                return false;
            }
            const selected = new Set((data.thumb_recipient_ids || []).map(String));
            const ambiguityResolved =
                reconcileUnconfirmedThumbVotes(selected);
            document.querySelectorAll(
                '.teammate-card[data-student-id] .thumb-btn'
            ).forEach(btn => {
                const card = btn.closest('.teammate-card');
                const recipientId = String(card?.dataset.studentId || '');
                const confirmedSelected = selected.has(recipientId);
                const checking = _unconfirmedThumbVotes.has(recipientId);
                const displayedSelected = checking
                    ? _unconfirmedThumbVotes.get(recipientId)
                    : confirmedSelected;
                btn.dataset.confirmedSelected =
                    confirmedSelected ? '1' : '0';
                btn.classList.toggle('active-green', displayedSelected);
                btn.setAttribute(
                    'aria-pressed',
                    displayedSelected ? 'true' : 'false'
                );
                if (checking) {
                    btn.dataset.checking = '1';
                    btn.title =
                        'Vote not confirmed. Click again to retry.';
                } else {
                    delete btn.dataset.checking;
                    if (btn.title ===
                            'Vote not confirmed. Click again to retry.') {
                        btn.removeAttribute('title');
                    }
                }
                btn.disabled = btn.dataset.thumbsAvailable !== '1' ||
                    btn.dataset.submitting === '1';
            });
            if (ambiguityResolved) completePendingDashboardReload();
        } else if (presentationKey) {
            if (data.phase !== 'competition' ||
                    data.presentation_key !== presentationKey) {
                return false;
            }

            if (data.rating) {
                const serverSelections = {
                    q1: Number(data.rating.q1_developed) || 0,
                    q2: Number(data.rating.q2_easy) || 0
                };
                savedPollSelections = { ...serverSelections };
                const pending = unconfirmedPresentationRating;
                if (pending &&
                        pending.presentationKey === presentationKey &&
                        pending.q1 === serverSelections.q1 &&
                        pending.q2 === serverSelections.q2) {
                    unconfirmedPresentationRating = null;
                    if (_unconfirmedChallengeRatings.size === 0 &&
                            _unconfirmedThumbVotes.size === 0 &&
                            _studentVoteInFlight === 0 &&
                            !_studentVoteKnownFailure) {
                        _studentVoteBatchFailure = null;
                        setStudentVoteStatus('Vote submitted', 'submitted');
                    }
                }
                if (!ratingDraftDirty) {
                    pollSelections = { ...serverSelections };
                    setStarRowValue('q1', pollSelections.q1);
                    setStarRowValue('q2', pollSelections.q2);
                    showCurrentRating(pollSelections);
                } else if (pollSelections.q1 === serverSelections.q1 &&
                        pollSelections.q2 === serverSelections.q2) {
                    ratingDraftDirty = false;
                    showCurrentRating(serverSelections);
                } else {
                    showRatingDraftStatus();
                }
            } else {
                savedPollSelections = null;
                if (!ratingDraftDirty) {
                    pollSelections = { q1: 0, q2: 0 };
                    setStarRowValue('q1', 0);
                    setStarRowValue('q2', 0);
                    const status = document.getElementById('rating-status');
                    if (status) status.style.display = 'none';
                } else {
                    showRatingDraftStatus();
                }
            }
            applySavedChallengeResponses(data, presentationKey);
        }
        return true;
    }

    async function loadState(state) {
        if (!state) {
            const result = await fetchJSONWithTimeout('/api/state');
            if (!result.res.ok) throw new Error('state load failed');
            state = result.data;
        }
        // Remember the latest snapshot so pending secondary syncs can retry
        // from cheap changed:false poll cycles.
        _lastState = state;
        lastReceivedState = state;
        discardOldSessionThumbUncertainty(state);

        // The dashboard's activity controls are rendered server-side for the
        // current phase and team. Reload once when either changes so students
        // never keep stale discussion or presentation controls.
        const currentTeamId = state.my_team ? state.my_team.id : null;
        const initialLocked = dashboard.dataset.teamsLocked === '1';
        const initialMaxTeams = dashboard.dataset.maxTeams ? Number(dashboard.dataset.maxTeams) : null;
        const initialMaxMembers = dashboard.dataset.maxMembers ? Number(dashboard.dataset.maxMembers) : null;
        const initialSessionKey = Number(dashboard.dataset.sessionKey || 0);
        const configChanged = Boolean(state.teams_locked) !== initialLocked ||
            (initialMaxTeams != null && state.max_teams != null && Number(state.max_teams) !== initialMaxTeams) ||
            (initialMaxMembers != null && state.max_members != null && Number(state.max_members) !== initialMaxMembers);
        const sessionChanged = normalizedSessionKey(state) !== initialSessionKey;
        if (state.phase !== dashboard.dataset.phase || currentTeamId !== MY_TEAM_ID ||
                configChanged || sessionChanged) {
            queueDashboardReload(state);
            completePendingDashboardReload();
            return true;
        }

        const badge = document.querySelector('.phase-badge');
        if (badge) {
            badge.textContent = 'Phase: ' + (PHASE_LABELS[state.phase] || state.phase.toUpperCase());
            badge.className = 'phase-badge phase-' + state.phase;
        }

        queueDiscussionQuestionsSync(state);
        queueResponsesSync(state);

        const discSec = document.getElementById('discussion-section');
        if (discSec) {
            discSec.style.display = PHASES_WITH_DISCUSSION.includes(state.phase) ? 'block' : 'none';
        }

        // Show session results only when the session has ended
        const resultsSec = document.getElementById('session-results');
        if (resultsSec) {
            resultsSec.style.display = state.phase === 'ended' ? 'block' : 'none';
        }

        const teamSec = document.getElementById('team-section');
        const teamGradeSec = document.getElementById('team-grading-section');
        if (teamSec) {
            const show = PHASES_WITH_TEAM.includes(state.phase);
            teamSec.style.display = show ? 'block' : 'none';
            const hasActivePresentation = !!(show && state.active_team && state.active_question);

            // Compute amPresenting early so we can also hide the grading card
            const amPresenting = !!(hasActivePresentation && state.active_team.id === MY_TEAM_ID);
            const canRate = !!(hasActivePresentation && MY_TEAM_ID && !amPresenting &&
                state.poll_active && state.poll_question_key);
            activePollOpen = canRate;
            ratingPollOpenForTimer = !!(hasActivePresentation && state.poll_active);
            updateRatingControlAvailability();

            // Update rating prompt with the presenting team's name
            const ratingPrompt = document.getElementById('rating-prompt');
            if (ratingPrompt) {
                if (canRate && state.active_team) {
                    ratingPrompt.textContent = 'Please rate ';
                    const teamName = document.createElement('strong');
                    teamName.textContent = state.active_team.name || '';
                    teamName.className = 'team-name-marked';
                    teamName.style.setProperty(
                        '--team-color',
                        state.active_team.color || 'var(--border)'
                    );
                    ratingPrompt.appendChild(teamName);
                } else {
                    ratingPrompt.textContent = '';
                }
            }

            // Detect new presentation — reset rating UI even if the same team presents again
            const currentPresentationKey = state.poll_question_key || null;
            if (currentPresentationKey !== lastRatedPresentationKey) {
                lastRatedPresentationKey = currentPresentationKey;
                pollSelections = { q1: 0, q2: 0 };
                savedPollSelections = null;
                ratingDraftDirty = false;
                // Reset star visuals
                document.querySelectorAll('#poll-section .star').forEach(s => {
                    s.classList.remove('active');
                    s.textContent = '☆';
                    s.setAttribute('aria-pressed', 'false');
                });
                // Reset submit button
                const sBtn = document.getElementById('btn-submit-rating');
                if (sBtn) {
                    sBtn.disabled = false;
                    sBtn.textContent = 'Submit Rating';
                }
                // Clear submitted-rating status
                const rStatus = document.getElementById('rating-status');
                if (rStatus) rStatus.style.display = 'none';
            }

            // An unsubmitted draft survives the rating window closing: keep
            // the section visible with the inputs disabled (activePollOpen is
            // false, so updateRatingControlAvailability already disabled them)
            // and explain instead of letting the UI vanish mid-tap.
            const draftLostWithWindow = !canRate && ratingDraftDirty && !!lastRatedPresentationKey;
            if (teamGradeSec) {
                teamGradeSec.style.display = (canRate || draftLostWithWindow) ? 'block' : 'none';
            }
            if (draftLostWithWindow) {
                showRatingWindowClosed();
            } else {
                hideRatingWindowClosed();
            }

            if (show) {
                const banner = document.getElementById('presenting-name');
                const qDisplay = document.getElementById('question-display');
                const timer = document.getElementById('student-timer');
                const pollSection = document.getElementById('poll-section');
                const youAreUp = document.getElementById('you-are-up');

                if (!hasActivePresentation) {
                    if (banner) {
                        banner.textContent = 'Waiting for next presentation...';
                        banner.classList.remove('has-team-color');
                        banner.style.removeProperty('--team-color');
                        banner.style.color = 'var(--text-muted)';
                    }
                    if (qDisplay) qDisplay.style.display = 'none';
                    if (timer) timer.style.display = 'none';
                    if (youAreUp) youAreUp.style.display = 'none';
                    if (pollSection) pollSection.style.display = 'none';
                    stopTimer();
                    stopPollPulse();
                    lastRenderedQuestionKey = null;
                    lastKnownQuestionId = null;
                    lastKnownQuestionRevision = null;
                    renderChallengeUI(state);
                    return;
                }

                if (banner) {
                    banner.textContent = state.active_team.name;
                    banner.classList.add('has-team-color');
                    banner.style.setProperty(
                        '--team-color',
                        state.active_team.color || 'var(--border)'
                    );
                    banner.style.color = 'var(--text)';
                }
                // Show active question — only re-render when question changes
                const qText = document.getElementById('active-question-text');
                if (qDisplay && qText && state.active_question) {
                    qDisplay.style.display = 'block';
                    const qKey = `${state.active_question.id}:${state.active_question.revision || ''}`;
                    if (qKey !== lastRenderedQuestionKey) {
                        if (state.active_question.html_content) {
                            qText.innerHTML = sanitizeHtml(state.active_question.html_content);
                        } else {
                            qText.innerHTML = renderPresentationQuestion(state.active_question);
                        }
                        safeTypeset(qText);
                        safeHighlight(qText);
                        lastRenderedQuestionKey = qKey;
                    }
                    lastKnownQuestionId = state.active_question.id;
                    lastKnownQuestionRevision = state.active_question.revision || null;
                } else if (qDisplay) {
                    qDisplay.style.display = 'none';
                    lastRenderedQuestionKey = null;
                    lastKnownQuestionId = null;
                    lastKnownQuestionRevision = null;
                }
                // --- Presentation countdown timer (visible to ALL teams) ---
                const hasPres = !!state.presentation_started_at;
                const hasRemaining = state.presentation_remaining != null;
                if (timer) {
                    timer.style.display = (hasPres || hasRemaining) ? 'flex' : 'none';
                }
                if (hasPres) {
                    startCountdownTimer(Number(state.presentation_remaining) || 0);
                } else if (hasRemaining) {
                    // Paused — show the frozen remaining value
                    stopTimer();
                    const tv = document.getElementById('student-timer-value');
                    const pausedRemaining = parseInt(state.presentation_remaining, 10);
                    if (tv) tv.textContent = formatDuration(pausedRemaining);
                    setTimerExpired(pausedRemaining <= 0);
                } else {
                    stopTimer();
                }

                // --- Grading + poll highlight ---
                // "You are up!" banner — presenting team does not grade
                if (youAreUp) youAreUp.style.display = amPresenting ? 'block' : 'none';
                if (pollSection) {
                    pollSection.style.display =
                        (canRate || draftLostWithWindow) ? 'block' : 'none';
                }

                // Pulsing configured poll countdown (non-presenting teams only)
                if (canRate && Number(state.poll_remaining) > 0) {
                    startPollPulse(state.poll_remaining);
                } else {
                    stopPollPulse();
                }
            } else {
                activePollOpen = false;
                updateRatingControlAvailability();
                stopTimer();
            }
        }

        // --- Challenge feature ---
        renderChallengeUI(state);
    }

    // --- Top 3 Teams results (shown when session ends) ---
    const MEDALS = ['🥇', '🥈', '🥉'];
    function renderTopTeams(teams) {
        const container = document.getElementById('top-teams-display');
        if (!container) return;
        if (!teams || teams.length === 0) {
            container.innerHTML = '<p class="hint">No presentation ratings were submitted.</p>';
            return;
        }
        container.innerHTML = teams.map((t, i) =>
            `<div class="result-row">` +
            `<span class="result-medal">${MEDALS[(Number(t.rank) || i + 1) - 1] || '🎯'}</span>` +
            `<span class="result-team">${escapeHtmlValue(t.name)}</span>` +
            `</div>`
        ).join('');
    }

    window.submitPeerGrade = function(recipientId, selected) {
        return postJSON('/api/grade_peer', {
            recipient_id: recipientId,
            selected
        }, VOTE_RETRY_OPTIONS);
    };

    // --- teammate cards: circle layout + free drag ---
    function cardPositionStorageKey() {
        const scope = [
            dashboard.dataset.courseSlug || '',
            dashboard.dataset.you || '',
            dashboard.dataset.teamId || 'unassigned'
        ].map(value => encodeURIComponent(value)).join('-');
        return `popping-pos-${scope}`;
    }

    function isValidCardPosition(position) {
        const left = Number(position?.left);
        const top = Number(position?.top);
        return Number.isFinite(left) && Number.isFinite(top) &&
            left >= 0 && left <= 100 && top >= 0 && top <= 100;
    }

    function initTeammateCards() {
        const table = document.getElementById('team-table');
        if (!table) return;

        const storageKey = cardPositionStorageKey();
        let positions = {};
        try { positions = JSON.parse(localStorage.getItem(storageKey)) || {}; } catch(e) {}

        const cards = [...table.querySelectorAll('.teammate-card')];
        const teammateCount = cards.filter(card => card.id !== 'card-you').length;
        const cardsPerRow = table.clientWidth < 450 ? 4 : 6;
        const rowCount = Math.ceil(teammateCount / cardsPerRow);
        table.style.minHeight = teammateCount > 6
            ? `${Math.max(360, 180 + rowCount * 100)}px`
            : '280px';
        const hasCompleteSaved = cards.length > 0 &&
            cards.every(card => isValidCardPosition(positions[card.dataset.studentId]));

        if (!hasCompleteSaved) {
            window.resetCardPositions();
        } else {
            cards.forEach(card => {
                const pos = positions[card.dataset.studentId];
                card.style.left = Number(pos.left) + '%';
                card.style.top = Number(pos.top) + '%';
                card.style.transform = 'translate(-50%, -50%)';
            });
        }

        // Drag to reposition
        let dragCard = null, startX, startY, startLeft, startTop;
        const tbl = table;

        cards.forEach(card => {
            card.addEventListener('dragstart', function(e) {
                dragCard = this;
                this.classList.add('dragging');
                e.dataTransfer.effectAllowed = 'move';
                e.dataTransfer.setData('text/plain', this.dataset.studentId);
                // Prevent default ghost image
                const img = new Image(); img.src = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
                e.dataTransfer.setDragImage(img, 0, 0);
            });

            card.addEventListener('dragend', function() {
                this.classList.remove('dragging');
                dragCard = null;
                saveCardPositions(getAllPositions());
            });

            // Touch support
            card.addEventListener('touchstart', function(e) {
                if (e.target.closest('.thumb-btn')) return;
                dragCard = this;
                const t = e.touches[0];
                startX = t.clientX;
                startY = t.clientY;
                startLeft = parseFloat(this.style.left) || 0;
                startTop = parseFloat(this.style.top) || 0;
                this.classList.add('dragging');
            });
        });

        tbl.addEventListener('dragover', function(e) {
            e.preventDefault();
            if (!dragCard) return;
            const rect = tbl.getBoundingClientRect();
            const left = Math.min(92, Math.max(8,
                ((e.clientX - rect.left) / rect.width) * 100
            ));
            const top = Math.min(92, Math.max(8,
                ((e.clientY - rect.top) / rect.height) * 100
            ));
            dragCard.style.left = left + '%';
            dragCard.style.top = top + '%';
        });

        tbl.addEventListener('drop', function(e) {
            e.preventDefault();
        });

        // Touch move/end on the table
        tbl.addEventListener('touchmove', function(e) {
            if (!dragCard) return;
            e.preventDefault();
            const t = e.touches[0];
            const rect = tbl.getBoundingClientRect();
            const dx = t.clientX - startX;
            const dy = t.clientY - startY;
            const left = Math.min(92, Math.max(8,
                startLeft + (dx / rect.width) * 100
            ));
            const top = Math.min(92, Math.max(8,
                startTop + (dy / rect.height) * 100
            ));
            dragCard.style.left = left + '%';
            dragCard.style.top = top + '%';
        }, { passive: false });

        tbl.addEventListener('touchend', function() {
            if (dragCard) {
                dragCard.classList.remove('dragging');
                saveCardPositions(getAllPositions());
                dragCard = null;
            }
        });

        function getAllPositions() {
            const pos = {};
            cards.forEach(c => {
                pos[c.dataset.studentId] = {
                    left: parseFloat(c.style.left) || 50,
                    top: parseFloat(c.style.top) || 50
                };
            });
            return pos;
        }
    }

    window.toggleRoster = function() {
        const content = document.getElementById('roster-content');
        const btn = document.getElementById('btn-toggle-roster');
        if (content.style.display === 'none') {
            content.style.display = '';
            btn.textContent = 'Hide';
        } else {
            content.style.display = 'none';
            btn.textContent = 'Show';
        }
    };

    window.resetCardPositions = function() {
        try { localStorage.removeItem(cardPositionStorageKey()); } catch(e) {}
        const table = document.getElementById('team-table');
        if (!table) return;
        const cards = [...table.querySelectorAll('.teammate-card')].filter(c => c.id !== 'card-you');
        const youCard = document.getElementById('card-you');
        const positions = {};
        // On narrow tables the ellipse spread squeezes cards together
        // horizontally, so fall back to the row layout even for few cards.
        const useRowLayout = cards.length > 6 || table.clientWidth < 450;
        if (!useRowLayout) {
            const cx = 50, cy = 55, rx = 42, ry = 32;
            cards.forEach((card, i) => {
                const angle = Math.PI -
                    (i / Math.max(cards.length - 1, 1)) * Math.PI;
                const left = cx + rx * Math.cos(angle);
                const top = cy - ry * Math.sin(angle);
                card.style.left = left + '%';
                card.style.top = top + '%';
                card.style.transform = 'translate(-50%, -50%)';
                positions[card.dataset.studentId] = { left, top };
            });
        } else {
            const cardsPerRow = table.clientWidth < 450 ? 4 : 6;
            const rowCount = Math.ceil(cards.length / cardsPerRow);
            table.style.minHeight = `${Math.max(360, 180 + rowCount * 100)}px`;
            let cardIndex = 0;
            for (let row = 0; row < rowCount; row++) {
                const remaining = cards.length - cardIndex;
                const rowsLeft = rowCount - row;
                const count = Math.ceil(remaining / rowsLeft);
                const top = 12 + (row / Math.max(rowCount - 1, 1)) * 55;
                for (let column = 0; column < count; column++) {
                    const card = cards[cardIndex++];
                    const left = count === 1
                        ? 50
                        : 10 + (column / (count - 1)) * 80;
                    card.style.left = left + '%';
                    card.style.top = top + '%';
                    card.style.transform = 'translate(-50%, -50%)';
                    positions[card.dataset.studentId] = { left, top };
                }
            }
        }
        // You at bottom center
        if (youCard) {
            youCard.style.left = '50%';
            youCard.style.top = useRowLayout ? '90%' : '85%';
            youCard.style.transform = 'translate(-50%, -50%)';
            positions['you'] = { left: 50, top: useRowLayout ? 90 : 85 };
        }
        saveCardPositions(positions);
    };

    function saveCardPositions(positions) {
        try {
            localStorage.setItem(cardPositionStorageKey(), JSON.stringify(positions));
        } catch(e) {}
    }

    // --- thumb grading on cards ---
    window.gradeTeammate = async function(btn, recipientId) {
        if (btn.disabled) return;
        ++_responseRequestId;
        responseNeedsRefresh = true;
        const retryingUnconfirmed =
            btn.dataset.checking === '1' &&
            _unconfirmedThumbVotes.has(String(recipientId));
        const wasSelected = retryingUnconfirmed
            ? btn.dataset.confirmedSelected === '1'
            : btn.classList.contains('active-green');
        const selected = retryingUnconfirmed
            ? _unconfirmedThumbVotes.get(String(recipientId))
            : !wasSelected;
        btn.dataset.confirmedSelected = wasSelected ? '1' : '0';
        if (!retryingUnconfirmed) {
            _unconfirmedThumbVotes.delete(String(recipientId));
        }
        delete btn.dataset.checking;
        if (btn.title === 'Vote not confirmed. Click again to retry.') {
            btn.removeAttribute('title');
        }
        beginStudentVote();
        btn.classList.toggle('active-green', selected);
        btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
        btn.dataset.submitting = '1';
        btn.setAttribute('aria-busy', 'true');
        btn.disabled = true;
        let success = false;
        let failureMessage = 'Vote not submitted. Please try again.';
        let unconfirmed = false;
        try {
            const data = await submitPeerGrade(recipientId, selected);
            success = data?.success === true;
            if (success) {
                confirmSuccessfulThumbVote(
                    recipientId,
                    retryingUnconfirmed
                );
            } else {
                if (data?._network_error) {
                    unconfirmed = true;
                    const activeSessionKey = normalizedSessionKey(
                        lastReceivedState || {
                            session_key: dashboard.dataset.sessionKey
                        }
                    );
                    if (_unconfirmedThumbVotes.size > 0 &&
                            _unconfirmedThumbSessionKey != null &&
                            _unconfirmedThumbSessionKey !== activeSessionKey) {
                        _unconfirmedThumbVotes.clear();
                        rememberStudentVoteWarnings(true, false);
                    }
                    _unconfirmedThumbSessionKey = activeSessionKey;
                    _unconfirmedThumbVotes.set(String(recipientId), selected);
                    btn.dataset.checking = '1';
                    failureMessage = 'Could not confirm your vote. Click the thumb again to retry.';
                } else if (retryingUnconfirmed) {
                    unconfirmed = true;
                    btn.dataset.checking = '1';
                    failureMessage =
                        'Could not confirm your vote. Click the thumb again to retry.';
                } else {
                    btn.classList.toggle('active-green', wasSelected);
                    btn.setAttribute('aria-pressed', wasSelected ? 'true' : 'false');
                }
                showToast(
                    data?._network_error ? failureMessage :
                        (data?.error || failureMessage),
                    'error'
                );
            }
        } catch (error) {
            btn.classList.toggle('active-green', wasSelected);
            btn.setAttribute('aria-pressed', wasSelected ? 'true' : 'false');
            showToast(failureMessage, 'error');
        } finally {
            ++_responseRequestId;
            responseNeedsRefresh = true;
            delete btn.dataset.submitting;
            btn.removeAttribute('aria-busy');
            btn.disabled = btn.dataset.thumbsAvailable !== '1';
            finishStudentVote(
                success,
                selected ? 'Vote submitted' : 'Vote removed',
                failureMessage,
                unconfirmed
            );
            if (completePendingDashboardReload()) return;
            requestImmediatePoll();
        }
    };

    initTeammateCards();
    window.requestDashboardPoll = requestImmediatePoll;
    initDashboard();
}

/* ===== GLOBAL FUNCTIONS ===== */
let sessionTimerInterval = null;
let sessionTimerStartedAt = null;

function sessionTimerStorageKey() {
    const slug = document.querySelector('.instructor[data-slug]')?.dataset.slug ||
        document.querySelector('[data-course-slug]')?.dataset.courseSlug;
    return slug ? `popping-session-start:${slug}` : null;
}

function clearSessionTimerStorage() {
    const key = sessionTimerStorageKey();
    if (key) localStorage.removeItem(key);
    localStorage.removeItem('popping-session-start');
}
window.clearSessionTimerStorage = clearSessionTimerStorage;

window.confirmDemoReset = function(form) {
    if (!confirm('Reset demo data? This will restore everything to its initial state.')) return false;
    clearSessionTimerStorage();
    return beginPageNavigation(form);
};

let rosterMutationInProgress = false;

window.randomAssign = async function(btn) {
    if (!confirm('Assign all remaining students evenly to teams?')) return;
    if (!beginRosterMutation()) return;
    if (btn) btn.disabled = true;
    try {
        const data = await postJSON('/api/random_assign', instructorStatePayload());
        if (data.success) {
            applyRosterMutationVersion(data);
            if (Number(data.remaining) > 0) {
                showToast(`Assigned ${data.assigned || 0}; ${data.remaining} remain unassigned because team capacity is full.`, 'warning');
            } else if (data.assigned === 0) {
                showToast('All students are already in teams.');
            } else {
                showToast(`Assigned ${data.assigned} student(s) to teams.`, 'success');
            }
            refreshRosterInPlace();
        } else {
            showInstructorMutationError(data, 'Assignment failed');
        }
    } finally {
        endRosterMutation();
        if (btn) btn.disabled = false;
    }
};

/* ===== INSTRUCTOR PANEL ===== */
const instructor = document.querySelector('.instructor[data-slug]');
const isDemoInstructor = instructor?.dataset.demo === '1';
let ratingsSettling = false;
let ratingsSettlingStatusTimer = null;

function applyRatingsSettlingState(source) {
    if (!source || !Object.prototype.hasOwnProperty.call(
            source, 'ratings_settling')) return;
    const wasSettling = ratingsSettling;
    ratingsSettling = source.ratings_settling === true;
    const remaining = Math.max(
        0,
        Math.ceil(Number(source.ratings_settling_remaining) || 0)
    );
    const status = document.getElementById('ratings-settling-status');
    if (ratingsSettlingStatusTimer && ratingsSettling) {
        window.clearTimeout(ratingsSettlingStatusTimer);
        ratingsSettlingStatusTimer = null;
    }
    if (status && ratingsSettling) {
        status.hidden = false;
        status.dataset.state = 'saving';
        status.textContent = remaining > 0
            ? 'Saving final ratings... ' + remaining + 's'
            : 'Saving final ratings...';
    } else if (status && wasSettling) {
        status.hidden = false;
        status.dataset.state = 'saved';
        status.textContent =
            'Final ratings saved. You can continue with the action.';
        ratingsSettlingStatusTimer = window.setTimeout(() => {
            status.hidden = true;
            status.textContent = '';
            ratingsSettlingStatusTimer = null;
        }, 3000);
    } else if (status && !ratingsSettlingStatusTimer) {
        status.hidden = true;
    }

    const startButton = document.getElementById('btn-start-poll');
    if (startButton) {
        startButton.disabled = ratingsSettling ||
            startButton.classList.contains('gliding');
    }
    const nextButton = document.getElementById('btn-next');
    if (nextButton) {
        nextButton.disabled = ratingsSettling || pollGlideAnchor !== null;
    }
    document.querySelectorAll('.challenge-mutating-btn').forEach(button => {
        button.disabled = ratingsSettling;
    });
    const cancelButton =
        document.getElementById('btn-cancel-presentation');
    if (cancelButton) cancelButton.disabled = ratingsSettling;
    document.querySelectorAll('.phase-btn').forEach(button => {
        button.disabled = ratingsSettling;
    });
}

function ratingsSettlementBlocksAction() {
    if (!ratingsSettling) return false;
    showToast('Saving final ratings. Please wait.', 'warning');
    return true;
}


function instructorStatePayload(extra = {}) {
    if (!instructor) return extra;
    return {
        expected_phase: instructor.dataset.phase,
        expected_session_key: Number(instructor.dataset.sessionKey || 0),
        expected_roster_version: Number(instructor.dataset.rosterVersion || 0),
        presentation_key: instructor.dataset.pollQuestionKey || '',
        ...extra
    };
}

function showInstructorMutationError(data, fallback) {
    applyRatingsSettlingState(data);
    const settling = data?.ratings_settling === true;
    showToast(
        settling ? 'Saving final ratings. Please wait.' :
            (data?.error || fallback), settling ? 'warning' : 'error');
    if (data?._status === 409 &&
            /stale|changed|another page|reload|no active presentation/i.test(data.error || '')) {
        window.setTimeout(() => window.location.reload(), 500);
    }
}

function applyRosterMutationVersion(data) {
    if (!instructor) return false;
    const version = Number(data?.roster_version);
    if (!Number.isInteger(version) || version < 0) return false;
    instructor.dataset.rosterVersion = String(version);
    return true;
}

function beginRosterMutation() {
    if (rosterMutationInProgress) {
        showToast('Another roster change is still saving. Please wait.', 'warning');
        return false;
    }
    rosterMutationInProgress = true;
    return true;
}

function endRosterMutation() {
    rosterMutationInProgress = false;
}

// Setup-phase roster mutations (team assign, add/remove student,
// auto-assign, clear-all) bump the server roster_version, and the
// instructor poll loop already re-fetches and re-renders the roster grid
// on that version change — so a full-page reload is unnecessary. Kick the
// poll so the grid (and the roster_version sent with mutation guards)
// catches up immediately, and refresh the student table explicitly since
// the poll only re-renders it every 10s. Reload stays as the fallback when
// the roster grid isn't on the page (these endpoints are setup-only, so
// this should not happen — correctness beats smoothness).
function refreshRosterInPlace() {
    if (!document.querySelector('.roster-grid')) {
        window.location.reload();
        return;
    }
    if (typeof window.instructorPollOnce === 'function') {
        window.instructorPollOnce();
    }
    if (document.getElementById('student-tbody')) {
        loadStudentTable();
    }
}

/* ===== CHALLENGE FEATURE ===== */

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function getPresentationKey(state) {
    return state.poll_question_key ||
        (state.presentation_started_at ? `pres-${state.presentation_started_at}` : null);
}
function discardStalePresentationUncertainty(state) {
    const presentationKey = state?.phase === 'competition'
        ? getPresentationKey(state) : null;
    let discarded = false;
    if (unconfirmedPresentationRating &&
            unconfirmedPresentationRating.presentationKey !== presentationKey) {
        unconfirmedPresentationRating = null;
        discarded = true;
    }
    for (const [key, pending] of _unconfirmedChallengeRatings.entries()) {
        if (pending.presentationKey !== presentationKey) {
            _unconfirmedChallengeRatings.delete(key);
            discarded = true;
        }
    }
    if (challengeHandConfirmationPending &&
            challengeHandConfirmationPending.presentationKey !== presentationKey) {
        challengeHandConfirmationPending = null;
        discarded = true;
    }
    if (discarded) {
        setStudentVoteStatus(
            'Could not confirm an action from the earlier presentation. ' +
            'Ask the instructor to verify it.',
            'error'
        );
    }
    return discarded;
}

function challengeSubmissionKey(presentationKey, challengeKey) {
    return String(presentationKey || '') + ':' + String(challengeKey);
}

function challengeIsSubmitting(challengeKey) {
    return challengeRatingSubmissions.get(challengeKey) ===
        activeChallengePresentationKey;
}

function resetChallengePresentation(presentationKey) {
    let lostVoteConfirmation = false;
    for (const [key, pending] of _unconfirmedChallengeRatings.entries()) {
        if (pending.presentationKey !== presentationKey) {
            _unconfirmedChallengeRatings.delete(key);
            lostVoteConfirmation = true;
        }
    }
    const lostHandConfirmation = Boolean(
        challengeHandConfirmationPending &&
        challengeHandConfirmationPending.presentationKey !== presentationKey
    );
    if (lostHandConfirmation) challengeHandConfirmationPending = null;
    activeChallengePresentationKey = presentationKey;
    challengeHandRaised = false;
    activeChallengeRatingsOpen = false;
    challengeRatings = {};
    savedChallengeRatings = {};
    challengeRatingDrafts.clear();
    _activeChallengeIdentities.clear();
    _lastChallengeCardsSig = null;
    if (lostVoteConfirmation || lostHandConfirmation) {
        const message = lostVoteConfirmation && lostHandConfirmation
            ? 'Could not confirm an earlier vote or hand change. Ask the instructor to verify them.'
            : lostVoteConfirmation
                ? 'Could not confirm an earlier vote. Ask the instructor to verify it.'
                : 'Could not confirm an earlier hand change. Ask the instructor to verify it.';
        setStudentVoteStatus(message, 'error');
    }
}

function reconcileActiveChallengeState(challenges, presentationKey) {
    const activeKeys = new Set();
    const nextIdentities = new Map();
    const replacedKeys = new Set();
    for (const challenge of challenges || []) {
        const key = String(challenge.challenge_key);
        const identity = [
            challenge.challenger_id,
            challenge.challenger_team_id,
            challenge.challenge_num
        ].join(':');
        activeKeys.add(key);
        nextIdentities.set(key, identity);
        if (_activeChallengeIdentities.has(key) &&
                _activeChallengeIdentities.get(key) !== identity) {
            replacedKeys.add(key);
        }
    }

    const keep = key => activeKeys.has(key) && !replacedKeys.has(key);
    for (const key of Object.keys(challengeRatings)) {
        if (!keep(key)) delete challengeRatings[key];
    }
    for (const key of Object.keys(savedChallengeRatings)) {
        if (!keep(key)) delete savedChallengeRatings[key];
    }
    for (const key of Array.from(challengeRatingDrafts)) {
        if (!keep(String(key))) challengeRatingDrafts.delete(key);
    }

    let removedUnconfirmed = false;
    for (const [pendingKey, pending] of
            _unconfirmedChallengeRatings.entries()) {
        if (pending.presentationKey === presentationKey &&
                !keep(String(pending.challengeKey))) {
            _unconfirmedChallengeRatings.delete(pendingKey);
            removedUnconfirmed = true;
        }
    }

    _activeChallengeIdentities.clear();
    for (const [key, identity] of nextIdentities.entries()) {
        _activeChallengeIdentities.set(key, identity);
    }
    if (removedUnconfirmed) {
        const message = (
            'Your earlier challenge vote is no longer part of the activity ' +
            'because the challenger was cleared.'
        );
        _studentVoteBatchFailure = message;
        _studentVoteKnownFailure = true;
        setStudentVoteStatus(message, 'error');
    }
    return activeKeys;
}

function updateChallengeRatingCard(challengeKey) {
    const card = Array.from(document.querySelectorAll('.challenge-card'))
        .find(item => item.dataset.challengeKey === challengeKey);
    if (!card) return;
    const score = Number(challengeRatings[challengeKey]) || 0;
    const savedScore = Number(savedChallengeRatings[challengeKey]) || 0;
    const submitting = challengeIsSubmitting(challengeKey);
    const pendingKey = challengeSubmissionKey(
        activeChallengePresentationKey,
        challengeKey
    );
    const unconfirmed = _unconfirmedChallengeRatings.has(pendingKey);
    card.querySelectorAll('.challenge-stars .star').forEach(star => {
        const value = Number(star.dataset.value) || 0;
        star.disabled = submitting || !activeChallengeRatingsOpen;
        star.classList.toggle('active', value <= score);
        star.textContent = value <= score ? '\u2605' : '\u2606';
        star.setAttribute('aria-pressed', value === score ? 'true' : 'false');
    });
    const button = card.querySelector('.challenge-submit-btn');
    if (button) {
        button.disabled = submitting || !activeChallengeRatingsOpen;
        button.setAttribute('aria-busy', submitting ? 'true' : 'false');
        button.textContent = submitting
            ? 'Submitting...'
            : (savedScore ? 'Update Rating' : 'Submit Rating');
    }
    const status = card.querySelector('.challenge-rating-status');
    if (!status) return;
    status.dataset.state = '';
    if (submitting) {
        status.textContent = 'Submitting your vote ...';
        status.dataset.state = 'submitting';
    } else if (unconfirmed) {
        status.textContent = 'Could not confirm your vote. Rechecking...';
        status.dataset.state = 'error';
    } else if (!activeChallengeRatingsOpen) {
        status.textContent = challengeRatingDrafts.has(challengeKey)
            ? 'Challenge rating closed. Your changes were not submitted.'
            : savedScore
            ? 'Challenge rating closed. Your rating: ' +
                savedScore + '/5 submitted.'
            : 'Challenge rating closed.';
        status.dataset.state = 'closed';
    } else if (challengeRatingDrafts.has(challengeKey)) {
        status.textContent = 'Changes not submitted.';
        status.dataset.state = 'draft';
    } else if (savedScore) {
        status.textContent = 'Your rating: ' + savedScore + '/5 submitted.';
        status.dataset.state = 'submitted';
    } else {
        status.textContent = '';
    }
}

function applySavedChallengeResponses(data, presentationKey) {
    if (activeChallengePresentationKey !== presentationKey) {
        resetChallengePresentation(presentationKey);
    }
    const pendingHand = challengeHandConfirmationPending;
    const handConfirmationResolved = Boolean(
        pendingHand &&
        pendingHand.presentationKey === presentationKey &&
        !_challengeToggleInFlight &&
        typeof data.challenge_hand_raised === 'boolean'
    );
    if (!_challengeToggleInFlight &&
            typeof data.challenge_hand_raised === 'boolean') {
        challengeHandRaised = data.challenge_hand_raised;
        if (handConfirmationResolved) {
            challengeHandConfirmationPending = null;
        }
    }
    const serverRatings = data.challenge_ratings || {};
    let resolvedUncertainVote = false;
    const challenges = lastReceivedState?.active_challenges || [];
    reconcileActiveChallengeState(challenges, presentationKey);
    for (const challenge of challenges) {
        const key = String(challenge.challenge_key);
        const serverScore = Number(serverRatings[key]) || 0;
        if (serverScore) savedChallengeRatings[key] = serverScore;
        else delete savedChallengeRatings[key];

        const pendingKey = challengeSubmissionKey(presentationKey, key);
        const pending = _unconfirmedChallengeRatings.get(pendingKey);
        if (pending && serverScore === pending.score) {
            _unconfirmedChallengeRatings.delete(pendingKey);
            resolvedUncertainVote = true;
            if (Number(challengeRatings[key]) === serverScore) {
                challengeRatingDrafts.delete(key);
            }
        }
        if (!challengeRatingDrafts.has(key) &&
                !challengeIsSubmitting(key)) {
            if (serverScore) challengeRatings[key] = serverScore;
            else delete challengeRatings[key];
        }
        updateChallengeRatingCard(key);
    }
    if (resolvedUncertainVote &&
            _unconfirmedChallengeRatings.size === 0 &&
            _studentVoteInFlight === 0 && !_studentVoteKnownFailure) {
        _studentVoteBatchFailure = null;
        setStudentVoteStatus('Vote submitted', 'submitted');
    }
    if (handConfirmationResolved) {
        const matchedRequest =
            challengeHandRaised === pendingHand.desiredRaised;
        let message;
        if (matchedRequest) {
            message = pendingHand.desiredRaised
                ? 'Hand raise confirmed.'
                : 'Hand lowered.';
        } else {
            message = pendingHand.desiredRaised
                ? 'Hand raise was not confirmed. Please try again.'
                : 'Your hand is still raised. Please try lowering it again.';
        }
        showToast(message, matchedRequest ? 'success' : 'warning');
    }
    if (lastReceivedState) renderChallengeUI(lastReceivedState);
}


function renderChallengeUI(state) {
    const section = document.getElementById('challenge-section');
    if (!section) return;

    const isCompetition = state.phase === 'competition';
    const hasActive = !!(isCompetition && state.active_team && state.active_question);
    const challenges = state.active_challenges || [];
    const presKey = getPresentationKey(state);
    if (presKey !== activeChallengePresentationKey) {
        resetChallengePresentation(presKey);
    }
    reconcileActiveChallengeState(challenges, presKey);

    activeChallengeRatingsOpen = hasActive &&
        state.challenge_ratings_open !== false;
    // Show section if we're in competition with an active presentation
    const showSection = hasActive;
    section.style.display = showSection ? 'block' : 'none';
    if (!showSection) {
        return;
    }

    const amPresenting = MY_TEAM_ID !== null && state.active_team.id === MY_TEAM_ID;
    const noTeam = MY_TEAM_ID === null;
    const raiseHandDiv = document.getElementById('challenge-raise-hand');
    if (raiseHandDiv) {
        raiseHandDiv.style.display = (amPresenting || noTeam) ? 'none' : 'block';
        const btn = document.getElementById('btn-raise-hand');
        const status = document.getElementById('raise-hand-status');
        if (btn) {
            btn.textContent = challengeHandRaised ? '✋ Hand Raised (Cancel)' : '🙋 Raise Hand to Challenge';
            btn.disabled = _challengeToggleInFlight;
            btn.setAttribute(
                'aria-busy',
                _challengeToggleInFlight ? 'true' : 'false'
            );
            btn.className = challengeHandRaised ? 'btn btn-secondary' : 'btn btn-primary';
            btn.setAttribute('aria-pressed', challengeHandRaised ? 'true' : 'false');
        }
        if (status) {
            status.textContent = challengeHandConfirmationPending
                ? 'Could not confirm. Checking...'
                : challengeHandRaised ? 'Waiting to be selected...' : '';
        }
    }

    // Render challenger cards (skip rebuild if data unchanged — avoids
    // destroying star/submit UI every 1s poll cycle)
    const cardsDiv = document.getElementById('challenge-cards');
    if (!cardsDiv) return;
    const cardsSig = JSON.stringify({
        presentationKey: presKey,
        challenges,
        amPresenting,
        MY_TEAM_ID
    });
    if (cardsSig !== _lastChallengeCardsSig) {
        _lastChallengeCardsSig = cardsSig;
        cardsDiv.innerHTML = '';

    for (const ch of challenges) {
        const isMyTeam = MY_TEAM_ID === ch.challenger_team_id;
        const isPresentingTeam = amPresenting;
        const canRate = !noTeam && !isMyTeam && !isPresentingTeam;

        const card = document.createElement('div');
        card.className = 'challenge-card';
        card.dataset.challengeKey = ch.challenge_key;

        const header = document.createElement('div');
        header.className = 'challenge-card-header';
        header.innerHTML = `<span class="challenge-badge">Challenger ${ch.challenge_num}</span>
            <strong>${escapeHtml(ch.challenger_name || '')}</strong>
            <span class="hint">from ${escapeHtml(ch.challenger_team_name || '')}</span>`;
        card.appendChild(header);

        if (canRate) {
            const ratingDiv = document.createElement('div');
            ratingDiv.className = 'challenge-rating-row';
            ratingDiv.innerHTML = `<span class="hint">Rate this question:</span>`;
            const starRow = document.createElement('div');
            starRow.className = 'star-row challenge-stars';
            starRow.dataset.challengeKey = ch.challenge_key;
            starRow.setAttribute('role', 'group');
            starRow.setAttribute('aria-label', `Rate challenger ${ch.challenger_name || ''}: 1 to 5 stars`);
            const currentVal = challengeRatings[ch.challenge_key] || 0;
            for (let i = 1; i <= 5; i++) {
                const star = document.createElement('button');
                star.type = 'button';
                star.className = 'star' + (i <= currentVal ? ' active' : '');
                star.dataset.value = i;
                star.innerHTML = i <= currentVal ? '★' : '☆';
                star.setAttribute('aria-label', `${i} out of 5`);
                star.addEventListener('click', () => selectChallengeRating(ch.challenge_key, i));
                starRow.appendChild(star);
            }
            ratingDiv.appendChild(starRow);

            const submitBtn = document.createElement('button');
            submitBtn.className =
                'btn btn-sm btn-primary challenge-submit-btn';
            submitBtn.textContent = 'Submit Rating';
            submitBtn.style.marginLeft = '8px';
            submitBtn.addEventListener('click', () => submitChallengeRating(ch.challenge_key));
            ratingDiv.appendChild(submitBtn);

            card.appendChild(ratingDiv);
            const ratingStatus = document.createElement('span');
            ratingStatus.className = 'challenge-rating-status';
            ratingStatus.setAttribute('role', 'status');
            ratingStatus.setAttribute('aria-live', 'polite');
            ratingDiv.appendChild(ratingStatus);

        } else if (noTeam) {
            const note = document.createElement('p');
            note.className = 'hint';
            note.textContent = '(You must be assigned to a team to rate challenges)';
            card.appendChild(note);
        } else if (isMyTeam) {
            const note = document.createElement('p');
            note.className = 'hint';
            note.textContent = '(You cannot rate your teammate)';
            card.appendChild(note);
        } else if (isPresentingTeam) {
            const note = document.createElement('p');
            note.className = 'hint';
            note.textContent = '(Your team is presenting, so you cannot rate challenges)';
            card.appendChild(note);
        }

        cardsDiv.appendChild(card);
    }
    } // end if (cardsSig changed)
    for (const challenge of challenges) {
        updateChallengeRatingCard(String(challenge.challenge_key));
    }
}

window.toggleRaiseHand = async function() {
    if (_challengeToggleInFlight) return;
    const state = lastReceivedState;
    if (!state) return;
    const presKey = getPresentationKey(state);
    if (!presKey || presKey !== activeChallengePresentationKey) return;
    const desiredRaised = !challengeHandRaised;
    ++_responseRequestId;
    challengeHandConfirmationPending = null;
    _challengeToggleInFlight = true;
    renderChallengeUI(state);
    try {
        const endpoint = desiredRaised ? '/api/raise_hand' : '/api/lower_hand';
        const data = await postJSON(endpoint, { presentation_key: presKey });
        if (data.success) {
            challengeHandRaised = desiredRaised;
            responseNeedsRefresh = true;
            window.requestDashboardPoll?.();
        } else if (data._network_error) {
            challengeHandConfirmationPending = {
                presentationKey: presKey,
                desiredRaised
            };
            responseNeedsRefresh = true;
            showToast('Could not confirm. Checking...', 'warning');
        } else {
            showToast(data.error || 'Failed', 'error');
        }
    } catch (e) {
        challengeHandConfirmationPending = {
            presentationKey: presKey,
            desiredRaised
        };
        responseNeedsRefresh = true;
        showToast('Could not confirm. Checking...', 'warning');
    } finally {
        _challengeToggleInFlight = false;
        if (lastReceivedState) renderChallengeUI(lastReceivedState);
        if (responseNeedsRefresh) window.requestDashboardPoll?.();
    }
};

function selectChallengeRating(challengeKey, score) {
    const key = String(challengeKey);
    if (!activeChallengeRatingsOpen) return;
    if (challengeIsSubmitting(key)) return;
    challengeRatings[key] = score;
    if (Number(savedChallengeRatings[key]) === Number(score)) {
        challengeRatingDrafts.delete(key);
    } else {
        challengeRatingDrafts.add(key);
    }
    // Update star display
    const starRow = document.querySelector(`.challenge-stars[data-challenge-key="${challengeKey}"]`);
    if (starRow) {
        starRow.querySelectorAll('.star').forEach(s => {
            const val = parseInt(s.dataset.value);
            s.classList.toggle('active', val <= score);
            s.innerHTML = val <= score ? '★' : '☆';
        });
    }
    updateChallengeRatingCard(key);
}

window.submitChallengeRating = async function(challengeKey) {
    const key = String(challengeKey);
    const presentationKey = activeChallengePresentationKey;
    if (!presentationKey || challengeIsSubmitting(key)) return;
    if (!activeChallengeRatingsOpen) {
        showToast('Challenge rating closed', 'warning');
        return;
    }
    const isActive = (lastReceivedState?.active_challenges || [])
        .some(challenge => String(challenge.challenge_key) === key);
    if (!isActive) {
        showToast('This challenge is no longer active', 'warning');
        return;
    }
    const score = Number(challengeRatings[key]) || 0;
    if (!score) {
        showToast('Select a rating first', 'warning');
        return;
    }

    const pendingKey = challengeSubmissionKey(presentationKey, key);
    const retryingUnconfirmed =
        _unconfirmedChallengeRatings.has(pendingKey);
    challengeRatingSubmissions.set(key, presentationKey);
    ++_responseRequestId;
    beginStudentVote();
    updateChallengeRatingCard(key);

    let success = false;
    let unconfirmed = false;
    let failureMessage = 'Vote not submitted. Please try again.';
    try {
        const data = await postJSON('/api/submit_challenge_rating', {
            challenge_key: key,
            score
        }, VOTE_RETRY_OPTIONS);
        success = data?.success === true;
        if (success) {
            _unconfirmedChallengeRatings.delete(pendingKey);
            if (retryingUnconfirmed &&
                    _unconfirmedChallengeRatings.size === 0 &&
                    _unconfirmedThumbVotes.size === 0 &&
                    !unconfirmedPresentationRating) {
                _studentVoteBatchFailure = null;
                _studentVoteKnownFailure = false;
            }
            savedChallengeRatings[key] = score;
            challengeRatingDrafts.delete(key);
            showToast('Rating submitted', 'success');
        } else if (data?._network_error) {
            unconfirmed = true;
            _unconfirmedChallengeRatings.set(pendingKey, {
                presentationKey,
                challengeKey: key,
                score
            });
            failureMessage =
                'Could not confirm your vote. Rechecking the saved response...';
            showToast(failureMessage, 'error');
        } else {
            failureMessage = data?.error || failureMessage;
            showToast(failureMessage, 'error');
        }
    } catch (error) {
        showToast(failureMessage, 'error');
    } finally {
        if (challengeRatingSubmissions.get(key) === presentationKey) {
            challengeRatingSubmissions.delete(key);
        }
        responseNeedsRefresh = true;
        finishStudentVote(
            success,
            'Vote submitted',
            failureMessage,
            unconfirmed
        );
        updateChallengeRatingCard(key);
        if (lastReceivedState) {
            reconcileActiveChallengeState(
                lastReceivedState.active_challenges || [],
                activeChallengePresentationKey
            );
        }
        if (!completePendingDashboardReload()) {
            window.requestDashboardPoll?.();
        }
    }
};
// Top challengers on the ended page (sibling of top-teams-display)
function renderTopChallengers(topChallengers) {
    const chDiv = document.getElementById('top-challengers-display');
    if (chDiv) {
        if (topChallengers && topChallengers.length > 0) {
            chDiv.innerHTML = '<h3>🏅 Best Challenger</h3>';
            const list = document.createElement('div');
            list.className = 'top-teams-list';
            for (const ch of topChallengers) {
                const item = document.createElement('div');
                item.className = 'top-team-item';
                const medal = ch.rank === 1 ? '🥇' : ch.rank === 2 ? '🥈' : '🥉';
                item.innerHTML = `<span class="medal">${medal}</span> ${escapeHtml(ch.name)}`;
                list.appendChild(item);
            }
            chDiv.appendChild(list);
        } else {
            chDiv.innerHTML = '';
        }
    }
}

const _knownParticipationPresentationKeys = new Set();
let _participationHistorySeeded = false;

function participationHistoryIdentity(item) {
    if (!item || typeof item !== 'object') return '';
    return String(item.presentation_key ||
        `${item.started_at || ''}:${item.team_id || ''}:${item.question_id || ''}`);
}

function seedCompetitionPresentationHistory(history) {
    _knownParticipationPresentationKeys.clear();
    if (Array.isArray(history)) {
        history.forEach(item => {
            const key = participationHistoryIdentity(item);
            if (key) _knownParticipationPresentationKeys.add(key);
        });
    }
    _participationHistorySeeded = true;
}

function competitionTeamOption(teamId) {
    const select = document.getElementById('comp-team');
    if (!select) return null;
    return Array.from(select.options).find(option =>
        option.value && String(option.value) === String(teamId)
    ) || null;
}

function updateCompetitionTeamOptionLabel(option) {
    if (!option || !option.value) return;
    const teamName = option.dataset.baseLabel ||
        option.dataset.teamName ||
        option.textContent.trim().replace(/ \(empty\)$/, '');
    option.dataset.baseLabel = teamName;
    const memberCount = normalizedParticipationCount(option.dataset.memberCount);
    const neverCount = normalizedParticipationCount(option.dataset.neverCount);
    if (memberCount === 0) {
        option.textContent = `${teamName} (empty)`;
        return;
    }
    const completed = option.dataset.completed === '1' ? ' · completed' : '';
    option.textContent = `${teamName} · ${neverCount}/${memberCount} no prior turn${completed}`;
}

function updateCompetitionTeamSummary(section) {
    if (!section) return;
    const members = Array.from(
        section.querySelectorAll('.team-participation-member')
    );
    const neverCount = members.filter(member =>
        normalizedParticipationCount(member.dataset.presentationCount) === 0
    ).length;
    const summary = section.querySelector('[data-role="team-summary"]');
    if (summary) {
        summary.textContent = members.length
            ? `${neverCount} of ${members.length} members have no completed team turns.`
            : 'No members.';
    }
    const option = competitionTeamOption(section.dataset.teamId);
    if (option) {
        option.dataset.memberCount = String(members.length);
        option.dataset.neverCount = String(neverCount);
        updateCompetitionTeamOptionLabel(option);
    }
}

function setMemberParticipationCount(member, kind, value) {
    const count = normalizedParticipationCount(value);
    const isPresentation = kind === 'presentation';
    const datasetKey = isPresentation ? 'presentationCount' : 'challengerCount';
    const valueSelector = isPresentation ? '.team-turn-count' : '.challenge-turn-count';
    member.dataset[datasetKey] = String(count);
    const valueElement = member.querySelector(valueSelector);
    if (!valueElement) return;
    valueElement.textContent = String(count);
    valueElement.closest('.participation-badge')?.classList.toggle('is-zero', count === 0);
}

function applyStudentParticipationCounts(record) {
    const studentId = record?.student_id ?? record?.challenger_id;
    if (studentId == null) return;
    const hasPresentation = Object.prototype.hasOwnProperty.call(
        record, 'presentation_count'
    );
    const hasChallenge = Object.prototype.hasOwnProperty.call(
        record, 'challenger_count'
    );
    if (!hasPresentation && !hasChallenge) return;
    document.querySelectorAll('.team-participation-member').forEach(member => {
        if (String(member.dataset.studentId) !== String(studentId)) return;
        if (hasPresentation) {
            setMemberParticipationCount(
                member, 'presentation', record.presentation_count
            );
        }
        if (hasChallenge) {
            setMemberParticipationCount(
                member, 'challenge', record.challenger_count
            );
        }
        updateCompetitionTeamSummary(member.closest('.team-participation-team'));
    });
}


const COMPETITION_PARTICIPATION_READBACK_ATTEMPTS = 2;
const COMPETITION_PARTICIPATION_READBACK_RETRY_MS = 300;
const COMPETITION_PARTICIPATION_LATER_RETRY_MS = 5000;
let _competitionParticipationReadbackSerial = 0;
let _competitionParticipationReadbackAppliedSerial = 0;
let _competitionParticipationReadbackDirty = false;
let _competitionParticipationNextRetryAt = 0;
let _competitionParticipationWarningShown = false;
let _competitionParticipationStateIdentity = null;

function markCompetitionParticipationCountsDirty() {
    _competitionParticipationReadbackDirty = true;
    _competitionParticipationNextRetryAt = 0;
}

async function refreshCompetitionParticipationCounts() {
    const requestSerial = ++_competitionParticipationReadbackSerial;
    try {
        const { res, data } = await fetchJSONWithTimeout('/api/teams');
        if (!res.ok || !Array.isArray(data)) return false;
        // Only the newest response may update the DOM. If a newer successful
        // readback already applied, this request is also satisfied. A newer
        // failed readback leaves the counts dirty for a later instructor poll.
        if (requestSerial !== _competitionParticipationReadbackSerial) {
            return _competitionParticipationReadbackAppliedSerial > requestSerial;
        }
        data.forEach(team => {
            const members = Array.isArray(team.members) ? team.members : [];
            members.forEach(member => applyStudentParticipationCounts({
                student_id: member.id,
                presentation_count: member.presentation_count,
                challenger_count: member.challenger_count,
            }));
        });
        _competitionParticipationReadbackAppliedSerial = requestSerial;
        _competitionParticipationReadbackDirty = false;
        _competitionParticipationNextRetryAt = 0;
        _competitionParticipationWarningShown = false;
        return true;
    } catch (error) {
        return false;
    }
}

async function reconcileCompetitionParticipationCounts(
        { markDirty = true } = {}) {
    if (markDirty) markCompetitionParticipationCountsDirty();
    if (!_competitionParticipationReadbackDirty) return true;
    for (let attempt = 0;
            attempt < COMPETITION_PARTICIPATION_READBACK_ATTEMPTS;
            attempt += 1) {
        if (await refreshCompetitionParticipationCounts()) return true;
        if (attempt + 1 < COMPETITION_PARTICIPATION_READBACK_ATTEMPTS) {
            await new Promise(resolve => window.setTimeout(
                resolve, COMPETITION_PARTICIPATION_READBACK_RETRY_MS
            ));
        }
    }
    _competitionParticipationReadbackDirty = true;
    _competitionParticipationNextRetryAt =
        Date.now() + COMPETITION_PARTICIPATION_LATER_RETRY_MS;
    if (!_competitionParticipationWarningShown) {
        _competitionParticipationWarningShown = true;
        showToast(
            'Participation counts may be stale. Retrying automatically.',
            'warning'
        );
    }
    return false;
}

function retryCompetitionParticipationCountsIfDirty() {
    if (!_competitionParticipationReadbackDirty ||
            Date.now() < _competitionParticipationNextRetryAt) return;
    _competitionParticipationNextRetryAt =
        Date.now() + COMPETITION_PARTICIPATION_LATER_RETRY_MS;
    void reconcileCompetitionParticipationCounts({ markDirty: false });
}

function competitionParticipationStateIdentity(state) {
    const challengeKeys = Array.isArray(state?.active_challenges)
        ? state.active_challenges
            .map(challenge => String(challenge?.challenge_key || ''))
            .filter(Boolean)
            .sort()
        : [];
    return JSON.stringify([
        state?.phase || '',
        Number(state?.session_key) || 0,
        state?.poll_question_key || '',
        state?.active_team_id ?? '',
        state?.active_question_id ?? '',
        challengeKeys,
    ]);
}

function syncCompetitionParticipationStateIdentity(state) {
    if (!state || !document.getElementById('team-participation-preview')) return;
    const identity = competitionParticipationStateIdentity(state);
    if (identity === _competitionParticipationStateIdentity) return;
    _competitionParticipationStateIdentity = identity;
    markCompetitionParticipationCountsDirty();
}
function syncCompetitionPresentationHistory(history) {
    if (!Array.isArray(history)) return;
    if (!_participationHistorySeeded) {
        seedCompetitionPresentationHistory(history);
        return;
    }
    let historyChanged = false;
    history.forEach(item => {
        const key = participationHistoryIdentity(item);
        if (!key || _knownParticipationPresentationKeys.has(key)) return;
        _knownParticipationPresentationKeys.add(key);
        historyChanged = true;
    });
    if (historyChanged) markCompetitionParticipationCountsDirty();
}

function showCompetitionTeamParticipation(teamId) {
    const container = document.getElementById('team-participation-preview');
    if (!container) return;
    const normalizedTeamId = teamId == null ? '' : String(teamId);
    let found = false;
    container.querySelectorAll('.team-participation-team').forEach(section => {
        const visible = normalizedTeamId !== '' &&
            String(section.dataset.teamId) === normalizedTeamId;
        section.hidden = !visible;
        found = found || visible;
    });
    const placeholder = document.getElementById('team-participation-placeholder');
    if (placeholder) placeholder.hidden = found;
    container.dataset.visibleTeamId = found ? normalizedTeamId : '';
}

function renderInstructorChallenge(state) {
    const panel = document.getElementById('challenge-instructor-panel');
    if (!panel) return;

    const hands = state.challenge_hands || [];
    const challenges = state.active_challenges || [];
    const summaries = state.challenge_rating_summaries || {};

    // Render raised hands
    const handsDiv = document.getElementById('challenge-hands-list');
    if (handsDiv) {
        if (hands.length === 0) {
            handsDiv.innerHTML = '<span class="hint">No raised hands yet.</span>';
        } else {
            handsDiv.innerHTML = '';
            for (const h of hands) {

                const challengeTurns = normalizedParticipationCount(h.challenger_count);
                const row = document.createElement('div');
                row.className = 'challenge-hand-row';
                row.innerHTML = `<span class="challenge-hand-details">
                    <span>${escapeHtml(h.student_name)} (${escapeHtml(h.student_team_name || '')})</span>
                    <span class="participation-badge${challengeTurns === 0 ? ' is-zero' : ''}">Challenge turns: <strong>${challengeTurns}</strong></span>
                </span>`;
                const btn = document.createElement('button');
                btn.className =
                    'btn btn-sm btn-primary challenge-mutating-btn';
                btn.textContent = 'Select as Challenger';
                btn.disabled = ratingsSettling;
                btn.addEventListener('click', () => selectChallenger(h.student_id));
                row.appendChild(btn);
                handsDiv.appendChild(row);
            }
        }
    }

    // Render active challengers
    const activeDiv = document.getElementById('challenge-active-list');
    if (activeDiv) {
        if (challenges.length === 0) {
            activeDiv.innerHTML = '';
        } else {
            activeDiv.innerHTML = '';
            for (const ch of challenges) {
                const summary = summaries[ch.challenge_key];

                const challengeTurns = normalizedParticipationCount(ch.challenger_count);
                const submitted = Number(summary?.submitted_count) || 0;
                const eligible = Number(summary?.eligible_count) || 0;
                const ratingText =
                    `${submitted} out of ${eligible} students voted`;
                const row = document.createElement('div');
                row.className = 'challenge-active-row';
                row.innerHTML = `<span class="challenge-active-details">
                    <strong>Challenger ${ch.challenge_num}:</strong>
                    ${escapeHtml(ch.challenger_name)} (${escapeHtml(ch.challenger_team_name || '')})
                    <span class="participation-badge${challengeTurns === 0 ? ' is-zero' : ''}">Challenge turns: <strong>${challengeTurns}</strong></span>
                    <span class="hint">${ratingText}</span>
                </span>`;
                const btn = document.createElement('button');
                btn.className =
                    'btn btn-sm btn-danger challenge-mutating-btn';
                btn.textContent = 'Clear';
                btn.disabled = ratingsSettling;
                btn.addEventListener('click', () => clearChallenger(ch.challenge_key));
                row.appendChild(btn);
                activeDiv.appendChild(row);
            }
        }
    }
}

window.selectChallenger = async function(studentId) {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    const payload = instructorStatePayload({
        student_id: studentId
    });
    _instructorActionInFlight = true;
    try {
        const data = await postJSON('/api/select_challenger', payload);
        if (!data.success) {
            showInstructorMutationError(
                data,
                'Failed to select challenger'
            );
        } else {
            const ratingsClosed = data.challenge_ratings_open === false ||
                (data.challenge_ratings_open == null &&
                 lastReceivedState?.challenge_ratings_open === false &&
                 !lastReceivedState?.poll_active);
            if (ratingsClosed) {
                showToast(
                    'Challenger selected. Start Poll to collect ratings.',
                    'warning'
                );
            }
            await reconcileCompetitionParticipationCounts();
            window.instructorPollOnce?.();
        }
    } finally {
        _instructorActionInFlight = false;
    }
};
window.clearChallenger = async function(challengeKey) {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    const payload = instructorStatePayload({
        challenge_key: challengeKey
    });
    _instructorActionInFlight = true;
    try {
        let data = await postJSON('/api/clear_challenger', payload);
        if (data.requires_discard) {
            const count = Number(
                data.challenge_rating_count ?? data.rating_count
            ) || 0;
            if (!confirm(
                    'This challenger has ' + count + ' submitted rating' +
                    (count === 1 ? '' : 's') +
                    '. Permanently discard them and clear the challenger?')) {
                return;
            }
            data = await postJSON('/api/clear_challenger', {
                ...payload,
                discard_ratings: true
            });
        }
        if (!data.success) {
            showInstructorMutationError(
                data,
                'Failed to clear challenger'
            );
        } else {
            await reconcileCompetitionParticipationCounts();
            window.instructorPollOnce?.();
        }
    } finally {
        _instructorActionInFlight = false;
    }
};
if (instructor) {
    // Match the compact JSON representation used by poll responses before
    // comparing snapshots. Server-rendered attribute whitespace must not
    // cause a redundant history redraw on the first poll.
    try {
        const initialHistory = JSON.parse(
            instructor.dataset.presentationHistory || '[]'
        );
        const normalizedHistory = Array.isArray(initialHistory) ? initialHistory : [];
        instructor.dataset.presentationHistory = JSON.stringify(normalizedHistory);
        seedCompetitionPresentationHistory(normalizedHistory);
    } catch (e) {
        instructor.dataset.presentationHistory = '[]';
        seedCompetitionPresentationHistory([]);
    }

    const competitionTeamSelect = document.getElementById('comp-team');
    if (competitionTeamSelect) {
        competitionTeamSelect.addEventListener('change', () => {
            showCompetitionTeamParticipation(competitionTeamSelect.value);
        });
    }
    showCompetitionTeamParticipation(
        instructor.dataset.activeTeamId || competitionTeamSelect?.value || ''
    );

    // Active question rendering is handled by instructorPollOnce() below,
    // which fires immediately and includes the same state data. No need
    // for a separate /api/state fetch on page load.

    let _instrPollInProgress = false;
    let _instrPollTimer = null;
    let _instrRosterVersion = Number(instructor.dataset.rosterVersion || 0);
    let _instrKnownQuestionId = null;
    let _instrKnownQuestionRevision = null;
    let _instrPollFailures = 0;
    let _instrPollStopped = false;
    let _lastStudentTableRefresh = 0;
    let _instrConnectionInterrupted = false;
    let _instrConnectionStatusTimer = null;

    function showInstructorConnectionStatus(message, recovered = false) {
        const status = document.getElementById('instructor-connection-status');
        if (!status) return;
        if (_instrConnectionStatusTimer) clearTimeout(_instrConnectionStatusTimer);
        status.textContent = message;
        status.classList.toggle('recovered', recovered);
        status.hidden = false;
        _instrConnectionInterrupted = !recovered;
        if (recovered) {
            _instrConnectionStatusTimer = setTimeout(() => {
                status.hidden = true;
                _instrConnectionStatusTimer = null;
            }, 3000);
        }
    }

    function instructorPollDelay(interval) {
        const base = Math.max(1000, Number(interval) || 3000);
        const backedOff = Math.min(30000, base * Math.pow(2, _instrPollFailures));
        return Math.round(backedOff * (1 + Math.random() * 0.2));
    }

    function instructorStructureChanged(state) {
        const normalized = value => value == null ? '' : String(value);
        const differsWhenPresent = (field, current, next) =>
            Object.prototype.hasOwnProperty.call(state, field) &&
            normalized(current) !== normalized(next);

        return normalized(instructor.dataset.phase) !== normalized(state.phase) ||
            differsWhenPresent(
                'session_key',
                instructor.dataset.sessionKey,
                state.session_key
            ) ||

            differsWhenPresent(
                'discussion_week',
                instructor.dataset.discussionWeek,
                state.discussion_week
            ) ||
            differsWhenPresent(
                'teams_locked',
                instructor.dataset.teamsLocked,
                state.teams_locked ? '1' : '0'
            ) ||
            differsWhenPresent('max_teams', instructor.dataset.maxTeams, state.max_teams) ||
            differsWhenPresent('max_members', instructor.dataset.maxMembers, state.max_members);
    }

    // Active team/question changes are NOT structural: both competition
    // blocks render server-side, so reconcilePresentationBlocks swaps them
    // in place instead of reloading.
    function instructorPresentationChanged(state) {
        const normalized = value => value == null ? '' : String(value);
        return normalized(instructor.dataset.activeTeamId) !== normalized(state.active_team?.id) ||
            normalized(instructor.dataset.activeQuestionId) !== normalized(state.active_question?.id) ||
            normalized(instructor.dataset.pollQuestionKey) !==
                normalized(state.poll_question_key);
    }

    // Swap the competition card between the idle block (team/question
    // selects) and the active block (timer/poll controls), and seed the
    // fields the rest of this poll cycle then repopulates. Returns false
    // when the expected structure is missing so the caller can reload.
    function reconcilePresentationBlocks(state) {
        const activeBlock = document.getElementById('presentation-active');
        const idleBlock = document.getElementById('presentation-idle');
        if (!activeBlock || !idleBlock) return false;
        const isActive = Boolean(state.active_team && state.active_question);
        const nextPresentationKey = state.poll_question_key == null
            ? '' : String(state.poll_question_key);
        const nextQuestionId = isActive ? state.active_question.id : null;
        const nextQuestionRevision = isActive
            ? (state.active_question.revision || null) : null;
        const sameKnownQuestion = isActive &&
            String(_instrKnownQuestionId ?? '') === String(nextQuestionId ?? '') &&
            String(_instrKnownQuestionRevision ?? '') ===
                String(nextQuestionRevision ?? '');
        activeBlock.style.display = isActive ? '' : 'none';
        idleBlock.style.display = isActive ? 'none' : '';
        instructor.dataset.activeTeamId = isActive ? String(state.active_team.id) : '';
        instructor.dataset.activeQuestionId = isActive ? String(state.active_question.id) : '';
        instructor.dataset.pollQuestionKey = nextPresentationKey;
        if (isActive) {
            const teamName = document.getElementById('active-team-name');
            if (teamName) teamName.textContent = state.active_team.name || '';
            const qText = document.getElementById('active-question-text');
            if (qText) qText.dataset.qnum = state.active_question.id;
            const pollCount = document.getElementById('poll-count');
            if (pollCount) pollCount.textContent = 'Loading rating activity...';
            if (!sameKnownQuestion) {
                lastRenderedQuestionKey = null;
                _instrKnownQuestionId = null;
                _instrKnownQuestionRevision = null;
            }
        } else {
            stopCountdownTimer();
            stopPollGlide();
            setTimerExpired(false);
            lastRenderedQuestionKey = null;
            _instrKnownQuestionId = null;
            _instrKnownQuestionRevision = null;
            markPresentedCompQuestionOptions(state.presentation_history);
        }
        return true;
    }

    // Mirror the server-rendered "— previously presented" option suffix
    // once a presentation moves into the history.
    function markPresentedCompQuestionOptions(history) {
        const select = document.getElementById('comp-question');
        if (!select || !Array.isArray(history)) return;
        const presented = new Set(
            history.map(item => String(item?.question_id ?? ''))
        );
        select.querySelectorAll('option[value]').forEach(option => {
            if (!option.value || !presented.has(option.value)) return;
            if (!option.textContent.endsWith(' — previously presented')) {
                option.textContent += ' — previously presented';
            }
        });
    }

    function updateCapacityStatus(state) {
        const el = document.getElementById('setup-capacity-warning');
        if (!el) return;
        const total = Number(instructor.dataset.studentCount) || 0;
        const maxTeams = Number(state.max_teams) || Number(instructor.dataset.maxTeams) || 0;
        const maxMembers = Number(state.max_members) || Number(instructor.dataset.maxMembers) || 0;
        const capacity = maxTeams * maxMembers;
        const unassigned = Number(state.unassigned_count) || 0;
        const overCapacity = total > capacity;
        const hasUnassigned = unassigned > 0;
        el.classList.toggle('capacity-danger', overCapacity);
        el.classList.toggle('capacity-warning', !overCapacity && hasUnassigned);
        el.classList.toggle('capacity-success', !overCapacity && !hasUnassigned);
        let message;
        if (overCapacity) {
            message = `\u26A0 Capacity warning: ${total} students, but only ${capacity} team places. At least ${total - capacity} cannot be assigned.`;
        } else if (unassigned > 0) {
            message = `\u26A0 ${unassigned} student${unassigned === 1 ? '' : 's'} still unassigned (${capacity - total} spare team places).`;
        } else {
            message = `\u2713 All ${total} students are assigned. Capacity: ${capacity}.`;
        }
        if (el.textContent.trim() !== message) el.textContent = message;
    }

    async function instructorPollOnce() {
        if (_instrPollTimer !== null) {
            clearTimeout(_instrPollTimer);
            _instrPollTimer = null;
        }
        if (_instrPollInProgress || _instrPollStopped) return;
        _instrPollInProgress = true;
        let interval = 3000;
        try {
            const pollParams = new URLSearchParams();
            if (_instrKnownQuestionId != null) {
                pollParams.set('known_question_id', _instrKnownQuestionId);
            }
            if (_instrKnownQuestionRevision) {
                pollParams.set('known_question_revision', _instrKnownQuestionRevision);
            }
            const questionQuery = pollParams.toString();
            const { res, data } = await fetchJSONWithTimeout(
                '/api/poll' + (questionQuery ? `?${questionQuery}` : '')
            );
            if (pageNavigationInProgress) {
                _instrPollStopped = true;
                return;
            }
            if (res.status === 401) {
                _instrPollStopped = true;
                redirectToSessionEnded();
                return;
            }
            if (!res.ok) throw new Error('poll failed');
            if (_instrConnectionInterrupted) {
                showInstructorConnectionStatus('Live updates restored.', true);
            }
            _instrPollFailures = 0;
            const state = data.state;
            applyRatingsSettlingState(state);

            if (state && instructorStructureChanged(state)) {
                _instrPollStopped = true;
                window.location.reload();
                return;
            }

            // Presentation start/next/cancel swap the two competition
            // blocks in place; reload only if the structure is missing.
            if (state && instructor.dataset.phase === 'competition' &&
                    instructorPresentationChanged(state) &&
                    !reconcilePresentationBlocks(state)) {
                _instrPollStopped = true;
                window.location.reload();
                return;
            }
            if (state && instructor.dataset.phase === 'competition') {
                const selectedTeamId = state.active_team?.id ??
                    document.getElementById('comp-team')?.value ?? '';
                showCompetitionTeamParticipation(selectedTeamId);
            }


            if (document.getElementById('disc-questions-list') &&
                    (Number(state.discussion_questions_version) || 0) !== renderedDiscQuestionsVersion) {
                try {
                    await refreshDiscussionQuestions();
                } catch (e) {
                    // Retry on the next poll without blocking other updates.
                }
            }
            const participation = document.getElementById('thumb-participation');
            if (participation) {
                const count = Number(state.thumb_participant_count) || 0;
                const eligible = Number(state.thumb_eligible_count) || 0;
                participation.textContent = `Thumb participation: ${count} out of ${eligible} eligible students participated.`;
            }
            const teamProgress = document.getElementById('thumb-team-progress');
            if (teamProgress && Array.isArray(state.thumb_team_progress)) {
                const signature = JSON.stringify(state.thumb_team_progress);
                if (teamProgress.dataset.signature !== signature) {
                    teamProgress.dataset.signature = signature;
                    teamProgress.innerHTML = '';
                    for (const [index, team] of state.thumb_team_progress.entries()) {
                        const item = document.createElement('li');
                        const teamName = team.team_name || `Team ${index + 1}`;
                        const memberCount = Number(team.member_count) || 0;
                        const eligibleCount = Number(team.eligible_count) || 0;
                        if (memberCount === 0) {
                            item.textContent = `${teamName}: No students`;
                        } else if (eligibleCount <= 0) {
                            item.textContent = `${teamName}: Not eligible`;
                        } else {
                            const thumbCount = Number(team.thumb_count) || 0;
                            const participantCount = Number(team.participant_count) || 0;
                            const thumbLabel = thumbCount === 1 ? 'thumb' : 'thumbs';
                            const studentLabel = eligibleCount === 1 ? 'student' : 'students';
                            item.textContent = `${teamName}: ${thumbCount} ${thumbLabel} | ${participantCount} of ${eligibleCount} ${studentLabel} participated`;
                        }
                        teamProgress.appendChild(item);
                    }
                    teamProgress.hidden = state.thumb_team_progress.length === 0;
                }
            }

            updateCapacityStatus(state);
            applySessionTimerState(
                state.session_started_at || null,
                state.session_elapsed
            );

            // --- Roster refresh (setup phase) ---
            const rosterGrid = document.querySelector('.roster-grid');
            if (rosterGrid && _instrRosterVersion !== state.roster_version) {
                const card = rosterGrid.closest('.card');
                if (card && card.querySelector('h2')?.textContent?.includes('Team and Members')) {
                    try {
                        const { res: rosterResponse, data: rosterData } =
                            await fetchJSONWithTimeout(
                                `/api/teams?version=${encodeURIComponent(state.roster_version)}`
                            );
                        if (!rosterResponse.ok) throw new Error('roster refresh failed');
                        renderRosterGrid(rosterGrid, rosterData, null);
                        _instrRosterVersion = state.roster_version;
                        instructor.dataset.rosterVersion = String(state.roster_version || 0);
                    } catch (e) {
                        // Retry on the next poll without blocking timer/rating updates.
                    }
                }
            }

            // Skip the tbody re-render while a team-picker dropdown is open so
            // the picker isn't orphaned mid-click; retry on the next poll.
            if (document.getElementById('student-tbody') &&
                    !document.querySelector('.team-picker:not(.filter-picker)') &&
                    Date.now() - _lastStudentTableRefresh >= 5000) {
                _lastStudentTableRefresh = Date.now();
                loadStudentTable();
            }

            // --- Competition state: poll count + active question + poll status ---
            const timerBox = document.getElementById('timer-box');
            if (timerBox && state) {
                const pollCount = document.getElementById('poll-count');
                if (pollCount) {
                    const count = Number(state.poll_count) || 0;
                    const eligible = Number(state.poll_eligible_count) || 0;
                    pollCount.textContent =
                        `${count} out of ${eligible} students voted`;
                }
                const presentationIndex = document.getElementById('presentation-index');
                if (presentationIndex) {
                    presentationIndex.textContent = state.presentation_number
                        ? `#${state.presentation_number}`
                        : 'Unavailable. Refresh if this persists';
                    presentationIndex.classList.toggle('text-danger', !state.presentation_number);
                }
                // Active question markdown — only re-render when question changes
                const qText = document.getElementById('active-question-text');
                if (qText && state.active_question) {
                    const qKey = `${state.active_question.id}:${state.active_question.revision || ''}`;
                    if (qKey !== lastRenderedQuestionKey) {
                        let newHtml;
                        if (state.active_question.html_content) {
                            newHtml = sanitizeHtml(state.active_question.html_content);
                        } else {
                            newHtml = renderPresentationQuestion(state.active_question);
                        }
                        qText.innerHTML = newHtml;
                        safeTypeset(qText);
                        safeHighlight(qText);
                        lastRenderedQuestionKey = qKey;
                    }
                    _instrKnownQuestionId = state.active_question.id;
                    _instrKnownQuestionRevision = state.active_question.revision || null;
                } else if (qText) {
                    qText.innerHTML = '';
                    lastRenderedQuestionKey = null;
                    _instrKnownQuestionId = null;
                    _instrKnownQuestionRevision = null;
                }

                const timerValue = document.getElementById('timer-value');
                const pauseButton = document.getElementById('btn-pause');
                const resumeButton = document.getElementById('btn-resume');
                const remaining = Number(state.presentation_remaining);
                ratingPollOpenForTimer = !!state.poll_active;
                if (state.presentation_started_at && remaining > 0) {
                    startCountdownTimer(remaining);
                    if (pauseButton) pauseButton.style.display = '';
                    if (resumeButton) resumeButton.style.display = 'none';
                } else if (state.presentation_started_at) {
                    stopCountdownTimer();
                    if (timerValue) timerValue.textContent = '00:00';
                    setTimerExpired(true);
                    if (pauseButton) pauseButton.style.display = 'none';
                    if (resumeButton) resumeButton.style.display = 'none';
                } else if (state.presentation_remaining != null) {
                    stopCountdownTimer();
                    if (timerValue) timerValue.textContent = formatDuration(Math.max(0, remaining));
                    setTimerExpired(Math.max(0, remaining) <= 0);
                    if (pauseButton) pauseButton.style.display = 'none';
                    if (resumeButton) resumeButton.style.display = remaining > 0 ? '' : 'none';
                }

                // Keep poll controls synchronized across instructor tabs.
                const ps = document.getElementById('poll-status');
                if (state.poll_active && Number(state.poll_remaining) > 0) {
                    startPollGlide(state.poll_remaining, state.poll_duration || 40);
                    if (ps) ps.style.display = 'flex';
                } else {
                    stopPollGlide();
                    if (ps) ps.style.display = 'none';
                }
            }

            const completed = document.getElementById('completed-presentations');
            if (completed && state.completed_presentation_count != null) {
                const count = Number(state.completed_presentation_count) || 0;
                completed.textContent = `${count} completed`;
            }

            // History list: the "N completed" counter above updates live, so
            // re-render the server-rendered list when the history changes to
            // keep the two in agreement.
            if (document.getElementById('history-list') && Array.isArray(state.presentation_history)) {
                const historySignature = JSON.stringify(state.presentation_history);
                if (instructor.dataset.presentationHistory !== historySignature) {
                    instructor.dataset.presentationHistory = historySignature;
                    syncCompetitionPresentationHistory(state.presentation_history);
                    renderHistory();
                }
            }

            // --- Challenge feature: raised hands + active challengers ---
            syncCompetitionParticipationStateIdentity(state);
            renderInstructorChallenge(state);
            retryCompetitionParticipationCountsIfDirty();

            if (data.poll_interval) interval = data.poll_interval;
            if (data.stop_polling) _instrPollStopped = true;
        } catch (e) {
            _instrPollFailures = Math.min(_instrPollFailures + 1, 5);
            showInstructorConnectionStatus(
                'Live updates interrupted. Controls may be stale. Reconnecting...'
            );
        }
        finally {
            _instrPollInProgress = false;
            if (!_instrPollStopped && !pageNavigationInProgress) {
                _instrPollTimer = setTimeout(instructorPollOnce, instructorPollDelay(interval));
            }
        }
    }

    window.addEventListener('offline', () => {
        showInstructorConnectionStatus(
            'Live updates interrupted. Controls may be stale. Reconnecting...'
        );
    });
    window.addEventListener('online', instructorPollOnce);
    document.addEventListener('visibilitychange', () => {
        if (!document.hidden) instructorPollOnce();
    });
    instructorPollOnce();
    // Expose for global functions (randomAssign, unassignAll, etc.)
    window.instructorPollOnce = instructorPollOnce;
}

let _originalMaxTeams = null;

window.startModifyTeams = function() {
    if (_originalMaxTeams !== null) return; // already editing
    const display = document.getElementById('team-count-display');
    _originalMaxTeams = parseInt(display.textContent, 10);
    document.getElementById('btn-modify-teams').style.display = 'none';
    display.innerHTML = `
        <button class="btn btn-sm btn-secondary" onclick="stepTeamCount(-1)">−</button>
        <span id="editing-count">${_originalMaxTeams}</span>
        <button class="btn btn-sm btn-secondary" onclick="stepTeamCount(1)">+</button>
    `;
    display.classList.add('editing');
    const row = display.closest('.team-count-control');
    const actions = document.createElement('span');
    actions.id = 'team-count-actions';
    actions.innerHTML = `
        <button class="btn btn-sm btn-primary" onclick="confirmModifyTeams()">Confirm</button>
        <button class="btn btn-sm btn-secondary" onclick="cancelModifyTeams()">Cancel</button>
    `;
    row.appendChild(actions);
};

window.stepTeamCount = function(delta) {
    const span = document.getElementById('editing-count');
    const val = parseInt(span.textContent, 10) + delta;
    if (val < 1 || val > 20) return;
    span.textContent = val;
};

window.cancelModifyTeams = function() {
    const display = document.getElementById('team-count-display');
    display.textContent = _originalMaxTeams;
    display.classList.remove('editing');
    document.getElementById('btn-modify-teams').style.display = '';
    const actions = document.getElementById('team-count-actions');
    if (actions) actions.remove();
    _originalMaxTeams = null;
};

window.toggleLock = async function(btn) {
    const wasLocked = btn.textContent.includes('Unlock');
    btn.disabled = true;
    try {
        const data = await postJSON('/api/toggle_lock_teams', instructorStatePayload({
            locked: !wasLocked
        }));
        if (data.success) {
            if (wasLocked) {
                btn.textContent = 'Lock Teams';
                btn.classList.remove('btn-danger');
                btn.classList.add('btn-primary');
                showToast('Teams unlocked');
            } else {
                btn.textContent = 'Unlock Teams';
                btn.classList.remove('btn-primary');
                btn.classList.add('btn-danger');
                showToast('Teams locked', 'warning');
            }
        } else {
            showInstructorMutationError(data, 'Failed to change team locking');
        }
    } finally {
        btn.disabled = false;
    }
};

window.toggleSessionTimer = async function() {
    const btn = document.getElementById('btn-session-timer');
    const wasRunning = btn.textContent.includes('Stop');
    if (wasRunning) {
        const data = await postJSON('/api/stop_session_timer', instructorStatePayload());
        if (!data.success) {
            showInstructorMutationError(data, 'Failed to stop session timer');
            return;
        }
        applySessionTimerState(null, null);
        showToast('Session timer stopped');
    } else {
        const data = await postJSON('/api/start_session_timer', instructorStatePayload());
        if (!data.success) {
            showInstructorMutationError(data, 'Failed to start session timer');
            return;
        }
        applySessionTimerState(data.session_started_at, 0);
        showToast('Session timer started');
    }
};

// ---- max members per team ----
let _originalMaxMembers = null;

window.startModifyMembers = function() {
    if (_originalMaxMembers !== null) return; // already editing
    const display = document.getElementById('max-members-display');
    _originalMaxMembers = parseInt(display.textContent, 10);
    document.getElementById('btn-modify-members').style.display = 'none';
    display.innerHTML = `
        <button class="btn btn-sm btn-secondary" onclick="stepMemberCount(-1)">−</button>
        <span id="editing-member-count">${_originalMaxMembers}</span>
        <button class="btn btn-sm btn-secondary" onclick="stepMemberCount(1)">+</button>
    `;
    display.classList.add('editing');
    const row = display.closest('.team-count-control');
    const actions = document.createElement('span');
    actions.id = 'member-count-actions';
    actions.innerHTML = `
        <button class="btn btn-sm btn-primary" id="btn-confirm-members" onclick="confirmModifyMembers()">Confirm</button>
        <button class="btn btn-sm btn-secondary" onclick="cancelModifyMembers()">Cancel</button>
    `;
    row.appendChild(actions);
};

window.stepMemberCount = function(delta) {
    const span = document.getElementById('editing-member-count');
    const val = parseInt(span.textContent, 10) + delta;
    if (val < 1 || val > 99) return;
    span.textContent = val;
};

window.cancelModifyMembers = function() {
    const display = document.getElementById('max-members-display');
    display.textContent = _originalMaxMembers;
    display.classList.remove('editing');
    document.getElementById('btn-modify-members').style.display = '';
    const actions = document.getElementById('member-count-actions');
    if (actions) actions.remove();
    _originalMaxMembers = null;
};

window.confirmModifyMembers = async function() {
    const span = document.getElementById('editing-member-count');
    const newMax = parseInt(span.textContent, 10);
    if (newMax === _originalMaxMembers) {
        cancelModifyMembers();
        return;
    }
    if (newMax < _originalMaxMembers) {
        if (!confirm(`Reducing max members to ${newMax} will unassign excess students from teams over the limit. Continue?`)) {
            return;
        }
    }
    const confirmBtn = document.getElementById('btn-confirm-members');
    if (confirmBtn) confirmBtn.disabled = true;
    const data = await postJSON('/api/set_max_members', instructorStatePayload({
        max_members: newMax
    }));
    if (data.success) {
        // Restore display with new value
        const display = document.getElementById('max-members-display');
        display.textContent = newMax;
        display.classList.remove('editing');
        document.getElementById('btn-modify-members').style.display = '';
        const actions = document.getElementById('member-count-actions');
        if (actions) actions.remove();
        _originalMaxMembers = null;
        showToast(`Max members set to ${newMax}`);
        window.setTimeout(() => window.location.reload(), 250);
    } else {
        if (confirmBtn) confirmBtn.disabled = false;
        showInstructorMutationError(data, 'Failed to update max members');
    }
};

window.confirmModifyTeams = async function() {
    const span = document.getElementById('editing-count');
    const newMax = parseInt(span.textContent, 10);
    if (newMax === _originalMaxTeams) {
        cancelModifyTeams();
        return;
    }
    if (newMax < _originalMaxTeams) {
        if (!confirm(`Reducing to ${newMax} teams will unassign students in teams ${newMax + 1} through ${_originalMaxTeams}. Continue?`)) {
            return;
        }
    }
    const confirmBtn = document.querySelector('#team-count-actions .btn-primary');
    if (confirmBtn) confirmBtn.disabled = true;
    const data = await postJSON('/api/set_max_teams', instructorStatePayload({
        max_teams: newMax
    }));
    if (data.success) {
        // Restore display with new value
        const display = document.getElementById('team-count-display');
        display.textContent = newMax;
        display.classList.remove('editing');
        document.getElementById('btn-modify-teams').style.display = '';
        const actions = document.getElementById('team-count-actions');
        if (actions) actions.remove();
        _originalMaxTeams = null;
        showToast(`Team count set to ${newMax}`);
        window.setTimeout(() => window.location.reload(), 250);
    } else {
        if (confirmBtn) confirmBtn.disabled = false;
        showInstructorMutationError(data, 'Failed to update team count');
    }
};

// Prevents double-click from firing two concurrent instructor mutations.
let _instructorActionInFlight = false;

window.setPhase = async function(phase) {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    _instructorActionInFlight = true;
    try {
        const currentPhase = instructor?.dataset?.phase || '';
        // timer-box renders even while idle, so key off the tracked active
        // team instead of the element's presence.
        const leavingActivePresentation = phase !== 'competition' &&
            currentPhase === 'competition' && instructor?.dataset?.activeTeamId;
        let confirmEndSession = false;
        const permanentParticipationWarning =
            'The team presentation turn and any challenger turns will be ' +
            'permanently recorded. Use Cancel Mistake first if anything is ' +
            'incorrect.';
        if (phase === 'ended' && currentPhase !== 'ended') {
            const message = leavingActivePresentation
                ? 'End this session and finalize the active presentation? ' +
                  permanentParticipationWarning
                : 'End this classroom session?';
            if (!confirm(message)) return;
            confirmEndSession = true;
        } else if (leavingActivePresentation &&
                   !confirm(
                       'A presentation is still active. Leave this phase and ' +
                       'finalize the current presentation? ' +
                       permanentParticipationWarning
                   )) {
            return;
        }
        const phasePayload = instructorStatePayload({
            phase,
            confirm_end_session: confirmEndSession
        });
        const data = await postJSON('/api/set_phase', phasePayload);
        if (data.success) {
            window.location.reload();
        } else {
            showInstructorMutationError(data, 'Failed to change phase');
        }
    } finally {
        _instructorActionInFlight = false;
    }
};

/* ===== STUDENT TABLE (paginated) ===== */
let studentSort = 'student_id';
let studentOrder = 'asc';
let studentPage = 1;
let studentPerPage = parseInt(localStorage.getItem('popping-student-per-page') || '10', 10);
if (![10, 25, 50, 100].includes(studentPerPage)) studentPerPage = 10;
let teamFilterVal = '';
let _studentTableLoading = false;
let _studentTableLoadFailed = false;
let _studentTableRetryTimer = null;

window.setStudentPerPage = function(value) {
    const size = parseInt(value, 10);
    if (![10, 25, 50, 100].includes(size)) return;
    studentPerPage = size;
    localStorage.setItem('popping-student-per-page', String(size));
    studentPage = 1;
    loadStudentTable();
};

// Page-size selector next to the pagination controls; the choice persists
// across reloads via localStorage.
function initStudentPageSizeSelector() {
    const pag = document.getElementById('student-pagination');
    if (!pag || document.getElementById('student-per-page')) return;
    const wrapper = document.createElement('span');
    wrapper.className = 'pag-info';
    wrapper.innerHTML = `Rows per page: <select id="student-per-page" onchange="setStudentPerPage(this.value)">
        ${[10, 25, 50, 100].map(n => `<option value="${n}"${n === studentPerPage ? ' selected' : ''}>${n}</option>`).join('')}
    </select>`;
    pag.parentElement.insertBefore(wrapper, pag);
}

function setTeamFilterExpanded(expanded) {
    const menu = document.getElementById('team-filter-menu');
    const trigger = document.getElementById('team-filter-trigger');
    if (!menu) return false;
    const isOpen = Boolean(expanded);
    menu.style.display = isOpen ? 'block' : 'none';
    if (trigger) trigger.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    return isOpen;
}

window.toggleTeamFilter = function(e) {
    e.stopPropagation();
    const menu = document.getElementById('team-filter-menu');
    if (!menu) return;
    const willOpen = menu.style.display !== 'block';
    setTeamFilterExpanded(willOpen);
    if (willOpen) {
        setTimeout(() => {
            document.addEventListener('click', function close() {
                setTeamFilterExpanded(false);
                document.removeEventListener('click', close);
            }, { once: true });
        }, 0);
    }
};

window.setTeamFilter = function(val, label) {
    teamFilterVal = val;
    document.getElementById('team-filter-label').textContent = label;
    setTeamFilterExpanded(false);
    document.getElementById('team-filter-trigger')?.focus();
    studentPage = 1;
    loadStudentTable();
};

window.teamFilterOptionKeydown = function(event, val, label) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    event.stopPropagation();
    window.setTeamFilter(val, label);
};

let _searchDebounce = null;
window.debouncedStudentSearch = function() {
    if (_searchDebounce) clearTimeout(_searchDebounce);
    _searchDebounce = setTimeout(() => {
        studentPage = 1;
        loadStudentTable();
    }, 250);
};

window.loadStudentTable = async function() {
    if (_studentTableLoading) return;
    _studentTableLoading = true;
    const search = document.getElementById('student-search')?.value || '';
    const params = new URLSearchParams({
        page: studentPage, per_page: studentPerPage, sort: studentSort, order: studentOrder, search, team: teamFilterVal
    });
    let data;
    try {
        const { res, data: studentData } = await fetchJSONWithTimeout(
            '/api/students?' + params
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (!res.ok) {
            const loadError = new Error('student load failed');
            loadError.serverMessage = (studentData && studentData.error) || null;
            throw loadError;
        }
        data = studentData;
    } catch (error) {
        const tbody = document.getElementById('student-tbody');
        if (tbody && !tbody.querySelector('tr[data-id]')) {
            const columnCount = tbody.closest?.('table')?.querySelectorAll('thead th').length || 8;
            tbody.innerHTML = `<tr><td colspan="${columnCount}" class="empty">Could not load students. Retrying automatically.</td></tr>`;
        }
        if (!_studentTableLoadFailed) {
            showToast(
                (error && error.serverMessage) ||
                'Could not refresh the student list. Retrying automatically.',
                'error'
            );
        }
        _studentTableLoadFailed = true;
        if (_studentTableRetryTimer === null) {
            _studentTableRetryTimer = window.setTimeout(() => {
                _studentTableRetryTimer = null;
                loadStudentTable();
            }, 5000);
        }
        return;
    } finally {
        _studentTableLoading = false;
    }
    _studentTableLoadFailed = false;
    if (_studentTableRetryTimer !== null) {
        window.clearTimeout(_studentTableRetryTimer);
        _studentTableRetryTimer = null;
    }
    window._lastTeams = data.teams || [];
    const instructorPage = document.querySelector('.instructor[data-student-count]');
    if (instructorPage && data.course_total != null) {
        instructorPage.dataset.studentCount = String(
            Math.max(0, Number(data.course_total) || 0)
        );
    }

    const tbody = document.getElementById('student-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const teams = data.teams || [];

    data.students.forEach(s => {
        const teamHtml = s.team_name
            ? `<span class="team-tag clickable" style="--team-color:${escapeAttrValue(s.team_color || '#94a3b8')}" onclick="toggleTeamPicker(event, ${s.id})" data-student="${s.id}">${escapeHtmlValue(s.team_name)}</span>`
            : `<span class="team-tag unassigned-tag clickable" onclick="toggleTeamPicker(event, ${s.id})" data-student="${s.id}">Unassigned</span>`;
        const statusHtml = s.is_online
            ? '<span class="online-dot"></span> Online'
            : '<span class="offline-dot"></span> Offline';
        const loginTime = s.last_login_at ? s.last_login_at.substring(0, 19) : 'Not yet';
        const presentationCount = normalizedParticipationCount(s.presentation_count);
        const challengerCount = normalizedParticipationCount(s.challenger_count);
        const name = s.name || s.student_id;
        const actionCell = isDemoInstructor
            ? ''
            : `<td><button class="btn btn-sm btn-danger" onclick="removeStudent(${s.id}, this)">Remove</button></td>`;

        const tr = document.createElement('tr');
        tr.setAttribute('data-id', s.id);
        tr.innerHTML = `
            <td>${escapeHtmlValue(s.student_id)}</td>
            <td>${escapeHtmlValue(name)}</td>
            <td>${teamHtml}</td>
            <td class="participation-table-count">${presentationCount}</td>
            <td class="participation-table-count">${challengerCount}</td>
            <td>${statusHtml}</td>
            <td class="text-muted small">${escapeHtmlValue(loginTime)}</td>
            ${actionCell}
        `;
        tbody.appendChild(tr);
    });

    // Update sort arrows and announce the active order to assistive technology.
    document.querySelectorAll('#student-table [data-sort]').forEach(header => {
        header.setAttribute('aria-sort', 'none');
        const arrow = header.querySelector('.sort-arrow');
        if (arrow) arrow.textContent = '';
    });
    const activeColumn = document.querySelector(`#student-table [data-sort="${studentSort}"]`);
    if (activeColumn) {
        activeColumn.setAttribute('aria-sort', studentOrder === 'asc' ? 'ascending' : 'descending');
        const arrow = activeColumn.querySelector('.sort-arrow');
        if (arrow) arrow.textContent = studentOrder === 'asc' ? ' ▲' : ' ▼';
    }

    // Pagination
    const pag = document.getElementById('student-pagination');
    if (pag) {
        let html = `<span class="pag-info">${data.total} students</span>`;
        for (let p = 1; p <= data.total_pages; p++) {
            html += `<button class="btn btn-sm ${p === data.page ? 'btn-primary' : 'btn-outline'}" onclick="goToStudentPage(${p})">${p}</button>`;
        }
        pag.innerHTML = html;
    }
};

window.sortStudents = function(col) {
    if (studentSort === col) {
        studentOrder = studentOrder === 'asc' ? 'desc' : 'asc';
    } else {
        studentSort = col;
        studentOrder = 'asc';
    }
    studentPage = 1;
    loadStudentTable();
};

window.studentSortHeaderKeydown = function(event, col) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    window.sortStudents(col);
};

window.goToStudentPage = function(p) {
    studentPage = p;
    loadStudentTable();
};

// Load on page load if table exists
if (document.getElementById('student-tbody')) {
    initStudentPageSizeSelector();
    loadStudentTable();
}

window.toggleTeamPicker = function(e, studentId) {
    e.stopPropagation();
    document.querySelectorAll('.team-picker:not(.filter-picker)').forEach(el => el.remove());

    const tag = e.currentTarget;
    const rect = tag.getBoundingClientRect();
    const picker = document.createElement('div');
    picker.className = 'team-picker';

    const teams = window._lastTeams || [];
    let html = '<div class="team-picker-item" onclick="pickTeam(' + studentId + ', null)">Unassigned</div>';
    teams.forEach(t => {
        html += `<div class="team-picker-item" style="border-left:3px solid ${escapeAttrValue(t.color)}" onclick="pickTeam(${studentId}, ${t.id})">${escapeHtmlValue(t.name)}</div>`;
    });
    picker.innerHTML = html;
    picker.style.position = 'fixed';
    picker.style.left = rect.left + 'px';

    // Position: below or above based on available space
    const pickerHeight = Math.min((teams.length + 1) * 36, 260);
    if (rect.bottom + pickerHeight + 8 > window.innerHeight) {
        picker.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
    } else {
        picker.style.top = (rect.bottom + 4) + 'px';
    }

    document.body.appendChild(picker);

    setTimeout(() => {
        document.addEventListener('click', function close() {
            picker.remove();
            document.removeEventListener('click', close);
        }, { once: true });
    }, 0);
};

window.pickTeam = async function(studentId, teamId) {
    if (!beginRosterMutation()) return;
    document.querySelectorAll('.team-picker:not(.filter-picker)').forEach(
        el => el.remove()
    );
    try {
        const data = await postJSON('/api/assign_student', instructorStatePayload({
            student_id: studentId,
            team_id: teamId
        }));
        if (data && data.success) {
            applyRosterMutationVersion(data);
            showToast('Team assignment updated', 'success');
            refreshRosterInPlace();
        } else {
            showInstructorMutationError(data, 'Failed to assign student');
        }
    } finally {
        endRosterMutation();
    }
};

window.addStudent = async function(btn) {
    const id = document.getElementById('new-student-id').value.trim();
    const name = document.getElementById('new-name').value.trim();
    const pin = document.getElementById('new-pin').value.trim();
    if (!id || !pin) { showToast('Student ID and PIN are required', 'warning'); return; }
    if (!beginRosterMutation()) return;
    if (btn) btn.disabled = true;
    try {
        const data = await postJSON('/api/add_student', instructorStatePayload({
            student_id: id,
            name,
            pin
        }));
        if (data.success) {
            applyRosterMutationVersion(data);
            showToast(data.updated ? 'Student info updated' : 'Student added', 'success');
            document.getElementById('new-student-id').value = '';
            document.getElementById('new-name').value = '';
            document.getElementById('new-pin').value = '';
            if (!data.updated) {
                // New or reactivated student: keep the capacity summary's
                // total (read from this dataset key) correct until the next
                // full page load.
                instructor.dataset.studentCount =
                    String((Number(instructor.dataset.studentCount) || 0) + 1);
            }
            refreshRosterInPlace();
        } else {
            showInstructorMutationError(data, 'Failed to add student');
        }
    } finally {
        endRosterMutation();
        if (btn) btn.disabled = false;
    }
};

/* ===== DISCUSSION QUESTIONS (weekly .md files) ===== */

let discussionCurrentWeek = null;
let renderedDiscQuestionsVersion = 0;

// Populate setup week selector and discussion loader
async function initWeekSelector() {
    let data;
    try {
        const { res, data: questionData } = await fetchJSONWithTimeout(
            '/api/discussion_questions'
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (!res.ok) {
            const loadError = new Error('question load failed');
            loadError.serverMessage = (questionData && questionData.error) || null;
            throw loadError;
        }
        data = questionData;
        discussionCurrentWeek = Number(data.current_week) || null;
    } catch (error) {
        const serverMessage = error && error.serverMessage;
        const setupSel = document.getElementById('setup-week-select');
        if (setupSel) {
            setupSel.innerHTML = '<option value="" selected disabled>Could not load question weeks</option>';
            setupSel.disabled = true;
        }
        const container = document.getElementById('disc-questions-list');
        if (container) {
            container.innerHTML = `<p class="empty">${escapeHtmlValue(
                serverMessage || 'Could not load discussion questions. Please refresh.'
            )}</p>`;
        }
        if (serverMessage) showToast(serverMessage, 'error');
        return;
    }

    // Setup page week selector
    const setupSel = document.getElementById('setup-week-select');
    const weeks = data.weeks || [];
    if (setupSel) {
        if (weeks.length > 0) {
            setupSel.disabled = false;
            setupSel.innerHTML = weeks.map(w => {
                const status = w.ready ? '' : ' (not ready)';
                const selected = w.num === data.current_week ? 'selected' : '';
                const disabled = w.ready ? '' : 'disabled';
                const issue = Array.isArray(w.issues) && w.issues[0]
                    ? ` title="${escapeAttrValue(w.issues[0])}"`
                    : '';
                return `<option value="${w.num}" ${selected} ${disabled}${issue}>Week ${w.num}${status}</option>`;
            }).join('');
        } else {
            setupSel.innerHTML = '<option value="" selected disabled>No question weeks found</option>';
            setupSel.disabled = true;
        }
    }

    // Preview and Apply are separate actions. Keep this fallback for older
    // cached templates, but changing the select itself never commits a week.
    if (setupSel && weeks.length > 0) {
        let previewBtn = document.getElementById('btn-preview-week');
        if (!previewBtn) {
            previewBtn = document.createElement('button');
            previewBtn.type = 'button';
            previewBtn.id = 'btn-preview-week';
            previewBtn.className = 'btn btn-sm btn-secondary';
            previewBtn.textContent = 'Preview';
            previewBtn.onclick = previewDiscussionWeek;
            setupSel.insertAdjacentElement('afterend', previewBtn);
        }
        if (!document.getElementById('btn-apply-week')) {
            const applyBtn = document.createElement('button');
            applyBtn.type = 'button';
            applyBtn.id = 'btn-apply-week';
            applyBtn.className = 'btn btn-sm btn-primary';
            applyBtn.textContent = 'Apply Week';
            applyBtn.onclick = setDiscussionWeek;
            previewBtn.insertAdjacentElement('afterend', applyBtn);
        }
    }

    // Discussion page: auto-load questions for current week
    const container = document.getElementById('disc-questions-list');
    if (container) {
        renderDiscussionQuestions(data);
    }
}

/* ===== WEEK PREVIEW MODAL ===== */
// Shows a week's questions (fetched with ?week=N) without committing; the
// explicit "Use this week" action runs the normal setDiscussionWeek flow.
function closeWeekPreviewModal() {
    document.getElementById('week-preview-overlay')?.remove();
}
window.closeWeekPreviewModal = closeWeekPreviewModal;

window.usePreviewedWeek = function(week) {
    const select = document.getElementById('setup-week-select');
    if (select) select.value = String(week);
    closeWeekPreviewModal();
    setDiscussionWeek();
};

function showWeekPreviewModal(week, questions) {
    closeWeekPreviewModal();
    const overlay = document.createElement('div');
    overlay.id = 'week-preview-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);display:flex;align-items:center;justify-content:center;z-index:1000;padding:16px;';
    const panel = document.createElement('div');
    panel.className = 'card';
    panel.style.cssText = 'max-width:720px;width:100%;max-height:80vh;overflow-y:auto;margin:0;';
    const body = questions.length
        ? questions.map((q, i) => `
        <div class="disc-question-card">
            <div class="disc-question-header" role="button" tabindex="0" aria-expanded="false" onclick="toggleDiscQuestionCard(this)" onkeydown="discQuestionHeaderKeydown(event)">
                <span class="disc-question-num">#${discussionQuestionNumber(q, i)}</span>
                <span class="disc-question-title">${escapeHtmlValue(q.title || 'Untitled')}</span>
            </div>
            <div class="disc-question-body">
                <div class="markdown-content">${renderMarkdown(q.content || '')}</div>
            </div>
        </div>`).join('')
        : '<p class="empty">No questions found for this week.</p>';
    panel.innerHTML = `
        <h2>Week ${week} questions preview</h2>
        ${body}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px;">
            <button type="button" class="btn btn-secondary" onclick="closeWeekPreviewModal()">Cancel</button>
            <button type="button" class="btn btn-primary" onclick="usePreviewedWeek(${week})">Use this week</button>
        </div>`;
    overlay.appendChild(panel);
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeWeekPreviewModal();
    });
    document.body.appendChild(overlay);
    safeTypeset(panel);
    safeHighlight(panel);
}

window.previewDiscussionWeek = async function() {
    const select = document.getElementById('setup-week-select');
    const week = select?.value;
    if (!week) { showToast('Select a week to preview', 'warning'); return; }
    const previewButton = document.getElementById('btn-preview-week');
    if (previewButton) previewButton.disabled = true;
    try {
        const { res, data } = await fetchJSONWithTimeout(
            '/api/discussion_questions?week=' + encodeURIComponent(week)
        );
        if (res.status === 401) {
            redirectToSessionEnded();
            return;
        }
        if (!res.ok) {
            showToast(
                (data && data.error) || 'Could not load the week preview',
                'error'
            );
            return;
        }
        showWeekPreviewModal(parseInt(week, 10), data.questions || []);
    } catch (error) {
        showToast(
            'Could not load the week preview. Check your connection and try again.',
            'error'
        );
    } finally {
        if (previewButton) previewButton.disabled = false;
    }
};

window.setDiscussionWeek = async function() {
    const select = document.getElementById('setup-week-select');
    const week = select?.value;
    if (!week) return;
    const applyButton = document.getElementById('btn-apply-week');
    const previewButton = document.getElementById('btn-preview-week');
    select.disabled = true;
    if (applyButton) applyButton.disabled = true;
    if (previewButton) previewButton.disabled = true;
    const data = await postJSON('/api/set_discussion_week', instructorStatePayload({
        week: parseInt(week)
    }));
    if (data && data.success) {
        const count = Number(data.question_count);
        const detail = Number.isFinite(count)
            ? ` with ${count} questions` : '';
        showToast(
            `Week ${week} selected${detail} for Discussion and Presentation`,
            'success'
        );
        window.setTimeout(() => window.location.reload(), 250);
    } else {
        select.value = instructor?.dataset?.discussionWeek || '';
        select.disabled = false;
        if (applyButton) applyButton.disabled = false;
        if (previewButton) previewButton.disabled = false;
        showInstructorMutationError(data, 'Failed to select discussion week');
    }
};

// Load on page load
if (document.getElementById('setup-week-select') || document.getElementById('disc-questions-list')) {
    initWeekSelector();
}

window.startPresentation = async function() {
    if (_instructorActionInFlight) return;
    const teamId = document.getElementById('comp-team').value;
    const questionId = document.getElementById('comp-question').value;
    const timeCap = parseInt(document.getElementById('time-cap')?.value || '300', 10);
    if (!teamId || !questionId) { showToast('Select both a team and a question', 'warning'); return; }
    _instructorActionInFlight = true;
    try {
        const data = await postJSON('/api/start_presentation', {
            ...instructorStatePayload(),
            team_id: parseInt(teamId, 10),
            question_id: parseInt(questionId, 10),
            time_cap: timeCap
        });
        if (data.success) {
            if (data.notice) showToast(data.notice, 'warning');
            // The poll swaps in the active-presentation block; reload only
            // when no poll loop is running to reconcile the card.
            if (typeof window.instructorPollOnce === 'function') {
                window.instructorPollOnce();
            } else {
                window.location.reload();
            }
        } else {
            showInstructorMutationError(data, 'Failed to start presentation');
        }
    } finally {
        _instructorActionInFlight = false;
    }
};

window.stopPresentation = async function() {
    const data = await postJSON('/api/stop_presentation', instructorStatePayload());
    if (data.success) {
        const btnPause = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnPause) btnPause.style.display = 'none';
        if (btnResume) btnResume.style.display = data.remaining > 0 ? '' : 'none';
        stopCountdownTimer();
        const tv = document.getElementById('timer-value');
        if (tv && data.remaining != null) tv.textContent = formatDuration(data.remaining);
        setTimerExpired(Number(data.remaining) <= 0);
    } else {
        showInstructorMutationError(data, 'Failed to pause presentation');
    }
};

window.resumePresentation = async function() {
    const data = await postJSON('/api/resume_presentation', instructorStatePayload());
    if (data.success) {
        const btnPause = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnPause) btnPause.style.display = '';
        if (btnResume) btnResume.style.display = 'none';
        // Start countdown locally — no page reload needed
        startCountdownTimer(data.remaining);
    } else {
        showInstructorMutationError(data, 'Failed to resume presentation');
    }
};

window.resetTimer = async function() {
    if (!confirm('Reset timer to full time?')) return;
    const data = await postJSON('/api/reset_presentation_timer', instructorStatePayload());
    if (data.success) {
        const btnPause = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnPause) btnPause.style.display = 'none';
        if (btnResume) btnResume.style.display = '';
        stopCountdownTimer();
        const tv = document.getElementById('timer-value');
        if (tv) tv.textContent = formatDuration(data.cap);
        setTimerExpired(false);
    } else {
        showInstructorMutationError(data, 'Failed to reset timer');
    }
};

window.nextPresentation = async function() {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    if (!confirm(
            'Finish this presentation? The team presentation turn and any ' +
            'challenger turns will be permanently recorded. Use Cancel ' +
            'Mistake first if anything is incorrect.')) return;

    _instructorActionInFlight = true;
    try {
        const data = await postJSON('/api/next_presentation', instructorStatePayload());
        if (data.success) {
            await reconcileCompetitionParticipationCounts();
            if (typeof window.instructorPollOnce === 'function') {
                window.instructorPollOnce();
            } else {
                window.location.reload();
            }
        } else {
            showInstructorMutationError(data, 'Failed to finish presentation');
        }
    } finally {
        _instructorActionInFlight = false;
    }
};

window.cancelPresentation = async function() {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    if (!confirm('Cancel this mistaken presentation without adding it to history?')) return;
    _instructorActionInFlight = true;
    try {
        let data = await postJSON('/api/cancel_presentation', instructorStatePayload());
        if (data.requires_discard) {
            const hasDetailedCounts =
                data.presentation_rating_count != null ||
                data.challenge_rating_count != null;
            const presentationCount = hasDetailedCounts
                ? (Number(data.presentation_rating_count) || 0)
                : (Number(data.rating_count) || 0);
            const challengeCount =
                Number(data.challenge_rating_count) || 0;
            const details = [];
            if (presentationCount > 0) {
                details.push(
                    presentationCount + ' presentation rating' +
                    (presentationCount === 1 ? '' : 's')
                );
            }
            if (challengeCount > 0) {
                details.push(
                    challengeCount + ' challenge rating' +
                    (challengeCount === 1 ? '' : 's')
                );
            }
            const description = details.length
                ? details.join(' and ') : 'the submitted ratings';
            if (!confirm(
                    'This will permanently discard ' + description +
                    '. Continue?')) return;
            data = await postJSON(
                '/api/cancel_presentation',
                instructorStatePayload({ discard_ratings: true })
            );
        }
        if (data.success) {
            await reconcileCompetitionParticipationCounts();
            if (typeof window.instructorPollOnce === 'function') {
                window.instructorPollOnce();
            } else {
                window.location.reload();
            }
        } else {
            showInstructorMutationError(data, 'Failed to cancel presentation');
        }
    } finally {
        _instructorActionInFlight = false;
    }
};

// ===== POLL GLIDE (instructor) / POLL PULSE (student) =====

let pollGlideTimeout = null;
let pollGlideAnchor = null;

/**
 * Instructor: animate a left→right fill across the Start Poll button for the
 * poll duration. The server supplies remaining seconds; performance.now()
 * only animates between state updates.
 */
function startPollGlide(remainingSec, durationSec) {
    const btn = document.getElementById('btn-start-poll');
    if (!btn) return;
    const glide = btn.querySelector('.btn-glide');
    const label = btn.querySelector('.btn-label');
    if (!glide) return;
    const duration = Math.max(1, Number(durationSec) || 40);
    pollGlideAnchor = {
        remaining: Math.max(0, Number(remainingSec) || 0),
        duration,
        at: performance.now()
    };

    btn.classList.add('gliding');
    btn.disabled = true;
    const nextButton = document.getElementById('btn-next');
    if (nextButton) nextButton.disabled = true;
    if (label) label.textContent = '⏳ Poll Active';

    function step() {
        if (!pollGlideAnchor) return;
        const elapsed = (performance.now() - pollGlideAnchor.at) / 1000;
        const remaining = Math.max(0, pollGlideAnchor.remaining - elapsed);
        const pct = Math.min(
            100,
            ((pollGlideAnchor.duration - remaining) / pollGlideAnchor.duration) * 100
        );
        glide.style.width = pct + '%';

        if (label) label.textContent = `⏳ ${Math.ceil(remaining)}s`;

        if (remaining <= 0) {
            btn.classList.remove('gliding');
            btn.disabled = ratingsSettling;
            const nextButton = document.getElementById('btn-next');
            if (nextButton) nextButton.disabled = ratingsSettling;
            glide.style.width = '0%';
            if (label) label.textContent = '⭐ Start Poll';
            if (pollGlideTimeout) { clearTimeout(pollGlideTimeout); pollGlideTimeout = null; }
            pollGlideAnchor = null;
            applyRatingsSettlingState({
                ratings_settling: true,
                ratings_settling_remaining: 3
            });
            window.instructorPollOnce?.();
            return;
        }
        pollGlideTimeout = setTimeout(step, 200);
    }
    if (pollGlideTimeout === null) step();
}

function stopPollGlide() {
    const btn = document.getElementById('btn-start-poll');
    if (!btn) return;
    const glide = btn.querySelector('.btn-glide');
    const label = btn.querySelector('.btn-label');
    btn.classList.remove('gliding');
    btn.disabled = ratingsSettling;
    const nextButton = document.getElementById('btn-next');
    if (nextButton) nextButton.disabled = ratingsSettling;
    if (glide) glide.style.width = '0%';
    if (label) label.textContent = '⭐ Start Poll';
    if (pollGlideTimeout) { clearTimeout(pollGlideTimeout); pollGlideTimeout = null; }
    pollGlideAnchor = null;
}

/**
 * Student: pulsing countdown badge on the grading card during the configured poll.
 */
let pollPulseInterval = null;
let pollPulseAnchor = null;
// In the final seconds of the rating window, escalate urgency so an unsaved
// rating isn't silently lost.
const POLL_URGENT_THRESHOLD = 8;

function startPollPulse(remainingSec) {
    const pulse = document.getElementById('poll-pulse');
    const pulseText = document.getElementById('poll-pulse-text');
    if (!pulse) return;

    pollPulseAnchor = {
        remaining: Math.max(0, Number(remainingSec) || 0),
        at: performance.now()
    };
    pulse.style.display = 'flex';

    function tick() {
        if (!pollPulseAnchor) return;
        const elapsed = (performance.now() - pollPulseAnchor.at) / 1000;
        const remaining = Math.max(0, pollPulseAnchor.remaining - elapsed);
        const urgent = remaining > 0 && remaining <= POLL_URGENT_THRESHOLD;
        pulse.classList.toggle('urgent', urgent);
        if (pulseText) {
            pulseText.textContent = urgent
                ? `⏱ ${Math.ceil(remaining)}s — submit now!`
                : `⏱ ${Math.ceil(remaining)}s left to rate!`;
        }
        // Nudge the Submit button when an unsaved rating is at risk.
        const submitBtn = document.getElementById('btn-submit-rating');
        if (submitBtn) submitBtn.classList.toggle('attention', urgent && ratingDraftDirty);

        if (remaining <= 0) {
            clearInterval(pollPulseInterval);
            pollPulseInterval = null;
            pollPulseAnchor = null;
            pulse.style.display = 'none';
            pulse.classList.remove('urgent');
            if (submitBtn) submitBtn.classList.remove('attention');
        }
    }
    tick();
    if (pollPulseAnchor && pollPulseInterval === null) {
        pollPulseInterval = setInterval(tick, 250);
    }
}

function stopPollPulse() {
    const pulse = document.getElementById('poll-pulse');
    if (pulse) {
        pulse.style.display = 'none';
        pulse.classList.remove('urgent');
    }
    if (pollPulseInterval) { clearInterval(pollPulseInterval); pollPulseInterval = null; }
    pollPulseAnchor = null;
    const submitBtn = document.getElementById('btn-submit-rating');
    if (submitBtn) submitBtn.classList.remove('attention');
}

// ===== POLL API =====

window.startPoll = async function() {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    const button = document.getElementById('btn-start-poll');
    if (button?.classList.contains('gliding')) return;
    _instructorActionInFlight = true;
    try {
        const data = await postJSON(
            '/api/start_poll',
            instructorStatePayload()
        );
        if (data.success && data.poll_started_at) {
            startPollGlide(
                data.poll_remaining,
                data.poll_duration || 40
            );
            const status = document.getElementById('poll-status');
            if (status) status.style.display = 'flex';
        } else {
            showInstructorMutationError(data, 'Failed to start poll');
        }
    } finally {
        _instructorActionInFlight = false;
    }
};

window.stopPoll = async function() {
    if (_instructorActionInFlight || ratingsSettlementBlocksAction()) return;
    _instructorActionInFlight = true;
    try {
        const data = await postJSON(
            '/api/stop_poll',
            instructorStatePayload()
        );
        if (data.success) {
            applyRatingsSettlingState(data);
            stopPollGlide();
            window.instructorPollOnce?.();
        } else {
            showInstructorMutationError(data, 'Failed to stop poll');
        }
    } finally {
        _instructorActionInFlight = false;
    }
};
window.submitRating = async function() {
    if (ratingSubmissionInProgress) return;
    if (!activePollOpen || !lastRatedPresentationKey) {
        showToast('The rating window is closed', 'warning');
        return;
    }
    if (!pollSelections.q1 || !pollSelections.q2) {
        showToast('Please rate both questions', 'warning');
        return;
    }
    const selectionSnapshot = {
        q1: pollSelections.q1,
        q2: pollSelections.q2
    };
    const presentationKey = lastRatedPresentationKey;
    const btn = document.getElementById('btn-submit-rating');
    const retryingUnconfirmed = Boolean(
        unconfirmedPresentationRating &&
        unconfirmedPresentationRating.presentationKey === presentationKey);
    ++_responseRequestId;
    ratingSubmissionInProgress = true;
    beginStudentVote();
    updateRatingControlAvailability();
    if (btn) btn.textContent = 'Submitting...';

    let success = false;
    let unconfirmed = false;
    let failureMessage = 'Vote not submitted. Please try again.';
    try {
        const data = await postJSON('/api/submit_rating', {
            q1_developed: selectionSnapshot.q1,
            q2_easy: selectionSnapshot.q2,
            presentation_key: presentationKey
        }, VOTE_RETRY_OPTIONS);
        success = data?.success === true;
        ++_responseRequestId;
        if (success) {
            unconfirmedPresentationRating = null;
            if (retryingUnconfirmed &&
                    _unconfirmedChallengeRatings.size === 0 &&
                    _unconfirmedThumbVotes.size === 0) {
                _studentVoteBatchFailure = null;
                _studentVoteKnownFailure = false;
            }
            showToast('Rating submitted', 'success');
            if (presentationKey === lastRatedPresentationKey) {
                savedPollSelections = { ...selectionSnapshot };
                ratingDraftDirty = false;
                showCurrentRating(selectionSnapshot);
            }
        } else {
            responseNeedsRefresh = true;
            if (data?._network_error) {
                unconfirmed = true;
                unconfirmedPresentationRating = {
                    presentationKey,
                    q1: selectionSnapshot.q1,
                    q2: selectionSnapshot.q2
                };
                failureMessage =
                    'Could not confirm your vote. Rechecking the saved response...';
            } else {
                failureMessage = data?.error || failureMessage;
            }
            showRatingNotSaved();
            showToast(failureMessage, 'error');
        }
    } catch (error) {
        ++_responseRequestId;
        responseNeedsRefresh = true;
        showRatingNotSaved();
        showToast(failureMessage, 'error');
    } finally {
        ratingSubmissionInProgress = false;
        responseNeedsRefresh = responseNeedsRefresh ||
            Boolean(unconfirmedPresentationRating) ||
            _unconfirmedChallengeRatings.size > 0 ||
            _unconfirmedThumbVotes.size > 0;
        finishStudentVote(
            success,
            'Vote submitted',
            failureMessage,
            unconfirmed
        );
        updateRatingControlAvailability();
        if (btn) btn.textContent = 'Submit Rating';
        if (!completePendingDashboardReload() && responseNeedsRefresh) {
            window.requestDashboardPoll?.();
        }
    }
};
function showCurrentRating(values) {
    const status = document.getElementById('rating-status');
    if (!status) return;
    status.textContent = `Your current rating: ${values.q1}/5 developed · ${values.q2}/5 clear`;
    status.style.color = 'var(--success-text)';
    status.style.display = 'block';
}

function showRatingDraftStatus() {
    const status = document.getElementById('rating-status');
    if (!status || status.dataset.windowClosed === '1') return;
    status.textContent = 'Changes not submitted.';
    status.style.color = 'var(--warning-text)';
    status.style.display = 'block';
}

function showRatingWindowClosed() {
    const status = document.getElementById('rating-status');
    if (!status) return;
    status.textContent = 'Rating window closed — your rating was not saved.';
    status.style.color = 'var(--error-text)';
    status.style.display = 'block';
    status.dataset.windowClosed = '1';
}

function hideRatingWindowClosed() {
    const status = document.getElementById('rating-status');
    if (!status || status.dataset.windowClosed !== '1') return;
    delete status.dataset.windowClosed;
    status.style.display = 'none';
}

function showRatingNotSaved() {
    const status = document.getElementById('rating-status');
    if (!status) return;
    status.textContent = 'Rating not saved. Rechecking the saved response...';
    status.style.color = 'var(--error-text)';
    status.style.display = 'block';
}

function updateRatingControlAvailability() {
    const disabled = !activePollOpen || ratingSubmissionInProgress;
    document.querySelectorAll('#poll-section .star-row .star').forEach(star => {
        star.disabled = disabled;
    });
    const button = document.getElementById('btn-submit-rating');
    if (button) button.disabled = disabled;
}

function setStarRowValue(question, value) {
    const row = document.querySelector(`.star-row[data-question="${question}"]`);
    if (!row) return;
    row.querySelectorAll('.star').forEach(star => {
        const starValue = parseInt(star.dataset.value, 10);
        star.classList.toggle('active', starValue <= value);
        star.textContent = starValue <= value ? '★' : '☆';
        star.setAttribute('aria-pressed', starValue === value ? 'true' : 'false');
    });
}

// Star interaction
function initStarRows() {
    document.querySelectorAll('.star-row').forEach(row => {
        const question = row.dataset.question;
        const stars = row.querySelectorAll('.star');
        stars.forEach(star => {
            star.addEventListener('click', () => {
                if (star.disabled) return;
                ++_responseRequestId;
                responseNeedsRefresh = true;
                const val = parseInt(star.dataset.value, 10);
                pollSelections[question] = val;
                setStarRowValue(question, val);
                ratingDraftDirty = !savedPollSelections ||
                    pollSelections.q1 !== savedPollSelections.q1 ||
                    pollSelections.q2 !== savedPollSelections.q2;
                if (!ratingDraftDirty && savedPollSelections) {
                    showCurrentRating(savedPollSelections);
                } else {
                    showRatingDraftStatus();
                }
            });
        });
    });
}

// ===== UNASSIGN ALL =====

window.unassignAll = async function(btn) {
    if (!confirm('Clear all team assignments?')) return;
    if (!beginRosterMutation()) return;
    if (btn) btn.disabled = true;
    try {
        const data = await postJSON('/api/unassign_all', instructorStatePayload());
        if (data.success) {
            applyRosterMutationVersion(data);
            showToast('All team assignments cleared', 'warning');
            refreshRosterInPlace();
        } else {
            showInstructorMutationError(data, 'Failed to clear team assignments');
        }
    } finally {
        endRosterMutation();
        if (btn) btn.disabled = false;
    }
};

// ===== APPENDIX QUESTIONS =====

let appendixMutationInProgress = false;
let editingAppendixId = null;
let restoredAppendixEditId = null;
const appendixQuestionsById = new Map();

function displayedAppendixWeek() {
    const week = Number(
        discussionCurrentWeek || instructor?.dataset?.discussionWeek || 0
    );
    return Number.isInteger(week) && week > 0 ? week : null;
}

function beginAppendixMutation(button, pendingLabel) {
    if (appendixMutationInProgress) return false;
    appendixMutationInProgress = true;
    if (button) {
        button.dataset.idleLabel = button.textContent;
        button.disabled = true;
        button.textContent = pendingLabel;
    }
    return true;
}

function endAppendixMutation(button) {
    appendixMutationInProgress = false;
    if (button) {
        button.disabled = false;
        button.textContent = button.dataset.idleLabel || button.textContent;
        delete button.dataset.idleLabel;
    }
}

// Add or update one appendix option in the server-rendered competition
// question select. New questions always receive the highest A-label, and
// appendix options sort after the bank questions, so appending matches the
// server-rendered order. Returns false when the response cannot be
// reconciled with the select, so the caller can fall back to a reload.
function upsertCompQuestionOption(data) {
    const select = document.getElementById('comp-question');
    const questionId = Number(data?.question_id);
    if (!select || !questionId) return false;
    const title = String(data?.title || data?.label || data?.appendix_id || '');
    const truncated = title.length > 60 ? `${title.slice(0, 60)}...` : title;
    const text = `Appendix — ${truncated}`;
    const existing = select.querySelector(`option[value="${questionId}"]`);
    if (existing) {
        const presented = existing.textContent.endsWith(' — previously presented');
        existing.textContent = presented ? `${text} — previously presented` : text;
        return true;
    }
    const placeholder = select.querySelector('option[value=""][disabled]');
    if (placeholder) placeholder.remove();
    const option = document.createElement('option');
    option.value = String(questionId);
    option.textContent = text;
    select.appendChild(option);
    return true;
}

window.addAppendixQuestion = async function(button) {
    const title = document.getElementById('appendix-title')?.value.trim();
    const content = document.getElementById('appendix-content')?.value.trim();
    if (!title || !content) { showToast('Title and content required', 'warning'); return; }
    const week = displayedAppendixWeek();
    if (!week) { showToast('Could not determine the displayed week', 'error'); return; }
    const editingId = editingAppendixId;
    if (!beginAppendixMutation(button, editingId ? 'Saving...' : 'Adding...')) return;
    let keepPending = false;
    try {
        const data = await postJSON(
            editingId ? '/api/edit_appendix_question' : '/api/questions',
            instructorStatePayload(editingId
                ? { appendix_id: editingId, title, content, week }
                : { title, content, week })
        );
        if (data && data.success) {
            document.getElementById('appendix-title').value = '';
            document.getElementById('appendix-content').value = '';
            showToast(editingId
                ? `${data.appendix_id || editingId} updated`
                : `${data.label || 'Appendix question'} added`, 'success');
            resetAppendixFormMode();
            clearAppendixDraft();
            if (document.getElementById('comp-question')) {
                // Competition phase: add/update the presentation question
                // option in place. Reload stays as the fallback when the
                // response cannot be reconciled with the select.
                if (!upsertCompQuestionOption(data)) {
                    keepPending = true;
                    window.setTimeout(() => window.location.reload(), 250);
                }
            } else {
                await initWeekSelector();
            }
        } else {
            showInstructorMutationError(
                data,
                editingId ? 'Failed to edit question' : 'Failed to add question'
            );
        }
    } finally {
        if (!keepPending) endAppendixMutation(button);
    }
};

function resetAppendixFormMode() {
    editingAppendixId = null;
    restoredAppendixEditId = null;
    const addButton = document.querySelector('.appendix-add-btn');
    if (addButton) {
        delete addButton.dataset.idleLabel;
        addButton.textContent = 'Add to Appendix';
    }
    const cancelButton = document.querySelector('.appendix-cancel-edit-btn');
    if (cancelButton) cancelButton.style.display = 'none';
}

window.editAppendixQuestion = function(button) {
    const appendixId = button?.dataset?.appendixId;
    const question = appendixId ? appendixQuestionsById.get(appendixId) : null;
    const titleInput = document.getElementById('appendix-title');
    const contentInput = document.getElementById('appendix-content');
    const addButton = document.querySelector('.appendix-add-btn');
    if (!question || !titleInput || !contentInput || !addButton) return;
    restoredAppendixEditId = null;
    editingAppendixId = appendixId;
    titleInput.value = question.title.replace(/^A\d+:\s*/, '');
    contentInput.value = question.content;
    addButton.textContent = `Save ${appendixId}`;
    const cancelButton = document.querySelector('.appendix-cancel-edit-btn');
    if (cancelButton) cancelButton.style.display = '';
    titleInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
    titleInput.focus();
};

window.cancelAppendixEdit = function() {
    document.getElementById('appendix-title').value = '';
    document.getElementById('appendix-content').value = '';
    resetAppendixFormMode();
    clearAppendixDraft();
};

/* ===== APPENDIX DRAFT PRESERVATION =====
   Structural reloads (phase transitions, another instructor tab mutating
   state) rebuild the page server-side and would wipe a half-written
   appendix question. Stash the form on every input — same sessionStorage
   pattern as 'popping-flash-message' — and restore it after the reload. */
function appendixDraftStorageKey() {
    return `popping-appendix-draft:${instructor?.dataset?.slug || ''}`;
}

function stashAppendixDraft() {
    const titleInput = document.getElementById('appendix-title');
    const contentInput = document.getElementById('appendix-content');
    if (!titleInput || !contentInput) return;
    const key = appendixDraftStorageKey();
    try {
        if (!titleInput.value && !contentInput.value) {
            sessionStorage.removeItem(key);
            return;
        }
        sessionStorage.setItem(key, JSON.stringify({
            week: Number(instructor?.dataset?.discussionWeek) || null,
            title: titleInput.value,
            content: contentInput.value,
            editingId: editingAppendixId
        }));
    } catch (e) {
        // sessionStorage unavailable (e.g. some private modes): the draft
        // simply isn't kept — never break typing over it.
    }
}

function clearAppendixDraft() {
    try {
        sessionStorage.removeItem(appendixDraftStorageKey());
    } catch (e) { /* ignore */ }
}

function setRestoredAppendixEditMode(appendixId) {
    editingAppendixId = appendixId;
    const addButton = document.querySelector('.appendix-add-btn');
    if (addButton) addButton.textContent = `Save ${appendixId}`;
    const cancelButton = document.querySelector('.appendix-cancel-edit-btn');
    if (cancelButton) cancelButton.style.display = '';
}

function restoreAppendixDraftAsAdd(message) {
    resetAppendixFormMode();
    // Rewrite the restored draft immediately so a later reload cannot
    // resurrect the stale target again. The typed text stays untouched.
    stashAppendixDraft();
    showToast(message, 'warning');
}

function resolveRestoredAppendixEditMode() {
    if (restoredAppendixEditId) {
        const appendixId = restoredAppendixEditId;
        restoredAppendixEditId = null;
        if (instructor?.dataset?.phase === 'discussion' &&
                appendixQuestionsById.has(appendixId)) {
            setRestoredAppendixEditMode(appendixId);
            stashAppendixDraft();
            showToast(`Restored your unsaved edit for ${appendixId}`, 'warning');
        } else {
            restoreAppendixDraftAsAdd(
                'The original question is unavailable. Draft restored as a new question.'
            );
        }
        return;
    }
    // A second instructor can delete a question while this page is open.
    // Preserve this page's text, but stop offering an edit that must fail.
    if (editingAppendixId && !appendixQuestionsById.has(editingAppendixId)) {
        restoreAppendixDraftAsAdd(
            'The original question was removed. Draft kept as a new question.'
        );
    }
}

function restoreAppendixDraft() {
    const titleInput = document.getElementById('appendix-title');
    const contentInput = document.getElementById('appendix-content');
    if (!titleInput || !contentInput || titleInput.value || contentInput.value) return;
    let draft = null;
    try {
        draft = JSON.parse(sessionStorage.getItem(appendixDraftStorageKey()) || 'null');
    } catch (e) {
        draft = null;
    }
    if (!draft || (!draft.title && !draft.content)) return;
    // Only restore into the week the draft was written for.
    const currentWeek = Number(instructor?.dataset?.discussionWeek) || null;
    if (draft.week && currentWeek && draft.week !== currentWeek) return;
    titleInput.value = draft.title || '';
    contentInput.value = draft.content || '';
    if (draft.editingId) {
        if (instructor?.dataset?.phase === 'discussion') {
            // The question map is populated asynchronously. Do not expose a
            // Save action until the current list confirms that the target
            // still exists.
            restoredAppendixEditId = String(draft.editingId);
            return;
        }
        restoreAppendixDraftAsAdd(
            'Editing is unavailable in this phase. Draft restored as a new question.'
        );
        return;
    }
    showToast('Restored your unsaved appendix draft', 'warning');
}

['appendix-title', 'appendix-content'].forEach(id => {
    document.getElementById(id)?.addEventListener('input', stashAppendixDraft);
});
restoreAppendixDraft();

window.deleteAppendixQuestion = async function(button) {
    const appendixId = button?.dataset?.appendixId;
    const week = displayedAppendixWeek();
    if (!appendixId || !week || appendixMutationInProgress) return;
    if (!confirm('Delete this appendix question?')) return;
    if (!beginAppendixMutation(button, 'Deleting...')) return;
    try {
        const data = await postJSON(
            '/api/delete_appendix_question',
            instructorStatePayload({ appendix_id: appendixId, week })
        );
        if (data && data.success) {
            showToast('Question deleted');
            await initWeekSelector();
        } else {
            showInstructorMutationError(data, 'Failed to delete question');
        }
    } finally {
        endAppendixMutation(button);
    }
};

window.removeStudent = async function(studentDbId, btn) {
    if (!confirm('Remove this student?')) return;
    if (!beginRosterMutation()) return;
    if (btn) btn.disabled = true;
    try {
        const data = await deleteJSON(
            '/api/remove_student/' + studentDbId,
            instructorStatePayload()
        );
        if (data.success) {
            applyRosterMutationVersion(data);
            showToast('Student removed');
            instructor.dataset.studentCount = String(Math.max(
                0,
                (Number(instructor.dataset.studentCount) || 0) - 1
            ));
            refreshRosterInPlace();
        } else {
            showInstructorMutationError(data, 'Failed to remove student');
        }
    } finally {
        endRosterMutation();
        if (btn) btn.disabled = false;
    }
};

window.resetData = async function() {
    if (!confirm('This will delete ALL grades and reset teams for this course. Are you sure?')) return;
    const slug = instructor?.dataset?.slug || '';
    const typed = prompt(`Type ${slug} to confirm this permanent reset:`);
    if (typed !== slug) {
        if (typed !== null) showToast('Reset cancelled: course slug did not match', 'warning');
        return;
    }
    const data = await postJSON('/api/reset_data', instructorStatePayload({
        confirm_slug: slug
    }));
    if (data.success) {
        clearSessionTimerStorage();
        sessionStorage.setItem(
            'popping-flash-message',
            `Reset complete. Backup: ${data.backup || 'created'}. The three most recent reset backups are retained.`
        );
        window.location.reload();
    } else {
        showInstructorMutationError(data, 'Reset failed');
    }
};

// ===== PRESENTATION TIMER =====
let timerInterval = null;

function formatDuration(seconds) {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
}

let countdownTimerInterval = null;
let countdownTimerAnchor = null;

function startCountdownTimer(remainingSeconds) {
    countdownTimerAnchor = {
        remaining: Math.max(0, Number(remainingSeconds) || 0),
        at: performance.now()
    };
    const tick = () => {
        if (!countdownTimerAnchor) return;
        const elapsed = Math.floor(
            (performance.now() - countdownTimerAnchor.at) / 1000
        );
        const remaining = Math.max(0, countdownTimerAnchor.remaining - elapsed);
        document.querySelectorAll('.timer-value').forEach(el => {
            el.textContent = formatDuration(remaining);
        });
        setTimerExpired(remaining <= 0);
        if (remaining <= 0) {
            clearInterval(countdownTimerInterval);
            countdownTimerInterval = null;
            countdownTimerAnchor = null;
        }
    };
    tick();
    if (countdownTimerAnchor && countdownTimerInterval === null) {
        countdownTimerInterval = setInterval(tick, 250);
    }
}

function stopCountdownTimer() {
    if (countdownTimerInterval) {
        clearInterval(countdownTimerInterval);
        countdownTimerInterval = null;
    }
    countdownTimerAnchor = null;
}

function setTimerExpired(expired) {
    // Time up while a rating poll is still open is not an alarm state: show
    // a calmer "time's up — rating open" style instead of the red pulse.
    const ratingOpen = Boolean(expired) && ratingPollOpenForTimer;
    document.querySelectorAll('.timer-value').forEach(el => {
        el.classList.toggle('timer-expired', Boolean(expired) && !ratingOpen);
        el.classList.toggle('timer-time-up', ratingOpen);
    });
    const timerBox = document.getElementById('timer-box');
    if (timerBox) {
        timerBox.classList.toggle('timer-expired', Boolean(expired) && !ratingOpen);
        timerBox.classList.toggle('timer-time-up', ratingOpen);
    }
    const studentTimer = document.getElementById('student-timer');
    if (studentTimer) {
        studentTimer.classList.toggle('timer-expired', Boolean(expired) && !ratingOpen);
        studentTimer.classList.toggle('timer-time-up', ratingOpen);
    }
}

function stopTimer() {
    stopCountdownTimer();
    document.querySelectorAll('.timer-value').forEach(el => {
        el.textContent = '--:--';
    });
    setTimerExpired(false);
}

// Check for active timers on page load
const instructorEl = document.querySelector('.instructor[data-slug]');
if (instructorEl) {
    // Presentation countdown timer
    const presStarted = instructorEl.dataset.presentationStarted;
    const presRemaining = instructorEl.dataset.presentationRemaining;
    if (presStarted) {
        startCountdownTimer(Number(presRemaining) || 0);
        const btnStop = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnStop) btnStop.style.display = '';
        if (btnResume) btnResume.style.display = 'none';
    } else if (presRemaining !== '') {
        const remaining = parseInt(presRemaining, 10);
        document.getElementById('timer-value').textContent = formatDuration(remaining);
        setTimerExpired(remaining <= 0);
        const btnStop = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnStop) btnStop.style.display = 'none';
        if (btnResume) btnResume.style.display = remaining > 0 ? '' : 'none';
    }
    // Session timer
    clearSessionTimerStorage();
    applySessionTimerState(
        instructorEl.dataset.sessionStarted || null,
        instructorEl.dataset.sessionElapsed
    );
    // Poll status — restore gliding fill if poll is active on page load
    const pollStatus = document.getElementById('poll-status');
    const pollStarted = instructorEl.dataset.pollStarted;
    const pollActive = instructorEl.dataset.pollActive === '1';
    const pollRemaining = Number(instructorEl.dataset.pollRemaining) || 0;
    const POLL_DURATION = parseInt(instructorEl.dataset.pollDuration || '40', 10);
    if (pollActive && pollStarted && pollRemaining > 0) {
        startPollGlide(pollRemaining, POLL_DURATION);
        if (pollStatus) pollStatus.style.display = 'flex';
    } else {
        stopPollGlide();
        if (pollStatus) pollStatus.style.display = 'none';
    }
    applyRatingsSettlingState({
        ratings_settling:
            instructorEl.dataset.ratingsSettling === '1',
        ratings_settling_remaining:
            Number(instructorEl.dataset.ratingsSettlingRemaining) || 0
    });
    // History
    renderHistory();
}

function startSessionTimer(elapsedSeconds) {
    const anchor = {
        elapsed: Math.max(0, Number(elapsedSeconds) || 0),
        at: performance.now()
    };
    if (sessionTimerInterval) clearInterval(sessionTimerInterval);
    const tick = () => {
        const elapsed = anchor.elapsed + Math.floor(
            (performance.now() - anchor.at) / 1000
        );
        const el = document.getElementById('session-timer-value');
        if (el) el.textContent = formatDuration(elapsed);
    };
    tick();
    sessionTimerInterval = setInterval(tick, 1000);
}

function applySessionTimerState(startedAt, elapsedSeconds) {
    const timer = document.getElementById('session-timer');
    const button = document.getElementById('btn-session-timer');
    const canShow = instructor && ['setup', 'discussion'].includes(instructor.dataset.phase);
    if (startedAt && canShow) {
        sessionTimerStartedAt = startedAt;
        startSessionTimer(elapsedSeconds);
        if (timer) timer.style.display = 'flex';
        if (button) {
            button.textContent = 'Stop Timer';
            button.classList.remove('btn-primary');
            button.classList.add('btn-danger');
        }
    } else {
        sessionTimerStartedAt = null;
        if (sessionTimerInterval) {
            clearInterval(sessionTimerInterval);
            sessionTimerInterval = null;
        }
        const value = document.getElementById('session-timer-value');
        if (value) value.textContent = '00:00';
        if (timer) timer.style.display = 'none';
        if (button) {
            button.textContent = 'Start Timer';
            button.classList.remove('btn-danger');
            button.classList.add('btn-primary');
        }
    }
    if (instructor) instructor.dataset.sessionStarted = startedAt || '';
}


// ===== THEME TOGGLE =====
// (Theme is restored early in <head> base.html to prevent FOUC)
updateThemeIcon();

window.toggleTheme = function() {
    const html = document.documentElement;
    const isLight = html.classList.toggle('light');
    localStorage.setItem('popping-theme', isLight ? 'light' : 'dark');
    updateThemeIcon();
};

function updateThemeIcon() {
    const icon = document.getElementById('theme-icon');
    if (!icon) return;
    icon.textContent = document.documentElement.classList.contains('light') ? '☽' : '☀';
}

// ===== HISTORY RENDER =====
function renderHistory() {
    const container = document.getElementById('history-list');
    if (!container) return;
    const instructorEl = document.querySelector('.instructor[data-slug]');
    if (!instructorEl) return;
    let history = [];
    try {
        history = JSON.parse(instructorEl.dataset.presentationHistory || '[]');
    } catch(e) {}
    const completedTeams = [...new Set(history.map(h => h.team).filter(Boolean))];
    const completedTeamIds = new Set(
        history.map(h => String(h.team_id ?? '')).filter(Boolean)
    );
    const completedSet = new Set(completedTeams);
    document.querySelectorAll('#comp-team option[value]').forEach(option => {
        const completed = completedTeamIds.has(String(option.value)) ||
            completedSet.has(option.dataset.teamName || '');
        option.dataset.completed = completed ? '1' : '0';
        updateCompetitionTeamOptionLabel(option);
    });

    if (!history.length) {
        container.innerHTML = '<p class="hint">No presentations yet.</p>';
        const teamSummary = document.getElementById('completed-teams-summary');
        if (teamSummary) teamSummary.textContent = 'No teams have completed a presentation.';
        return;
    }
    const teamSummary = document.getElementById('completed-teams-summary');
    if (teamSummary) {
        teamSummary.textContent = completedTeams.length
            ? `Completed teams: ${completedTeams.join(', ')}`
            : 'No teams have completed a presentation.';
    }
    container.innerHTML = history.map(h => `
        <div class="history-item">
            <span>${escapeHtmlValue(h.title || 'Untitled')}: <strong>${escapeHtmlValue(h.team || '?')}</strong></span>
            <span>${escapeHtmlValue(h.responses || 0)} ratings</span>
        </div>
    `).join('');
}

// ===== DISCUSSION QUESTIONS RENDER =====
function renderDiscussionQuestions(data) {
    const container = document.getElementById('disc-questions-list');
    if (!container) return;
    renderedDiscQuestionsVersion = Number(data.version) || 0;
    appendixQuestionsById.clear();

    if (!data.questions || data.questions.length === 0) {
        container.innerHTML = '<p class="empty">No questions found. Add <code>week-N-questions.md</code> files in the course folder, then select a week during setup.</p>';
        resolveRestoredAppendixEditMode();
        return;
    }

    container.innerHTML = data.questions.map((q, i) => {
        const key = discussionQuestionKey(q) || `week-${data.current_week}-q${i}`;
        const safeTitle = escapeHtmlValue(q.title || 'Untitled');
        const expanded = _discQuestionExpanded.has(key) ? ' expanded' : '';
        const appendixId = q.appendix_id || '';
        if (appendixId) {
            appendixQuestionsById.set(appendixId, {
                title: q.title || '',
                content: q.content || ''
            });
        }
        const editBtn = appendixId
            ? `<button class="btn btn-sm btn-secondary appendix-edit-btn" data-appendix-id="${escapeAttrValue(appendixId)}" onclick="event.stopPropagation(); editAppendixQuestion(this)">✏️ Edit</button>`
            : '';
        const deleteBtn = appendixId
            ? `<button class="btn btn-sm btn-danger appendix-delete-btn" data-appendix-id="${escapeAttrValue(appendixId)}" onclick="event.stopPropagation(); deleteAppendixQuestion(this)">🗑 Delete</button>`
            : '';
        const hiddenBadge = q.hidden
            ? '<span class="hidden-question-badge">Hidden from students</span>'
            : '';
        const toggleBtn = `<button class="btn btn-sm ${q.hidden ? 'btn-primary' : 'btn-secondary'} toggle-question-btn" data-question-key="${escapeAttrValue(key)}" data-hidden="${q.hidden ? 1 : 0}" onclick="event.stopPropagation(); toggleDiscussionQuestion(this)">${q.hidden ? 'Show' : 'Hide'}</button>`;
        const action = `<div class="disc-question-actions">
               ${hiddenBadge}
               ${toggleBtn}
               ${editBtn}
               ${deleteBtn}
           </div>`;
        return `
        <div class="disc-question-card${q.hidden ? ' is-hidden' : ''}${expanded}" data-question-key="${escapeAttrValue(key)}">
            <div class="disc-question-header" role="button" tabindex="0" aria-expanded="${expanded ? 'true' : 'false'}" onclick="toggleDiscQuestionCard(this)" onkeydown="discQuestionHeaderKeydown(event)">
                <span class="disc-question-num">#${discussionQuestionNumber(q, i)}</span>
                <span class="disc-question-title">${safeTitle}</span>
                ${action}
            </div>
            <div class="disc-question-body">
                <div class="markdown-content">${renderMarkdown(q.content || '')}</div>
            </div>
        </div>
    `}).join('');

    resolveRestoredAppendixEditMode();
    safeTypeset(container);
    safeHighlight(container);
}

async function refreshDiscussionQuestions() {
    const container = document.getElementById('disc-questions-list');
    if (!container) return;
    const { res, data } = await fetchJSONWithTimeout('/api/discussion_questions');
    if (res.status === 401) {
        redirectToSessionEnded();
        return;
    }
    if (!res.ok) throw new Error('question load failed');
    renderDiscussionQuestions(data);
}

window.toggleDiscussionQuestion = async function(btn) {
    const key = btn.dataset.questionKey;
    const currentlyHidden = btn.dataset.hidden === '1';
    if (!key) return;
    btn.disabled = true;
    try {
        const data = await postJSON('/api/toggle_discussion_question', instructorStatePayload({
            question_key: key,
            visible: currentlyHidden
        }));
        if (data && data.success) {
            showToast(currentlyHidden
                ? 'Question is visible to students'
                : 'Question hidden from students', 'success');
            // No direct refresh here: the toggle bumps
            // discussion_questions_version and the instructor poll loop
            // re-renders the list on that version change.
        } else {
            showInstructorMutationError(data, 'Failed to update question visibility');
        }
    } finally {
        btn.disabled = false;
    }
};
