# Author: Koushik Sen (ksen@berkeley.edu)
# Contributors:
# Koushik Sen (ksen@berkeley.edu)
# add your name here
"""MCP tool wrappers parse JSON text sent for object/array parameters.

Reproduces the Atlassian MCP failure seen in production: the model
called ``transitionJiraIssue`` with ``transition='{"id": "131"}'`` and
the server rejected it with ``Expected object, received string``.  Two
things were wrong in ``make_mcp_tool_wrapper``:

1. Object and array properties were annotated with the bare ``dict`` and
   ``list`` types, which kiss's schema builder renders as ``string``, so
   the model was told to send text in the first place.
2. ``wrapper()`` forwarded the text unchanged instead of parsing it.

Every test here spawns a real FastMCP stdio server whose tools echo the
Python type of each argument they received, so a coerced argument is
observed at the server, not at the wrapper.  No model calls are made.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from kiss.agents.sorcar.mcp_servers import (
    MCPManager,
    MCPServerConfig,
    _connection_key,
    make_mcp_tool_wrapper,
    make_mcp_tools,
    save_mcp_server,
)
from kiss.core.models.model import _python_type_to_json_schema

_SERVER_SCRIPT = '''
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("typesrv")


def _kind(value: Any) -> str:
    return type(value).__name__


@mcp.tool()
def transition(
    issue: str, transition: dict[str, Any], fields: dict[str, Any] | None = None,
) -> str:
    """Echo the received argument types (object parameters)."""
    return json.dumps({
        "issue": issue,
        "transition": _kind(transition),
        "id": transition.get("id"),
        "fields": _kind(fields),
    })


@mcp.tool()
def pick(fields: list[str]) -> str:
    """Echo the received argument type (array parameter)."""
    return json.dumps({"fields": _kind(fields), "value": fields})


@mcp.tool()
def spaces(keys: str | list[str]) -> str:
    """Echo the received argument type (string-or-array parameter)."""
    return json.dumps({"keys": _kind(keys), "value": keys})


@mcp.tool()
def note(text: str) -> str:
    """Echo the received argument type (string parameter)."""
    return json.dumps({"text": _kind(text), "value": text})


if __name__ == "__main__":
    mcp.run()
'''


@pytest.fixture
def isolated_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every user-level MCP location into *tmp_path*."""
    monkeypatch.setenv("KISS_HOME", str(tmp_path / ".kisshome"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "project").mkdir()
    return tmp_path


def _stdio_config(tmp_path: Path) -> MCPServerConfig:
    """Return a stdio config running the type-echo FastMCP server."""
    script = tmp_path / "typesrv.py"
    script.write_text(_SERVER_SCRIPT, encoding="utf-8")
    return MCPServerConfig(
        name="typesrv", transport="stdio", command=sys.executable, args=(str(script),),
    )


def _live_tools(isolated_homes: Path, tmp_path: Path) -> dict[str, Any]:
    """Start the echo server and return its wrapped tools by name."""
    project = isolated_homes / "project"
    save_mcp_server(_stdio_config(tmp_path), "user", str(project))
    tools = make_mcp_tools(str(project))
    by_name = {t.__name__: t for t in tools}
    assert set(by_name) == {
        "typesrv_transition", "typesrv_pick", "typesrv_spaces", "typesrv_note",
    }
    return by_name


def test_object_param_json_text_reaches_server_as_object(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """The production failure: ``transition='{"id": "131"}'`` becomes a dict."""
    tools = _live_tools(isolated_homes, tmp_path)
    out = json.loads(tools["typesrv_transition"](
        issue="RND-1", transition='{"id": "131"}', fields='{"resolution": null}',
    ))
    assert out == {"issue": "RND-1", "transition": "dict", "id": "131", "fields": "dict"}


def test_object_param_native_dict_passes_through(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """A real dict (what the model sends once the schema says object) is untouched."""
    tools = _live_tools(isolated_homes, tmp_path)
    out = json.loads(tools["typesrv_transition"](issue="RND-1", transition={"id": "2"}))
    assert out == {"issue": "RND-1", "transition": "dict", "id": "2", "fields": "NoneType"}


def test_array_param_json_text_reaches_server_as_list(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """``fields='["status", "summary"]'`` becomes a list at the server."""
    tools = _live_tools(isolated_homes, tmp_path)
    out = json.loads(tools["typesrv_pick"](fields='["status", "summary"]'))
    assert out == {"fields": "list", "value": ["status", "summary"]}
    out = json.loads(tools["typesrv_pick"](fields=["a"]))
    assert out == {"fields": "list", "value": ["a"]}


def test_string_or_array_param_keeps_plain_text_and_parses_arrays(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """For ``anyOf[string, array]`` only JSON that parses to a list is converted."""
    tools = _live_tools(isolated_homes, tmp_path)
    spaces = tools["typesrv_spaces"]
    assert json.loads(spaces(keys="ENG")) == {"keys": "str", "value": "ENG"}
    assert json.loads(spaces(keys='["ENG", "OPS"]')) == {
        "keys": "list", "value": ["ENG", "OPS"],
    }
    # Text that only looks like an array stays text.
    assert json.loads(spaces(keys="[ENG, OPS")) == {"keys": "str", "value": "[ENG, OPS"}
    # JSON text of a type the property does not take (an object) is
    # forwarded as text; FastMCP itself then parses and rejects it, which
    # is the server's call, not the wrapper's.
    assert spaces(keys='{"a": 1}').startswith("Error:")


def test_string_param_is_never_parsed(isolated_homes: Path, tmp_path: Path) -> None:
    """JSON-looking text for a plain string property is forwarded verbatim."""
    tools = _live_tools(isolated_homes, tmp_path)
    out = json.loads(tools["typesrv_note"](text='{"not": "parsed"}'))
    assert out == {"text": "str", "value": '{"not": "parsed"}'}
    out = json.loads(tools["typesrv_note"](text="[1, 2]"))
    assert out == {"text": "str", "value": "[1, 2]"}


def test_unparseable_text_for_object_param_is_forwarded_and_server_rejects_it(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """Invalid JSON and JSON of the wrong type reach the server, which reports the error."""
    tools = _live_tools(isolated_homes, tmp_path)
    transition = tools["typesrv_transition"]
    result = transition(issue="RND-1", transition="not json at all")
    assert result.startswith("Error:")
    assert "transition" in result
    result = transition(issue="RND-1", transition='["a", "list"]')
    assert result.startswith("Error:")


def test_wrapper_signature_and_tool_schema_advertise_containers(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """The model is told object/array, not string, for container parameters."""
    tools = _live_tools(isolated_homes, tmp_path)
    params = inspect.signature(tools["typesrv_transition"]).parameters
    assert params["transition"].annotation == dict[str, Any]
    assert _python_type_to_json_schema(params["transition"].annotation) == {
        "type": "object",
    }
    assert params["issue"].annotation is str
    # FastMCP publishes ``dict[str, Any] | None`` as ``anyOf[object, null]``;
    # the optional object must still be advertised as an object, not as
    # ``anyOf[object, string]``.
    assert params["fields"].annotation == dict[str, Any] | None
    assert _python_type_to_json_schema(params["fields"].annotation) == {
        "type": "object",
    }

    pick = inspect.signature(tools["typesrv_pick"]).parameters["fields"]
    assert pick.annotation == list[str]
    assert _python_type_to_json_schema(pick.annotation) == {
        "type": "array", "items": {"type": "string"},
    }

    keys = inspect.signature(tools["typesrv_spaces"]).parameters["keys"]
    assert _python_type_to_json_schema(keys.annotation) == {
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
    }

    assert inspect.signature(tools["typesrv_note"]).parameters["text"].annotation is str


def test_hand_written_schema_variants_drive_coercion(
    isolated_homes: Path, tmp_path: Path,
) -> None:
    """``type`` lists, ``allOf``, nested arrays and untyped properties.

    The wrapper is built from a hand-written ``inputSchema`` (as a
    server may publish) but routed to the live echo server, so each
    schema shape is checked by what the server receives.
    """
    from mcp.types import Tool

    _live_tools(isolated_homes, tmp_path)
    key = _connection_key(_stdio_config(tmp_path))
    manager = MCPManager.instance()

    tool = Tool(
        name="transition",
        description="hand-written",
        inputSchema={
            "type": "object",
            "properties": {
                "issue": {},
                "transition": {"type": ["object", "null"]},
                "fields": {"allOf": [{"type": "object"}]},
                "bogus": "not even a schema",
            },
            "required": ["issue", "transition"],
        },
    )
    wrapper = make_mcp_tool_wrapper(manager, "typesrv", tool, key)
    params = inspect.signature(wrapper).parameters
    assert params["issue"].annotation is str
    assert params["bogus"].annotation is str
    # A ``type`` list is the union of its names, rendered as the non-null one.
    assert params["transition"].annotation == dict[str, Any] | None
    assert _python_type_to_json_schema(params["transition"].annotation) == {
        "type": "object",
    }
    # ``allOf`` has no single declared type for the annotation, but the
    # coercion still knows the property takes an object.
    assert params["fields"].annotation is str
    out = json.loads(wrapper(
        issue="RND-1", transition='{"id": "9"}', fields='{"x": 1}', bogus=None,
    ))
    assert out == {"issue": "RND-1", "transition": "dict", "id": "9", "fields": "dict"}

    nested = Tool(
        name="pick",
        description="nested array",
        inputSchema={
            "type": "object",
            "properties": {
                "fields": {"type": "array", "items": {"type": "string"}},
                "extra": {"type": "array", "items": {"type": "object"}},
                "loose": {"type": "array"},
                "either": {"oneOf": [{"type": "string"}, {"type": "string"}]},
            },
            "required": ["fields"],
        },
    )
    wrapper = make_mcp_tool_wrapper(manager, "typesrv", nested, key)
    params = inspect.signature(wrapper).parameters
    assert params["extra"].annotation == list[dict[str, Any]]
    assert params["loose"].annotation == list[str]
    assert params["either"].annotation is str
    assert json.loads(wrapper(fields='["k"]')) == {"fields": "list", "value": ["k"]}
