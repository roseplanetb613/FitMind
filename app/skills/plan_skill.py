# -*- coding: utf-8 -*-
"""计划生成：调用 runtime.pipeline 编排链。
产出计划即登记记忆图谱 PlanVersion（采纳闭环前置——个人记忆 spec v0.3 §2）。"""
from __future__ import annotations
import hashlib
import json
import uuid
from app.skills.base import Skill, SkillResult
from app.runtime.pipeline import build_plan


def _register_plan(plan: dict, user_id: str) -> None:
    """计划产出 → 图谱 PlanVersion（静默：图谱不可用/失败不影响计划主链路）。"""
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return
        content_hash = hashlib.sha1(
            json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:16]
        m.register_plan(user_id, f"plan-{uuid.uuid4().hex[:8]}",
                        content_hash)
    except Exception:
        pass


# N-8 偏好消费：部位→pattern 映射（仅在 screening 放行空间内加权，绝不解禁）
_PART2PAT = {"胸": "push", "肩": "push", "背": "pull", "臂": "pull",
             "腿": "squat", "臀": "squat", "腹": "core", "核心": "core"}


def _load_prefs(ctx) -> dict | None:
    """读记忆偏好 → {'pats_like': set, 'dislike_ex': set}；无图/无偏好/异常 → None。"""
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return None
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        pats_like, dislike_ex = set(), set()
        for r in m.current(uid, "preference"):
            about = str(r.get("about") or "")
            val = str(r.get("value") or "")
            if about.startswith("部位:"):
                if val == "喜欢":
                    pat = _PART2PAT.get(about[3:])
                    if pat:
                        pats_like.add(pat)
            elif about:
                if val == "不喜欢":
                    dislike_ex.add(about)
        if not pats_like and not dislike_ex:
            return None
        return {"pats_like": pats_like, "dislike_ex": dislike_ex}
    except Exception:
        return None


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
        prefs = _load_prefs(ctx)
        out = build_plan(profile, prefs=prefs)
        if not out.get("ok", True):
            return SkillResult(ok=False, data={},
                               provenance=out.get("provenance", []),
                               error=out.get("error"))
        _register_plan(out["plan"],
                      getattr(getattr(ctx, "session", None), "user_id", "local"))  # F4
        prov = list(out.get("provenance", []))
        if prefs and out["plan"].get("preferences_applied"):
            prov.append("memory#preference")     # 偏好应用留痕
        return SkillResult(ok=True, data=out["plan"], provenance=prov)