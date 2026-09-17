# -*- coding: utf-8 -*-
"""训练记录读取（图谱侧）—— `progression` 的数据源。

**为什么在图谱读**（2026-09-17 修复）：训练记录的**写入侧只写图谱**
（`MemoryStore.log_event` 的 checkin 事件），而 SQLite 的 `workout_set` /
`diet_log` **没有任何生产写入方**。早先 `progress` 技能与计划的进阶回哺读的是
那张空表，于是恒拿空历史 —— 两个功能静默失效，且降级话术体面
（"暂无训练记录，建议从轻重量开始"），看起来像"用户还没记录"，不像 bug。
详见 `docs/SDD/user-data-domain.md` §6-F1。

**修法是读侧也读图谱**（不动写侧，保持单源）。不要两边都写 ——
那会制造下一个"同一份事实两个家、必然漂移"。

接口与 `app.storage.db.LogStore` 的对应方法**逐字兼容**（`exercise_names()` /
`history()`），所以消费方只换构造、不改逻辑。图谱不可用 → 各方法返回空，
调用方按"无记录"回落（沿用本项目"全降级、绝不抛出"的传统）。
"""
from __future__ import annotations

from app.graph.memory import MemoryStore

# v1 单用户常量（与 memory.MEMORY_USER_ID 同口径）
DEFAULT_USER = "local"


class WorkoutHistory:
    """训练记录读取适配：写侧只写图谱，故读侧也读图谱。"""

    def __init__(self, store: "MemoryStore | None" = None,
                 user_id: str = DEFAULT_USER):
        # 显式传入优先；缺省自取单例（图谱不可用时为 None，不抛）
        self._m = store if store is not None else MemoryStore.get()
        self._uid = user_id or DEFAULT_USER

    @property
    def available(self) -> bool:
        """图谱是否可用。False 时各方法返回空 —— 调用方据此走"无记录"回落。"""
        return self._m is not None

    def exercise_names(self) -> list[str]:
        """记录过的动作标签去重 —— 供与计划里的动作名匹配。"""
        if self._m is None:
            return []
        try:
            return self._m.recorded_exercise_labels(self._uid)
        except Exception:
            return []

    def history(self, exercise: str, top: int = 3) -> list[dict]:
        """某动作的逐组记录（时间升序，最多 top 组）—— `progression` 的输入。

        返回结构与 `LogStore.history()` 一致：`[{weight_kg, reps, rir, date}]`。
        ⚠ 图谱不记 RIR，故 `rir` 恒 0.0（按力竭组估算，方向保守）。
        """
        if self._m is None:
            return []
        try:
            return self._m.exercise_sets(self._uid, exercise, limit=max(1, top))
        except Exception:
            return []
