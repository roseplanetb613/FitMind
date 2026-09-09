# -*- coding: utf-8 -*-
"""闲聊/寒暄技能：问候、自我介绍、道谢、道别等无检索必要的内容。
数据带 message 字段，render 层要求直接友好回应（不臆造数据）。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult

_GREET = ("你好呀！我是 FitMind 健身助手，可以帮你查动作、算饮食、出训练/减脂计划，"
          "也能根据你的训练记录给下一组建议。想从哪开始？")
_ID = "我是 FitMind 的 AI 健身助手——不做诊断，只做数据驱动的运动与饮食参考。"
_THANKS = "不客气！有训练或饮食上的问题随时找我。"
_BYE = "再见，训练顺利！记得热身和拉伸。"
_FALLBACK = ("我在听——想聊训练、饮食还是身体数据？也可以试试问我"
             "“鸡胸肉蛋白质多少”或“帮我制定一日减脂计划”。")


class SmallTalkSkill(Skill):
    name = "smalltalk"
    description = "问候/寒暄/道谢/道别固定应答（render 直接回 message，不检索）"
    task_types = ("smalltalk", "fallback")

    def execute(self, ctx, params) -> SkillResult:
        topic = str(params.get("topic", ""))
        if any(k in topic for k in ("谢谢", "感谢", "多谢")):
            msg = _THANKS
        elif any(k in topic for k in ("再见", "拜拜", "走了", "88")):
            msg = _BYE
        elif any(k in topic for k in ("你是谁", "你叫什么", "介绍一下自己")):
            msg = _ID
        elif any(k in topic for k in ("你好", "您好", "hi", "hello", "嗨", "哈喽")):
            msg = _GREET
        else:
            msg = _FALLBACK
        # 2026-09-08 个性化：记忆档案投影的称呼前缀（无 name 逐字不变）
        name = str(getattr(ctx, "profile", {}).get("name", "") or "").strip()
        if name:
            msg = f"{name}，{msg}"
        return SkillResult(ok=True, data={"message": msg, "topic": topic},
                           provenance=["smalltalk#default"])