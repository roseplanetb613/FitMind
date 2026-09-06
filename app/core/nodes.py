# -*- coding: utf-8 -*-
"""LangGraph 节点（闭包工厂）：guard/classify/execute/plan/aggregate/think/act/
validate/render。全部复用现有领域层：skills、runtime.validator、router。"""
from __future__ import annotations
from app.core.router import Mode, RouteClassifier, route
from app.skills.base import ExecutionContext, SkillResult
from app.runtime.validator import RuleValidator

MAX_STEPS = 4
MAX_PLAN = 4


def build_nodes(registry, llm, classifier=None, validator=None) -> dict:
    classifier = classifier or RouteClassifier(llm)
    validator = validator or RuleValidator()

    def _ctx(state: dict, mode: str) -> ExecutionContext:
        return ExecutionContext(session=None, profile=state.get("profile") or {},
                                mode=mode, skill_log=[])

    # ---------------- guard（无条件前置） ----------------
    def guard(state: dict) -> dict:
        guard_skill = registry.get("guard")
        out = {"blocked": False, "guard_data": None}
        if guard_skill is None:
            return out
        r = guard_skill.execute(_ctx(state, "guard"),
                                {"signal": state.get("message", "")})
        if r.ok and r.data.get("blocked"):
            d = r.data
            out = {"blocked": True, "guard_data": d, "mode_used": "guard",
                   "reply": f"风险提示：{d.get('level_label', '')}。{d.get('advice', '')}",
                   "provenance": r.provenance}
        return out

    def guard_route(state: dict) -> str:
        return "blocked" if state.get("blocked") else "pass"

    # ---------------- classify（意图 + 模式，尊重调用方预置） ----------------
    def classify(state: dict) -> dict:
        if state.get("intent") and state.get("mode_used"):
            return {}
        intent = classifier.intent_for(state.get("message", ""),
                                       state.get("profile") or {})
        try:
            mode = route(intent.complexity, intent.task_type)
        except Exception:
            mode = Mode.DIRECT
        return {"intent": intent.__dict__, "mode_used": mode.value}

    def mode_route(state: dict) -> str:
        return state.get("mode_used", "direct")

    # ---------------- 技能执行 ----------------
    def _call_skill(state: dict, name: str, params: dict, mode: str):
        s = registry.get(name)
        if s is None:
            return SkillResult(ok=False, data={}, provenance=[], error=f"无 {name}")
        return s.execute(_ctx(state, mode), params)

    def execute(state: dict) -> dict:
        task_type = (state.get("intent") or {}).get("task_type", "fallback")
        params = (state.get("intent") or {}).get("params", {})
        cands = registry.by_task(task_type)
        if not cands:
            return {"outcome": {"ok": False, "data": {}, "provenance": [],
                                "error": f"无技能承接 {task_type}"}}
        r = cands[0].execute(_ctx(state, "direct"), params)
        return {"outcome": {"ok": r.ok, "data": r.data,
                            "provenance": r.provenance, "error": r.error},
                "provenance": r.provenance}

    # ---------------- plan（PlanExec/ReWOO 共用） ----------------
    def plan_node(state: dict) -> dict:
        task = (state.get("intent") or {}).get("task_type", "fallback")
        calls = llm.plan(task, registry.names)
        return {"plan_calls": [{"step_id": c.step_id, "skill": c.skill,
                                "params": c.params} for c in calls]}

    def plan_route(state: dict) -> str:
        return "parallel" if state.get("mode_used") == "rewoo" else "seq"

    def _make_execute_step(i: int):
        def step(state: dict) -> dict:
            calls = state.get("plan_calls") or []
            if i >= len(calls):
                return {}
            c = calls[i]
            r = _call_skill(state, c["skill"], c["params"], "rewoo")
            return {"observations": [{"step": c["step_id"], "skill": c["skill"],
                                      "ok": r.ok, "data": r.data,
                                      "error": r.error}]}
        return step

    # ---------------- aggregate（PlanExec/ReWOO 汇合 + ReAct 结算） ----------------
    def aggregate(state: dict) -> dict:
        obs = state.get("observations") or []
        steps = state.get("react_steps") or []
        if not obs and steps:
            act_steps = [s for s in steps
                         if s.get("skill") and s["skill"] != "_done"]
            ok = all(s.get("ok", False) for s in act_steps) if act_steps else False
            last = act_steps[-1].get("data", {}) if act_steps else {}
            prov = [f"react:{s['skill']}" for s in act_steps]
            return {"outcome": {"ok": ok, "data": last, "provenance": prov,
                                "_react_steps": steps}, "provenance": prov}
        if not obs:
            return {"outcome": {"ok": False, "data": {}, "provenance": [],
                                "error": "无执行观测"}}
        ok = all(o["ok"] for o in obs)
        last = obs[-1]["data"] if obs else {}
        prov = [f"{o['skill']}:{o['step']}" for o in obs]
        return {"outcome": {"ok": ok, "data": last, "provenance": prov,
                            "_observations": obs}, "provenance": prov}

    # ---------------- ReAct 循环 ----------------
    def think(state: dict) -> dict:
        steps = state.get("react_steps") or []
        call = llm.think(steps, registry.names)
        if call.skill == "_done":
            return {"react_done": True, "react_next": {}}
        return {"react_done": False,
                "react_next": {"skill": call.skill, "params": call.params}}

    def act(state: dict) -> dict:
        nxt = state.get("react_next") or {}
        done = state.get("react_done", False)
        if done or not nxt.get("skill"):
            return {"react_steps": [{"skill": "_done"}]}
        r = _call_skill(state, nxt["skill"], nxt["params"], "react")
        return {"react_steps": [{"skill": nxt["skill"], "params": nxt["params"],
                                 "ok": r.ok, "data": r.data, "error": r.error,
                                 "result": {"ok": r.ok, "data": r.data,
                                            "error": r.error}}]}

    def react_route(state: dict) -> str:
        done = state.get("react_done", False)
        steps = state.get("react_steps") or []
        if done or len(steps) >= MAX_STEPS:
            return "done"
        return "loop"

    # ---------------- validate + render ----------------
    def validate(state: dict) -> dict:
        from app.core.graph import state_to_intent
        outcome = dict(state.get("outcome") or {})
        intent = state_to_intent(state.get("intent") or {})
        return {"outcome": validator.check(outcome, intent)}

    def render(state: dict) -> dict:
        intent = state.get("intent") or {}
        outcome = state.get("outcome") or {}
        structured = {"title": intent.get("task_type", "fallback"), "items": [],
                      "sources": outcome.get("provenance", [])}
        if outcome.get("ok"):
            structured["data"] = outcome.get("data", {})
        try:
            reply = llm.render(structured)
        except Exception:
            reply = _render_fallback(structured)
        return {"reply": reply}

    nodes = {"guard": guard, "guard_route": guard_route,
             "classify": classify, "mode_route": mode_route,
             "execute": execute,
             "plan_node": plan_node, "plan_route": plan_route,
             "aggregate": aggregate,
             "think": think, "act": act, "react_route": react_route,
             "validate": validate, "render": render}
    for i in range(MAX_PLAN):
        nodes[f"execute_step{i}"] = _make_execute_step(i)
    return nodes


def _render_fallback(structured: dict) -> str:
    d = structured.get("data") or {}
    lines = [str(structured.get("title", "回答")).replace("qa", "检索结果")]
    for it in d.get("items", []):
        nm = it.get("name") or it.get("name_zh")
        if nm:
            lines.append(f"· {nm}")
    if "macros" in d:
        m = d["macros"]
        lines.append(f"目标热量 {m.get('target_kcal')} kcal，"
                     f"蛋白 {m.get('protein_g')}g")
    if structured.get("sources"):
        lines.append("来源: " + ", ".join(structured["sources"][:3]))
    return "\n".join(lines) if lines else "（渲染服务暂不可用）"