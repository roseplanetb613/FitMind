# -*- coding: utf-8 -*-
"""执行模式动态变换 + 意图三层编排（规则→语义→LLM→arbitrate 单点）。"""
from __future__ import annotations
import functools
import json
from enum import Enum
from pathlib import Path
from app.core.intent import Intent
from app.core.llm import Classification, LLMProvider, StubProvider
from app.core.arbitrate import arbitrate
from app.core.semantic import ExemplarStore, _params_for, map_conf


class Mode(Enum):
    DIRECT = "direct"
    REACT = "react"
    PLAN_EXEC = "plan_exec"
    REWOO = "rewoo"


CONFIG = Path(__file__).resolve().parent.parent / "config" / "router_config.json"

_MODE_MAP = {"direct": Mode.DIRECT, "react": Mode.REACT,
             "plan_exec": Mode.PLAN_EXEC, "rewoo": Mode.REWOO}


@functools.lru_cache(maxsize=1)
def load_router_config() -> dict:
    """路由配置（进程内缓存：此前 route() 每轮对话都 open+parse 一次）。"""
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def route(complexity: str, task_type: str = "") -> Mode:
    cfg = load_router_config()
    ov = cfg.get("overrides", {}).get(task_type)
    if ov:
        return _MODE_MAP[ov]
    return _MODE_MAP[cfg["default"].get(complexity, "direct")]


class RouteClassifier:
    """三层分类器：L0 规则(DIRECT，纯规则可复现) → L1 语义例句库 → L2 LLM
    → arbitrate 单点。语义层加载失败静默跳过；stub => 与现状一致。"""

    _SEM_DEFAULTS = {"enabled": True, "adopt_sim": 0.62,
                     "guard_adopt_sim": 0.55, "sim_floor": 0.40, "sim_ceil": 0.75}

    def __init__(self, llm: LLMProvider):
        self._llm = llm
        self._rules = StubProvider()              # L0 纯规则（与 classify 兜底同源）
        sem = dict(self._SEM_DEFAULTS)
        sem.update(load_router_config().get("semantic") or {})
        self._sem_on = bool(sem.get("enabled", True))
        self._adopt = float(sem.get("adopt_sim", 0.62))
        self._guard_adopt = float(sem.get("guard_adopt_sim", 0.55))
        # 线上紧急开关：semantic.enabled=false 时强制置 None（跳过 L1）
        self._store = ExemplarStore.get() if self._sem_on else None

    def intent_for(self, text: str, profile: dict | None = None) -> Intent:
        c = self._classify(text, profile)         # 三层决策
        # 规则复杂度信号：更复杂任务类型给更重模式
        complexity = {"plan": "complex", "progress": "medium",
                      "teach": "medium"}.get(c.task_type, "simple")
        if "请" in text and "计划" in text and "一周" in text:
            complexity = "batch"
        return Intent(task_type=c.task_type, params=c.params, raw_text=text,
                      complexity=complexity, confidence=c.confidence,
                      needs_clarify=c.needs_clarify)

    def _classify(self, text, profile) -> Classification:
        rule = self._rules.classify(text, profile)        # L0 纯规则
        if rule.confidence >= 0.7:                        # 强信号直接定（现状）
            return rule
        if self._store is not None:
            sem_tt, sim = self._store.match(text)          # L1 语义例句库
            thr = (self._guard_adopt if sem_tt == "guard"
                   else self._adopt)
            if sim >= thr:                                 # 语义命中 → 采纳
                return Classification(sem_tt, _params_for(sem_tt, text),
                                      confidence=map_conf(sim))
            if self._llm and hasattr(self._llm, "classify_llm"):
                try:
                    out = self._llm.classify_llm(text)
                    return arbitrate(out.task_type, out.confidence, out.params,
                                     rule)                 # L2 纯 LLM + 仲裁
                except Exception:
                    return rule                            # 幻觉/失败 → 规则兜底
        elif self._llm and hasattr(self._llm, "classify_llm"):
            try:
                out = self._llm.classify_llm(text)
                return arbitrate(out.task_type, out.confidence, out.params,
                                 rule)
            except Exception:
                return rule
        return rule                                        # stub 环境 = 现状