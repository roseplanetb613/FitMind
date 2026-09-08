# -*- coding: utf-8 -*-
"""计划生成：调用 runtime.pipeline 编排链。
产出计划即登记记忆图谱 PlanVersion（采纳闭环前置——个人记忆 spec v0.3 §2）。"""
from __future__ import annotations
import hashlib
import json
import uuid
from app.skills.base import Skill, SkillResult
from app.runtime.pipeline import build_plan


def _register_plan(plan: dict) -> None:
    """计划产出 → 图谱 PlanVersion（静默：图谱不可用/失败不影响计划主链路）。"""
    try:
        from app.graph.memory import MEMORY_USER_ID, MemoryStore
        m = MemoryStore.get()
        if m is None:
            return
        content_hash = hashlib.sha1(
            json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:16]
        m.register_plan(MEMORY_USER_ID, f"plan-{uuid.uuid4().hex[:8]}",
                        content_hash)
    except Exception:
        pass


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
        _register_plan(out["plan"])          # 产出即登记 PlanVersion（记忆谱系）
        return SkillResult(ok=True, data=out["plan"],
                           provenance=out.get("provenance", []))