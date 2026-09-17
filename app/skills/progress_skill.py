# -*- coding: utf-8 -*-
"""渐进指导：历史(训练记录) × progression.evaluate → 下组建议。

训练记录的**写入侧只写图谱**，故读取侧也读图谱（`app/runtime/history`）。
2026-09-17 之前读的是 SQLite `workout_set`，那张表没有生产写入方 →
本技能恒返回 `no history`（见 docs/SDD/user-data-domain.md §6-F1）。
"""
from __future__ import annotations
import progression
from app.skills.base import Skill, SkillResult


class ProgressSkill(Skill):
    name = "progress"
    description = "按历史记录给出下一组重量/次数/RIR 建议"
    task_types = ("progress", "fallback")

    def __init__(self, store=None):
        # store 仅用于测试注入（任何有 history() 的对象）；None → 执行时按 ctx 取图谱
        self._injected = store

    def _source(self, ctx):
        """训练记录来源。默认读**图谱** —— 写侧只写图谱，读空表必空。"""
        if self._injected is not None:
            return self._injected
        from app.runtime.history import WorkoutHistory
        return WorkoutHistory(user_id=getattr(
            getattr(ctx, "session", None), "user_id", None))

    def execute(self, ctx, params) -> SkillResult:
        exercise = str(params.get("exercise") or params.get("query", ""))
        tgt_reps = int(params.get("target_reps") or 8)
        tgt_rir = float(params.get("target_rir") or 2)
        rows = self._source(ctx).history(exercise, top=6)
        if not rows:
            return SkillResult(ok=True,
                               data={"status": "hold", "reason": "no history",
                                     "advice": "暂无训练记录：建议从轻重量开始，"
                                               "先确认动作模式"},
                               provenance=["progress#WorkoutHistory.history"])
        h = progression.ExerciseHistory(
            name=exercise,
            sets=[progression.SetRecord(r["weight_kg"], r["reps"], r["rir"],
                                        r["date"]) for r in rows])
        out = progression.evaluate(h, tgt_reps, tgt_rir)
        return SkillResult(ok=True, data=out,
                           provenance=["progress#progression.evaluate"])