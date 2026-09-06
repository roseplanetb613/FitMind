# -*- coding: utf-8 -*-
"""技能契约：插拔注册的核心。新能力=新 Skill 子类+一行注册。"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from app.core.intent import Intent
from app.core.session import Session


@dataclass
class SkillResult:
    ok: bool
    data: dict
    provenance: list[str]
    error: str | None = None


@dataclass
class ExecutionContext:
    session: Session
    profile: dict
    mode: str
    skill_log: list


class Skill(ABC):
    name: str = ""
    description: str = ""
    task_types: tuple[str, ...] = ()

    def can_handle(self, intent: Intent) -> bool:
        return intent.task_type in self.task_types

    @abstractmethod
    def execute(self, ctx: ExecutionContext, params: dict) -> SkillResult: ...