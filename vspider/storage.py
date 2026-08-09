"""SQLite 落库 + 断点续跑支持。

用标准库 sqlite3 而不是 sqlalchemy/aiosqlite，理由和 settings 一样：
少一个安装依赖，本地 venv 与服务器 conda base 都能零配置跑起来。
写入只在每个 run 结束时发生一次，读取都是小查询，同步调用足够，
用一把锁保证多协程/线程安全（check_same_thread=False）。

断点续跑：视频以 uid（platform:video_id）为主键去重。同一条视频若此前已
成功归纳，可通过 processed_uids() 查到并跳过，避免重复下载与推理。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vspider.registry import Paths

if TYPE_CHECKING:
    from vspider.pipeline.orchestrator import RunResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    mode          TEXT,
    platform      TEXT,
    profile       TEXT,
    scenario      TEXT,
    started_at    TEXT,
    elapsed_sec   REAL,
    success_rate  REAL,
    total         INTEGER,
    succeeded     INTEGER,
    created_at    TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS videos (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT,
    uid              TEXT,
    platform         TEXT,
    video_id         TEXT,
    title            TEXT,
    author_name      TEXT,
    url              TEXT,
    cover_url        TEXT,
    duration_sec     INTEGER,
    publish_time     TEXT,
    one_liner        TEXT,
    key_points       TEXT,
    topics           TEXT,
    sentiment        TEXT,
    is_promotion     INTEGER,
    confidence       REAL,
    ocr_chars        INTEGER,
    transcript_chars INTEGER,
    timings          TEXT,
    error            TEXT,
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_videos_run ON videos(run_id);
CREATE INDEX IF NOT EXISTS idx_videos_uid ON videos(uid);
"""


class Storage:
    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            db_path = Paths.from_env().data_root / "vspider.db"
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def save_run(self, run: "RunResult", meta: dict[str, Any]) -> None:
        run_id = meta.get("run_id", "")
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs"
                "(run_id, mode, platform, profile, scenario, started_at,"
                " elapsed_sec, success_rate, total, succeeded)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    meta.get("mode", ""),
                    meta.get("platform", ""),
                    meta.get("profile", ""),
                    run.scenario,
                    meta.get("started_at", ""),
                    round(run.elapsed_sec, 2),
                    round(run.success_rate, 3),
                    len(run.results),
                    len(run.succeeded),
                ),
            )
            for r in run.results:
                item = r.item
                s = r.summary
                self._conn.execute(
                    "INSERT INTO videos"
                    "(run_id, uid, platform, video_id, title, author_name, url,"
                    " cover_url, duration_sec, publish_time, one_liner, key_points,"
                    " topics, sentiment, is_promotion, confidence, ocr_chars,"
                    " transcript_chars, timings, error)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id,
                        item.uid,
                        item.platform.value,
                        item.video_id,
                        item.title,
                        item.author_name,
                        item.url,
                        item.cover_url,
                        item.duration_sec,
                        item.publish_time.isoformat() if item.publish_time else None,
                        s.one_liner if s else "",
                        json.dumps(s.key_points, ensure_ascii=False) if s else "[]",
                        json.dumps(s.topics, ensure_ascii=False) if s else "[]",
                        s.sentiment.value if s else "",
                        int(s.is_promotion) if s else 0,
                        s.confidence if s else 0.0,
                        len(r.ocr.merged_text()) if r.ocr else 0,
                        len(r.transcript.full_text) if r.transcript else 0,
                        json.dumps(
                            {k: round(v, 3) for k, v in r.stage_timings.items()},
                            ensure_ascii=False,
                        ),
                        r.error,
                    ),
                )
            self._conn.commit()

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                return None
            vids = self._conn.execute(
                "SELECT * FROM videos WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        out = dict(run)
        out["videos"] = [self._decode_video(v) for v in vids]
        return out

    def processed_uids(self) -> set[str]:
        """已成功归纳过的视频 uid 集合，供断点续跑跳过。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT uid FROM videos WHERE error = '' AND one_liner != ''"
            ).fetchall()
        return {r["uid"] for r in rows}

    def latest_summary(self, uid: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM videos WHERE uid = ? AND error = '' AND one_liner != ''"
                " ORDER BY id DESC LIMIT 1",
                (uid,),
            ).fetchone()
        return self._decode_video(row) if row else None

    @staticmethod
    def _decode_video(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for key in ("key_points", "topics", "timings"):
            try:
                d[key] = json.loads(d.get(key) or ("[]" if key != "timings" else "{}"))
            except (json.JSONDecodeError, TypeError):
                d[key] = [] if key != "timings" else {}
        d["is_promotion"] = bool(d.get("is_promotion"))
        return d

    def close(self) -> None:
        with self._lock:
            self._conn.close()
