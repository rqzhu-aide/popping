'use strict';
/* Behavior harness for the student poll loop's secondary-sync retry logic
 * Regression coverage. Runs the real static/js/app.js in a Node vm with a
 * minimal DOM stub and a scripted fetch router, then drives poll cycles
 * directly. Exits non-zero on the first failed assertion.
 *
 * Run directly (`node tests/js/poll_retry_harness.js`) or via
 * tests/test_poll_retry_behavior.py. */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = process.env.POLL_RETRY_APP_JS ||
    path.join(__dirname, '..', '..', 'static', 'js', 'app.js');

function makeElement(id) {
    let textValue = '';
    let htmlValue = '';
    return {
        id: id || '',
        dataset: {},
        style: { setProperty() {}, removeProperty() {} },
        classList: {
            add() {}, remove() {}, toggle() {}, contains() { return false; },
        },
        get textContent() { return textValue; },
        set textContent(value) {
            textValue = value == null ? '' : String(value);
            htmlValue = textValue
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        },
        get innerHTML() { return htmlValue; },
        set innerHTML(value) { htmlValue = value == null ? '' : String(value); },
        appendedChildren: [],
        parentElement: null,
        ownerDocument: null,
        insertions: 0,
        get children() { return this.appendedChildren; },
        get nextElementSibling() {
            const siblings = this.parentElement?.children || [];
            return siblings[siblings.indexOf(this) + 1] || null;
        },
        contains(target) {
            return this === target || this.children.some(child => child.contains(target));
        },
        hidden: false,
        disabled: false,
        open: true,
        scrollCalls: 0,
        focusCalls: 0,
        scrollIntoView() { this.scrollCalls += 1; },
        focus() {
            this.focusCalls += 1;
            if (this.ownerDocument) this.ownerDocument.activeElement = this;
        },
        title: '',
        appendChild(child) { return this.insertBefore(child, null); },
        insertBefore(child, anchor) {
            if (child.parentElement) {
                // Match the native DOM's focus loss when a focused ancestor
                // is detached. A restore-focus workaround cannot hide it.
                if (child.contains(child.ownerDocument?.activeElement)) {
                    child.ownerDocument.activeElement = child.ownerDocument.body;
                    child.ownerDocument.focusDetachments += 1;
                }
                const siblings = child.parentElement.children;
                siblings.splice(siblings.indexOf(child), 1);
            }
            const index = anchor === null ? this.children.length : this.children.indexOf(anchor);
            assert(index >= 0, 'insertBefore anchor must belong to its parent');
            this.children.splice(index, 0, child);
            child.parentElement = this;
            child.ownerDocument = this.ownerDocument;
            this.insertions += 1;
            return child;
        },
        setAttribute() {},
        removeAttribute() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
        closest() { return null; },
    };
}

function makeStorage() {
    return { getItem() { return null; }, setItem() {}, removeItem() {} };
}

// routes: array of [prefix, responses]; responses are consumed per prefix in
// call order and the last one repeats. A response is { status, data },
// { throw: true }, or { defer: label, response: { status, data } }.
function makeSession(routes, options = {}) {
    const fetchLog = [];
    const callCounts = new Map();
    const deferredFetches = new Map();
    let pendingFetches = 0;

    const dashboardEl = makeElement('dashboard');
    dashboardEl.dataset = {
        phase: options.phase || 'discussion',
        sessionKey: String(options.sessionKey ?? 4),
        you: 'S1',
        teamId: options.myTeamId == null ? '' : String(options.myTeamId),
    };
    const elementsById = {
        'student-disc-questions-list': makeElement('student-disc-questions-list'),
        'roster-table': makeElement('roster-table'),
        'team-section': makeElement('team-section'),
        'team-grading-section': makeElement('team-grading-section'),
        'student-live-activity': makeElement('student-live-activity'),
        'question-display': makeElement('question-display'),
        'active-question-text': makeElement('active-question-text'),
        'rating-window-state': makeElement('rating-window-state'),
        'poll-section': makeElement('poll-section'),
        'challenge-section': makeElement('challenge-section'),
        'poll-pulse': makeElement('poll-pulse'),
        'poll-pulse-text': makeElement('poll-pulse-text'),
        'rating-status': makeElement('rating-status'),
        'btn-submit-rating': makeElement('btn-submit-rating'),
    };
    const questionSummary = makeElement('question-summary');
    elementsById['question-summary'] = questionSummary;
    elementsById['question-display'].querySelector = selector =>
        selector === 'summary' ? questionSummary : null;
    elementsById['challenge-question-link'] = makeElement('challenge-question-link');
    const teammateCards = (options.teammateIds || []).map(studentId => {
        const card = makeElement(`card-${studentId}`);
        card.dataset.studentId = studentId;
        return card;
    });
    if (teammateCards.length) {
        const selfCard = makeElement('card-you');
        selfCard.dataset.studentId = 'you';
        selfCard.dataset.rosterStudentId = 'S1';
        teammateCards.push(selfCard);
    }

    let nowMs = 1_000_000;
    class FakeDate extends Date {
        static now() { return nowMs; }
    }

    const noopTimer = () => 0;
    // The poll loop schedules its next cycle through the global setTimeout;
    // capture those callbacks so the harness can drive cycles deterministically.
    const scheduledTimers = [];
    const windowListeners = new Map();
    let viewportWidth = options.width || 1280;
    let reloadCount = 0;
    const globalSetTimeout = (fn, delay) => {
        scheduledTimers.push({ fn, delay });
        return scheduledTimers.length;
    };
    const windowStub = {
        setTimeout: noopTimer,
        clearTimeout() {},
        setInterval: noopTimer,
        clearInterval() {},
        addEventListener(type, handler) {
            if (!windowListeners.has(type)) windowListeners.set(type, []);
            windowListeners.get(type).push(handler);
        },
        matchMedia(query) {
            const maximum = Number(query.match(/max-width:\s*(\d+)px/)?.[1]);
            return { matches: viewportWidth <= maximum };
        },
        location: {
            href: '',
            reload() { reloadCount += 1; },
        },
    };
    const documentStub = {
        hidden: false,
        body: makeElement('body'),
        focusDetachments: 0,
        querySelector(sel) {
            if (sel === '.dashboard') return dashboardEl;
            return null;
        },
        querySelectorAll(sel) {
            if (sel === '.teammate-card[data-student-id]') return teammateCards;
            return [];
        },
        getElementById(id) { return elementsById[id] || null; },
        createElement() { return makeElement(); },
        addEventListener() {},
    };
    documentStub.activeElement = documentStub.body;
    for (const element of Object.values(elementsById)) element.ownerDocument = documentStub;
    const activity = elementsById['student-live-activity'];
    for (const id of ['team-section', 'team-grading-section', 'challenge-section']) {
        activity.appendChild(elementsById[id]);
    }
    elementsById['team-section'].appendChild(elementsById['question-display']);
    elementsById['question-display'].appendChild(questionSummary);
    elementsById['team-grading-section'].appendChild(elementsById['poll-section']);
    elementsById['poll-section'].appendChild(elementsById['btn-submit-rating']);
    elementsById['challenge-section'].appendChild(elementsById['challenge-question-link']);

    const sandbox = {
        URLSearchParams,
        AbortController,
        Date: FakeDate,
        performance: { now: () => nowMs },
        Math,
        JSON,
        console,
        confirm: () => true,
        setTimeout: globalSetTimeout,
        clearTimeout() {},
        setInterval: noopTimer,
        clearInterval() {},
        sessionStorage: makeStorage(),
        localStorage: makeStorage(),
        MY_TEAM_ID: options.myTeamId ?? null,
        window: windowStub,
        document: documentStub,
        fetch: async (url) => {
            fetchLog.push(url);
            const route = routes.find(([prefix]) => url.startsWith(prefix));
            assert(route, `unexpected fetch: ${url}`);
            const count = callCounts.get(route[0]) || 0;
            callCounts.set(route[0], count + 1);
            const queue = route[1];
            let response = queue[Math.min(count, queue.length - 1)];
            pendingFetches += 1;
            try {
                if (response.defer) {
                    const deferredSpec = response;
                    response = await new Promise(resolve => {
                        assert(!deferredFetches.has(deferredSpec.defer),
                            `duplicate deferred fetch label: ${deferredSpec.defer}`);
                        deferredFetches.set(deferredSpec.defer, override => {
                            resolve(override || deferredSpec.response);
                        });
                    });
                }
                if (response.throw) throw new Error('network down');
                return {
                    status: response.status,
                    ok: response.status >= 200 && response.status < 300,
                    json: async () => response.data,
                };
            } finally {
                pendingFetches -= 1;
            }
        },
    };
    windowStub.document = documentStub;
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(APP_JS, 'utf8'), sandbox, { filename: 'app.js' });

    async function drain() {
        // Let the load-time pollOnce() and fire-and-forget fetches settle.
        for (let i = 0; i < 50; i += 1) {
            await new Promise(resolve => setImmediate(resolve));
            if (pendingFetches === 0 && i >= 5) break;
        }
    }

    return {
        sandbox,
        fetchLog,
        callCounts,
        elementsById,
        drain,
        resize: width => {
            viewportWidth = width;
            for (const handler of windowListeners.get('resize') || []) handler();
        },
        advance: (ms) => { nowMs += ms; },
        resolveDeferred: (label, response) => {
            const resolve = deferredFetches.get(label);
            assert(resolve, `no deferred fetch is waiting for ${label}`);
            deferredFetches.delete(label);
            resolve(response);
        },
        pollOnce: async () => {
            // pollOnce() schedules its next cycle in its finally block; the
            // most recently scheduled callback is that continuation.
            const timer = scheduledTimers.pop();
            scheduledTimers.length = 0;
            assert(timer, 'a poll timer should be scheduled');
            await timer.fn();
            await drain();
        },
        getReloadCount: () => reloadCount,
        getScheduledTimerCount: () => scheduledTimers.length,
    };
}

const ok = (data) => ({ status: 200, data });
const fail500 = { status: 500, data: {} };

const discussionState = {
    phase: 'discussion',
    session_key: 4,
    discussion_week: 1,
    discussion_questions_version: 2,
    roster_version: 7,
    teams_locked: false,
    my_team: null,
};

const competitionState = {
    phase: 'competition',
    session_key: 4,
    roster_version: 7,
    teams_locked: false,
    my_team: { id: 1, name: 'Team 1' },
    active_team: { id: 2, name: 'Team 2', color: '#abc' },
    active_question: { id: 10, revision: 'r1', title: 'Present' },
    poll_active: true,
    poll_question_key: 'presentation-1',
    poll_remaining: 20,
    presentation_remaining: 60,
};

const pollRoute = (extra = {}) => [
    '/api/poll',
    [
        ok({ changed: true, state_version: 3, state: discussionState, poll_interval: 1000, ...extra }),
        ok({ changed: false, state_version: 3, poll_interval: 1000 }),
    ],
];

function countFetches(fetchLog, prefix) {
    return fetchLog.filter(url => url.startsWith(prefix)).length;
}

async function scenarioRosterRetriesWithBackoff() {
    const session = makeSession([
        ['/api/poll', [
            ok({
                changed: true,
                state_version: 3,
                state: discussionState,
                poll_interval: 1000,
            }),
            ok({
                changed: true,
                state_version: 4,
                state: { ...discussionState, session_elapsed: 1 },
                poll_interval: 1000,
            }),
            ok({ changed: false, state_version: 4, poll_interval: 1000 }),
        ]],
        ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] })]],
        ['/api/teams', [fail500, ok([{ id: 1, name: 'Team 1', color: '#abc', members: [] }])]],
    ]);
    await session.drain();
    assert.strictEqual(countFetches(session.fetchLog, '/api/teams'), 1,
        'first roster sync should have run once');
    assert.strictEqual(session.elementsById['roster-table'].appendedChildren.length, 0,
        'failed roster sync should not render');

    // Backoff not yet due: even a full state poll must not refetch the roster.
    session.fetchLog.length = 0;
    session.advance(100);
    await session.pollOnce();
    assert.strictEqual(session.fetchLog.length, 1,
        `full poll inside backoff should only hit /api/poll, got ${session.fetchLog}`);
    assert(session.fetchLog[0].startsWith('/api/poll?since=3'),
        `next poll should send since=3, got ${session.fetchLog[0]}`);

    // After the backoff, the unchanged poll retries the roster and succeeds.
    session.fetchLog.length = 0;
    session.advance(2000);
    await session.pollOnce();
    assert.strictEqual(countFetches(session.fetchLog, '/api/teams'), 1,
        'unchanged poll should retry the failed roster sync once');
    assert.strictEqual(session.elementsById['roster-table'].appendedChildren.length, 1,
        'retried roster sync should render the roster');

    // Pending flag cleared: steady state is back to the cheap main poll only.
    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    assert.deepStrictEqual(session.fetchLog.map(u => u.split('?')[0]), ['/api/poll'],
        'healthy session should send only the main poll');
    console.error('PASS roster retry');
}

async function scenarioLateJoinRefreshesDiscussionCards() {
    for (const teammateIds of [[], ['S2']]) {
        const members = ['S1', ...teammateIds].map(student_id => ({
            student_id, name: student_id,
        }));
        const team = { id: 1, name: 'Team 1', color: '#abc', members };
        const session = makeSession([
            ['/api/poll', [7, 8, 9].map(version => ok({
                changed: true,
                state_version: version,
                state: { ...discussionState, my_team: team, roster_version: version },
                poll_interval: 1000,
            }))],
            ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
            ['/api/my_responses', [ok({
                phase: 'discussion', session_key: 4, thumb_recipient_ids: [],
            })]],
            ['/api/teams', [
                ok([team]),
                ok([{ ...team, members: [...members].reverse().map(member => ({
                    ...member, name: `${member.name} updated`,
                })) }]),
                ok([{ ...team, members: [...members, { student_id: 'S3', name: 'Late arrival' }] }]),
            ]],
        ], { myTeamId: 1, teammateIds });
        await session.drain();
        assert.strictEqual(session.getReloadCount(), 0,
            'matching rendered membership must not reload on initial roster sync');

        session.advance(1500);
        await session.pollOnce();
        assert.strictEqual(session.getReloadCount(), 0,
            'name or ordering changes without membership changes must not reload');

        vm.runInContext('_studentVoteInFlight = 1', session.sandbox);
        session.advance(1500);
        await session.pollOnce();
        assert.strictEqual(session.getReloadCount(), 0,
            'a newcomer must not interrupt an in-flight vote');
        vm.runInContext('_studentVoteInFlight = 0; completePendingDashboardReload()', session.sandbox);
        assert.strictEqual(session.getReloadCount(), 1,
            teammateIds.length
                ? 'a newcomer should refresh existing teammates discussion cards'
                : 'a newcomer should replace the solo-team message with discussion cards');
    }
    console.error('PASS late join discussion card refresh');
}

async function scenarioDiscussionQuestionsRetry() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [fail500, ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] })]],
        ['/api/teams', [ok([])]],
    ]);
    await session.drain();
    assert.strictEqual(countFetches(session.fetchLog, '/api/discussion_questions'), 1,
        'first discussion-questions sync should have run once');

    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    assert.strictEqual(countFetches(session.fetchLog, '/api/discussion_questions'), 1,
        'unchanged poll should retry the failed discussion-questions sync');

    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    assert.deepStrictEqual(session.fetchLog.map(u => u.split('?')[0]), ['/api/poll'],
        'healthy session should send only the main poll');
    console.error('PASS discussion questions retry');
}

async function scenarioMyResponsesRetry() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [fail500, ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] })]],
        ['/api/teams', [ok([])]],
    ]);
    await session.drain();
    assert.strictEqual(countFetches(session.fetchLog, '/api/my_responses'), 1,
        'first my-responses sync should have run once');

    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    await session.drain();
    assert.strictEqual(countFetches(session.fetchLog, '/api/my_responses'), 1,
        'unchanged poll should retry the failed my-responses sync');

    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    assert.deepStrictEqual(session.fetchLog.map(u => u.split('?')[0]), ['/api/poll'],
        'healthy session should send only the main poll');
    console.error('PASS my-responses retry');
}

async function scenarioAmbiguousThumbWaitsForMatchingReadback() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [
            ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] }),
            ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: ['S2'] }),
        ]],
        ['/api/teams', [ok([])]],
    ]);
    vm.runInContext(
        "_unconfirmedThumbVotes.set('S2', true);" +
        "responseNeedsRefresh = true;",
        session.sandbox
    );
    await session.drain();

    assert.strictEqual(
        vm.runInContext("_unconfirmedThumbVotes.has('S2')", session.sandbox),
        true,
        'a stale mismatch must keep the thumb unconfirmed'
    );
    assert.strictEqual(
        vm.runInContext('responseNeedsRefresh', session.sandbox),
        true,
        'a stale mismatch must keep saved-response retry pending'
    );

    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    await session.drain();

    assert.strictEqual(
        countFetches(session.fetchLog, '/api/my_responses'),
        1,
        'the next compact poll should retry ambiguous thumb read-back'
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedThumbVotes.size', session.sandbox),
        0,
        'a matching database read should confirm and clear the thumb'
    );
    assert.strictEqual(
        vm.runInContext('responseNeedsRefresh', session.sandbox),
        false,
        'matching read-back should stop the saved-response retry'
    );
    console.error('PASS ambiguous thumb read-back retry');
}

async function scenarioPersistentThumbMismatchCannotBlockReload() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [
            ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] }),
        ]],
        ['/api/teams', [ok([])]],
    ]);
    vm.runInContext(
        "_unconfirmedThumbVotes.set('S2', true);" +
        "_unconfirmedThumbSessionKey = 4;" +
        "responseNeedsRefresh = true;" +
        "queueDashboardReload({ phase: 'discussion', session_key: 4 });",
        session.sandbox
    );
    await session.drain();
    assert.strictEqual(
        session.getReloadCount(),
        0,
        'the first mismatch should remain inside the read-back grace'
    );

    session.advance(6000);
    await session.pollOnce();
    await session.drain();
    assert.strictEqual(
        session.getReloadCount(),
        1,
        'a retry completion after the grace must proceed with the reload'
    );
    console.error('PASS persistent thumb mismatch reload deadline');
}

async function scenarioSteadyHealthySession() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] })]],
        ['/api/teams', [ok([])]],
    ]);
    await session.drain();
    for (let cycle = 0; cycle < 3; cycle += 1) {
        session.fetchLog.length = 0;
        session.advance(1500);
        await session.pollOnce();
        assert.deepStrictEqual(session.fetchLog.map(u => u.split('?')[0]), ['/api/poll'],
            `steady cycle ${cycle + 1} should send only the main poll, got ${session.fetchLog}`);
    }
    assert.strictEqual(session.callCounts.get('/api/teams'), 1,
        'roster should be fetched once while its version is unchanged');
    assert.strictEqual(session.callCounts.get('/api/discussion_questions'), 1,
        'discussion questions should be fetched once while their version is unchanged');
    console.error('PASS steady healthy session');
}

async function scenarioDelayedSyncsDoNotOverlapOrBlockPolling() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [{
            defer: 'questions',
            response: ok({ version: 2, questions: [] }),
        }]],
        ['/api/my_responses', [{
            defer: 'responses',
            response: ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] }),
        }]],
        ['/api/teams', [{
            defer: 'roster',
            response: ok([]),
        }]],
    ]);
    await session.drain();
    assert.strictEqual(countFetches(session.fetchLog, '/api/discussion_questions'), 1);
    assert.strictEqual(countFetches(session.fetchLog, '/api/my_responses'), 1);
    assert.strictEqual(countFetches(session.fetchLog, '/api/teams'), 1);

    // The live poll must continue, but each delayed secondary resource keeps
    // exactly one request in flight.
    for (let cycle = 0; cycle < 2; cycle += 1) {
        session.advance(1500);
        await session.pollOnce();
        assert.strictEqual(countFetches(
            session.fetchLog, '/api/discussion_questions'
        ), 1, 'delayed questions request must not overlap');
        assert.strictEqual(countFetches(
            session.fetchLog, '/api/my_responses'
        ), 1, 'delayed responses request must not overlap');
        assert.strictEqual(countFetches(
            session.fetchLog, '/api/teams'
        ), 1, 'delayed roster request must not overlap');
    }

    session.resolveDeferred('questions');
    session.resolveDeferred('responses');
    session.resolveDeferred('roster');
    await session.drain();

    session.fetchLog.length = 0;
    session.advance(1500);
    await session.pollOnce();
    assert.deepStrictEqual(session.fetchLog.map(u => u.split('?')[0]), ['/api/poll'],
        'resolved secondary syncs should clear their pending flags');
    console.error('PASS delayed sync overlap guards');
}

async function scenarioStaleDiscussionResultCannotOverwriteNewContext() {
    const newState = {
        ...discussionState,
        discussion_questions_version: 3,
    };
    const session = makeSession([
        ['/api/poll', [
            ok({
                changed: true,
                state_version: 3,
                state: discussionState,
                poll_interval: 1000,
            }),
            ok({
                changed: true,
                state_version: 4,
                state: newState,
                poll_interval: 1000,
            }),
            ok({ changed: false, state_version: 4, poll_interval: 1000 }),
        ]],
        ['/api/discussion_questions', [
            {
                defer: 'old-questions',
                response: ok({
                    version: 2,
                    questions: [{
                        key: 'week-1-q-old',
                        display_number: 4,
                        title: 'Old question',
                        content: 'Old body',
                    }],
                }),
            },
            ok({
                version: 3,
                questions: [{
                    key: 'week-1-q-new',
                    display_number: 7,
                    title: 'New question',
                    content: 'New body',
                }],
            }),
        ]],
        ['/api/my_responses', [
            ok({ phase: 'discussion', session_key: 4, thumb_recipient_ids: [] }),
        ]],
        ['/api/teams', [ok([])]],
    ]);
    await session.drain();
    assert.strictEqual(countFetches(
        session.fetchLog, '/api/discussion_questions'
    ), 1);

    // Move to a newer question version while the old request is still open.
    session.advance(1500);
    await session.pollOnce();
    assert.strictEqual(countFetches(
        session.fetchLog, '/api/discussion_questions'
    ), 1, 'context change must not create an overlapping questions request');

    session.resolveDeferred('old-questions');
    await session.drain();
    assert.strictEqual(countFetches(
        session.fetchLog, '/api/discussion_questions'
    ), 2, 'the newer context should sync as soon as the stale request settles');
    const html = session.elementsById['student-disc-questions-list'].innerHTML;
    assert(html.includes('New question'),
        `new question version should render, got: ${html}`);
    assert(!html.includes('Old question'), 'stale question version must not render');
    assert(html.includes('disc-question-num">#7</span>'),
        'server-provided discussion number should render');
    console.error('PASS stale discussion result protection');
}

async function scenarioCompactPollClosePreservesDraftWarning() {
    const session = makeSession([
        ['/api/poll', [
            ok({
                changed: true,
                state_version: 8,
                state: { ...competitionState },
                poll_interval: 1000,
            }),
            ok({
                changed: false,
                state_version: 8,
                poll_interval: 1000,
                poll_closed: false,
            }),
            ok({
                changed: false,
                state_version: 8,
                poll_interval: 1000,
                poll_closed: true,
            }),
        ]],
        ['/api/my_responses', [ok({
            phase: 'competition',
            presentation_key: 'presentation-1',
            rating: null,
        })]],
        ['/api/teams', [ok([])]],
    ], { phase: 'competition', myTeamId: 1 });
    await session.drain();
    assert.strictEqual(
        vm.runInContext('activePollOpen', session.sandbox),
        true,
        'initial competition state should open rating controls'
    );
    assert.strictEqual(
        session.elementsById['poll-pulse'].style.display,
        'flex',
        'initial competition state should show the poll pulse'
    );

    vm.runInContext(
        'ratingDraftDirty = true; pollSelections = { q1: 4, q2: 3 };' +
            'activeChallengeRatingsOpen = true;',
        session.sandbox
    );
    session.advance(1500);
    await session.pollOnce();
    assert.strictEqual(
        vm.runInContext('activePollOpen', session.sandbox),
        true,
        'compact open heartbeat should keep rating controls open'
    );
    assert.strictEqual(
        vm.runInContext('activeChallengeRatingsOpen', session.sandbox),
        true,
        'compact open heartbeat should keep challenge controls open'
    );
    assert.strictEqual(
        session.elementsById['poll-pulse'].style.display,
        'flex',
        'compact open heartbeat should keep the countdown visible'
    );
    session.advance(1500);
    await session.pollOnce();

    assert.strictEqual(
        vm.runInContext('activePollOpen', session.sandbox),
        false,
        'compact close signal should close the rating controls'
    );
    assert.strictEqual(
        vm.runInContext('ratingPollOpenForTimer', session.sandbox),
        false,
        'compact close signal should close the timer rating state'
    );
    assert.strictEqual(
        vm.runInContext('activeChallengeRatingsOpen', session.sandbox),
        false,
        'compact close signal should close challenge rating controls'
    );
    assert.strictEqual(
        vm.runInContext(
            'lastReceivedState.challenge_ratings_open', session.sandbox
        ),
        false,
        'compact close signal should update the challenge state snapshot'
    );
    assert.strictEqual(
        vm.runInContext('ratingDraftDirty', session.sandbox),
        true,
        'unsaved draft flag must survive the close signal'
    );
    assert.strictEqual(
        vm.runInContext('pollSelections.q1', session.sandbox),
        4,
        'unsaved selections must survive the close signal'
    );
    assert.strictEqual(session.elementsById['btn-submit-rating'].disabled, true,
        'submit button should be disabled after the poll closes');
    assert.strictEqual(session.elementsById['poll-pulse'].style.display, 'none',
        'poll pulse should stop after the poll closes');
    assert.strictEqual(
        session.elementsById['team-grading-section'].style.display,
        'block',
        'grading section should remain visible for the unsaved-draft warning'
    );
    assert.strictEqual(session.elementsById['poll-section'].style.display, 'block',
        'draft controls and warning should remain visible');
    assert(
        session.elementsById['rating-status'].textContent.includes(
            'Rating window closed'
        ),
        'student should see that the unsaved draft was not recorded'
    );
    console.error('PASS compact poll close draft preservation');
}

async function scenarioStudentRatingLayoutAndSavedClose() {
    const session = makeSession([
        ['/api/poll', [
            ok({ changed: true, state_version: 1, state: { ...competitionState } }),
            ok({ changed: true, state_version: 2, state: { ...competitionState } }),
            ok({ changed: false, state_version: 2, poll_closed: true }),
        ]],
        ['/api/my_responses', [ok({
            phase: 'competition', presentation_key: 'presentation-1',
            rating: { q1_developed: 4, q2_easy: 3 },
        })]],
        ['/api/teams', [ok([])]],
    ], { phase: 'competition', myTeamId: 1, width: 390 });
    await session.drain();
    const elements = session.elementsById;
    const question = elements['question-display'];
    assert.strictEqual(question.open, false,
        'phone readers should reach the eligible rating before the full question');
    assert.strictEqual(question.scrollCalls, 0,
        'poll updates must not scroll to the question');
    assert.strictEqual(elements['student-live-activity'].dataset.ratingVisible, 'true');
    assert.strictEqual(elements['poll-section'].style.display, 'block');

    session.sandbox.window.viewCurrentQuestion();
    assert.strictEqual(question.open, true,
        'one deliberate action should reveal the full question');
    assert.strictEqual(question.scrollCalls, 1);
    assert.strictEqual(elements['question-summary'].focusCalls, 1,
        'an explicit reveal should put keyboard navigation at the question');
    session.advance(1500);
    await session.pollOnce();
    assert.strictEqual(question.open, true,
        'a subsequent state refresh must preserve the reader\'s disclosure choice');
    assert.strictEqual(question.scrollCalls, 1,
        'refreshing the open question must not move the viewport');
    assert.strictEqual(elements['question-summary'].focusCalls, 1,
        'live updates must not move keyboard focus again');

    session.advance(1500);
    await session.pollOnce();
    assert.strictEqual(elements['team-grading-section'].style.display, 'block');
    assert.strictEqual(elements['poll-section'].style.display, 'block',
        'a saved response should stay visible after its window closes');
    assert.strictEqual(elements['btn-submit-rating'].disabled, true);
    assert.match(elements['rating-status'].textContent, /Q1 4\/5.*Q2 3\/5/);
    assert.match(elements['rating-window-state'].textContent, /closed/);
    console.error('PASS student disclosure and saved rating close');
}

async function scenarioStudentRatingWaitingAndEligibility() {
    for (const { myTeamId, activeTeamId, eligible, width } of [
        { myTeamId: 1, activeTeamId: 2, eligible: true, width: 390 },
        { myTeamId: 1, activeTeamId: 1, eligible: false, width: 390 },
        { myTeamId: null, activeTeamId: 2, eligible: false, width: 390 },
        { myTeamId: 1, activeTeamId: 2, eligible: true, width: 1280 },
    ]) {
        const state = {
            ...competitionState,
            my_team: myTeamId ? { id: myTeamId } : null,
            active_team: { id: activeTeamId, name: 'Presenting team' },
            poll_active: false,
            poll_remaining: 0,
        };
        const session = makeSession([
            ['/api/poll', [ok({ changed: true, state_version: 1, state })]],
            ['/api/my_responses', [ok({
                phase: 'competition', presentation_key: 'presentation-1', rating: null,
            })]],
            ['/api/teams', [ok([])]],
        ], { phase: 'competition', myTeamId, width });
        await session.drain();
        const elements = session.elementsById;
        assert.strictEqual(elements['team-grading-section'].style.display,
            eligible ? 'block' : 'none');
        assert.strictEqual(elements['poll-section'].style.display, 'none');
        assert.strictEqual(elements['btn-submit-rating'].disabled, true);
        assert.strictEqual(elements['question-display'].open, true,
            'the current question stays open before any rating action is available');
        assert.strictEqual(elements['student-live-activity'].dataset.ratingFirst, 'false');
        if (eligible) assert.match(elements['rating-window-state'].textContent, /Waiting/);
    }
    console.error('PASS student waiting and rating eligibility');
}

async function scenarioStudentActionPriorityTransitions() {
    for (const deliberatelyOpen of [false, true]) {
        const session = makeSession([
            ['/api/poll', [
                ok({ changed: true, state_version: 1,
                    state: { ...competitionState, poll_active: false, poll_remaining: 0 } }),
                ok({ changed: true, state_version: 2, state: { ...competitionState } }),
            ]],
            ['/api/my_responses', [ok({
                phase: 'competition', presentation_key: 'presentation-1', rating: null,
            })]],
            ['/api/teams', [ok([])]],
        ], { phase: 'competition', myTeamId: 1, width: 390 });
        await session.drain();
        const elements = session.elementsById;
        assert.strictEqual(elements['question-display'].open, true);
        if (deliberatelyOpen) session.sandbox.window.viewCurrentQuestion();
        session.advance(1500);
        await session.pollOnce();
        assert.strictEqual(elements['student-live-activity'].dataset.ratingFirst, 'true');
        assert.strictEqual(elements['question-display'].open, deliberatelyOpen,
            'opening a real poll should prioritize voting and retain an explicit reader choice');
        assert.strictEqual(elements['question-display'].focusCalls, 0);
        assert.strictEqual(elements['question-summary'].focusCalls, deliberatelyOpen ? 1 : 0);
        assert.strictEqual(elements['question-display'].scrollCalls, deliberatelyOpen ? 1 : 0);
    }
    for (const challengerTeam of [1, 3]) {
        const state = {
            ...competitionState, poll_active: false, poll_remaining: 0,
            challenge_ratings_open: true,
            active_challenges: [{ challenge_key: 'ch1', challenge_num: 1,
                challenger_team_id: challengerTeam, challenger_id: 'S3' }],
        };
        const session = makeSession([
            ['/api/poll', [
                ok({ changed: true, state_version: 1, state }),
                ok({ changed: false, state_version: 1, poll_closed: true }),
            ]],
            ['/api/my_responses', [ok({
                phase: 'competition', presentation_key: 'presentation-1', rating: null,
            })]],
            ['/api/teams', [ok([])]],
        ], { phase: 'competition', myTeamId: 1, width: 390 });
        await session.drain();
        const activity = session.elementsById['student-live-activity'];
        assert.strictEqual(activity.dataset.ratingFirst, 'false');
        assert.strictEqual(activity.dataset.challengeFirst, String(challengerTeam !== 1),
            'an eligible challenge-only rating should be reachable before the question');
        if (challengerTeam !== 1) {
            vm.runInContext("challengeRatingDrafts.add('ch1')", session.sandbox);
            session.advance(1500);
            await session.pollOnce();
            assert.strictEqual(vm.runInContext('activeChallengeRatingsOpen', session.sandbox), false);
            assert.strictEqual(activity.dataset.challengeFirst, 'true',
                'a challenge draft should keep its closed-window explanation accessible');
        }
    }
    console.error('PASS student action priority transitions');
}

async function scenarioStudentNaturalSectionOrder() {
    const questionFirst = ['team-section', 'team-grading-section', 'challenge-section'];
    const ratingFirst = ['team-grading-section', 'team-section', 'challenge-section'];
    const bothFirst = ['team-grading-section', 'challenge-section', 'team-section'];
    const challengeFirst = ['challenge-section', 'team-section', 'team-grading-section'];
    const challenge = { challenge_key: 'ch1', challenge_num: 1,
        challenger_team_id: 3, challenger_id: 'S3' };
    const cases = [
        { name: 'before ratings', order: questionFirst },
        { name: 'presentation open', pollOpen: true, order: ratingFirst },
        { name: 'both open', pollOpen: true, challengeOpen: true,
            challenges: [challenge], order: bothFirst },
        { name: 'challenge only', challengeOpen: true,
            challenges: [challenge], order: challengeFirst },
        { name: 'closed without responses', closed: true, order: questionFirst },
        { name: 'closed saved presentation', closed: true,
            rating: { q1_developed: 4, q2_easy: 3 }, order: ratingFirst },
        { name: 'closed saved challenge', closed: true, challenges: [challenge],
            challengeRatings: { ch1: 5 }, order: challengeFirst },
        { name: 'closed saved both', closed: true, challenges: [challenge],
            rating: { q1_developed: 4, q2_easy: 3 },
            challengeRatings: { ch1: 5 }, order: bothFirst },
        { name: 'presenting team', myTeamId: 2, pollOpen: true,
            challengeOpen: true, challenges: [challenge], order: questionFirst },
        { name: 'unassigned student', myTeamId: null, pollOpen: true,
            challengeOpen: true, challenges: [challenge], order: questionFirst },
        { name: 'own challenger team', challengeOpen: true,
            challenges: [{ ...challenge, challenger_team_id: 1 }], order: questionFirst },
        { name: 'between presentations', noPresentation: true, order: questionFirst },
    ];
    for (const width of [390, 959, 960, 1280]) {
        for (const example of cases) {
            const myTeamId = 'myTeamId' in example ? example.myTeamId : 1;
            const state = { ...competitionState,
                my_team: myTeamId ? { id: myTeamId } : null,
                poll_active: Boolean(example.pollOpen),
                poll_remaining: example.pollOpen ? 20 : 0,
                poll_started_at: example.closed ? 100 : null,
                challenge_ratings_open: Boolean(example.challengeOpen),
                active_challenges: example.challenges || [],
            };
            if (example.noPresentation) Object.assign(state, {
                active_team: null, active_question: null, poll_question_key: null,
            });
            const session = makeSession([
                ['/api/poll', [
                    ok({ changed: true, state_version: 1, state }),
                    ok({ changed: true, state_version: 2, state }),
                ]],
                ['/api/my_responses', [ok({ phase: 'competition',
                    presentation_key: 'presentation-1', rating: example.rating || null,
                    challenge_ratings: example.challengeRatings || {},
                })]],
                ['/api/teams', [ok([])]],
            ], { phase: 'competition', myTeamId, width });
            await session.drain();
            const activity = session.elementsById['student-live-activity'];
            assert.deepStrictEqual(activity.children.map(section => section.id),
                width < 960 ? example.order : questionFirst,
                `${example.name} at ${width}px must have natural reading and Tab order`);
            const insertions = activity.insertions;
            session.advance(1500);
            await session.pollOnce();
            assert.strictEqual(activity.insertions, insertions,
                'a routine refresh must leave already ordered sections attached');
        }
    }
    console.error('PASS natural student order across 48 state/viewport combinations');
}

async function scenarioStudentOrderPreservesFocusedControls() {
    const challenge = { challenge_key: 'ch1', challenge_num: 1,
        challenger_team_id: 3, challenger_id: 'S3' };
    const waiting = { ...competitionState, poll_active: false, poll_remaining: 0,
        challenge_ratings_open: false, active_challenges: [challenge] };
    const open = { ...waiting, poll_active: true, poll_remaining: 20,
        challenge_ratings_open: true };
    for (const focusedId of ['btn-submit-rating', 'question-summary', 'challenge-question-link']) {
        const session = makeSession([
            ['/api/poll', [
                ok({ changed: true, state_version: 1, state: waiting }),
                ok({ changed: true, state_version: 2, state: open }),
                ok({ changed: true, state_version: 3, state: open }),
                ok({ changed: false, state_version: 3, poll_closed: true }),
            ]],
            ['/api/my_responses', [ok({ phase: 'competition',
                presentation_key: 'presentation-1', rating: null,
            })]],
            ['/api/teams', [ok([])]],
        ], { phase: 'competition', myTeamId: 1, width: 390 });
        await session.drain();
        const elements = session.elementsById;
        const activity = elements['student-live-activity'];
        const originalSections = activity.children.slice();
        const focused = elements[focusedId];
        focused.focus();
        vm.runInContext(
            "ratingDraftDirty = true; pollSelections = { q1: 2, q2: 3 };" +
            "savedPollSelections = { q1: 4, q2: 3 };" +
            "challengeRatings = { ch1: 2 }; savedChallengeRatings = { ch1: 5 };" +
            "challengeRatingDrafts.add('ch1');", session.sandbox);
        session.advance(1500);
        await session.pollOnce();
        assert.deepStrictEqual(activity.children.map(section => section.id),
            ['team-grading-section', 'challenge-section', 'team-section']);
        if (focusedId === 'question-summary') {
            assert.strictEqual(elements['question-display'].open, true,
                'prioritizing a new vote must not collapse the focused question');
        }
        const insertions = activity.insertions;
        session.advance(1500);
        await session.pollOnce();
        assert.strictEqual(activity.insertions, insertions,
            'a background update must not remount the rating controls');
        session.advance(1500);
        await session.pollOnce();
        for (const width of [960, 1280, 959, 390, 390]) {
            session.resize(width);
            assert.deepStrictEqual(activity.children.map(section => section.id),
                width < 960
                    ? ['team-grading-section', 'challenge-section', 'team-section']
                    : ['team-section', 'team-grading-section', 'challenge-section']);
            assert.strictEqual(session.sandbox.document.activeElement, focused,
                `${focusedId} must stay focused through priority changes and resize`);
        }
        assert.strictEqual(session.sandbox.document.focusDetachments, 0,
            'the focused section must never be detached and refocused');
        assert.strictEqual(focused.focusCalls, 1,
            'background updates must not call focus to repair an avoidable blur');
        for (const section of originalSections) assert(activity.children.includes(section));
        assert.strictEqual(elements['poll-section'].children[0], elements['btn-submit-rating']);
        assert.strictEqual(vm.runInContext('pollSelections.q1', session.sandbox), 2);
        assert.strictEqual(vm.runInContext('savedPollSelections.q1', session.sandbox), 4);
        assert.strictEqual(vm.runInContext('ratingDraftDirty', session.sandbox), true);
        assert.strictEqual(vm.runInContext('challengeRatings.ch1', session.sandbox), 2);
        assert.strictEqual(vm.runInContext('savedChallengeRatings.ch1', session.sandbox), 5);
        assert.strictEqual(vm.runInContext("challengeRatingDrafts.has('ch1')", session.sandbox), true);
    }
    console.error('PASS focused controls, drafts, and saved values survive section reordering');
}

async function scenarioPresentationEndClearsChallengePanel() {
    const waitingState = {
        ...competitionState,
        active_team: null,
        active_question: null,
        poll_active: false,
        poll_question_key: null,
        poll_remaining: 0,
        active_challenges: [],
        challenge_ratings_open: false,
    };
    const session = makeSession([
        ['/api/poll', [
            ok({
                changed: true,
                state_version: 10,
                state: { ...competitionState },
                poll_interval: 1000,
            }),
            ok({
                changed: true,
                state_version: 11,
                state: waitingState,
                poll_interval: 1000,
            }),
        ]],
        ['/api/my_responses', [ok({
            phase: 'competition',
            presentation_key: 'presentation-1',
            rating: null,
            challenge_ratings: {},
            challenge_hand_raised: false,
        })]],
        ['/api/teams', [ok([])]],
    ], { phase: 'competition', myTeamId: 1 });
    await session.drain();
    assert.strictEqual(
        session.elementsById['challenge-section'].style.display,
        'block',
        'active presentation should show challenge controls'
    );

    session.advance(1500);
    await session.pollOnce();
    assert.strictEqual(
        session.elementsById['challenge-section'].style.display,
        'none',
        'ended presentation must hide stale challenge controls'
    );
    assert.strictEqual(
        vm.runInContext('activeChallengePresentationKey', session.sandbox),
        null,
        'ended presentation must clear the prior challenge context'
    );
    console.error('PASS presentation end clears challenge panel');
}



async function scenarioIntentionalDemoNavigationStopsStalePoll() {
    const session = makeSession([
        ['/api/poll', [{
            defer: 'stale-poll',
            response: ok({
                changed: true,
                state_version: 4,
                state: { ...discussionState, phase: 'competition' },
                poll_interval: 1000,
            }),
        }]],
    ]);
    await session.drain();

    assert.strictEqual(
        vm.runInContext('window.confirmDemoReset()', session.sandbox),
        true,
        'confirmed demo reset should begin intentional navigation'
    );
    assert.strictEqual(
        vm.runInContext('window.confirmDemoReset()', session.sandbox),
        false,
        'a duplicate demo reset submission should be blocked'
    );
    session.resolveDeferred('stale-poll');
    await session.drain();

    assert.strictEqual(session.getReloadCount(), 0,
        'a stale poll must not reload the old dashboard during reset');
    assert.strictEqual(session.sandbox.window.location.href, '',
        'a stale poll must not override the reset destination');
    assert.strictEqual(session.getScheduledTimerCount(), 0,
        'no further poll should be scheduled during reset navigation');
    console.error('PASS intentional demo navigation poll guard');
}

(async () => {
    await scenarioRosterRetriesWithBackoff();
    await scenarioLateJoinRefreshesDiscussionCards();
    await scenarioDiscussionQuestionsRetry();
    await scenarioMyResponsesRetry();
    await scenarioAmbiguousThumbWaitsForMatchingReadback();
    await scenarioPersistentThumbMismatchCannotBlockReload();
    await scenarioSteadyHealthySession();
    await scenarioDelayedSyncsDoNotOverlapOrBlockPolling();
    await scenarioStaleDiscussionResultCannotOverwriteNewContext();
    await scenarioCompactPollClosePreservesDraftWarning();
    await scenarioStudentRatingLayoutAndSavedClose();
    await scenarioStudentRatingWaitingAndEligibility();
    await scenarioStudentActionPriorityTransitions();
    await scenarioStudentNaturalSectionOrder();
    await scenarioStudentOrderPreservesFocusedControls();
    await scenarioPresentationEndClearsChallengePanel();
    await scenarioIntentionalDemoNavigationStopsStalePoll();
    console.error('All poll-retry scenarios passed.');
})().catch(error => {
    console.error(error);
    process.exit(1);
});
