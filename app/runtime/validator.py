# -*- coding: utf-8 -*-
"""规则结果复核（S3 增强）：所有模式输出前兜底检查。"""
from __future__ import annotations
from app.core.intent import Intent


class RuleValidator:
    def check(self, outcome: dict, intent: Intent) -> dict:
        # 早拦截：技能失败给结构化错误
        if not outcome["ok"]:
            outcome["_error"] = outcome.get("error") or "技能执行失败"
        return outcome