'use strict';

/* Behavior coverage for authoritative instructor participation readbacks. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(
    __dirname, '..', '..', 'static', 'js', 'app.js'
), 'utf8');
const response = (status, data) => ({
    status,
    ok: status >= 200 && status < 300,
    json: async () => data,
});
const flush = async () => {
    for (let i = 0; i < 20; i += 1) {
        await new Promise(resolve => setImmediate(resolve));
    }
};

async function main() {
    const fetchLog = [];
    const events = [];
    const timerDelays = [];
    const appliedRecords = [];
    const incrementedTeams = [];
    const toastMessages = [];
    const participationName = { textContent: 'Old Identity' };
    const participationRow = {
        dataset: { studentId: 's2' },
        querySelector(selector) {
            return selector === '.team-participation-member-name'
                ? participationName : null;
        },
    };
    let fetchHandler;
    let confirmResult = true;
    let confirmMessage = '';

    const windowStub = {
        location: { href: '', reload() { events.push('reload'); } },
        addEventListener() {},
        setTimeout(handler, delay) {
            timerDelays.push(delay);
            if (delay === 300) setImmediate(handler);
            return timerDelays.length;
        },
        clearTimeout() {},
        instructorPollOnce() { events.push('poll'); },
    };
    const sandbox = {
        AbortController,
        JSON,
        Math,
        Number,
        Promise,
        console,
        window: windowStub,
        document: {
            getElementById(id) {
                return id === 'team-participation-preview' ? {} : null;
            },
            querySelectorAll(selector) {
                assert.strictEqual(
                    selector,
                    '.team-participation-member[data-student-id]'
                );
                return [participationRow];
            },
        },
        appliedRecords,
        incrementedTeams,
        toastMessages,
        confirm(message) {
            confirmMessage = String(message);
            events.push('confirm');
            return confirmResult;
        },
        fetch(url, options = {}) {
            const method = options.method || 'GET';
            fetchLog.push({ url, method });
            events.push(`fetch:${method}:${url}`);
            return fetchHandler(url, options);
        },
    };
    vm.createContext(sandbox);

    vm.runInContext(
        source.slice(0, source.indexOf('function escapeHtmlValue')),
        sandbox,
        { filename: 'app-request-helpers.js' }
    );
    vm.runInContext(
        source.slice(
            source.indexOf('function normalizedIdentityText(value) {'),
            source.indexOf('function initDisplayNameControls() {')
        ),
        sandbox,
        { filename: 'app-identity-helpers.js' }
    );
    vm.runInContext(
        'let _instructorActionInFlight = false;' +
        "const instructor = { dataset: { phase: 'competition', " +
            "activeTeamId: '1', sessionKey: '1', " +
            "pollQuestionKey: 'pres-current' } };" +
        'let lastReceivedState = null;' +
        'function ratingsSettlementBlocksAction() { return false; }' +
        'function instructorStatePayload(extra = {}) { return extra; }' +
        'async function postJSONAfterRatingsSettle(url, payload) {' +
            'return postJSON(url, payload);' +
        '}' +
        'function showInstructorMutationError() {}' +
        'function showToast(message, type) {' +
            'toastMessages.push({ message: String(message), type });' +
        '}' +
        'function applyStudentParticipationCounts(record) {' +
            'appliedRecords.push({ ...record });' +
        '}' +
        'function incrementCompetitionTeamTurns(teamId) {' +
            'incrementedTeams.push(teamId);' +
        '}',
        sandbox
    );

    const historyStateStart = source.indexOf(
        'const _knownParticipationPresentationKeys'
    );
    vm.runInContext(source.slice(
        historyStateStart,
        source.indexOf('function competitionTeamOption', historyStateStart)
    ), sandbox);

    const refreshStart = source.indexOf(
        'const COMPETITION_PARTICIPATION_READBACK_ATTEMPTS'
    );
    vm.runInContext(source.slice(
        refreshStart,
        source.indexOf('function syncCompetitionPresentationHistory', refreshStart)
    ), sandbox);

    sandbox.identityTeams = [{
        members: [{
            id: 's2',
            student_id: 's2-public',
            display_name: 'Sam',
            roster_name: 'Roster Sam',
            presentation_count: 2,
            challenger_count: 3,
        }],
    }];
    vm.runInContext(
        'applyCompetitionParticipationRoster(identityTeams)', sandbox
    );
    assert.strictEqual(participationName.textContent, 'Sam (s2-public)');
    assert.strictEqual(appliedRecords[0].presentation_count, 2);
    appliedRecords.length = 0;
    const syncStart = source.indexOf(
        'function syncCompetitionPresentationHistory'
    );
    vm.runInContext(source.slice(
        syncStart,
        source.indexOf('function showCompetitionTeamParticipation', syncStart)
    ), sandbox);

    const selectStart = source.indexOf(
        'window.selectChallenger = async function(studentId) {'
    );
    vm.runInContext(source.slice(
        selectStart,
        source.indexOf(
            'window.clearChallenger = async function(challengeKey) {',
            selectStart
        )
    ), sandbox);

    const nextStart = source.indexOf(
        'window.nextPresentation = async function() {'
    );
    vm.runInContext(source.slice(
        nextStart,
        source.indexOf(
            'window.cancelPresentation = async function() {', nextStart
        )
    ), sandbox);

    const setPhaseStart = source.indexOf(
        'window.setPhase = async function(phase) {'
    );
    vm.runInContext(source.slice(
        setPhaseStart,
        source.indexOf('/* ===== STUDENT TABLE', setPhaseStart)
    ), sandbox);

    // The newest request owns the DOM update if responses arrive out of order.
    let teamsCalls = 0;
    let resolveOlder;
    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        teamsCalls += 1;
        if (teamsCalls === 1) {
            return new Promise(resolve => { resolveOlder = resolve; });
        }
        return response(200, [{ members: [{
            id: 's2', presentation_count: 2, challenger_count: 4,
        }] }]);
    };
    const older = vm.runInContext(
        'refreshCompetitionParticipationCounts()', sandbox
    );
    const newer = vm.runInContext(
        'refreshCompetitionParticipationCounts()', sandbox
    );
    await newer;
    resolveOlder(response(200, [{ members: [{
        id: 's2', presentation_count: 1, challenger_count: 1,
    }] }]));
    await older;
    assert.deepStrictEqual(
        appliedRecords.map(record => record.challenger_count), [4]
    );

    // A superseded success followed by newer failures stays dirty. A later
    // instructor poll retries it instead of silently accepting no readback.
    appliedRecords.length = 0;
    toastMessages.length = 0;
    teamsCalls = 0;
    let resolveAvailableSuccess;
    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        teamsCalls += 1;
        if (teamsCalls === 1) {
            return new Promise(resolve => {
                resolveAvailableSuccess = resolve;
            });
        }
        return response(500, {});
    };
    const olderReconcile = vm.runInContext(
        'reconcileCompetitionParticipationCounts()', sandbox
    );
    const newerReconcile = vm.runInContext(
        'reconcileCompetitionParticipationCounts()', sandbox
    );
    assert.strictEqual(await newerReconcile, false);
    resolveAvailableSuccess(response(200, [{ members: [{
        id: 's2', presentation_count: 7, challenger_count: 7,
    }] }]));
    assert.strictEqual(await olderReconcile, false);
    assert.deepStrictEqual(appliedRecords, []);
    assert.strictEqual(
        vm.runInContext('_competitionParticipationReadbackDirty', sandbox),
        true
    );
    assert.strictEqual(toastMessages.length, 1);
    assert.match(toastMessages[0].message, /Retrying automatically/);

    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        return response(200, [{ members: [{
            id: 's2', presentation_count: 8, challenger_count: 9,
        }] }]);
    };
    vm.runInContext(
        '_competitionParticipationNextRetryAt = 0;' +
        'retryCompetitionParticipationCountsIfDirty();',
        sandbox
    );
    await flush();
    assert.strictEqual(appliedRecords[0].challenger_count, 9);
    assert.strictEqual(
        vm.runInContext('_competitionParticipationReadbackDirty', sandbox),
        false
    );

    // Selection marks the counts dirty and asks the normal instructor poll to
    // reconcile them. It must not perform a second, duplicate /api/teams read.
    fetchLog.length = 0;
    events.length = 0;
    appliedRecords.length = 0;
    fetchHandler = async url => {
        assert.strictEqual(url, '/api/select_challenger');
        return response(200, {
            success: true, challenge_ratings_open: true,
        });
    };
    await sandbox.window.selectChallenger('s2');
    assert.deepStrictEqual(fetchLog.map(call =>
        `${call.method} ${call.url}`
    ), ['POST /api/select_challenger']);
    assert.strictEqual(
        vm.runInContext('_competitionParticipationReadbackDirty', sandbox),
        true
    );
    assert.strictEqual(events[events.length - 1], 'poll');

    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        return response(200, [{
            members: [{
                id: 's2', presentation_count: 0, challenger_count: 1,
            }],
        }]);
    };
    vm.runInContext('retryCompetitionParticipationCountsIfDirty();', sandbox);
    await flush();
    assert.deepStrictEqual(fetchLog.map(call =>
        `${call.method} ${call.url}`
    ), ['POST /api/select_challenger', 'GET /api/teams']);
    assert.strictEqual(appliedRecords[0].challenger_count, 1);

    // Declining Finish stops before mutation. Accepting it lets the poll own
    // the single authoritative count readback.
    fetchLog.length = 0;
    events.length = 0;
    confirmResult = false;
    fetchHandler = async () => {
        throw new Error('declined Finish must not fetch');
    };
    await sandbox.window.nextPresentation();
    assert.strictEqual(fetchLog.length, 0);
    assert.match(confirmMessage, /permanently recorded/i);
    assert.match(confirmMessage, /Cancel Mistake/);

    confirmResult = true;
    fetchHandler = async url => {
        assert.strictEqual(url, '/api/next_presentation');
        return response(200, { success: true });
    };
    await sandbox.window.nextPresentation();
    assert.deepStrictEqual(fetchLog.map(call =>
        `${call.method} ${call.url}`
    ), ['POST /api/next_presentation']);
    assert.strictEqual(events[events.length - 1], 'poll');

    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        return response(200, [{ members: [{
            id: 's2', presentation_count: 1, challenger_count: 1,
        }] }]);
    };
    vm.runInContext('retryCompetitionParticipationCountsIfDirty();', sandbox);
    await flush();
    assert.deepStrictEqual(fetchLog.map(call =>
        `${call.method} ${call.url}`
    ), ['POST /api/next_presentation', 'GET /api/teams']);

    // A history addition from another instructor tab also retries and reconciles.
    fetchLog.length = 0;
    appliedRecords.length = 0;
    incrementedTeams.length = 0;
    teamsCalls = 0;
    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        teamsCalls += 1;
        return teamsCalls === 1 ? response(500, {}) : response(200, [{
            members: [{
                id: 's3', presentation_count: 1, challenger_count: 0,
            }],
        }]);
    };
    vm.runInContext(
        'seedCompetitionPresentationHistory([]);' +
        "syncCompetitionPresentationHistory([{ presentation_key: 'p2', team_id: 3 }]);" +
        'retryCompetitionParticipationCountsIfDirty();',
        sandbox
    );
    await flush();
    assert.deepStrictEqual(
        incrementedTeams, [],
        'history sync must not optimistically double-count a completed turn'
    );
    assert.strictEqual(fetchLog.length, 2);
    assert.strictEqual(appliedRecords[0].presentation_count, 1);

    vm.runInContext(
        "syncCompetitionPresentationHistory([{ presentation_key: 'p2', team_id: 3 }]);",
        sandbox
    );
    await flush();
    assert.strictEqual(fetchLog.length, 2);

    // Active challenger and presentation identity changes from another tab
    // each queue an authoritative readback. Identical polls do not.
    fetchLog.length = 0;
    appliedRecords.length = 0;
    fetchHandler = async url => {
        assert.strictEqual(url, '/api/teams');
        return response(200, [{ members: [{
            id: 's4', presentation_count: 2, challenger_count: 3,
        }] }]);
    };
    const syncIdentity = state => vm.runInContext(
        'syncCompetitionParticipationStateIdentity(' +
        JSON.stringify(state) + ');' +
        'retryCompetitionParticipationCountsIfDirty();',
        sandbox
    );
    syncIdentity({
        phase: 'competition',
        session_key: 1,
        poll_question_key: 'p2',
        active_team_id: 1,
        active_question_id: 1,
        active_challenges: [],
    });
    await flush();
    syncIdentity({
        phase: 'competition',
        session_key: 1,
        poll_question_key: 'p2',
        active_team_id: 1,
        active_question_id: 1,
        active_challenges: [],
    });
    await flush();
    syncIdentity({
        phase: 'competition',
        session_key: 1,
        poll_question_key: 'p2',
        active_team_id: 1,
        active_question_id: 1,
        active_challenges: [{ challenge_key: 'c1' }],
    });
    await flush();
    syncIdentity({
        phase: 'competition',
        session_key: 1,
        poll_question_key: 'p3',
        active_team_id: 2,
        active_question_id: 1,
        active_challenges: [],
    });
    await flush();
    assert.strictEqual(fetchLog.length, 3);

    // Both setPhase paths that finalize an active presentation use the same
    // permanent-turn warning and advise correcting mistakes first.
    confirmResult = false;
    fetchLog.length = 0;
    fetchHandler = async () => {
        throw new Error('declined phase change must not fetch');
    };
    await sandbox.window.setPhase('ended');
    assert.match(confirmMessage, /permanently recorded/i);
    assert.match(confirmMessage, /Cancel Mistake/);
    await sandbox.window.setPhase('discussion');
    assert.match(confirmMessage, /permanently recorded/i);
    assert.match(confirmMessage, /Cancel Mistake/);
    assert.strictEqual(fetchLog.length, 0);

    console.log('instructor participation refresh behavior: ok');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
