# -*- coding: utf-8 -*-
"""向量通道的**判决层** —— 把"接受 / 弃权"从排序原语里拆出来。

## 为什么要有这个模块

`retriever.vector_search` 同时干了两件事：**排序**（HNSW 最近邻）和**判决**
（`1 - (embedding <=> %s) >= min_score` 写在 SQL 的 `WHERE` 里）。判决被焊死在
原语里，于是只有一个答案，却要同时服务两类分布完全不同的 chunk_type。

实测（2026-09-20，162 条标注，见
`docs/superpowers/specs/2026-09-20-vector-search-optimization-design.md` §1）：

  · 相关 query 的 top1 余弦 0.5394~0.8708，无关 0.2968~0.7003 —— **两个分布重叠**，
    一个标量在结构上分不开它们；
  · 主要错误是**排序粒度**（`WRONG` 在阈值 0.45~0.62 恒为 16），阈值类手段无效；
  · **词法旁证**有效，但**分 chunk_type**：`exercise_cue` 上把 WRONG 5→0、LEAK 2→0，
    召回**一分不掉**（0.909→0.909）；`science_doc` 上召回 0.692→0.308，太贵。

⚠ 关于"要不要调 `MIN_SCORE`"的结论**变过一次**，原因值得记：
  2026-09-20 白天在 162 条标注上测出"调它没有样本外收益"，据此把门槛留在 0.55。
  **但那个集合只有 17 个负样本（进 test 的更少），把"漏召"照得很小。**
  当晚把标注集扩到 221 条、**负样本 17→50** 后，0.55 的问题立刻现形：
  全量 test 上 LEAK **14/24**、弃权准只有 0.417；0.60 时 LEAK 5、弃权准 0.792。
  ⇒ 门槛已调到 **0.60**（0.60~0.65 是单调宽平台，取下沿）。
  **教训：负样本太少的集合会系统性掩盖 LEAK。**

所以本模块只做一件事：**给判决一个可以按 chunk_type 分化、可以单测、可以记账的
位置**，且**零自由度** —— 不含任何在 dev 上选出来的数，结构上不可能过拟合。

## 三条不变式

1. **零自由度**：门槛只来自现成的 `MIN_SCORE`（未调），旁证条件是**严格相等**
   （没有 margin 可调）。改这个模块**不应**引入新的可调数值。
2. **fail-closed**：需要旁证却拿不到（`lexical_top1_id is None`）时**弃权**，不是
   放行。依据是实测：`science_doc` 上被"一致闸门"丢掉的 8 条正例里，
   **8 条都是两条通道都不一致、且词法那条也错**的情况，误伤 0 条 —— 弃掉的正是
   该弃的。项目口径是"错证据比没证据更糟"，所以安全侧宁缺勿错。
   ⚠ 这与本项目"全降级"（外部依赖失败就返回 None）的传统**方向相反**，所以
   弃权理由必须记进 `Decision.reason`，否则会变成一种新的静默失败。
3. **不动召回**：只看 `ranked[0]`。边界法（`top1 - top2 >= margin`）实测召回只剩
   0.317，已否掉，所以本模块**不需要** `top_k > 1`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.rag.retriever import MIN_SCORE

# 需要**词法旁证**的 chunk_type（结构化动作名，有生产级词法孪生）。
#
# ⚠ `science_doc` **刻意不在其中**：实测加旁证后召回 0.692→0.308，而词法信号在
# 散文上单用精度只有 0.520（散文 query 与散文正文没有可靠的字面孪生关系）。
# 加进去是把一个坏信号接进判决。见规格 §1.6。
CORROBORATED_TYPES: tuple[str, ...] = ("exercise_cue",)

# 弃权/接受的理由（Decision.reason 的取值域）。
# 存在的意义：让"为什么沉默"可归因 —— 对抗"全降级"传统造成的静默失败。
REASON_EMPTY = "empty"                        # 库里没有该 chunk_type 的候选
REASON_BELOW_SCORE = "below_score"            # 相似度不够（正常弃权，多数情况）
REASON_NO_CORROBORATION = "no_corroboration"  # 旁证通道不可用（**异常**，该报警）
REASON_DISAGREE = "disagree"                  # 两条通道不一致（正常弃权）
REASON_OK = "ok"                              # 接受


@dataclass(frozen=True)
class Decision:
    """判决结果。`score` 是参与判决的那条（top1）的相似度。"""

    accepted: bool
    reason: str
    score: float | None = None
    corroborated: bool | None = None      # None = 该 chunk_type 不需要旁证


def from_hits(hits: Sequence[dict] | None) -> list[tuple[str | None, float]]:
    """`vector_search` 的输出 → `[(id, score)]`（判决层的输入形态）。

    把原语的返回形状挡在本模块之外，`decide` 因此可以纯函数地单测（无 PG / 无
    Ollama）。缺 `source_ref` / 缺 `id` / `score` 不是数 —— 一律降级成 `None` 与
    `-inf`（`-inf` 必然低于任何门槛 → 弃权），**不抛**。
    """
    out: list[tuple[str | None, float]] = []
    for h in hits or []:
        if not isinstance(h, dict):
            continue
        sr = h.get("source_ref")
        eid = sr.get("id") if isinstance(sr, dict) else None
        s = h.get("score")
        out.append((eid, float(s) if isinstance(s, (int, float)) else float("-inf")))
    return out


def decide(ranked: Sequence[tuple[str | None, float]],
           *,
           chunk_type: str,
           lexical_top1_id: str | None = None,
           min_score: float = MIN_SCORE) -> Decision:
    """对**已排序**的候选做接受/弃权判决。

    `ranked`：`[(id, score)]`，最像的在前（`from_hits` 的产物）。
    `lexical_top1_id`：调用方算好的**词法通道 top1 的 id**（判决层不自己取数 ——
        评测用 `LexIndex`、生产用 `exercise_repo.search_zh`，口径差异因此是**显式
        传入**的，不会藏进模块里）。
    `min_score`：沿用 `MIN_SCORE`，**本设计不调它**。

    判定顺序与理由见规格 §4.2。注意 `science_doc` 等不在 `CORROBORATED_TYPES` 的
    类型**忽略** `lexical_top1_id`，退化为纯标量 —— 即当前线上行为，本模块对它是
    无操作。
    """
    if not ranked:
        return Decision(False, REASON_EMPTY, None, None)

    top_id, top_score = ranked[0]
    if top_score < min_score:
        return Decision(False, REASON_BELOW_SCORE, top_score, None)

    if chunk_type not in CORROBORATED_TYPES:
        # 纯标量路径（science_doc 走这里）= 线上现状，行为不变
        return Decision(True, REASON_OK, top_score, None)

    # 需要旁证：fail-closed
    if lexical_top1_id is None:
        return Decision(False, REASON_NO_CORROBORATION, top_score, False)
    if lexical_top1_id != top_id:
        return Decision(False, REASON_DISAGREE, top_score, False)
    return Decision(True, REASON_OK, top_score, True)
