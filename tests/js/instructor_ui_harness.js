'use strict';

/* Executable regressions for instructor-only UI reconciliation. */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(__dirname, '..', '..', 'static', 'js', 'app.js');
const INSTRUCTOR_TEMPLATE = path.join(
    __dirname, '..', '..', 'templates', 'instructor.html'
);
const source = fs.readFileSync(APP_JS, 'utf8');
const instructorTemplate = fs.readFileSync(INSTRUCTOR_TEMPLATE, 'utf8');

function sourceSlice(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert(start >= 0 && end > start, `missing source slice: ${startMarker}`);
    return source.slice(start, end);
}

async function testRosterMutationSerializationAndPickerScope() {
    let resolvePost;
    let postCalls = 0;
    let refreshCalls = 0;
    const toastMessages = [];
    const dynamicPicker = { removed: 0, remove() { this.removed += 1; } };
    const permanentFilter = { removed: 0, remove() { this.removed += 1; } };
    const selectors = [];
    const instructor = {
        dataset: {
            phase: 'setup',
            sessionKey: '4',
            rosterVersion: '9',
            pollQuestionKey: '',
        },
    };
    const sandbox = {
        console,
        instructor,
        document: {
            querySelectorAll(selector) {
                selectors.push(selector);
                if (selector === '.team-picker:not(.filter-picker)') {
                    return [dynamicPicker];
                }
                if (selector === '.team-picker') {
                    return [dynamicPicker, permanentFilter];
                }
                return [];
            },
        },
        window: {},
        showToast(message) { toastMessages.push(message); },
        showInstructorMutationError() {},
        refreshRosterInPlace() { refreshCalls += 1; },
        instructorStatePayload(extra) { return { ...extra }; },
        postJSON() {
            postCalls += 1;
            return new Promise(resolve => { resolvePost = resolve; });
        },
    };
    vm.createContext(sandbox);
    vm.runInContext('let rosterMutationInProgress = false;', sandbox);
    vm.runInContext(sourceSlice(
        'function applyRosterMutationVersion(data) {',
        '// Setup-phase roster mutations'
    ), sandbox, { filename: 'app-roster-helpers.js' });
    vm.runInContext(sourceSlice(
        'window.pickTeam = async function(studentId, teamId) {',
        'window.addStudent = async function(btn) {'
    ), sandbox, { filename: 'app-pick-team.js' });

    const first = sandbox.window.pickTeam(11, 2);
    await sandbox.window.pickTeam(12, 3);
    assert.strictEqual(postCalls, 1, 'overlapping roster edits must be serialized');
    assert(toastMessages.some(message => /still saving/i.test(message)));
    resolvePost({ success: true, roster_version: 10 });
    await first;

    assert.deepStrictEqual(selectors, ['.team-picker:not(.filter-picker)']);
    assert.strictEqual(dynamicPicker.removed, 1);
    assert.strictEqual(permanentFilter.removed, 0);
    assert.strictEqual(instructor.dataset.rosterVersion, '10');
    assert.strictEqual(refreshCalls, 1);
}

async function testStudentTableKeepsCourseTotalSeparateFromFilterTotal() {
    const instructor = { dataset: { studentCount: '40' } };
    const tbody = { innerHTML: '', appendChild() {}, querySelector() { return null; } };
    const pagination = { innerHTML: '', parentElement: { insertBefore() {} } };
    const elements = {
        'student-search': { value: 'matching filter' },
        'student-tbody': tbody,
        'student-pagination': pagination,
    };
    const sandbox = {
        console,
        URLSearchParams,
        instructor,
        isDemoInstructor: false,
        window: { _lastTeams: [], clearTimeout() {}, setTimeout() { return 1; } },
        document: {
            getElementById(id) { return elements[id] || null; },
            querySelector(selector) {
                if (selector === '.instructor[data-student-count]') return instructor;
                return null;
            },
            querySelectorAll() { return []; },
            createElement() { return { setAttribute() {}, innerHTML: '' }; },
        },
        fetchJSONWithTimeout: async () => ({
            res: { status: 200, ok: true },
            data: {
                course_total: 40,
                total: 3,
                total_pages: 1,
                page: 1,
                students: [],
                teams: [],
            },
        }),
        redirectToSessionEnded() {},
        showToast() {},
        escapeAttrValue(value) { return value; },
        escapeHtmlValue(value) { return value; },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        "let studentPage = 1;" +
        "let studentPerPage = 10;" +
        "let studentSort = 'student_id';" +
        "let studentOrder = 'asc';" +
        "let teamFilterVal = '2';" +
        "let _studentTableLoading = false;" +
        "let _studentTableLoadFailed = false;" +
        "let _studentTableRetryTimer = null;",
        sandbox
    );
    vm.runInContext(sourceSlice(
        'window.loadStudentTable = async function() {',
        'window.sortStudents = function(col) {'
    ), sandbox, { filename: 'app-student-table.js' });

    await sandbox.window.loadStudentTable();
    assert.strictEqual(
        instructor.dataset.studentCount,
        '40',
        'capacity calculations must retain the unfiltered course total'
    );
    assert.match(
        pagination.innerHTML,
        />3 students</,
        'pagination must report the filtered result total'
    );
}

function testPermanentTeamFilterAccessibility() {
    const filterStart = instructorTemplate.indexOf('id="team-filter-dropdown"');
    const filterEnd = instructorTemplate.indexOf('<div class="table-wrapper">', filterStart);
    assert(filterStart >= 0 && filterEnd > filterStart);
    const filterMarkup = instructorTemplate.slice(filterStart, filterEnd);
    assert(filterMarkup.includes('id="team-filter-trigger"'));
    assert(filterMarkup.includes('aria-controls="team-filter-menu"'));
    assert(filterMarkup.includes('aria-expanded="false"'));
    assert.strictEqual(
        (filterMarkup.match(/role="button" tabindex="0"/g) || []).length,
        3,
        'all permanent filter option templates must be keyboard reachable'
    );
    assert.strictEqual(
        (filterMarkup.match(/onkeydown="teamFilterOptionKeydown/g) || []).length,
        3
    );

    let closeHandler = null;
    let loadCalls = 0;
    let focusCalls = 0;
    const triggerAttributes = { 'aria-expanded': 'false' };
    const trigger = {
        setAttribute(name, value) { triggerAttributes[name] = value; },
        focus() { focusCalls += 1; },
    };
    const menu = { style: { display: 'none' } };
    const label = { textContent: 'All' };
    const sandbox = {
        console,
        window: {},
        document: {
            getElementById(id) {
                if (id === 'team-filter-trigger') return trigger;
                if (id === 'team-filter-menu') return menu;
                if (id === 'team-filter-label') return label;
                return null;
            },
            addEventListener(type, handler) {
                if (type === 'click') closeHandler = handler;
            },
            removeEventListener() {},
        },
        setTimeout(handler) { handler(); return 1; },
        loadStudentTable() { loadCalls += 1; },
    };
    vm.createContext(sandbox);
    vm.runInContext("let teamFilterVal = ''; let studentPage = 3;", sandbox);
    vm.runInContext(sourceSlice(
        'function setTeamFilterExpanded(expanded) {',
        'let _searchDebounce = null;'
    ), sandbox, { filename: 'app-team-filter.js' });

    sandbox.window.toggleTeamFilter({ stopPropagation() {} });
    assert.strictEqual(menu.style.display, 'block');
    assert.strictEqual(triggerAttributes['aria-expanded'], 'true');
    assert.strictEqual(typeof closeHandler, 'function');
    closeHandler();
    assert.strictEqual(menu.style.display, 'none');
    assert.strictEqual(triggerAttributes['aria-expanded'], 'false');

    sandbox.window.toggleTeamFilter({ stopPropagation() {} });
    let prevented = false;
    let stopped = false;
    sandbox.window.teamFilterOptionKeydown({
        key: ' ',
        preventDefault() { prevented = true; },
        stopPropagation() { stopped = true; },
    }, 'none', 'Unassigned');
    assert.strictEqual(prevented, true);
    assert.strictEqual(stopped, true);
    assert.strictEqual(vm.runInContext('teamFilterVal', sandbox), 'none');
    assert.strictEqual(vm.runInContext('studentPage', sandbox), 1);
    assert.strictEqual(label.textContent, 'Unassigned');
    assert.strictEqual(menu.style.display, 'none');
    assert.strictEqual(triggerAttributes['aria-expanded'], 'false');
    assert.strictEqual(focusCalls, 1);
    assert.strictEqual(loadCalls, 1);
}

function testPresentationTransitionsReconcileInPlace() {
    const instructor = {
        dataset: {
            phase: 'competition',
            sessionKey: '4',
            discussionWeek: '1',
            teamsLocked: '0',
            maxTeams: '8',
            maxMembers: '5',
            activeTeamId: '2',
            activeQuestionId: '10',
            pollQuestionKey: 'presentation-old',
        },
    };
    const elements = {
        'presentation-active': { style: {} },
        'presentation-idle': { style: {} },
        'active-team-name': { textContent: '' },
        'active-question-text': { dataset: {} },
        'poll-count': { textContent: '' },
    };
    const sandbox = {
        console,
        instructor,
        document: { getElementById(id) { return elements[id] || null; } },
        stopCountdownTimer() {},
        stopPollGlide() {},
        setTimerExpired() {},
        markPresentedCompQuestionOptions() {},
    };
    vm.createContext(sandbox);
    vm.runInContext(
        "let _instrKnownQuestionId = 10;" +
        "let _instrKnownQuestionRevision = 'r1';" +
        "let lastRenderedQuestionKey = '10:r1';",
        sandbox
    );
    vm.runInContext(sourceSlice(
        'function instructorStructureChanged(state) {',
        '// Mirror the server-rendered'
    ), sandbox, { filename: 'app-presentation-reconcile.js' });

    sandbox.nextState = {
        phase: 'competition',
        session_key: 4,
        poll_question_key: 'presentation-new',
        active_team: { id: 2, name: 'Team 2' },
        active_question: { id: 10, revision: 'r1' },
    };
    assert.strictEqual(
        vm.runInContext('instructorStructureChanged(nextState)', sandbox),
        false,
        'a presentation key change alone must not force a structural reload'
    );
    assert.strictEqual(
        vm.runInContext('instructorPresentationChanged(nextState)', sandbox),
        true
    );
    assert.strictEqual(
        vm.runInContext('reconcilePresentationBlocks(nextState)', sandbox),
        true
    );
    assert.strictEqual(instructor.dataset.pollQuestionKey, 'presentation-new');
    assert.strictEqual(
        vm.runInContext('lastRenderedQuestionKey', sandbox),
        '10:r1',
        'same-question compact content must remain cached'
    );

    sandbox.nextState = {
        phase: 'competition',
        session_key: 4,
        poll_question_key: 'presentation-next',
        active_team: { id: 3, name: 'Team 3' },
        active_question: { id: 11, revision: 'r2' },
    };
    vm.runInContext('reconcilePresentationBlocks(nextState)', sandbox);
    assert.strictEqual(vm.runInContext('_instrKnownQuestionId', sandbox), null);
    assert.strictEqual(vm.runInContext('lastRenderedQuestionKey', sandbox), null);

    sandbox.nextState = {
        phase: 'competition',
        session_key: 4,
        poll_question_key: null,
        active_team: null,
        active_question: null,
        presentation_history: [],
    };
    vm.runInContext('reconcilePresentationBlocks(nextState)', sandbox);
    assert.strictEqual(instructor.dataset.pollQuestionKey, '');
    assert.strictEqual(elements['presentation-active'].style.display, 'none');
    assert.strictEqual(elements['presentation-idle'].style.display, '');
}

(async () => {
    await testRosterMutationSerializationAndPickerScope();
    await testStudentTableKeepsCourseTotalSeparateFromFilterTotal();
    testPermanentTeamFilterAccessibility();
    testPresentationTransitionsReconcileInPlace();
    console.log('instructor UI behavior: ok');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
