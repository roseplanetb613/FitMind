# -*- coding: utf-8 -*-
"""私有用户记录（训练/饮食）——仅本地 SQLite，不入 git。"""
from __future__ import annotations
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "storage_output" / "user_log.db"


class LogStore:
    def __init__(self, path: Path | str = DEFAULT_DB):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
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
        self._conn.commit()

    def log_workout_set(self, exercise, weight_kg, reps, rir=0.0, date=""):
        self._conn.execute(
            "INSERT INTO workout_set(exercise,weight_kg,reps,rir,date) "
            "VALUES(?,?,?,?,?)", (exercise, weight_kg, reps, rir, date))
        self._conn.commit()

    def history(self, exercise, top=3) -> list[dict]:
        cur = self._conn.execute(
            "SELECT weight_kg,reps,rir,date FROM workout_set "
            "WHERE exercise=? ORDER BY id DESC LIMIT ?", (exercise, top))
        rows = cur.fetchall()
        return [{"weight_kg": r[0], "reps": r[1], "rir": r[2], "date": r[3]}
                for r in reversed(rows)]

    def log_diet(self, food_id, food_name, grams, date=""):
        self._conn.execute(
            "INSERT INTO diet_log(food_id,food_name,grams,date) VALUES(?,?,?,?)",
            (food_id, food_name, grams, date))
        self._conn.commit()

    def diet(self, limit=20) -> list[dict]:
        cur = self._conn.execute(
            "SELECT food_id,food_name,grams,date FROM diet_log "
            "ORDER BY id DESC LIMIT ?", (limit,))
        return [{"food_id": r[0], "food_name": r[1], "grams": r[2], "date": r[3]}
                for r in cur.fetchall()]

    def close(self):
        self._conn.close()