# -*- coding: utf-8 -*-
"""渲染叶子工具：训练计划天级行单源（nodes._render_fallback 与
llm.StubProvider.render 共用——LLM 失败兜底与无 key/断网路径输出一致）。"""
from __future__ import annotations

# data 里的**人类可读文本载荷**键（顺序即输出顺序）。
# 不取 reason/status：那是机器字段（"no history"/"hold"），透出去等于把实现细节
# 说给用户——llm 渲染提示词第 6 条明令禁止"结构化/字段/为空"这类措辞。
_PAYLOAD_TEXT_KEYS = ("advice", "message")


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
