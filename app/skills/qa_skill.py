# -*- coding: utf-8 -*-
"""知识问答：动作/食物检索，结果带来源标注（不发 LLM，纯规则；科学语境补 RAG 块）。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult


def repos():
    """共享单例（app.runtime.repos）：避免与 pipeline/teach 各自装配大表。"""
    from app.runtime.repos import exercise_repo, foods_repo
    return exercise_repo(), foods_repo()


class QaSkill(Skill):
    name = "qa"
    description = "动作/食物/知识检索问答"
    task_types = ("qa", "fallback")

    # 科学/医学语境触发词 → 追加 RAG 知识块（training-science / sports-medicine）
    _SCIENCE_KW = ("强度", "RPE", "心率", "渐进", "免疫", "肌肉生长",
                   "营养", "增肌", "减脂", "代谢", "激素", "运动科学",
                   "科学", "原理", "机制", "恢复")

    def execute(self, ctx, params) -> SkillResult:
        ex, fr = repos()
        query = str(params.get("query", "")).strip()
        kind = params.get("kind")
        if kind == "food" or (kind != "exercise" and self._sounds_food(query)):
            res = self._foods(fr, query)
        else:
            res = self._exercises(ex, query)
        if res.ok and res.data.get("items"):
            self._rag_science(res.data["items"], query)
        return res

    def _foods(self, fr, query: str) -> SkillResult:
        items = []
        # 整句无命中时，回退到抽取具体食物词（"鸡胸肉蛋白质多少"→"鸡胸"），
        # 优于泛化的营养素词"蛋白质"
        search = query
        for _k, _v in self._FOOD_ALIASES.items():   # 口语词归一（子串替换）
            if _k in search:
                search = search.replace(_k, _v)
                break
        if not fr.search(search, limit=1):
            kw = sorted((k for k in self._FOOD_CONCRETE if k in search),
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
        # 中文检索已下沉 exercise_repo.search_zh（qa/teach 单源）：
        # 复合切分+修饰剥离+别名归一+双向匹配，装配期预计算归一名。
        matched = ex.search_zh(query, limit=5)
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

    def _rag_science(self, items: list, query: str) -> None:
        """科学/医学语境 → 追加 top1 science_doc 知识块。全 try/except 静默降级。"""
        if not any(k in query for k in QaSkill._SCIENCE_KW):
            return
        try:
            from app.rag import retriever
            from app.rag.store import PgStore
            from app.rag.embedder import OllamaEmbedder
            store, embedder = PgStore(), OllamaEmbedder()
            qv = retriever.embed_query(embedder, query)
            hits = retriever.vector_search(
                store, qv, getattr(embedder, "model", "bge-m3"),
                top_k=1, chunk_types=("science_doc",))
        except Exception:
            return
        if not hits:
            return
        items.append({"name": "知识块(RAG)", "content": hits[0]["content"],
                      "pending_review": True, "rag": True})

    @staticmethod
    def _sounds_food(q: str) -> bool:
        return any(k in q for k in QaSkill._FOOD_KW)

    # 可识别为"食物提问"的全部触发词；其中 _FOOD_CONCRETE 为具体食材词，
    # 用于无命中时的搜索回退（优于"蛋白质"这类泛化营养素词）
    _FOOD_KW = ("鸡胸", "鸡蛋", "牛奶", "牛肉", "鱼", "米饭", "蛋白质",
                "碳水", "脂肪", "卡路里", "热量", "食物")
    _FOOD_CONCRETE = ("鸡胸", "鸡蛋", "牛奶", "牛肉", "鱼", "米饭",
                      "虾仁", "虾", "豆腐", "红薯", "燕麦", "香蕉", "苹果",
                      "牛油果", "三文鱼", "豆浆", "面条", "鸡腿", "酸奶",
                      "蛋白粉",
                      "乳清")
    _FOOD_ALIASES = {"蛋白质粉": "蛋白粉"}   # 口语词↔库内词