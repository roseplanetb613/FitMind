# -*- coding: utf-8 -*-
"""意图与计划调用的数据模型（跨层契约，禁加业务逻辑）。"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Intent:
    task_type: str                    # guard/qa/teach/plan/progress/fallback
    params: dict
    raw_text: str
    complexity: str = "simple"        # simple/medium/complex/batch
    confidence: float = 1.0


@dataclass
class PlannedCall:
    step_id: str
    skill: str
    params: dict
    depends_on: list[str] = field(default_factory=list)