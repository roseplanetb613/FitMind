# -*- coding: utf-8 -*-
"""肌群恢复模型（纯函数，零状态、零随机——同输入必同输出）。

缺陷背景（2026-09-11）：per-muscle 链路此前只到"练过几次/最近哪天"
（`MemoryStore.muscle_summary`），**没有恢复度**；编排侧用的只有模式级 48h 硬阈值
（`plan_skill._load_linkage` 命中 push/pull/squat/core 即减量）。要做"肌肉状态可视化"
就必须补这一环。

模型：
    fatigue(muscle, now) = Σ_i  w_i · 0.5 ** ((now - t_i) / half_life(muscle))
    recovery             = clamp(1 - fatigue, 0, 1)
    w_i = role_weight(role) × min(1, sets*reps / ref_volume)

- 半衰期形式（而非线性）理由：衰减刚练完最快、尾部趋缓，符合主观体感；且**单调、
  可叠加、天然落在 [0,1]**（clamp 只兜叠加溢出）。
- 参数不写死在代码里：来自 `data/training-science/data/recovery_model.json`，
  便于按用户反馈校准（见该文件的 note：**初始值，待校准**）。

**边界（必须一并传达给用户）**：这是**编排参考**，不是生理测量。缺 sets/reps 时
退化为"按动作计次"并标 `confidence="low"`——不猜精确负荷。
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

DATA = (Path(__file__).resolve().parent.parent
        / "data" / "training-science" / "data" / "recovery_model.json")

# 数据包缺失/损坏时的兜底（与数据文件同形，宁可保守也不要炸）
_FALLBACK = {
    "role_weight": {"target": 1.0, "synergist": 0.5},
    "ref_volume": 12,
    "default_half_life_hours": 36,
    "half_life_hours": {},
}


@lru_cache(maxsize=1)
def load_params() -> dict:
    """读参数表（缓存只读）；缺失/损坏 → 内置兜底。"""
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
        if d.get("half_life_hours") is not None:
            return d
    except Exception:
        pass
    return dict(_FALLBACK)


def _half_life_hours(muscle: str, params: dict) -> float:
    return float((params.get("half_life_hours") or {}).get(
        muscle, params.get("default_half_life_hours", 36)))


def _volume_factor(load: dict, params: dict) -> float:
    """组次 → 归一负荷系数；缺 sets/reps → 1.0（退化为计次，不猜）。"""
    sets, reps = load.get("sets"), load.get("reps")
    ref = float(params.get("ref_volume") or 12) or 12.0
    try:
        v = float(sets) * float(reps)
    except (TypeError, ValueError):
        return 1.0
    if v <= 0:
        return 1.0
    return min(1.0, v / ref)


def _as_dt(t) -> datetime | None:
    """datetime / ISO 字符串 → 带时区的 datetime；不可解析 → None。"""
    if isinstance(t, datetime):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    if not t:
        return None
    try:
        d = datetime.fromisoformat(str(t))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _role_rank(role) -> int:
    return {"target": 2, "synergist": 1}.get(str(role or "target"), 1)


def _per_session(loads: list[dict]) -> list[dict]:
    """同一肌群**同一天**的多次出现 → 合并为一次训练（取最强角色 / 最大组次）。

    不合并会**重复计负荷**：一次训练里若有多个深蹲变体，臀会被叠加数倍——而生理上
    "今天练过臀"只有一次刺激，恢复曲线应按天计。实测样张里 glutes 因此低到 20%
    （实际应为两天前练过一次、恢复 50% 量级），故必须按训练日聚合。"""
    by_day: dict[str, dict] = {}
    for x in loads or []:
        at = _as_dt(x.get("at"))
        if at is None:
            continue
        day = at.date().isoformat()
        cur = by_day.get(day)
        if cur is None:
            by_day[day] = dict(x, at=at)
            continue
        if _role_rank(x.get("role")) > _role_rank(cur.get("role")):
            cur["role"] = x.get("role")
        try:
            cur_v = float(cur.get("sets") or 0) * float(cur.get("reps") or 0)
            new_v = float(x.get("sets") or 0) * float(x.get("reps") or 0)
        except (TypeError, ValueError):
            continue
        if new_v > cur_v:
            cur["sets"], cur["reps"] = x.get("sets"), x.get("reps")
    return list(by_day.values())


def _weight(load: dict, params: dict) -> float:
    role = str(load.get("role") or "target")
    rw = float((params.get("role_weight") or {}).get(role, 1.0))
    return rw * _volume_factor(load, params)


def recovery_of(muscle: str, loads: list[dict], now: datetime,
                params: dict | None = None) -> float:
    """单肌群恢复度 ∈ [0,1]。`loads` = [{"at": datetime|ISO, "role":..., "sets":.., "reps":..}]。

    **替换口（单点）**：恢复**形式**按 `params["model"]` 从 `_FORMS` 选派；数据文件当前
    声明 `"exponential-half-life"`。后续接入运动恢复模型时，只需在 `_FORMS` 注册一个
    新形式（或在数据文件把 model 换成新名），调用方全部无感——`recovery_map` / `describe`
    / qa 肌肉面板 / muscle_map 都只依赖本函数的**契约**（入参、返回 [0,1]），不依赖形式。
    模型名未知 → 回落当前形式（不炸，宁可给旧口径也不给空值）。

    契约：无有效记录 → 1.0；未来时间点被忽略（时钟异常不产生负疲劳）；
    **按训练日聚合**（见 `_per_session`）：同一天多次出现只算一次刺激。"""
    params = params or load_params()
    form = _FORMS.get(str(params.get("model") or _DEFAULT_MODEL),
                      _form_exponential)
    return form(muscle, loads, now, params)


def _form_exponential(muscle: str, loads: list[dict], now: datetime,
                      params: dict) -> float:
    """当前形式：指数半衰期。fatigue = Σ w·0.5^(Δt/半衰期)，recovery = 1-fatigue。

    半衰期形式（而非线性）理由：衰减刚练完最快、尾部趋缓，符合主观体感；且**单调、
    可叠加、天然落在 [0,1]**（clamp 只兜叠加溢出）。"""
    hl = _half_life_hours(muscle, params)
    if hl <= 0:
        return 1.0
    now = _as_dt(now) or datetime.now(timezone.utc)
    fatigue = 0.0
    for load in _per_session(loads):
        at = _as_dt(load.get("at"))
        if at is None:
            continue
        dt_h = (now - at).total_seconds() / 3600.0
        if dt_h < 0:                 # 未来时间点 → 忽略（时钟异常不产生负疲劳）
            continue
        fatigue += _weight(load, params) * (0.5 ** (dt_h / hl))
    return max(0.0, min(1.0, 1.0 - fatigue))


# 恢复**形式**注册表（替换口，见 recovery_of docstring）。
# 新增运动恢复模型 = 写一个同签名的 `_form_xxx` + 在此注册 + 数据文件 `model` 改为该名。
# 不预设更多抽象：等真的有了第二个形式再谈插件化（YAGNI）。
_DEFAULT_MODEL = "exponential-half-life"
_FORMS = {_DEFAULT_MODEL: _form_exponential}


def recovery_map(loads_by_muscle: dict, now: datetime,
                 params: dict | None = None) -> dict:
    """{muscle: loads} → {muscle: {recovery, confidence, last_trained, sessions}}。

    confidence：**任一次**训练缺 sets/reps → "low"（该肌群恢复度含"按次估算"成分）；
    全部有组次 → "high"。无记录 → recovery=1.0 但 `has_record=False`——
    **"没有记录"与"完全恢复"必须可区分**（否则 UI 会误导训练决策）。"""
    params = params or load_params()
    out: dict = {}
    for muscle, loads in (loads_by_muscle or {}).items():
        rows = [x for x in (loads or []) if _as_dt(x.get("at")) is not None]
        if not rows:
            continue
        low = any(x.get("sets") is None or x.get("reps") is None for x in rows)
        last = max(_as_dt(x.get("at")) for x in rows)
        out[muscle] = {
            "recovery": round(recovery_of(muscle, rows, now, params), 3),
            "confidence": "low" if low else "high",
            "last_trained": last.isoformat(),
            "sessions": len(rows),
            "has_record": True,
        }
    return out


def describe(muscle: str, state: dict | None) -> str:
    """状态 → 用户可读短语。**措辞不得越界为生理测量**（见模块 docstring 边界）。"""
    if not state or not state.get("has_record"):
        return "还没有记录"
    pct = int(round(float(state.get("recovery", 1.0)) * 100))
    conf = "" if state.get("confidence") == "high" else "（按次数估算）"
    return f"约 {pct}% 恢复{conf}"
