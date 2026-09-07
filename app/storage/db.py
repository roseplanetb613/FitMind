# -*- coding: utf-8 -*-
"""私有用户记录（训练/饮食/意图缺口）——仅本地 SQLite，不入 git。"""
from __future__ import annotations
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "storage_output" / "user_log.db"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LogStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._tid = None
        self._conn = None
        self._ensure_thread()   # 惰性+跨线程：每次访问按当前线程重连

    def _ensure_thread(self):
        """TestClient 等会在线程池执行 sync 端点；SQLite 连接有线程亲和性，
        故按线程重连（幂等建表），避免跨线程共享单连接。"""
        tid = threading.get_ident()
        if self._conn is not None and tid == self._tid:
            return
        self._tid = tid
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS workout_set(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exercise TEXT NOT NULL, weight_kg REAL, reps INT, rir REAL,
                date TEXT)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS diet_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                food_id TEXT, food_name TEXT, grams REAL, date TEXT)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS intent_miss(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT, guessed TEXT, confidence REAL,
                created_at TEXT)
        """)
        self._conn.commit()

    def log_workout_set(self, exercise, weight_kg, reps, rir=0.0, date=""):
        self._ensure_thread()
        self._conn.execute(
            "INSERT INTO workout_set(exercise,weight_kg,reps,rir,date) "
            "VALUES(?,?,?,?,?)", (exercise, weight_kg, reps, rir, date))
        self._conn.commit()

    def history(self, exercise, top=3) -> list[dict]:
        self._ensure_thread()
        cur = self._conn.execute(
            "SELECT weight_kg,reps,rir,date FROM workout_set "
            "WHERE exercise=? ORDER BY id DESC LIMIT ?", (exercise, top))
        rows = cur.fetchall()
        return [{"weight_kg": r[0], "reps": r[1], "rir": r[2], "date": r[3]}
                for r in reversed(rows)]

    def log_diet(self, food_id, food_name, grams, date=""):
        self._ensure_thread()
        self._conn.execute(
            "INSERT INTO diet_log(food_id,food_name,grams,date) VALUES(?,?,?,?)",
            (food_id, food_name, grams, date))
        self._conn.commit()

    def diet(self, limit=20) -> list[dict]:
        self._ensure_thread()
        cur = self._conn.execute(
            "SELECT food_id,food_name,grams,date FROM diet_log "
            "ORDER BY id DESC LIMIT ?", (limit,))
        return [{"food_id": r[0], "food_name": r[1], "grams": r[2], "date": r[3]}
                for r in cur.fetchall()]

    def log_miss(self, text, guessed, confidence=0.0):
        """落库澄清/低置信输入（数据飞轮）：本地 SQLite，不入 git。"""
        self._ensure_thread()
        self._conn.execute(
            "INSERT INTO intent_miss(text,guessed,confidence,created_at) "
            "VALUES(?,?,?,?)",
            (str(text), str(guessed), float(confidence), _now()))
        self._conn.commit()

    def misses(self, limit=50) -> list[dict]:
        self._ensure_thread()
        cur = self._conn.execute(
            "SELECT id,text,guessed,confidence,created_at FROM intent_miss "
            "ORDER BY id DESC LIMIT ?", (limit,))
        return [{"id": r[0], "text": r[1], "guessed": r[2],
                 "confidence": r[3], "created_at": r[4]} for r in cur.fetchall()]

    def close(self):
        self._conn.close()