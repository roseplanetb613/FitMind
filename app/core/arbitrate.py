# -*- coding: utf-8 -*-
"""置信仲裁单点：llm 与 router 共用，防循环依赖。
规则强信号(L0)/语义命中(L1)/LLM(L2) 三类结果统一在此仲裁：
≥0.7 采纳 ｜ 0.4~0.7 clarify ｜ <0.4 规则兜底 ｜ unknown → clarify。"""
from __future__ import annotations
from app.core.llm import Classification, INTENT_WHITELIST


def arbitrate(task_type, confidence: float, params: dict,
              fallback: Classification) -> Classification:
    """置信仲裁（纯函数，独立单测）。unknown → 体面退路 clarify；
    白名单外 → fallback；三级置信决策。
    W2：plan 是重操作（生成完整计划/方案），拿不准就该问：0.5~0.7 即 clarify
    （其他意图 0.4~0.7）。"""
    # unknown：LLM 拿不准时给低置信+反问，消除"被迫硬选"的结构性幻觉
    if task_type == "unknown":
        return Classification("fallback", {}, confidence=0.3, needs_clarify=True)
    if task_type not in INTENT_WHITELIST:
        return fallback
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if conf >= 0.7:
        return Classification(task_type, params or {}, confidence=conf)
    clarify_floor = 0.5 if task_type == "plan" else 0.4   # plan 收窄
    if conf >= clarify_floor:
        # 不确定但不至于否定：不硬猜，标记澄清（图走 clarify_node 反问）
        base = fallback if fallback.task_type == task_type else Classification(
            task_type, params or {})
        base.complexity = "simple"
        base.confidence = conf
        base.needs_clarify = True
        return base
    return fallback