# -*- coding: utf-8 -*-


def build_default_registry():
    # 延迟导入：skills.base 被 core.registry 导入，模块级 import 会造成循环依赖
    from app.core.registry import SkillRegistry
    from app.skills.guard_skill import GuardSkill
    reg = SkillRegistry()
    reg.register(GuardSkill())
    # S2 起在此注册：qa/teach/plan/progress
    return reg