'use strict';

/* Executable regressions for student display names and live reconciliation. */

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP_JS = path.join(__dirname, '..', '..', 'static', 'js', 'app.js');
const source = fs.readFileSync(APP_JS, 'utf8');

function sourceSlice(startMarker, endMarker) {
    const start = source.indexOf(startMarker);
    const end = source.indexOf(endMarker, start);
    assert(start >= 0 && end > start, `missing source slice: ${startMarker}`);
    return source.slice(start, end);
}

function loadDisplayHelper(sandbox) {
    vm.createContext(sandbox);
    vm.runInContext(sourceSlice(
        'function normalizedIdentityText(value) {',
        'function initDisplayNameControls() {'
    ), sandbox, { filename: 'app-student-display.js' });
}

function testStudentDisplayFormatting() {
    const sandbox = {};
    loadDisplayHelper(sandbox);
    sandbox.cases = [
        { display_name: 'Kit', roster_name: 'Katherine', student_id: 's1' },
        { display_name: ' ', roster_name: 'Katherine', student_id: 's1' },
        { display_name: null, roster_name: null, student_id: 's1' },
        { display_name: 's1', roster_name: 'Katherine', student_id: 's1' },
        {
            challenger_name: 's1',
            challenger_student_id: 's1',
            identity_source: 'student_id',
        },
    ];
    assert.deepStrictEqual(
        Array.from(vm.runInContext(
            'cases.map(student => studentDisplayName(student) + "|" + ' +
            'instructorDisplayName(student))',
            sandbox
        )),
        [
            'Kit|Kit (s1)', 'Katherine|Katherine (s1)', 's1|s1',
            's1|s1 (s1)', 's1|s1'
        ]
    );
}
function testCurrentStudentDisplayRefresh() {
    const header = { textContent: 'Old', title: 'Old' };
    const nav = { textContent: 'Old', title: 'Old' };
    const sandbox = {
        document: {
            querySelectorAll(selector) {
                assert.strictEqual(
                    selector, '[data-current-student-display]'
                );
                return [header, nav];
            },
        },
    };
    loadDisplayHelper(sandbox);
    vm.runInContext(sourceSlice(
        'function updateCurrentStudentDisplay(studentOrName) {',
        'function updateDiscussionCardIdentities(teams) {'
    ), sandbox, { filename: 'app-current-student-display.js' });

    sandbox.currentName = 'New Display';
    vm.runInContext(
        'updateCurrentStudentDisplay(currentName)', sandbox
    );
    assert.deepStrictEqual(
        [header.textContent, header.title, nav.textContent, nav.title],
        ['New Display', 'New Display', 'New Display', 'New Display']
    );
    const loadState = sourceSlice(
        'async function loadState(state) {',
        '// The dashboard\'s activity controls are rendered server-side'
    );
    assert(loadState.includes(
        'updateCurrentStudentDisplay(state.current_student_display_name)'
    ));
}


function testDiscussionCardIdentityRefresh() {
    let idElement = null;
    const nameElement = { textContent: 'Old Name', title: '' };
    const thumb = {
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
    };
    const circle = {
        insertBefore(element, before) {
            assert.strictEqual(before, thumb);
            idElement = element;
        },
    };
    const card = {
        dataset: { studentId: 's1' },
        querySelector(selector) {
            if (selector === '.card-circle') return circle;
            if (selector === '.card-name') return nameElement;
            if (selector === '.card-id') return idElement;
            if (selector === '.thumb-btn') return thumb;
            return null;
        },
    };
    const sandbox = {
        document: {
            querySelectorAll(selector) {
                assert.strictEqual(selector, '.teammate-card[data-student-id]');
                return [card];
            },
            createElement(tagName) {
                assert.strictEqual(tagName, 'span');
                return {
                    className: '',
                    textContent: '',
                    title: '',
                    remove() { idElement = null; },
                };
            },
        },
    };
    loadDisplayHelper(sandbox);
    vm.runInContext(sourceSlice(
        'function updateDiscussionCardIdentities(teams) {',
        '/** Keep display-math blocks intact'
    ), sandbox, { filename: 'app-discussion-identities.js' });

    sandbox.teams = [{ members: [{ student_id: 's1', roster_name: 'Katherine', display_name: 'Kit' }] }];
    vm.runInContext('updateDiscussionCardIdentities(teams)', sandbox);
    assert.strictEqual(nameElement.textContent, 'Kit');
    assert.strictEqual(nameElement.title, 'Kit');
    assert.strictEqual(idElement, null);
    assert.strictEqual(thumb.attributes['aria-label'], 'Thumbs up to Kit');

    sandbox.teams = [{ members: [{ student_id: 's1' }] }];
    vm.runInContext('updateDiscussionCardIdentities(teams)', sandbox);
    assert.strictEqual(nameElement.textContent, 's1');
    assert.strictEqual(nameElement.title, 's1');
    assert.strictEqual(idElement, null);
    assert.strictEqual(thumb.attributes['aria-label'], 'Thumbs up to s1');
}

function testTopChallengerDisplay() {
    const topDiv = {
        innerHTML: '',
        children: [],
        appendChild(child) { this.children.push(child); },
    };
    const sandbox = {
        escapeHtml(value) { return String(value); },
        document: {
            getElementById(id) {
                return id === 'top-challengers-display' ? topDiv : null;
            },
            createElement(tagName) {
                return {
                    tagName,
                    className: '',
                    innerHTML: '',
                    children: [],
                    appendChild(child) { this.children.push(child); },
                };
            },
        },
    };
    loadDisplayHelper(sandbox);
    vm.runInContext(sourceSlice(
        'function renderTopChallengers(topChallengers) {',
        'const _knownParticipationPresentationKeys'
    ), sandbox, { filename: 'app-top-challengers.js' });
    sandbox.challengers = [{ rank: 1, display_name: 'Kit', student_id: 's1' }];
    vm.runInContext('renderTopChallengers(challengers)', sandbox);
    assert.strictEqual(topDiv.children.length, 1);
    assert.match(topDiv.children[0].children[0].innerHTML, /Kit/);
}

function testLiveChallengerRenderersUsePublicIds() {
    const studentRenderer = sourceSlice(
        'function renderChallengeUI(state) {',
        '// Top challengers on the ended page'
    );
    const instructorRenderer = sourceSlice(
        'function renderInstructorChallenge(state) {',
        'window.selectChallenger = async function(studentId) {'
    );
    assert.match(
        studentRenderer,
        /studentDisplayName\(\s*ch\s*\)/
    );
    assert(studentRenderer.includes(
        '<strong>${escapeHtml(challengerDisplay)}</strong>'
    ));
    assert(studentRenderer.includes(
        '`Rate challenger ${challengerDisplay}: 1 to 5 stars`'
    ));
    assert.match(
        instructorRenderer,
        /instructorDisplayName\(\s*h\s*\)/
    );
    assert(instructorRenderer.includes(
        "${escapeHtml(studentDisplay)} (${escapeHtml(h.student_team_name || '')})"
    ));
    assert(instructorRenderer.includes(
        "btn.setAttribute('aria-label', `Select ${studentDisplay} as challenger`)"
    ));
    assert.match(
        instructorRenderer,
        /instructorDisplayName\(\s*ch\s*\)/
    );
    assert(instructorRenderer.includes(
        "${escapeHtml(challengerDisplay)} (${escapeHtml(ch.challenger_team_name || '')})"
    ));
    assert(instructorRenderer.includes(
        "btn.setAttribute('aria-label', `Clear challenger ${challengerDisplay}`)"
    ));
}

testStudentDisplayFormatting();
testDiscussionCardIdentityRefresh();
testTopChallengerDisplay();
testCurrentStudentDisplayRefresh();
testLiveChallengerRenderersUsePublicIds();
console.log('student name display behavior: ok');
