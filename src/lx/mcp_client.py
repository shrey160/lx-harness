"""Tools from local MCP servers, for the agent inside a run (D-030). `mcp_server` is the other direction.

A person connects a server, because its command runs on this machine outside
the sandbox. Each tool it offers is pinned as read then, and is usable only
once a person marks it read-only: a rollout repeats calls, and MCP's own
annotations are not to be trusted. The controller holds the connection, so the
sandbox stays without network, and a spec holds the definitions it approved.

Standard library only, so a run needs no MCP extra: newline-delimited JSON-RPC
over stdio, which is all a local server needs. The child gets a short
environment, so a server never inherits a key from this process.
"""
from __future__ import annotations

import json
import os
from queue import Empty, Queue
import re
import subprocess
from threading import Lock, Thread
import time

PROTOCOL = "2025-06-18"
PREFIX = "mcp_"
CALL_SECONDS, START_SECONDS = 30, 30
MAX_RESULT = 8000
MAX_LINE = 4 * 1024 ** 2
MAX_TOOLS = 32
MAX_DESCRIPTION = 2000
MAX_SCHEMA = 8000
TOOL_FIELDS = ("tool", "description", "inputSchema")
_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SERVER = re.compile(r"[a-z][a-z0-9_]{0,20}")
_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
        "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR")


class McpError(ValueError):
    """Becomes a tool error the agent sees, or a refusal before a run."""


def check_server_name(name):
    if type(name) is not str or _SERVER.fullmatch(name) is None:
        raise McpError("A server name needs lower-case letters, digits and underscores, at most 21")
    return name


def check_command(command):
    if type(command) is not list or not command or any(type(part) is not str or not part or "\0" in part for part in command):
        raise McpError("Give the server's command as a program and its arguments")
    return command


def agent_name(server, tool):
    """The name the agent sees, which providers limit to letters, digits, _ and -."""
    return PREFIX+server+"_"+tool.replace(".", "_")


def _clean(tool):
    if type(tool) is not dict or type(tool.get("name")) is not str or _TOOL.fullmatch(tool["name"]) is None:
        raise McpError("The server offered a tool with an unusable name")
    schema = tool.get("inputSchema")
    if type(schema) is not dict or schema.get("type") != "object" or len(json.dumps(schema)) > MAX_SCHEMA:
        raise McpError(f"The server's tool {tool['name']} has an unusable input schema")
    description = tool.get("description")
    return {"tool": tool["name"], "description": (description if type(description) is str else "")[:MAX_DESCRIPTION], "inputSchema": schema}


class Session:
    """One running server. Requests go one at a time."""

    def __init__(self, command):
        env = {key: os.environ[key] for key in _ENV if key in os.environ}
        try:
            self.process = subprocess.Popen(check_command(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        except OSError as exc:
            raise McpError(f"Could not start the server: {exc.strerror or 'it would not run'}") from None
        self.lines, self.next_id, self.lock = Queue(), 0, Lock()
        Thread(target=self._read, daemon=True).start()
        try:
            self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "lx", "version": "1"}}, START_SECONDS)
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            self.close()
            raise

    def _read(self):
        for line in iter(lambda: self.process.stdout.readline(MAX_LINE), b""):
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, message):
        try:
            self.process.stdin.write(json.dumps(message).encode()+b"\n")
            self.process.stdin.flush()
        except OSError:
            raise McpError("The server closed its connection") from None

    def _request(self, method, params, seconds):
        with self.lock:
            self.next_id += 1
            self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
            deadline = time.monotonic()+seconds
            while True:
                try:
                    line = self.lines.get(timeout=max(0, deadline-time.monotonic()))
                except Empty:
                    raise McpError(f"The server did not answer within {seconds} seconds") from None
                if line is None:
                    raise McpError("The server closed its connection")
                try:
                    message = json.loads(line)
                except ValueError:
                    continue  # a server's stray log line
                if type(message) is not dict:
                    continue
                if "method" in message and "id" in message:  # a server's own request; a client with no capabilities refuses it
                    self._send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Not supported"}})
                elif message.get("id") == self.next_id:
                    if "error" in message or type(message.get("result")) is not dict:
                        raise McpError("The server refused that request")
                    return message["result"]

    def tools(self):
        found, cursor = [], None
        while True:
            page = self._request("tools/list", {"cursor": cursor} if cursor else {}, START_SECONDS)
            found += page.get("tools") or []
            cursor = page.get("nextCursor")
            if len(found) > MAX_TOOLS:
                raise McpError(f"The server offers more than {MAX_TOOLS} tools")
            if not cursor:
                return sorted((_clean(tool) for tool in found), key=lambda tool: tool["tool"])

    def call(self, tool, arguments):
        result = self._request("tools/call", {"name": tool, "arguments": arguments}, CALL_SECONDS)
        parts = [item.get("text", "") if type(item) is dict and item.get("type") == "text" else "[content that is not text was left out]"
                 for item in result.get("content") or []]
        text = "\n".join(part for part in parts if type(part) is str)
        if result.get("isError"):
            raise McpError("The tool reported an error: "+text[:500])
        return {"text": text[:MAX_RESULT], "truncated": len(text) > MAX_RESULT}

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()


def read_tools(command):
    """What a server offers now. Starts it, so only a person's action may reach this."""
    session = Session(command)
    try:
        return session.tools()
    finally:
        session.close()


def definition(server, tool):
    """A spec's record of one tool, approved with it."""
    return {"server": server, "name": agent_name(server, tool["tool"]), **tool}


def function_tool(entry):
    return {"type": "function", "function": {"name": entry["name"], "description": entry["description"], "parameters": entry["inputSchema"]}}


class McpRun:
    """A run's connections: started on first use, cached by call so a rerun compares harnesses and not a changing server."""

    def __init__(self, entries, commands, call_limit):
        self.entries, self.commands, self.call_limit = {entry["name"]: entry for entry in entries}, commands, call_limit
        self.tools = [function_tool(entry) for entry in entries]
        self.sessions, self.cache, self.calls, self.lock = {}, {}, 0, Lock()

    def _session(self, server):
        if server not in self.sessions:
            self.sessions[server] = Session(self.commands[server])
        return self.sessions[server]

    def check(self):
        """Refuses a run if a server no longer offers what was approved."""
        for server in sorted({entry["server"] for entry in self.entries.values()}):
            offered = {tool["tool"]: tool for tool in self._session(server).tools()}
            for entry in self.entries.values():
                if entry["server"] == server and offered.get(entry["tool"]) != {key: entry[key] for key in TOOL_FIELDS}:
                    raise McpError(f"The server {server} changed its tool {entry['tool']} since approval; a person needs to refresh it")

    def call(self, name, arguments):
        entry, key = self.entries[name], json.dumps([name, arguments], sort_keys=True)
        with self.lock:
            if key not in self.cache:
                if self.calls >= self.call_limit:
                    raise McpError("This run reached its limit of MCP tool calls")
                self.calls += 1
                self.cache[key] = self._session(entry["server"]).call(entry["tool"], arguments)
            return self.cache[key]

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                session.close()
            self.sessions = {}


class Conversation:
    """One agent run's view: its results reach helper scripts, as segment boxes do, so a script can use what a tool returned."""
    MAX_KEPT = 20

    def __init__(self, run):
        self.run, self.results = run, []

    def __contains__(self, name):
        return name in self.run.entries

    def __call__(self, name, arguments):
        result = {"tool": name, **self.run.call(name, arguments)}
        self.results = (self.results+[{**result, "arguments": arguments}])[-self.MAX_KEPT:]
        return result
