# -*- coding: utf-8 -*-
"""多智能体契约（按需启用，当前仅接口定义）：
AgentSpec = 某个角色的智能体定义（技能子集+模式+模型）；
MessageBus = 多 agent 间消息路由（actor_id 寻址）。
启用条件见设计文档 §7：跨域联动或独立感知能力（video/voice/3d）。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentSpec:
    role: str                      # coach/nutritionist/vision/voice...
    skills: list[str]
    mode: str = "plan_exec"
    models: dict = field(default_factory=dict)   # {"classify":..,"render":..}


@dataclass
class BusMessage:
    to: str                        # actor_id
    frm: str
    payload: dict


class MessageBus(ABC):
    @abstractmethod
    def send(self, msg: BusMessage) -> None: ...

    @abstractmethod
    def route(self) -> None: ...