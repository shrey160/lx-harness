"""Session hook preserving the pinned engine's cache/audit/harvest; no live I/O."""
from pathlib import Path, PurePosixPath
import threading

from .spark import MODEL

_SECRET_NAMES = {"credentials", "credentials.json", "service-account.json", "secrets.json",
                 "id_rsa", "id_ed25519", "id_ecdsa", "authorized_keys"}


def _tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


TOOLS = [
    _tool("list_files", "List allowed files relative to the session context.", {}, []),
    _tool("read_file", "Read a UTF-8 file relative to the session context.", {"path": {"type": "string"}}, ["path"]),
    _tool("write_file", "Replace a UTF-8 file inside harness/ only.", {
        "path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
]


def allowed_path(root, name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name:
        raise ValueError("invalid context path")
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(p in {"..", "."} or p.startswith(".") or p in {"__pycache__", "node_modules"} for p in relative.parts):
        raise ValueError("invalid context path")
    if any(p.lower() in _SECRET_NAMES or p.lower().endswith((".pem", ".key", ".p12", ".env")) or ".env." in p.lower() for p in relative.parts):
        raise ValueError("secret filename rejected")
    current = root
    for component in relative.parts:
        current = current/component
        if current.is_symlink():
            raise ValueError("linked paths are not allowed")
    target = current.resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("path escapes context")
    if target.is_file() and target.suffix.lower() not in {".md", ".json", ".py", ".txt", ".diff"}:
        raise ValueError("unsupported session file type")
    return target


class FileTools:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def __call__(self, name, arguments):
        if name == "list_files" and not arguments:
            files = []
            for entry in sorted(self.root.rglob("*")):
                try:
                    path = allowed_path(self.root, entry.relative_to(self.root).as_posix())
                except ValueError:
                    continue
                if path.is_file() and path.stat().st_size <= 100_000:
                    files.append(entry.relative_to(self.root).as_posix())
            return files[:200]
        if name not in {"read_file", "write_file"}:
            raise ValueError("unknown tool")
        expected = {"path"} if name == "read_file" else {"path", "content"}
        if set(arguments) != expected:
            raise ValueError("invalid tool fields")
        path = allowed_path(self.root, arguments["path"])
        if name == "read_file":
            if not path.is_file() or path.stat().st_size > 100_000:
                raise ValueError("file missing or too large")
            return path.read_text(encoding="utf-8")
        if not arguments["path"].startswith("harness/"):
            raise ValueError("only harness files can be written")
        text = arguments["content"]
        if not isinstance(text, str) or len(text.encode()) > 100_000:
            raise ValueError("invalid file content")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return "written"


class SparkProposer:
    def __init__(self, client, audit_dir, *, role="improver"):
        from harness2.core.proposer import Proposer
        from harness2.core.spec import ChannelSpec
        self.client, self.role = client, role
        self._local = threading.local()
        self._delegate = Proposer(ChannelSpec("opencode", MODEL, timeout=180), audit_dir=Path(audit_dir), attempts=1)
        self._delegate._opencode = self._session
        self.spec, self._calls = self._delegate.spec, self._delegate._calls
        self.audit_dir, self.attempts = self._delegate.audit_dir, 1

    def call(self, *args, **kwargs):
        self._local.label = kwargs.get("label", args[2] if len(args) > 2 else "session")
        # Reject unexpected inputs by filename before upstream fingerprints/copies them.
        context = kwargs.get("context_dir", args[1] if len(args) > 1 else None)
        if context is not None:
            root = Path(context)
            if root.is_symlink():
                raise ValueError("linked context rejected")
            for item in root.rglob("*"):
                allowed_path(root, item.relative_to(root).as_posix())
        # Upstream may request 2; the protocol alone owns transport retries.
        kwargs["max_attempts"] = 1
        return self._delegate.call(*args, **kwargs)

    def _session(self, cwd, mission, system, wall):
        files = FileTools(cwd/"context")
        messages = [
            {"role": "developer", "content": (
                "Work only through the supplied context file tools. Paths are relative to context. "
                "Read the files you need; edit only harness/. Do not request shell or network access. "
                "Follow the mission's exact final response format.\n"+system)},
            {"role": "user", "content": mission+"\nAvailable files:\n"+"\n".join(files("list_files", {}))},
        ]
        phase = "reflection" if (cwd/"context"/"adoption.json").is_file() else (
            "selection" if (cwd/"context"/"candidates").is_dir() else "improvement")
        text, cost = self.client.session(messages, files, TOOLS, role=self.role, phase=phase,
                                         candidate=self._local.label, timeout=wall)
        return text, float(cost), ""
