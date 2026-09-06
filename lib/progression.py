# -*- coding: utf-8 -*-
"""
progression.py — 渐进超负荷规则引擎（消费层）
================================================
输入：单个动作的历史组记录（重量/次数/RIR），输出：下一训练建议（重量/次数）
规则（共识化、可审计）：
  1. e1RM 估测：Epley 公式 + RIR 修正（有效次数 = 实际次数 + RIR）
  2. 达标判定：目标 RIR 达成（上一组 RIR ≤ 目标 RIR + 0.5）→ 上调
  3. 上调步长：小重量 2.5kg / 大重量 5kg（或按载荷 2.5-5%）
  4. 连续两次未达标 → 减载提示；连续 3 周未达标 → Deload 周建议
纯函数、无 IO；数据包表文件见 data/training-science/data/rpe_rir.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SCI_DATA = Path(__file__).resolve().parent.parent / "data" / "training-science" / "data"
_RPE = json.load(open(SCI_DATA / "rpe_rir.json", encoding="utf-8"))
_OFFSET_PCT = _RPE["rpe_reps_below_max_offset"]          # %1RM 每 1 RIR 下调幅度

# 上调步长（kg 或 %1RM）
STEP_KG = 2.5          # ≤100kg 载荷
STEP_KG_HEAVY = 5.0    # >150kg 载荷（粗粒度）
STEP_PCT = 0.05        # 载荷百分比步长备选


@dataclass
class SetRecord:
    weight_kg: float
    reps: int
    rir: float = 0.0          # 距力竭余量（0=力竭 / 2=余 2 次）
    date: str = ""            # YYYY-MM-DD（可空）


@dataclass
class ExerciseHistory:
    name: str
    sets: list[SetRecord] = field(default_factory=list)


def e1rm(weight_kg: float, reps: int, rir: float = 0.0) -> float:
    """Epley 估测 1RM（RIR 修正：把"余量次数"计入有效次数）。"""
    eff = max(reps + rir, 1)
    return weight_kg * (1 + eff / 30)


def step_for(weight_kg: float) -> float:
    """按当前载荷选择上调步长。"""
    if weight_kg >= 150:
        return STEP_KG_HEAVY
    return STEP_KG


def _recent_sets(h: ExerciseHistory, top: int = 3) -> list[SetRecord]:
    return h.sets[-top:]


def evaluate(h: ExerciseHistory, target_reps: int, target_rir: float) -> dict:
    """
    评估最近 3 组表现并给出下一组建议。
    返回 {status, hit_count, advice:{...}, detail}
    status: up / hold / deload
    """
    recent = _recent_sets(h)
    if not recent:
        return {"status": "hold", "hit_count": 0,
                "advice": {"weight_kg": None, "reps": target_reps,
                           "rir": target_rir, "reason": "no history"},
                "detail": {"e1rm": None}}

    est = [e1rm(s.weight_kg, s.reps, s.rir) for s in recent]
    last = recent[-1]
    hit = int(last.reps >= target_reps and last.rir <= target_rir + 0.5)
    # 连续 3 组都在目标内才算"达标周"
    streaks = sum(1 for s in recent if s.reps >= target_reps and s.rir <= target_rir + 0.5)

    if last.reps < target_reps - 2 and streaks == 0:
        return {"status": "deload", "hit_count": hit,
                "advice": {"weight_kg": round(last.weight_kg * 0.9, 1),
                           "reps": max(target_reps - 2, 3),
                           "rir": target_rir + 2,
                           "reason": "连续未达标：减载 10%，降次加余量"},
                "detail": {"e1rm": round(max(est), 1)}}

    if streaks >= 2:
        w = last.weight_kg + step_for(last.weight_kg)
        return {"status": "up", "hit_count": hit,
                "advice": {"weight_kg": round(w, 1), "reps": target_reps,
                           "rir": target_rir,
                           "reason": f"连续达标：上调 {step_for(last.weight_kg)}kg"},
                "detail": {"e1rm": round(max(est), 1)}}

    return {"status": "hold", "hit_count": hit,
            "advice": {"weight_kg": round(last.weight_kg, 1), "reps": target_reps,
                       "rir": target_rir, "reason": "维持当前重量，确保技术达标后再加"},
            "detail": {"e1rm": round(max(est), 1)}}


def expected_pct1rm(reps: int, target_rir: float) -> float:
    """按锚点+线性外推粗估目标强度（%1RM）。"""
    anchors = {a["reps"]: a["pct_1rm"] for a in _RPE["anchors"]}
    if reps in anchors:
        base = anchors[reps]
    else:
        keys = sorted(anchors)
        lo = max(k for k in keys if k <= reps) if reps >= min(keys) else min(keys)
        hi = min(k for k in keys if k >= reps) if reps <= max(keys) else max(keys)
        if lo == hi:
            base = anchors[lo]
        else:
            blend = (reps - lo) / (hi - lo)
            base = anchors[lo] * (1 - blend) + anchors[hi] * blend
    return max(base - target_rir * _OFFSET_PCT, 40.0)


if __name__ == "__main__":
    # 演示：连续达标 → 上调；一次失败 → hold；连续失败 → deload
    hist = ExerciseHistory("杠铃深蹲", [
        SetRecord(100, 8, 2), SetRecord(100, 8, 1), SetRecord(100, 8, 1),
    ])
    print("① 连续达标:", evaluate(hist, 8, 1)["advice"])
    hist2 = ExerciseHistory("杠铃深蹲", [
        SetRecord(105, 8, 2), SetRecord(105, 6, 1), SetRecord(105, 5, 0),
    ])
    print("② 连续未达标:", evaluate(hist2, 8, 1)["status"],
          evaluate(hist2, 8, 1)["advice"])
    print("③ 目标强度参考(8×@RIR1):", expected_pct1rm(8, 1), "%1RM")