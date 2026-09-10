# -*- coding: utf-8 -*-
"""split_cycle.py — 训练编排方案：加载/别名解析/循环展开/plan 参数抽取。

纯函数、无外部状态（load_schemes lru_cache 只读缓存）；循环相位语义唯一出处。
数据单源：data/split-schemes/data/split_schemes.json（缺失/损坏 → 内置默认兜底）。
"""
from __future__ import annotations
import json
import re
from datetime import date, timedelta
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


def resolve_scheme(name: str | None) -> dict:
    """别名/id/name_zh → 方案 dict；未命中或 None → default 行（不报错）。"""
    if name:
        low = str(name).lower()
        for s in load_schemes():
            if s.get("id") == name or s.get("name_zh") == name:
                return s
            if any(a == name or str(a).lower() == low
                   for a in (s.get("aliases") or [])):
                return s
    return default_scheme()


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
