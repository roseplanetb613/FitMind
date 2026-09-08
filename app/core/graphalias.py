# -*- coding: utf-8 -*-
"""图谱前置别名（W5）：检索/意图命中前先查 GraphStore.lookup_alias。
- 仅采纳 status=approved 映射（pending 不入主路径，避免污染检索）；
- 任何失败/图谱不可用 → None（静态 NAME_ALIASES 退居降级位，行为与现状一致）；
- 单点：qa/teach 共用，禁止各自实现图谱查询。
"""
from __future__ import annotations


def graph_alias_for(text: str, domain: str) -> str | None:
    """查图谱 approved 别名。命中返回标准说法，否则 None（外部按原词继续）。"""
    try:
        from app.graph.store import GraphStore
        g = GraphStore.get()
        if g is None:
            return None
        return g.lookup_alias(text, domain)
    except Exception:
        return None