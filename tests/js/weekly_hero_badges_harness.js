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

console.log('weekly hero badge harness passed');
