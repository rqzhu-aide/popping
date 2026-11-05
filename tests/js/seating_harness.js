'use strict';

// Behavioral regressions for seating bounds and voting/drag gesture isolation.
// Geometry is modeled here; the rendered/browser checks cover actual text metrics.
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'static', 'js', 'app.js'), 'utf8');
const start = source.indexOf('    // --- teammate cards: circle layout + free drag ---');
const end = source.indexOf('    // --- thumb grading on cards ---', start);
assert(start >= 0 && end > start);

function surface({ compact = false, width = 850, heights = [110, 76], saved = null } = {}) {
    const state = { compact, width, heights };
    const events = target => {
        target.listeners = {};
        target.addEventListener = (type, handler) => {
            (target.listeners[type] ||= []).push(handler);
        };
        target.emit = (type, properties = {}) => {
            const event = {
                type, target, prevented: false, stopped: false,
                preventDefault() { this.prevented = true; },
                stopImmediatePropagation() { this.stopped = true; },
                ...properties
            };
            for (const handler of target.listeners[type] || []) {
                handler.call(target, event);
                if (event.stopped) break;
            }
            return event;
        };
        return target;
    };
    const cards = heights.map((height, index) => {
        const classes = new Set();
        return events({
            id: index === heights.length - 1 ? 'card-you' : `card-s${index}`,
            dataset: { studentId: index === heights.length - 1 ? 'you' : `s${index}` },
            style: {},
            get offsetHeight() { return state.heights[index]; },
            get offsetWidth() { return state.compact ? state.width - 24 : 128; },
            classList: { add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value) },
            closest(selector) { return selector === '.teammate-card' ? this : null; }
        });
    });
    const table = events({
        style: {}, clientLeft: 2, clientTop: 2,
        get clientWidth() { return state.width; },
        get clientHeight() { return state.compact ? 400 : Number.parseFloat(this.style.minHeight || 280) - 4; },
        querySelectorAll() { return cards; },
        getBoundingClientRect() { return { left: 20, top: 100, width: this.clientWidth + 4, height: this.clientHeight + 4 }; }
    });
    const thumb = { closest: selector => selector === '.thumb-btn' ? thumb : selector === '.teammate-card' ? cards[0] : null };
    const thumbChild = { closest: selector => selector === '.thumb-btn' ? thumb : null };
    const frames = [];
    const timers = [];
    const storage = new Map();
    if (saved) storage.set('popping-pos-course-me-team', JSON.stringify(saved));
    const window = events({
        matchMedia: () => ({ matches: state.compact }),
        requestAnimationFrame: callback => (frames.push(callback), frames.length),
        setTimeout: callback => (timers.push(callback), timers.length)
    });
    let observer;
    const sandbox = {
        window,
        document: { getElementById: id => id === 'team-table' ? table : id === 'card-you' ? cards.at(-1) : null },
        dashboard: { dataset: { courseSlug: 'course', you: 'me', teamId: 'team' } },
        localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
        ResizeObserver: class { constructor(callback) { observer = { callback, observed: [] }; } observe(element) { observer.observed.push(element); } },
        Image: class {}
    };
    vm.createContext(sandbox);
    vm.runInContext(source.slice(start, end), sandbox);
    vm.runInContext('initTeammateCards()', sandbox);
    const flush = () => { while (frames.length) frames.shift()(); };
    return {
        state, cards, table, window, thumb, thumbChild, storage,
        get observer() { return observer; }, flush,
        flushTimers: () => { while (timers.length) timers.shift()(); },
        reset: () => window.resetCardPositions()
    };
}

function assertContained(view) {
    for (const card of view.cards) {
        const x = Number.parseFloat(card.style.left) / 100 * view.table.clientWidth;
        const y = Number.parseFloat(card.style.top) / 100 * view.table.clientHeight;
        assert(x - card.offsetWidth / 2 >= 7.999, `${card.id} left`);
        assert(x + card.offsetWidth / 2 <= view.table.clientWidth - 7.999, `${card.id} right`);
        assert(y - card.offsetHeight / 2 >= 7.999, `${card.id} top`);
        assert(y + card.offsetHeight / 2 <= view.table.clientHeight - 7.999, `${card.id} bottom`);
    }
}

function positionsOf(view) {
    return Object.fromEntries(view.cards.map(card => [card.dataset.studentId, {
        left: Number.parseFloat(card.style.left), top: Number.parseFloat(card.style.top)
    }]));
}

function assertNoOverlap(view) {
    const bounds = view.cards.map(card => {
        const x = Number.parseFloat(card.style.left) / 100 * view.table.clientWidth;
        const y = Number.parseFloat(card.style.top) / 100 * view.table.clientHeight;
        return { id: card.id, left: x - card.offsetWidth / 2, right: x + card.offsetWidth / 2,
            top: y - card.offsetHeight / 2, bottom: y + card.offsetHeight / 2 };
    });
    bounds.forEach((seat, index) => bounds.slice(index + 1).forEach(other => {
        assert(seat.right <= other.left || other.right <= seat.left ||
            seat.bottom <= other.top || other.bottom <= seat.top,
        `${seat.id} overlaps ${other.id}`);
    }));
}

function testMeasuredLayoutAndReflow() {
    // Two supported 200-character names, including the legacy bottom seat.
    const view = surface({ heights: [416, 382], saved: { s0: { left: 0, top: 100 }, you: { left: 50, top: 85 } } });
    assertContained(view);
    assert.strictEqual(view.observer.observed.length, 3);
    // Resize/name changes preserve already safe relative centers.
    const normal = surface({ saved: { s0: { left: 35, top: 45 }, you: { left: 60, top: 75 } } });
    assert.strictEqual(normal.cards[0].style.left, '35%');
    normal.state.width = 570;
    normal.window.emit('resize'); normal.flush();
    assertContained(normal);
    assert.strictEqual(normal.cards[0].style.left, '35%');
    // The self card can be taller than every teammate after a live rename.
    normal.state.heights[1] = 480;
    normal.observer.callback(); normal.flush();
    assert(Number.parseFloat(normal.table.style.minHeight) >= 480 * 3);
    assertContained(normal);

    // Phone initialization must not save positions measured from list cards.
    for (const phoneWidth of [320, 390]) {
        const phone = surface({ compact: true, width: phoneWidth, heights: [130, 122] });
        assert.strictEqual(phone.table.style.minHeight, undefined);
        assert.strictEqual(phone.storage.size, 0);
        phone.state.compact = false;
        phone.state.width = 850;
        phone.state.heights = [416, 382];
        phone.window.emit('resize'); phone.flush();
        assertContained(phone);
        const seat = phone.cards[0].style.left;
        phone.state.compact = true;
        phone.window.emit('resize'); phone.flush();
        assert.strictEqual(phone.cards[0].style.left, seat);
        phone.state.compact = false;
        phone.observer.callback(); phone.flush();
        assertContained(phone);
    }

    // Row capacity uses the actual available canvas, including large teams.
    const many = surface({ width: 520, heights: Array(13).fill(416) });
    assertContained(many);
    const rows = new Map();
    many.cards.slice(0, -1).forEach(card => {
        const top = card.style.top;
        rows.set(top, (rows.get(top) || 0) + 1);
    });
    assert.strictEqual(rows.size, 4);
    assert(Math.max(...rows.values()) <= 3);
    const lastMate = Number.parseFloat(many.cards.at(-2).style.top) / 100 * many.table.clientHeight;
    const self = Number.parseFloat(many.cards.at(-1).style.top) / 100 * many.table.clientHeight;
    assert(self - lastMate >= 440 - 0.001);
}

function testResponsiveDefaultsAndCustomPersistence() {
    const key = 'popping-pos-course-me-team';
    const heights = Array(5).fill(110);
    const view = surface({ width: 850, heights });
    const wideDefaults = positionsOf(view);
    assertNoOverlap(view);
    assert.strictEqual(view.storage.size, 0, 'generated defaults must not become saved custom seats');

    // Four teammates change from an arc to rows at a tighter desktop width.
    view.state.width = 570;
    view.window.emit('resize'); view.flush();
    assertContained(view);
    assertNoOverlap(view);
    const narrowDefaults = positionsOf(view);
    assert.notDeepStrictEqual(narrowDefaults, wideDefaults);
    assert.strictEqual(view.storage.size, 0);
    const reloadedDefaults = surface({ width: 570, heights });
    assert.deepStrictEqual(positionsOf(reloadedDefaults), narrowDefaults);
    view.state.width = 850;
    view.window.emit('resize'); view.flush();
    assert.deepStrictEqual(positionsOf(view), wideDefaults);

    // Legacy maps remain custom. Constraining a rendered seat never overwrites
    // its desired center, including across a reload at the narrower width.
    const legacy = { ...wideDefaults, s0: { left: 10, top: 50 } };
    const custom = surface({ width: 850, heights, saved: legacy });
    const savedLegacy = custom.storage.get(key);
    assert.strictEqual(custom.cards[0].style.left, '10%');
    custom.state.width = 570;
    custom.window.emit('resize'); custom.flush();
    assertContained(custom);
    assert(Number.parseFloat(custom.cards[0].style.left) > 10);
    assert.strictEqual(custom.storage.get(key), savedLegacy);
    const reloadedCustom = surface({ width: 570, heights, saved: JSON.parse(savedLegacy) });
    assert.deepStrictEqual(positionsOf(reloadedCustom), positionsOf(custom));
    reloadedCustom.state.width = 850;
    reloadedCustom.window.emit('resize'); reloadedCustom.flush();
    assert.strictEqual(reloadedCustom.cards[0].style.left, '10%');

    // Moving another seat saves that move without replacing the first seat's
    // temporarily clamped desired center with its narrow-screen coordinate.
    const transfer = { setData() {}, setDragImage() {} };
    custom.cards[1].emit('dragstart', { dataTransfer: transfer });
    custom.table.emit('dragover', { clientX: 350, clientY: 200 });
    custom.cards[1].emit('dragend');
    const moved = JSON.parse(custom.storage.get(key));
    assert.strictEqual(moved.s0.left, 10);
    assert.notDeepStrictEqual(moved.s1, legacy.s1);

    // Reset discards the custom map and restores defaults for the current
    // width; later resize and reload continue to use responsive defaults.
    custom.reset();
    assert.strictEqual(custom.storage.size, 0);
    assert.deepStrictEqual(positionsOf(custom), narrowDefaults);
    assertNoOverlap(custom);
    custom.state.width = 850;
    custom.window.emit('resize'); custom.flush();
    assert.deepStrictEqual(positionsOf(custom), wideDefaults);

    // An actual move from an automatic layout is the point at which positions
    // become custom and persist in the existing coordinate-map format.
    view.cards[0].emit('dragstart', { dataTransfer: transfer });
    view.cards[0].emit('dragend');
    assert.strictEqual(view.storage.size, 0, 'a drag with no movement leaves defaults automatic');
    view.cards[0].emit('dragstart', { dataTransfer: transfer });
    view.table.emit('dragover', { clientX: 450, clientY: 280 });
    view.cards[0].emit('dragend');
    const savedMove = JSON.parse(view.storage.get(key));
    assert.deepStrictEqual(savedMove, positionsOf(view));
    const reloadedMove = surface({ width: 850, heights, saved: savedMove });
    assert.deepStrictEqual(positionsOf(reloadedMove), positionsOf(view));
}

function pointer(view, type, x = 100, y = 100) {
    const target = type === 'pointerdown' ? view.cards[0] : view.window;
    return target.emit(type, { target: view.thumbChild, button: 0, pointerId: 1, clientX: x, clientY: y });
}

function testThumbGestureIsolationAndKeyboard() {
    const view = surface();
    const initial = [view.cards[0].style.left, view.cards[0].style.top];
    pointer(view, 'pointerdown');
    pointer(view, 'pointermove', 220, 200);
    // The browser identifies the draggable card as dragstart.target.
    const rejected = view.cards[0].emit('dragstart');
    assert(rejected.prevented);
    view.table.emit('dragover', { clientX: 500, clientY: 400 });
    assert.deepStrictEqual([view.cards[0].style.left, view.cards[0].style.top], initial);
    pointer(view, 'pointerup', 220, 200);
    const draggedClick = view.cards[0].emit('click', { target: view.thumbChild, detail: 1 });
    assert(draggedClick.prevented && draggedClick.stopped, 'a thumb drag must not submit a vote');
    view.flushTimers();

    pointer(view, 'pointerdown');
    pointer(view, 'pointerup');
    const clicked = view.cards[0].emit('click', { target: view.thumbChild, detail: 1 });
    assert(!clicked.prevented && !clicked.stopped, 'a deliberate thumb click must reach voting');
    const keyboard = view.cards[0].emit('click', { target: view.thumbChild, detail: 0 });
    assert(!keyboard.prevented && !keyboard.stopped, 'Enter/Space activation must reach voting');
}

function testIntentionalMouseAndWideTouchDrag() {
    const view = surface({ heights: [416, 382] });
    const transfer = { setData() {}, setDragImage() {} };
    assert(!view.cards[0].emit('dragstart', { dataTransfer: transfer }).prevented);
    assert(view.cards[0].classList.contains('dragging'));
    view.table.emit('dragover', { clientX: -200, clientY: 3000 });
    assertContained(view);
    view.cards[0].emit('dragend');
    assert(!view.cards[0].classList.contains('dragging'));

    const first = { identifier: 7, clientX: 100, clientY: 100 };
    view.cards[0].emit('touchstart', { touches: [first] });
    const before = view.cards[0].style.left;
    view.table.emit('touchmove', { touches: [{ identifier: 8, clientX: 900, clientY: 100 }] });
    assert.strictEqual(view.cards[0].style.left, before, 'another finger cannot take over');
    view.table.emit('touchmove', { touches: [{ identifier: 7, clientX: 2000, clientY: -500 }] });
    assertContained(view);
    view.table.emit('touchcancel');
    assert(!view.cards[0].classList.contains('dragging'));
    view.cards[0].emit('touchstart', { touches: [first] });
    assert(view.cards[0].classList.contains('dragging'), 'another touch drag works after cancellation');
    view.window.emit('blur');
    assert(!view.cards[0].classList.contains('dragging'));

    view.state.compact = true;
    assert(view.cards[0].emit('dragstart', { dataTransfer: transfer }).prevented);
    view.cards[0].emit('touchstart', { touches: [first] });
    assert(!view.cards[0].classList.contains('dragging'), 'compact list leaves touch scrolling alone');
}

testMeasuredLayoutAndReflow();
testResponsiveDefaultsAndCustomPersistence();
testThumbGestureIsolationAndKeyboard();
testIntentionalMouseAndWideTouchDrag();
console.log('seating geometry and gestures: ok');
