'use strict';

/* Behavior harness for vote retry and progress-state handling. */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(
    __dirname, '..', '..', 'static', 'js', 'app.js'
);
const source = fs.readFileSync(APP_JS, 'utf8');

function makeStatusElement() {
    return {
        dataset: {},
        hidden: true,
        textContent: '',
    };
}

function response(status, data) {
    return {
        status,
        ok: status >= 200 && status < 300,
        json: async () => data,
    };
}

async function main() {
    const listeners = {};
    const timerDelays = [];
    let instructorStatus = null;
    let studentStatus = makeStatusElement();
    let fetchLog = [];
    let fetchHandler = null;
    let reloadCount = 0;
    const sessionValues = new Map();
    const sessionStorageStub = {
        getItem(key) {
            return sessionValues.has(key) ? sessionValues.get(key) : null;
        },
        setItem(key, value) {
            sessionValues.set(key, String(value));
        },
        removeItem(key) {
            sessionValues.delete(key);
        },
    };

    const windowStub = {
        location: {
            href: '',
            reload() { reloadCount += 1; },
        },
        addEventListener(type, handler) {
            listeners[type] = handler;
        },
        setTimeout(handler, delay) {
            timerDelays.push(delay);
            if (delay === 2000) setImmediate(handler);
            return timerDelays.length;
        },
        clearTimeout() {},
    };
    const documentStub = {
        getElementById(id) {
            if (id === 'instructor-save-status') return instructorStatus;
            if (id === 'student-vote-status') return studentStatus;
            return null;
        },
    };
    const sandbox = {
        AbortController,
        JSON,
        Math,
        Number,
        Promise,
        console,
        document: documentStub,
        sessionStorage: sessionStorageStub,
        window: windowStub,
        fetch(url, options) {
            fetchLog.push({ url, options });
            return fetchHandler(url, options);
        },
    };
    vm.createContext(sandbox);

    const helperEnd = source.indexOf('function escapeHtmlValue');
    assert(helperEnd > 0);
    vm.runInContext(source.slice(0, helperEnd), sandbox, {
        filename: 'app-requests.js',
    });

    const voteStart = source.indexOf('let _studentVoteInFlight');
    vm.runInContext(
        'let ratingSubmissionInProgress = false;' +
        'let unconfirmedPresentationRating = null;' +
        'let ratingDraftDirty = false;' +
        'const challengeRatingDrafts = new Set();',
        sandbox
    );
    const voteEnd = source.indexOf(
        '// --- Challenge feature state ---',
        voteStart
    );
    assert(voteStart > 0 && voteEnd > voteStart);
    vm.runInContext(source.slice(voteStart, voteEnd), sandbox, {
        filename: 'app-vote-state.js',
    });

    const body = { recipient_id: 'S2', selected: true };
    const serializedBody = JSON.stringify(body);

    let queue = [
        response(503, {
            success: false,
            error: 'Database busy',
            retry_after: 2,
        }),
        response(200, { success: true }),
    ];
    fetchHandler = async () => queue.shift();
    const retried = await sandbox.postJSON(
        '/api/grade_peer',
        body,
        { retryVoteOnce: true }
    );
    assert.strictEqual(retried.success, true);
    assert.strictEqual(fetchLog.length, 2);
    assert.deepStrictEqual(
        fetchLog.map(call => call.options.body),
        [serializedBody, serializedBody]
    );
    assert(timerDelays.includes(2000));

    fetchLog = [];
    let networkAttempts = 0;
    fetchHandler = async () => {
        networkAttempts += 1;
        if (networkAttempts === 1) throw new Error('network interrupted');
        return response(200, { success: true });
    };
    const networkRetried = await sandbox.postJSON(
        '/api/grade_peer',
        body,
        { retryVoteOnce: true }
    );
    assert.strictEqual(networkRetried.success, true);
    assert.strictEqual(fetchLog.length, 2);
    assert.deepStrictEqual(
        fetchLog.map(call => call.options.body),
        [serializedBody, serializedBody]
    );

    fetchLog = [];
    networkAttempts = 0;
    fetchHandler = async () => {
        networkAttempts += 1;
        if (networkAttempts === 1) throw new Error('network interrupted');
        return response(500, {
            success: false,
            error: 'Unexpected error',
        });
    };
    const ambiguousThenFailed = await sandbox.postJSON(
        '/api/grade_peer',
        body,
        { retryVoteOnce: true }
    );
    assert.strictEqual(ambiguousThenFailed.success, false);
    assert.strictEqual(ambiguousThenFailed._network_error, true);
    assert.strictEqual(fetchLog.length, 2);

    fetchLog = [];
    queue = [
        response(500, { success: false, error: 'Unexpected error' }),
        response(200, { success: true }),
    ];
    fetchHandler = async () => queue.shift();
    const notRetried = await sandbox.postJSON(
        '/api/grade_peer',
        body,
        { retryVoteOnce: true }
    );
    assert.strictEqual(notRetried._status, 500);
    assert.strictEqual(fetchLog.length, 1);

    fetchLog = [];
    queue = [
        response(503, {
            success: false,
            error: 'Database busy',
            retry_after: 2,
        }),
        response(200, { success: true }),
    ];
    fetchHandler = async () => queue.shift();
    await sandbox.postJSON('/api/instructor_action', {});
    assert.strictEqual(fetchLog.length, 1);

    instructorStatus = makeStatusElement();
    fetchLog = [];
    let releaseFetch;
    fetchHandler = () => new Promise(resolve => {
        releaseFetch = resolve;
    });
    const instructorRequest = sandbox.postJSON('/api/set_phase', {});
    assert.strictEqual(instructorStatus.dataset.visible, 'true');
    assert.strictEqual(instructorStatus.textContent, 'Saving');
    assert.strictEqual(instructorStatus.dataset.state, 'saving');
    releaseFetch(response(200, { success: true }));
    await instructorRequest;
    assert.strictEqual(instructorStatus.dataset.visible, 'true');
    assert.strictEqual(instructorStatus.textContent, 'Saved');
    assert.strictEqual(instructorStatus.dataset.state, 'saved');

    let releases = [];
    fetchHandler = () => new Promise(resolve => {
        releases.push(resolve);
    });
    const overlappingPost = sandbox.postJSON('/api/set_phase', {});
    const overlappingDelete = sandbox.deleteJSON('/api/delete_question', {});
    assert.strictEqual(instructorStatus.textContent, 'Saving');
    assert.strictEqual(releases.length, 2);
    releases[0](response(200, { success: true }));
    await overlappingPost;
    assert.strictEqual(instructorStatus.textContent, 'Saving');
    releases[1](response(200, { success: true }));
    await overlappingDelete;
    assert.strictEqual(instructorStatus.textContent, 'Saved');

    releases = [];
    fetchHandler = () => new Promise(resolve => {
        releases.push(resolve);
    });
    const failingPost = sandbox.postJSON('/api/set_phase', {});
    const successfulDelete = sandbox.deleteJSON('/api/delete_question', {});
    assert.strictEqual(instructorStatus.textContent, 'Saving');
    releases[0](response(500, {
        success: false,
        error: 'Unexpected error',
    }));
    await failingPost;
    assert.strictEqual(instructorStatus.textContent, 'Saving');
    releases[1](response(200, { success: true }));
    await successfulDelete;
    assert.strictEqual(instructorStatus.dataset.visible, undefined);
    assert.strictEqual(instructorStatus.textContent, '');

    instructorStatus = null;
    vm.runInContext('beginStudentVote()', sandbox);
    assert.strictEqual(studentStatus.dataset.visible, 'true');
    assert.strictEqual(
        studentStatus.textContent,
        'Submitting your vote ...'
    );
    assert.strictEqual(studentStatus.dataset.state, 'submitting');

    let prevented = false;
    const pendingEvent = {
        preventDefault() { prevented = true; },
        returnValue: undefined,
    };
    listeners.beforeunload(pendingEvent);
    assert.strictEqual(prevented, true);
    assert.strictEqual(pendingEvent.returnValue, '');

    vm.runInContext(
        "finishStudentVote(true, 'Vote submitted', '')",
        sandbox
    );
    assert.strictEqual(studentStatus.textContent, 'Vote submitted');
    assert.strictEqual(studentStatus.dataset.state, 'submitted');

    prevented = false;
    const settledEvent = {
        preventDefault() { prevented = true; },
        returnValue: undefined,
    };
    listeners.beforeunload(settledEvent);
    assert.strictEqual(prevented, false);

    vm.runInContext('ratingDraftDirty = true;', sandbox);
    prevented = false;
    const mainDraftEvent = {
        preventDefault() { prevented = true; },
        returnValue: undefined,
    };
    listeners.beforeunload(mainDraftEvent);
    assert.strictEqual(prevented, true);
    vm.runInContext(
        'ratingDraftDirty = false;' +
            "challengeRatingDrafts.add('challenge-draft');",
        sandbox
    );
    prevented = false;
    const challengeDraftEvent = {
        preventDefault() { prevented = true; },
        returnValue: undefined,
    };
    listeners.beforeunload(challengeDraftEvent);
    assert.strictEqual(prevented, true);
    vm.runInContext('challengeRatingDrafts.clear()', sandbox);

    vm.runInContext(
        "_unconfirmedThumbVotes.set('S2', true)",
        sandbox
    );
    prevented = false;
    const uncertainEvent = {
        preventDefault() { prevented = true; },
        returnValue: undefined,
    };
    listeners.beforeunload(uncertainEvent);
    assert.strictEqual(prevented, true);

    sessionValues.delete('popping-unconfirmed-teammate-vote-warning');
    vm.runInContext('forcedPageNavigationInProgress = true', sandbox);
    prevented = false;
    listeners.beforeunload(uncertainEvent);
    assert.strictEqual(
        prevented,
        false,
        'an expired-session redirect must not be cancelable'
    );
    assert.strictEqual(
        sessionValues.get('popping-unconfirmed-teammate-vote-warning'),
        '1',
        'forced navigation must preserve the uncertainty warning'
    );
    vm.runInContext(
        'forcedPageNavigationInProgress = false;' +
        '_unconfirmedThumbVotes.clear()',
        sandbox
    );

    vm.runInContext(
        "beginStudentVote();" +
        "beginStudentVote();" +
        "_unconfirmedThumbVotes.set('S2', true);" +
        "finishStudentVote(false, '', " +
            "'Could not confirm your vote. Checking ...', true);" +
        "finishStudentVote(false, '', " +
            "'Vote not submitted. Please try again.', false);" +
        "beginStudentVote();" +
        "finishStudentVote(true, 'Vote submitted', '');" +
        "reconcileUnconfirmedThumbVotes(new Set());" +
        "if (_unconfirmedThumbVotes.size !== 1) " +
            "throw new Error('early mismatch cleared ambiguity');" +
        "reconcileUnconfirmedThumbVotes(new Set(['S2']));",
        sandbox
    );
    assert.strictEqual(
        studentStatus.textContent,
        'Vote not submitted. Please try again.'
    );
    assert.strictEqual(studentStatus.dataset.state, 'error');

    vm.runInContext(
        "_unconfirmedThumbVotes.set('S2', true);" +
        "_unconfirmedThumbSessionKey = 7;" +
        "queueDashboardReload({ phase: 'discussion', session_key: 7 });",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('completePendingDashboardReload()', sandbox),
        false,
        'a current-session discussion reload should allow a brief read-back'
    );
    assert.strictEqual(reloadCount, 0);
    vm.runInContext(
        "queueDashboardReload({ phase: 'competition', session_key: 7 });",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('completePendingDashboardReload()', sandbox),
        true,
        'a phase exit must carry the unresolved warning and reload'
    );
    assert.strictEqual(reloadCount, 1);
    assert.strictEqual(
        sessionValues.get('popping-unconfirmed-teammate-vote-warning'),
        '1'
    );

    vm.runInContext(
        "_allowAutomaticVoteNavigation = false;" +
        "_unconfirmedThumbVotes.clear();" +
        "_unconfirmedThumbVotes.set('S3', true);" +
        "_unconfirmedThumbSessionKey = 8;" +
        "queueDashboardReload({ phase: 'discussion', session_key: 8 });" +
        "_pendingDashboardReloadStartedAt -= " +
            "DASHBOARD_RELOAD_READBACK_GRACE_MS + 1;",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('completePendingDashboardReload()', sandbox),
        true,
        'a persistent read-back mismatch must not block reload forever'
    );
    assert.strictEqual(reloadCount, 2);

    vm.runInContext(
        "_allowAutomaticVoteNavigation = false;" +
        "_unconfirmedThumbVotes.clear();" +
        "_unconfirmedThumbSessionKey = null;" +
        "queueDashboardReload({ phase: 'ended', session_key: 8 });" +
        "ratingDraftDirty = true;",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('completePendingDashboardReload()', sandbox),
        true,
        'an automatic phase reload may proceed after recording a draft warning'
    );
    assert.strictEqual(reloadCount, 3);
    assert.strictEqual(
        sessionValues.get('popping-unsaved-rating-draft-warning'),
        '1',
        'automatic reload must report a selected rating that was not submitted'
    );

    vm.runInContext(
        "_unconfirmedThumbVotes.set('old', true);" +
        "_unconfirmedThumbSessionKey = 8;" +
        "discardOldSessionThumbUncertainty({ session_key: 9 });",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedThumbVotes.size', sandbox),
        0,
        'old-session thumb uncertainty must be discarded safely'
    );
    assert.match(studentStatus.textContent, /previous session/i);

    vm.runInContext(
        "_allowAutomaticVoteNavigation = false;" +
        "_pendingDashboardReloadPhase = null;" +
        "ratingDraftDirty = false;" +
        "_unconfirmedThumbVotes.clear();" +
        "_unconfirmedThumbVotes.set('S2', true);" +
        "_studentVoteBatchFailure = " +
            "'Could not confirm your vote. Click the thumb again to retry.';" +
        "_studentVoteKnownFailure = false;" +
        "beginStudentVote();" +
        "confirmSuccessfulThumbVote('S2', true);" +
        "finishStudentVote(true, 'Vote submitted', '');",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedThumbVotes.size', sandbox),
        0
    );
    assert.strictEqual(studentStatus.textContent, 'Vote submitted');
    assert.strictEqual(studentStatus.dataset.state, 'submitted');

    assert(
        source.includes(
            "document.querySelectorAll('#poll-section .star-row .star')"
        ),
        'main rating availability must not disable challenge stars'
    );
    assert(
        source.includes("'|challenges=' + challengeKeys"),
        'challenge keys must participate in saved-response hydration context'
    );
    assert(
        source.includes("settling ? 'warning' : 'error'"),
        'intentional rating settlement must not appear as an error'
    );
    assert(
        (source.match(/btn\.disabled = ratingsSettling;/g) || []).length >= 2,
        'rebuilt Select and Clear buttons must remain disabled while ' +
            'final ratings are settling'
    );

    assert(
        source.includes('discardStalePresentationUncertainty(_lastState);'),
        'saved-response retries must prune stale presentation uncertainty'
    );
    vm.runInContext(
        "let activeChallengePresentationKey = 'pres-1';" +
        "let activeChallengeRatingsOpen = true;" +
        "let challengeRatings = { ch1: 4 };" +
        "let savedChallengeRatings = {};" +
        "challengeRatingDrafts.add('ch1');" +
        "const challengeRatingSubmissions = new Map();" +
        "const _activeChallengeIdentities = new Map();" +
        "let challengeHandRaised = false;" +
        "let challengeHandConfirmationPending = null;" +
        "let _challengeToggleInFlight = false;" +
        "let lastReceivedState = {" +
            "poll_question_key: 'pres-1'," +
            "active_challenges: [{ challenge_key: 'ch1' }]" +
        "};" +
        "let responseNeedsRefresh = false;" +
        "let _responseRequestId = 0;" +
        "function getPresentationKey(state) { return state.poll_question_key; }" +
        "function renderChallengeUI() {}" +
        "function challengeSubmissionKey(presentationKey, challengeKey) {" +
            "return presentationKey + ':' + challengeKey;" +
        "}" +
        "function challengeIsSubmitting(challengeKey) {" +
            "return challengeRatingSubmissions.get(challengeKey) === " +
                "activeChallengePresentationKey;" +
        "}" +
        "function updateChallengeRatingCard() {}" +
        "let lastToastMessage = null;" +
        "let lastToastType = null;" +
        "function showToast(message, type) {" +
            "lastToastMessage = message; lastToastType = type;" +
        "}" +
        "window.requestDashboardPoll = function() {};",
        sandbox
    );

    const resetStart = source.indexOf(
        'function resetChallengePresentation(presentationKey) {'
    );
    const reconcileStart = source.indexOf(
        'function reconcileActiveChallengeState(challenges, presentationKey) {'
    );
    const reconcileEnd = source.indexOf(
        'function updateChallengeRatingCard(challengeKey) {',
        reconcileStart
    );
    assert(resetStart > 0 && reconcileStart > resetStart);
    assert(reconcileStart > 0 && reconcileEnd > reconcileStart);
    vm.runInContext(
        source.slice(resetStart, reconcileStart),
        sandbox,
        { filename: 'app-challenge-reset.js' }
    );
    vm.runInContext(
        source.slice(reconcileStart, reconcileEnd),
        sandbox,
        { filename: 'app-challenge-reconcile.js' }
    );

    const discardStart = source.indexOf(
        'function discardStalePresentationUncertainty(state) {'
    );
    const discardEnd = source.indexOf(
        'function challengeSubmissionKey(presentationKey, challengeKey) {',
        discardStart
    );
    assert(discardStart > 0 && discardEnd > discardStart);
    vm.runInContext(
        source.slice(discardStart, discardEnd),
        sandbox,
        { filename: 'app-stale-presentation-uncertainty.js' }
    );
    vm.runInContext(
        "unconfirmedPresentationRating = { presentationKey: 'pres-old' };" +
        "_unconfirmedChallengeRatings.set('pres-old:ch-old', {" +
            "presentationKey: 'pres-old', challengeKey: 'ch-old', score: 4" +
        "});" +
        "challengeHandConfirmationPending = {" +
            "presentationKey: 'pres-old', desiredRaised: true" +
        "};" +
        "discardStalePresentationUncertainty({" +
            "phase: 'competition', poll_question_key: 'pres-1'" +
        "});",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('unconfirmedPresentationRating', sandbox),
        null,
        'an old presentation rating uncertainty must be discarded'
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedChallengeRatings.size', sandbox),
        0,
        'old challenge rating uncertainty must not cause endless read-back'
    );
    assert.strictEqual(
        vm.runInContext('challengeHandConfirmationPending', sandbox),
        null,
        'an old hand uncertainty must not survive into a new presentation'
    );
    assert.match(studentStatus.textContent, /earlier presentation/i);
    vm.runInContext(
        "savedChallengeRatings.ch1 = 4;" +
        "challengeRatings.old = 5;" +
        "savedChallengeRatings.old = 5;" +
        "challengeRatingDrafts.add('old');" +
        "challengeRatingSubmissions.set('old', 'pres-1');" +
        "_activeChallengeIdentities.set('ch1', '1:2:1');" +
        "_activeChallengeIdentities.set('old', '2:3:2');" +
        "_unconfirmedChallengeRatings.set('pres-1:old', {" +
            "presentationKey: 'pres-1', challengeKey: 'old', score: 5" +
        "});" +
        "reconcileActiveChallengeState([" +
            "{ challenge_key: 'replacement', challenger_id: 3," +
            " challenger_team_id: 4, challenge_num: 1 }" +
        "], 'pres-1');",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext("'old' in challengeRatings", sandbox),
        false,
        'a cleared challenge must lose its local draft'
    );
    assert.strictEqual(
        vm.runInContext("'ch1' in savedChallengeRatings", sandbox),
        false,
        'a replacement challenge must not inherit saved stars'
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedChallengeRatings.size', sandbox),
        0,
        'a cleared challenge must not leave an unload warning forever'
    );
    assert.strictEqual(
        vm.runInContext("challengeRatingSubmissions.has('old')", sandbox),
        true,
        'an in-flight request remains guarded until its finally block'
    );
    assert.match(studentStatus.textContent, /challenger was cleared/i);

    vm.runInContext(
        "challengeRatingSubmissions.delete('old');" +
        "challengeRatings = { ch1: 4 };" +
        "savedChallengeRatings = {};" +
        "challengeRatingDrafts.clear();" +
        "challengeRatingDrafts.add('ch1');" +
        "lastReceivedState = {" +
            "poll_question_key: 'pres-1'," +
            "active_challenges: [{ challenge_key: 'ch1' }]" +
        "};",
        sandbox
    );

    const handToggleStart = source.indexOf(
        'window.toggleRaiseHand = async function() {'
    );
    const handToggleEnd = source.indexOf(
        'function selectChallengeRating(challengeKey, score) {',
        handToggleStart
    );
    assert(handToggleStart > 0 && handToggleEnd > handToggleStart);
    vm.runInContext(
        source.slice(handToggleStart, handToggleEnd),
        sandbox,
        { filename: 'app-challenge-hand-toggle.js' }
    );
    const applyChallengeResponsesStart = source.indexOf(
        'function applySavedChallengeResponses(data, presentationKey) {'
    );
    const applyChallengeResponsesEnd = source.indexOf(
        'function renderChallengeUI(state) {',
        applyChallengeResponsesStart
    );
    assert(
        applyChallengeResponsesStart > 0 &&
        applyChallengeResponsesEnd > applyChallengeResponsesStart
    );
    vm.runInContext(
        source.slice(applyChallengeResponsesStart, applyChallengeResponsesEnd),
        sandbox,
        { filename: 'app-challenge-response-readback.js' }
    );

    fetchLog = [];
    fetchHandler = async () => {
        throw new Error('connection interrupted');
    };
    vm.runInContext(
        'responseNeedsRefresh = false;' +
            'challengeHandRaised = false;' +
            'challengeHandConfirmationPending = null;',
        sandbox
    );
    await sandbox.window.toggleRaiseHand();
    assert.strictEqual(
        fetchLog.filter(call => call.url === '/api/raise_hand').length,
        1,
        'ambiguous hand changes should use read-back, not blind retries'
    );
    assert.strictEqual(
        vm.runInContext('challengeHandRaised', sandbox),
        false,
        'an ambiguous response must not optimistically change hand state'
    );
    assert.strictEqual(
        vm.runInContext(
            'challengeHandConfirmationPending.presentationKey', sandbox
        ),
        'pres-1',
        'an ambiguous hand change must stay with its presentation'
    );
    assert.strictEqual(
        vm.runInContext(
            'challengeHandConfirmationPending.desiredRaised', sandbox
        ),
        true,
        'an ambiguous hand change must remember the requested action'
    );
    assert.strictEqual(
        vm.runInContext('responseNeedsRefresh', sandbox),
        true,
        'an ambiguous hand change must schedule saved-state read-back'
    );

    vm.runInContext(
        "applySavedChallengeResponses({" +
            "challenge_hand_raised: false, challenge_ratings: {}" +
        "}, 'pres-1');",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('challengeHandConfirmationPending', sandbox),
        null,
        'authoritative read-back must resolve the ambiguity marker'
    );
    assert.strictEqual(
        vm.runInContext('lastToastMessage', sandbox),
        'Hand raise was not confirmed. Please try again.'
    );
    assert.strictEqual(
        vm.runInContext('lastToastType', sandbox),
        'warning',
        'a mismatched read-back must not be described as success'
    );

    vm.runInContext(
        "challengeHandConfirmationPending = {" +
            "presentationKey: 'pres-1', desiredRaised: false" +
        "};" +
        "resetChallengePresentation('pres-2');",
        sandbox
    );
    assert.strictEqual(
        vm.runInContext('challengeHandConfirmationPending', sandbox),
        null,
        'a new presentation must clear the old hand ambiguity'
    );
    assert.match(studentStatus.textContent, /earlier hand change/i);
    vm.runInContext(
        "activeChallengePresentationKey = 'pres-1';" +
        "activeChallengeRatingsOpen = true;" +
        "challengeRatings = { ch1: 4 };" +
        "challengeRatingDrafts.add('ch1');" +
        "lastReceivedState = {" +
            "poll_question_key: 'pres-1'," +
            "active_challenges: [{ challenge_key: 'ch1' }]" +
        "};",
        sandbox
    );

    const challengeSubmitStart = source.indexOf(
        'window.submitChallengeRating = async function(challengeKey) {'
    );
    const challengeSubmitEnd = source.indexOf(
        '// Top challengers on the ended page',
        challengeSubmitStart
    );
    assert(
        challengeSubmitStart > 0 && challengeSubmitEnd > challengeSubmitStart
    );
    vm.runInContext(
        source.slice(challengeSubmitStart, challengeSubmitEnd),
        sandbox,
        { filename: 'app-challenge-submit.js' }
    );

    fetchLog = [];
    fetchHandler = async () => {
        throw new Error('connection interrupted');
    };
    await sandbox.window.submitChallengeRating('ch1');
    assert.strictEqual(
        fetchLog.filter(call =>
            call.url === '/api/submit_challenge_rating').length,
        2,
        'challenge rating should retry one ambiguous network attempt'
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedChallengeRatings.size', sandbox),
        1
    );

    fetchLog = [];
    fetchHandler = async () => response(500, {
        success: false,
        error: 'Rating window closed',
    });
    await sandbox.window.submitChallengeRating('ch1');
    assert.strictEqual(
        fetchLog.filter(call =>
            call.url === '/api/submit_challenge_rating').length,
        1,
        'definitive failures must not be retried'
    );
    assert.strictEqual(
        vm.runInContext('_unconfirmedChallengeRatings.size', sandbox),
        1,
        'a failed explicit retry must preserve the earlier ambiguity marker'
    );

    fetchHandler = async () => response(200, { success: true });
    await sandbox.window.submitChallengeRating('ch1');
    assert.strictEqual(
        vm.runInContext('_unconfirmedChallengeRatings.size', sandbox),
        0,
        'confirmed success should clear the ambiguity marker'
    );

    const makeRatingRow = question => {
        const row = {
            dataset: { question },
            stars: [],
            querySelectorAll() { return this.stars; },
        };
        row.stars = [1, 2, 3, 4, 5].map(value => ({
            dataset: { value: String(value) },
            disabled: false,
            textContent: '',
            classList: { toggle() {} },
            setAttribute() {},
            addEventListener(type, handler) {
                if (type === 'click') this.click = handler;
            },
        }));
        return row;
    };
    const ratingRows = {
        q1: makeRatingRow('q1'),
        q2: makeRatingRow('q2'),
    };
    const ratingStatus = { dataset: {}, style: {}, textContent: '' };
    const ratingSandbox = {
        console,
        window: {},
        document: {
            getElementById(id) {
                return id === 'rating-status' ? ratingStatus : null;
            },
            querySelector(selector) {
                const match = selector.match(/data-question="([^"]+)"/);
                return match ? ratingRows[match[1]] : null;
            },
            querySelectorAll(selector) {
                return selector === '.star-row' ? Object.values(ratingRows) : [];
            },
        },
    };
    vm.createContext(ratingSandbox);
    const ratingStart = source.indexOf('function showCurrentRating(values) {');
    const ratingEnd = source.indexOf('// ===== UNASSIGN ALL =====', ratingStart);
    assert(ratingStart > 0 && ratingEnd > ratingStart);
    vm.runInContext(
        'let ratingDraftDirty = false;' +
        'let pollSelections = { q1: 4, q2: 3 };' +
        'let savedPollSelections = { q1: 4, q2: 3 };' +
        'let responseNeedsRefresh = false;' +
        'let _responseRequestId = 0;' +
        source.slice(ratingStart, ratingEnd),
        ratingSandbox,
        { filename: 'app-rating-revert.js' }
    );
    vm.runInContext('initStarRows()', ratingSandbox);
    ratingRows.q1.stars[4].click();
    assert.strictEqual(
        vm.runInContext('ratingDraftDirty', ratingSandbox),
        true,
        'changing a saved star value must mark the rating dirty'
    );
    ratingRows.q1.stars[3].click();
    assert.strictEqual(
        vm.runInContext('ratingDraftDirty', ratingSandbox),
        false,
        'returning to the saved values must clear the draft warning'
    );
    assert.match(ratingStatus.textContent, /Your current rating/);

    console.log('vote submission behavior: ok');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});