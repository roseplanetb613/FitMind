# -*- coding: utf-8 -*-
"""Agent 门面：LangGraph 编排 + Session 管理 + ChatResponse 组装。
领域层（skills/runtime/storage）框架中立；本模块仅为图的门面。"""
from __future__ import annotations
from dataclasses import dataclass, field
from app.core.graph import build_graph
from app.core.llm import LLMProvider
from app.core.registry import SkillRegistry
from app.core.router import RouteClassifier
from app.core.session import Session, SessionManager


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
                 classifier: RouteClassifier | None = None,
                 checkpointer=None):
        self.registry = registry
        self.llm = llm
        self.sessions = sessions or SessionManager()
        self.classifier = classifier or RouteClassifier(llm)
        self.graph = build_graph(registry=registry, llm=llm,
                                 classifier=self.classifier,
                                 checkpointer=checkpointer)

    def run(self, message: str, session_id: str | None = None) -> ChatResponse:
        sess = self.sessions.get(session_id) if session_id else None
        if sess is None:
            sess = self.sessions.create(session_id)
        thread = f"{sess.id}-{len(sess.history)}"   # 每轮独立 thread，避免跨轮 state 累计
        result = self.graph.invoke({
            "session_id": sess.id, "message": message,
            "profile": dict(sess.profile)},
            config={"configurable": {"thread_id": thread}})

        intent = result.get("intent") or {}
        outcome = result.get("outcome") or {}
        structured = {"title": intent.get("task_type", "fallback"),
                      "items": [], "sources": result.get("provenance", [])}
        if outcome.get("ok"):
            structured["data"] = outcome.get("data", {})
        reply = result.get("reply", "")
        sess.history.append({"role": "user", "text": message})
        sess.history.append({"role": "assistant", "text": reply})
        return ChatResponse(session_id=sess.id, reply=reply,
                            structured=structured,
                            mode_used=result.get("mode_used", "direct"),
                            provenance=result.get("provenance", []),
                            guard=result.get("guard_data"))