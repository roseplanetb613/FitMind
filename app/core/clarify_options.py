# -*- coding: utf-8 -*-
"""消歧选项（human-in-the-loop）的组装 —— **唯一的一处**。

为什么单独成模块：这份 payload 有**两个产出点**，而它们用的是同一个前端渲染器
（`web/src/ui/chat-panel.ts` 的 `renderOptions`）：

    · `app.core.nodes.clarify_exercise`   —— 说的词没对上库内动作
    · `app.server./v1/checkin/resolve` 的 `need_pick` 分支 —— 自己打的字没对上

两条都是"从库内候选里挑一个"，字段集必须逐字一致。在合并之前它俩是**各拼一份**的，
于是已经漂移过两次：

    · `recent_count`：前者真的数了近 30 天的次数并据此排序，后者硬编码 0 且不排
      → 同一屏上下两张卡片，上面有「练过 N 次」角标、下面没有
      （2026-09-15 用户在真机上实测到）
    · `image` / `gif_url`：加图示时差点只加了一半

这类漂移**不会报错** —— 漏一个字段只是界面上少一点东西，选项照样能点，
所以没有任何信号会提醒。新增字段时只改这里一处即可。
"""
from __future__ import annotations

from typing import Iterable, Optional

# 消歧选择框给几个动作。**3 个**（用户定）：再多就不如直接自己打字了，
# 所以界面还配了一个自定义输入口 —— 三个候选 + 一个"都不是，我自己写"。
MAX_CLARIFY_OPTIONS = 3

# 先在库里捞多少条候选，再由"练过的优先"排序截断到 MAX_CLARIFY_OPTIONS。
# **捞的必须比给的多**：取舍要由排序决定，所以用户常练但检索排名第 4、第 5 的
# 动作得有机会冒上来。两个产出点用同一个宽度 —— 否则同一句话在两条路径下
# 给出不同的候选集（此前 server 那份只捞 3 条，等于把排序让给了检索排名）。
CLARIFY_CANDIDATE_POOL = MAX_CLARIFY_OPTIONS * 2

# "最近练过"的回看窗口。与偏好重排（server 的 prefer）用同一个口径。
RECENT_DAYS = 30


def recent_counts(user_id: Optional[str], days: int = RECENT_DAYS) -> dict:
    """近 N 天练过的动作 → `{exercise_id: 次数}`。

    **全降级**：图谱不可用（`MemoryStore.get()` 返回 None）或查询炸了，都返回空表，
    由调用方按"没练过"渲染。**绝不抛** —— "常练的排前面"是个附加信息，
    它取不到不该让整次消歧失败（连选项都没了）。
    """
    try:
        from app.graph.memory import MemoryStore
        store = MemoryStore.get()
        if store is None:
            return {}
        return store.recent_exercises(user_id or "local", days=days) or {}
    except Exception:
        return {}


def build_clarify_options(cands: Iterable[dict], user_id: Optional[str] = None, *,
                          limit: int = MAX_CLARIFY_OPTIONS,
                          days: int = RECENT_DAYS) -> list[dict]:
    """候选动作记录 → 消歧选项，**已按"常练的在前"排好序**。

    排序只在这里做：前端（`renderOptions`）拿到什么顺序就画什么顺序。两处各排一次
    迟早会分家，而分家的表现是"同一个列表在两条路径下顺序不同"——同样没人会发现。

    `cands` 是 `ExerciseRepo` 的增强记录（含 `normalized_equipment` / `difficulty` /
    `image` / `gif_url`）。取不到的字段一律为 `None` —— **不编填充值**：
    界面上"未知"和"确实没有"读不出区别，宁可让那一行不画。

    ⚠ 图示素材 © Gym visual，授权只到 180×180 且**每次使用须带署名**
    （见 `data/exercises-dataset/NOTICE.md`）。
    """
    recent = recent_counts(user_id, days=days)
    ordered = sorted(cands, key=lambda c: -recent.get(str(c.get("id")), 0))
    return [{"id": c.get("id"),
             "name_zh": c.get("name_zh"),
             "equipment": c.get("normalized_equipment"),
             "difficulty": c.get("difficulty"),
             "recent_count": recent.get(str(c.get("id")), 0),
             # 图示：库里的中文名是**逐词翻译**（"摆臂 悬垂 直腿s"），光看字挑不出
             # 自己练的是哪一个 —— 把图一起发过去，让人看着选。
             # 路径相对 data/exercises-dataset/，前端拼 /media 前缀（mediaUrl）。
             "image": c.get("image"),
             "gif_url": c.get("gif_url")}
            for c in ordered[:limit]]
