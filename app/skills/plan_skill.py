# -*- coding: utf-8 -*-
"""计划生成：调用 runtime.pipeline 编排链。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult
from app.runtime.pipeline import build_plan


class PlanSkill(Skill):
    name = "plan"
    description = "一日训练+饮食计划（筛查→FITT→宏观→动作→食材）"
    task_types = ("plan",)

    REQUIRED = ("weight_kg", "height_cm", "age")

    def execute(self, ctx, params) -> SkillResult:
        profile = dict(ctx.profile or {})
        missing = [k for k in self.REQUIRED if k not in profile]
        if missing:
            return SkillResult(ok=False, data={}, provenance=[],
                               error=f"缺少计划必需档案字段: {', '.join(missing)}")
        profile.setdefault("goal", str(params.get("goal", "maintain")))
        profile.setdefault("activity", 1.55)
        out = build_plan(profile)
        if not out.get("ok", True):
            return SkillResult(ok=False, data={},
                               provenance=out.get("provenance", []),
                               error=out.get("error"))
        return SkillResult(ok=True, data=out["plan"],
                           provenance=out.get("provenance", []))