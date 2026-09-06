# -*- coding: utf-8 -*-
"""技能注册表：注册/按意图路由。/新增能力=注册新 Skill，核心零改动。"""
from __future__ import annotations
from app.skills.base import Skill


class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"技能已注册: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def by_task(self, task_type: str) -> list[Skill]:
        return [s for s in self._skills.values() if task_type in s.task_types]

    @property
    def names(self) -> list[str]:
        return list(self._skills)