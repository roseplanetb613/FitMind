# -*- coding: utf-8 -*-
"""执行模式动态变换：感知层(LLM) + 规则信号 → 模式。"""
from __future__ import annotations
import json
from enum import Enum
from pathlib import Path
from app.core.intent import Intent
from app.core.llm import LLMProvider


class Mode(Enum):
    DIRECT = "direct"
    REACT = "react"
    PLAN_EXEC = "plan_exec"
    REWOO = "rewoo"


CONFIG = Path(__file__).resolve().parent.parent / "config" / "router_config.json"

_MODE_MAP = {"direct": Mode.DIRECT, "react": Mode.REACT,
             "plan_exec": Mode.PLAN_EXEC, "rewoo": Mode.REWOO}


def load_router_config() -> dict:
    return json.load(open(CONFIG, encoding="utf-8"))


def route(complexity: str, task_type: str = "") -> Mode:
    cfg = load_router_config()
    ov = cfg.get("overrides", {}).get(task_type)
    if ov:
        return _MODE_MAP[ov]
    return _MODE_MAP[cfg["default"].get(complexity, "direct")]


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