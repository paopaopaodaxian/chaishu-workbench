"""本地数据存储：SQLite + 原始文件副本。

对象与设计稿第 7 节一一对应：
Book / TextVersion / Chapter / Paragraph / TemplateVersion /
AnalysisJob / WorkUnit / AnalysisResult / Review / Comparison / ProviderConnection(settings)
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("NOVEL_DATA", ROOT / "data")).expanduser().resolve()
DB_PATH = DATA_DIR / "app.db"
ORIGINALS = DATA_DIR / "originals"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
  id INTEGER PRIMARY KEY, title TEXT, author TEXT DEFAULT '', grp TEXT DEFAULT '',
  file_name TEXT, file_hash TEXT, file_size INTEGER, encoding TEXT,
  current_version INTEGER, notes TEXT DEFAULT '', created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS text_versions(
  id INTEGER PRIMARY KEY, book_id INTEGER, vno INTEGER, status TEXT,     -- draft / confirmed
  split_rule TEXT, segmented INTEGER DEFAULT 0, n_chars INTEGER DEFAULT 0,
  n_chapters INTEGER DEFAULT 0, parent INTEGER, created REAL, confirmed REAL
);
CREATE TABLE IF NOT EXISTS chapters(
  id INTEGER PRIMARY KEY, version_id INTEGER, seq INTEGER, title TEXT,
  kind TEXT DEFAULT 'chapter',          -- chapter / segment(文本分段) / front(开篇简介) / extra(番外)
  excluded INTEGER DEFAULT 0, n_chars INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_ch_v ON chapters(version_id, seq);
CREATE TABLE IF NOT EXISTS paragraphs(
  id INTEGER PRIMARY KEY, version_id INTEGER, chapter_id INTEGER, pseq INTEGER,
  text TEXT, excluded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_p_ch ON paragraphs(chapter_id, pseq);
CREATE TABLE IF NOT EXISTS templates(
  id INTEGER PRIMARY KEY, name TEXT, builtin INTEGER DEFAULT 0, latest INTEGER,
  source_template INTEGER, created REAL, deleted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS template_versions(
  id INTEGER PRIMARY KEY, template_id INTEGER, version INTEGER, data TEXT, created REAL
);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY, kind TEXT,          -- analysis / comparison
  book_id INTEGER, version_id INTEGER, template_version_id INTEGER, comparison_id INTEGER,
  engine TEXT, model TEXT, scope TEXT, options TEXT, title TEXT,
  status TEXT, status_reason TEXT DEFAULT '', parent_job INTEGER,
  created REAL, queued_at REAL, started REAL, finished REAL, elapsed REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY, job_id INTEGER, seq INTEGER,
  kind TEXT,                                  -- chapter / segment / summary / comparison
  chapter_id INTEGER, part_idx INTEGER DEFAULT 0, part_total INTEGER DEFAULT 1,
  label TEXT, status TEXT DEFAULT 'pending',  -- pending/running/done/failed/uncertain/skipped
  attempts INTEGER DEFAULT 0, error TEXT DEFAULT '', error_kind TEXT DEFAULT '',
  started REAL, finished REAL, extra TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_u_job ON units(job_id, seq);
CREATE TABLE IF NOT EXISTS results(
  id INTEGER PRIMARY KEY, job_id INTEGER, unit_id INTEGER, kind TEXT,  -- part/chapter/segment/summary/comparison
  chapter_id INTEGER, part_idx INTEGER DEFAULT 0, data TEXT, raw TEXT, usage TEXT, created REAL
);
CREATE INDEX IF NOT EXISTS ix_r_job ON results(job_id, kind);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY, result_id INTEGER, item_key TEXT, status TEXT DEFAULT '待核对',
  note TEXT DEFAULT '', revision TEXT DEFAULT '', updated REAL, UNIQUE(result_id, item_key)
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, job_id INTEGER, ts REAL, level TEXT, msg TEXT
);
CREATE INDEX IF NOT EXISTS ix_e_job ON events(job_id, id);
CREATE TABLE IF NOT EXISTS comparisons(
  id INTEGER PRIMARY KEY, title TEXT, scope TEXT, items TEXT, flags TEXT,
  status TEXT DEFAULT 'draft', job_id INTEGER, created REAL, updated REAL
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""

_local = threading.local()
_init_lock = threading.Lock()


GEN = [0]   # 恢复备份后递增，各线程据此重新打开数据库


def bump_generation():
    GEN[0] += 1


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is not None and getattr(_local, "gen", 0) != GEN[0]:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
        c = None
    if c is None:
        _local.gen = GEN[0]
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=OFF")
        _local.conn = c
    return c


def close_thread_conn():
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


def init():
    with _init_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ORIGINALS.mkdir(parents=True, exist_ok=True)
        conn().executescript(SCHEMA)


def now() -> float:
    return time.time()


def q(sql: str, args=()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql: str, args=()) -> dict | None:
    r = conn().execute(sql, args).fetchone()
    return dict(r) if r else None


def x(sql: str, args=()) -> int:
    cur = conn().execute(sql, args)
    return cur.lastrowid


class tx:
    """with db.tx(): 多条写入作为一个事务。"""
    def __enter__(self):
        conn().execute("BEGIN IMMEDIATE")

    def __exit__(self, et, ev, tb):
        conn().execute("ROLLBACK" if et else "COMMIT")
        return False


def jload(s, default=None):
    if s is None or s == "":
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default


def jdump(o) -> str:
    return json.dumps(o, ensure_ascii=False)


def get_setting(key: str, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return jload(r["value"], default) if r else default


def set_setting(key: str, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, jdump(value)))


def log_event(job_id: int, msg: str, level: str = "info"):
    x("INSERT INTO events(job_id,ts,level,msg) VALUES(?,?,?,?)", (job_id, now(), level, msg))
