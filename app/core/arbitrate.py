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
    # 澄清带只留给**重操作**（plan）：生成整份计划猜错的代价远高于反问一句。
    # 只读/建议类（qa/teach/progress/smalltalk）此前同样落在 0.4~0.7 澄清带，
    # 实测把正常提问打死（qa 0.6、progress 0.62 全被反问成"我不确定你想做哪件事"）
    # ——而这类答错的代价很低（空检索会如实回"没找到"），反问才是死路。
    # guard 落在 0.4~0.7 也从"反问"变"采纳"，方向更安全。
    if task_type == "plan":
        if conf >= 0.5:
            base = (fallback if fallback.task_type == task_type
                    else Classification(task_type, params or {}))
            base.complexity = "simple"
            base.confidence = conf
            base.needs_clarify = True
            return base
        return fallback               # 重操作低置信 → 不采纳（原行为）
    if conf >= 0.4:
        return Classification(task_type, params or {}, confidence=conf)
    return fallback