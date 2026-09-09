# -*- coding: utf-8 -*-
"""记忆规则抽取器（spec v0.3 §4 明确句式，USER_MEMORY_TEST_PLAN W2 EX 用例）。

只抽**结构化动作**与**明确句式**；问句/情绪/假设/意向 一律不抽，
归一失败即放弃（宁缺毋滥——记忆错误进入 guard 是安全回归）。
- preference："我喜欢/讨厌/不想练 X" → preference + ABOUT(exercise 归一命中)
- checkin 补录："昨天我练了腿" → Event(occurred=昨天, recorded=今天)
injury 新建/失效/续期由 guard_skill 承接（EX-01/02，见 test_memory_guard）。
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone

_LIKE = ("喜欢", "偏爱", "沉迷", "上瘾")
_DISLIKE = ("讨厌", "不喜欢", "不想", "排斥")

# 训练动作时态词 →（occurred 偏移天数，名称）
_EVENT_HINT = (("上周三", 9), ("上周四", 8), ("上周五", 7), ("上周六", 6),
               ("上周日", 5), ("上周一", 4), ("上周二", 3),
               ("昨天", 1), ("前天", 2), ("上个月", 30), ("上周", 7),
               ("今天", 0))
_EVENT_VERB = ("练了", "练", "做了", "跑了", "练过")

_QUESTION = ("怎么", "吗", "?", "？", "啥", "为什么", "能不能", "可以吗",
             "行不行", "应该", "要不要")

# 体重陈述句（E2E-01："我体重 82" → profile 同型替换；"斤" → 公斤）
_WEIGHT_RE = re.compile(r"体重\s*(\d+(?:\.\d+)?)\s*(公斤|千克|kg|KG|斤)?")

# 档案陈述句扩展（2026-09-08 个性化）：数值区间+锚点护栏，宁缺毋滥
_WEIGHT_STMT_RE = re.compile(
    r"(?:我(?:现在)?(?:只有|是)?|现在|改成|改为)\s*(\d+(?:\.\d+)?)"
    r"\s*(公斤|千克|斤|kg|KG)")
_WEIGHT_EXCLUDE = ("每公斤", "每千克", "每kg", "每KG")   # 营养剂量语境绝不抽
# 全文粒度：句含剂量语境（“每公斤摄入…”）即整句不抽，宁缺毋滥
_AGE_RE = re.compile(r"(?:我今年|今年|我)\s*(\d{1,3})\s*岁")
_HEIGHT_ANCHOR_RE = re.compile(r"身高\s*(\d{2,3}(?:\.\d+)?)\s*(?:cm|厘米)?")
_HEIGHT_UNIT_RE = re.compile(r"我\s*(\d{2,3}(?:\.\d+)?)\s*(?:cm|厘米)")
_W_RANGE = (30.0, 300.0)
_A_RANGE = (5, 120)
_H_RANGE = (100.0, 250.0)

# 数据删除权（E2E-01/IS-02："忘掉我的数据" → forget 全清）
_FORGET_RE = re.compile(r"(忘掉|删除|清除|抹掉|清空).*(数据|记忆|档案|记录)")

# 情绪/假设/意向（不抽）
_SKIP = ("emo", "心情", "低落", "焦虑", "想试试", "想练", "打算", "计划",
         "想开始", "犹豫")


def _is_question(text: str) -> bool:
    return any(k in text for k in _QUESTION)


def _norm_exercise(text: str):
    """动作回归一：search_zh 命中返回名称；无命中 None（宁缺毋滥）。"""
    try:
        from app.runtime.repos import exercise_repo
        hits = exercise_repo().search_zh(text, limit=1)
        if hits:
            return hits[0].get("name_zh")
    except Exception:
        pass
    return None


def _weight(text: str) -> dict | None:
    """'我体重82/我只有60kg/现在80斤' → profile.weight_kg（区间+剂量语境护栏）。"""
    if any(k in text for k in _WEIGHT_EXCLUDE):
        return None
    m = _WEIGHT_RE.search(text)
    raw, unit = (m.group(1), m.group(2)) if m else (None, None)
    if raw is None:
        m2 = _WEIGHT_STMT_RE.search(text)          # 无"体重"锚点时单位必须显式
        if m2 is None:
            return None
        raw, unit = m2.group(1), m2.group(2)
    try:
        v = float(raw)
        if unit == "斤":
            v /= 2.0
        if not (_W_RANGE[0] <= v <= _W_RANGE[1]):
            return None
        return {"op": "profile", "key": "weight_kg", "value": v}
    except (TypeError, ValueError):
        return None


def _age(text: str) -> dict | None:
    """'我18岁/今年18岁' → profile.age（区间 5-120；'我3岁'不抽）。"""
    m = _AGE_RE.search(text)
    if m is None:
        return None
    try:
        v = int(m.group(1))
    except (TypeError, ValueError):
        return None
    if not (_A_RANGE[0] <= v <= _A_RANGE[1]):
        return None
    return {"op": "profile", "key": "age", "value": v}


def _height(text: str) -> dict | None:
    """'我身高165/我165cm' → profile.height_cm（'我165'无锚点不抽）。"""
    m = _HEIGHT_ANCHOR_RE.search(text) or _HEIGHT_UNIT_RE.search(text)
    if m is None:
        return None
    try:
        v = float(m.group(1))
    except (TypeError, ValueError):
        return None
    if not (_H_RANGE[0] <= v <= _H_RANGE[1]):
        return None
    return {"op": "profile", "key": "height_cm", "value": v}


def _preference(text: str) -> dict | None:
    """'我喜欢/讨厌 X' → 动作归一命中才抽取。"""
    for kw in _LIKE + _DISLIKE:
        idx = text.find(kw)
        if idx < 0:
            continue
        tail = text[idx + len(kw):].strip(" ，。的、和跟练")
        if not tail:
            return None
        name = _norm_exercise(tail)
        if not name:
            return None
        return {"op": "preference", "value": "喜欢" if kw in _LIKE else "不喜欢",
                "about": name}
    return None


def _event(text: str) -> dict | None:
    """'昨天我练了腿' → Event 补录（occurred<recorded）。"""
    verb_hit = next((v for v in _EVENT_VERB if v in text), None)
    if verb_hit is None:
        return None
    time_hit = next(((n, d) for n, d in _EVENT_HINT if n in text), None)
    if time_hit is None:
        return None
    # 归一动作（否则宁缺毋滥）
    seg = text.split(verb_hit)[-1] if verb_hit in text else text
    name = _norm_exercise(seg.strip(" ，。的"))
    today = datetime.now(timezone.utc).date()
    occurred = (today - timedelta(days=time_hit[1])).isoformat()
    return {"op": "checkin", "occurred": occurred,
            "about": name, "verb": verb_hit}


def extract(text: str) -> list[dict]:
    """主入口：返回抽取指令列表（空=不抽）。问句/情绪/假设 一律跳过。"""
    if not text or _is_question(text):
        return []
    if any(k in text for k in _SKIP):
        return []
    out: list[dict] = []
    if _FORGET_RE.search(text):
        out.append({"op": "forget_all"})
        return out                           # 删除权最高优先，不再抽取其它
    for fn in (_weight, _age, _height):
        if cmd := fn(text):
            out.append(cmd)
    if p := _preference(text):
        out.append(p)
    if e := _event(text):
        out.append(e)
    return out


def apply_memory_extract(text: str, user_id: str) -> int:
    """把抽取指令写入 MemoryStore（静默；无图谱/失败 → 0，不影响主链路）。"""
    n = 0
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return 0
        for cmd in extract(text):
            if cmd["op"] == "preference":
                if m.upsert_state(user_id, "preference", cmd["value"],
                                  about=cmd["about"]) is not None:
                    n += 1
            elif cmd["op"] == "checkin":
                if m.log_event(user_id, "checkin",
                               {"about": cmd["about"] or "",
                                "verb": cmd["verb"]},
                               occurred_at=f"{cmd['occurred']}T00:00:00+00:00"):
                    n += 1
            elif cmd["op"] == "profile":
                if m.upsert_state(
                        user_id, f"profile.{cmd['key']}",
                        __import__("json").dumps(cmd["value"])) is not None:
                    n += 1
            elif cmd["op"] == "forget_all":
                m.forget(user_id)            # 删除权最高（E2E-01/IS-02）
                n += 1
    except Exception:
        return n
    return n
