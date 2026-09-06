# -*- coding: utf-8 -*-
"""动作教学：检索动作+处方组次+器械要求（要领文本 RAG 补强，全静默降级）。"""
from __future__ import annotations
import re
from app.skills.base import Skill, SkillResult
from exercise_repo import ExerciseRepo

_EX = None


def _repo():
    global _EX
    if _EX is None:
        _EX = ExerciseRepo()
    return _EX


class TeachSkill(Skill):
    name = "teach"
    description = "动作要领：目标肌群/组次/休息/器械（RAG 扩展位）"
    task_types = ("teach", "fallback")
    _QUES = ("应该怎么做", "应该怎么练", "怎么做", "怎么练", "如何做",
             "如何练", "的做法", "怎么", "如何", "动作")

    def _search(self, query: str, limit: int = 3) -> list:
        repo = _repo()
        if re.search(r"[\u4e00-\u9fff]", query):
            def _norm(s: str) -> str:
                return "".join(s.split())     # 名称带空格（"杠铃 卧推"），归一后子串匹配
            q = query.strip()
            for suff in TeachSkill._QUES:
                if q.endswith(suff) and len(q) > len(suff):
                    q = q[: -len(suff)]
                    break
            if not q:
                q = query.strip()
            nq = _norm(q)
            hits = [e for e in repo.by_id.values()
                    if nq and nq in _norm(e.get("name_zh") or "")]
            hits.sort(key=lambda e: e.get("difficulty") or 9)
            return hits[:limit]
        return repo.search(query, limit=limit)

    def execute(self, ctx, params) -> SkillResult:
        query = str(params.get("query", ""))
        items = []
        for e in self._search(query):
            mus = e.get("muscles_canonical") or {}
            sug = e.get("suggested") or {}
            items.append({
                "id": e["id"],
                "name_zh": e.get("name_zh"),
                "target": mus.get("target"),
                "sets": sug.get("sets"), "reps": sug.get("reps"),
                "rest_sec": sug.get("rest_sec"),
                "equipment": e.get("normalized_equipment"),
                "pattern": e.get("movement_pattern"),
                "cue": f"保持目标肌群发力，按建议 {sug.get('sets')}组×{sug.get('reps')}次，"
                       f"组间休息 {sug.get('rest_sec')}s（通用要领，详见后续 RAG）",
                "rag": False,
            })
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "reason": "动作库未收录，尝试其他说法"},
                               provenance=["teach#exercise_repo.search"])
        self._rag_enrich(ctx, query, items)      # RAG 补强（异常静默，不影响规则结果）
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"teach:{it['name_zh']}" for it in items])

    def _rag_enrich(self, ctx, query: str, items: list) -> None:
        """对前 2 条动作追加 RAG 证据：同族替代 + 要领向量块。全 try/except 静默降级。"""
        try:
            from app.rag import retriever
            from app.rag.store import PgStore
            from app.rag.embedder import OllamaEmbedder
            store, embedder = PgStore(), OllamaEmbedder()
            for it in items[:2]:
                eid = it.get("id")
                if not eid:
                    continue
                ev = {"alternatives": [], "cue": None}
                try:
                    ev["alternatives"] = retriever.graph_family_alternatives(
                        store, eid)
                except Exception:
                    pass
                try:
                    qv = retriever.embed_query(embedder, it.get("name_zh") or query)
                    hits = retriever.vector_search(
                        store, qv, getattr(embedder, "model", "bge-m3"),
                        top_k=1, chunk_types=("exercise_cue",))
                    if hits:
                        ev["cue"] = hits[0]["content"]
                except Exception:
                    pass
                if ev["alternatives"] or ev["cue"]:
                    it["rag_evidence"] = ev
                    it["rag"] = True
        except Exception:
            pass