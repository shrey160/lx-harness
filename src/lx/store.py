"""Versioned project/job records; credentials live in the registry only."""
import json
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (kind TEXT,id TEXT,body TEXT,PRIMARY KEY(kind,id))")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self, kind):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM documents WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def get(self, kind, identifier):
        with self.connect() as db:
            row = db.execute("SELECT body FROM documents WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        if row is None:
            raise ValueError("Record not found")
        return json.loads(row[0])

    def save(self, kind, record):
        record = dict(record)
        record.setdefault("id", uuid.uuid4().hex)
        record["updated_at"] = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO documents VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body",
                       (kind, record["id"], json.dumps(record, allow_nan=False)))
        return record

