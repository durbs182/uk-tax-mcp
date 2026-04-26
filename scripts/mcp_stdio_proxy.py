#!/usr/bin/env python3
"""MCP JSON-RPC stdio server that proxies tool calls to the HTTP dev server.

Implements the MCP stdio transport (Content-Length framing, JSON-RPC 2.0)
and handles: initialize, tools/list, tools/call, notifications/*.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

HTTP_ENDPOINT = "http://127.0.0.1:8000/call"

TOOLS = [
    {
        "name": "list_rules",
        "description": "List all available HMRC tax rules in the registry.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_rule",
        "description": "Get a specific tax rule by ID and optional version/jurisdiction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Rule identifier"},
                "version": {"type": "string", "description": "Rule version (default: latest)"},
                "jurisdiction": {"type": "string", "description": "e.g. rUK or scotland"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "execute_rule",
        "description": "Execute a tax rule with input variables and return the computed result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "inputs": {"type": "object", "description": "Input variables as key/value pairs"},
                "jurisdiction": {"type": "string"},
                "trace": {"type": "boolean", "description": "Return evaluation trace steps"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "compile_dsl",
        "description": "Compile DSL source code to an AST.",
        "inputSchema": {
            "type": "object",
            "properties": {"dsl": {"type": "string", "description": "DSL source text"}},
            "required": ["dsl"],
        },
    },
    {
        "name": "validate_rule",
        "description": "Validate a tax rule against semantic checks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "jurisdiction": {"type": "string"},
            },
            "required": ["rule_id"],
        },
    },
    {
        "name": "explain_rule",
        "description": "Explain a tax rule in plain English.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "jurisdiction": {"type": "string"},
            },
            "required": ["rule_id"],
        },
    },
]


def log(*parts: object) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[hmrc-mcp {ts}]", *parts, file=sys.stderr, flush=True)


def read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            return None
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    try:
        return json.loads(body.decode("utf-8"))
    except Exception as exc:
        log("JSON parse error:", exc)
        return None


def write_message(obj: dict) -> None:
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    b = s.encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(b)}\r\n\r\n".encode())
    sys.stdout.buffer.write(b)
    sys.stdout.buffer.flush()


def call_http(name: str, arguments: dict) -> object:
    payload = json.dumps({"name": name, "arguments": arguments or {}}).encode("utf-8")
    req = urllib.request.Request(
        HTTP_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc


def handle(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    # Notifications require no response
    if method.startswith("notifications/"):
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "uk-tax-mcp", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = call_http(name, arguments)
            text = json.dumps(result, indent=2, ensure_ascii=False)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        except Exception as exc:
            log("tool call error:", exc)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    log("starting — forwarding tool calls to", HTTP_ENDPOINT)
    while True:
        try:
            msg = read_message()
        except Exception as exc:
            log("read error:", exc)
            break
        if msg is None:
            log("stdin closed, exiting")
            break
        method = msg.get("method", "?")
        log("recv", method, "id=", msg.get("id"))
        try:
            resp = handle(msg)
        except Exception as exc:
            log("handler error:", exc)
            resp = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": str(exc)},
            }
        if resp is not None:
            log("send id=", resp.get("id"))
            write_message(resp)


if __name__ == "__main__":
    main()
