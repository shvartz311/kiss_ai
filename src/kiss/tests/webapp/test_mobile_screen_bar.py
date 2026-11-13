# ruff: noqa: F811  (module fixtures imported from other test modules)
"""End-to-end test of the phone layout's screen bar, in a REAL headless
Chromium against a REAL :class:`RemoteAccessServer`.

A phone (mobile user agent, 390px wide) shows a bar at the top of the
page with one button per phone screen: Chats, the chat on screen, Files
(only while a file is open) and Info.  A tap puts that screen on view,
so the chats and the task info need no swipe, and the file screen is
left with one tap.  The Chats button shows the number of running chats
the daemon lists (``runningChats``): the same chats the drawer puts
under Running, whether or not they have a tab in the page.  A desktop
browser at the same width, and a phone browser at desktop width, have
no bar.

The running tasks of the count test are real history rows with a real
agent state whose worker thread is alive, which is all the daemon reads
to call a task running; no model runs.
"""

from __future__ import annotations

import threading

import pytest

from kiss.agents.sorcar import persistence
from kiss.server import agent_state
from kiss.tests.server.test_explorer_scm_commands import (
    harness,  # noqa: F401  (module fixture used by param name)
)
from kiss.tests.webapp.test_mobile_swipe_hint import (
    PHONE_UA,
    TOAST,
    _open_phone_page,
)
from kiss.tests.webapp.test_mobile_swipe_screens import (
    _open_file_from_drawer,
    _screen,
    _wait_screen,
)
from kiss.tests.webapp.test_workspace_sections import (
    browser,  # noqa: F401  (module fixture used by param name)
)

BAR = "#mobile-screens"
CHATS = "#mobile-screen-chats"
CHAT = "#mobile-screen-chat"
FILE = "#mobile-screen-file"
INFO = "#mobile-screen-info"

# [the Chats button's number (0 while hidden), the items the drawer
# shows under its Running separator].
RUNNING_JS = """() => {
    const mark = document.getElementById('mobile-running');
    const count = document.getElementById('mobile-running-count');
    let listed = 0;
    let running = false;
    for (const el of document.querySelectorAll('#history-list > *')) {
        if (el.classList.contains('history-day-sep')) {
            running = el.dataset.day === 'running';
        } else if (
            running && el.style.display !== 'none' &&
            el.matches('.history-chat-group, .sidebar-item')
        ) {
            listed++;
        }
    }
    return [mark.hidden ? 0 : Number(count.textContent), listed];
}"""


def _start_task(
    started: list[agent_state.AgentState], title: str, chat: str, **extra: object,
) -> agent_state.AgentState:
    """Persist a task of *chat* and run it as the daemon sees a run: an
    agent state whose worker thread is alive.  It has no tab anywhere.
    The state is added to *started*."""
    task_id, _ = persistence._add_task(title, chat, extra)
    stop = threading.Event()
    worker = threading.Thread(target=stop.wait, daemon=True)
    worker.start()
    state = agent_state.AgentState(task_id, stop_event=stop, task_thread=worker)
    agent_state.register(state)
    started.append(state)
    return state


def _end_task(state: agent_state.AgentState, broadcast) -> None:
    """End a task of :func:`_start_task` (a second time is harmless) and
    announce it as the task runner does: a ``status`` for its tab, which
    no page has."""
    assert state.stop_event is not None and state.task_thread is not None
    state.stop_event.set()
    state.task_thread.join()
    agent_state.unregister(state.task_id, state)
    broadcast({"type": "status", "running": False, "tabId": "tab-of-" + state.task_id})


@pytest.fixture
def started(harness):  # noqa: F811
    """The tasks a test starts with :func:`_start_task`, ended after it."""
    states: list[agent_state.AgentState] = []
    yield states
    for state in states:
        _end_task(state, harness.server._printer.broadcast)


def _wait_running(page, count: int, listed: int | None) -> None:
    """Wait for the Chats button to count *count* running chats while the
    drawer lists *listed* items under Running (``None``: the drawer has
    not asked for its rows yet, so there is nothing to compare with)."""
    page.wait_for_function(
        f"want => {{ const got = ({RUNNING_JS})(); return got[0] === want[0]"
        " && (want[1] === null || got[1] === want[1]); }",
        arg=[count, listed],
        timeout=15000,
    )


def _current(page) -> list[str]:
    """The ids of the bar's buttons marked as the screen on view."""
    return list(page.evaluate(
        "[...document.querySelectorAll('#mobile-screens [aria-current=\"page\"]')]"
        ".map(b => b.id)"
    ))


def test_phone_bar_puts_every_screen_one_tap_away(browser, harness):
    context, page, _ = _open_phone_page(browser, harness, PHONE_UA)
    try:
        page.wait_for_selector(BAR, state="visible", timeout=15000)
        # The bar spans the top of the page, above the composer's row,
        # which still fits on one line.
        box = page.locator(BAR).bounding_box()
        assert box["y"] == 0 and box["width"] == 390
        assert page.locator("#menu-btn").is_visible() is False
        assert page.locator(FILE).is_visible() is False
        assert page.locator(CHAT).inner_text().strip() != ""
        assert _current(page) == ["mobile-screen-chat"]

        # The swipe hint opens under the bar, so the bar works at once.
        page.wait_for_selector(TOAST, state="visible", timeout=15000)
        page.click(CHATS)
        _wait_screen(page, "chats")
        page.click("#sidebar-close")
        _wait_screen(page, "chat")
        page.click(TOAST + " .kiss-notification-close")

        # A tap between the buttons is not a tap on a screen.
        page.click(BAR, position={"x": 3, "y": 2})
        assert _screen(page) == "chat"

        page.click(INFO)
        _wait_screen(page, "info")
        _open_file_from_drawer(page, "README.md")
        page.wait_for_selector(FILE, state="visible", timeout=5000)
        assert _current(page) == ["mobile-screen-file"]

        # One tap leaves the file screen, and one returns to it.
        page.click(CHAT)
        _wait_screen(page, "chat")
        assert _current(page) == ["mobile-screen-chat"]
        assert page.locator(FILE).is_visible()
        page.click(FILE)
        _wait_screen(page, "file")
        assert _current(page) == ["mobile-screen-file"]

        # The only chat closed from the strip: the file stays on screen
        # and the chat's button has no chat to name.
        page.click("#tab-list .chat-tab:not(.content-tab) .chat-tab-close")
        page.wait_for_function(
            "document.getElementById('mobile-screen-chat-title').textContent"
            " === 'Chat'",
            timeout=5000,
        )
        assert _current(page) == ["mobile-screen-file"]
    finally:
        context.close()


def test_phone_bar_counts_the_chats_the_drawer_lists_as_running(browser, harness, started):
    broadcast = harness.server._printer.broadcast
    alpha_one = _start_task(started, "alpha one", "chat-alpha")
    _start_task(started, "alpha two", "chat-alpha")
    beta = _start_task(started, "beta", "chat-beta")
    # Hidden from the list, and so from the count, until the Cron chip is on.
    _start_task(started, "nightly", "chat-cron", is_cron=True)
    # A sub-agent's task is not a row of the list.
    _start_task(started, "helper", "chat-helper", subagent={"parent_task_id": alpha_one.task_id})
    done, _ = persistence._add_task("finished", "chat-done")
    persistence._save_task_result("done", task_id=done)
    context, page, sent = _open_phone_page(browser, harness, PHONE_UA)
    try:
        # Straight after the load, with the drawer closed, no tab for
        # any of the chats and no status event: two chats are running.
        _wait_running(page, 2, None)
        assert page.locator("#mobile-running-count").is_visible()
        assert page.get_attribute(CHATS, "aria-label") == "Chats, 2 running"

        # The page's own tab reported running, with no task behind it,
        # is not a running chat.  A scheduled job reporting into a chat
        # that has other tasks is: its task is hidden like every cron
        # task, the chat is listed under Running all the same, so the
        # closed drawer's rows are fetched to see that it is.
        tab_id = next(f["tabId"] for f in sent if f.get("type") == "ready")
        broadcast({"type": "status", "running": True, "tabId": tab_id})
        page.wait_for_selector("#stop-btn", state="visible", timeout=10000)
        asked, _ = persistence._add_task("ask", "chat-mixed")
        persistence._save_task_result("done", task_id=asked)
        report = _start_task(started, "report", "chat-mixed", is_cron=True)
        broadcast({"type": "tasks_updated"})
        _wait_running(page, 3, 3)
        # More task news: the closed drawer shows the chat by now, and
        # its rows are not asked for again.
        fetches = sum(f.get("type") == "getHistory" for f in sent)
        broadcast({"type": "status", "running": False, "tabId": tab_id})
        page.wait_for_selector("#stop-btn", state="hidden", timeout=10000)
        _end_task(beta, broadcast)
        _wait_running(page, 2, None)
        assert sum(f.get("type") == "getHistory" for f in sent) == fetches

        page.click(TOAST + " .kiss-notification-close")
        page.click(CHATS)
        _wait_screen(page, "chats")
        _wait_running(page, 2, 2)
        page.click("#history-filters-toggle")
        page.click("label:has(#hf-cron)")
        _wait_running(page, 3, 3)
        # The flat view lists tasks, not chats, and no hidden cron task.
        page.click("#history-view-toggle")
        _wait_running(page, 4, 4)
        page.click("label:has(#hf-cron)")
        _wait_running(page, 2, 2)
        _end_task(alpha_one, broadcast)
        _wait_running(page, 1, 1)
        # Back to chats: one of chat-alpha's two tasks still runs.
        page.click("#history-view-toggle")
        _wait_running(page, 2, 2)
        _end_task(report, broadcast)
        _wait_running(page, 1, 1)
        for state in started:
            _end_task(state, broadcast)
        _wait_running(page, 0, 0)
        assert page.get_attribute(CHATS, "aria-label") == "Chats"
    finally:
        context.close()


def test_phone_bar_returns_from_a_browser_page_to_the_chat_left(browser, harness):
    context, page, sent = _open_phone_page(browser, harness, PHONE_UA)
    broadcast = harness.server._printer.broadcast
    busy = ""
    try:
        page.click(TOAST + " .kiss-notification-close")

        def new_chat_beside_a_busy_one() -> str:
            """Mark the chat on screen as running and press "+", which
            leaves a busy chat open; returns the busy chat's tab id."""
            tab_id: str = [
                f["tabId"] for f in sent if f.get("type") in ("ready", "openTab")
            ][-1]
            broadcast({"type": "status", "running": True, "tabId": tab_id})
            page.wait_for_selector("#stop-btn", state="visible", timeout=10000)
            page.click("#new-chat-btn")
            page.wait_for_selector("#stop-btn", state="hidden", timeout=10000)
            return tab_id

        # The page's first tab is a placeholder the daemon never listed,
        # so the daemon's tab list drops it once it lists the second.
        new_chat_beside_a_busy_one()
        # The second tab is listed, so it stays open and busy before
        # the third: whichever chat is first in the list is busy.
        busy = new_chat_beside_a_busy_one()

        # A browser page belongs to no chat.  Its screen returns to the
        # chat it took the screen from, not to the first chat (the busy
        # one, which would show its Stop button).
        broadcast({
            "type": "openBrowserTab", "tab_id": "screen-bar-page",
            "url": "https://example.com/", "title": "Example",
        })
        page.wait_for_selector(FILE, state="visible", timeout=10000)
        page.click(FILE)
        _wait_screen(page, "file")
        page.click(CHAT)
        _wait_screen(page, "chat")
        assert page.locator("#stop-btn").is_visible() is False
    finally:
        broadcast({"type": "status", "running": False, "tabId": busy})
        context.close()


def test_no_bar_for_a_desktop_browser_or_at_desktop_width(browser, harness):
    context, page, _ = _open_phone_page(browser, harness, None)
    try:
        page.wait_for_selector("#menu-btn", state="visible", timeout=15000)
        assert page.locator(BAR).is_visible() is False
    finally:
        context.close()

    context, page, _ = _open_phone_page(browser, harness, PHONE_UA)
    try:
        page.wait_for_selector(BAR, state="visible", timeout=15000)
        page.set_viewport_size({"width": 1200, "height": 800})
        page.wait_for_function(
            "document.body.classList.contains('remote-desktop')", timeout=15000
        )
        assert page.locator(BAR).is_visible() is False
    finally:
        context.close()
