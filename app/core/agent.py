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

    # ------------------------------------------------------------ 建档
    def update_profile(self, session_id: str, profile: dict) -> dict:
        """合并更新会话档案（未传字段保留）；非法字段抛 ValueError（details 列表）。"""
        errors = validate_profile(profile)
        if errors:
            raise ValueError("; ".join(errors))
        sess = self.sessions.get(session_id) or self.sessions.create(session_id)
        sess.profile.update(profile)
        return dict(sess.profile)

    def get_profile(self, session_id: str) -> dict | None:
        sess = self.sessions.get(session_id)
        return dict(sess.profile) if sess else None


def validate_profile(profile: dict) -> list[str]:
    """建档字段校验（系统入口边界）：返回错误列表，空=合法。"""
    errors: list[str] = []
    rules = {
        "sex": lambda v: v in ("male", "female"),
        "age": lambda v: isinstance(v, int) and 1 <= v <= 120,
        "height_cm": lambda v: isinstance(v, (int, float)) and 50 <= v <= 250,
        "weight_kg": lambda v: isinstance(v, (int, float)) and 5 <= v <= 400,
        "activity": lambda v: isinstance(v, (int, float)) and 1.0 <= v <= 3.0,
        "goal": lambda v: v in ("lose_fat", "maintain", "build_muscle"),
        "deficit_kcal": lambda v: isinstance(v, (int, float)) and 0 <= v <= 2000,
        "protein_g_per_kg": lambda v: isinstance(v, (int, float)) and 0 <= v <= 4,
        "fat_g_per_kg": lambda v: isinstance(v, (int, float)) and 0 <= v <= 3,
        "conditions": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
        "patterns": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
    }
    for key, ok in rules.items():
        if key in profile and not ok(profile[key]):
            errors.append(f"{key} 取值非法: {profile[key]!r}")
    return errors