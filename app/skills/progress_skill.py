# -*- coding: utf-8 -*-
"""渐进指导：历史(私有存储) × progression.evaluate → 下组建议。"""
from __future__ import annotations
import progression
from app.skills.base import Skill, SkillResult
from app.storage.db import LogStore


class ProgressSkill(Skill):
    name = "progress"
    description = "按历史记录给出下一组重量/次数/RIR 建议"
    task_types = ("progress", "fallback")

    def __init__(self, store: LogStore | None = None):
        self.store = store or LogStore()

    def execute(self, ctx, params) -> SkillResult:
        exercise = str(params.get("exercise") or params.get("query", ""))
        tgt_reps = int(params.get("target_reps") or 8)
        tgt_rir = float(params.get("target_rir") or 2)
        rows = self.store.history(exercise, top=6)
        if not rows:
            return SkillResult(ok=True,
                               data={"status": "hold", "reason": "no history",
                                     "advice": "暂无训练记录：建议从轻重量开始，"
                                               "先确认动作模式"},
                               provenance=["progress#LogStore.history"])
        h = progression.ExerciseHistory(
            name=exercise,
            sets=[progression.SetRecord(r["weight_kg"], r["reps"], r["rir"],
                                        r["date"]) for r in rows])
        out = progression.evaluate(h, tgt_reps, tgt_rir)
        return SkillResult(ok=True, data=out,
                           provenance=["progress#progression.evaluate"])