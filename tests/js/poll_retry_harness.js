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
        hidden: false,
        disabled: false,
        title: '',
        appendChild(child) { this.appendedChildren.push(child); },
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
        you: 'S1',
        teamId: options.myTeamId == null ? '' : String(options.myTeamId),
    };
    const elementsById = {
        'student-disc-questions-list': makeElement('student-disc-questions-list'),
        'roster-table': makeElement('roster-table'),
        'team-section': makeElement('team-section'),
        'team-grading-section': makeElement('team-grading-section'),
        'poll-section': makeElement('poll-section'),
        'poll-pulse': makeElement('poll-pulse'),
        'poll-pulse-text': makeElement('poll-pulse-text'),
        'rating-status': makeElement('rating-status'),
        'btn-submit-rating': makeElement('btn-submit-rating'),
    };

    let nowMs = 1_000_000;
    class FakeDate extends Date {
        static now() { return nowMs; }
    }

    const noopTimer = () => 0;
    // The poll loop schedules its next cycle through the global setTimeout;
    // capture those callbacks so the harness can drive cycles deterministically.
    const scheduledTimers = [];
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
        addEventListener() {},
        location: {
            href: '',
            reload() { reloadCount += 1; },
        },
    };
    const documentStub = {
        hidden: false,
        body: makeElement('body'),
        querySelector(sel) {
            if (sel === '.dashboard') return dashboardEl;
            return null;
        },
        querySelectorAll() { return []; },
        getElementById(id) { return elementsById[id] || null; },
        createElement() { return makeElement(); },
        addEventListener() {},
    };

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
    discussion_week: 1,
    discussion_questions_version: 2,
    roster_version: 7,
    teams_locked: false,
    my_team: null,
};

const competitionState = {
    phase: 'competition',
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
        ['/api/my_responses', [ok({ phase: 'discussion', thumb_recipient_ids: [] })]],
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

async function scenarioDiscussionQuestionsRetry() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [fail500, ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [ok({ phase: 'discussion', thumb_recipient_ids: [] })]],
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
        ['/api/my_responses', [fail500, ok({ phase: 'discussion', thumb_recipient_ids: [] })]],
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

async function scenarioSteadyHealthySession() {
    const session = makeSession([
        pollRoute(),
        ['/api/discussion_questions', [ok({ version: 2, questions: [] })]],
        ['/api/my_responses', [ok({ phase: 'discussion', thumb_recipient_ids: [] })]],
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
            response: ok({ phase: 'discussion', thumb_recipient_ids: [] }),
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
            ok({ phase: 'discussion', thumb_recipient_ids: [] }),
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
                state: competitionState,
                poll_interval: 1000,
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
        'ratingDraftDirty = true; pollSelections = { q1: 4, q2: 3 };',
        session.sandbox
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
    await scenarioDiscussionQuestionsRetry();
    await scenarioMyResponsesRetry();
    await scenarioSteadyHealthySession();
    await scenarioDelayedSyncsDoNotOverlapOrBlockPolling();
    await scenarioStaleDiscussionResultCannotOverwriteNewContext();
    await scenarioCompactPollClosePreservesDraftWarning();
    await scenarioIntentionalDemoNavigationStopsStalePoll();
    console.error('All poll-retry scenarios passed.');
})().catch(error => {
    console.error(error);
    process.exit(1);
});
