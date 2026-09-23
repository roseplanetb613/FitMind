# -*- coding: utf-8 -*-
"""
screening.py — 运动前筛查 + 禁忌映射（消费层）
==================================================
  - PAR-Q+ 7 主问题门检：任一"是" → 引导附表/医生，不建议直接开计划
  - 红黄绿分级：red=暂停建议就医 / yellow=规避+替代 / green=正常
  - 禁忌映射：按已报疾病/损伤 → 危险动作模式黑名单 + 替代建议
数据见 data/training-science/data/{parq_plus,contraindications}.json
注意：本模块是“风险提示”不是医学诊断；免责与转诊提示由调用方负责展示。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

SCI_DATA = Path(__file__).resolve().parent.parent / "data" / "training-science" / "data"
_PARQ = json.load(open(SCI_DATA / "parq_plus.json", encoding="utf-8"))
_CI = json.load(open(SCI_DATA / "contraindications.json", encoding="utf-8"))
CONDITIONS = _CI["conditions"]
RISK_LEVELS = _CI["risk_levels"]

# 严重度序：red 最重（0）。**全仓唯一**的严重度序 —— 检索层与筛查层共用，
# 不要在别处再写一份（同源漂移，本仓架构铁律）。
#
# ⚠ 不要用字符串比较代替它：'yellow' > 'red' > 'green'（码点序），
# 所以 `ORDER BY risk_level DESC` 会把 red 排到**最后**。
# 2026-09-23 实测踩过（图检索 contraindications 首版即此写法）。
RISK_ORDER = {"red": 0, "yellow": 1, "green": 2}


def sort_by_risk(rows: list[dict]) -> list[dict]:
    """按严重度升序（red → yellow → green），同度按 condition、pattern 稳定排序。

    未知 risk_level 排在最后且**不抛异常**（全降级）。
    """
    return sorted(
        rows,
        key=lambda r: (RISK_ORDER.get(r.get("risk_level"), len(RISK_ORDER)),
                       r.get("condition") or "",
                       r.get("pattern") or ""))


def parq_questions() -> list[dict]:
    return _PARQ["questions"]


def parq_gate(answers: Iterable[bool]) -> dict:
    """answers: 7 题依序布尔（True=是）。返回 {pass_ok, level, advice}。"""
    lst = list(answers)
    if len(lst) != 7:
        raise ValueError("PAR-Q+ 主问卷需 7 题")
    if not any(lst):
        return {"pass_ok": True, "level": "green", "advice": _PARQ["rules"]["all_no"]}
    yes_ids = [f"q{i+1}" for i, v in enumerate(lst) if v]
    return {"pass_ok": False, "level": "yellow", "yes_questions": yes_ids,
            "advice": _PARQ["rules"]["any_yes"]}


def contraindication(condition_zh: str) -> dict | None:
    """按中文病名/损伤查禁忌条目。"""
    c = condition_zh.strip().lower()
    for entry in CONDITIONS:
        if c in entry["condition_zh"].lower() or c in entry["condition_en"].lower():
            return entry
    return None


def plan_check(conditions: Iterable[str], patterns: Iterable[str]) -> dict:
    """
    对已报疾病 + 计划动作模式做交叉检查，输出分级与替代。
    patterns: 计划中出现的动作模式（push/pull/squat/hinge/lunge/core/carry…）
    """
    blocks, warns = [], []
    level = "green"
    for cond in conditions:
        entry = contraindication(cond)
        if not entry:
            continue
        # 升级到更重的等级 —— 序取自 RISK_ORDER（唯一来源），不再 here 重编 if/elif。
        level = min(level, entry["risk_level"],
                    key=lambda v: RISK_ORDER.get(v, len(RISK_ORDER)))
        hit = [p for p in patterns if p in entry["danger_patterns"]]
        blocks.append({
            "condition": entry["condition_zh"], "risk_level": entry["risk_level"],
            "hit_patterns": hit, "safe_prescriptions": entry["safe_prescriptions"],
            "alternatives": entry["alternatives"], "source": entry["source"],
        })
        if hit:
            warns.append(f"{entry['condition_zh']}：规避 {hit}，替代建议见上")
    return {"level": level, "level_label": RISK_LEVELS.get(level, ""),
            "blocks": blocks, "warnings": warns}


if __name__ == "__main__":
    g = parq_gate([False] * 7)
    print("① 全否:", g["level"], "|", g["advice"])
    g2 = parq_gate([False, False, False, False, False, False, True])
    print("② 任一为是:", g2["level"], g2["yes_questions"], "|", g2["advice"][:20], "…")
    r = plan_check(["腰椎间盘突出", "老寒腿"], ["hinge", "squat", "core"])
    print("③ 禁忌交叉:")
    for b in r["blocks"]:
        print(f"   {b['risk_level'].upper()} {b['condition']} | 命中 {b['hit_patterns']} "
              f"| 替代 {b['alternatives']} | {b['source'][:12]}…")