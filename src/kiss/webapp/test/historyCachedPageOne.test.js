// Author: Koushik Sen (ksen@berkeley.edu)
// Contributors:
// Koushik Sen (ksen@berkeley.edu)
// add your name here

'use strict';

// End-to-end tests of the CHATS panel's first paint over a slow link.
//
// A page used to show its placeholder until the first `history` reply,
// which on a slow connection queues behind whatever the `ready`
// fan-out sends first; and that first reply was thrown away on every
// fresh page: the `machines` reply of the fan-out relabels a machine
// the page did not know yet, and the relabel opened a new generation
// of the request.  Now the page paints the page one the previous
// visit left in localStorage before its first request goes out (the
// reply replaces or keeps it), a relabel while page one is in flight
// leaves the flight alone (its reply renders with the labels), and
// page one of the plain view is written back for the next load.
//
// A phone-width page, whose drawer starts closed, asked for page one
// only when the drawer first opened, behind the transcript the `ready`
// fan-out had queued by then, and its list read "No conversations yet"
// until that reply landed.  Now it asks at load like the desktop, the
// placeholder says the list is loading, and opening the drawer keeps a
// page one in flight.

const assert = require('assert');
const {makePage, send, fakeMatchMedia, ofType} = require('./simplify2_harness');
const {
  machineSession: session,
  lastGetHistory,
  rowTitles,
} = require('./history_helpers');

const CACHE_KEY = 'sorcar-history-page1';

const CACHED = [session(1, 'old local'), session(2, 'old box task', 'box')];
const FRESH = [session(3, 'new local'), session(4, 'new box task', 'box')];

function chips(win) {
  return Array.from(
    new Set(
      Array.from(
        win.document.querySelectorAll('#history-list .sidebar-item-machine'),
      ).map(el => el.textContent),
    ),
  );
}

function reply(generation, sessions, extra) {
  return Object.assign(
    {type: 'history', sessions, offset: 0, generation, machineLabels: {}},
    extra || {},
  );
}

/**
 * A desktop-width page (the sidebar opens itself at boot, as on a
 * laptop) whose storage holds `cached` as the last page one.
 */
function openDesktopPage(cached) {
  return makePage({
    beforeScripts: win => {
      fakeMatchMedia(win, true);
      if (cached) win.localStorage.setItem(CACHE_KEY, JSON.stringify(cached));
    },
  });
}

function testCachedRowsPaintBeforeTheFirstReply() {
  const {win, posted} = openDesktopPage({
    sessions: CACHED,
    machineLabels: {box: 'The box'},
  });
  // Painted at boot, before the daemon said anything: the rows, with
  // the machine chip labelled as the cached reply labelled it.
  assert.deepStrictEqual(rowTitles(win), ['old box task', 'old local']);
  assert.deepStrictEqual(chips(win), ['The box']);
  // One page-one request went out, and no page two from cached rows.
  const reqs = ofType(posted, 'getHistory');
  assert.strictEqual(reqs.length, 1, 'exactly one request at boot');
  assert.strictEqual(reqs[0].offset, 0);
  // The daemon's reply replaces the cached rows and is kept for the
  // next load.
  send(win, reply(reqs[0].generation, FRESH, {machineLabels: {box: 'Box'}}));
  assert.deepStrictEqual(rowTitles(win), ['new box task', 'new local']);
  assert.deepStrictEqual(chips(win), ['Box']);
  const kept = JSON.parse(win.localStorage.getItem(CACHE_KEY));
  assert.deepStrictEqual(
    kept.sessions.map(s => s.title),
    ['new local', 'new box task'],
  );
  assert.deepStrictEqual(kept.machineLabels, {box: 'Box'});
  assert.strictEqual(ofType(posted, 'getHistory').length, 1, 'no refetch');
  win.close();
  console.log('  ok - cached rows paint before the first reply');
}

function testAReplyEqualToTheCacheKeepsTheRows() {
  const {win, posted} = openDesktopPage({sessions: CACHED, machineLabels: {}});
  const before = Array.from(
    win.document.querySelectorAll('#history-list .sidebar-item'),
  );
  assert.strictEqual(before.length, 2);
  send(win, reply(lastGetHistory(posted).generation, CACHED));
  const after = Array.from(
    win.document.querySelectorAll('#history-list .sidebar-item'),
  );
  // The very same nodes, not equal-looking replacements (a deep compare
  // of jsdom elements cannot tell a rebuilt row from the kept one).
  assert.strictEqual(after.length, before.length, 'the same number of rows');
  before.forEach((row, i) => {
    assert.strictEqual(after[i], row, `row ${i} is the same node`);
    assert.ok(row.isConnected, `row ${i} is still in the document`);
  });
  win.close();
  console.log('  ok - a reply equal to the cache keeps the rows');
}

function testNothingIsPaintedWithoutACacheOrOverAFilteredView() {
  const {win, posted} = openDesktopPage(null);
  assert.deepStrictEqual(rowTitles(win), [], 'no cache, no rows');
  assert.strictEqual(ofType(posted, 'getHistory').length, 1);
  // An empty page one is remembered: a list the user emptied is not
  // painted back from an older visit.
  send(win, reply(lastGetHistory(posted).generation, []));
  assert.deepStrictEqual(JSON.parse(win.localStorage.getItem(CACHE_KEY)), {
    sessions: [],
    machineLabels: {},
  });
  // A search's reply is not the plain view: the cache keeps the empty page.
  const search = win.document.getElementById('history-search');
  search.value = 'box';
  search.dispatchEvent(new win.Event('input', {bubbles: true}));
  const searchReq = lastGetHistory(posted);
  assert.strictEqual(searchReq.query, 'box');
  send(win, reply(searchReq.generation, FRESH.slice(1)));
  assert.deepStrictEqual(rowTitles(win), ['new box task']);
  assert.deepStrictEqual(
    JSON.parse(win.localStorage.getItem(CACHE_KEY)).sessions,
    [],
  );
  win.close();
  console.log('  ok - no paint without a cache; a filtered reply is not kept');
}

function testACacheIsNotPaintedOverRowsOrIntoAFilteredView() {
  // A page without matchMedia paints nothing at load: its sidebar
  // opens from the burger, into the search's view, which is left alone.
  const {win, posted} = makePage({
    beforeScripts: win => {
      win.localStorage.setItem(
        CACHE_KEY,
        JSON.stringify({sessions: CACHED, machineLabels: {}}),
      );
    },
  });
  const search = win.document.getElementById('history-search');
  search.value = 'box';
  search.dispatchEvent(new win.Event('input', {bubbles: true}));
  win.document.getElementById('menu-btn').click();
  assert.ok(win.document.getElementById('sidebar').classList.contains('open'));
  assert.deepStrictEqual(rowTitles(win), [], 'a filtered view paints no cache');
  assert.ok(ofType(posted, 'getHistory').length >= 1);
  win.close();
  console.log('  ok - the cache is not painted into a filtered view');
}

function testARelabelDuringTheFlightDoesNotDiscardTheReply() {
  const {win, posted} = openDesktopPage(null);
  send(win, {type: 'daemonStatus', connected: true});
  const first = lastGetHistory(posted);
  posted.length = 0;
  // The `ready` fan-out's machines reply labels a machine the page
  // did not know: page one is in flight, so no new generation.
  send(win, {
    type: 'machines',
    machines: [
      {name: 'myhost', local: true, status: 'reachable'},
      {name: 'box', label: 'The box', status: 'reachable'},
    ],
  });
  assert.strictEqual(
    ofType(posted, 'getHistory').length,
    0,
    'a relabel while page one is in flight opens no new generation',
  );
  // The in-flight reply renders, its chips with the new label.
  send(win, reply(first.generation, FRESH));
  assert.deepStrictEqual(rowTitles(win), ['new box task', 'new local']);
  assert.deepStrictEqual(chips(win), ['The box']);
  // The connect's refresh was folded into the flight and goes out now
  // as the only request; while it is in flight a relabel still waits.
  const folded = ofType(posted, 'getHistory');
  assert.strictEqual(folded.length, 1, 'the folded refresh goes out once');
  assert.strictEqual(folded[0].offset, 0);
  send(win, {
    type: 'machines',
    machines: [
      {name: 'myhost', local: true, status: 'reachable'},
      {name: 'box', label: 'Box again', status: 'reachable'},
    ],
  });
  assert.strictEqual(
    ofType(posted, 'getHistory').length,
    1,
    'still one request',
  );
  send(win, reply(folded[0].generation, FRESH));
  assert.deepStrictEqual(chips(win), ['Box again']);
  // Once nothing is in flight, a relabel refetches as before.
  posted.length = 0;
  send(win, {
    type: 'machines',
    machines: [
      {name: 'myhost', local: true, status: 'reachable'},
      {name: 'box', label: 'Renamed box', status: 'reachable'},
    ],
  });
  const refetch = ofType(posted, 'getHistory');
  assert.strictEqual(refetch.length, 1, 'an idle relabel refetches');
  assert.strictEqual(refetch[0].offset, 0);
  win.close();
  console.log('  ok - a relabel during the flight does not discard the reply');
}

function listText(win) {
  return win.document.getElementById('history-list').textContent.trim();
}

function testAPhonePageAsksForPageOneAtLoad() {
  const {win, posted} = makePage({
    beforeScripts: win => fakeMatchMedia(win, false),
  });
  const drawer = win.document.getElementById('sidebar');
  assert.ok(!drawer.classList.contains('open'), 'the drawer starts closed');
  // Nothing has answered yet: the list says so, not that it is empty.
  assert.strictEqual(listText(win), 'Loading chats...');
  const boot = ofType(posted, 'getHistory');
  assert.strictEqual(boot.length, 1, 'page one is asked for at load');
  assert.strictEqual(boot[0].offset, 0);
  // The drawer opens while that page one is in flight: no second
  // request (a new generation would throw the reply away).
  win.document.getElementById('menu-btn').click();
  assert.ok(drawer.classList.contains('open'));
  assert.strictEqual(
    ofType(posted, 'getHistory').length,
    1,
    'the flight is kept',
  );
  send(win, reply(boot[0].generation, FRESH));
  assert.deepStrictEqual(rowTitles(win), ['new box task', 'new local']);
  // The opening was folded into the flight: one refresh follows the
  // reply, so a change between the request and the opening shows.
  const after = ofType(posted, 'getHistory');
  assert.strictEqual(after.length, 2, 'one refresh after the reply');
  send(win, reply(after[1].generation, CACHED));
  assert.deepStrictEqual(rowTitles(win), ['old box task', 'old local']);
  assert.strictEqual(ofType(posted, 'getHistory').length, 2, 'and no more');
  win.close();
  console.log('  ok - a phone page asks for page one at load');
}

function testAClosedDrawerIsFilledAndAnEmptyReplySaysSo() {
  const {win, posted} = makePage({
    beforeScripts: win => fakeMatchMedia(win, false),
  });
  // The reply lands before the drawer was ever opened: the rows are
  // there (and kept for the next load), and nothing is refetched.
  send(win, reply(lastGetHistory(posted).generation, FRESH));
  assert.deepStrictEqual(rowTitles(win), ['new box task', 'new local']);
  assert.strictEqual(
    JSON.parse(win.localStorage.getItem(CACHE_KEY)).sessions.length,
    2,
  );
  assert.strictEqual(ofType(posted, 'getHistory').length, 1, 'no refetch');
  // Opening the drawer with nothing in flight asks afresh, as before;
  // a reply without rows is what reads "No conversations yet".
  win.document.getElementById('menu-btn').click();
  assert.strictEqual(ofType(posted, 'getHistory').length, 2);
  send(win, reply(lastGetHistory(posted).generation, []));
  assert.strictEqual(listText(win), 'No conversations yet');
  win.close();
  console.log('  ok - a closed drawer is filled; an empty reply says so');
}

testCachedRowsPaintBeforeTheFirstReply();
testAReplyEqualToTheCacheKeepsTheRows();
testNothingIsPaintedWithoutACacheOrOverAFilteredView();
testACacheIsNotPaintedOverRowsOrIntoAFilteredView();
testARelabelDuringTheFlightDoesNotDiscardTheReply();
testAPhonePageAsksForPageOneAtLoad();
testAClosedDrawerIsFilledAndAnEmptyReplySaysSo();
console.log('historyCachedPageOne: all tests passed');
