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
- `expands_to` 区域词在**检索旁路**里显式展开的肌群 id 列表（2026-09-20 定标）。
            ⚠ **刻意不派生自 `region`**：`region` 只是本体的分区字段，与"用户说这个
            部位时想要什么"不是一回事 —— `腰` 的 region 是 `back`，按它展开会把
            斜方肌/背阔肌全拉进来（"腰"变"整个背"，过宽回归）。故逐词写死、可对账。
            缺此字段 → 退回 `muscle`（单肌群），再退回 `region` 兜底。

维保约定：新增部位词只改这里；`test_part2muscle_all_values_exist_in_graph` 会遍历
全表校验每个 `muscle` 在图中真实存在。
"""
from __future__ import annotations

# 区域词的检索展开表（肌群 id 单源；顺序 = 代表肌群在前，用于跨肌群轮转的起点）。
# 取值依据：本体 region 的成员 + `kind="region"` 的文档语义。
_REGION_EXPANDS: dict[str, list[str]] = {
    # 文档语义：腿日不止 quadriceps（"腿"→股四+臀+腘绳）
    "腿": ["quadriceps", "hamstrings", "glutes"],
    "大腿": ["quadriceps", "hamstrings"],          # 大腿=股+腘绳，不含臀
    "臀": ["glutes"],
    # ⚠ 刻意**不含** serratus_anterior（本体里它 region=chest，但"练胸"没人指前锯肌）：
    # 全库只有 5 条以它为主目标的动作（肩胛骨俯卧撑 / 肩部抬举），而跨肌群轮转是
    # 等权交替 —— 带上它，"练胸"前 8 条里 4 条是前锯肌（实测 2026-09-20，
    # scripts/eval_retrieval.py L03）。与"背"不含 spine/levator_scapulae 同一取舍。
    "胸": ["pectorals"],
    "背": ["latissimus_dorsi", "trapezius", "rhomboids",
           "upper_back", "lower_back"],
    "肩": ["deltoids", "rotator_cuff"],
    "臂": ["biceps", "triceps"],
    "手臂": ["biceps", "triceps"],
    "胳膊": ["biceps", "triceps"],
    "腹": ["rectus_abdominis", "obliques"],
    "核心": ["core", "rectus_abdominis", "obliques"],
    "小腿": ["calves", "tibialis_anterior", "ankle_stabilizers"],
    "前臂": ["forearms"],
    "髋": ["abductors", "adductors", "hip_flexors"],
    # ⚠ 刻意只留 lower_back：这就是"不按 region 展开"的样板词（region=back）
    "腰": ["lower_back"],
}

PARTS: dict[str, dict] = {
    # ---- 区域词（kind="region"：编辑按区域展开，非单肌群）----
    "胸": {"region": "chest", "muscle": "pectorals", "pattern": "push",
           "kind": "region", "expands_to": _REGION_EXPANDS["胸"]},
    "背": {"region": "back", "muscle": "latissimus_dorsi", "pattern": "pull",
           "kind": "region", "expands_to": _REGION_EXPANDS["背"]},
    "肩": {"region": "shoulders", "muscle": "deltoids", "pattern": "push",
           "kind": "region", "expands_to": _REGION_EXPANDS["肩"]},
    "腿": {"region": "upper_legs", "muscle": "quadriceps", "pattern": "squat",
           "kind": "region", "expands_to": _REGION_EXPANDS["腿"]},
    "臀": {"region": "glutes", "muscle": "glutes", "pattern": "squat",
           "kind": "region", "expands_to": _REGION_EXPANDS["臀"]},
    "腹": {"region": "core", "muscle": "rectus_abdominis", "pattern": "core",
           "kind": "region", "expands_to": _REGION_EXPANDS["腹"]},
    "核心": {"region": "core", "muscle": "core", "pattern": "core",
             "kind": "region", "expands_to": _REGION_EXPANDS["核心"]},
    "臂": {"region": "upper_arms", "muscle": "biceps", "pattern": "pull",
           "kind": "region", "expands_to": _REGION_EXPANDS["臂"]},
    # ---- 区域别名（不额外给 muscle：语义上是同一区域的另一种说法）----
    "大腿": {"region": "upper_legs", "muscle": "quadriceps", "kind": "region",
             "expands_to": _REGION_EXPANDS["大腿"]},
    "小腿": {"region": "lower_legs", "kind": "region",
             "expands_to": _REGION_EXPANDS["小腿"]},
    "手臂": {"region": "upper_arms", "kind": "region",
             "expands_to": _REGION_EXPANDS["手臂"]},
    "胳膊": {"region": "upper_arms", "kind": "region",
             "expands_to": _REGION_EXPANDS["胳膊"]},
    "前臂": {"region": "forearms", "kind": "region",
             "expands_to": _REGION_EXPANDS["前臂"]},
    "髋": {"region": "hips", "kind": "region",
           "expands_to": _REGION_EXPANDS["髋"]},
    "腰": {"region": "back", "muscle": "lower_back", "kind": "region",
           "expands_to": _REGION_EXPANDS["腰"]},
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


def expands_to(part: str) -> list[str] | None:
    """部位词 → **显式**展开的肌群 id 列表（区域词的检索语义）；不展开 → None。

    单源见 `_REGION_EXPANDS`。刻意不派生自 `region`（理由见模块 docstring 的
    `expands_to` 字段说明与 `_muscles_for_part`）。
    """
    e = (PARTS.get(part) or {}).get("expands_to")
    return list(e) if e else None


def region_words() -> set[str]:
    """kind="region" 的部位词集合。"""
    return {p for p, v in PARTS.items() if v.get("kind") == "region"}
