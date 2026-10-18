# Author: Koushik Sen (ksen@berkeley.edu)
# Contributors:
# Koushik Sen (ksen@berkeley.edu)
# add your name here
"""End-to-end regression: finish() must salvage near-miss summary keys.

Production incidents (2026-10-17/18, ~15 tasks in one chat on
openrouter/moonshotai/kimi-k3, recoverable from ~/.kiss/history.db):
the model intermittently emits the final ``finish`` call with a
malformed ``summary_in_html`` key — ``"summary_in_html:"`` (trailing
colon), ``"summary_in_html_string"`` (suffix), or stray case.  The old
dispatch passed the raw args to ``kiss.core.utils.finish(**args)``;
the unknown key raised ``TypeError`` and, because a ``finish`` result
is terminal in ``KISSAgent._execute_step``, the error text became the
task result.  Downstream YAML parsing found no ``summary`` and the UI
showed "No summary available" for work that was actually done.

Fix: ``KISSAgent._execute_tool`` runs ``_salvage_near_miss_args`` on
``finish`` arguments before dispatch.  A key that is not a parameter is
a near-miss when its normalized form (stripped, trailing colons
removed, lowercased) equals or starts with a parameter name; its value
moves to that parameter unless the exact key already holds a non-empty
value.  Genuinely unknown keys keep the old error behavior.

Every test drives a real ``KISSAgent`` against a local
OpenAI-compatible HTTP server; nothing is mocked.  The
``sig is None`` branch of ``_salvage_near_miss_args`` is defensive
(a registered finish callable is introspectable in practice) and is
not reachable end-to-end without test doubles, so it is documented
here instead of covered.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import yaml

from kiss.core.kiss_agent import KISSAgent
from kiss.core.utils import finish as structured_finish

_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _tool_call_response(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-compatible response with a single tool call."""
    return {
        "id": "chatcmpl-test-finish",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_finish",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": _USAGE,
    }


def _serve(respond: Callable[[int], dict[str, Any]]) -> HTTPServer:
    """Start a local chat-completions server; ``respond(turn)`` builds each reply."""
    turn_counter = [0]

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length:
                self.rfile.read(content_length)
            body = json.dumps(respond(turn_counter[0])).encode()
            turn_counter[0] += 1
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _run(server: HTTPServer, agent: KISSAgent, tools: list[Callable[..., Any]]) -> str:
    """Run *agent* against the local server and return its result."""
    return agent.run(
        model_name="gpt-4o-mini",
        prompt_template="Report the status.",
        tools=tools,
        max_steps=6,
        max_budget=5.0,
        verbose=False,
        model_config={
            "base_url": f"http://127.0.0.1:{server.server_address[1]}/v1",
            "api_key": "sk-test",
        },
    )


def _finish_result(server: HTTPServer, agent_name: str) -> dict[str, Any]:
    """Run one finish call against *server* and parse the YAML result."""
    try:
        agent = KISSAgent(agent_name)
        payload = yaml.safe_load(_run(server, agent, tools=[structured_finish]))
        assert isinstance(payload, dict), payload
        return payload
    finally:
        server.shutdown()


class TestFinishNearMissArgs:
    """kimi-k3-style malformed finish args must not lose the summary."""

    def test_trailing_colon_summary_key_is_salvaged(self) -> None:
        """The most common production failure: ``"summary_in_html:"``."""
        server = _serve(
            lambda turn: _tool_call_response(
                "finish",
                {"success": True, "summary_in_html:": "<p>Recovered summary.</p>"},
            )
        )
        payload = _finish_result(server, "test-finish-colon")
        assert payload["success"] is True
        assert payload["summary"] == "<p>Recovered summary.</p>"

    def test_summary_in_html_string_suffix_is_salvaged(self) -> None:
        """Second observed variant: ``"summary_in_html_string"``."""
        server = _serve(
            lambda turn: _tool_call_response(
                "finish",
                {"success": True, "summary_in_html_string": "<p>Suffix variant.</p>"},
            )
        )
        payload = _finish_result(server, "test-finish-suffix")
        assert payload["summary"] == "<p>Suffix variant.</p>"

    def test_exact_key_wins_over_near_miss(self) -> None:
        """A correct ``summary_in_html`` beats a near-miss duplicate, and the
        near-miss key must not crash the call with an unexpected-kwarg error."""
        server = _serve(
            lambda turn: _tool_call_response(
                "finish",
                {
                    "success": True,
                    "summary_in_html": "<p>Correct key.</p>",
                    "summary_in_html:": "<p>Near miss.</p>",
                },
            )
        )
        payload = _finish_result(server, "test-finish-exact-wins")
        assert payload["summary"] == "<p>Correct key.</p>"

    def test_empty_exact_key_is_filled_from_near_miss(self) -> None:
        """An empty exact key must not shadow real content in a near-miss key."""
        server = _serve(
            lambda turn: _tool_call_response(
                "finish",
                {
                    "success": True,
                    "summary_in_html": "",
                    "summary_in_html:": "<p>From near miss.</p>",
                },
            )
        )
        payload = _finish_result(server, "test-finish-empty-exact")
        assert payload["summary"] == "<p>From near miss.</p>"

    def test_unrelated_unknown_key_still_errors(self) -> None:
        """Keys that match no parameter keep the old behavior: a visible
        error, not a silently empty summary.  (A finish result is terminal
        in ``_execute_step``, so the error text ends the run.)"""
        server = _serve(
            lambda turn: _tool_call_response(
                "finish",
                {"success": True, "unrelated_key": 1, "summary_in_html": "<p>Real.</p>"},
            )
        )
        try:
            agent = KISSAgent("test-finish-unknown-key")
            result = _run(server, agent, tools=[structured_finish])
            assert "Failed to call finish" in result
            assert "unrelated_key" in result
        finally:
            server.shutdown()

    def test_kwargs_finish_receives_keys_untouched(self) -> None:
        """A finish contract taking ``**kwargs`` accepts every key by
        definition, so salvage must leave the arguments alone."""
        captured: dict[str, Any] = {}

        def finish(**kwargs: Any) -> str:
            captured.update(kwargs)
            return "done"

        server = _serve(
            lambda turn: _tool_call_response(
                "finish",
                {"success": True, "summary_in_html:": "raw"},
            )
        )
        try:
            agent = KISSAgent("test-finish-kwargs")
            result = _run(server, agent, tools=[finish])
            assert result == "done"
            assert captured == {"success": True, "summary_in_html:": "raw"}
        finally:
            server.shutdown()
