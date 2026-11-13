# ruff: noqa: F811  (module fixtures imported from other test modules)
"""End-to-end test of the phone layout's screen bar, in a REAL headless
Chromium against a REAL :class:`RemoteAccessServer`.

A phone (mobile user agent, 390px wide) shows a bar at the top of the
page with one button per phone screen: Chats, the chat on screen, Files
(only while a file is open) and Info.  A tap puts that screen on view,
so the chats and the task info need no swipe, and the file screen is
left with one tap.  The Chats button counts the running chats from the
``status`` frames the daemon broadcasts.  A desktop browser at the same
width, and a phone browser at desktop width, have no bar.
"""

from __future__ import annotations

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
RUNNING = "#mobile-running"


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


def test_phone_bar_counts_the_running_chats(browser, harness):
    context, page, sent = _open_phone_page(browser, harness, PHONE_UA)
    try:
        page.wait_for_selector(BAR, state="visible", timeout=15000)
        assert page.locator(RUNNING).is_visible() is False
        assert page.get_attribute(CHATS, "aria-label") == "Chats"

        tab_id = next(f["tabId"] for f in sent if f.get("type") == "ready")
        broadcast = harness.server._printer.broadcast
        broadcast({"type": "status", "running": True, "tabId": tab_id})
        page.wait_for_selector(RUNNING, state="visible", timeout=10000)
        assert page.locator("#mobile-running-count").inner_text() == "1"
        assert page.get_attribute(CHATS, "aria-label") == "Chats, 1 running"

        broadcast({"type": "status", "running": False, "tabId": tab_id})
        page.wait_for_selector(RUNNING, state="hidden", timeout=10000)
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
        page.wait_for_function(
            "document.getElementById('mobile-running-count').textContent === '0'",
            timeout=30000,
        )
        # The second tab is listed, so it stays open, first in the list
        # and busy, behind the third.
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
        assert page.locator("#mobile-running-count").inner_text() == "1"
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
