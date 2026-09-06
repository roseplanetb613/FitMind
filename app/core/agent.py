# -*- coding: utf-8 -*-
"""Agent 宿主：guard 前置 → 分类 → 路由 → 执行 → 校验 → 渲染。"""
from __future__ import annotations
from dataclasses import dataclass, field
from app.core.executors import execute_skills, run_react, run_plan_exec
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
        # 2) 模式路由
        mode = route(intent.complexity)
        # 3) 执行（按模式）
        ctx = ExecutionContext(session=sess, profile=sess.profile,
                               mode=mode.value, skill_log=[])
        if mode is Mode.REACT:
            outcome = run_react(self.registry, self.llm, ctx,
                                intent.task_type, intent.params)
        elif mode in (Mode.PLAN_EXEC, Mode.REWOO):
            outcome = run_plan_exec(self.registry, self.llm, ctx,
                                    intent.task_type, intent.params)
        else:
            outcome = execute_skills(self.registry, intent.task_type, ctx,
                                     intent.params, mode)
        # 4) 规则校验兜底
        outcome = self.validator.check(outcome, intent)
        # 5) 渲染
        structured = {"title": intent.task_type, "items": [],
                      "sources": outcome["provenance"]}
        if outcome["ok"]:
            structured["data"] = outcome["data"]
        reply = self.llm.render(structured)
        sess.history.append({"role": "user", "text": message})
        sess.history.append({"role": "assistant", "text": reply})
        return ChatResponse(session_id=sess.id, reply=reply,
                            structured=structured, mode_used=mode.value,
                            provenance=outcome["provenance"], guard=gres)

    def _render_guard(self, gres) -> str:
        d = gres.data
        return f"风险提示：{d['level_label']}。{d['advice']}"