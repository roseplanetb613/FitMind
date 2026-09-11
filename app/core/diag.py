# -*- coding: utf-8 -*-
"""降级可观测计数（单源）。

**为什么不改降级策略**：`except Exception: pass` 是有意设计——图谱/记忆/检索引擎是
可选依赖，失败不得影响主链路（见各处 docstring）。把它改成抛异常会破坏这个语义。

**为什么仍需本模块**：静默的代价已经实证——
  · `_PART2MUSCLE["胸"]` 误写 `pectoralis` → 胸部面板永远"没有记录"，无人察觉
  · `muscles_of_exercises` 缺空格归一 → 整条 训练记录→肌肉 链路不可用，表面正常
  · `_norm_food` 吞掉食物库构建失败 → 27 个食物/meal 测试**连锁间歇性失败**
三者共同点：失败被吞，功能整个不可用，而外部看起来"一切正常"。
本模块只做一件事：**让降级可查**（计数 + 经 /health 暴露），不改控制流。

调用约定：只在"静默失败会污染用户可见数据"的路径上打点，不要无差别铺开。
"""
from __future__ import annotations
from collections import Counter

_COUNTS: Counter = Counter()


def bump(key: str, n: int = 1) -> None:
    """记一次降级。**绝不抛异常**——它本身就在异常路径上被调用。"""
    try:
        _COUNTS[key] += n
    except Exception:
        pass


def counts() -> dict:
    """当前降级计数快照 {key: 次数}。"""
    return dict(_COUNTS)


def reset() -> None:
    """清空（测试用）。"""
    _COUNTS.clear()
