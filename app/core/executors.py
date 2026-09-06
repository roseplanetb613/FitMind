# -*- coding: utf-8 -*-
"""执行器：同一技能、不同跑法。S1 仅 Direct；S3 加 ReAct/PlanExec；S5 加 ReWOO。"""
from __future__ import annotations
from app.core.intent import Intent
from app.core.llm import LLMProvider
from app.core.registry import SkillRegistry
from app.core.router import Mode
from app.skills.base import ExecutionContext, SkillResult


def execute_skills(registry: SkillRegistry, task_type: str, ctx: ExecutionContext,
                   params: dict, mode: Mode) -> dict:
    """通用执行：找承接该意图的技能并执行（同名技能取首个）。"""
    cands = registry.by_task(task_type)
    if not cands:
        return {"ok": False, "data": {}, "provenance": [],
                "error": f"无技能承接 {task_type}"}
    s = cands[0]
    r = s.execute(ctx, params)
    ctx.skill_log.append({"skill": s.name, "params": params, "result": r.data})
    return {"ok": r.ok, "data": r.data, "provenance": r.provenance,
            "error": r.error}