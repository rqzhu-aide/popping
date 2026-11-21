'use strict';

/* Executable behavior coverage for compact Weekly Hero badge rendering. */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(
    __dirname, '..', '..', 'static', 'js', 'app.js'
), 'utf8');
const start = source.indexOf('function normalizedParticipationCount(value) {');
const end = source.indexOf('function participationBadgesHtml(', start);
assert.ok(start >= 0 && end > start, 'Weekly Hero helper block is missing');

const sandbox = {
    Number,
    Object,
    escapeAttrValue(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('"', '&quot;');
    },
};
vm.createContext(sandbox);
vm.runInContext(source.slice(start, end), sandbox, {
    filename: 'weekly-hero-badge-helpers.js',
});

const owned = vm.runInContext(`weeklyHeroBadgesHtml({
    hero_badges: { gold: 1, silver: 1, bronze: 0, bolt: 2 }
})`, sandbox);
assert.match(owned, /weekly-hero-badge-gold/);
assert.match(owned, /🥇 <strong>×1<\/strong>/);
assert.match(owned, /weekly-hero-badge-silver/);
assert.match(owned, /🥈 <strong>×1<\/strong>/);
assert.doesNotMatch(owned, /weekly-hero-badge-bronze/);
assert.match(owned, /weekly-hero-badge-bolt/);
assert.match(owned, /⚡ <strong>×2<\/strong>/);
assert.ok(
    owned.indexOf('weekly-hero-badge-gold') <
    owned.indexOf('weekly-hero-badge-silver')
);
assert.ok(
    owned.indexOf('weekly-hero-badge-silver') <
    owned.indexOf('weekly-hero-badge-bolt')
);
assert.match(owned, /Gold team award, 1 week/);
assert.match(owned, /Best Challenger award, 2 weeks/);

assert.strictEqual(
    vm.runInContext('weeklyHeroBadgesHtml({ hero_badges: {} })', sandbox),
    ''
);
assert.strictEqual(
    vm.runInContext(`weeklyHeroBadgesHtml({
        hero_badges: { gold: 0, silver: -1, bronze: 'bad', bolt: null }
    })`, sandbox),
    ''
);
assert.strictEqual(
    vm.runInContext('weeklyHeroBadgesHtml({})', sandbox),
    ''
);

const populatedHost = { hidden: true, innerHTML: '' };
sandbox.populatedHost = populatedHost;
vm.runInContext(`updateWeeklyHeroBadges(populatedHost, {
    hero_badges: { bronze: 3 }
})`, sandbox);
assert.strictEqual(populatedHost.hidden, false);
assert.match(populatedHost.innerHTML, /weekly-hero-badge-bronze/);
assert.match(populatedHost.innerHTML, /🥉 <strong>×3<\/strong>/);

vm.runInContext(
    'updateWeeklyHeroBadges(populatedHost, { hero_badges: {} })', sandbox
);
assert.strictEqual(populatedHost.hidden, true);
assert.strictEqual(populatedHost.innerHTML, '');

let focused = null;
let changes = 0;
class Element {
    constructor(tagName, markup = '') {
        this.tagName = tagName;
        this.markup = markup;
        this.children = [];
        this.dataset = {};
        this.attributes = new Map();
        this.nativeDisabled = false;
        this.parentElement = null;
    }
    get innerHTML() {
        return this.markup + this.children.map(child =>
            `<${child.tagName}>${child.innerHTML}</${child.tagName}>`
        ).join('');
    }
    set innerHTML(value) {
        [...this.children].forEach(child => child.remove());
        this.markup = value;
        changes += 1;
    }
    get textContent() { return this.markup; }
    set textContent(value) { this.innerHTML = value; }
    get firstElementChild() { return this.children[0] || null; }
    get lastElementChild() { return this.children.at(-1) || null; }
    get nextElementSibling() {
        const siblings = this.parentElement?.children;
        return siblings?.[siblings.indexOf(this) + 1] || null;
    }
    get title() { return this.getAttribute('title') || ''; }
    set title(value) { this.setAttribute('title', value); }
    get disabled() { return this.nativeDisabled; }
    set disabled(value) {
        this.nativeDisabled = value;
        if (value && focused === this) focused = null;
    }
    setAttribute(name, value) { this.attributes.set(name, value); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    addEventListener(type, handler) { this[`on${type}`] = handler; }
    focus() { focused = this; }
    contains(element) {
        for (let node = element; node; node = node.parentElement) {
            if (node === this) return true;
        }
        return false;
    }
    click() { if (!this.disabled) this.onclick?.(); }
    appendChild(child) { this.insertBefore(child, null); }
    insertBefore(child, next) {
        child.remove();
        const index = next ? this.children.indexOf(next) : this.children.length;
        assert.ok(index >= 0, 'insertion reference must be a child');
        this.children.splice(index, 0, child);
        child.parentElement = this;
        changes += 1;
    }
    remove() {
        if (!this.parentElement) return;
        if (this.contains(focused)) focused = null;
        const siblings = this.parentElement.children;
        siblings.splice(siblings.indexOf(this), 1);
        this.parentElement = null;
        changes += 1;
    }
}

const handsDiv = new Element('div', 'No raised hands yet.');
const activeDiv = new Element('div');
const challengeHeading = new Element('h4');
const settlingStatus = new Element('p');
sandbox.document = {
    get activeElement() { return focused; },
    getElementById(id) {
        if (id === 'challenge-instructor-panel') return {};
        if (id === 'challenge-instructor-heading') return challengeHeading;
        if (id === 'challenge-hands-list') return handsDiv;
        if (id === 'challenge-active-list') return activeDiv;
        if (id === 'ratings-settling-status') return settlingStatus;
        return null;
    },
    createElement(tagName) { return new Element(tagName); },
    querySelectorAll(selector) {
        if (selector === '.challenge-active-row') return [...activeDiv.children];
        if (selector !== '.challenge-mutating-btn') return [];
        return [...handsDiv.children, ...activeDiv.children].map(row => row.lastElementChild);
    },
};
sandbox.window = { setTimeout: () => 1, clearTimeout() {} };
sandbox.ratingsSettling = false;
sandbox.ratingsSettlingStatusTimer = null;
sandbox.escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
sandbox.instructorDisplayName = record =>
    record.student_name || record.challenger_name || '';
const selected = [];
const cleared = [];
const toasts = [];
sandbox.selectChallenger = studentId => selected.push(studentId);
sandbox.clearChallenger = key => cleared.push(key);
sandbox.showToast = (message, type) => toasts.push({ message, type });

const challengeStart = source.indexOf('function renderInstructorChallenge(state) {');
const challengeEnd = source.indexOf('window.selectChallenger', challengeStart);
assert.ok(
    challengeStart >= 0 && challengeEnd > challengeStart,
    'Instructor challenger renderer is missing'
);
vm.runInContext(source.slice(challengeStart, challengeEnd), sandbox, {
    filename: 'instructor-challenge-renderer.js',
});
const settlingStart = source.indexOf('function updateChallengeButtonAvailability(');
const settlingEnd = source.indexOf('function ratingsSettlementBlocksAction(', settlingStart);
assert.ok(settlingStart >= 0 && settlingEnd > settlingStart);
vm.runInContext(source.slice(settlingStart, settlingEnd), sandbox, {
    filename: 'instructor-ratings-settling.js',
});
const alice = {
    student_id: 1,
    student_name: 'Alice (ID01)',
    student_team_name: 'Team 1',
    challenger_count: 0,
    hero_badges: { gold: 1, bolt: 2 },
};
const ben = { student_id: 2, student_name: 'Ben' };
const charlie = { student_id: 3, student_name: 'Charlie' };
const firstChallenge = { challenge_key: 'first', challenge_num: 1, challenger_name: 'Dana' };
const secondChallenge = {
    challenge_key: 'second', challenge_num: 2, challenger_name: 'Evan',
    challenger_team_name: 'Team 2', hero_badges: { silver: 1 },
};
const state = {
    challenge_hands: [alice, ben],
    active_challenges: [firstChallenge, secondChallenge],
    challenge_rating_summaries: { second: { submitted_count: 1, eligible_count: 4 } },
    poll_active: true,
};
const render = update => sandbox.renderInstructorChallenge({ ...state, ...update });
render();
const aliceRow = handsDiv.firstElementChild;
const select = aliceRow.lastElementChild;
assert.strictEqual(select.textContent, 'Select as Challenger');
assert.strictEqual(select.getAttribute('aria-label'), 'Select Alice (ID01) as challenger');
assert.match(aliceRow.innerHTML, /weekly-hero-badge-gold/);
assert.match(aliceRow.innerHTML, /weekly-hero-badge-bolt/);
assert.match(aliceRow.innerHTML, /Challenge turns: <strong>0<\/strong>/);
assert.doesNotMatch(handsDiv.innerHTML, /No raised hands yet/);

select.focus();
const previousChanges = changes;
render();
assert.strictEqual(focused, select);
assert.strictEqual(changes, previousChanges, 'identical polls leave the lists in place');
select.click();

render({
    challenge_hands: [charlie, ben, {
        ...alice, student_id: '1', student_name: 'Ali', challenger_count: 2,
    }],
});
assert.deepStrictEqual(handsDiv.children.map(row => row.dataset.studentId), ['3', '2', '1']);
assert.strictEqual(handsDiv.lastElementChild, aliceRow);
assert.strictEqual(aliceRow.lastElementChild, select);
assert.strictEqual(focused, select);
assert.strictEqual(select.getAttribute('aria-label'), 'Select Ali as challenger');
assert.match(aliceRow.innerHTML, /Challenge turns: <strong>2<\/strong>/);
select.click();
assert.deepStrictEqual(selected, [1, '1'], 'the same button uses the current record');

const activeRow = activeDiv.lastElementChild;
const clear = activeRow.lastElementChild;
assert.strictEqual(activeRow.firstElementChild.className, 'challenge-active-details');
assert.strictEqual(clear.textContent, 'Clear');
assert.strictEqual(clear.title, 'Stop Poll before clearing this challenger');
assert.match(activeRow.innerHTML, /1 out of 4 students voted/);
clear.focus();
clear.click();
assert.strictEqual(toasts.length, 1);
assert.strictEqual(toasts[0].type, 'warning');
assert.deepStrictEqual(cleared, []);

const settled = {
    active_challenges: [secondChallenge],
    challenge_rating_summaries: { second: { submitted_count: 3, eligible_count: 4 } },
    poll_active: false,
};
render(settled);
assert.deepStrictEqual(activeDiv.children, [activeRow]);
assert.strictEqual(activeRow.lastElementChild, clear);
assert.strictEqual(focused, clear);
assert.strictEqual(clear.getAttribute('aria-label'), 'Clear challenger Evan');
assert.strictEqual(clear.getAttribute('title'), null);
assert.match(activeRow.innerHTML, /3 out of 4 students voted/);
assert.match(activeRow.innerHTML, /weekly-hero-badge-silver/);
clear.click();
assert.deepStrictEqual(cleared, ['second']);

const setSaving = saving => sandbox.applyRatingsSettlingState({
    ratings_settling: saving, ratings_settling_remaining: saving ? 2 : 0,
});
setSaving(true);
assert.strictEqual(focused, clear);
assert.strictEqual(select.disabled, false);
assert.strictEqual(clear.disabled, false);
assert.strictEqual(select.getAttribute('aria-disabled'), 'true');
assert.strictEqual(clear.getAttribute('aria-disabled'), 'true');
assert.match(settlingStatus.textContent, /Saving final ratings/);

render({ ...settled, poll_active: true, challenge_hands: [alice, charlie] });
const newSelect = handsDiv.lastElementChild.lastElementChild;
assert.strictEqual(newSelect.getAttribute('aria-disabled'), 'true');
assert.strictEqual(focused, clear);
select.focus();
select.click();
newSelect.click();
assert.deepStrictEqual(selected, [1, '1']);
clear.focus();
clear.click();
assert.deepStrictEqual(cleared, ['second']);
assert.strictEqual(toasts.length, 1, 'settling must block the Stop Poll warning too');

render(settled);
setSaving(false);
assert.strictEqual(focused, clear);
assert.strictEqual(select.getAttribute('aria-disabled'), 'false');
assert.strictEqual(clear.getAttribute('aria-disabled'), 'false');
assert.match(settlingStatus.textContent, /Final ratings saved/);
clear.click();
assert.deepStrictEqual(cleared, ['second', 'second']);

setSaving(true);
const outside = new Element('button');
outside.focus();
setSaving(false);
render(settled);
assert.strictEqual(focused, outside, 'settlement must not reclaim focus');

select.focus();
render({ ...settled, challenge_hands: [ben] });
assert.strictEqual(focused, challengeHeading, 'a withdrawn hand returns focus to Challenge');
clear.focus();
render({ challenge_hands: [], active_challenges: [] });
assert.strictEqual(focused, challengeHeading, 'removing the last challenger returns focus to Challenge');
assert.match(handsDiv.innerHTML, /No raised hands yet/);
assert.strictEqual(activeDiv.children.length, 0);

const instructorTemplate = fs.readFileSync(path.join(
    __dirname, '..', '..', 'templates', 'instructor.html'
), 'utf8');
assert.match(instructorTemplate, /<h4 id="challenge-instructor-heading" tabindex="-1">/);

async function testImmediateClearFocus() {
    const clearStart = source.indexOf('window.clearChallenger = async function(challengeKey) {');
    const clearEnd = source.indexOf('\nif (instructor) {', clearStart);
    assert.ok(clearStart >= 0 && clearEnd > clearStart);
    let finishClear;
    sandbox._instructorActionInFlight = false;
    sandbox.ratingsSettlementBlocksAction = () => false;
    sandbox.instructorStatePayload = payload => payload;
    sandbox.postJSONAfterRatingsSettle = () => new Promise(resolve => { finishClear = resolve; });
    sandbox.markCompetitionParticipationCountsDirty = () => {};
    vm.runInContext(source.slice(clearStart, clearEnd), sandbox);

    render(settled);
    activeDiv.firstElementChild.lastElementChild.focus();
    let request = sandbox.window.clearChallenger('second');
    finishClear({ success: true });
    await request;
    assert.strictEqual(activeDiv.children.length, 0);
    assert.strictEqual(focused, challengeHeading, 'successful Clear returns focus to Challenge');

    render(settled);
    activeDiv.firstElementChild.lastElementChild.focus();
    request = sandbox.window.clearChallenger('second');
    outside.focus();
    finishClear({ success: true });
    await request;
    assert.strictEqual(activeDiv.children.length, 0);
    assert.strictEqual(focused, outside, 'successful Clear leaves focus alone after tabbing away');
}

testImmediateClearFocus().then(() => {
    console.log('weekly hero badge harness passed');
}).catch(error => {
    console.error(error);
    process.exitCode = 1;
});
