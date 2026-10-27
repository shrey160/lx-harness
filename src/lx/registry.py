"""SQLite model catalog with API credentials kept in separate private files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from typing import Any

from .spark import MODEL


class RegistryError(ValueError):
    """Invalid registry operation or unavailable runtime credential."""


_BUILTINS = (
    (
        "spark-contributor",
        "Muse Spark 1.3 Contributor",
        MODEL,
        ["General reasoning and task execution"],
    ),
    ("sam-catalog", "SAM", "meta/sam", ["Image segmentation and object masks"]),
    ("transcribe-catalog", "Transcribe", "meta/transcribe", ["Speech-to-text transcription"]),
)
_MAX_NAME = 120
_MAX_MODEL_ID = 200
_MAX_CAPABILITY = 240
_MAX_CAPABILITIES = 20
_MAX_KEY = 8192


class Registry:
    """Model metadata and runtime-only credential access."""

    def __init__(self, db_path: str | Path, secrets_dir: str | Path):
        self.db_path = Path(db_path)
        self.secrets_dir = Path(secrets_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS models (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    builtin INTEGER NOT NULL DEFAULT 0,
                    secret_ref TEXT
                );
                """
            )
            db.execute("BEGIN IMMEDIATE")
            for model_id, name, upstream_id, capabilities in _BUILTINS:
                db.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id,name,model_id,capabilities,builtin,secret_ref) VALUES (?,?,?,?,1,NULL)",
                    (model_id, name, upstream_id, json.dumps(capabilities)),
                )
            db.commit()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @staticmethod
    def _text(value: Any, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise RegistryError(f"invalid {field}")
        return value.strip()

    @classmethod
    def _capabilities(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, (list, tuple)):
            values = list(value)
        else:
            raise RegistryError("invalid capabilities")
        if not values or len(values) > _MAX_CAPABILITIES:
            raise RegistryError("invalid capabilities")
        return [cls._text(item, "capability", _MAX_CAPABILITY) for item in values]

    @staticmethod
    def _api_key(value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_KEY:
            raise RegistryError("invalid API key")
        return value

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "model_id": row["model_id"],
            "capabilities": json.loads(row["capabilities"]),
            "live_eligible": row["model_id"] == MODEL,
            "has_key": row["secret_ref"] is not None,
            "credential_source": (
                "registered" if row["secret_ref"] is not None
                else "runtime configuration" if row["model_id"] == MODEL
                else None
            ),
        }

    def list_models(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM models ORDER BY builtin DESC, name, id").fetchall()
        return [self._public(row) for row in rows]

    def create(
        self,
        name: str,
        model_id: str,
        capabilities: Any,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        name = self._text(name, "name", _MAX_NAME)
        model_id = self._text(model_id, "model_id", _MAX_MODEL_ID)
        caps = self._capabilities(capabilities)
        key = self._api_key(api_key)
        if model_id == MODEL:
            raise RegistryError("the Spark model uses its runtime credential configuration")
        record_id = uuid.uuid4().hex
        secret_ref = self._write_secret(key) if key is not None else None
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO models (id,name,model_id,capabilities,builtin,secret_ref) "
                    "VALUES (?,?,?,?,0,?)",
                    (record_id, name, model_id, json.dumps(caps), secret_ref),
                )
                row = db.execute("SELECT * FROM models WHERE id=?", (record_id,)).fetchone()
                db.commit()
        except sqlite3.IntegrityError:
            if secret_ref:
                self._remove_secret(secret_ref)
            raise RegistryError("model could not be created") from None
        except Exception:
            if secret_ref:
                self._remove_secret(secret_ref)
            raise
        return self._public(row)

    def update(
        self,
        record_id: str,
        *,
        name: str | None = None,
        capabilities: Any = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        if name is not None:
            name = self._text(name, "name", _MAX_NAME)
        caps = None if capabilities is None else self._capabilities(capabilities)
        key = self._api_key(api_key)
        new_ref = self._write_secret(key) if key is not None else None
        old_ref: str | None = None
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM models WHERE id=?", (record_id,)).fetchone()
                if row is None:
                    raise RegistryError("unknown model id")
                if row["builtin"] and row["model_id"] != MODEL:
                    raise RegistryError("catalog models are immutable")
                old_ref = row["secret_ref"]
                next_name = name if name is not None else row["name"]
                next_caps = json.dumps(caps) if caps is not None else row["capabilities"]
                next_ref = new_ref if key is not None else old_ref
                db.execute(
                    "UPDATE models SET name=?, capabilities=?, secret_ref=? WHERE id=?",
                    (next_name, next_caps, next_ref, record_id),
                )
                result = db.execute("SELECT * FROM models WHERE id=?", (record_id,)).fetchone()
                db.commit()
        except Exception:
            if new_ref:
                self._remove_secret(new_ref)
            raise
        if key is not None and old_ref:
            self._remove_secret(old_ref)
        return self._public(result)

    def delete(self, record_id: str) -> None:
        secret_ref: str | None = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT builtin, secret_ref FROM models WHERE id=?", (record_id,)).fetchone()
            if row is None:
                db.rollback()
                raise RegistryError("unknown model id")
            if row["builtin"]:
                db.rollback()
                raise RegistryError("catalog models are immutable")
            secret_ref = row["secret_ref"]
            db.execute("DELETE FROM models WHERE id=?", (record_id,))
            db.commit()
        if secret_ref:
            self._remove_secret(secret_ref)

    def credential(self, record_id: str) -> str:
        with self._connect() as db:
            row = db.execute("SELECT model_id, secret_ref FROM models WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise RegistryError("unknown model id")
        if row["model_id"] == MODEL and row["secret_ref"]:
            try:
                return (self.secrets_dir / row["secret_ref"]).read_text(encoding="utf-8")
            except OSError:
                raise RegistryError("registered credential unavailable") from None
        if row["model_id"] == MODEL:
            try:
                from .live import _runtime_key

                return _runtime_key()
            except Exception:
                raise RegistryError("runtime credential unavailable") from None
        raise RegistryError("live credentials are available only for the Spark model")

    def _write_secret(self, key: str | None) -> str | None:
        if key is None:
            return None
        reference = uuid.uuid4().hex
        path = self.secrets_dir / reference
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                if os.name == "posix" and stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                    raise RegistryError("Credential storage requires a private native filesystem")
                stream.write(key)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            path.unlink(missing_ok=True)
            raise RegistryError("API key could not be stored") from None
        return reference

    def _remove_secret(self, reference: str) -> None:
        try:
            (self.secrets_dir / reference).unlink(missing_ok=True)
        except OSError:
            # The opaque reference remains inaccessible through the registry.
            pass
