"""SQLite model catalog; API keys live in the OS keychain, or private files where there is none.

Each entry is a provider: what it does (kind and plain-language
capabilities), where it is called (base URL) and what it costs. Built-in Meta
entries use the shared runtime credential configuration (D-008); every other
entry, local or hosted, is registered the same way with an optional key.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from typing import Any

from . import limits
from .providers import META_API, SAM, SPARK, TRANSCRIBE, ProviderProfile, chat_endpoint, validate_endpoint
from .spark import MODEL


class RegistryError(ValueError):
    """Invalid registry operation or unavailable runtime credential."""


# id, name, model_id, capabilities, profile. Non-Spark built-ins are re-applied
# on every open so older databases pick up the current catalog.
_BUILTINS = (
    (
        "spark-contributor",
        "Muse Spark 1.3 Contributor",
        MODEL,
        ["General reasoning and task execution"],
        SPARK,
    ),
    ("sam-catalog", "SAM 3.1", SAM.model,
     ["Image segmentation: finds every instance of a short noun phrase in a photo and returns one box and mask per instance"],
     SAM),
    ("transcribe-catalog", "Muse Voice Transcribe", TRANSCRIBE.model,
     ["Speech-to-text: transcribes spoken audio (mono WAV) into text"],
     TRANSCRIBE),
)
_PROFILES = {entry[0]: entry[4] for entry in _BUILTINS}
_COLUMNS = {"kind": "TEXT NOT NULL DEFAULT 'chat'", "base_url": "TEXT",
            "accepts_images": "INTEGER NOT NULL DEFAULT 0", "supports_tools": "INTEGER NOT NULL DEFAULT 0",
            "pricing": "TEXT NOT NULL DEFAULT '{}'", "context_limit": "INTEGER",
            "requests_per_minute": "INTEGER", "limits": "TEXT NOT NULL DEFAULT '{}'"}
_PRICE_FIELDS = ("input_per_million", "output_per_million")
_MAX_NAME = 120
_MAX_MODEL_ID = 200
_MAX_CAPABILITY = 240
_MAX_CAPABILITIES = 20
_MAX_KEY = 8192
_KEYCHAIN = "keychain:"


class Registry:
    """Model metadata and runtime-only credential access."""

    def __init__(self, db_path: str | Path, secrets_dir: str | Path, keychain: Any = None):
        self.db_path = Path(db_path)
        self.secrets_dir = Path(secrets_dir)
        self.keychain = keychain
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
            present = {row["name"] for row in db.execute("PRAGMA table_info(models)")}
            for column, definition in _COLUMNS.items():
                if column not in present:
                    db.execute(f"ALTER TABLE models ADD COLUMN {column} {definition}")
            db.execute("BEGIN IMMEDIATE")
            for model_id, name, upstream_id, capabilities, profile in _BUILTINS:
                db.execute(
                    "INSERT OR IGNORE INTO models "
                    "(id,name,model_id,capabilities,builtin,secret_ref) VALUES (?,?,?,?,1,NULL)",
                    (model_id, name, upstream_id, json.dumps(capabilities)),
                )
                db.execute("UPDATE models SET kind=?, base_url=?, accepts_images=?, supports_tools=? WHERE id=?",
                           (profile.kind, META_API, int(profile.accepts_images), int(profile.supports_tools), model_id))
                if upstream_id != MODEL:
                    db.execute("UPDATE models SET name=?, model_id=?, capabilities=? WHERE id=?",
                               (name, upstream_id, json.dumps(capabilities), model_id))
            db.commit()
        self._move_files_to_keychain()

    def _keychain_ready(self) -> bool:
        return self.keychain is not None and self.keychain.available()

    def _move_files_to_keychain(self) -> None:
        with self._connect() as db:
            rows = db.execute("SELECT id, secret_ref FROM models WHERE secret_ref IS NOT NULL").fetchall()
            files = [r for r in rows if not r["secret_ref"].startswith(_KEYCHAIN)]
            if not files or not self._keychain_ready():
                return
            for row in files:
                try:
                    reference = self._write_secret(self._read_secret(row["secret_ref"]))
                except RegistryError:
                    continue  # the file still serves; the next open tries again
                db.execute("UPDATE models SET secret_ref=? WHERE id=?", (reference, row["id"]))
                self._remove_secret(row["secret_ref"])

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

    @staticmethod
    def _base_url(value: Any) -> str | None:
        if value is None:
            return None
        try:
            return validate_endpoint(value.strip() if isinstance(value, str) else value).rstrip("/")
        except ValueError as exc:
            raise RegistryError(str(exc)) from None

    @staticmethod
    def _pricing(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict) or not set(value) <= set(_PRICE_FIELDS):
            raise RegistryError("invalid pricing")
        result = {}
        for field, amount in value.items():
            try:
                price = Decimal(str(amount))
            except (InvalidOperation, ValueError):
                raise RegistryError("invalid pricing") from None
            if isinstance(amount, bool) or not price.is_finite() or price < 0 or price > 1000:
                raise RegistryError("invalid pricing")
            result[field] = str(price)
        return result

    @staticmethod
    def _context_limit(value: Any) -> int | None:
        if value is None:
            return None
        if type(value) is not int or not 1024 <= value <= 20_000_000:
            raise RegistryError("invalid context limit")
        return value

    @staticmethod
    def _rate(value: Any) -> int | None:
        """Calls a minute; 0 clears a stored rate on update."""
        if value is None:
            return None
        if type(value) is not int or not 0 <= value <= 100_000:
            raise RegistryError("invalid request rate")
        return value

    @staticmethod
    def _limits(value: Any) -> dict[str, int] | None:
        if value is None:
            return None
        try:
            return limits.check(value)
        except ValueError as exc:
            raise RegistryError(str(exc)) from None

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        prices = json.loads(row["pricing"])
        return {
            "id": row["id"],
            "name": row["name"],
            "model_id": row["model_id"],
            "capabilities": json.loads(row["capabilities"]),
            "kind": row["kind"],
            "base_url": row["base_url"],
            "accepts_images": bool(row["accepts_images"]),
            "supports_tools": bool(row["supports_tools"]),
            "pricing": prices,
            "context_limit": row["context_limit"],
            "requests_per_minute": row["requests_per_minute"],
            "limits": json.loads(row["limits"]),
            # Any chat entry with an endpoint can fill any role; live spending also needs prices.
            "role_ready": row["kind"] == "chat" and bool(row["base_url"]),
            "priced": bool(row["builtin"]) or set(prices) == set(_PRICE_FIELDS),
            "has_key": row["secret_ref"] is not None,
            "credential_source": (
                "registered" if row["secret_ref"] is not None
                else "runtime configuration" if row["builtin"]
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
        *,
        kind: str = "chat",
        base_url: str | None = None,
        accepts_images: bool = False,
        supports_tools: bool = False,
        pricing: dict[str, Any] | None = None,
        context_limit: int | None = None,
        requests_per_minute: int | None = None,
        limits: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        name = self._text(name, "name", _MAX_NAME)
        model_id = self._text(model_id, "model_id", _MAX_MODEL_ID)
        caps = self._capabilities(capabilities)
        key = self._api_key(api_key)
        if model_id == MODEL:
            raise RegistryError("the Spark model uses its runtime credential configuration")
        # Only chat providers have a client; segmentation and transcription are the fixed Meta tools.
        if kind != "chat":
            raise RegistryError("registered models must be chat providers")
        url = self._base_url(base_url)
        prices = self._pricing(pricing)
        context = self._context_limit(context_limit)
        rate = self._rate(requests_per_minute) or None
        own_limits = self._limits(limits) or {}
        if type(accepts_images) is not bool or type(supports_tools) is not bool:
            raise RegistryError("invalid model flags")
        record_id = uuid.uuid4().hex
        secret_ref = self._write_secret(key) if key is not None else None
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO models (id,name,model_id,capabilities,builtin,secret_ref,"
                    "kind,base_url,accepts_images,supports_tools,pricing,context_limit,requests_per_minute,limits) "
                    "VALUES (?,?,?,?,0,?,?,?,?,?,?,?,?,?)",
                    (record_id, name, model_id, json.dumps(caps), secret_ref,
                     kind, url, int(accepts_images), int(supports_tools), json.dumps(prices), context,
                     rate, json.dumps(own_limits)),
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
        base_url: str | None = None,
        accepts_images: bool | None = None,
        supports_tools: bool | None = None,
        pricing: dict[str, Any] | None = None,
        context_limit: int | None = None,
        requests_per_minute: int | None = None,
        limits: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if name is not None:
            name = self._text(name, "name", _MAX_NAME)
        caps = None if capabilities is None else self._capabilities(capabilities)
        url = self._base_url(base_url)
        prices = None if pricing is None else self._pricing(pricing)
        context = self._context_limit(context_limit)
        rate = self._rate(requests_per_minute)
        own_limits = self._limits(limits)
        if any(flag is not None and type(flag) is not bool for flag in (accepts_images, supports_tools)):
            raise RegistryError("invalid model flags")
        provider_change = any(v is not None for v in (base_url, accepts_images, supports_tools, pricing, context, rate, own_limits))
        key = self._api_key(api_key)
        new_ref = self._write_secret(key) if key is not None else None
        old_ref: str | None = None
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM models WHERE id=?", (record_id,)).fetchone()
                if row is None:
                    raise RegistryError("unknown model id")
                if row["builtin"] and (row["model_id"] != MODEL or provider_change):
                    raise RegistryError("catalog models are immutable")
                old_ref = row["secret_ref"]
                next_name = name if name is not None else row["name"]
                next_caps = json.dumps(caps) if caps is not None else row["capabilities"]
                next_ref = new_ref if key is not None else old_ref
                db.execute(
                    "UPDATE models SET name=?, capabilities=?, secret_ref=?, base_url=?, "
                    "accepts_images=?, supports_tools=?, pricing=?, context_limit=?, "
                    "requests_per_minute=?, limits=? WHERE id=?",
                    (next_name, next_caps, next_ref, url if url is not None else row["base_url"],
                     int(accepts_images) if accepts_images is not None else row["accepts_images"],
                     int(supports_tools) if supports_tools is not None else row["supports_tools"],
                     json.dumps(prices) if prices is not None else row["pricing"],
                     context if context is not None else row["context_limit"],
                     row["requests_per_minute"] if rate is None else rate or None,
                     json.dumps(own_limits) if own_limits is not None else row["limits"], record_id),
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
            row = db.execute("SELECT model_id, secret_ref, builtin FROM models WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise RegistryError("unknown model id")
        if row["secret_ref"]:
            return self._read_secret(row["secret_ref"])
        if row["builtin"]:
            # Spark, SAM and Transcribe share the Meta runtime configuration (D-008).
            try:
                from .live import _runtime_key

                return _runtime_key()
            except Exception:
                raise RegistryError("runtime credential unavailable") from None
        raise RegistryError("no API key is registered for this model")

    def profile(self, record_id: str) -> ProviderProfile:
        """Where and how to call an entry; never includes the credential."""
        if record_id in _PROFILES:
            return _PROFILES[record_id]
        with self._connect() as db:
            row = db.execute("SELECT * FROM models WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise RegistryError("unknown model id")
        if not row["base_url"]:
            raise RegistryError("this model has no endpoint; add its base URL first")
        prices = json.loads(row["pricing"])
        per_token = lambda field: Decimal(prices.get(field, "0")) / 1_000_000
        try:
            return ProviderProfile(
                kind=row["kind"], endpoint=chat_endpoint(row["base_url"]), model=row["model_id"],
                input_price=per_token("input_per_million"), cached_price=per_token("input_per_million"),
                output_price=per_token("output_per_million"), token_param="max_tokens",
                **({"context_limit": row["context_limit"]} if row["context_limit"] else {}),
                accepts_images=bool(row["accepts_images"]), supports_tools=bool(row["supports_tools"]))
        except ValueError as exc:
            raise RegistryError(str(exc)) from None

    def _read_secret(self, reference: str) -> str:
        try:
            if reference.startswith(_KEYCHAIN):
                value = self.keychain.get(reference[len(_KEYCHAIN):]) if self.keychain is not None else None
                if value is None:
                    raise RegistryError("registered credential unavailable")
                return value
            return (self.secrets_dir / reference).read_text(encoding="utf-8")
        except RegistryError:
            raise
        except Exception:
            raise RegistryError("registered credential unavailable") from None

    def _write_secret(self, key: str | None) -> str | None:
        if key is None:
            return None
        reference = uuid.uuid4().hex
        if self._keychain_ready():
            try:
                self.keychain.set(reference, key)
            except Exception:
                raise RegistryError("API key could not be stored") from None
            return _KEYCHAIN + reference
        if self.keychain is not None and os.name == "nt":
            raise RegistryError("the Windows Credential Manager is unavailable")
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
            if reference.startswith(_KEYCHAIN):
                if self.keychain is not None:
                    self.keychain.delete(reference[len(_KEYCHAIN):])
                return
            (self.secrets_dir / reference).unlink(missing_ok=True)
        except Exception:
            # The opaque reference remains inaccessible through the registry.
            pass
