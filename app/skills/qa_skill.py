# -*- coding: utf-8 -*-
"""知识问答：动作/食物检索，结果带来源标注（不发 LLM，纯规则）。"""
from __future__ import annotations
import re
from app.skills.base import Skill, SkillResult
from exercise_repo import ExerciseRepo
from foods_repo import FoodsRepo

_EX = None
_FR = None


def repos():
    global _EX, _FR
    if _EX is None:
        _EX = ExerciseRepo()
        _FR = FoodsRepo()
    return _EX, _FR


class QaSkill(Skill):
    name = "qa"
    description = "动作/食物/知识检索问答"
    task_types = ("qa", "fallback")

    def execute(self, ctx, params) -> SkillResult:
        ex, fr = repos()
        query = str(params.get("query", "")).strip()
        kind = params.get("kind")
        if kind == "food" or (kind != "exercise" and self._sounds_food(query)):
            return self._foods(fr, query)
        return self._exercises(ex, query)

    def _foods(self, fr, query: str) -> SkillResult:
        items = []
        # 整句无命中时，回退到抽取具体食物词（"鸡胸肉蛋白质多少"→"鸡胸"），
        # 优于泛化的营养素词"蛋白质"
        search = query
        if not fr.search(query, limit=1):
            kw = sorted((k for k in self._FOOD_CONCRETE if k in query),
                        key=len, reverse=True)
            if kw:
                search = kw[0]
        for f in fr.search(search, limit=5):
            p = f.get("per_100g") or {}
            items.append({
                "name": f.get("name_zh") or f.get("name"),
                "per_100g": {"calories_kcal": p.get("calories_kcal"),
                             "protein_g": p.get("protein_g"),
                             "fat_g": p.get("fat_g"),
                             "carbs_g": p.get("carbs_g")},
                "source": f.get("source"),
            })
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "reason": "未检索到食物"},
                               provenance=["qa#foods_repo.search"])
        src = [f"{it['source']}:{i}{it['name']}" for i, it in enumerate(items)]
        return SkillResult(ok=True, data={"items": items},
                           provenance=src[:3])

    def _exercises(self, ex, query: str) -> SkillResult:
        # 优先精确的中文动作名匹配（exercise_repo.search 仅搜英文名），
        # 用 query 在 name_zh 上做子串检索，否则回退英文搜索
        if re.search(r"[\u4e00-\u9fff]", query):
            names = [(i, r) for i, r in ex.by_id.items()
                     if query.strip().lower() in (r.get("name_zh") or "").lower()]
            names.sort(key=lambda kv: kv[1].get("difficulty", 9))
            matched = [r for _, r in names[:5]]
        else:
            matched = ex.search(query, limit=5)
        items = []
        for e in matched:
            sug = e.get("suggested") or {}
            items.append({"id": e["id"], "name_zh": e.get("name_zh"),
                          "difficulty": e.get("difficulty"),
                          "pattern": e.get("movement_pattern"),
                          "equipment": e.get("normalized_equipment"),
                          "sets": sug.get("sets"), "reps": sug.get("reps"),
                          "rest_sec": sug.get("rest_sec")})
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "reason": "未检索到动作"},
                               provenance=["qa#exercise_repo.search"])
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"ex:{it['id']}" for it in items])

    @staticmethod
    def _sounds_food(q: str) -> bool:
        return any(k in q for k in QaSkill._FOOD_KW)

    # 可识别为"食物提问"的全部触发词；其中 _FOOD_CONCRETE 为具体食材词，
    # 用于无命中时的搜索回退（优于"蛋白质"这类泛化营养素词）
    _FOOD_KW = ("鸡胸", "鸡蛋", "牛奶", "牛肉", "鱼", "米饭", "蛋白质",
                "碳水", "脂肪", "卡路里", "热量", "食物")
    _FOOD_CONCRETE = ("鸡胸", "鸡蛋", "牛奶", "牛肉", "鱼", "米饭")