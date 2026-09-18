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

from negation import has_negation, negated_at  # 同库否定原语单源（lib 在 sys.path 上）
from functools import lru_cache
from pathlib import Path

DATA = (Path(__file__).resolve().parent.parent
        / "data" / "split-schemes" / "data" / "split_schemes.json")

_WD_ZH = "一二三四五六日"

# 数据包缺失/损坏时的兜底（spec §9）：与 default_4split 等价的一份副本
# ⚠ 必须与 split_schemes.json 里的 default_4split **逐字一致**（含 summary/example）。
# 那两个字教字段是 2026-09-17 补的：它们原先只存在于 teach_skill._SPLIT_KB，
# 于是"编排方案"这一个概念有两份描述，且两份对"上下肢"的循环节奏说法不一致。
# 现在教学文案归数据包，teach 只读不写。见 app/skills/teach_skill.py 的 _schema_hit。
_FALLBACK = {"schemes": [{
    "id": "default_4split", "name_zh": "经典四天分化", "aliases": [],
    "default": True,
    "cycle": [
        {"type": "train", "label": "推日(胸·肩·三头)", "pattern": "push"},
        {"type": "train", "label": "拉日(背·二头)", "pattern": "pull"},
        {"type": "train", "label": "腿日(股四·臀·腘绳)", "pattern": "squat"},
        {"type": "train", "label": "核心日", "pattern": "core"}],
    "summary": "把训练分为推日、拉日、腿日、核心日四天一轮，每个肌群约每 4 天刺激一次，"
               "部位覆盖均衡，适合有一定基础、能稳定安排四天训练的人。",
    "example": "Day1 推（胸·肩·三头）→ Day2 拉（背·二头）→ Day3 腿（股四·臀·腘绳）"
               "→ Day4 核心，循环往复。",
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


# 休息日恢复提示：pipeline 生成与 plan_skill 编辑**同一句话**，落在这里单源
# （两边各写一份字面量必然漂移，用户会看到同一件事两种说法）。
REST_NOTE = "睡眠 7-9 小时、蛋白质吃够、可做轻拉伸或散步"


# 整天改休息日（2026-09-13 用户实测）。缺陷形态：助手自己建议「想整天空出来，就说
# 『今天改成休息日』」，用户照说 → plan_skill._EDIT_REPLACE_RE 把句子切成
# X=今天 / Y=休息日（**时间锚点当动作名、日类型当替换目标**）→「当前计划里没有
# 「今天」这个动作」。能力不存在却已被承诺，是"假编辑"家族的第四例（前三次：
# 假确认/假撤三头/假平移），故语法与定位一并沉到本层单源（分类层与技能层共用）。
_REST_WORDS = ("休息日", "休息", "休整", "恢复日", "歇一天", "歇着")
_REST_VERBS = ("改成", "改为", "换成", "换掉", "调成", "设成", "设为",
               "排成", "变成", "安排成", "改")
_DAY_TOKENS = (("今天", 0), ("今儿", 0), ("今日", 0),
               ("明天", 1), ("明儿", 1), ("后天", 2))
# 裸式：「今天不练」「今天不练了」。日词 + 不练 + **仅**语气词收尾——'今天不练三头'
# 是部位级删除（走 _muscle_targets 撤三头动作），不是整天休，故必须收尾锚定。
# 刻意**不**放过逗号后的补充（"今天不练了，改成练胸"读作改计划而非整天休）：破坏性
# 且持久化的编辑宁可漏判（另有 parse_fail 提示句式兜底），也不误判整天。
_REST_BARE_RE = re.compile(
    r"(?:今天|今儿|今日|明天|明儿|后天)\s*(?:就)?\s*不练(?:了|啦|吧|咯|哟)?$")
_DATE_IN_TEXT_RE = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]")


def _day_target(text: str) -> dict | None:
    """句中日词/具体日期 → {"offset": n} 或 {"month","day"}；都没有 → None（缺省今天）。"""
    for tok, off in _DAY_TOKENS:
        if tok in text:
            return {"offset": off}
    m = _DATE_IN_TEXT_RE.search(text)
    if m:
        return {"month": int(m.group(1)), "day": int(m.group(2))}
    return None


def extract_rest_day(text: str) -> dict | None:
    """整天改休息目的请求 → 目标日 spec（{"offset"} / {"month","day"} / None=今天）；
    非此请求 → None。分类层据此判 plan_edit，plan_skill 据此定位条目。

    **必须是指令形态**：带变更动词，或裸式「<日>不练[了]」。'我今天休息''我平时
    休息两天' 是状态/习惯陈述，不是指令——破坏性编辑不认陈述（同 2026-09-11
    否定式编辑误触的既有原则）。变更动词须**先于**休息词——'把休息日改成训练日'
    是反向请求，不是这一支；取**首次**出现的动词，'改成休息日吧，别改了' 这类
    尾巴上的重复动词才不会把目标词挤出窗口。动词前短窗口内的否定
    （'今天别改成休息日'/'我不想改成休息日'）整句否决。"""
    t = (text or "").strip()
    if not t:
        return None
    bare = _REST_BARE_RE.search(t)
    if bare:                       # 裸式的"不"就是指令本身，不做否定否决
        return _day_target(bare.group(0)) or {"offset": 0}
    pos = min((i for i in (t.find(v) for v in _REST_VERBS) if i >= 0),
              default=-1)
    if pos < 0 or not any(w in t[pos:] for w in _REST_WORDS):
        return None
    if negated_at(t, pos, win=3):
        return None
    return _day_target(t) or {"offset": 0}   # 无日词 → 缺省今天（"改成休息日吧"）


# 整天换训练日（2026-09-18）。缺陷形态与上面"整天改休息日"**同族**，只是这次连能力
# 都不存在：用户说「把今天的训练计划改成练腿的」→ 技能层 `_EDIT_REPLACE_RE` 把它切成
# X=今天的训练计划 / Y=练腿的 → not_found（时间锚点当动作名，与 2026-09-13 那次逐字同类）；
# 助手于是建议「今天想练腿，帮我调整计划」，用户**照说** → 仍是 parse_fail。
# 这是"能力不存在却已被承诺"的第五例（前四例：假确认 / 假撤三头 / 假平移 / 假休息日）。
# 抽取沉到本层单源，分类层与技能层共用（同 extract_rest_day / extract_shift_days）。
_SWAP_VERBS = ("想练", "要练", "改成", "改为", "换成", "换掉", "调成",
               "变成", "排成", "安排成")
# 生成类动词否决：「帮我排一个练腿的计划」是**生成**请求而不是改现有计划。
# 不否决的话本支会把它从 plan 抢成 plan_edit —— 而"排一个 X 的计划"是很常见的说法。
_SWAP_GEN_VETO = ("生成", "制定", "设计", "排一个", "排个", "做一个", "做一份",
                  "来个", "整一份", "出一份", "重排")
# 部位词只认**两种形态**：紧跟"练/做"（练腿 / 做背），或带"日/天/训练"后缀
# （腿日 / 背部训练）。⚠ 刻意**不做裸词匹配**：「把深蹲换成腿部伸展」里的"腿部"是
# **动作名的一部分**，裸匹配会让它被当成"整天换成腿日"——那是破坏性误判（整个推日
# 被换掉）。末尾前瞻再要求部位短语后是收尾/语气词，把"腿部伸展"这类后接名词的挡掉。
_SWAP_PART_RE = re.compile(
    r"(?:(?:练|做)\s*(?P<a>胸|背|肩|腿|臀|臂|腹|核心)"
    r"|(?P<b>胸|背|肩|腿|臀|臂|腹|核心|推|拉)\s*(?:日|天|部?训练))"
    r"(?=[的了呢吧啊嘛，。,.、和或\s]|$)")
# "前移"式：「把 9月20日 的腿日提前到今天」——助手自创过的另一句（规则 10 禁止
# 自创说法，但它还是会编，所以能力侧必须接得住）。
# ⚠ 必须单列：这句里有**两个**日词，句首那个是**要被挪走**的那天、动词后那个才是
# 目标日。而 `_day_target` 取的是**首个**日词 —— 直接用会定位到 9月20日，正好反了。
_MOVE_VERBS = ("提前到", "提前至", "挪到", "挪至", "移到", "移至", "换到")


def extract_day_swap(text: str) -> tuple[dict | None, str] | None:
    """整天换训练日的请求 → `(目标日 spec, 部位词)`；非此请求 → None。

    与 `extract_rest_day` 同口径：**必须是指令形态**（变更动词先于部位词），且动词前
    短窗口内是否定 → 整句否决（"今天不想练腿"是状态陈述，不是指令）。无日词 → 缺省今天。

    返回值第二项是**词面**（"腿" / "背" / "推"），归一由调用方做：`lib/parts.py`
    给得出 pattern 的走 pattern 匹配，给不出的（"推"/"拉" 是日名不是部位）退回按
    day 名匹配 —— 那两张表都不该在这里再抄一份。
    """
    t = (text or "").strip()
    if not t or any(k in t for k in _SWAP_GEN_VETO):
        return None
    # 前移式先判：它的目标日在动词**之后**，不能走下面那条"取首个日词"的路
    for _v in _MOVE_VERBS:
        i = t.find(_v)
        if i < 0:
            continue
        pm = _SWAP_PART_RE.search(t[:i]) or _SWAP_PART_RE.search(t[i:])
        if not pm:
            continue
        if negated_at(t, i, win=3):
            return None
        return (_day_target(t[i:]) or {"offset": 0}, pm.group("a") or pm.group("b"))
    pos = min((i for i in (t.find(v) for v in _SWAP_VERBS) if i >= 0),
              default=-1)
    if pos < 0:
        return None
    m = _SWAP_PART_RE.search(t[pos:])
    if not m:
        return None
    if negated_at(t, pos, win=3):
        return None
    return _day_target(t) or {"offset": 0}, (m.group("a") or m.group("b"))


def day_index(plan: dict, target: dict | None, today: date) -> int | None:
    """目标日 → training.items 下标；定位不到（无 start_date/超出跨度）→ None。

    按 `start_date + 序号` 算真实日期，**不解析 '今天（9月13日 周日）' 锚点串**——
    锚点是生成时快照，隔日即错（D2）；序号才是稳定坐标。start_date 缺失（旧数据）
    → None，调用方如实拒绝（同 _shift 的 no_base：无基准不猜）。"""
    if target is None:
        return None
    if "index" in target:                     # 词面直接命中某个 day 名 → 已定位
        return target["index"]
    sd = plan.get("start_date")
    if not sd:
        return None
    try:
        start = date.fromisoformat(str(sd))
    except (TypeError, ValueError):
        return None
    items = (plan.get("training") or {}).get("items") or []
    for i in range(len(items)):
        d = start + timedelta(days=i)
        if "offset" in target and (d - today).days == target["offset"]:
            return i
        if "month" in target and (d.month, d.day) == (target["month"], target["day"]):
            return i
    return None


def day_index_of_label(items: list, term: str) -> int | None:
    """词面**恰好**是某个训练日的 day 名 → 下标（"今天不练推日" 的 day 级读法）。

    只认全等（比对去掉括号后缀的日名），不认部分包含——'不练推' 不该被当成
    '推日'：宁可如实说没找到，也不猜着撤掉一整天的训练。"""
    from exercise_repo import norm_zh as _nz
    want = _nz(str(term or ""))
    if not want:
        return None
    for i, d in enumerate(items):
        if (d or {}).get("type") == "rest":
            continue
        base = _nz(re.split(r"[（(]", str((d or {}).get("day") or ""))[0])
        if base and want == base:
            return i
    return None


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
