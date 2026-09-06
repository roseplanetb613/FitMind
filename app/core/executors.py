# -*- coding: utf-8 -*-
"""执行器：同一技能、不同跑法。Direct 直调；ReAct 循环；PlanExec 规划后执行。"""
from __future__ import annotations
from app.core.intent import Intent
from app.core.llm import LLMProvider
from app.core.registry import SkillRegistry
from app.core.router import Mode
from app.skills.base import ExecutionContext, SkillResult

MAX_STEPS = 4


def execute_skills(registry: SkillRegistry, task_type: str, ctx: ExecutionContext,
                   params: dict, mode: Mode) -> dict:
    cands = registry.by_task(task_type)
    if not cands:
        return {"ok": False, "data": {}, "provenance": [],
                "error": f"无技能承接 {task_type}"}
    s = cands[0]
    r = s.execute(ctx, params)
    ctx.skill_log.append({"skill": s.name, "params": params, "result": r.data})
    return {"ok": r.ok, "data": r.data, "provenance": r.provenance,
            "error": r.error}


def _call(reg: SkillRegistry, name: str, ctx: ExecutionContext,
          params: dict) -> SkillResult:
    s = reg.get(name)
    if s is None:
        return SkillResult(ok=False, data={}, provenance=[], error=f"无 {name}")
    r = s.execute(ctx, params)
    ctx.skill_log.append({"skill": name, "params": params, "result": r.data})
    return r


def run_react(reg: SkillRegistry, llm: LLMProvider, ctx: ExecutionContext,
              task_type: str, params: dict) -> dict:
    steps: list[dict] = [{"task_type": task_type, "params": params}]
    for _ in range(MAX_STEPS):
        call = llm.think(steps, reg.names)
        if call.skill == "_done":
            break
        r = _call(reg, call.skill, ctx, call.params)
        steps.append({"skill": call.skill, "params": call.params,
                      "ok": r.ok, "data": r.data, "error": r.error,
                      "result": {"ok": r.ok, "data": r.data,
                                 "error": r.error}})
    last_ok = steps[-1].get("ok", False) if len(steps) > 1 else False
    data = steps[-1].get("data", {}) if len(steps) > 1 else {}
    prov = [f"react:{s['skill']}" for s in steps if "skill" in s]
    return {"ok": last_ok, "data": data, "provenance": prov,
            "_steps": steps}


def run_plan_exec(reg: SkillRegistry, llm: LLMProvider, ctx: ExecutionContext,
                  task_type: str, params: dict) -> dict:
    plan = llm.plan(task_type, reg.names)
    results, prov = [], []
    for call in plan:
        r = _call(reg, call.skill, ctx, call.params)
        results.append({"step": call.step_id, "skill": call.skill,
                        "ok": r.ok, "data": r.data, "error": r.error})
        prov.extend(r.provenance or [])
    ok = all(x["ok"] for x in results)
    data = results[-1]["data"] if results else {}
    return {"ok": ok, "data": data, "provenance": prov, "_steps": results}