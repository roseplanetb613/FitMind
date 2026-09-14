# -*- coding: utf-8 -*-
"""偏好陈述句：回执，**不检索**。

缺陷（2026-09-13 用户实测）：「我今天不想练杠铃卧推」走 qa → 动作库检索 →
reply 列出「杠铃 卧推」——**把用户刚说不想练的动作端回给他**。检索式回答对
否定偏好句是**结构性反向**的：句子里的动作正是他要避开的，任何检索命中都必然是
反答案。所以这一支只做回执，正文由 memory_ack 承担。

写入不在这里：`memory_extract.apply_memory_extract` 在 agent 入口已完成落库，
本技能拿到的只是"已经写完了"这件事。**故本技能不产出 data.items**——
若回执没有配上真实的 memory_ack（写入失败/未识别），渲染层会落到空检索兜底，
而不是在这里编一句"已记下"（N-11 ack 双向必达护栏守的就是这条）。
"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult


class PreferenceSkill(Skill):
    name = "preference"
    description = "训练/饮食偏好陈述的回执（写入由 memory_extract 完成，此处不检索）"
    task_types = ("preference",)

    # 图谱不可用时的诚实声明。**不能返回空 data**：那样渲染层只剩标题+来源，
    # 用户看到一张空白卡片，无从判断"记下了"还是"系统坏了"（实测）。
    # 刻意**不带** `empty: True`——那会再叠一句"没有找到相关内容"，与本句语义打架。
    _OFFLINE = "记忆功能暂不可用（图谱离线），这条偏好没有记下。"

    def execute(self, ctx, params) -> SkillResult:
        try:
            from app.graph.memory import MemoryStore
            store = MemoryStore.get()
        except Exception:
            store = None
        if store is None:
            return SkillResult(ok=True, data={"message": self._OFFLINE},
                               provenance=["preference#offline"])
        return SkillResult(ok=True, data={}, provenance=["preference#ack"])
