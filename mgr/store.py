"""SQLite persistence for tasks, memories, events, questions and devices."""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
create table if not exists tasks (
  id integer primary key autoincrement,
  cwd text not null,
  title text not null,
  status text not null,
  rounds integer not null default 0,
  max_rounds integer not null default 5,
  created real not null,
  updated real not null,
  last_result text
);
create table if not exists memories (
  id integer primary key autoincrement,
  scope text not null,
  project text,
  kind text not null,
  key text,
  content text not null,
  created real not null,
  valid_from real not null,
  valid_to real,
  superseded_by integer,
  source text
);
create table if not exists events (
  id integer primary key autoincrement,
  ts real not null,
  task_id integer,
  kind text not null,
  payload text not null
);
create table if not exists questions (
  id integer primary key autoincrement,
  batch text not null,
  text text not null,
  options text not null,
  answer text,
  created real not null,
  answered real
);
"""


def home() -> Path:
    """State directory. Override with MGR_HOME (used by tests and the demo)."""
    return Path(os.environ.get("MGR_HOME") or (Path.home() / ".mgr"))


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else home() / "mgr.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def q(self, sql, args=()):
        with self.lock:
            rows = [dict(r) for r in self.db.execute(sql, args).fetchall()]
            self.db.commit()
            return rows

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    def event(self, kind, payload, task_id=None):
        return self.x(
            "insert into events(ts, task_id, kind, payload) values(?,?,?,?)",
            (time.time(), task_id, kind, json.dumps(payload, default=str)),
        )

    def close(self):
        with self.lock:
            self.db.close()
