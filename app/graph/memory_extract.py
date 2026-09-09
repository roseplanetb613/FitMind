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
import json
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

# W2 个性化（2026-09-09）：名字/性别/目标/饮食偏好陈述句（spec §3.1）
_NAME_RE = re.compile(
    r"(?:我是|我叫|叫我)\s*([一-龥]{2,4}|[A-Za-z][A-Za-z0-9_]{1,19})")
_NAME_STOP = frozenset({
    "谁", "谁啊", "谁呀", "男生", "女生", "男的", "女的", "男性", "女性",
    "男人", "女人", "学生", "新手", "小白", "老师", "教练", "好人", "坏人"})
_NAME_BAD_PREFIX = "说不很想真也都还就要会能应在把被让跟问听看吃练跑睡爱恨"
_SEX_RE = re.compile(
    r"^我是\s*(男生|女生|男人|女人|男孩|女孩|男的|女的|男性|女性)")
_SEX_MAP = {"男生": "male", "男人": "male", "男孩": "male", "男的": "male",
            "男性": "male", "女生": "female", "女人": "female",
            "女孩": "female", "女的": "female", "女性": "female"}
_GOAL_RE = re.compile(r"(?:我想|我要|目标是|目标)\s*(增肌|减脂|减肥|维持|保持)")
_GOAL_MAP = {"增肌": "build_muscle", "减脂": "lose_fat", "减肥": "lose_fat",
             "维持": "maintain", "保持": "maintain"}
_DIET_PREF_KW = ("清淡", "偏淡", "不吃辣", "少油", "少盐", "低油", "低盐",
                 "不吃甜", "戒糖", "无糖", "素食", "吃素", "不吃肉",
                 "吃辣", "重口", "偏咸", "低脂")


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


def _name(text: str) -> dict | None:
    """'我是星海/叫我Harry' → profile.name；停用词+坏前缀护栏（'我是谁'不抽'谁'）。"""
    m = _NAME_RE.search(text)
    if m is None:
        return None
    nm = m.group(1)
    if nm in _NAME_STOP or nm[0] in _NAME_BAD_PREFIX:
        return None
    return {"op": "profile", "key": "name", "value": nm}


def _sex(text: str) -> dict | None:
    """'我是男的/我是女生' → profile.sex（^我是 句首锚点，防'我男朋友…'）。"""
    m = _SEX_RE.match(text.strip())
    if m is None:
        return None
    return {"op": "profile", "key": "sex", "value": _SEX_MAP[m.group(1)]}


def _goal(text: str) -> dict | None:
    """'我想增肌/目标是维持' → profile.goal；'不想'否定与'增肌粉'名词护栏。"""
    if "不想" in text:
        return None
    m = _GOAL_RE.search(text)
    if m is None:
        return None
    if m.end() < len(text) and text[m.end()] in "粉剂":
        return None                     # "增肌粉/增肌剂"是补剂名词非目标
    return {"op": "profile", "key": "goal", "value": _GOAL_MAP[m.group(1)]}


def _diet_preference(text: str) -> dict | None:
    """'我喜欢清淡/不吃辣' → preference(about=None)，多命中 '、' 合并（同槽替换）。"""
    # 否定/厌恶语境整句否决（"我不想吃辣/我讨厌吃辣"不抽正向偏好，宁缺毋滥）
    if any(k in text for k in ("不想", "讨厌", "不喜欢", "别给我", "不要")):
        return None
    hits = [k for k in _DIET_PREF_KW if k in text]
    if "不吃辣" in hits and "吃辣" in hits:
        hits.remove("吃辣")                     # 否定优先，防"不吃辣"抽成"吃辣"
    if not hits:
        return None
    return {"op": "preference_diet", "value": "、".join(hits)}


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
    for fn in (_weight, _age, _height, _name, _sex, _goal):
        if cmd := fn(text):
            out.append(cmd)
    if p := _preference(text):
        out.append(p)
    if dp := _diet_preference(text):
        out.append(dp)
    if e := _event(text):
        out.append(e)
    return out


# ---------- ack 确认话术（2026-09-09：有写入必有"已记下"，单源格式化） ----------
_ACK_PROFILE = {"weight_kg": ("体重", "kg"), "age": ("年龄", "岁"),
                "height_cm": ("身高", "cm"), "name": ("称呼", ""),
                "sex": ("性别", ""), "goal": ("目标", "")}
_ACK_GOAL_ZH = {"build_muscle": "增肌", "lose_fat": "减脂", "maintain": "维持"}
_ACK_SEX_ZH = {"male": "男", "female": "女"}


def _ack_text(cmd: dict) -> str:
    """抽取指令 → 用户可见确认话术（单源；render/clarify 共用）。"""
    op = cmd["op"]
    if op == "profile":
        key = cmd["key"]
        label, unit = _ACK_PROFILE.get(key, (key, ""))
        v = cmd["value"]
        if key == "goal":
            v = _ACK_GOAL_ZH.get(v, v)
        elif key == "sex":
            v = _ACK_SEX_ZH.get(v, v)
        return f"{label} {v}{unit}".strip()
    if op == "preference":
        return f"训练偏好 {cmd['value']}{cmd.get('about') or ''}"
    if op == "preference_diet":
        return f"饮食偏好 {cmd['value']}"
    if op == "preference_part":
        return f"训练偏好 {cmd['value']}{cmd.get('about', '').replace('部位:', '练')}"
    if op == "checkin":
        seg = []
        for it in cmd.get("items", []):
            nm = it.get("name") or it.get("raw") or ""
            if it.get("sets") and it.get("reps"):
                nm += f"{it['sets']}x{it['reps']}"
            if nm:
                seg.append(nm)
        body = f"（{'、'.join(seg)}）" if seg else ""
        return f"训练记录 {cmd['occurred']}{body}"
    if op == "forget_all":
        return "已清除全部记忆数据"
    return op


def apply_memory_extract(text: str, user_id: str) -> list[str]:
    """把抽取指令写入 MemoryStore，返回 ack 确认话术列表（空=无写入）。
    静默；无图谱/失败 → 已收集的 acks 原样返回（绝不抛异常影响主链路）。"""
    acks: list[str] = []
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return acks
        for cmd in extract(text):
            ok = False
            if cmd["op"] == "preference":
                ok = m.upsert_state(user_id, "preference", cmd["value"],
                                    about=cmd["about"]) is not None
            elif cmd["op"] == "preference_diet":
                ok = m.upsert_state(user_id, "preference", cmd["value"],
                                    about=None) is not None
            elif cmd["op"] == "preference_part":
                ok = m.upsert_state(user_id, "preference", cmd["value"],
                                    about=cmd["about"]) is not None
            elif cmd["op"] == "checkin":
                ok = bool(m.log_event(
                    user_id, "checkin",
                    {"about": cmd["about"] or "", "verb": cmd["verb"],
                     "items": cmd.get("items", [])},
                    occurred_at=f"{cmd['occurred']}T00:00:00+00:00"))
            elif cmd["op"] == "profile":
                ok = m.upsert_state(user_id, f"profile.{cmd['key']}",
                                    json.dumps(cmd["value"])) is not None
            elif cmd["op"] == "forget_all":
                m.forget(user_id)            # 删除权最高（E2E-01/IS-02）
                ok = True
            if ok:
                acks.append(_ack_text(cmd))
    except Exception:
        return acks
    return acks
