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

/* ===== LOGIN PAGE ===== */
if (document.querySelector('.login-form')) {
    // nothing special needed
}

/* ===== STUDENT DASHBOARD ===== */
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

    async function joinTeam(teamId) {
        const data = await postJSON('/api/join_team', { team_id: teamId });
        if (data.success) {
            window.location.reload();
        } else {
            alert(data.error || 'Failed to join team');
        }
    }

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
                return `<div class="roster-member">
                    <span class="team-dot" style="background:${team.color}"></span>
                    <span class="${isYou ? 'you' : ''}">${m.name} ${isYou ? '(You)' : ''}</span>
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
            badge.textContent = 'Phase: ' + state.phase.toUpperCase();
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
        const peerSec = document.getElementById('peer-section');
        if (peerSec) {
            peerSec.style.display = PHASES_WITH_PEER.includes(state.phase) ? 'block' : 'none';
            if (PHASES_WITH_PEER.includes(state.phase)) loadPeerGraders();
        }
        const teamSec = document.getElementById('team-section');
        if (teamSec) {
            const show = PHASES_WITH_TEAM.includes(state.phase);
            teamSec.style.display = show ? 'block' : 'none';
            if (show) {
                const banner = document.getElementById('presenting-name');
                if (banner && state.active_team) {
                    banner.textContent = state.active_team.name;
                    banner.style.color = state.active_team.color;
                }
                loadTeamGraders(state.active_team);
            }
        }
    }

    async function loadPeerGraders() {
        const res = await fetch('/api/teams');
        const teams = await res.json();
        const container = document.getElementById('peer-graders');
        if (!container || container.dataset.loaded) return;

        container.innerHTML = '';
        const myId = dashboard.dataset.you;
        const myTeam = teams.find(t => t.members.some(m => m.student_id === myId));
        if (!myTeam) return;

        myTeam.members.forEach(m => {
            if (m.student_id === myId) return;
            const row = document.createElement('div');
            row.className = 'grade-row';
            row.innerHTML = `
                <span class="grade-name">${m.name}</span>
                <input type="number" min="1" max="10" class="grade-input"
                       data-recipient="${m.student_id}" placeholder="1-10"
                       onchange="submitPeerGrade('${m.student_id}', this.value)">
            `;
            container.appendChild(row);
        });
        container.dataset.loaded = 'true';
    }

    async function loadTeamGraders(activeTeam) {
        const container = document.getElementById('team-graders');
        if (!container || !activeTeam) return;
        if (container.dataset.teamId === String(activeTeam.id)) return;

        container.innerHTML = '';
        const criteria = ['Content', 'Delivery', 'Creativity', 'Teamwork'];
        criteria.forEach(crit => {
            const row = document.createElement('div');
            row.className = 'grade-row';
            row.innerHTML = `
                <span class="grade-name">${crit}</span>
                <input type="number" min="1" max="10" class="grade-input"
                       data-criterion="${crit}" placeholder="1-10"
                       onchange="submitTeamGrade(${activeTeam.id}, '${crit}', this.value)">
            `;
            container.appendChild(row);
        });
        container.dataset.teamId = activeTeam.id;
    }

    window.submitPeerGrade = async function(recipientId, score) {
        await postJSON('/api/grade_peer', { recipient_id: recipientId, criterion: 'overall', score });
    };

    window.submitTeamGrade = async function(recipientTeamId, criterion, score) {
        await postJSON('/api/grade_team', { recipient_team_id: recipientTeamId, criterion, score });
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
        container.innerHTML = rows.slice(0, 20).map(r => `
            <div class="discussion-item">
                <div class="meta">${r.name} · ${r.team_name || 'No team'} · ${r.created_at}</div>
                <div>${escapeHtml(r.response)}</div>
            </div>
        `).join('');
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    initDashboard();
}

/* ===== INSTRUCTOR PANEL ===== */
const instructor = document.querySelector('.instructor[data-course-id]');
if (instructor) {
    setInterval(async () => {
        const res = await fetch('/api/teams');
        if (res.ok) {
            const teams = await res.json();
            // Could update roster live here if desired
        }
    }, 3000);
}

window.setPhase = async function(phase) {
    const courseId = getCourseId();
    await postJSON('/api/set_phase', { course_id: courseId, phase });
    window.location.reload();
};

window.setActiveTeam = async function(teamId) {
    const courseId = getCourseId();
    await postJSON('/api/set_active_team', { course_id: courseId, team_id: teamId });
    window.location.reload();
};

window.setQuestion = async function() {
    const courseId = getCourseId();
    const q = document.getElementById('question-input')?.value || '';
    await postJSON('/api/set_question', { course_id: courseId, question: q });
    alert('Question updated');
};

window.addStudent = async function() {
    const courseId = getCourseId();
    const id = document.getElementById('new-student-id').value.trim();
    const name = document.getElementById('new-name').value.trim();
    const pin = document.getElementById('new-pin').value.trim();
    if (!id || !name || !pin) { alert('All fields required'); return; }
    const data = await postJSON('/api/add_student', { course_id: courseId, student_id: id, name, pin });
    if (data.success) {
        window.location.reload();
    } else {
        alert(data.error || 'Failed to add student');
    }
};

window.removeStudent = async function(studentDbId) {
    if (!confirm('Remove this student?')) return;
    await deleteJSON('/api/remove_student/' + studentDbId);
    window.location.reload();
};

window.resetData = async function() {
    const courseId = getCourseId();
    if (!confirm('This will delete ALL grades and reset teams for this course. Are you sure?')) return;
    await postJSON('/api/reset_data', { course_id: courseId });
    window.location.reload();
};
