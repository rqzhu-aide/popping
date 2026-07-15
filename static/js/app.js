async function postJSON(url, body) {
    let res;
    try {
        res = await fetch(url, {
            method: 'POST',
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
    if (res.status === 401) {
        window.location.href = '/';
        return { success: false, error: 'Session expired' };
    }
    const data = await res.json().catch(() => ({}));
    data._status = res.status;
    if (!res.ok && !data.error) data.error = 'Request failed';
    return data;
}

async function deleteJSON(url, body = {}) {
    let res;
    try {
        res = await fetch(url, {
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
    if (res.status === 401) {
        window.location.href = '/';
        return { success: false, error: 'Session expired' };
    }
    const data = await res.json().catch(() => ({}));
    data._status = res.status;
    if (!res.ok && !data.error) data.error = 'Request failed';
    return data;
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

/** Format a student as 'Name (id)' or just 'id' if no name / name equals id. */
function formatStudentDisplay(name, studentId) {
    return (name && name !== studentId)
        ? `${name} (${studentId})`
        : studentId;
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
            return `<div class="roster-member">
                <span class="team-dot" style="background:${escapeAttrValue(team.color)}"></span>
                <span class="${isYou ? 'you' : ''}">${escapeHtmlValue(display)}${isYou ? ' (You)' : ''}</span>
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
        ? `Appendix — ${title}`
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

const resetMessage = sessionStorage.getItem('popping-reset-message');
if (resetMessage) {
    sessionStorage.removeItem('popping-reset-message');
    window.setTimeout(() => showToast(resetMessage, 'success'), 0);
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
        const res = await fetch('/api/upload_roster', { method: 'POST', body: formData });
        if (res.status === 401) {
            window.location.href = '/';
            return;
        }
        const data = await res.json().catch(() => ({}));
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
    if (btn) { btn.disabled = true; btn.textContent = 'Replacing...'; }
    try {
        const formData = new FormData();
        formData.append('file', pendingRosterUpload.file);
        formData.append('confirm', 'true');
        formData.append('preview_token', pendingRosterUpload.token);
        appendInstructorStateForm(formData);
        const res = await fetch('/api/upload_roster', { method: 'POST', body: formData });
        if (res.status === 401) {
            window.location.href = '/';
            return;
        }
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            data._status = res.status;
            showInstructorMutationError(
                data,
                rosterErrorMessage(data, 'Roster replacement failed')
            );
            return;
        }
        showToast(`Roster replaced: ${data.added} added, ${data.updated} updated, ${data.removed || 0} removed.`, 'success');
        closeRosterPreview();
        window.location.reload();
    } catch (error) {
        showToast('Network error while replacing the roster', 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Replace Roster'; }
    }
};

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
let lastRenderedDiscussionKey = null;
let lastKnownQuestionId = null;    // lets the server omit unchanged question bodies
let lastKnownQuestionRevision = null;
let activePollOpen = false;
let ratingSubmissionInProgress = false;
let ratingDraftDirty = false;
let _responseRequestId = 0;
let responseNeedsRefresh = false;

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

    function initDashboard() {
        lastRenderedDiscussionKey = dashboard.dataset.discussionKey || null;
        const initialDiscussion = document.querySelector('#current-question .initial-question-content');
        if (initialDiscussion) {
            initialDiscussion.innerHTML = renderMarkdown(initialDiscussion.textContent);
            safeTypeset(initialDiscussion);
            safeHighlight(initialDiscussion);
        }
        initStarRows();
        updateRatingControlAvailability();
        updateThumbAvailability(Boolean(lastRenderedDiscussionKey));
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

    async function pollOnce() {
        if (_pollInProgress || _pollStopped) return;
        _pollInProgress = true;
        let interval = 3000;
        try {
            const pollParams = new URLSearchParams();
            if (lastKnownQuestionId != null) {
                pollParams.set('known_question_id', lastKnownQuestionId);
            }
            if (lastKnownQuestionRevision) {
                pollParams.set('known_question_revision', lastKnownQuestionRevision);
            }
            if (lastRenderedDiscussionKey) {
                pollParams.set('known_discussion_key', lastRenderedDiscussionKey);
            }
            const questionQuery = pollParams.toString();
            const res = await fetch('/api/poll' + (questionQuery ? `?${questionQuery}` : ''));
            if (res.status === 401) {
                _pollStopped = true;
                window.location.href = '/';
                return;
            }
            if (!res.ok) throw new Error('poll failed');
            const data = await res.json();
            if (_connectionInterrupted) showConnectionStatus('Live updates restored.', true);
            _pollFailures = 0;
            // State is frequent; the roster is fetched only when its version changes.
            const pageReloading = await loadState(data.state);
            if (!pageReloading) {
                try {
                    await syncRoster(data.state?.roster_version);
                } catch (e) {
                    // Keep state polling healthy; retry the roster on the next cycle.
                }
            }
            if (data.top_teams) {
                renderTopTeams(data.top_teams);
            }
            // The server keeps Ended pages on a low-frequency recovery poll.
            if (data.poll_interval) interval = data.poll_interval;
            if (data.stop_polling) _pollStopped = true;
        } catch (e) {
            _pollFailures = Math.min(_pollFailures + 1, 5);
            showConnectionStatus('Live updates interrupted. Information may be out of date.');
        } finally {
            _pollInProgress = false;
            if (!_pollStopped) {
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
            const res = await fetch('/api/teams');
            if (res.status === 401) {
                window.location.href = '/';
                return;
            }
            if (!res.ok) throw new Error('team load failed');
            const teams = await res.json();
            if (!Array.isArray(teams)) throw new Error('invalid team response');
            const maxMembers = Number(dashboard.dataset.maxMembers) || null;
            grid.innerHTML = `
                <button class="team-card unassigned-card" onclick="joinTeam(0)">
                    <span class="team-name" style="color:var(--text-muted)">Remove me from my team</span>
                </button>
            ` + teams.map(t => {
                const memberCount = Array.isArray(t.members) ? t.members.length : Number(t.member_count) || 0;
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
            const memberCount = Array.isArray(team.members)
                ? team.members.length
                : Number(team.member_count) || 0;
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
        if (_rosterVersion === normalizedVersion) return;
        const res = await fetch(`/api/teams?version=${encodeURIComponent(normalizedVersion)}`);
        if (!res.ok) throw new Error('roster refresh failed');
        await loadRoster(await res.json());
        _rosterVersion = normalizedVersion;
    }

    function resetResponseControls() {
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

    function updateThumbAvailability(questionAvailable) {
        document.querySelectorAll('.thumb-btn').forEach(btn => {
            btn.dataset.questionAvailable = questionAvailable ? '1' : '0';
            btn.disabled = !questionAvailable || btn.dataset.saving === '1';
            if (questionAvailable) {
                if (btn.title === 'Wait for the instructor to post a question') {
                    btn.removeAttribute('title');
                }
            } else {
                btn.title = 'Wait for the instructor to post a question';
            }
        });
    }

    function syncMyResponses(state) {
        const discussionKey = state.phase === 'discussion' ? state.discussion_question?.key : null;
        const presentationKey = state.phase === 'competition' ? state.poll_question_key : null;
        const context = discussionKey
            ? `discussion:${discussionKey}`
            : presentationKey
                ? `competition:${presentationKey}`
                : state.phase;
        const contextChanged = context !== _responseContext;
        if (!contextChanged && !responseNeedsRefresh) return;

        _responseContext = context;
        const requestId = ++_responseRequestId;
        if (contextChanged) resetResponseControls();
        if (!discussionKey && !presentationKey) {
            responseNeedsRefresh = false;
            return;
        }

        fetch('/api/my_responses').then(async res => {
            if (res.status === 401) {
                _pollStopped = true;
                window.location.href = '/';
                return null;
            }
            if (!res.ok) return null;
            return res.json();
        }).then(data => {
            if (!data || requestId !== _responseRequestId || context !== _responseContext) return;
            responseNeedsRefresh = false;
            if (discussionKey) {
                if (data.phase !== 'discussion' || data.discussion_question_key !== discussionKey) return;
                const selected = new Set((data.thumb_recipient_ids || []).map(String));
                document.querySelectorAll('.teammate-card[data-student-id] .thumb-btn').forEach(btn => {
                    const card = btn.closest('.teammate-card');
                    const isSelected = selected.has(String(card?.dataset.studentId || ''));
                    btn.classList.toggle('active-green', isSelected);
                    btn.setAttribute('aria-pressed', isSelected ? 'true' : 'false');
                });
            } else if (presentationKey) {
                if (data.phase !== 'competition' || data.presentation_key !== presentationKey) return;
                if (data.rating) {
                    const serverSelections = {
                        q1: Number(data.rating.q1_developed) || 0,
                        q2: Number(data.rating.q2_easy) || 0
                    };
                    savedPollSelections = { ...serverSelections };
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
            }
        }).catch(() => { responseNeedsRefresh = true; });
    }

    async function loadState(state) {
        if (!state) {
            const res = await fetch('/api/state');
            state = await res.json();
        }

        // The dashboard's activity controls are rendered server-side for the
        // current phase and team. Reload once when either changes so students
        // never keep stale discussion or presentation controls.
        const currentTeamId = state.my_team ? state.my_team.id : null;
        const initialLocked = dashboard.dataset.teamsLocked === '1';
        const initialMaxTeams = dashboard.dataset.maxTeams ? Number(dashboard.dataset.maxTeams) : null;
        const initialMaxMembers = dashboard.dataset.maxMembers ? Number(dashboard.dataset.maxMembers) : null;
        const configChanged = Boolean(state.teams_locked) !== initialLocked ||
            (initialMaxTeams != null && state.max_teams != null && Number(state.max_teams) !== initialMaxTeams) ||
            (initialMaxMembers != null && state.max_members != null && Number(state.max_members) !== initialMaxMembers);
        if (state.phase !== dashboard.dataset.phase || currentTeamId !== MY_TEAM_ID || configChanged) {
            window.location.reload();
            return true;
        }

        const badge = document.querySelector('.phase-badge');
        if (badge) {
            badge.textContent = 'Phase: ' + (PHASE_LABELS[state.phase] || state.phase.toUpperCase());
            badge.className = 'phase-badge phase-' + state.phase;
        }

        const qEl = document.getElementById('current-question');
        const question = state.discussion_question;
        if (question && question.key) {
            if (qEl && question.key !== lastRenderedDiscussionKey && !question.content_unchanged) {
                    qEl.innerHTML = `<h3>${escapeHtmlValue(question.title || 'Discussion question')}</h3>${renderMarkdown(question.content || '')}`;
                    safeTypeset(qEl);
                    safeHighlight(qEl);
            }
            // Even students who are awaiting team assignment must acknowledge
            // the body so it is not resent on every discussion poll.
            lastRenderedDiscussionKey = question.key;
        } else {
            if (qEl) {
                qEl.textContent = 'Waiting for the instructor to post a question...';
            }
            lastRenderedDiscussionKey = null;
        }
        updateThumbAvailability(Boolean(question?.key));

        syncMyResponses(state);

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
            updateRatingControlAvailability();
            if (teamGradeSec) teamGradeSec.style.display = canRate ? 'block' : 'none';

            // Update rating prompt with the presenting team's name
            const ratingPrompt = document.getElementById('rating-prompt');
            if (ratingPrompt) {
                if (canRate && state.active_team) {
                    ratingPrompt.textContent = 'Please rate ';
                    const teamName = document.createElement('strong');
                    teamName.textContent = state.active_team.name || '';
                    teamName.style.color = state.active_team.color || '';
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
                document.querySelectorAll('.star').forEach(s => {
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

            if (show) {
                const banner = document.getElementById('presenting-name');
                const qDisplay = document.getElementById('question-display');
                const timer = document.getElementById('student-timer');
                const pollSection = document.getElementById('poll-section');
                const youAreUp = document.getElementById('you-are-up');

                if (!hasActivePresentation) {
                    if (banner) {
                        banner.textContent = 'Waiting for next presentation...';
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
                    return;
                }

                if (banner) {
                    banner.textContent = state.active_team.name;
                    banner.style.color = state.active_team.color;
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
                    if (tv) tv.textContent = formatDuration(parseInt(state.presentation_remaining, 10));
                } else {
                    stopTimer();
                }

                // --- Grading + poll highlight ---
                // "You are up!" banner — presenting team does not grade
                if (youAreUp) youAreUp.style.display = amPresenting ? 'block' : 'none';
                if (pollSection) pollSection.style.display = canRate ? 'block' : 'none';

                // Pulsing 30s poll countdown (non-presenting teams only)
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

    window.submitPeerGrade = async function(recipientId, selected) {
        try {
            const data = await postJSON('/api/grade_peer', {
                recipient_id: recipientId,
                selected,
                question_key: lastRenderedDiscussionKey || ''
            });
            return data && data.success;
        } catch (e) {
            return false;
        }
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
        if (cards.length <= 6) {
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
            youCard.style.top = cards.length > 6 ? '90%' : '85%';
            youCard.style.transform = 'translate(-50%, -50%)';
            positions['you'] = { left: 50, top: cards.length > 6 ? 90 : 85 };
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
        const wasSelected = btn.classList.contains('active-green');
        const selected = !wasSelected;
        btn.classList.toggle('active-green', selected);
        btn.setAttribute('aria-pressed', selected ? 'true' : 'false');
        btn.dataset.saving = '1';
        btn.disabled = true;
        const ok = await submitPeerGrade(recipientId, selected);
        ++_responseRequestId;
        responseNeedsRefresh = true;
        delete btn.dataset.saving;
        btn.disabled = btn.dataset.questionAvailable !== '1';
        if (!ok) {
            btn.classList.toggle('active-green', wasSelected);
            btn.setAttribute('aria-pressed', wasSelected ? 'true' : 'false');
            showToast('Failed to save — please try again', 'error');
        }
        requestImmediatePoll();
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

window.clearSessionTimerStorage = function() {
    const key = sessionTimerStorageKey();
    if (key) localStorage.removeItem(key);
    localStorage.removeItem('popping-session-start');
};

window.confirmDemoReset = function() {
    if (!confirm('Reset demo data? This will restore everything to its initial state.')) return false;
    clearSessionTimerStorage();
    return true;
};

window.randomAssign = async function(btn) {
    if (!confirm('Assign all remaining students evenly to teams?')) return;
    if (btn) btn.disabled = true;
    try {
        const data = await postJSON('/api/random_assign', instructorStatePayload());
        if (data.success) {
            if (Number(data.remaining) > 0) {
                showToast(`Assigned ${data.assigned || 0}; ${data.remaining} remain unassigned because team capacity is full.`, 'warning');
            } else if (data.assigned === 0) {
                showToast('All students are already in teams.');
            } else {
                showToast(`Assigned ${data.assigned} student(s) to teams.`, 'success');
            }
            window.setTimeout(() => window.location.reload(), 250);
        } else {
            showInstructorMutationError(data, 'Assignment failed');
        }
    } finally {
        if (btn) btn.disabled = false;
    }
};

/* ===== INSTRUCTOR PANEL ===== */
const instructor = document.querySelector('.instructor[data-slug]');

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
    showToast(data?.error || fallback, 'error');
    if (data?._status === 409 &&
            /stale|changed|another page|reload|no active presentation/i.test(data.error || '')) {
        window.setTimeout(() => window.location.reload(), 500);
    }
}

if (instructor) {
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
                'poll_question_key',
                instructor.dataset.pollQuestionKey,
                state.poll_question_key
            ) ||
            normalized(instructor.dataset.activeTeamId) !== normalized(state.active_team?.id) ||
            normalized(instructor.dataset.activeQuestionId) !== normalized(state.active_question?.id) ||
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

    function updateCapacityStatus(state) {
        const el = document.getElementById('setup-capacity-warning');
        if (!el) return;
        const total = Number(instructor.dataset.studentCount) || 0;
        const maxTeams = Number(state.max_teams) || Number(instructor.dataset.maxTeams) || 0;
        const maxMembers = Number(state.max_members) || Number(instructor.dataset.maxMembers) || 0;
        const capacity = maxTeams * maxMembers;
        const unassigned = Number(state.unassigned_count) || 0;
        el.classList.toggle('capacity-danger', total > capacity);
        if (total > capacity) {
            el.textContent = `Capacity warning: ${total} students, but only ${capacity} team places. At least ${total - capacity} cannot be assigned.`;
        } else if (unassigned > 0) {
            el.textContent = `${unassigned} student${unassigned === 1 ? '' : 's'} still unassigned (${capacity - total} spare team places).`;
        } else {
            el.textContent = `All ${total} students are assigned. Capacity: ${capacity}.`;
        }
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
            const res = await fetch('/api/poll' + (questionQuery ? `?${questionQuery}` : ''));
            if (res.status === 401) {
                _instrPollStopped = true;
                window.location.href = '/';
                return;
            }
            if (!res.ok) throw new Error('poll failed');
            const data = await res.json();
            if (_instrConnectionInterrupted) {
                showInstructorConnectionStatus('Live updates restored.', true);
            }
            _instrPollFailures = 0;
            const state = data.state;

            if (state && instructorStructureChanged(state)) {
                _instrPollStopped = true;
                window.location.reload();
                return;
            }

            if (document.getElementById('discussion-post-status')) {
                updatePostedDiscussionQuestion(state.discussion_question || null);
                const participation = document.getElementById('thumb-participation');
                if (participation) {
                    const count = Number(state.thumb_participant_count) || 0;
                    const eligible = Number(state.thumb_eligible_count) || 0;
                    const online = Number(state.thumb_online_eligible_count) || 0;
                    participation.textContent = `Recorded participants: ${count}. Eligible now: ${eligible}; online now: ${online}.`;
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
                        const rosterResponse = await fetch(`/api/teams?version=${encodeURIComponent(state.roster_version)}`);
                        if (!rosterResponse.ok) throw new Error('roster refresh failed');
                        renderRosterGrid(rosterGrid, await rosterResponse.json(), null);
                        _instrRosterVersion = state.roster_version;
                        instructor.dataset.rosterVersion = String(state.roster_version || 0);
                    } catch (e) {
                        // Retry on the next poll without blocking timer/rating updates.
                    }
                }
            }

            if (document.getElementById('student-tbody') && Date.now() - _lastStudentTableRefresh >= 10000) {
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
                    const online = Number(state.poll_online_eligible_count) || 0;
                    pollCount.textContent = `Recorded ratings: ${count}. Eligible now: ${eligible}; online now: ${online}.`;
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
                if (state.presentation_started_at && remaining > 0) {
                    startCountdownTimer(remaining);
                    if (pauseButton) pauseButton.style.display = '';
                    if (resumeButton) resumeButton.style.display = 'none';
                } else if (state.presentation_started_at) {
                    stopCountdownTimer();
                    if (timerValue) timerValue.textContent = '00:00';
                    if (pauseButton) pauseButton.style.display = 'none';
                    if (resumeButton) resumeButton.style.display = 'none';
                } else if (state.presentation_remaining != null) {
                    stopCountdownTimer();
                    if (timerValue) timerValue.textContent = formatDuration(Math.max(0, remaining));
                    if (pauseButton) pauseButton.style.display = 'none';
                    if (resumeButton) resumeButton.style.display = remaining > 0 ? '' : 'none';
                }

                // Keep poll controls synchronized across instructor tabs.
                const ps = document.getElementById('poll-status');
                if (state.poll_active && Number(state.poll_remaining) > 0) {
                    startPollGlide(state.poll_remaining, state.poll_duration || 30);
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
            if (!_instrPollStopped) {
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
        if (!confirm(`Reducing to ${newMax} teams will unassign students in teams ${newMax+1}–${_originalMaxTeams}. Continue?`)) {
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

window.setPhase = async function(phase) {
    const currentPhase = instructor?.dataset?.phase || '';
    const leavingActivePresentation = phase !== 'competition' && document.getElementById('timer-box');
    let confirmEndSession = false;
    if (phase === 'ended' && currentPhase !== 'ended') {
        const message = leavingActivePresentation
            ? 'End this session and finalize the active presentation? This closes the current classroom session.'
            : 'End this classroom session?';
        if (!confirm(message)) return;
        confirmEndSession = true;
    } else if (leavingActivePresentation &&
               !confirm('A presentation is still active. Leave this phase and finalize the current presentation?')) {
        return;
    }
    const data = await postJSON('/api/set_phase', instructorStatePayload({
        phase,
        confirm_end_session: confirmEndSession
    }));
    if (data.success) {
        window.location.reload();
    } else {
        showInstructorMutationError(data, 'Failed to change phase');
    }
};

/* ===== STUDENT TABLE (paginated) ===== */
let studentSort = 'student_id';
let studentOrder = 'asc';
let studentPage = 1;
let teamFilterVal = '';
let _studentTableLoading = false;
let _studentTableLoadFailed = false;
let _studentTableRetryTimer = null;

window.toggleTeamFilter = function(e) {
    e.stopPropagation();
    const menu = document.getElementById('team-filter-menu');
    if (!menu) return;
    const isOpen = menu.style.display === 'block';
    menu.style.display = isOpen ? 'none' : 'block';
    if (!isOpen) {
        setTimeout(() => {
            document.addEventListener('click', function close() {
                menu.style.display = 'none';
                document.removeEventListener('click', close);
            }, { once: true });
        }, 0);
    }
};

window.setTeamFilter = function(val, label) {
    teamFilterVal = val;
    document.getElementById('team-filter-label').textContent = label;
    document.getElementById('team-filter-menu').style.display = 'none';
    studentPage = 1;
    loadStudentTable();
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
        page: studentPage, per_page: 10, sort: studentSort, order: studentOrder, search, team: teamFilterVal
    });
    let data;
    try {
        const res = await fetch('/api/students?' + params);
        if (res.status === 401) {
            window.location.href = '/';
            return;
        }
        if (!res.ok) throw new Error('student load failed');
        data = await res.json();
    } catch (error) {
        const tbody = document.getElementById('student-tbody');
        if (tbody && !tbody.querySelector('tr[data-id]')) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty">Could not load students. Retrying automatically.</td></tr>';
        }
        if (!_studentTableLoadFailed) {
            showToast('Could not refresh the student list. Retrying automatically.', 'error');
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
    if (instructorPage) instructorPage.dataset.studentCount = String(data.total || 0);

    const tbody = document.getElementById('student-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const teams = data.teams || [];

    data.students.forEach(s => {
        const teamHtml = s.team_name
            ? `<span class="team-tag clickable" style="background:${escapeAttrValue(s.team_color || '#ccc')}" onclick="toggleTeamPicker(event, ${s.id})" data-student="${s.id}">${escapeHtmlValue(s.team_name)}</span>`
            : `<span class="team-tag unassigned-tag clickable" onclick="toggleTeamPicker(event, ${s.id})" data-student="${s.id}">Unassigned</span>`;
        const statusHtml = s.is_online
            ? '<span class="online-dot"></span> Online'
            : '<span class="offline-dot"></span> Offline';
        const loginTime = s.last_login_at ? s.last_login_at.substring(0, 19) : '—';
        const name = s.name || s.student_id;

        const tr = document.createElement('tr');
        tr.setAttribute('data-id', s.id);
        tr.innerHTML = `
            <td>${escapeHtmlValue(s.student_id)}</td>
            <td>${escapeHtmlValue(name)}</td>
            <td>${teamHtml}</td>
            <td>${statusHtml}</td>
            <td class="text-muted small">${escapeHtmlValue(loginTime)}</td>
            <td><button class="btn btn-sm btn-danger" onclick="removeStudent(${s.id}, this)">Remove</button></td>
        `;
        tbody.appendChild(tr);
    });

    // Update sort arrows
    document.querySelectorAll('#student-table .sort-arrow').forEach(el => el.textContent = '');
    const activeHeader = document.querySelector(`#student-table [data-sort="${studentSort}"] .sort-arrow`);
    if (activeHeader) activeHeader.textContent = studentOrder === 'asc' ? ' ▲' : ' ▼';

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

window.goToStudentPage = function(p) {
    studentPage = p;
    loadStudentTable();
};

// Load on page load if table exists
if (document.getElementById('student-tbody')) {
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

let rosterMutationInProgress = false;

window.pickTeam = async function(studentId, teamId) {
    if (rosterMutationInProgress) return;
    rosterMutationInProgress = true;
    document.querySelectorAll('.team-picker').forEach(el => el.remove());
    try {
        const data = await postJSON('/api/assign_student', instructorStatePayload({
            student_id: studentId,
            team_id: teamId
        }));
        if (data && data.success) {
            window.location.reload();
        } else {
            showInstructorMutationError(data, 'Failed to assign student');
        }
    } finally {
        rosterMutationInProgress = false;
    }
};

window.addStudent = async function(btn) {
    const id = document.getElementById('new-student-id').value.trim();
    const name = document.getElementById('new-name').value.trim();
    const pin = document.getElementById('new-pin').value.trim();
    if (!id || !pin) { showToast('Student ID and PIN are required', 'warning'); return; }
    if (btn) btn.disabled = true;
    try {
        const data = await postJSON('/api/add_student', instructorStatePayload({
            student_id: id,
            name,
            pin
        }));
        if (data.success) {
            showToast(data.updated ? 'Student info updated' : 'Student added', 'success');
            window.setTimeout(() => window.location.reload(), 250);
        } else {
            showInstructorMutationError(data, 'Failed to add student');
        }
    } finally {
        if (btn) btn.disabled = false;
    }
};

/* ===== DISCUSSION QUESTIONS (weekly .md files) ===== */

let discussionQuestionsByKey = new Map();
let discussionCurrentWeek = null;
let currentDiscussionInstanceKey = '';
let discussionPostPending = false;

// Populate setup week selector and discussion loader
async function initWeekSelector() {
    let data;
    try {
        const res = await fetch('/api/discussion_questions');
        if (res.status === 401) {
            window.location.href = '/';
            return;
        }
        if (!res.ok) throw new Error('question load failed');
        data = await res.json();
        discussionCurrentWeek = Number(data.current_week) || null;
    } catch (error) {
        const setupSel = document.getElementById('setup-week-select');
        if (setupSel) {
            setupSel.innerHTML = '<option value="" selected disabled>Could not load question weeks</option>';
            setupSel.disabled = true;
        }
        const container = document.getElementById('disc-questions-list');
        if (container) container.innerHTML = '<p class="empty">Could not load discussion questions. Please refresh.</p>';
        return;
    }

    // Setup page week selector
    const setupSel = document.getElementById('setup-week-select');
    const weeks = data.weeks || [];
    if (setupSel) {
        if (weeks.length > 0) {
            setupSel.disabled = false;
            setupSel.innerHTML = weeks.map(w => {
                let status = '';
                if (!w.discussion_ready && !w.presentation_ready) {
                    status = ' (not ready)';
                } else if (!w.discussion_ready) {
                    status = ' (discussion not ready)';
                } else if (!w.presentation_ready) {
                    status = ' (discussion only; presentation unavailable)';
                }
                const selected = w.num === data.current_week ? 'selected' : '';
                const disabled = w.discussion_ready ? '' : 'disabled';
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

    // Discussion page: auto-load questions for current week
    const container = document.getElementById('disc-questions-list');
    if (container) {
        renderDiscussionQuestions(data);
    }
}

window.setDiscussionWeek = async function() {
    const select = document.getElementById('setup-week-select');
    const week = select?.value;
    if (!week) return;
    select.disabled = true;
    const data = await postJSON('/api/set_discussion_week', instructorStatePayload({
        week: parseInt(week)
    }));
    if (data && data.success) {
        const count = data.question_count;
        const detail = data.presentation_ready
            ? `${count != null ? ` (${count} presentation choices synced)` : ''}`
            : ' (discussion ready; presentation questions unavailable)';
        showToast(`Week ${week} selected${detail}`, 'success');
        window.setTimeout(() => window.location.reload(), 250);
    } else {
        select.value = instructor?.dataset?.discussionWeek || '';
        select.disabled = false;
        showInstructorMutationError(data, 'Failed to select discussion week');
    }
};

function setDiscussionPostPending(pending) {
    discussionPostPending = pending;
    document.querySelectorAll('.post-question-btn').forEach(button => {
        button.disabled = pending || button.closest('.disc-question-card')
            ?.classList.contains('is-posted');
    });
    const unpostButton = document.getElementById('btn-unpost-question');
    if (unpostButton) unpostButton.disabled = pending;
}

window.postActiveQuestion = async function(question) {
    if (!question?.key || discussionPostPending) return;
    setDiscussionPostPending(true);
    const data = await postJSON('/api/set_question', instructorStatePayload({
        key: question.key,
        title: question.title || '',
        content: question.content || '',
        expected_discussion_key: currentDiscussionInstanceKey
    }));
    if (data && data.success) {
        updatePostedDiscussionQuestion(data.discussion_question || question);
        showToast('Question posted: ' + question.title, 'success');
    } else {
        showInstructorMutationError(data, 'Failed to post question');
    }
    setDiscussionPostPending(false);
};

// Load on page load
if (document.getElementById('setup-week-select') || document.getElementById('disc-questions-list')) {
    initWeekSelector();
}

window.startPresentation = async function() {
    const teamId = document.getElementById('comp-team').value;
    const questionId = document.getElementById('comp-question').value;
    const timeCap = parseInt(document.getElementById('time-cap')?.value || '300', 10);
    if (!teamId || !questionId) { showToast('Select both a team and a question', 'warning'); return; }
    const data = await postJSON('/api/start_presentation', {
        ...instructorStatePayload(),
        team_id: parseInt(teamId, 10),
        question_id: parseInt(questionId, 10),
        time_cap: timeCap
    });
    if (data.success) {
        window.location.reload();
    } else {
        showInstructorMutationError(data, 'Failed to start presentation');
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
    } else {
        showInstructorMutationError(data, 'Failed to reset timer');
    }
};

window.nextPresentation = async function() {
    const data = await postJSON('/api/next_presentation', instructorStatePayload());
    if (data.success) {
        window.location.reload();
    } else {
        showInstructorMutationError(data, 'Failed to finish presentation');
    }
};

window.cancelPresentation = async function() {
    if (!confirm('Cancel this mistaken presentation without adding it to history?')) return;
    let data = await postJSON('/api/cancel_presentation', instructorStatePayload());
    if (data.requires_discard) {
        const count = Number(data.rating_count) || 0;
        if (!confirm(`This will permanently discard ${count} submitted rating${count === 1 ? '' : 's'}. Continue?`)) return;
        data = await postJSON('/api/cancel_presentation', instructorStatePayload({
            discard_ratings: true
        }));
    }
    if (data.success) {
        window.location.reload();
    } else {
        showInstructorMutationError(data, 'Failed to cancel presentation');
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
    const duration = Math.max(1, Number(durationSec) || 30);
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
            btn.disabled = false;
            const nextButton = document.getElementById('btn-next');
            if (nextButton) nextButton.disabled = false;
            glide.style.width = '0%';
            if (label) label.textContent = '⭐ Start Poll';
            if (pollGlideTimeout) { clearTimeout(pollGlideTimeout); pollGlideTimeout = null; }
            pollGlideAnchor = null;
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
    btn.disabled = false;
    const nextButton = document.getElementById('btn-next');
    if (nextButton) nextButton.disabled = false;
    if (glide) glide.style.width = '0%';
    if (label) label.textContent = '⭐ Start Poll';
    if (pollGlideTimeout) { clearTimeout(pollGlideTimeout); pollGlideTimeout = null; }
    pollGlideAnchor = null;
}

/**
 * Student: pulsing countdown badge on the grading card during the 30s poll.
 */
let pollPulseInterval = null;
let pollPulseAnchor = null;

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
        if (pulseText) pulseText.textContent = `⏱ ${Math.ceil(remaining)}s left to rate!`;

        if (remaining <= 0) {
            clearInterval(pollPulseInterval);
            pollPulseInterval = null;
            pollPulseAnchor = null;
            pulse.style.display = 'none';
        }
    }
    tick();
    if (pollPulseAnchor && pollPulseInterval === null) {
        pollPulseInterval = setInterval(tick, 250);
    }
}

function stopPollPulse() {
    const pulse = document.getElementById('poll-pulse');
    if (pulse) pulse.style.display = 'none';
    if (pollPulseInterval) { clearInterval(pollPulseInterval); pollPulseInterval = null; }
    pollPulseAnchor = null;
}

// ===== POLL API =====

window.startPoll = async function() {
    const btn = document.getElementById('btn-start-poll');
    if (btn && btn.classList.contains('gliding')) return; // already running
    const data = await postJSON('/api/start_poll', instructorStatePayload());
    if (data.success && data.poll_started_at) {
        startPollGlide(data.poll_remaining, data.poll_duration || 30);
        // Reveal the Stop-Poll panel so the instructor can cancel early
        const ps = document.getElementById('poll-status');
        if (ps) ps.style.display = 'flex';
    } else {
        showInstructorMutationError(data, 'Failed to start poll');
    }
};

window.stopPoll = async function() {
    const data = await postJSON('/api/stop_poll', instructorStatePayload());
    if (data.success) {
        stopPollGlide();
    } else {
        showInstructorMutationError(data, 'Failed to stop poll');
    }
};

window.submitRating = async function() {
    if (!activePollOpen || !lastRatedPresentationKey) {
        showToast('The rating window is closed', 'warning');
        return;
    }
    if (!pollSelections.q1 || !pollSelections.q2) {
        showToast('Please rate both questions', 'warning');
        return;
    }
    const selectionSnapshot = { q1: pollSelections.q1, q2: pollSelections.q2 };
    const presentationKey = lastRatedPresentationKey;
    const btn = document.getElementById('btn-submit-rating');
    ++_responseRequestId;
    ratingSubmissionInProgress = true;
    updateRatingControlAvailability();
    if (btn) btn.textContent = 'Submitting...';
    try {
        const data = await postJSON('/api/submit_rating', {
            q1_developed: selectionSnapshot.q1,
            q2_easy: selectionSnapshot.q2,
            presentation_key: presentationKey
        });
        if (data && data.success) {
            ++_responseRequestId;
            showToast('Rating submitted', 'success');
            savedPollSelections = { ...selectionSnapshot };
            ratingDraftDirty = false;
            responseNeedsRefresh = false;
            if (presentationKey === lastRatedPresentationKey) {
                showCurrentRating(selectionSnapshot);
            }
        } else {
            ++_responseRequestId;
            responseNeedsRefresh = true;
            showRatingNotSaved();
            showToast(data?.error || 'Failed to submit rating', 'error');
            window.requestDashboardPoll?.();
        }
    } catch (e) {
        ++_responseRequestId;
        responseNeedsRefresh = true;
        showRatingNotSaved();
        showToast('Network error. Please try again.', 'error');
        window.requestDashboardPoll?.();
    } finally {
        ratingSubmissionInProgress = false;
        updateRatingControlAvailability();
        if (btn) btn.textContent = 'Submit Rating';
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
    if (!status) return;
    status.textContent = 'Changes not submitted.';
    status.style.color = 'var(--warning)';
    status.style.display = 'block';
}

function showRatingNotSaved() {
    const status = document.getElementById('rating-status');
    if (!status) return;
    status.textContent = 'Rating not saved. Rechecking the saved response...';
    status.style.color = 'var(--danger)';
    status.style.display = 'block';
}

function updateRatingControlAvailability() {
    const disabled = !activePollOpen || ratingSubmissionInProgress;
    document.querySelectorAll('.star-row .star').forEach(star => {
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
                ratingDraftDirty = true;
                const val = parseInt(star.dataset.value, 10);
                pollSelections[question] = val;
                setStarRowValue(question, val);
                if (savedPollSelections &&
                        pollSelections.q1 === savedPollSelections.q1 &&
                        pollSelections.q2 === savedPollSelections.q2) {
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
    if (btn) btn.disabled = true;
    const data = await postJSON('/api/unassign_all', instructorStatePayload());
    if (data.success) {
        showToast('All team assignments cleared', 'warning');
        window.setTimeout(() => window.location.reload(), 250);
    } else {
        if (btn) btn.disabled = false;
        showInstructorMutationError(data, 'Failed to clear team assignments');
    }
};

// ===== APPENDIX QUESTIONS =====

let appendixMutationInProgress = false;

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

window.addAppendixQuestion = async function(button) {
    const title = document.getElementById('appendix-title')?.value.trim();
    const content = document.getElementById('appendix-content')?.value.trim();
    if (!title || !content) { showToast('Title and content required', 'warning'); return; }
    const week = displayedAppendixWeek();
    if (!week) { showToast('Could not determine the displayed week', 'error'); return; }
    if (!beginAppendixMutation(button, 'Adding...')) return;
    let keepPending = false;
    try {
        const data = await postJSON('/api/questions', instructorStatePayload({
            title,
            content,
            week
        }));
        if (data && data.success) {
            document.getElementById('appendix-title').value = '';
            document.getElementById('appendix-content').value = '';
            showToast(`${data.label || 'Appendix question'} added`, 'success');
            if (document.getElementById('competition-appendix-form')) {
                keepPending = true;
                window.setTimeout(() => window.location.reload(), 250);
            } else {
                await initWeekSelector();
            }
        } else {
            showInstructorMutationError(data, 'Failed to add question');
        }
    } finally {
        if (!keepPending) endAppendixMutation(button);
    }
};

window.deleteAppendixQuestion = async function(button) {
    const appendixId = button?.dataset?.appendixId;
    const week = displayedAppendixWeek();
    if (!appendixId || !week || appendixMutationInProgress) return;
    const isPosted = button.closest('.disc-question-card')?.classList.contains('is-posted');
    const warning = isPosted
        ? 'This question is currently posted. Delete it and remove it from student screens?'
        : 'Delete this appendix question?';
    if (!confirm(warning)) return;
    if (!beginAppendixMutation(button, 'Deleting...')) return;
    try {
        const data = await postJSON(
            '/api/delete_appendix_question',
            instructorStatePayload({ appendix_id: appendixId, week })
        );
        if (data && data.success) {
            showToast(data.unposted ? 'Question deleted and unposted' : 'Question deleted');
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
    if (btn) btn.disabled = true;
    const data = await deleteJSON(
        '/api/remove_student/' + studentDbId,
        instructorStatePayload()
    );
    if (data.success) {
        showToast('Student removed');
        window.setTimeout(() => window.location.reload(), 250);
    } else {
        if (btn) btn.disabled = false;
        showInstructorMutationError(data, 'Failed to remove student');
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
            'popping-reset-message',
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

function stopTimer() {
    stopCountdownTimer();
    document.querySelectorAll('.timer-value').forEach(el => {
        el.textContent = '--:--';
    });
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
    const POLL_DURATION = parseInt(instructorEl.dataset.pollDuration || '30', 10);
    if (pollActive && pollStarted && pollRemaining > 0) {
        startPollGlide(pollRemaining, POLL_DURATION);
        if (pollStatus) pollStatus.style.display = 'flex';
    } else {
        stopPollGlide();
        if (pollStatus) pollStatus.style.display = 'none';
    }
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
    if (!history.length) {
        container.innerHTML = '<p class="hint">No presentations yet.</p>';
        const teamSummary = document.getElementById('completed-teams-summary');
        if (teamSummary) teamSummary.textContent = 'No teams have completed a presentation.';
        return;
    }
    const completedTeams = [...new Set(history.map(h => h.team).filter(Boolean))];
    const completedSet = new Set(completedTeams);
    document.querySelectorAll('#comp-team option[value]').forEach(option => {
        const baseLabel = option.dataset.baseLabel || option.textContent.trim();
        option.dataset.baseLabel = baseLabel;
        option.textContent = completedSet.has(baseLabel) ? `${baseLabel} (completed)` : baseLabel;
    });
    const teamSummary = document.getElementById('completed-teams-summary');
    if (teamSummary) {
        teamSummary.textContent = completedTeams.length
            ? `Completed teams: ${completedTeams.join(', ')}`
            : 'No teams have completed a presentation.';
    }
    container.innerHTML = history.map(h => `
        <div class="history-item">
            <span>${escapeHtmlValue(h.title || 'Untitled')} — <strong>${escapeHtmlValue(h.team || '?')}</strong></span>
            <span>${escapeHtmlValue(h.responses || 0)} ratings</span>
        </div>
    `).join('');
}

// ===== DISCUSSION QUESTIONS RENDER =====
function renderDiscussionQuestions(data) {
    const container = document.getElementById('disc-questions-list');
    if (!container) return;

    if (!data.questions || data.questions.length === 0) {
        discussionQuestionsByKey.clear();
        container.innerHTML = '<p class="empty">No questions found. Add <code>week-N-questions.md</code> files in the course folder, then select a week during setup.</p>';
        return;
    }

    discussionQuestionsByKey = new Map();
    container.innerHTML = data.questions.map((q, i) => {
        const key = q.key || q._key || `week-${data.current_week}-q${i}`;
        const question = { key, title: q.title || 'Untitled', content: q.content || '' };
        discussionQuestionsByKey.set(key, question);
        const safeTitle = escapeHtmlValue(q.title || 'Untitled');
        const appendixId = q.appendix_id || '';
        const deleteBtn = appendixId
            ? `<button class="btn btn-sm btn-danger appendix-delete-btn" data-appendix-id="${escapeAttrValue(appendixId)}" onclick="event.stopPropagation(); deleteAppendixQuestion(this)">🗑 Delete</button>`
            : '';
        const action = `<div class="disc-question-actions">
               <span class="posted-question-badge" hidden>● Currently Posted</span>
               <button class="btn btn-sm btn-primary post-question-btn" data-question-key="${escapeAttrValue(key)}" onclick="event.stopPropagation(); postActiveQuestionFromButton(this)">Post to Class</button>
               ${deleteBtn}
           </div>`;
        return `
        <div class="disc-question-card" data-question-key="${escapeAttrValue(key)}">
            <div class="disc-question-header" onclick="this.parentElement.classList.toggle('expanded')">
                <span class="disc-question-num">#${i + 1}</span>
                <span class="disc-question-title">${safeTitle}</span>
                ${action}
            </div>
            <div class="disc-question-body">
                <div class="markdown-content">${renderMarkdown(q.content || '')}</div>
            </div>
        </div>
    `}).join('');

    updatePostedDiscussionQuestion(data.discussion_question || data.current_question || null);

    safeTypeset(container);
    safeHighlight(container);
}

window.postActiveQuestionFromButton = function(btn) {
    postActiveQuestion(discussionQuestionsByKey.get(btn.dataset.questionKey));
};

window.unpostDiscussionQuestion = async function() {
    if (discussionPostPending) return;
    setDiscussionPostPending(true);
    const data = await postJSON('/api/set_question', instructorStatePayload({
        key: '',
        title: '',
        content: '',
        expected_discussion_key: currentDiscussionInstanceKey
    }));
    if (data.success) {
        updatePostedDiscussionQuestion(null);
        showToast('Question removed from student screens');
    } else {
        showInstructorMutationError(data, 'Failed to unpost question');
    }
    setDiscussionPostPending(false);
};

function updatePostedDiscussionQuestion(question) {
    let posted = question;
    if (typeof question === 'string') {
        posted = [...discussionQuestionsByKey.values()].find(q => q.title === question) ||
            (question ? { key: '', title: question, content: '' } : null);
    }
    const postedKey = posted?.source_key || posted?.key || '';
    const postedTitle = posted?.title || '';
    currentDiscussionInstanceKey = posted?.key || '';
    const status = document.getElementById('discussion-post-status');
    const statusText = document.getElementById('posted-discussion-question');
    if (status) status.classList.toggle('has-posted-question', !!postedTitle);
    if (statusText) {
        statusText.textContent = postedTitle
            ? `Currently posted: ${postedTitle}`
            : 'No question is currently posted to the class.';
    }
    const unpostButton = document.getElementById('btn-unpost-question');
    if (unpostButton) unpostButton.hidden = !postedTitle;

    document.querySelectorAll('.disc-question-card[data-question-key]').forEach(card => {
        const isPosted = !!postedKey && card.dataset.questionKey === postedKey;
        card.classList.toggle('is-posted', isPosted);
        const badge = card.querySelector('.posted-question-badge');
        if (badge) badge.hidden = !isPosted;
        const button = card.querySelector('.post-question-btn');
        if (button) {
            button.disabled = isPosted;
            button.textContent = isPosted ? 'Posted to Class' : 'Post to Class';
        }
    });
}
