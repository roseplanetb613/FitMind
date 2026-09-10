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

# N-3 部位偏好（spec §6）：单字部位 + 常见双字部位
_PART_CHARS = "腿肩背胸臂腹臀"
_PART_WORDS = ("核心",)

# 训练动作时态词 →（occurred 偏移天数，名称）
_EVENT_HINT = (("上周三", 9), ("上周四", 8), ("上周五", 7), ("上周六", 6),
               ("上周日", 5), ("上周一", 4), ("上周二", 3),
               ("昨天", 1), ("前天", 2), ("上个月", 30), ("上周", 7),
               ("今天", 0))
_EVENT_VERB = ("练了", "练", "做了", "跑了", "练过")

_EVENT_SPLIT = re.compile(r"[+，、和,\s]+")
_SETS_RE = re.compile(r"(\d{1,2})\s*[xX×*]\s*(\d{1,3})")


_QUESTION = ("怎么", "吗", "?", "？", "啥", "为什么", "能不能", "可以吗",
             "行不行", "应该", "要不要",
             # 疑问代词（CLI 实证："我今天练什么"被 _event 抽成 raw="什么"
             # 的伪 checkin）："练几组/练多少/练多久/练哪"同类问法一律不抽
             "什么", "几", "多少", "多久", "哪")

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

# 相对体重（N-9："又瘦了一斤"）：中文数字一二两…十 + 阿拉伯数字；apply 侧锚定现值
_CN_NUM = {"一": 1.0, "二": 2.0, "两": 2.0, "三": 3.0, "四": 4.0, "五": 5.0,
           "六": 6.0, "七": 7.0, "八": 8.0, "九": 9.0, "十": 10.0}
_WEIGHT_DELTA_RE = re.compile(
    r"(瘦|轻|胖|重|涨)了\s*(\d+(?:\.\d+)?|[一二两三四五六七八九十])"
    r"\s*(公斤|千克|斤|kg|KG)")

# 数据删除权（E2E-01/IS-02："忘掉我的数据" → forget 全清）
_FORGET_RE = re.compile(r"(忘掉|删除|清除|抹掉|清空).*(数据|记忆|档案|记录)")

# 情绪/假设/意向（不抽）
_SKIP = ("emo", "心情", "低落", "焦虑", "想试试", "想练", "打算", "计划",
         "想开始", "犹豫", "想吃", "想喝")

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

# 编排习惯句式（2026-09-10 周期编排 spec §7.2 + review 收窄）："我习惯/平时 + 方案别名"
# review 收窄：去掉"一直/一般"（医学持续态副词，"膝盖一直疼"误抽风险，宁缺毋滥）
_SPLIT_HABIT_RE = re.compile(r"习惯|平时")
# 否定/伤病语境整句否决（宁缺毋滥：假阴性可接受，假偏好 180 天不可接受）
_SPLIT_NEG_KW = ("不", "别", "没", "讨厌")
_SPLIT_INJURY_KW = ("疼", "痛", "伤", "肿", "麻", "不适", "恶心")
# W4 负向编排偏好 carve-out：体验型否定（不习惯/跟不上/受不了/太累/太频繁/吃不消）
# 是**真偏好**（用户不适合这套节奏），值得落库供 plan 反向避开；祈使型（别/不要/没）
# 与厌恶型（讨厌）维持否决——"别排推拉腿"不得变 180 天假偏好反向操控方案选择。
_SPLIT_NEG_FEEL_RE = re.compile(r"不习惯|跟不上|受不了|太累|太频繁|吃不消")

# T3 食物事件/偏好（spec §3-T3）：餐次提示 + 进食动词 + 量词
_MEAL_HINT = (("早餐", "早餐"), ("早饭", "早餐"), ("早上", "早餐"),
              ("午餐", "午餐"), ("中午", "午餐"), ("午饭", "午餐"),
              ("晚餐", "晚餐"), ("晚上", "晚餐"), ("晚饭", "晚餐"),
              ("加餐", "加餐"), ("夜宵", "夜宵"))
_MEAL_VERB = ("吃了", "吃", "喝了", "喝")
_AMOUNT_RE = re.compile(r"(\d{1,4})\s*(克|g|毫升|ml|杯|个|碗)")


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


def _norm_food(text: str):
    """食物归一：FoodsRepo search 命中返回名称；无命中 None（宁缺毋滥）。"""
    try:
        from app.runtime.repos import foods_repo
        hits = foods_repo().search(text, limit=1)
        if hits:
            return hits[0].get("name_zh") or hits[0].get("name")
    except Exception:
        pass
    return None


def _meal_event(text: str) -> dict | None:
    """'我中午吃了鸡胸+米饭' → meal 打卡（归一失败 raw 保底；意向句不抽）。
    护栏：意向/否定/口味语境不抽；全部条目均无归一命中 → 宁缺毋滥不记
    （防'吃多少没事的'类垃圾句产生伪事件）。"""
    if any(k in text for k in ("爱吃", "喜欢吃", "想吃", "想喝", "不想",
                               "讨厌", "不吃", "别吃", "别喝", "要吃", "要喝")):
        return None                                 # 意向/偏好/否定非事件
    verb = next((v for v in _MEAL_VERB if v in text), None)
    if verb is None:
        return None
    meal = next((lbl for kw, lbl in _MEAL_HINT if kw in text), None)
    seg = text.split(verb)[-1]
    items: list[dict] = []
    for part in _EVENT_SPLIT.split(seg):
        part = part.strip(" 的了。，")
        if not part:
            continue
        am = _AMOUNT_RE.search(part)
        namepart = _AMOUNT_RE.sub("", part).strip()
        nm = _norm_food(namepart) if namepart else None
        it: dict = {}
        if nm:
            it["name"] = nm
        elif namepart:
            it["raw"] = namepart
        else:
            continue
        if am:
            it["amount"] = f"{am.group(1)}{am.group(2)}"
        items.append(it)
    if not items:
        return None
    if not any(i.get("name") for i in items):
        return None                     # 全 raw 无归一命中 → 不记（宁缺毋滥）
    return {"op": "meal", "meal": meal,
            "occurred": datetime.now(timezone.utc).date().isoformat(),
            "items": items}


def _food_preference(tail: str, like: bool) -> dict | None:
    """'吃鸡胸' → preference about='食物:X'（与部位:/动作 分槽）。"""
    nm = _norm_food(tail.lstrip("吃"))
    if not nm:
        return None
    return {"op": "preference_food",
            "value": "喜欢" if like else "不喜欢", "about": f"食物:{nm}"}


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


def _weight_delta(text: str) -> dict | None:
    """'又瘦了一斤/胖了2公斤' → profile_delta 相对调整（apply 侧锚定现值写回）。"""
    m = _WEIGHT_DELTA_RE.search(text)
    if m is None:
        return None
    num = m.group(2)
    try:
        v = float(num) if num[0].isdigit() else _CN_NUM[num]
    except (KeyError, ValueError):
        return None
    if m.group(3) == "斤":
        v /= 2.0
    delta = -v if m.group(1) in ("瘦", "轻") else v
    return {"op": "profile_delta", "key": "weight_kg", "delta": delta}


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

def _part_preference(tail: str, like: bool) -> list[dict]:
    """'练腿练肩背' → 每部位一条 preference(about='部位:X') 分槽共存。
    护栏：tail 须含'练'（防'我喜欢胸闷的感觉'误抽'胸'）。"""
    if "练" not in tail:
        return []
    seg = re.sub(r"[练，、和+\s的不]", "", tail)
    parts = [ch for ch in seg if ch in _PART_CHARS]
    for w in _PART_WORDS:
        if w in seg:
            parts.append(w)
    parts = list(dict.fromkeys(parts))
    if not parts:
        return []
    return [{"op": "preference_part",
             "value": "喜欢" if like else "不喜欢",
             "about": f"部位:{p}"} for p in parts]




def _event(text: str) -> dict | None:
    """'昨天我练了腿'/'前天练的腿+悬垂举腿4x8 弯举4*10' -> checkin 补录。
    混合存储：归一命中（无空格干净名）存 name，否则存 raw=用户原词保底（宁丢结构不丢信息）；组次可解析即带。
    注：search_zh 对绝大多数输入返回带空格复合名（如 '腿'→'摆臂 悬垂 屈膝 腿'），
    存 raw 更保真；仅当归一为无空格单一名时才存 name。"""
    verb_hit = next((v for v in _EVENT_VERB if v in text), None)
    if verb_hit is None:
        return None
    time_hit = next(((n, d) for n, d in _EVENT_HINT if n in text), None)
    if time_hit is None:
        return None
    seg = text.split(verb_hit)[-1]
    items: list[dict] = []
    for part in _EVENT_SPLIT.split(seg):
        part = part.strip(" 的了。，")
        if not part:
            continue
        m = _SETS_RE.search(part)
        namepart = _SETS_RE.sub("", part).strip(" 的了。")
        if not namepart:
            continue
        it: dict = {}
        norm = _norm_exercise(namepart)
        if norm and " " not in norm:
            it["name"] = norm              # 无空格干净归一名
        else:
            it["raw"] = namepart           # 保底：宁丢结构不丢信息
        if m:
            it["sets"], it["reps"] = int(m.group(1)), int(m.group(2))
        items.append(it)
    if not items:
        return None
    about = next((i["name"] for i in items if i.get("name")), None)
    today = datetime.now(timezone.utc).date()
    occurred = (today - timedelta(days=time_hit[1])).isoformat()
    return {"op": "checkin", "occurred": occurred,
            "about": about, "verb": verb_hit, "items": items}


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


def _split_preference(text: str) -> dict | None:
    """'我习惯练三休一' → preference(about='编排:<方案名>', value='喜欢')；
    '我不习惯练三休一' → 同槽 value='不喜欢'（W4 体验型负向偏好）。
    只认数据包 aliases 命中；未命中不抽（宁缺毋滥）。
    review 加固（2026-09-10）：触发词收窄（习惯/平时）；否定/伤病语境整句否决
    （抽取先于 guard 执行，"别排推拉腿"不得变成 180 天假偏好反向操控方案选择）；
    一句命中多个不同方案别名 → 歧义不抽。
    W4 carve-out：体验型否定（_SPLIT_NEG_FEEL_RE）不视为"否定指令"，无需习惯词即可
    抽取并记 value='不喜欢'；伤病语境仍是红线——任何极性都不抽。"""
    feel_neg = bool(_SPLIT_NEG_FEEL_RE.search(text))
    if feel_neg:
        if any(k in text for k in _SPLIT_INJURY_KW):
            return None                  # 伤病语境任何极性都不抽（红线不动）
    else:
        if not _SPLIT_HABIT_RE.search(text):
            return None
        if any(k in text for k in _SPLIT_NEG_KW) or \
                any(k in text for k in _SPLIT_INJURY_KW):
            return None
    try:
        import split_cycle
        hits = []
        for s in split_cycle.load_schemes():
            for a in s.get("aliases") or []:
                if a and (a in text or str(a).lower() in text.lower()):
                    hits.append(s)
                    break
        if not hits or len({h.get("id") for h in hits}) > 1:
            return None
        scheme = hits[0]
    except Exception:
        return None
    return {"op": "preference_split",
            "value": "不喜欢" if feel_neg else "喜欢",
            "about": f"编排:{scheme.get('name_zh')}"}


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
    for fn in (_weight, _weight_delta, _age, _height, _name, _sex, _goal):
        if cmd := fn(text):
            out.append(cmd)
    dp = _diet_preference(text)
    if p := _preference(text):
        out.append(p)
    else:
        # 部位/食物偏好（N-3/T3）：归一失败的"练腿练肩背"→部位；"吃鸡胸"→食物。
        # 否定形最长优先（"不喜欢"先于"喜欢"），防极性误判
        for kw in sorted(_LIKE + _DISLIKE, key=len, reverse=True):
            idx = text.find(kw)
            if idx >= 0:
                rest = text[idx + len(kw):]
                cmds = _part_preference(rest, kw in _LIKE)
                if cmds:
                    out.extend(cmds)
                elif not dp and not any(k in text for k in _DIET_PREF_KW):
                    # 口味词语境（吃辣/清淡…）不猜具体食物（宁缺毋滥）
                    fp = _food_preference(rest.strip(" ，。的、和跟练吃"),
                                          kw in _LIKE)
                    if fp:
                        out.append(fp)
                break
    if dp:
        out.append(dp)
    if sp := _split_preference(text):
        out.append(sp)
    if e := _event(text):
        out.append(e)
    if ml := _meal_event(text):
        out.append(ml)
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
    if op == "preference_food":
        return f"食物偏好 {cmd['value']}{cmd.get('about', '').replace('食物:', '')}"
    if op == "meal":
        seg = []
        for it in cmd.get("items", []):
            nm = it.get("name") or it.get("raw") or ""
            if it.get("amount"):
                nm += it["amount"]
            if nm:
                seg.append(nm)
        return f"饮食记录 {cmd.get('meal') or cmd['occurred']}（{'、'.join(seg)}）"
    if op == "preference_split":
        # W4：负向必须把"不喜欢"念出来——只写方案名会让用户以为记成了喜欢
        # （语义反转）；正向保持原文案不变（回归）。
        name = cmd.get("about", "").replace("编排:", "")
        neg = "不喜欢" if cmd.get("value") == "不喜欢" else ""
        return f"训练编排 {neg}{name}"
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
            elif cmd["op"] == "preference_split":
                ok = m.upsert_state(user_id, "preference", cmd["value"],
                                    about=cmd["about"]) is not None
            elif cmd["op"] == "checkin":
                # name（归一）与 raw（用户原词）都试：CONTAINS 解析，宁多挂不漏挂
                names = [i[k] for i in cmd.get("items", [])
                         for k in ("name", "raw") if i.get(k)]
                ok = bool(m.log_event(
                    user_id, "checkin",
                    {"about": cmd["about"] or "", "verb": cmd["verb"],
                     "items": cmd.get("items", [])},
                    occurred_at=f"{cmd['occurred']}T00:00:00+00:00",
                    muscles=m.muscles_of_exercises(names) if names else None))
            elif cmd["op"] == "preference_food":
                ok = m.upsert_state(user_id, "preference", cmd["value"],
                                    about=cmd["about"]) is not None
            elif cmd["op"] == "meal":
                fnames = [i["name"] for i in cmd.get("items", [])
                          if i.get("name")]
                ok = bool(m.log_event(
                    user_id, "meal",
                    {"meal": cmd.get("meal"), "items": cmd.get("items", [])},
                    occurred_at=f"{cmd['occurred']}T00:00:00+00:00",
                    foods=fnames or None))
            elif cmd["op"] == "profile_delta":
                cur = (m.current_profile(user_id) or {}).get(cmd["key"])
                if cur is None:
                    continue                     # 无现值锚定 → 不抽（宁缺毋滥）
                try:
                    new = round(float(cur) + float(cmd["delta"]), 1)
                except (TypeError, ValueError):
                    continue
                if not (30.0 <= new <= 300.0):
                    continue
                ok = m.upsert_state(user_id, f"profile.{cmd['key']}",
                                    json.dumps(new)) is not None
                if ok:
                    acks.append(f"体重 {new}kg（{cmd['delta']:+g}kg）")
                    continue
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
