# -*- coding: utf-8 -*-


def build_default_registry():
    # 延迟导入：skills.base 被 core.registry 导入，模块级 import 会造成循环依赖
    from app.core.registry import SkillRegistry
    from app.skills.guard_skill import GuardSkill
    from app.skills.qa_skill import QaSkill
    from app.skills.plan_skill import PlanSkill
    reg = SkillRegistry()
    reg.register(GuardSkill())
    reg.register(QaSkill())
    reg.register(PlanSkill())
    # S2 起在此注册：qa/teach/plan/progress
    return reg