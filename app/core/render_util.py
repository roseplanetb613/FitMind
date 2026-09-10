# -*- coding: utf-8 -*-
"""渲染叶子工具：训练计划天级行单源（nodes._render_fallback 与
llm.StubProvider.render 共用——LLM 失败兜底与无 key/断网路径输出一致）。"""
from __future__ import annotations


def training_lines(d: dict) -> list[str]:
    """data → 计划天级行（日期锚点/休息日/封堵说明/progression 建议，
    含 blocked_note/fatigue_note 透传）。纯函数：无 I/O、无日志。"""
    lines: list[str] = []
    for day in (d.get("training") or {}).get("items") or []:
        head = f"{day.get('date', '')} {day.get('day', '')}".strip()
        if day.get("type") == "rest":
            note = day.get("note") or "好好恢复"
            lines.append(f"{head}：{note}" if head else f"· 休息日：{note}")
            if day.get("blocked_from"):
                lines.append("  （因身体筛查，原定训练改为休息）")
            continue
        if head:
            lines.append(head)
        for e in day.get("exercises") or []:
            seg = f"· {e.get('name')}"
            pr = e.get("progression") or {}
            if pr.get("weight_kg"):
                seg += f"（建议 {pr['weight_kg']}kg）"
            lines.append(seg)
    if d.get("blocked_note"):
        lines.append(str(d["blocked_note"]))
    if d.get("fatigue_note"):
        lines.append(str(d["fatigue_note"]))
    return lines
