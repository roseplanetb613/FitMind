# -*- coding: utf-8 -*-
"""部位词 → 肌群/区域/运动模式 单表（单源）。

背景（2026-09-11 收口）：同一个"部位"概念此前被 **4 张表**分别映射，散在 2 个文件：

    app/graph/memory.py       _PART2MUSCLE     部位 → muscle id（挂边/肌肉面板）
    app/skills/plan_skill.py  _PART2PAT        部位 → 运动模式（计划加权）
    app/skills/plan_skill.py  _PART_MUSCLE_ZH  部位 → 细分肌群（编辑按肌群撤动作）
    app/skills/plan_skill.py  _PART_REGION_ZH  部位 → region（区域展开）

它们**不是分区**而是"每个消费方各取所需"（"腿"同时出现在 3 张表里）。故本表给全
属性，消费者只读自己要的那个，键集不再各自维护。实际后果实例：`_PART2MUSCLE["胸"]`
曾误写 `pectoralis`（本体实为 `pectorals`）——没有单表可对账，长期无人发现，
表现为"胸部面板永远说没有记录"。

字段语义：

- `muscle`  代表肌群 id（本体 ontology 的 id；**必须存在于图谱 Muscle.name**）
- `region`  所属区域（本体 `region` 字段；用于"区域展开"）
- `pattern` 运动模式（push/pull/squat/core/…；用于计划偏好加权）
- `kind`    本词的类型：`"region"`=区域词（编辑时**展开整个区域**，"腿"→股四+臀+腘绳；
            "腿日"不止 quadriceps）、`"muscle"`=细分工位（直接取该肌群）

维保约定：新增部位词只改这里；`test_part2muscle_all_values_exist_in_graph` 会遍历
全表校验每个 `muscle` 在图中真实存在。
"""
from __future__ import annotations

PARTS: dict[str, dict] = {
    # ---- 区域词（kind="region"：编辑按区域展开，非单肌群）----
    "胸": {"region": "chest", "muscle": "pectorals", "pattern": "push",
           "kind": "region"},
    "背": {"region": "back", "muscle": "latissimus_dorsi", "pattern": "pull",
           "kind": "region"},
    "肩": {"region": "shoulders", "muscle": "deltoids", "pattern": "push",
           "kind": "region"},
    "腿": {"region": "upper_legs", "muscle": "quadriceps", "pattern": "squat",
           "kind": "region"},
    "臀": {"region": "glutes", "muscle": "glutes", "pattern": "squat",
           "kind": "region"},
    "腹": {"region": "core", "muscle": "rectus_abdominis", "pattern": "core",
           "kind": "region"},
    "核心": {"region": "core", "muscle": "core", "pattern": "core",
             "kind": "region"},
    "臂": {"region": "upper_arms", "muscle": "biceps", "pattern": "pull",
           "kind": "region"},
    # ---- 区域别名（不额外给 muscle：语义上是同一区域的另一种说法）----
    "大腿": {"region": "upper_legs", "muscle": "quadriceps", "kind": "region"},
    "小腿": {"region": "lower_legs", "kind": "region"},
    "手臂": {"region": "upper_arms", "kind": "region"},
    "胳膊": {"region": "upper_arms", "kind": "region"},
    "前臂": {"region": "forearms", "kind": "region"},
    "髋": {"region": "hips", "kind": "region"},
    "腰": {"region": "back", "muscle": "lower_back", "kind": "region"},
    "脚踝": {"muscle": "ankle_stabilizers", "kind": "muscle"},
    # ---- 细分工位（kind="muscle"：直接取该肌群）----
    "二头": {"muscle": "biceps", "kind": "muscle"},
    "三头": {"muscle": "triceps", "kind": "muscle"},
    "腹肌": {"muscle": "rectus_abdominis", "kind": "muscle"},
}

# 逐字扫描用（memory_extract._part_preference）：单字部位 + 少数多字部位。
# **注意这不是"所有部位词"**——扩表会改变偏好抽取的行为（"我喜欢练手臂"会新变成
# 可抽取），属行为变更需单独评估，故与 PARTS 分开维护。
PART_CHARS = "腿肩背胸臂腹臀"
PART_WORDS = ("核心",)


def muscle_of(part: str) -> str | None:
    """部位词 → 代表肌群 id；无映射 → None。"""
    return (PARTS.get(part) or {}).get("muscle")


def region_of(part: str) -> str | None:
    """部位词 → 区域名；无映射 → None。"""
    return (PARTS.get(part) or {}).get("region")


def pattern_of(part: str) -> str | None:
    """部位词 → 运动模式；无映射 → None。"""
    return (PARTS.get(part) or {}).get("pattern")


def kind_of(part: str) -> str | None:
    """"region"=区域词（编辑按区域展开）/"muscle"=细分工位（取单肌群）/None。"""
    return (PARTS.get(part) or {}).get("kind")


def region_words() -> set[str]:
    """kind="region" 的部位词集合。"""
    return {p for p, v in PARTS.items() if v.get("kind") == "region"}
