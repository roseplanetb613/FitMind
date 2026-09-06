# -*- coding: utf-8 -*-
"""Agent 宿主：guard 前置 → 分类 → 路由 → 执行 → 校验 → 渲染。"""
from __future__ import annotations
from dataclasses import dataclass, field
from app.core.executors import execute_skills, run_react, run_plan_exec, run_rewoo
from app.core.llm import LLMProvider
from app.core.registry import SkillRegistry
from app.core.router import Mode, RouteClassifier, route
from app.core.session import Session, SessionManager
from app.skills.base import ExecutionContext
from app.runtime.validator import RuleValidator   # S3 创建，先占位


@dataclass
class ChatResponse:
    session_id: str
    reply: str
    structured: dict
    mode_used: str
    provenance: list[str]
    guard: dict | None = None


class Agent:
    def __init__(self, registry: SkillRegistry, llm: LLMProvider,
                 sessions: SessionManager | None = None,
                 classifier: RouteClassifier | None = None):
        self.registry = registry
        self.llm = llm
        self.sessions = sessions or SessionManager()
        self.classifier = classifier or RouteClassifier(llm)
        self.validator = RuleValidator()

    def run(self, message: str, session_id: str | None = None) -> ChatResponse:
        sess = self.sessions.get(session_id) if session_id else None
        if sess is None:
            sess = self.sessions.create(session_id)

        # 0) guard 无条件前置（规则拦截，不依赖 LLM 分类）
        guard = self.registry.get("guard")
        gres = None
        if guard is not None:
            gres = guard.execute(
                ExecutionContext(session=sess, profile=sess.profile,
                                 mode="guard", skill_log=[]),
                {"signal": message})
            if gres.ok and gres.data.get("blocked"):
                return ChatResponse(session_id=sess.id,
                                    reply=self._render_guard(gres),
                                    structured=gres.data, mode_used="guard",
                                    provenance=gres.provenance, guard=gres.data)

        # 1) 意图+复杂度
        intent = self.classifier.intent_for(message, sess.profile)
        # 2) 模式路由（含配置异常兜底）
        mode = self._route_with_fallback(intent)
        # 3) 执行（含失败降级）
        ctx = ExecutionContext(session=sess, profile=sess.profile,
                               mode=mode.value, skill_log=[])
        mode, outcome = self._execute_with_fallback(mode, intent, ctx)
        # 4) 规则校验兜底
        outcome = self.validator.check(outcome, intent)
        # 5) 渲染
        structured = {"title": intent.task_type, "items": [],
                      "sources": outcome["provenance"]}
        if outcome["ok"]:
            structured["data"] = outcome["data"]
        try:
            reply = self.llm.render(structured)
        except Exception:
            reply = self._render_fallback(structured)
        sess.history.append({"role": "user", "text": message})
        sess.history.append({"role": "assistant", "text": reply})
        return ChatResponse(session_id=sess.id, reply=reply,
                            structured=structured, mode_used=mode.value,
                            provenance=outcome["provenance"], guard=gres)

    def _render_fallback(self, structured: dict) -> str:
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

    def _route_with_fallback(self, intent: Intent) -> Mode:
        try:
            return route(intent.complexity, intent.task_type)
        except Exception:
            return Mode.DIRECT   # 配置异常 → 最安全模式

    def _execute_with_fallback(self, mode: Mode, intent: Intent,
                               ctx: ExecutionContext) -> tuple[Mode, dict]:
        """执行；PlanExec/ReWOO/ReAct 失败 → 降级 DIRECT 规则直答。"""
        if mode is Mode.DIRECT:
            return mode, execute_skills(self.registry, intent.task_type, ctx,
                                        intent.params, mode)
        try:
            if mode is Mode.REACT:
                out = run_react(self.registry, self.llm, ctx, intent.task_type,
                                intent.params)
            elif mode is Mode.REWOO:
                out = run_rewoo(self.registry, self.llm, ctx, intent.task_type,
                                intent.params)
            else:
                out = run_plan_exec(self.registry, self.llm, ctx,
                                    intent.task_type, intent.params)
        except Exception:
            out = {"ok": False, "data": {}, "provenance": [],
                   "error": "executor_failed"}
        if not out.get("ok") and out.get("error"):
            ctx.skill_log.append({"skill": "_degrade", "params": {},
                                  "result": {"from": mode.value}})
            d = execute_skills(self.registry, intent.task_type, ctx,
                               intent.params, Mode.DIRECT)
            d["_degraded_from"] = mode.value
            return Mode.DIRECT, d
        return mode, out

    def _render_guard(self, gres) -> str:
        d = gres.data
        return f"风险提示：{d['level_label']}。{d['advice']}"