# -*- coding: utf-8 -*-
"""规则结果复核：所有模式输出前兜底检查。"""
from __future__ import annotations
from app.core.intent import Intent


class RuleValidator:
    def check(self, outcome: dict, intent: Intent) -> dict:
        if not outcome["ok"]:
            outcome["_error"] = outcome.get("error") or "技能执行失败"
        # 计划类：红灯仍给输出，但抬高警告
        data = outcome.get("data") or {}
        if "screening" in data and data["screening"].get("level") == "red":
            outcome["_warnings"] = data["screening"].get("warnings", [])
        return outcome