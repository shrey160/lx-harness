"""Tools from MCP servers, for the agent inside a run (D-030). `mcp_server` is the other direction.

A server is a command on this machine, a Docker image, or a URL something
already serves. A person connects it, because a command or image runs code and
a URL receives data. Each tool it offers is pinned as read then, and is usable
only once a person marks it read-only: a rollout repeats calls, and MCP's own
annotations are not to be trusted. The controller holds the connection, so the
sandbox stays without network, and a spec holds the definitions it approved.

Standard library only, so a run needs no MCP extra. A child gets a short
environment and only the secrets a person gave its server, so it never inherits
a key from this process.
"""
from __future__ import annotations

import json
import os
from queue import Empty, Queue
from pathlib import Path
import re
import shutil
import subprocess
from threading import Lock, Thread
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

PROTOCOL = "2025-06-18"
PREFIX = "mcp_"
CALL_SECONDS, START_SECONDS, DOCKER_START_SECONDS, CLOSE_SECONDS = 30, 30, 60, 5
KINDS = ("command", "docker", "url")
LOOPBACK = ("localhost", "127.0.0.1", "::1")
URL_TOKEN = "TOKEN"  # the secret sent as a bearer token to a URL server
MAX_RESULT = 8000
MAX_LINE = 4 * 1024 ** 2
MAX_TOOLS = 32
MAX_DESCRIPTION = 2000
MAX_SCHEMA = 8000
TOOL_FIELDS = ("tool", "description", "inputSchema")
_TOOL = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_SERVER = re.compile(r"[a-z][a-z0-9_]{0,20}")
_SECRET = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_IMAGE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./:@-]{0,254}")
_ENV = ("PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA",
        "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "HOME", "USER", "LOGNAME", "LANG", "TERM", "TMPDIR", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")


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


def check_record(data):
    """A server as a person described it, reduced to what LX keeps. Nothing here is passed to a shell."""
    if type(data) is not dict or data.get("kind") not in KINDS:
        raise McpError("Choose how the server runs: "+", ".join(KINDS))
    if data["kind"] == "command":
        return {"kind": "command", "command": check_command(data.get("command"))}
    if data["kind"] == "url":
        return {"kind": "url", "url": check_url(data.get("url"))}
    image, args = data.get("image"), data.get("args") or []
    if type(image) is not str or _IMAGE.fullmatch(image) is None:
        raise McpError("Give the image's name, such as ghcr.io/owner/server:1.2")
    if type(args) is not list or any(type(part) is not str or "\0" in part for part in args):
        raise McpError("Give the container's arguments as a list")
    if type(data.get("network", False)) is not bool:
        raise McpError("Network is on or off")
    mounts = data.get("mounts") or []
    if type(mounts) is not list:
        raise McpError("Give folders to share as a list")
    return {"kind": "docker", "image": image, "args": args, "network": data.get("network", False), "mounts": [_mount(item) for item in mounts]}


def _mount(item):
    source, target = (item.get("source"), item.get("target")) if type(item) is dict else (None, None)
    if type(source) is not str or type(target) is not str or not target.startswith("/") or "," in source+target or "\0" in source+target:
        raise McpError("A shared folder needs a folder here and an absolute path in the container, with no commas")
    if not Path(source).is_absolute() or not Path(source).is_dir():
        raise McpError(f"{source} is not a folder on this machine")
    return {"source": source, "target": target}


def check_url(url):
    """https, or plain http to this machine only: a token must not cross a network in the clear."""
    parts = urlsplit(url) if type(url) is str else None
    if parts is None or not parts.hostname or parts.username or parts.password:
        raise McpError("Give the server's full address, such as http://localhost:8931/mcp")
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in LOOPBACK):
        raise McpError("A server's address must use https, or http on this machine")
    return url


def check_secret_name(name):
    if type(name) is not str or _SECRET.fullmatch(name) is None or name in _ENV:
        raise McpError("A secret's name needs upper-case letters, digits and underscores, and cannot be a system variable")
    return name


def resolve_image(image):
    """The image's id. A run uses the id, so a moved tag cannot swap what was approved."""
    try:
        done = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", image], capture_output=True, text=True, timeout=START_SECONDS, env=_short_env())
    except FileNotFoundError:
        raise McpError("Docker is not installed here") from None
    except subprocess.TimeoutExpired:
        raise McpError("Docker did not answer; is it running?") from None
    if done.returncode == 0:
        return done.stdout.strip()
    if "no such image" in done.stderr.lower():
        raise McpError(f"That image is not on this machine. Run: docker pull {image}")
    raise McpError("Docker could not read that image; is it running?")


def docker_command(record, secrets, name):
    """Runs the image by id, shut in: the network only if a person asked, nothing shared but read-only folders.
    A secret's value stays out of the command line: Docker copies it from this process's environment."""
    command = ["docker", "run", "-i", "--rm", "--init", "--name", name, "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--memory", "1g", "--cpus", "1", "--pids-limit", "256"]
    command += [] if record["network"] else ["--network", "none"]
    for variable in sorted(secrets):
        command += ["--env", variable]
    for mount in record["mounts"]:
        command += ["--mount", f"type=bind,source={mount['source']},target={mount['target']},readonly"]
    return command+[record["image_id"], *record["args"]]


def pin(record):
    """What must stay the same from approval to run, beside the tool's own text."""
    return {"command": lambda: " ".join(record["command"]), "docker": lambda: record["image_id"], "url": lambda: record["url"]}[record["kind"]]()


def where(record):
    """Where the agent's tool arguments go, for the notice."""
    if record["kind"] == "url":
        return urlsplit(record["url"]).hostname
    return "a Docker container on this machine" if record["kind"] == "docker" else "this machine, outside the sandbox"


def _short_env():
    return {key: os.environ[key] for key in _ENV if key in os.environ}


class Session:
    """One connection to a server. Requests go one at a time."""

    def open(self, seconds):
        try:
            self._request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "lx", "version": "1"}}, seconds)
            self._notify("notifications/initialized")
        except McpError:
            self.close()
            raise
        return self

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


def _result(message):
    if "error" in message or type(message.get("result")) is not dict:
        raise McpError("The server refused that request")
    return message["result"]


class StdioSession(Session):
    """A server this process starts: newline-delimited JSON-RPC over its stdin and stdout."""

    def __init__(self, command, secrets=None, container=None):
        self.container = container
        env = {**_short_env(), **(secrets or {})}
        program = shutil.which(command[0], path=env.get("PATH")) or command[0]  # finds npx.cmd and the like on Windows
        try:
            self.process = subprocess.Popen([program, *command[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
        except OSError as exc:
            raise McpError(f"Could not start the server: {exc.strerror or 'it would not run'}") from None
        self.lines, self.next_id, self.lock = Queue(), 0, Lock()
        Thread(target=self._read, daemon=True).start()

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

    def _notify(self, method):
        self._send({"jsonrpc": "2.0", "method": method})

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
                    return _result(message)

    def close(self):
        killed = False
        try:
            self.process.stdin.close()
            self.process.wait(timeout=CLOSE_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            killed = True
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()
        if killed and self.container:  # killing the docker client leaves its container going
            subprocess.run(["docker", "rm", "-f", self.container], capture_output=True, timeout=START_SECONDS, env=_short_env())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class HttpSession(Session):
    """A server already running somewhere: MCP's streamable HTTP. The older HTTP with server-sent events is not supported."""

    def __init__(self, url, token=None):
        self.url, self.token, self.session, self.next_id, self.lock = url, token, None, 0, Lock()
        handlers = [_NoRedirect()]+([urllib.request.ProxyHandler({})] if urlsplit(url).hostname in LOOPBACK else [])
        self.opener = urllib.request.build_opener(*handlers)

    def _post(self, message, seconds):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL,
                   **({"Authorization": "Bearer "+self.token} if self.token else {}), **({"Mcp-Session-Id": self.session} if self.session else {})}
        request = urllib.request.Request(self.url, json.dumps(message).encode(), headers, method="POST")
        try:
            return self.opener.open(request, timeout=seconds)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise McpError({401: "The server refused the token", 403: "The server refused the token"}.get(exc.code, f"The server answered {exc.code}")) from None
        except OSError:
            raise McpError("Could not reach the server") from None

    def _notify(self, method):
        self._post({"jsonrpc": "2.0", "method": method}, START_SECONDS).close()

    def _read_reply(self, response, identifier):
        """The reply to this request, from JSON or an event stream, which may carry other messages first."""
        with response:
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                message = json.loads(response.read(MAX_LINE))
                return message
            data = []
            for raw in response:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and data:
                    message = json.loads("\n".join(data))
                    data = []
                    if type(message) is dict and message.get("id") == identifier and "method" not in message:
                        return message
        raise McpError("The server closed its connection")

    def _request(self, method, params, seconds):
        with self.lock:
            self.next_id += 1
            response = self._post({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}, seconds)
            self.session = response.headers.get("Mcp-Session-Id") or self.session
            try:
                message = self._read_reply(response, self.next_id)
            except ValueError:
                raise McpError("The server sent something that is not MCP") from None
            except OSError:
                raise McpError(f"The server did not answer within {seconds} seconds") from None
            return _result(message if type(message) is dict else {})

    def close(self):
        if self.session:
            try:
                self.opener.open(urllib.request.Request(self.url, headers={"Mcp-Session-Id": self.session, **({"Authorization": "Bearer "+self.token} if self.token else {})},
                                                        method="DELETE"), timeout=CLOSE_SECONDS).close()
            except OSError:
                pass  # a server may not offer this


def connect(record, secrets=None):
    """A session on a checked record. Starts its program, so only a person's action or an approved run may reach this."""
    secrets = secrets or {}
    if record["kind"] == "url":
        return HttpSession(record["url"], secrets.get(URL_TOKEN)).open(START_SECONDS)
    if record["kind"] == "docker":
        name = "lx-mcp-"+uuid.uuid4().hex[:12]
        return StdioSession(docker_command(record, secrets, name), secrets, name).open(DOCKER_START_SECONDS)
    return StdioSession(record["command"], secrets).open(START_SECONDS)


def read_tools(record, secrets=None):
    """What a server offers now."""
    session = connect(record, secrets)
    try:
        return session.tools()
    finally:
        session.close()


def definition(server, record, tool):
    """A spec's record of one tool, approved with it."""
    return {"server": server, "name": agent_name(server, tool["tool"]), "pin": pin(record), "where": where(record), **{key: tool[key] for key in TOOL_FIELDS}}


def function_tool(entry):
    return {"type": "function", "function": {"name": entry["name"], "description": entry["description"], "parameters": entry["inputSchema"]}}


class McpRun:
    """A run's connections: started on first use, cached by call so a rerun compares harnesses and not a changing server."""

    def __init__(self, entries, records, call_limit, secrets=None):
        self.entries, self.records, self.secrets, self.call_limit = {entry["name"]: entry for entry in entries}, records, secrets or {}, call_limit
        self.tools = [function_tool(entry) for entry in entries]
        self.sessions, self.cache, self.calls, self.lock = {}, {}, 0, Lock()

    def _session(self, server):
        if server not in self.sessions:
            self.sessions[server] = connect(self.records[server], self.secrets.get(server))
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
