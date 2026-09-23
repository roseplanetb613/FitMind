# -*- coding: utf-8 -*-
"""渲染叶子工具：训练计划天级行单源（nodes._render_fallback 与
llm.StubProvider.render 共用——LLM 失败兜底与无 key/断网路径输出一致）；
另含**卡片顺序与正文绑定**（bind_items_to_reply）。"""
from __future__ import annotations
import re

# data 里的**人类可读文本载荷**键（顺序即输出顺序）。
# 不取 reason/status：那是机器字段（"no history"/"hold"），透出去等于把实现细节
# 说给用户——llm 渲染提示词第 6 条明令禁止"结构化/字段/为空"这类措辞。
_PAYLOAD_TEXT_KEYS = ("advice", "message")

# 图检索事实的渲染前缀（`rag_evidence` 的三个子键 → 用户可见行）。
# ⚠ 这三个键由 `app/rag/fusion.py::_FACT_KEYS` 定义，**必须保持同源** ——
# 两处各写一份是本仓"同源漂移"的经典入口。改一处必须改另一处。
# `test_render_fact_labels_match_fusion_fact_keys` 把两者钉死。
# ⚠ 键顺序即输出顺序；缺键/空列表一律不输出（见 item_lines 的向后兼容约束）。
_FACT_LABELS = (("alternatives", "可替代"), ("peers", "同肌群"),
                ("contraindications", "禁忌"))


def payload_lines(d: dict) -> list[str]:
    """data 的文本载荷（advice / message）单源。

    ⚠ 键名歧义：`structured["data"]["message"]` 是**闲聊/固定应答的正文**
    （smalltalk_skill），而 `structured["message"]` 是**用户这一句原话**（W1 注入）。
    同名不同义，llm 渲染提示词用规则 3 与规则 9 分别对待——本函数只处理前者。

    此前两个确定性渲染器平行地只认 items/training/macros，advice 与 message
    双双丢失，实测后果：
      · **guard 的 advice 丢失最严重**——红灯症状建议一旦被数字护栏降级，卡片
        只剩"安全提示 / 来源: guard#symptom"，**安全警告变成空白**；
      · smalltalk 的 data.message 丢失——离线（无 LLM）时所有闲聊都只剩标题。
    与 training_lines 同理，单源化是为了让两条兜底路径输出一致。
    """
    out: list[str] = []
    for k in _PAYLOAD_TEXT_KEYS:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def item_lines(d: dict) -> list[str]:
    """检索条目行单源（data.items → "· 名称"）。

    两个渲染器此前**各认一种形态**：nodes._render_fallback 读 `data["items"]`
    （build_structured 的真实产物），llm.StubProvider.render 读顶层
    `structured["items"]`——而该键早已不再产出（见 build_structured 的
    "不再携带恒空的 items 死键"）。两条路径各瞎一半，后果：离线时动作检索
    命中 5 条，卡片上一条都不显示，只剩标题+来源。

    兼容 dict（取 name/name_zh）与裸字符串两种条目形态。

    ⚠ **`value` 必须一起渲染**（2026-09-13）：档案/记忆/肌肉面板的条目是
    `{name, value}` 对（`qa._profile` 的"年龄: 18"、`_memory` 的"训练记录: …"），
    只渲染 name 会得到 `· 年龄` / `· 体重kg` —— 用户看到一排字段名、一个值都没有。
    线上 LLM 会自己把 JSON 组织成散文，所以只有**离线/降级**路径暴露，实测
    `我多少岁` 在无 LLM 时答成"知识问答 / · 年龄 / · 体重kg"。

    `source`（2026-09-14）：条目级**出处**标注（"这条知识哪来的"），可选。
    ⚠ 别和 `structured["sources"]` 混：那一行是 `SkillResult.provenance`
    （`qa#exercise_repo.search` / `ex:0987` 这类**代码路径 / 记录 id**），
    回答的是"哪段代码答的"。两者是不同的东西，本键回答"知识出自哪里"。
    """
    out: list[str] = []
    for it in d.get("items") or []:
        if not isinstance(it, dict):
            if it:
                out.append(f"· {it}")
            continue
        nm = it.get("name") or it.get("name_zh")
        if not nm:
            continue
        val = it.get("value")
        line = f"· {nm}：{val}" if val not in (None, "") else f"· {nm}"
        src = it.get("source")
        if src:
            line += f"（来源：{src}）"
        out.append(line)
        # 图检索事实（2026-09-23）：`rag_evidence` 此前**全仓 0 读取方**
        # （teach_skill 写入、没有任何地方读），所以图检索的产出对用户不可见。
        # ⚠ 缺键/空列表一律不输出 —— 无 rag_evidence 时本段输出逐字不变。
        ev = it.get("rag_evidence")
        if isinstance(ev, dict):
            for key, label in _FACT_LABELS:
                vals = ev.get(key)
                if not isinstance(vals, list) or not vals:
                    continue
                if key == "contraindications":
                    names = [f"{v.get('condition')}（{v.get('risk_level')}）"
                             for v in vals if isinstance(v, dict) and v.get("condition")]
                else:
                    names = [v.get("name_zh") or v.get("id")
                             for v in vals if isinstance(v, dict)]
                names = [n for n in names if n]
                if names:
                    out.append(f"  {label}：{'、'.join(names)}")
    return out


def empty_result_lines(d: dict) -> list[str]:
    """空结果兜底行单源（data.empty 为真时）。

    没有这一行，确定性渲染器对空检索会输出**只剩标题+来源的空白卡片**——
    与"静默降级"同一类毛病：用户分不清是"没找到"还是"页面坏了"。实测线上
    LLM 渲染偶发返回空串时就是这个形态（reply 逐字等于 `标题\\n来源: ...`）。

    LLM 路径的正常措辞由渲染提示词规则 1 负责（含换关键词建议）；这里只保证
    断网/降级/LLM 返回空时同样有一句人话。带 advice 的技能（guard/progress）
    不走这条——它们的结论本身就是正文。
    """
    return ["没有找到相关内容。"] if d.get("empty") else []


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
            elif day.get("rest_from"):
                # 用户自己要求的休息日，与筛查封堵分开措辞——说成筛查等于替筛查背锅
                lines.append("  （按你的要求，原定训练改为休息）")
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


# ---------------------------------------------------------------- 卡片顺序 ↔ 正文
# 2026-09-16 用户报「卡片和文字对应不上 跟文字绑定吧」。
#
# 现象（四轮追问链路的第 2 轮，"有更进阶的吗"）：正文按
#     杠铃翻举推举 → 杠铃跳跃深蹲 → 杠铃深蹲跳步后弓步 → 后跳 → 分腿跳
# 逐条讲，卡片却按 data.items 原序摆成
#     分腿跳（男）→ 后跳 → 杠铃翻举推举 → 杠铃跳跃深蹲 → 杠铃深蹲跳步后弓步
# ——同一批 5 个动作，两个顺序全拧；组次/休息写得都对，用户就是认不出
# "哪句话说的是哪张卡"。
#
# 根因是**同一批动作被排了两次序**，两次互不通气：
#   · 卡片：前端 `renderExerciseCards` 直接遍历 `structured.data.items`
#     （qa_skill._pick_harder 按难度降序取的前 5 条）；
#   · 正文：llm.render 把 structured 丢给 LLM，语序是 LLM 自己组织的
#     （渲染提示词规则 1-11 里**没有一条**约束枚举顺序）。
#
# 修法：**卡片跟正文**（用户口径"跟文字绑定"）。之所以不反过来去约束 LLM 语序：
# 提示词约束在本仓库反复被证明不可靠（见 nodes 渲染后置护栏一段的历次实测），
# 而重排是纯确定性计算，永远生效。提示词侧另加一句"按 data.items 顺序介绍"
# （llm.render 规则 12），让常见情况下这次重排退化成恒等操作 —— 卡片于是维持
# "难度降序"这个对用户有意义的顺序，而不是被 LLM 的临时语序牵着走。

# 变体后缀："分腿跳 （男）" / "单腿 深蹲 (手枪) 男"。正文只会说"分腿跳"，
# 拿全名去匹配必然对不上 → 整批判成"未提及"、顺序纹丝不动（等于本函数没生效）。
_SUFFIX_RE = re.compile(r"[（(]")


def _norm_name(s: str) -> str:
    """名字比对归一（去空格+小写）。单源在 `lib.exercise_repo.norm_zh`——
    此处**不得**另写一份（该模块已因"裸子串比对"出过两次同类缺陷，见 `same_name`）。"""
    try:
        from lib.exercise_repo import norm_zh
        return norm_zh(s)
    except Exception:                      # lib 不在 sys.path（异常环境）→ 最小降级
        return "".join((s or "").split()).lower()


def _name_keys(name: str) -> list[str]:
    """条目名 → 匹配键（长名在前）：全名 + 去括号后缀的短名。"""
    full = _norm_name(name)
    keys = [full] if full else []
    base = _norm_name(_SUFFIX_RE.split(name, maxsplit=1)[0])
    if base and base != full:
        keys.append(base)
    return keys


def _mention_order(text: str, keys: list[list[str]]) -> list[int]:
    """正文里**点名次序** → 条目下标列表（只含被点到的）。

    从左到右逐位置认领**最长**匹配，不是"每个名字各查一次首次出现位置"：
    后者会让短名命中长名内部（卡片里「深蹲」与「杠铃 深蹲」并存时，「深蹲」会
    抢走「杠铃深蹲」那个位置），批次顺序随即错乱。
    """
    out: list[int] = []
    taken: set[int] = set()
    pos = 0
    while pos < len(text):
        best, best_len = -1, 0
        for i, cands in enumerate(keys):
            if i in taken:
                continue
            for k in cands:
                if k and len(k) > best_len and text.startswith(k, pos):
                    best, best_len = i, len(k)
        if best < 0:
            pos += 1
            continue
        taken.add(best)
        out.append(best)
        pos += best_len
    return out


def bind_items_to_reply(structured: dict, reply: str) -> int:
    """把动作卡片重排成**正文点名的先后顺序**（原地改 structured）。

    返回参与排序的卡片数；0 = 没动（无 items / 非动作卡片 / 正文一个都没点到）。

    **适用面刻意窄**：只在条目带 `id` + `name_zh`（= 动作卡片，`qa._exercise_item`
    的形状）时动手。档案/记忆条目是 `{name, value}` 对、食物条目带 per_100g ——
    它们的"顺序"没有正文语序可言（档案项本就是一排字段），重排只会制造无谓抖动。

    非动作条目（如 `qa._rag_science` 追加在末尾的知识块）**原地不动**：只把动作
    卡片在它们自己占的槽位之间重排，别的东西一个不挪。

    正文没点到的卡片（LLM 漏讲/换了说法）保留原有相对次序，排在点名过的后面 ——
    不丢弃：数据在、只是正文没提，用户仍该看得见（丢卡会变成"答少了"）。
    """
    data = (structured or {}).get("data")
    items = (data or {}).get("items")
    if not isinstance(items, list) or len(items) < 2:
        return 0
    slots = [i for i, it in enumerate(items)
             if isinstance(it, dict) and it.get("id") and it.get("name_zh")]
    if len(slots) < 2:
        return 0
    keys = [_name_keys(str(items[i]["name_zh"])) for i in slots]
    if not any(keys):
        return 0
    order = _mention_order(_norm_name(reply or ""), keys)
    if not order:
        return 0                       # 正文没点名 → 无从排起，保持原样
    mentioned = [slots[j] for j in order]
    rest = [i for i in slots if i not in set(mentioned)]
    seq = mentioned + rest
    if seq == slots:
        return 0
    # 先取副本再回写：槽位只占动作卡片那几个，非动作条目原地不动
    ordered = [items[i] for i in seq]
    for slot, it in zip(slots, ordered):
        items[slot] = it
    return len(slots)
