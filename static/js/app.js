async function postJSON(url, body) {
    const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    return res.json();
}

async function deleteJSON(url) {
    const res = await fetch(url, { method: 'DELETE' });
    return res.json();
}

function getCourseId() {
    const el = document.querySelector('.instructor[data-course-id]');
    return el ? parseInt(el.dataset.courseId, 10) : null;
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

window.handleRosterFile = async function(e) {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/upload_roster', { method: 'POST', body: formData });
    const data = await res.json();
    if (data.success) {
        alert(`Roster uploaded: ${data.added} added, ${data.updated} updated, ${data.removed || 0} removed.`);
        window.location.reload();
    } else {
        alert(data.error || 'Upload failed');
    }
    e.target.value = '';
};

/* ===== LOGIN PAGE ===== */
if (document.querySelector('.login-form')) {
    // nothing special needed
}

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
let pollInterval = null;
let ratingSubmitted = false;       // has the student submitted for the current presentation?
let lastRatedTeamId = null;        // track which team was rated to detect new presentations
let lastRenderedQuestionKey = null; // track active question to avoid re-rendering same HTML

const dashboard = document.querySelector('.dashboard');
if (dashboard) {
    function initDashboard() {
        loadRoster();
        loadState();
        setInterval(() => {
            loadRoster();
            loadState();
            if (document.getElementById('discussion-section')?.style.display !== 'none') {
                loadDiscussions();
            }
        }, 3000);
    }

    window.joinTeam = async function(teamId) {
        const data = await postJSON('/api/join_team', { team_id: teamId });
        if (data.success) {
            window.location.reload();
        } else {
            alert(data.error || 'Failed to join team');
        }
    };

    window.showTeamPicker = async function() {
        document.getElementById('btn-change-team').style.display = 'none';
        const panel = document.getElementById('team-picker-panel');
        panel.style.display = 'block';
        const grid = document.getElementById('team-picker-grid');
        const res = await fetch('/api/teams');
        const teams = await res.json();
        grid.innerHTML = `
            <button class="team-card unassigned-card" onclick="joinTeam(0)">
                <span class="team-name" style="color:var(--text-muted)">Remove me from my team</span>
            </button>
        ` + teams.map(t => `
            <button class="team-card" style="--team-color: ${t.color}"
                    onclick="joinTeam(${t.id})">
                <span class="team-dot" style="background: ${t.color}"></span>
                <span class="team-name">${t.name}</span>
            </button>
        `).join('');
    };

    window.hideTeamPicker = function() {
        document.getElementById('btn-change-team').style.display = '';
        document.getElementById('team-picker-panel').style.display = 'none';
    };

    async function loadRoster() {
        const res = await fetch('/api/teams');
        const teams = await res.json();
        const container = document.getElementById('roster-table');
        if (!container) return;
        container.innerHTML = '';
        teams.forEach(team => {
            const div = document.createElement('div');
            div.className = 'roster-team';
            const myId = dashboard.dataset.you;
            const members = team.members.map(m => {
                const isYou = m.student_id === myId;
                const display = m.name && m.name !== m.student_id
                    ? `${m.name} (${m.student_id})`
                    : m.student_id;
                return `<div class="roster-member">
                    <span class="team-dot" style="background:${team.color}"></span>
                    <span class="${isYou ? 'you' : ''}">${display} ${isYou ? '(You)' : ''}</span>
                </div>`;
            }).join('');
            div.innerHTML = `<h3><span class="team-dot" style="background:${team.color}"></span>${team.name}</h3>${members || '<em>No members</em>'}`;
            container.appendChild(div);
        });
    }

    async function loadState() {
        const res = await fetch('/api/state');
        const state = await res.json();

        const badge = document.querySelector('.phase-badge');
        if (badge) {
            badge.textContent = 'Phase: ' + (PHASE_LABELS[state.phase] || state.phase.toUpperCase());
            badge.className = 'phase-badge phase-' + state.phase;
        }

        const qEl = document.getElementById('current-question');
        if (qEl && state.current_question) {
            qEl.textContent = state.current_question;
        }

        const discSec = document.getElementById('discussion-section');
        if (discSec) {
            discSec.style.display = PHASES_WITH_DISCUSSION.includes(state.phase) ? 'block' : 'none';
        }
        const teamSec = document.getElementById('team-section');
        const teamGradeSec = document.getElementById('team-grading-section');
        if (teamSec) {
            const show = PHASES_WITH_TEAM.includes(state.phase);
            teamSec.style.display = show ? 'block' : 'none';

            // Compute amPresenting early so we can also hide the grading card
            const amPresenting = !!(state.active_team && state.active_team.id === MY_TEAM_ID);
            // "Now Presenting" card shows for all teams; grading card hidden for presenting team
            if (teamGradeSec) teamGradeSec.style.display = (show && !amPresenting) ? 'block' : 'none';

            // Detect new presentation — reset rating UI when active team changes
            const currentTeamId = state.active_team ? state.active_team.id : null;
            if (currentTeamId !== lastRatedTeamId) {
                lastRatedTeamId = currentTeamId;
                ratingSubmitted = false;
                pollSelections = { q1: 0, q2: 0 };
                // Reset star visuals
                document.querySelectorAll('.star').forEach(s => {
                    s.classList.remove('active');
                    s.textContent = '☆';
                });
                // Reset submit button
                const sBtn = document.getElementById('btn-submit-rating');
                if (sBtn) {
                    sBtn.disabled = false;
                    sBtn.textContent = 'Submit Rating';
                    sBtn.classList.remove('btn-submitted');
                }
            }

            if (show) {
                const banner = document.getElementById('presenting-name');
                if (banner && state.active_team) {
                    banner.textContent = state.active_team.name;
                    banner.style.color = state.active_team.color;
                }
                // Show active question — only re-render when question changes
                const qDisplay = document.getElementById('question-display');
                const qText = document.getElementById('active-question-text');
                if (qDisplay && qText && state.active_question) {
                    qDisplay.style.display = 'block';
                    const qKey = `${state.active_question.id}-${state.active_question.question_num}`;
                    if (qKey !== lastRenderedQuestionKey) {
                        lastRenderedQuestionKey = qKey;
                        if (state.active_question.html_content) {
                            qText.innerHTML = state.active_question.html_content;
                        } else {
                            const md = state.active_question.content || state.active_question.question_text || '';
                            qText.innerHTML = marked.parse(`#${state.active_question.question_num}: ${state.active_question.title || ''}\n\n${md}`);
                        }
                        if (window.MathJax) MathJax.typesetPromise([qText]);
                        if (window.hljs) hljs.highlightAll();
                    }
                } else if (qDisplay) {
                    qDisplay.style.display = 'none';
                    lastRenderedQuestionKey = null;
                }
                // --- Presentation countdown timer (visible to ALL teams) ---
                const timer = document.getElementById('student-timer');
                const hasPres = !!state.presentation_started_at;
                const hasRemaining = state.presentation_remaining != null;
                if (timer) {
                    timer.style.display = (hasPres || hasRemaining) ? 'flex' : 'none';
                }
                if (hasPres) {
                    startTimer(state.presentation_started_at, state.presentation_time_cap);
                } else if (hasRemaining) {
                    // Paused — show the frozen remaining value
                    stopTimer();
                    const tv = document.getElementById('student-timer-value');
                    if (tv) tv.textContent = formatDuration(parseInt(state.presentation_remaining, 10));
                } else {
                    stopTimer();
                }

                // --- Grading + poll highlight ---
                const pollSection = document.getElementById('poll-section');
                const youAreUp = document.getElementById('you-are-up');

                // "You are up!" banner — presenting team does not grade
                if (youAreUp) youAreUp.style.display = amPresenting ? 'block' : 'none';
                // Grading cards are always available to non-presenting teams
                if (pollSection) pollSection.style.display = amPresenting ? 'none' : 'block';

                // Pulsing 30s poll countdown (non-presenting teams only)
                if (!amPresenting && state.poll_active && state.poll_started_at) {
                    startPollPulse(state.poll_started_at, state.poll_duration || 30);
                } else {
                    stopPollPulse();
                }

                // Initialize star rows ONCE — not on every 3-second poll (prevents listener leak)
                if (!window._starRowsBound) {
                    initStarRows();
                    window._starRowsBound = true;
                }
            } else {
                stopTimer();
            }
        }
    }

    window.submitPeerGrade = async function(recipientId, score) {
        await postJSON('/api/grade_peer', { recipient_id: recipientId, criterion: 'overall', score });
    };

    window.submitDiscussion = async function() {
        const input = document.getElementById('discussion-input');
        const question = document.getElementById('current-question')?.textContent || 'General';
        if (!input.value.trim()) return;
        await postJSON('/api/submit_discussion', { question, response: input.value.trim() });
        input.value = '';
        loadDiscussions();
    };

    async function loadDiscussions() {
        const res = await fetch('/api/discussion_responses');
        const rows = await res.json();
        const container = document.getElementById('discussion-list');
        if (!container) return;
        container.innerHTML = rows.slice(0, 20).map(r => {
            const display = r.name && r.name !== r.student_id
                ? `${r.name} (${r.student_id})`
                : r.student_id;
            return `
            <div class="discussion-item">
                <div class="meta">${display} · ${r.team_name || 'No team'} · ${r.created_at}</div>
                <div>${escapeHtml(r.response)}</div>
            </div>
        `}).join('');
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // --- teammate cards: circle layout + free drag ---
    function initTeammateCards() {
        const table = document.getElementById('team-table');
        if (!table) return;

        const storageKey = 'popping-pos-' + (dashboard.dataset.you || '');
        let positions = {};
        try { positions = JSON.parse(localStorage.getItem(storageKey)) || {}; } catch(e) {}

        const cards = [...table.querySelectorAll('.teammate-card')];
        const hasSaved = cards.some(c => positions[c.dataset.studentId]);

        if (!hasSaved) {
            // Arrange in an arc above "You"
            const cx = 50, cy = 55, rx = 42, ry = 32;
            cards.forEach((card, i) => {
                const angle = Math.PI - (i / Math.max(cards.length - 1, 1)) * Math.PI;
                const left = cx + rx * Math.cos(angle);
                const top = cy - ry * Math.sin(angle);
                positions[card.dataset.studentId] = { left, top };
                card.style.left = left + '%';
                card.style.top = top + '%';
                card.style.transform = 'translate(-50%, -50%)';
            });
            saveCardPositions(positions);
        } else {
            cards.forEach(card => {
                const pos = positions[card.dataset.studentId];
                if (pos) {
                    card.style.left = pos.left + '%';
                    card.style.top = pos.top + '%';
                    card.style.transform = 'translate(-50%, -50%)';
                }
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
            const left = ((e.clientX - rect.left) / rect.width) * 100;
            const top = ((e.clientY - rect.top) / rect.height) * 100;
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
            const left = startLeft + (dx / rect.width) * 100;
            const top = startTop + (dy / rect.height) * 100;
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
        const storageKey = 'popping-pos-' + (dashboard.dataset.you || '');
        localStorage.removeItem(storageKey);
        const table = document.getElementById('team-table');
        if (!table) return;
        const cards = [...table.querySelectorAll('.teammate-card')].filter(c => c.id !== 'card-you');
        const youCard = document.getElementById('card-you');
        // Teammates in an arc above "You"
        const cx = 50, cy = 55, rx = 42, ry = 32;
        const positions = {};
        cards.forEach((card, i) => {
            const angle = Math.PI - (i / Math.max(cards.length - 1, 1)) * Math.PI;
            const left = cx + rx * Math.cos(angle);
            const top = cy - ry * Math.sin(angle);
            card.style.left = left + '%';
            card.style.top = top + '%';
            card.style.transform = 'translate(-50%, -50%)';
            positions[card.dataset.studentId] = { left, top };
        });
        // You at bottom center
        if (youCard) {
            youCard.style.left = '50%';
            youCard.style.top = '85%';
            youCard.style.transform = 'translate(-50%, -50%)';
            positions['you'] = { left: 50, top: 85 };
        }
        saveCardPositions(positions);
    };

    function saveCardPositions(positions) {
        const storageKey = 'popping-pos-' + (dashboard.dataset.you || '');
        try { localStorage.setItem(storageKey, JSON.stringify(positions)); } catch(e) {}
    }

    // --- thumb grading on cards ---
    window.gradeTeammate = function(btn, recipientId, score) {
        const card = btn.closest('.teammate-card');
        card.querySelectorAll('.thumb-btn').forEach(b => {
            b.classList.remove('active-green', 'active-red');
        });
        if (score === 1) {
            btn.classList.add('active-green');
        } else {
            btn.classList.add('active-red');
        }
        submitPeerGrade(recipientId, score);
    };

    initTeammateCards();
    initDashboard();
}

/* ===== GLOBAL FUNCTIONS ===== */
let sessionTimerInterval = null;

window.randomAssign = async function() {
    if (!confirm('Assign all remaining students evenly to teams?')) return;
    const data = await postJSON('/api/random_assign', {});
    if (data.success) {
        if (data.assigned === 0) {
            alert('All students are already in teams.');
        } else {
            alert(`Assigned ${data.assigned} student(s) to teams.`);
        }
        window.location.reload();
    } else {
        alert(data.error || 'Assignment failed');
    }
};

/* ===== INSTRUCTOR PANEL ===== */
const instructor = document.querySelector('.instructor[data-slug]');
if (instructor) {
    // Render active question markdown on page load (competition phase)
    (async function renderInstructorActiveQuestion() {
        const qText = document.getElementById('active-question-text');
        if (!qText) return;
        try {
            const res = await fetch('/api/state');
            const state = await res.json();
            if (state.active_question) {
                lastRenderedQuestionKey = `${state.active_question.id}-${state.active_question.question_num}`;
                if (state.active_question.html_content) {
                    qText.innerHTML = state.active_question.html_content;
                } else {
                    const md = state.active_question.content || state.active_question.question_text || '';
                    qText.innerHTML = marked.parse(`#${state.active_question.question_num}: ${state.active_question.title || ''}\n\n${md}`);
                }
                if (window.MathJax) MathJax.typesetPromise([qText]);
                if (window.hljs) hljs.highlightAll();
            }
        } catch (e) {}
    })();

    setInterval(async () => {
        // Auto-refresh the team roster in SETUP phase
        const rosterGrid = document.querySelector('.roster-grid');
        if (!rosterGrid) return;
        const res = await fetch('/api/teams');
        if (!res.ok) return;
        const teams = await res.json();
        // Only update if this is the SETUP roster (not the filter dropdown)
        const card = rosterGrid.closest('.card');
        if (!card || !card.querySelector('h2')?.textContent?.includes('Team and Members')) return;
        rosterGrid.innerHTML = '';
        let unassignedCount = 0;
        teams.forEach(team => {
            const div = document.createElement('div');
            div.className = 'roster-team';
            const membersHtml = team.members.map(m => {
                const display = m.name && m.name !== m.student_id ? `${m.name} (${m.student_id})` : m.student_id;
                return `<div class="roster-member"><span class="team-dot" style="background:${team.color}"></span>${display}</div>`;
            }).join('');
            div.innerHTML = `<h3><span class="team-dot" style="background:${team.color}"></span>${team.name}</h3>${membersHtml || '<em>No members</em>'}`;
            rosterGrid.appendChild(div);
            unassignedCount += team.members.length;
        });
        // Update unassigned count
        const totalStudents = document.querySelectorAll('#student-table tr').length || document.querySelectorAll('.data-table tbody tr').length;
        const hint = card.querySelector('.hint');
        // Try to refresh the whole page if it seems stale
    }, 5000);

    // Competition-phase live state: update poll response count + poll status panel
    setInterval(async () => {
        const timerBox = document.getElementById('timer-box');
        if (!timerBox) return; // not on competition page
        try {
            const res = await fetch('/api/state');
            if (!res.ok) return;
            const state = await res.json();
            // Update poll response count
            const pollCount = document.getElementById('poll-count');
            if (pollCount) {
                pollCount.textContent = `${state.poll_count || 0} response${state.poll_count === 1 ? '' : 's'}`;
            }
            // Update active question markdown — only re-render when question changes
            const qText = document.getElementById('active-question-text');
            if (qText && state.active_question) {
                const qKey = `${state.active_question.id}-${state.active_question.question_num}`;
                if (qKey !== lastRenderedQuestionKey) {
                    lastRenderedQuestionKey = qKey;
                    const newHtml = state.active_question.html_content || marked.parse(`#${state.active_question.question_num}: ${state.active_question.title || ''}\n\n${state.active_question.content || state.active_question.question_text || ''}`);
                    qText.innerHTML = newHtml;
                    if (window.MathJax) MathJax.typesetPromise([qText]);
                    if (window.hljs) hljs.highlightAll();
                }
            } else if (qText) {
                qText.innerHTML = '';
                lastRenderedQuestionKey = null;
            }
            // Toggle poll status panel based on server state
            const ps = document.getElementById('poll-status');
            if (ps) {
                const btn = document.getElementById('btn-start-poll');
                const isGliding = btn && btn.classList.contains('gliding');
                // Show panel when poll is active (server-authoritative) or glide is running
                ps.style.display = (state.poll_active || isGliding) ? 'flex' : 'none';
            }
        } catch (e) { /* network blip — ignore */ }
    }, 3000);
}

let _originalMaxTeams = null;

window.startModifyTeams = function() {
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

window.toggleLock = async function() {
    const btn = document.activeElement || event.target;
    const isLocked = btn.textContent.includes('Unlock');
    if (isLocked) {
        await postJSON('/api/toggle_lock_teams', { locked: false });
    } else {
        await postJSON('/api/toggle_lock_teams', { locked: true });
    }
    window.location.reload();
};

window.toggleSessionTimer = async function() {
    const btn = document.getElementById('btn-session-timer');
    const isRunning = btn.textContent.includes('Stop');
    if (isRunning) {
        localStorage.removeItem('popping-session-start');
        await postJSON('/api/stop_session_timer', {});
    } else {
        localStorage.setItem('popping-session-start', Date.now().toString());
        await postJSON('/api/start_session_timer', {});
    }
    window.location.reload();
};

// ---- max members per team ----
let _originalMaxMembers = null;

window.startModifyMembers = function() {
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
        <button class="btn btn-sm btn-primary" onclick="confirmModifyMembers()">Confirm</button>
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
    const data = await postJSON('/api/set_max_members', { max_members: newMax });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to update max members');
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
    const data = await postJSON('/api/set_max_teams', { max_teams: newMax });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to update team count');
    }
};

window.setMaxTeams = async function() {
    const val = parseInt(document.getElementById('max-teams-input').value, 10);
    if (isNaN(val) || val < 1 || val > 20) {
        alert('Team count must be between 1 and 20');
        return;
    }
    const data = await postJSON('/api/set_max_teams', { max_teams: val });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to update team count');
    }
};

window.setPhase = async function(phase) {
    await postJSON('/api/set_phase', { phase });
    window.location.reload();
};

window.setActiveTeam = async function(teamId) {
    await postJSON('/api/set_active_team', { team_id: teamId });
    window.location.reload();
};

window.setQuestion = async function() {
    const q = document.getElementById('question-input')?.value || '';
    await postJSON('/api/set_question', { question: q });
    alert('Question updated');
};

/* ===== STUDENT TABLE (paginated) ===== */
let studentSort = 'student_id';
let studentOrder = 'asc';
let studentPage = 1;
let teamFilterVal = '';

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

window.loadStudentTable = async function() {
    const search = document.getElementById('student-search')?.value || '';
    const params = new URLSearchParams({
        page: studentPage, per_page: 10, sort: studentSort, order: studentOrder, search, team: teamFilterVal
    });
    const res = await fetch('/api/students?' + params);
    const data = await res.json();
    window._lastTeams = data.teams || [];

    const tbody = document.getElementById('student-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    const teams = data.teams || [];

    data.students.forEach(s => {
        const teamHtml = s.team_name
            ? `<span class="team-tag clickable" style="background:${s.team_color || '#ccc'}" onclick="toggleTeamPicker(event, ${s.id})" data-student="${s.id}">${s.team_name}</span>`
            : `<span class="team-tag unassigned-tag clickable" onclick="toggleTeamPicker(event, ${s.id})" data-student="${s.id}">Unassigned</span>`;
        const statusHtml = s.is_online
            ? '<span class="online-dot"></span> Online'
            : '<span class="offline-dot"></span> Offline';
        const loginTime = s.last_login_at ? s.last_login_at.substring(0, 19) : '—';
        const name = s.name || s.student_id;

        const tr = document.createElement('tr');
        tr.setAttribute('data-id', s.id);
        tr.innerHTML = `
            <td>${s.student_id}</td>
            <td>${name}</td>
            <td>${teamHtml}</td>
            <td>${statusHtml}</td>
            <td class="text-muted small">${loginTime}</td>
            <td><button class="btn btn-sm btn-danger" onclick="removeStudent(${s.id})">Remove</button></td>
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
        html += `<div class="team-picker-item" style="border-left:3px solid ${t.color}" onclick="pickTeam(${studentId}, ${t.id})">${t.name}</div>`;
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
    document.querySelectorAll('.team-picker').forEach(el => el.remove());
    await postJSON('/api/assign_student', { student_id: studentId, team_id: teamId });
    loadStudentTable();
};

window.addStudent = async function() {
    const id = document.getElementById('new-student-id').value.trim();
    const name = document.getElementById('new-name').value.trim();
    const pin = document.getElementById('new-pin').value.trim();
    if (!id || !pin) { alert('Student ID and PIN are required'); return; }
    const data = await postJSON('/api/add_student', { student_id: id, name, pin });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to add student');
    }
};

/* ===== DISCUSSION QUESTIONS (weekly .md files) ===== */

// Populate setup week selector and discussion loader
async function initWeekSelector() {
    const res = await fetch('/api/discussion_questions');
    const data = await res.json();

    // Setup page week selector
    const setupSel = document.getElementById('setup-week-select');
    if (setupSel && data.weeks.length > 0) {
        setupSel.innerHTML = data.weeks.map(w =>
            `<option value="${w.num}" ${w.num === data.current_week ? 'selected' : ''}>Week ${w.num}</option>`
        ).join('');
    }

    // Discussion page: auto-load questions for current week
    const container = document.getElementById('disc-questions-list');
    if (container) {
        renderDiscussionQuestions(data);
    }
}

function renderDiscussionQuestions(data) {
    const container = document.getElementById('disc-questions-list');
    if (!container) return;

    if (!data.questions || data.questions.length === 0) {
        container.innerHTML = '<p class="empty">No questions found. Add <code>week-N-questions.md</code> files in the course folder, then select a week during setup.</p>';
        return;
    }

    container.innerHTML = data.questions.map((q, i) => `
        <div class="disc-question-card">
            <div class="disc-question-header" onclick="this.parentElement.classList.toggle('expanded')">
                <span class="disc-question-num">#${i + 1}</span>
                <span class="disc-question-title">${q.title}</span>
                <button class="btn btn-sm btn-primary" onclick="event.stopPropagation(); postActiveQuestion('${q.title.replace(/'/g, "\\'")}')">Post to Class</button>
            </div>
            <div class="disc-question-body">
                <div class="markdown-content">${marked.parse(q.content)}</div>
            </div>
        </div>
    `).join('');

    if (window.MathJax) MathJax.typesetPromise([container]);
    if (window.hljs) container.querySelectorAll('pre code').forEach(hljs.highlightElement);
}

window.setDiscussionWeek = async function() {
    const week = document.getElementById('setup-week-select')?.value;
    if (!week) return;
    await postJSON('/api/set_discussion_week', { week: parseInt(week) });
};

window.postActiveQuestion = async function(title) {
    await postJSON('/api/set_question', { question: title });
    alert('Question posted: ' + title);
};

// Load on page load
if (document.getElementById('setup-week-select') || document.getElementById('disc-questions-list')) {
    initWeekSelector();
}

window.addQuestion = async function() {
    const text = document.getElementById('new-question').value.trim();
    if (!text) { alert('Question text required'); return; }
    const data = await postJSON('/api/questions', { question_text: text });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to add question');
    }
};

window.deleteQuestion = async function(qid) {
    if (!confirm('Delete this question?')) return;
    await deleteJSON('/api/questions/' + qid);
    window.location.reload();
};

window.startPresentation = async function() {
    const teamId = document.getElementById('comp-team').value;
    const questionId = document.getElementById('comp-question').value;
    const timeCap = parseInt(document.getElementById('time-cap')?.value || '300', 10);
    if (!teamId || !questionId) { alert('Select both a team and a question'); return; }
    const data = await postJSON('/api/start_presentation', {
        team_id: parseInt(teamId, 10),
        question_id: parseInt(questionId, 10),
        time_cap: timeCap,
        question_text: document.getElementById('comp-question').selectedOptions[0].text
    });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to start presentation');
    }
};

window.stopPresentation = async function() {
    const data = await postJSON('/api/stop_presentation', {});
    if (data.success) {
        const btnPause = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnPause) btnPause.style.display = 'none';
        if (btnResume) btnResume.style.display = '';
        stopCountdownTimer();
        const tv = document.getElementById('timer-value');
        if (tv && data.remaining != null) tv.textContent = formatDuration(data.remaining);
    }
};

window.resumePresentation = async function() {
    const data = await postJSON('/api/resume_presentation', {});
    if (data.success) {
        const btnPause = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnPause) btnPause.style.display = '';
        if (btnResume) btnResume.style.display = 'none';
        // Start countdown locally — no page reload needed
        startCountdownTimer(data.remaining);
    }
};

window.resetTimer = async function() {
    if (!confirm('Reset timer to full time?')) return;
    const data = await postJSON('/api/reset_presentation_timer', {});
    if (data.success) {
        const btnPause = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnPause) btnPause.style.display = 'none';
        if (btnResume) btnResume.style.display = '';
        stopCountdownTimer();
        const tv = document.getElementById('timer-value');
        if (tv) tv.textContent = formatDuration(data.cap);
    }
};

window.nextPresentation = async function() {
    await postJSON('/api/next_presentation', {});
    window.location.reload();
};

// ===== POLL GLIDE (instructor) / POLL PULSE (student) =====

let pollGlideTimeout = null;

/**
 * Instructor: animate a left→right fill across the Start Poll button for the
 * poll duration.  Called on click (client-side) or restored from server
 * state on page load (synced to the real start time).
 */
function startPollGlide(startedAtIso, durationSec) {
    const btn = document.getElementById('btn-start-poll');
    if (!btn) return;
    const glide = btn.querySelector('.btn-glide');
    const label = btn.querySelector('.btn-label');
    if (!glide) return;

    btn.classList.add('gliding');
    if (label) label.textContent = '⏳ Poll Active';

    const startTime = new Date(startedAtIso.replace(' ', 'T') + 'Z').getTime();
    const totalMs = durationSec * 1000;

    function step() {
        const elapsed = Date.now() - startTime;
        const pct = Math.min(100, (elapsed / totalMs) * 100);
        glide.style.width = pct + '%';

        const remaining = Math.max(0, Math.ceil((totalMs - elapsed) / 1000));
        if (label) label.textContent = `⏳ ${remaining}s`;

        if (pct >= 100) {
            btn.classList.remove('gliding');
            glide.style.width = '0%';
            if (label) label.textContent = '⭐ Start Poll';
            if (pollGlideTimeout) { clearTimeout(pollGlideTimeout); pollGlideTimeout = null; }
            return;
        }
        pollGlideTimeout = setTimeout(step, 200);
    }
    step();
}

function stopPollGlide() {
    const btn = document.getElementById('btn-start-poll');
    if (!btn) return;
    const glide = btn.querySelector('.btn-glide');
    const label = btn.querySelector('.btn-label');
    btn.classList.remove('gliding');
    if (glide) glide.style.width = '0%';
    if (label) label.textContent = '⭐ Start Poll';
    if (pollGlideTimeout) { clearTimeout(pollGlideTimeout); pollGlideTimeout = null; }
}

/**
 * Student: pulsing countdown badge on the grading card during the 30s poll.
 */
let pollPulseInterval = null;

function startPollPulse(startedAtIso, durationSec) {
    const pulse = document.getElementById('poll-pulse');
    const pulseText = document.getElementById('poll-pulse-text');
    if (!pulse) return;

    if (pollPulseInterval) clearInterval(pollPulseInterval);
    pulse.style.display = 'flex';

    const startTime = new Date(startedAtIso.replace(' ', 'T') + 'Z').getTime();
    const totalMs = durationSec * 1000;

    function tick() {
        const elapsed = Date.now() - startTime;
        const remaining = Math.max(0, Math.ceil((totalMs - elapsed) / 1000));
        if (pulseText) pulseText.textContent = `⏱ ${remaining}s left to rate!`;

        if (remaining <= 0) {
            clearInterval(pollPulseInterval);
            pollPulseInterval = null;
            pulse.style.display = 'none';
        }
    }
    tick();
    pollPulseInterval = setInterval(tick, 250);
}

function stopPollPulse() {
    const pulse = document.getElementById('poll-pulse');
    if (pulse) pulse.style.display = 'none';
    if (pollPulseInterval) { clearInterval(pollPulseInterval); pollPulseInterval = null; }
}

// ===== POLL API =====

window.startPoll = async function() {
    const btn = document.getElementById('btn-start-poll');
    if (btn && btn.classList.contains('gliding')) return; // already running
    const data = await postJSON('/api/start_poll', {});
    if (data.success && data.poll_started_at) {
        startPollGlide(data.poll_started_at, data.poll_duration || 30);
        // Reveal the Stop-Poll panel so the instructor can cancel early
        const ps = document.getElementById('poll-status');
        if (ps) ps.style.display = 'flex';
    } else {
        alert(data.error || 'Failed to start poll');
    }
};

window.stopPoll = async function() {
    const data = await postJSON('/api/stop_poll', {});
    if (data.success) {
        stopPollGlide();
    } else {
        alert(data.error || 'Failed to stop poll');
    }
};

window.submitRating = async function() {
    if (!pollSelections.q1 || !pollSelections.q2) {
        alert('Please rate both questions');
        return;
    }
    const btn = document.getElementById('btn-submit-rating');
    if (btn) btn.disabled = true;
    const data = await postJSON('/api/submit_rating', {
        q1_developed: pollSelections.q1,
        q2_easy: pollSelections.q2
    });
    if (data.success) {
        if (btn) {
            btn.textContent = '✓ Submitted';
            btn.classList.add('btn-submitted');
        }
        ratingSubmitted = true;
    } else {
        if (btn) btn.disabled = false;
        alert(data.error || 'Failed to submit rating');
    }
};

// Star interaction
function initStarRows() {
    document.querySelectorAll('.star-row').forEach(row => {
        const question = row.dataset.question;
        const stars = row.querySelectorAll('.star');
        stars.forEach(star => {
            star.addEventListener('click', () => {
                const val = parseInt(star.dataset.value, 10);
                pollSelections[question] = val;
                stars.forEach(s => {
                    s.classList.toggle('active', parseInt(s.dataset.value, 10) <= val);
                    s.textContent = parseInt(s.dataset.value, 10) <= val ? '★' : '☆';
                });
            });
        });
    });
}

// ===== DISCUSSION SIGN-UP =====

window.togglePresent = async function(questionKey, btn) {
    const data = await postJSON('/api/toggle_present', { question_key: questionKey });
    if (data.success) {
        btn.classList.toggle('selected', data.selected);
        btn.textContent = data.selected ? 'Signed Up' : 'Sign Up';
        // Refresh counts
        const res = await fetch('/api/discussion_questions');
        const d = await res.json();
        renderDiscussionQuestions(d);
    }
};

// ===== UNASSIGN ALL =====

window.unassignAll = async function() {
    if (!confirm('Clear all team assignments?')) return;
    const data = await postJSON('/api/unassign_all', {});
    if (data.success) window.location.reload();
};

// ===== APPENDIX QUESTIONS =====

window.addAppendixQuestion = async function() {
    const title = document.getElementById('appendix-title')?.value.trim();
    const content = document.getElementById('appendix-content')?.value.trim();
    if (!title || !content) { alert('Title and content required'); return; }
    const data = await postJSON('/api/questions', { title, content });
    if (data.success) {
        document.getElementById('appendix-title').value = '';
        document.getElementById('appendix-content').value = '';
        window.location.reload();
    } else {
        alert(data.error || 'Failed to add question');
    }
};

window.deleteAppendixQuestion = async function(index) {
    if (!confirm('Delete this appendix question?')) return;
    const data = await postJSON('/api/delete_appendix_question', { index });
    if (data.success) window.location.reload();
};

window.removeStudent = async function(studentDbId) {
    if (!confirm('Remove this student?')) return;
    await deleteJSON('/api/remove_student/' + studentDbId);
    window.location.reload();
};

window.resetData = async function() {
    if (!confirm('This will delete ALL grades and reset teams for this course. Are you sure?')) return;
    await postJSON('/api/reset_data', {});
    window.location.reload();
};

// ===== PRESENTATION TIMER =====
let timerInterval = null;

function formatDuration(seconds) {
    const m = Math.floor(seconds / 60).toString().padStart(2, '0');
    const s = Math.floor(seconds % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
}

let countdownTimerInterval = null;

function startCountdownTimer(remainingSeconds) {
    if (countdownTimerInterval) clearInterval(countdownTimerInterval);
    let remaining = remainingSeconds;
    const tick = () => {
        document.querySelectorAll('.timer-value').forEach(el => {
            el.textContent = formatDuration(Math.max(0, remaining));
        });
        if (remaining <= 0) {
            clearInterval(countdownTimerInterval);
            countdownTimerInterval = null;
        }
        remaining--;
    };
    tick();
    countdownTimerInterval = setInterval(tick, 1000);
}

function stopCountdownTimer() {
    if (countdownTimerInterval) {
        clearInterval(countdownTimerInterval);
        countdownTimerInterval = null;
    }
}

function startTimer(startedAt, capOverride) {
    // FIX: append 'Z' — SQLite CURRENT_TIMESTAMP is UTC
    const startTime = new Date(startedAt.replace(' ', 'T') + 'Z').getTime();
    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    let cap;
    if (capOverride != null) {
        cap = parseInt(capOverride, 10) || 300;
    } else {
        const instructorEl = document.querySelector('.instructor[data-slug]');
        cap = instructorEl ? parseInt(instructorEl.dataset.presentationTimeCap || '300', 10) : 300;
    }
    startCountdownTimer(Math.max(0, cap - elapsed));
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
    const presCap = parseInt(instructorEl.dataset.presentationTimeCap || '300', 10);
    if (presStarted) {
        // FIX: append 'Z' — SQLite CURRENT_TIMESTAMP is UTC
        const startTime = new Date(presStarted.replace(' ', 'T') + 'Z').getTime();
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        startCountdownTimer(Math.max(0, presCap - elapsed));
        const btnStop = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnStop) btnStop.style.display = '';
        if (btnResume) btnResume.style.display = 'none';
    } else if (presRemaining) {
        document.getElementById('timer-value').textContent = formatDuration(parseInt(presRemaining, 10));
        const btnStop = document.getElementById('btn-pause');
        const btnResume = document.getElementById('btn-resume');
        if (btnStop) btnStop.style.display = 'none';
        if (btnResume) btnResume.style.display = '';
    }
    // Session timer
    const sessionStartDb = instructorEl.dataset.sessionStarted;
    const sessionStartLs = localStorage.getItem('popping-session-start');
    if (sessionStartDb) {
        startSessionTimer(new Date(sessionStartDb.replace(' ', 'T') + 'Z').getTime());
    } else if (sessionStartLs) {
        startSessionTimer(parseInt(sessionStartLs, 10));
    } else {
        const st = document.getElementById('session-timer');
        if (st) st.style.display = 'none';
    }
    // Poll status — restore gliding fill if poll is active on page load
    const pollStatus = document.getElementById('poll-status');
    const pollStarted = instructorEl.dataset.pollStarted;
    const pollActive = instructorEl.dataset.pollActive === '1';
    if (pollActive && pollStarted) {
        startPollGlide(pollStarted, 30);
    }
    if (pollStatus && pollActive) {
        pollStatus.style.display = 'flex';
    }
    // History
    renderHistory();
}

function startSessionTimer(startMs) {
    if (sessionTimerInterval) clearInterval(sessionTimerInterval);
    const tick = () => {
        const elapsed = Math.floor((Date.now() - startMs) / 1000);
        const el = document.getElementById('session-timer-value');
        if (el) el.textContent = formatDuration(elapsed);
    };
    tick();
    sessionTimerInterval = setInterval(tick, 1000);
}


// ===== THEME TOGGLE =====
(function() {
    const saved = localStorage.getItem('popping-theme');
    if (saved === 'light') {
        document.documentElement.classList.add('light');
    }
    updateThemeIcon();
})();

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
        return;
    }
    container.innerHTML = history.map(h => `
        <div class="history-item">
            <span>${h.title || 'Untitled'} — <strong>${h.team || '?'}</strong></span>
            <span>${h.responses || 0} responses</span>
        </div>
    `).join('');
}

// ===== DISCUSSION QUESTIONS RENDER (enhanced with sign-ups) =====
function renderDiscussionQuestions(data) {
    const container = document.getElementById('disc-questions-list');
    if (!container) return;

    if (!data.questions || data.questions.length === 0) {
        container.innerHTML = '<p class="empty">No questions found. Add <code>week-N-questions.md</code> files in the course folder, then select a week during setup.</p>';
        return;
    }

    const isInstructor = !!document.querySelector('.instructor[data-slug]');

    container.innerHTML = data.questions.map((q, i) => {
        const key = q._key || `week-${data.current_week}-q${i}`;
        const presenting = q.teams_presenting || 0;
        const presenters = q.presenters || 0;
        const selected = q.i_selected ? 'selected' : '';
        const btn = isInstructor
            ? `<span class="presenting-badge">${presenting} team${presenting !== 1 ? 's' : ''}</span>`
            : `<button class="toggle-present-btn ${selected}" onclick="togglePresent('${key}', this)">${q.i_selected ? 'Signed Up' : 'Sign Up'}</button>`;
        return `
        <div class="disc-question-card">
            <div class="disc-question-header" onclick="this.parentElement.classList.toggle('expanded')">
                <span class="disc-question-num">#${i + 1}</span>
                <span class="disc-question-title">${q.title}</span>
                ${btn}
            </div>
            <div class="disc-question-body">
                <div class="markdown-content">${marked.parse(q.content)}</div>
            </div>
        </div>
    `}).join('');

    if (window.MathJax) MathJax.typesetPromise([container]);
    if (window.hljs) container.querySelectorAll('pre code').forEach(hljs.highlightElement);
}
