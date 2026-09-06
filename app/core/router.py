# -*- coding: utf-8 -*-
"""执行模式动态变换：感知层(LLM) + 规则信号 → 模式。"""
from __future__ import annotations
from enum import Enum
from app.core.intent import Intent
from app.core.llm import LLMProvider


class Mode(Enum):
    DIRECT = "direct"
    REACT = "react"
    PLAN_EXEC = "plan_exec"
    REWOO = "rewoo"


def route(complexity: str) -> Mode:
    """复杂度→模式映射（S5 改为 router_config.json 可配置）。"""
    return {"simple": Mode.DIRECT,
            "medium": Mode.REACT,
            "complex": Mode.PLAN_EXEC,
            "batch": Mode.REWOO}.get(complexity, Mode.DIRECT)


class RouteClassifier:
    """LLM.classify + 规则信号 → Intent（含复杂度）。"""

    def __init__(self, llm: LLMProvider):
        self._llm = llm

    def intent_for(self, text: str, profile: dict | None = None) -> Intent:
        c = self._llm.classify(text, profile)
        # 规则复杂度信号：更复杂任务类型给更重模式
        complexity = {"plan": "complex", "progress": "medium",
                      "teach": "medium"}.get(c.task_type, "simple")
        if "请" in text and "计划" in text and "一周" in text:
            complexity = "batch"
        return Intent(task_type=c.task_type, params=c.params, raw_text=text,
                      complexity=complexity)