# -*- coding: utf-8 -*-
"""LLM 抽象层：供应商可插拔；S1–S5 用 StubProvider（确定可复现）。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from app.core.intent import Intent, PlannedCall


@dataclass
class Classification:
    task_type: str
    params: dict
    complexity: str = "simple"


# 规则意图词典（stub 分类用；真实 provider 接管后可删）
_RULES = [
    (("计划", "练什么", "怎么安排", "一周"), "plan",
     lambda t, kw: {"days": 1}),
    (("怎么做", "要领", "动作教学", "怎么练"), "teach",
     lambda t, kw: {"query": t}),
    (("下一组", "加重量", "减重量", "加几公斤", "减载"), "progress",
     lambda t, kw: {"query": t}),
    (("腰", "膝", "伤", "痛", "禁忌", "能不能练"), "guard",
     lambda t, kw: {"signal": t}),
]


class LLMProvider(ABC):
    is_stub: bool = False

    @abstractmethod
    def classify(self, text: str, profile: dict | None = None) -> Classification:
        """意图+复杂度分类（低成本）。"""

    @abstractmethod
    def plan(self, task: str, available: list[str]) -> list[PlannedCall]:
        """PlanExec/ReWOO：一次产出全部技能调用。"""

    @abstractmethod
    def think(self, context: list[dict], available: list[str]) -> PlannedCall:
        """ReAct：基于上下文决定下一步技能调用。"""

    @abstractmethod
    def render(self, structured: dict, tone: str = "coach") -> str:
        """把结构化结果渲染成自然语言。"""


class StubProvider(LLMProvider):
    """规则实现：分类/计划/渲染全确定，供测试与无 LLM 环境。"""
    is_stub: bool = True

    def __init__(self, lang: str = "zh"):
        self.lang = lang
        self.calls: list[str] = []

    def classify(self, text, profile=None) -> Classification:
        self.calls.append("classify")
        for kws, tt, build in reversed(_RULES):
            if any(k in text for k in kws):
                return Classification(tt, build(text, kws))
        return Classification("qa", {"query": text})   # 默认问答

    def plan(self, task, available) -> list[PlannedCall]:
        self.calls.append("plan")
        # task 为 task_type（"plan"）或含计划关键词 → 调 plan 技能
        if "plan" in available and (task == "plan" or any(
                k in task for k in ("计划", "训练", "减脂", "维持"))):
            return [PlannedCall("1", "plan", {"goal": task})]
        return [PlannedCall("1", "qa", {"query": task})]

    def think(self, context, available) -> PlannedCall:
        self.calls.append("think")
        last = context[-1] if context else {}
        if "result" in last and last["result"].get("ok"):
            return PlannedCall("end", "_done", {})   # 结束标记
        first = context[0] if context else {}
        tt = first.get("task_type")
        p0 = first.get("params") or {}
        if tt in ("plan", "teach", "progress"):
            return PlannedCall("1", tt, p0)
        return PlannedCall("1", "qa", {"query": p0.get("query", "追问")})

    def render(self, structured, tone="coach") -> str:
        self.calls.append("render")
        parts = [str(structured.get("title", "回答"))]
        for it in structured.get("items", []):
            parts.append(f"· {it}")
        for s in structured.get("sources", []):
            parts.append(f"来源: {s}")
        return "\n".join(parts)