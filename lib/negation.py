# -*- coding: utf-8 -*-
"""否定判断原语（单源）。

背景（2026-09-11 收口）：同一个"否定"概念此前在三处各写一份词表——

    app/graph/memory_extract.py  _EVENT_NEG_KW   动词前窗口（"不练"/"没练"）
    app/graph/memory_extract.py  _SPLIT_NEG_KW   整句否决
    lib/split_cycle.py           _NEG_MARK       整句否决

且三份内容互不相同（事件表多 取消/免/拒绝）。语义确实不同（窗口 vs 整句），但
**判定原语相同**——词表归一、语义交给调用方。`_event` 与 `_preference` 各出过一次
否定缺陷（"今天不练三头"被记成打卡、"我不喜欢练腿"记成喜欢），根因都是缺统一原语。

有意纠正：原 `_EVENT_NEG_KW` 含裸"免"，会命中"**免**费""避**免**"这类非否定词；
中文的否定用法是"以免/免得/免遭"，不是裸"免"。故本表**不含"免"**。
"""
from __future__ import annotations

# 基本否定（"不练"/"没练"/"别练"/"未练"）
NEG_CORE = ("不", "没", "别", "未")
# 厌恶表达（"讨厌练腿"——负向偏好，非严格否定，但消费方都需要拦）
NEG_DISLIKE = ("讨厌",)
# 指令性取消（"取消核心日"/"拒绝拉伸"）
NEG_CANCEL = ("取消", "拒绝")

NEG_WORDS = NEG_CORE + NEG_DISLIKE + NEG_CANCEL

# 动词前短窗口默认宽度（"我今天不想练腿"→ 窗口须覆盖"不想"）
DEFAULT_WIN = 2


def has_negation(text: str, words: tuple = NEG_WORDS) -> bool:
    """整句/整段是否含否定标记（用于"整句否决"语义）。"""
    return any(k in (text or "") for k in words)


def negated_at(text: str, idx: int, win: int = DEFAULT_WIN,
               words: tuple = NEG_WORDS) -> bool:
    """`idx` **之前**的 win 字窗口内是否有否定标记（"动词前窗口"语义）。

    注意 idx=0（段首）时窗口为空 → 恒 False；段首要判否定请用 `negated_prefix`。"""
    t = text or ""
    return any(k in t[max(0, idx - win):idx] for k in words)


def negated_prefix(text: str, win: int = DEFAULT_WIN,
                   words: tuple = NEG_WORDS) -> bool:
    """**开头** win 字内是否有否定标记（"段级否决"语义："练了腿，没练胸"）。"""
    return has_negation((text or "")[:win], words)
