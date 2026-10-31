"""A workspace is a user folder; everything LX keeps lives in its .lx/ directory.

Layout: project.json, spec/vN.json and checks/vN/check.py (never rewritten), snapshots/, runs/<id>/
(run.json, events.jsonl, snapshot/, outputs/), packages/<id>/, core-jobs/ and
preview-usage.sqlite3. Samples stay where the user keeps them. Approval records
are deliberately not here: an agent working in the folder can write to .lx/.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import cached_property
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import time
import uuid

from h2portable import _platform

from .budget import BudgetLedger
from .intake import _is_link_mode, inspect_folder, windows_to_wsl

DIRECTORY = ".lx"
SCHEMA = 1
_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_RUN_ID = re.compile(r"[a-f0-9]{12}")
_SPEC_FILE = re.compile(r"v([1-9][0-9]*)\.json")


def native_path(text):
    """The controller's path for a folder a browser or terminal names."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2048:
        raise ValueError("Choose a workspace folder")
    text = text.strip()
    path = Path(windows_to_wsl(text) if os.name == "posix" and _DRIVE_PATH.match(text) else text)
    if not path.is_absolute():
        raise ValueError("Use the folder's full path")
    return path


def _patient(action):
    # Windows refuses a replace or read while another process has the file open.
    for attempt in range(50):
        try:
            return action()
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.02)


def read_json(path):
    return _patient(lambda: json.loads(Path(path).read_text(encoding="utf-8")))


def write_json(path, value):
    path = Path(path)
    stage = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with stage.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2, allow_nan=False)
        output.flush()
        os.fsync(output.fileno())
    _patient(lambda: os.replace(stage, path))


def _samples_dir(value):
    parts = PurePosixPath(value).parts if isinstance(value, str) and value else ()
    if value == ".":
        return value
    if not parts or "\\" in value or ":" in value or any(p == ".." or p.startswith(".") or p == "/" for p in parts):
        raise ValueError("The samples folder must be inside the workspace")
    return "/".join(parts)


class Workspace:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.home = self.folder/DIRECTORY
        if self.home.exists() and _is_link_mode(self.home.lstat()):
            raise ValueError("The workspace's .lx folder cannot be a link")

    @classmethod
    def create(cls, folder, *, name, goal, samples_dir, inventory, compatible_demo):
        workspace = cls(folder)
        if not workspace.folder.is_dir():
            raise ValueError("The workspace folder does not exist")
        try:
            workspace.home.mkdir()
        except FileExistsError:
            raise ValueError("This folder is already a workspace; open it instead") from None
        for name_ in ("spec", "checks", "runs", "packages", "snapshots"):
            (workspace.home/name_).mkdir()
        (workspace.home/".gitignore").write_text("*\n", encoding="utf-8")
        workspace.write_project({"schema": SCHEMA, "id": uuid.uuid4().hex, "name": name, "goal": goal,
                                 "samples_dir": _samples_dir(samples_dir), "inventory": inventory,
                                 "compatible_demo": compatible_demo, "messages": [], "spec_version": 0})
        return workspace

    @classmethod
    def open(cls, folder):
        workspace = cls(folder)
        if not (workspace.home/"project.json").is_file():
            raise ValueError("This folder is not a workspace yet")
        record = workspace.project()
        if record.get("schema") != SCHEMA:
            raise ValueError("This workspace was made by a different version")
        _samples_dir(record.get("samples_dir"))
        return workspace

    def project(self):
        return read_json(self.home/"project.json")

    def write_project(self, record):
        write_json(self.home/"project.json", record)

    @contextmanager
    def lock(self):
        """Exclusive across threads and processes. Not re-entrant."""
        descriptor = os.open(self.home/"lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            _platform.try_lock(descriptor, blocking=True)
            try:
                yield
            finally:
                _platform.unlock(descriptor)
        finally:
            os.close(descriptor)

    @property
    def samples_path(self):
        return self.folder.joinpath(*PurePosixPath(self.project()["samples_dir"]).parts)

    def inspect_samples(self, max_bytes=None):
        return inspect_folder(self.samples_path, skip_root=(DIRECTORY,), max_bytes=max_bytes)

    def spec_versions(self):
        names = (_SPEC_FILE.fullmatch(p.name) for p in (self.home/"spec").iterdir())
        return sorted(int(match[1]) for match in names if match)

    def spec(self, version):
        return read_json(self.home/"spec"/f"v{int(version)}.json")

    def add_spec(self, spec):
        """Each version is one file, written once; exclusive creation enforces it."""
        target = self.home/"spec"/f"v{spec['version']}.json"
        with target.open("x", encoding="utf-8") as output:
            json.dump(spec, output, indent=2, allow_nan=False)
        return spec

    def add_check(self, version, source):
        target = self.home/"checks"/f"v{int(version)}"/"check.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(source)

    def check_source(self, check):
        """The check file a spec names, refused if it no longer matches the spec's hash."""
        path = self.home/"checks"/f"v{int(check['version'])}"/"check.py"
        data = path.read_bytes() if path.is_file() else b""
        if hashlib.sha256(data).hexdigest() != check["sha256"]:
            raise ValueError("The scoring check changed; draft a new spec version")
        return data.decode("utf-8")

    def snapshot_dir(self, name):
        if not re.fullmatch(r"[a-f0-9]{32}", name):
            raise ValueError("Invalid snapshot identity")
        return self.home/"snapshots"/name

    def run_dir(self, identifier):
        if not isinstance(identifier, str) or not _RUN_ID.fullmatch(identifier):
            raise ValueError("Invalid run identity")
        return self.home/"runs"/identifier

    def run(self, identifier):
        path = self.run_dir(identifier)/"run.json"
        if not path.is_file():
            raise ValueError("Record not found")
        return read_json(path)

    def runs(self):
        """Newest first; a folder with no run.json never finished starting."""
        records = [read_json(p/"run.json") for p in (self.home/"runs").iterdir() if (p/"run.json").is_file()]
        return sorted(records, key=lambda r: r["created_at"], reverse=True)

    def write_run(self, record):
        write_json(self.run_dir(record["id"])/"run.json", record)
        line = json.dumps({"at": time.time(), "status": record["status"], "phase": record["phase"]})
        with (self.run_dir(record["id"])/"events.jsonl").open("a", encoding="utf-8") as events:
            events.write(line+"\n")
        return record

    def update_run(self, identifier, **changes):
        """Merge under the lock. A None value removes the field."""
        with self.lock():
            record = self.run(identifier)
            if changes.get("status") in {"queued", "running"}:
                record.pop("finished_at", None)
            elif changes.get("status"):
                record.setdefault("finished_at", time.time())
            for key, value in changes.items():
                if value is None:
                    record.pop(key, None)
                else:
                    record[key] = value
            return self.write_run(record)

    def package_dir(self, identifier):
        self.run_dir(identifier)
        return self.home/"packages"/identifier

    def publish_package(self, identifier):
        target = self.package_dir(identifier)
        stage = target.with_name(target.name+".stage")
        shutil.rmtree(stage, ignore_errors=True)
        shutil.copytree(self.run_dir(identifier)/"outputs"/"package", stage)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(stage, target)
        return target

    @cached_property
    def preview_ledger(self):
        return BudgetLedger(self.home/"preview-usage.sqlite3", initial_spend_usd=0)
