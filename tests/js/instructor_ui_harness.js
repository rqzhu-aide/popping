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

function testRosterMergeModalRejectsLegacyAndLocksDuringApply() {
    const toasts = [];
    const classes = new Set();
    const activeElement = {
        isConnected: true,
        focused: 0,
        focus() { this.focused += 1; },
    };
    const dialog = {
        hidden: true,
    };
    const summary = { innerHTML: '' };
    const input = { value: 'selected' };
    const applyButton = {
        focused: 0,
        focus() { this.focused += 1; },
    };
    const elements = {
        'roster-preview-dialog': dialog,
        'roster-preview-summary': summary,
        'roster-file-input': input,
        'btn-confirm-roster-upload': applyButton,
    };
    const sandbox = {
        console,
        window: {},
        document: {
            activeElement,
            body: {
                classList: {
                    add(name) { classes.add(name); },
                    remove(name) { classes.delete(name); },
                },
            },
            getElementById(id) { return elements[id] || null; },
        },
        showToast(message, type) {
            toasts.push({ message: String(message), type });
        },
        escapeHtmlValue(value) {
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        'let rosterPreviewReturnFocus = null; ' +
        'let pendingRosterUpload = null; ' +
        'let rosterUploadApplying = false;',
        sandbox
    );
    vm.runInContext(sourceSlice(
        'function closeRosterPreview({ restoreFocus = true, force = false } = {}) {',
        "document.getElementById('roster-preview-dialog')?.addEventListener"
    ), sandbox, { filename: 'app-roster-modal.js' });

    sandbox.file = { name: 'partial.csv' };
    sandbox.legacyData = {
        preview_token: 'legacy-token',
        total: 2,
        added: 1,
        updated: 1,
        removed: 0,
    };
    assert.strictEqual(
        vm.runInContext('showRosterPreview(file, legacyData)', sandbox),
        false
    );
    assert.strictEqual(dialog.hidden, true);
    assert.strictEqual(
        vm.runInContext('pendingRosterUpload', sandbox),
        null
    );
    assert(toasts.some(item => /older server response/i.test(item.message)));

    sandbox.unsafeMergeData = {
        roster_upload_mode: 'merge',
        preview_token: 'unsafe-merge-token',
        total: 2,
        new: 1,
        updated: 1,
        removed: 1,
    };
    assert.strictEqual(
        vm.runInContext('showRosterPreview(file, unsafeMergeData)', sandbox),
        false
    );
    assert.strictEqual(dialog.hidden, true);
    assert.strictEqual(
        vm.runInContext('pendingRosterUpload', sandbox),
        null
    );

    sandbox.mergeData = {
        roster_upload_mode: 'merge',
        preview_token: 'merge-token',
        total: 2,
        new: 1,
        restored: 0,
        updated: 1,
        unchanged: 0,
        pin_changed: 1,
        omitted_unchanged: 6,
        removed: 0,
    };
    assert.strictEqual(
        vm.runInContext('showRosterPreview(file, mergeData)', sandbox),
        true
    );
    assert.strictEqual(dialog.hidden, false);
    assert(classes.has('modal-open'));
    assert.match(summary.innerHTML, /6 active students not listed and left unchanged/);

    vm.runInContext(
        'rosterUploadApplying = true; closeRosterPreview();',
        sandbox
    );
    assert.strictEqual(dialog.hidden, false);
    assert.strictEqual(
        vm.runInContext('pendingRosterUpload.token', sandbox),
        'merge-token'
    );

    vm.runInContext('closeRosterPreview({ force: true });', sandbox);
    assert.strictEqual(dialog.hidden, true);
    assert.strictEqual(
        vm.runInContext('pendingRosterUpload', sandbox),
        null
    );
    assert(!classes.has('modal-open'));
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
        removeIndividualTeamPicker(picker) { picker.remove(); },
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

    assert.deepStrictEqual(selectors, [
        '.team-tag[aria-expanded="true"]',
        '.team-picker:not(.filter-picker)',
    ]);
    assert.strictEqual(dynamicPicker.removed, 1);
    assert.strictEqual(permanentFilter.removed, 0);
    assert.strictEqual(instructor.dataset.rosterVersion, '10');
    assert.strictEqual(refreshCalls, 1);
}

async function testRandomAssignmentUsesAuthoritativeOnlineCounts() {
    const confirmations = [];
    const toasts = [];
    const responses = [];
    const postUrls = [];
    let responseData = null;
    let refreshCalls = 0;
    let appliedVersions = 0;
    let confirmResult = true;
    const sandbox = {
        console,
        window: {},
        confirm(message) {
            confirmations.push(String(message));
            return confirmResult;
        },
        beginRosterMutation() { return true; },
        endRosterMutation() {},
        instructorStatePayload() { return {}; },
        async postJSON(url) {
            postUrls.push(url);
            return responseData;
        },
        applyRosterMutationVersion() { appliedVersions += 1; },
        showToast(message, type) {
            toasts.push({ message: String(message), type });
        },
        showInstructorMutationError(data, fallback) {
            responses.push({ data, fallback });
        },
        refreshRosterInPlace() { refreshCalls += 1; },
    };
    vm.createContext(sandbox);
    vm.runInContext(sourceSlice(
        'window.randomAssign = async function(btn) {',
        '/* ===== INSTRUCTOR PANEL ===== */'
    ), sandbox, { filename: 'app-random-assign.js' });

    const button = { disabled: false };
    responseData = {
        success: true,
        assigned: 2,
        remaining: 0,
        skipped_offline: 1,
        roster_version: 10,
    };
    await sandbox.window.randomAssign(button);
    assert.match(confirmations[0], /currently online unassigned students/i);
    assert.match(confirmations[0], /currently offline will be left unchanged/i);
    assert.deepStrictEqual(toasts.pop(), {
        message:
            'Assigned 2 online students. 1 offline student remains unassigned.',
        type: 'success',
    });

    responseData = {
        success: true,
        assigned: 1,
        remaining: 2,
        skipped_offline: 1,
        roster_version: 11,
    };
    await sandbox.window.randomAssign(button);
    assert.deepStrictEqual(toasts.pop(), {
        message:
            'Assigned 1 online student. ' +
            '2 online students remain unassigned because teams are full. ' +
            '1 offline student remains unassigned.',
        type: 'warning',
    });

    responseData = {
        success: true,
        assigned: 0,
        remaining: 2,
        skipped_offline: 1,
        roster_version: 10,
    };
    await sandbox.window.randomAssign(button);
    assert.deepStrictEqual(toasts.pop(), {
        message:
            'Teams are full. 2 online students remain unassigned. ' +
            '1 offline student remains unassigned.',
        type: 'warning',
    });

    responseData = {
        success: true,
        assigned: 0,
        remaining: 0,
        skipped_offline: 2,
        roster_version: 10,
    };
    await sandbox.window.randomAssign(button);
    assert.deepStrictEqual(toasts.pop(), {
        message:
            'No online students need assignment. ' +
            '2 offline students remain unassigned.',
        type: undefined,
    });

    responseData = {
        success: true,
        assigned: 0,
        remaining: 0,
        skipped_offline: 0,
        roster_version: 10,
    };
    await sandbox.window.randomAssign(button);
    assert.strictEqual(toasts.pop().message, 'All students are already in teams.');

    confirmResult = false;
    await sandbox.window.randomAssign(button);

    assert(postUrls.every(url => url === '/api/random_assign'));
    assert.strictEqual(postUrls.length, 5);
    assert.strictEqual(appliedVersions, 5);
    assert.strictEqual(refreshCalls, 5);
    assert.strictEqual(confirmations.length, 6);
    assert.strictEqual(toasts.length, 0);
    assert.strictEqual(responses.length, 0);
    assert.strictEqual(button.disabled, false);
    assert.match(instructorTemplate, />Assign Online<\/button>/);
}

function testInstructorQuickRollIsLocalAccessibleAndUniform() {
    let activeElement = null;

    function makeClassList() {
        const values = new Set();
        return {
            add(...names) { names.forEach(name => values.add(name)); },
            remove(...names) { names.forEach(name => values.delete(name)); },
            contains(name) { return values.has(name); },
        };
    }

    function makeElement(id) {
        const listeners = new Map();
        const attributes = new Map();
        return {
            id,
            hidden: false,
            value: '',
            textContent: '',
            offsetWidth: 120,
            classList: makeClassList(),
            focusCount: 0,
            selectCount: 0,
            addEventListener(type, handler) {
                if (!listeners.has(type)) listeners.set(type, []);
                listeners.get(type).push(handler);
            },
            dispatch(type, event = {}) {
                for (const handler of listeners.get(type) || []) handler(event);
            },
            setAttribute(name, value) {
                attributes.set(name, String(value));
            },
            getAttribute(name) { return attributes.get(name); },
            focus() {
                this.focusCount += 1;
                activeElement = this;
            },
            select() { this.selectCount += 1; },
        };
    }

    const ids = [
        'quick-roll-widget',
        'quick-roll-panel',
        'quick-roll-trigger',
        'quick-roll-close',
        'quick-roll-form',
        'quick-roll-maximum',
        'quick-roll-error',
        'quick-roll-result',
        'quick-roll-result-number',
        'quick-roll-result-range',
        'quick-roll-announcement',
    ];
    const elements = Object.fromEntries(ids.map(id => [id, makeElement(id)]));
    const widgetElements = new Set(Object.values(elements));
    const panelElements = new Set(Object.values(elements).filter(element =>
        element !== elements['quick-roll-widget'] &&
        element !== elements['quick-roll-trigger']
    ));
    elements['quick-roll-panel'].hidden = true;
    elements['quick-roll-error'].hidden = true;
    elements['quick-roll-maximum'].value = '6';
    elements['quick-roll-widget'].contains = target => widgetElements.has(target);
    elements['quick-roll-panel'].contains = target => panelElements.has(target);

    const documentListeners = new Map();
    const document = {
        get activeElement() { return activeElement; },
        getElementById(id) { return elements[id] || null; },
        addEventListener(type, handler) {
            if (!documentListeners.has(type)) documentListeners.set(type, []);
            documentListeners.get(type).push(handler);
        },
    };
    function dispatchDocument(type, event) {
        for (const handler of documentListeners.get(type) || []) handler(event);
    }

    let draws = 0;
    const samples = [];
    const crypto = {
        getRandomValues(array) {
            draws += 1;
            assert(samples.length > 0, 'unexpected random draw');
            array[0] = samples.shift();
            return array;
        },
    };
    const window = { crypto };
    for (const forbidden of ('localStorage sessionStorage').split(' ')) {
        Object.defineProperty(window, forbidden, {
            get() { throw new Error(`${forbidden} must not be accessed`); },
        });
    }
    const sandbox = {
        console,
        document,
        window,
        fetch() { throw new Error('Quick Roll must not use fetch'); },
        postJSON() { throw new Error('Quick Roll must not use postJSON'); },
    };
    vm.createContext(sandbox);
    vm.runInContext(sourceSlice(
        '/* ===== INSTRUCTOR QUICK ROLL ===== */',
        '/* ===== END INSTRUCTOR QUICK ROLL ===== */'
    ), sandbox, { filename: 'app-quick-roll.js' });

    const panel = elements['quick-roll-panel'];
    const trigger = elements['quick-roll-trigger'];
    const close = elements['quick-roll-close'];
    const form = elements['quick-roll-form'];
    const input = elements['quick-roll-maximum'];
    const error = elements['quick-roll-error'];
    const result = elements['quick-roll-result'];
    const resultNumber = elements['quick-roll-result-number'];
    const resultRange = elements['quick-roll-result-range'];
    const announcement = elements['quick-roll-announcement'];
    const event = () => ({ prevented: false, preventDefault() { this.prevented = true; } });

    trigger.dispatch('click');
    assert.strictEqual(panel.hidden, false);
    assert.strictEqual(trigger.getAttribute('aria-expanded'), 'true');
    assert.strictEqual(trigger.getAttribute('aria-label'), 'Close Quick Roll');
    assert.strictEqual(input.focusCount, 1);
    assert.strictEqual(input.selectCount, 1);
    dispatchDocument('click', { target: input });
    assert.strictEqual(panel.hidden, false, 'inside clicks must keep the panel open');

    trigger.dispatch('click');
    assert.strictEqual(panel.hidden, true);
    assert.strictEqual(trigger.getAttribute('aria-expanded'), 'false');
    trigger.dispatch('click');
    dispatchDocument('click', { target: {} });
    assert.strictEqual(panel.hidden, true, 'outside click must close the panel');
    assert.strictEqual(trigger.focusCount, 1,
        'outside click must restore focus when the hidden panel retained it');

    const outsideButton = makeElement('outside-button');
    trigger.dispatch('click');
    outsideButton.focus();
    dispatchDocument('click', { target: outsideButton });
    assert.strictEqual(panel.hidden, true);
    assert.strictEqual(activeElement, outsideButton,
        'a focusable outside target must retain focus');
    assert.strictEqual(trigger.focusCount, 1);

    trigger.dispatch('click');
    const escapeEvent = { key: 'Escape', prevented: false,
        preventDefault() { this.prevented = true; } };
    dispatchDocument('keydown', escapeEvent);
    assert.strictEqual(escapeEvent.prevented, true);
    assert.strictEqual(panel.hidden, true);
    assert.strictEqual(trigger.focusCount, 2);
    trigger.dispatch('click');
    close.dispatch('click');
    assert.strictEqual(panel.hidden, true);
    assert.strictEqual(trigger.focusCount, 3);

    input.value = '6';
    samples.push(0xffffffff, 5);
    const submitSix = event();
    form.dispatch('submit', submitSix);
    assert.strictEqual(submitSix.prevented, true);
    assert.strictEqual(draws, 2, 'rejected samples must be redrawn');
    assert.strictEqual(resultNumber.textContent, '6');
    assert.strictEqual(resultRange.textContent, '1 through 6');
    assert.strictEqual(announcement.textContent, 'Rolled 6 out of 6.');
    assert.strictEqual(result.classList.contains('is-rolling'), true);
    assert.strictEqual(error.hidden, true);

    input.dispatch('input');
    assert.strictEqual(resultNumber.textContent, '?');
    assert.strictEqual(announcement.textContent, 'No roll yet.');
    assert.strictEqual(result.classList.contains('is-rolling'), false);

    input.value = '6';
    samples.push(0);
    form.dispatch('submit', event());
    assert.strictEqual(resultNumber.textContent, '1');

    input.value = '1';
    input.dispatch('input');
    samples.push(0xffffffff);
    form.dispatch('submit', event());
    assert.strictEqual(resultNumber.textContent, '1');
    assert.strictEqual(resultRange.textContent, '1 through 1');

    for (const invalid of ['', '0', '-1', '2.5', '1000001', 'Infinity']) {
        const drawsBefore = draws;
        input.value = invalid;
        input.dispatch('input');
        form.dispatch('submit', event());
        assert.strictEqual(draws, drawsBefore, `invalid limit drew: ${invalid}`);
        assert.strictEqual(error.hidden, false);
        assert.strictEqual(input.getAttribute('aria-invalid'), 'true');
        assert.strictEqual(resultNumber.textContent, '?');
    }

    input.value = '4';
    input.dispatch('input');
    window.crypto = undefined;
    form.dispatch('submit', event());
    assert.match(error.textContent, /unavailable/i);
    assert.strictEqual(resultNumber.textContent, '?');
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
        "let _studentTableRetryTimer = null;" +
        "let _studentTableReloadPending = false;" +
        "let _studentTableTrailingReloadTimer = null;" +
        "let _studentTableRenderSignature = null;",
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
    const filterStart = instructorTemplate.indexOf('id="team-filter-select"');
    const filterEnd = instructorTemplate.indexOf('<div class="table-wrapper">', filterStart);
    assert(filterStart >= 0 && filterEnd > filterStart);
    const filterMarkup = instructorTemplate.slice(filterStart, filterEnd);
    assert(instructorTemplate.includes('<label class="filter-label" for="team-filter-select">'));
    assert(filterMarkup.includes('onchange="setTeamFilterFromSelect(this)"'));
    assert(filterMarkup.includes('<option value="">All</option>'));
    assert(filterMarkup.includes('<option value="none">Unassigned</option>'));
    assert(source.includes('window.setTeamFilterFromSelect = function(select)'));
    assert(source.includes("teamFilterVal = String(select?.value ?? '')"));
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

function testInstructorVisibilityRecovery() {
    const instructorBlockStart = source.indexOf('if (instructor) {');
    const instructorBlockEnd = source.indexOf(
        '\nlet _originalMaxTeams', instructorBlockStart
    );
    assert(instructorBlockStart >= 0 && instructorBlockEnd > instructorBlockStart);
    const instructorBlock = source.slice(
        instructorBlockStart, instructorBlockEnd
    );
    const registrationStart = instructorBlock.indexOf(
        "document.addEventListener('visibilitychange'"
    );
    const registrationEnd = instructorBlock.indexOf(
        '\n    instructorPollOnce();', registrationStart
    );
    assert(registrationStart >= 0 && registrationEnd > registrationStart);
    const registration = instructorBlock.slice(
        registrationStart, registrationEnd
    );
    assert(registration.includes('revalidateInstructorQuestions();'));

    let handler = null;
    let pollCalls = 0;
    let revalidationCalls = 0;
    const sandbox = {
        document: {
            hidden: true,
            addEventListener(type, callback) {
                assert.strictEqual(type, 'visibilitychange');
                handler = callback;
            },
        },
        revalidateInstructorQuestions() { revalidationCalls += 1; },
        instructorPollOnce() { pollCalls += 1; },
    };
    vm.createContext(sandbox);
    vm.runInContext(registration, sandbox, {
        filename: 'app-instructor-visibility.js',
    });
    assert.strictEqual(typeof handler, 'function');
    handler();
    assert.strictEqual(pollCalls, 0, 'hidden tabs must not trigger a refresh');
    sandbox.document.hidden = false;
    handler();
    assert.strictEqual(pollCalls, 1, 'a visible instructor tab must refresh now');
    assert.strictEqual(revalidationCalls, 1);

    const pollLoop = sourceSlice(
        'async function instructorPollOnce() {',
        "window.addEventListener('offline'"
    );
    assert(pollLoop.includes(
        'if (_instrPollInProgress || _instrPollStopped) return;'
    ), 'visibility recovery must reuse the existing overlap guard');
}

function testMarkdownPreservesMultilineDisplayMath() {
    const marked = require(path.join(
        __dirname, '..', '..', 'static', 'vendor', 'marked.min.js'
    ));
    let sanitizeCalls = 0;
    const purifier = {
        sanitize(html) {
            sanitizeCalls += 1;
            return html;
        },
    };
    const sandbox = {
        console,
        window: { marked, DOMPurify: purifier },
        DOMPurify: purifier,
        document: {
            createElement() {
                return {
                    _text: '',
                    set textContent(value) { this._text = String(value); },
                    get innerHTML() { return this._text; },
                };
            },
        },
        escapeHtmlValue(value) {
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#39;');
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(sourceSlice(
        'let _markdownParser = null;',
        'function renderPresentationQuestion(question) {'
    ), sandbox, { filename: 'app-markdown-render.js' });

    const math = sandbox.renderMarkdown(
        '$$\nf(x)\n+ g(x)\n- h(x)\n1. k(x)\n\na < b & c\n$$' +
        '\n\nBetween\n\n$$\nz = 2\n$$'
    );
    assert.strictEqual((math.match(/class="math-display"/g) || []).length, 2);
    assert(!math.includes('<ul>'), 'plus/minus math lines must not become a list');
    assert(!math.includes('<ol>'), 'numbered math lines must not become a list');
    assert(math.includes('+ g(x)'));
    assert(math.includes('&lt;'));
    assert(math.includes('&amp;'));

    const fenced = sandbox.renderMarkdown(
        '~~~text\n$$\n+ this is code\n$$\n~~~'
    );
    assert(fenced.includes('<pre><code'));
    assert(!fenced.includes('class="math-display"'));
    assert.strictEqual(sanitizeCalls, 2, 'DOMPurify must sanitize every result');
}

function testIndividualTeamPickerKeyboardContract() {
    const tableRenderer = sourceSlice(
        'window.loadStudentTable = async function() {',
        'window.sortStudents = function(col) {'
    );
    assert(tableRenderer.includes('<button type="button" class="team-tag'));
    assert(tableRenderer.includes('aria-haspopup="menu" aria-expanded="false"'));

    const picker = sourceSlice(
        'window.toggleTeamPicker = function(e, studentId) {',
        'window.pickTeam = async function(studentId, teamId) {'
    );
    assert(source.includes('function removeIndividualTeamPicker('));
    assert(picker.includes("picker.setAttribute('role', 'menu')"));
    assert(picker.includes('_closeIndividualTeamPicker'));
    assert(picker.includes('role="menuitem"'));
    assert(picker.includes("event.key === 'Escape'"));
    assert(picker.includes("event.key === 'ArrowDown'"));
    assert(picker.includes("tag.setAttribute('aria-expanded', 'true')"));
    assert(picker.includes("picker.querySelector('.team-picker-item')?.focus()"));
}

function testAppendixCreateRequestIdentity() {
    let requestNumber = 0;
    const sandbox = {
        console,
        JSON,
        Math,
        Date,
        instructor: { dataset: { discussionWeek: '3' } },
        window: {
            crypto: {
                randomUUID() {
                    requestNumber += 1;
                    return 'request-' + requestNumber;
                },
            },
        },
    };
    vm.createContext(sandbox);
    vm.runInContext(
        'let discussionCurrentWeek = 3;' +
        sourceSlice(
            'let appendixMutationInProgress = false;',
            'function beginAppendixMutation(button, pendingLabel) {'
        ),
        sandbox,
        { filename: 'app-appendix-request-id.js' }
    );

    const first = vm.runInContext(
        "ensureAppendixCreateRequest('Title', 'Body', 3)", sandbox
    );
    const retry = vm.runInContext(
        "ensureAppendixCreateRequest('Title', 'Body', 3)", sandbox
    );
    const changed = vm.runInContext(
        "ensureAppendixCreateRequest('Title', 'Changed body', 3)", sandbox
    );
    assert.strictEqual(first, retry, 'same add payload must reuse its request key');
    assert.notStrictEqual(
        first,
        changed,
        'a material draft change must receive a new request key'
    );

    const addFlow = sourceSlice(
        'window.addAppendixQuestion = async function(button) {',
        'function resetAppendixFormMode() {'
    );
    assert(addFlow.includes('client_request_id: createRequestId'));
    const draftFlow = sourceSlice(
        'function stashAppendixDraft() {',
        'function setRestoredAppendixEditMode(appendixId) {'
    );
    assert(draftFlow.includes('clientRequestId:'));
    assert(draftFlow.includes('appendixCreateRequestFingerprint !== fingerprint'));
}

async function testStudentTableQueuesTrailingReload() {
    let resolveFirst;
    let trailingReload = null;
    const urls = [];
    const tbody = {
        innerHTML: '',
        appendChild() {},
        querySelector() { return null; },
    };
    const pagination = { innerHTML: '' };
    const search = { value: 'first search' };
    const elements = {
        'student-search': search,
        'student-tbody': tbody,
        'student-pagination': pagination,
    };
    const successfulData = {
        course_total: 2,
        total: 0,
        total_pages: 1,
        page: 1,
        students: [],
        teams: [],
    };
    const sandbox = {
        console,
        URLSearchParams,
        instructor: { dataset: { studentCount: '2' } },
        isDemoInstructor: false,
        window: {
            _lastTeams: [],
            clearTimeout() {},
            setTimeout(handler, delay) {
                if (delay === 0) trailingReload = handler;
                return 1;
            },
        },
        document: {
            getElementById(id) { return elements[id] || null; },
            querySelector(selector) {
                if (selector === '.instructor[data-student-count]') {
                    return sandbox.instructor;
                }
                return null;
            },
            querySelectorAll() { return []; },
            createElement() {
                return { setAttribute() {}, innerHTML: '' };
            },
        },
        fetchJSONWithTimeout(url) {
            urls.push(url);
            if (urls.length === 1) {
                return new Promise(resolve => { resolveFirst = resolve; });
            }
            return Promise.resolve({
                res: { status: 200, ok: true },
                data: successfulData,
            });
        },
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
        "let teamFilterVal = '';" +
        "let _studentTableLoading = false;" +
        "let _studentTableLoadFailed = false;" +
        "let _studentTableRetryTimer = null;" +
        "let _studentTableReloadPending = false;" +
        "let _studentTableTrailingReloadTimer = null;" +
        "let _studentTableRenderSignature = null;",
        sandbox
    );
    vm.runInContext(sourceSlice(
        'window.loadStudentTable = async function() {',
        'window.sortStudents = function(col) {'
    ), sandbox, { filename: 'app-student-table-trailing.js' });
    sandbox.loadStudentTable = sandbox.window.loadStudentTable;

    const first = sandbox.window.loadStudentTable();
    search.value = 'newest search';
    await sandbox.window.loadStudentTable();
    assert.strictEqual(urls.length, 1, 'an in-flight table request must not overlap');

    resolveFirst({
        res: { status: 200, ok: true },
        data: successfulData,
    });
    await first;
    assert.strictEqual(typeof trailingReload, 'function');
    trailingReload();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.strictEqual(urls.length, 2);
    assert(
        urls[1].includes('search=newest+search'),
        'the trailing request must use the latest table controls'
    );
}

async function testQuestionLoaderRecoversInitialFailure() {
    const timers = [];
    const toasts = [];
    let fetchCalls = 0;
    let renderCalls = 0;
    const select = { innerHTML: '', disabled: false };
    const preview = { disabled: false };
    const apply = { disabled: false };
    const container = { innerHTML: '' };
    const elements = {
        'setup-week-select': select,
        'btn-preview-week': preview,
        'btn-apply-week': apply,
        'disc-questions-list': container,
    };
    const questionData = {
        current_week: 1,
        version: 0,
        weeks: [{ num: 1, ready: true, issues: [] }],
        questions: [{ key: 'week-1-q1', title: 'Q1', content: 'Body' }],
    };
    const sandbox = {
        console,
        Date,
        Math,
        pageNavigationInProgress: false,
        window: {
            clearTimeout(id) {
                const timer = timers[id - 1];
                if (timer) timer.cancelled = true;
            },
            setTimeout(handler, delay) {
                timers.push({ handler, delay, cancelled: false });
                return timers.length;
            },
        },
        document: {
            getElementById(id) { return elements[id] || null; },
            createElement() {
                return {
                    className: '',
                    disabled: false,
                    id: '',
                    textContent: '',
                    type: '',
                    insertAdjacentElement() {},
                };
            },
        },
        fetchJSONWithTimeout() {
            fetchCalls += 1;
            if (fetchCalls === 1) return Promise.reject(new Error('offline'));
            return Promise.resolve({
                res: { status: 200, ok: true },
                data: questionData,
            });
        },
        redirectToSessionEnded() {},
        escapeHtmlValue(value) { return String(value); },
        escapeAttrValue(value) { return String(value); },
        showToast(message) { toasts.push(String(message)); },
        renderDiscussionQuestions() { renderCalls += 1; },
    };
    vm.createContext(sandbox);
    vm.runInContext(sourceSlice(
        'let discussionCurrentWeek = null;',
        '/* ===== WEEK PREVIEW MODAL ===== */'
    ), sandbox, { filename: 'app-question-loader.js' });

    await vm.runInContext('initWeekSelector()', sandbox);
    assert.strictEqual(select.disabled, true);
    assert.strictEqual(preview.disabled, true);
    assert.match(container.innerHTML, /Retrying automatically/);
    assert.strictEqual(timers.length, 1);
    assert.strictEqual(timers[0].delay, 1000);
    assert.strictEqual(
        vm.runInContext('renderedDiscQuestionsVersion', sandbox),
        null,
        'version zero must not masquerade as a successful initial load'
    );
    assert.strictEqual(toasts.length, 1);

    timers[0].handler();
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.strictEqual(fetchCalls, 2);
    assert.strictEqual(select.disabled, false);
    assert.strictEqual(preview.disabled, false);
    assert.strictEqual(apply.disabled, false);
    assert.strictEqual(renderCalls, 1);
    assert.strictEqual(
        vm.runInContext('renderedDiscQuestionsVersion', sandbox),
        0
    );

    let resolveDeferred;
    sandbox.fetchJSONWithTimeout = () => {
        fetchCalls += 1;
        return new Promise(resolve => { resolveDeferred = resolve; });
    };
    const firstReload = vm.runInContext('initWeekSelector()', sandbox);
    const sharedReload = vm.runInContext('initWeekSelector()', sandbox);
    assert.strictEqual(fetchCalls, 3, 'question refreshes must share one request');
    resolveDeferred({
        res: { status: 200, ok: true },
        data: { ...questionData, version: 1 },
    });
    await Promise.all([firstReload, sharedReload]);
    assert.strictEqual(
        vm.runInContext('renderedDiscQuestionsVersion', sandbox),
        1
    );
}

function testContextualRosterBadgesAndTeamLabels() {
    const sandbox = { console };
    vm.createContext(sandbox);
    vm.runInContext(sourceSlice(
        'function normalizedIdentityText(value) {',
        'function initDisplayNameControls() {'
    ), sandbox, { filename: 'app-display-name-helpers.js' });
    vm.runInContext(sourceSlice(
        'function normalizedParticipationCount(value) {',
        'function renderRosterGrid(container, teams, myId'
    ), sandbox, { filename: 'app-participation-badges.js' });
    vm.runInContext(sourceSlice(
        'function updateCompetitionTeamOptionLabel(option) {',
        'function setMemberParticipationCount(member, kind, value) {'
    ), sandbox, { filename: 'app-team-option-label.js' });

    sandbox.escapeHtmlValue = value => String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
    sandbox.escapeAttrValue = value => sandbox.escapeHtmlValue(value)
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    sandbox.setupMember = {
        student_id: 's<1>',
        display_name: 'Ada & Lin',
        roster_name: 'Ada Roster',
        presentation_count: 0,
        challenger_count: 9,
    };
    const setupMemberHtml = vm.runInContext(
        "instructorSetupRosterMemberHtml(setupMember, '#123456')",
        sandbox
    );
    assert.match(setupMemberHtml, /setup-roster-member-topline/);
    assert.match(setupMemberHtml, /Ada &amp; Lin \(s&lt;1&gt;\)/);
    assert.doesNotMatch(setupMemberHtml, /Ada Roster/);
    assert.doesNotMatch(setupMemberHtml, /Challenge turns/);
    assert(
        setupMemberHtml.indexOf('setup-roster-member-identity') <
        setupMemberHtml.indexOf('setup-roster-team-turns')
    );

    sandbox.record = {
        presentation_count: 2,
        challenger_count: 3,
    };
    const setupBadges = vm.runInContext(
        'participationBadgesHtml(record, false)', sandbox
    );
    assert.match(setupBadges, /Team turns: <strong>2/);
    assert.doesNotMatch(setupBadges, /Challenge turns/);
    const individualBadges = vm.runInContext(
        'participationBadgesHtml(record, true)', sandbox
    );
    assert.match(individualBadges, /Challenge turns: <strong>3/);

    const option = {
        value: '1',
        dataset: {
            teamName: 'Team 1',
            memberCount: '4',
            neverCount: '1',
            totalTurns: '7',
            completed: '1',
        },
        textContent: '',
    };
    sandbox.option = option;
    vm.runInContext('updateCompetitionTeamOptionLabel(option)', sandbox);
    assert.strictEqual(
        option.textContent,
        'Team 1 · 3/4 · 7 turns (completed)'
    );

    option.dataset.totalTurns = '1';
    vm.runInContext('updateCompetitionTeamOptionLabel(option)', sandbox);
    assert.strictEqual(
        option.textContent,
        'Team 1 · 3/4 · 1 turn (completed)'
    );

    option.dataset.neverCount = '4';
    option.dataset.totalTurns = '0';
    option.dataset.completed = '0';
    vm.runInContext('updateCompetitionTeamOptionLabel(option)', sandbox);
    assert.strictEqual(option.textContent, 'Team 1 · 0/4 · 0 turns');

    const members = ['2', '1', '0', '0'].map(presentationCount => ({
        dataset: { presentationCount },
    }));
    const summary = { textContent: '' };
    const section = {
        dataset: { teamId: '1' },
        querySelectorAll() { return members; },
        querySelector() { return summary; },
    };
    sandbox.competitionTeamOption = () => option;
    sandbox.section = section;
    vm.runInContext('updateCompetitionTeamSummary(section)', sandbox);
    assert.strictEqual(option.dataset.memberCount, '4');
    assert.strictEqual(option.dataset.neverCount, '2');
    assert.strictEqual(option.dataset.totalTurns, '3');
    assert.strictEqual(option.textContent, 'Team 1 · 2/4 · 3 turns');
    assert.strictEqual(
        summary.textContent,
        '2 of 4 members have no completed team turns.'
    );
}

(async () => {
    testRosterMergeModalRejectsLegacyAndLocksDuringApply();
    await testRosterMutationSerializationAndPickerScope();
    await testRandomAssignmentUsesAuthoritativeOnlineCounts();
    testInstructorQuickRollIsLocalAccessibleAndUniform();
    await testStudentTableKeepsCourseTotalSeparateFromFilterTotal();
    await testStudentTableQueuesTrailingReload();
    await testQuestionLoaderRecoversInitialFailure();
    testPermanentTeamFilterAccessibility();
    testPresentationTransitionsReconcileInPlace();
    testInstructorVisibilityRecovery();
    testMarkdownPreservesMultilineDisplayMath();
    testIndividualTeamPickerKeyboardContract();
    testAppendixCreateRequestIdentity();
    testContextualRosterBadgesAndTeamLabels();
    console.log('instructor UI behavior: ok');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
