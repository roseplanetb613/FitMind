# -*- coding: utf-8 -*-


def build_default_registry():
    # 延迟导入：skills.base 被 core.registry 导入，模块级 import 会造成循环依赖
    from app.core.registry import SkillRegistry
    reg = SkillRegistry()
    # S2 起在此注册：guard/qa/teach/plan/progress
    return reg