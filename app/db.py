"""SQLite 持久化：目录版本与规划方案。"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    version_id INTEGER NOT NULL REFERENCES catalog_versions(id),
    code TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    credits INTEGER NOT NULL,
    seasons TEXT NOT NULL,                   -- 逗号分隔：SPRING,FALL
    prereq_groups TEXT NOT NULL DEFAULT '[]', -- JSON: [[code, ...], ...]
    coreqs TEXT NOT NULL DEFAULT '[]',        -- JSON: [code, ...]
    PRIMARY KEY (version_id, code)
);
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES catalog_versions(id),
    request_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',  -- proposed / confirmed / draft
    created_at TEXT NOT NULL,
    source_plan_id TEXT REFERENCES plans(id), -- 滚动重排草案关联的原方案；非草案为 NULL
    replan_json TEXT                          -- 草案的重排上下文与差异；非草案为 NULL
);
"""

# 既有数据库增量升级（SQLite 不支持 CREATE TABLE IF NOT EXISTS 加列）
MIGRATIONS = (
    "ALTER TABLE plans ADD COLUMN source_plan_id TEXT REFERENCES plans(id)",
    "ALTER TABLE plans ADD COLUMN replan_json TEXT",
)


def db_path():
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def connect():
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    """建表；首次启动时写入种子目录（版本 1）；对既有库执行增量升级。"""
    directory = os.path.dirname(os.path.abspath(db_path()))
    os.makedirs(directory, exist_ok=True)
    with session() as conn:
        conn.executescript(SCHEMA)
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(plans)").fetchall()}
        for stmt in MIGRATIONS:
            column = stmt.split("ADD COLUMN")[1].split()[0]
            if column not in existing:
                conn.execute(stmt)
        n = conn.execute("SELECT COUNT(*) AS n FROM catalog_versions").fetchone()["n"]
        if n == 0:
            from .seed_data import SEED_COURSES
            create_catalog_version(conn, "初始目录", SEED_COURSES)


def create_catalog_version(conn, note, courses):
    """全量提交一套课程，形成新的目录版本，返回版本号。"""
    cur = conn.execute(
        "INSERT INTO catalog_versions (note, created_at) VALUES (?, ?)", (note, _now()))
    vid = cur.lastrowid
    for c in courses:
        conn.execute(
            "INSERT INTO courses (version_id, code, name, credits, seasons, prereq_groups, coreqs)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                vid, c["code"], c.get("name", ""), c["credits"],
                ",".join(c["semesters"]),
                json.dumps(c.get("prereq_groups", []), ensure_ascii=False),
                json.dumps(c.get("coreqs", []), ensure_ascii=False),
            ),
        )
    return vid


def get_current_version(conn):
    return conn.execute("SELECT * FROM catalog_versions ORDER BY id DESC LIMIT 1").fetchone()


def get_version(conn, version_id):
    return conn.execute("SELECT * FROM catalog_versions WHERE id=?", (version_id,)).fetchone()


def load_courses(conn, version_id):
    rows = conn.execute(
        "SELECT * FROM courses WHERE version_id=? ORDER BY code", (version_id,)).fetchall()
    return [
        {
            "code": r["code"],
            "name": r["name"],
            "credits": r["credits"],
            "semesters": r["seasons"].split(","),
            "prereq_groups": json.loads(r["prereq_groups"]),
            "coreqs": json.loads(r["coreqs"]),
        }
        for r in rows
    ]


def insert_plan(conn, plan_id, version_id, request, result):
    conn.execute(
        "INSERT INTO plans (id, version_id, request_json, result_json, status, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (plan_id, version_id, json.dumps(request, ensure_ascii=False),
         json.dumps(result, ensure_ascii=False), "proposed", _now()),
    )


def insert_draft(conn, plan_id, version_id, request, result, source_plan_id, replan):
    """滚动重排草案：关联原方案并保存重排上下文与差异，状态为 draft。"""
    conn.execute(
        "INSERT INTO plans (id, version_id, request_json, result_json, status, created_at,"
        " source_plan_id, replan_json) VALUES (?,?,?,?,?,?,?,?)",
        (plan_id, version_id, json.dumps(request, ensure_ascii=False),
         json.dumps(result, ensure_ascii=False), "draft", _now(),
         source_plan_id, json.dumps(replan, ensure_ascii=False)),
    )


def get_plan(conn, plan_id):
    return conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()


def list_plans(conn):
    return conn.execute("SELECT * FROM plans ORDER BY created_at DESC, id DESC").fetchall()


def set_plan_status(conn, plan_id, status):
    conn.execute("UPDATE plans SET status=? WHERE id=?", (status, plan_id))
