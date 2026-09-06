# -*- coding: utf-8 -*-
"""风险守卫：规则拦截最优先，任何模式不得绕过。非诊断提示。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult
import screening

# 症状级信号（口语表达+常见损伤缩写，命中即黄色拦截+咨询医生；绝不误报绿灯）
SYMPTOMS = ("疼", "痛", "眩晕", "头晕", "胸闷", "发烧", "发热",
            "骨折", "断了", "扭伤", "脱臼", "麻木", "麻", "刺痛", "发炎",
            "拉伤", "半月板", "十字韧带", "韧带", "tfcc", "acl", "mcl")


class GuardSkill(Skill):
    name = "guard"
    description = "风险信号拦截：按已报疾病/损伤关键词命中禁忌表 → 拦截+转诊提示"
    task_types = ("guard", "fallback")

    def execute(self, ctx, params) -> SkillResult:
        signal = str(params.get("signal", ""))
        outcome = {"blocked": False, "level_label": "green", "advice": "",
                   "blocks": []}
        # condition_zh 是长描述（如"腰椎间盘突出/坐骨神经痛（活动期或明显症状）"），
        # 取首个"/"前核心病名词应对信号做子串匹配
        hits = []
        for cond in self._conditions():
            head = cond.split("（")[0].split("/")[0]
            if head and head in signal:
                hits.append(cond)
        if hits:
            out = screening.plan_check(hits, ["push", "pull", "squat", "hinge",
                                              "lunge", "core", "carry", "stretch"])
            # plan_check 返回 level/level_label/blocks/warnings（无 advice 键，需合成）
            advice = "；".join(out["warnings"]) or (
                "已报禁忌疾病：建议在医生/康复师评估后再安排训练"
                if out["level"] != "green" else "")
            outcome = {"blocked": out["level"] != "green",
                       "level_label": out["level_label"], "advice": advice,
                       "blocks": out["blocks"]}
        elif any(k in signal.lower() for k in SYMPTOMS):
            outcome = {"blocked": True, "level_label": "黄色",
                       "advice": "出现症状建议暂停训练并咨询医生，切勿硬撑", "blocks": []}
        src = ["guard#screening.plan_check"] if hits else ["guard#keyword"]
        return SkillResult(ok=True, data=outcome, provenance=src)

    @staticmethod
    def _conditions() -> list[str]:
        return [c["condition_zh"] for c in screening.CONDITIONS]