"""Saved conversations (data/conversations.sqlite). They survive restarts; deleting one removes its messages."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, title TEXT NOT NULL, mode TEXT NOT NULL, think INTEGER,
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, reasoning TEXT, meta TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conv_id, id);
"""


class ConvStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.c = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA journal_mode=WAL")
        self.c.executescript(DDL)

    def create(self, title: str = "New conversation", mode: str = "yaob", think: bool | None = None) -> dict:
        cid = uuid.uuid4().hex[:16]
        t = time.time()
        with self.lock:
            self.c.execute("INSERT INTO conversations VALUES(?,?,?,?,?,?)", (cid, title[:200], mode, None if think is None else int(think), t, t))
        return self.get(cid)

    def list(self, limit: int = 100) -> list[dict]:
        with self.lock:
            rows = self.c.execute("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conv_id=c.id) AS n FROM conversations c ORDER BY updated DESC LIMIT ?",
                                  (limit,)).fetchall()
        return [self._conv(r) | {"messages": r["n"]} for r in rows]

    @staticmethod
    def _conv(r) -> dict:
        return {"id": r["id"], "title": r["title"], "mode": r["mode"], "think": None if r["think"] is None else bool(r["think"]),
                "created": r["created"], "updated": r["updated"]}

    def get(self, cid: str) -> dict | None:
        with self.lock:
            r = self.c.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
            if r is None:
                return None
            ms = self.c.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,)).fetchall()
        msgs = [{"id": m["id"], "role": m["role"], "content": m["content"], "reasoning": m["reasoning"], "meta": json.loads(m["meta"] or "null"),
                 "created": m["created"]} for m in ms]
        return self._conv(r) | {"messages": msgs}

    def add(self, cid: str, role: str, content: str, reasoning: str | None = None, meta: dict | None = None) -> int:
        t = time.time()
        with self.lock:
            cur = self.c.execute("INSERT INTO messages(conv_id, role, content, reasoning, meta, created) VALUES(?,?,?,?,?,?)",
                                 (cid, role, content, reasoning, json.dumps(meta, ensure_ascii=False) if meta else None, t))
            self.c.execute("UPDATE conversations SET updated=? WHERE id=?", (t, cid))
            n = self.c.execute("SELECT COUNT(*) FROM messages WHERE conv_id=?", (cid,)).fetchone()[0]
            if role == "user" and n == 1:
                self.c.execute("UPDATE conversations SET title=? WHERE id=? AND title='New conversation'", (content.strip().splitlines()[0][:80] or "Conversation", cid))
            return cur.lastrowid

    def update(self, cid: str, **kw) -> dict | None:
        allowed = {k: v for k, v in kw.items() if k in ("title", "mode", "think")}
        if allowed:
            if "think" in allowed and allowed["think"] is not None:
                allowed["think"] = int(bool(allowed["think"]))
            with self.lock:
                self.c.execute(f"UPDATE conversations SET {', '.join(k + '=?' for k in allowed)}, updated=? WHERE id=?", (*allowed.values(), time.time(), cid))
        return self.get(cid)

    def delete(self, cid: str) -> bool:
        with self.lock:
            self.c.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
            return self.c.execute("DELETE FROM conversations WHERE id=?", (cid,)).rowcount > 0
