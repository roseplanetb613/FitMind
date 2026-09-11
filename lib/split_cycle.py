# -*- coding: utf-8 -*-
"""split_cycle.py — 训练编排方案：加载/别名解析/循环展开/plan 参数抽取。

纯函数、无外部状态（load_schemes lru_cache 只读缓存）；循环相位语义唯一出处。
数据单源：data/split-schemes/data/split_schemes.json（缺失/损坏 → 内置默认兜底）。
"""
from __future__ import annotations
import copy
import json
import re
from datetime import date, timedelta

from negation import has_negation  # 同库否定原语单源（lib 在 sys.path 上）
from functools import lru_cache
from pathlib import Path

DATA = (Path(__file__).resolve().parent.parent
        / "data" / "split-schemes" / "data" / "split_schemes.json")

_WD_ZH = "一二三四五六日"

# 数据包缺失/损坏时的兜底（spec §9）：与 default_4split 等价的一份副本
_FALLBACK = {"schemes": [{
    "id": "default_4split", "name_zh": "经典四天分化", "aliases": [],
    "default": True,
    "cycle": [
        {"type": "train", "label": "推日(胸·肩·三头)", "pattern": "push"},
        {"type": "train", "label": "拉日(背·二头)", "pattern": "pull"},
        {"type": "train", "label": "腿日(股四·臀·腘绳)", "pattern": "squat"},
        {"type": "train", "label": "核心日", "pattern": "core"}],
    "note": "同一肌群间隔 48 小时以上"}]}

_DAYS_RE = re.compile(r"(\d+)\s*天")
_WEEK_RE = re.compile(r"一周|本周")

# 缺席态关键词（终审 I-2）：时长短语前后短窗口命中 → 该时长是"多久没练"的
# 缺席陈述而非计划跨度，否决不抽（"90天没练了"≠90天计划）。
# 词表不含单字"休"——"练三休一"等方案别名合法含"休"，误伤会破坏 split 抽取。
_ABSENCE_KW = ("没练", "没锻炼", "没训", "没动", "没去", "停练", "休息了", "歇了")


def day_label(d: date, offset: int) -> str:
    """日期锚点（自 pipeline 迁入，单源）：第 1 天'今天'、第 2 天'明天'、其后具体日期。"""
    wd = f"周{_WD_ZH[d.weekday()]}"
    if offset == 0:
        return f"今天（{d.month}月{d.day}日 {wd}）"
    if offset == 1:
        return f"明天（{d.month}月{d.day}日 {wd}）"
    return f"{d.month}月{d.day}日（{wd}）"


@lru_cache(maxsize=1)
def load_schemes() -> tuple:
    """读数据包（缓存只读）；缺失/损坏/为空 → 内置默认兜底。"""
    try:
        data = json.loads(DATA.read_text(encoding="utf-8"))
        schemes = data.get("schemes") or []
        if schemes:
            return tuple(schemes)
    except Exception:
        pass
    return tuple(_FALLBACK["schemes"])


def schemes_available() -> bool:
    """数据包是否正常加载（False = 缺失/损坏，兜底副本生效中）。"""
    return load_schemes() != tuple(_FALLBACK["schemes"])


def default_scheme() -> dict:
    for s in load_schemes():
        if s.get("default"):
            return s
    return load_schemes()[0]


def find_alias(text: str) -> str | None:
    """原句命中任一方案别名 → 返回该别名（原样）；无命中 None。"""
    if not text:
        return None
    low = text.lower()
    for s in load_schemes():
        for a in s.get("aliases") or []:
            if a and (a in text or str(a).lower() in low):
                return a
    return None


# ---- 方案意图判别（2026-09-11 补本体缺项："采纳/选择" ≠ "询问编排"）----
# 缺陷背景：方案别名裸词原先三层同源归 teach（编排知识问法）。于是"我要练三休一这种"
# 这类**选择/采纳**表达只返知识、不进 plan——用户"无法主动改计划"。判别复用两个
# 既有确定性信号（零幻觉、数据包单源），不新增方案同义词：
#   find_alias（别名命中）+ 问法/采纳标记。
# 否定语境（不习惯/别/不要…）整句弃权 → None，交回 L1/L2 消歧（宁缺毋滥：
# "我不习惯练三休一"是负向偏好陈述，绝不能当采纳生成计划）。
_QUERY_MARK = ("怎么", "如何", "吗", "?", "？", "啥", "为什么", "能不能",
               "可不可以", "可以吗", "该不该", "要不要", "值不值")
_ADOPT_MARK = ("我要", "我想", "就要", "就按", "按这个", "用这个", "搞这个",
               "来这个", "来一套", "习惯", "平时")
# 否定词表与判定原语已下沉 lib/negation.py（单源，2026-09-11 收口）


def scheme_intent(text: str) -> str | None:
    """方案别名命中 → "query"（编排问法，走 teach）/ "adopt"（选择采纳，走 plan）；
    无别名或无法判定（否定语境/陈述事实）→ None（交回上层，不硬判）。

    顺序敏感："要不要"既是问法又含"不要"子串——问法必须先于否定判定。"""
    if not find_alias(text):
        return None
    t = text or ""
    if any(k in t for k in _QUERY_MARK):
        return "query"
    if has_negation(t):
        return None
    if any(k in t for k in _ADOPT_MARK):
        return "adopt"
    return None


def resolve_scheme(name: str | None) -> dict:
    """别名/id/name_zh → 方案 dict；未命中或 None → default 行（不报错）。"""
    return resolve_scheme_checked(name)[0]


def resolve_scheme_checked(name: str | None) -> tuple[dict, bool]:
    """(scheme, resolved)：resolved=False 表示 name 非空但未命中任何方案（已回落默认）。

    调用方据此如实告知，避免"用户点了名却静默给默认"（2026-09-11）。
    name 为空/None → (default, True)——未指定方案不是未解析。"""
    if name:
        low = str(name).lower()
        for s in load_schemes():
            if s.get("id") == name or s.get("name_zh") == name:
                return s, True
            if any(a == name or str(a).lower() == low
                   for a in (s.get("aliases") or [])):
                return s, True
        return default_scheme(), False
    return default_scheme(), True


def scheme_names() -> set:
    """全部 id + name_zh + aliases（validate_profile 白名单用，对齐 resolve_scheme 可解析集）。"""
    out: set = set()
    for s in load_schemes():
        if s.get("id"):
            out.add(s["id"])
        if s.get("name_zh"):
            out.add(s["name_zh"])
        out.update(s.get("aliases") or [])
    return out


def expand(scheme: dict, days: int | None, today: date) -> list[dict]:
    """方案 × 跨度 → 日骨架。days=None → 一个完整循环；days=N → 循环平铺 N 天。
    返回 [{"type","label","date","date_iso","pattern"?,"extra_patterns"?,"note"?}]"""
    cycle = scheme.get("cycle") or []
    if not cycle:
        raise ValueError("scheme cycle 为空")
    if days is None:
        days = len(cycle)
    if days < 1:
        raise ValueError("days 必须 ≥1")
    out: list[dict] = []
    for i in range(days):
        entry = cycle[i % len(cycle)]
        d = today + timedelta(days=i)
        item: dict = {"type": entry["type"], "label": entry.get("label", ""),
                      "date": day_label(d, i), "date_iso": d.isoformat()}
        if entry.get("pattern"):
            item["pattern"] = entry["pattern"]
        if entry.get("extra_patterns"):
            item["extra_patterns"] = list(entry["extra_patterns"])
        if entry.get("note"):
            item["note"] = entry["note"]
        out.append(item)
    return out


# 计划整体平移（2026-09-11 W4）："把计划都后延一天呢" 是真实需求，此前无此能力 →
# 静默重新生成 + 渲染层编造平移日程（含数据里不存在的日期）。平移为纯日期运算。
_SHIFT_MARK = ("后延", "延后", "顺延", "推迟", "平移", "往后挪", "往后推", "往后延")
_SHIFT_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7}
_SHIFT_RE = re.compile(r"(\d+|[一两二三四五六日])\s*(?:天|日)")


def extract_shift_days(text: str) -> int | None:
    """计划整体平移请求 → 平移天数（缺省 1）；非平移请求 → None。"""
    t = text or ""
    if not any(k in t for k in _SHIFT_MARK):
        return None
    m = _SHIFT_RE.search(t)
    if not m:
        return 1
    g = m.group(1)
    return int(g) if g.isdigit() else _SHIFT_NUM.get(g, 1)


def reanchor(plan: dict, today: date) -> dict:
    """按 plan["start_date"] + 条目序号重算 training.items[].date（返回**副本**）。

    读回的计划带的是**生成时快照**（"今天（9月10日 周四）"）——隔日读若不重算，
    就会把昨天说成"今天"（实测锚点整体倒退一天）。重算规则：
    偏移 0/1 → 今天/明天；其余 → 具体日期；**过去日退化为具体日期**，不冒充今天。
    非当日生成的计划同时剔除 fatigue_note/fatigue_sources（48h 窗口相对生成时刻，
    隔日即失效，照旧复述等于对用户说过期的话）。
    start_date 缺失/非法（旧数据）→ 原样返回，行为与重锚定前逐字一致。"""
    sd = plan.get("start_date")
    if not sd:
        return plan
    try:
        start = date.fromisoformat(str(sd))
    except (TypeError, ValueError):
        return plan
    out = copy.deepcopy(plan)                 # 不改写调用方持有的对象
    items = (out.get("training") or {}).get("items") or []
    for i, it in enumerate(items):
        d = start + timedelta(days=i)
        it["date"] = day_label(d, (d - today).days)
    if start != today:                        # 隔日 → 疲劳断言已过期
        for k in ("fatigue_note", "fatigue_sources"):
            out.pop(k, None)
    return out


def _absence_veto(text: str, start: int, end: int, win: int = 4) -> bool:
    """时长短语前后短窗口（默认 4 字）内命中缺席态关键词 → True（否决该时长）。
    否决的是单个时长匹配而非整句：被否决后继续找下一个时长匹配
    （"休息了3天，排个5天计划"→ 3天被否决、5天正常抽取）。"""
    seg = text[max(0, start - win):start] + text[end:end + win]
    return any(k in seg for k in _ABSENCE_KW)


def extract_plan_params(text: str) -> dict:
    """plan 意图参数抽取（单源，llm 规则层与 semantic L1 共用）：
    split=原句命中的方案别名；days=显式天数（'5天'/'一周'）。缺省不带 days
    （=一个循环，由 expand 处理），不再是历史遗留的固定 {"days": 1}。
    缺席态否决（终审 I-2）：'90天没练了'是多久没练的陈述，非计划跨度，
    时长前后窗口命中 _ABSENCE_KW 即跳过该时长（'一周没练'同理不抽 days）。"""
    p: dict = {}
    alias = find_alias(text)
    if alias:
        p["split"] = alias
    t = text or ""
    for m in _DAYS_RE.finditer(t):
        if not _absence_veto(t, m.start(), m.end()):
            p["days"] = int(m.group(1))
            return p
    for m in _WEEK_RE.finditer(t):
        if not _absence_veto(t, m.start(), m.end()):
            p["days"] = 7
            return p
    return p
