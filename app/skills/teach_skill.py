# -*- coding: utf-8 -*-
"""动作教学：检索动作+处方组次+器械要求（要领文本位预留 RAG 升级）。"""
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
            q = query.strip()
            for suff in TeachSkill._QUES:
                if q.endswith(suff) and len(q) > len(suff):
                    q = q[: -len(suff)]
                    break
            if not q:
                q = query.strip()
            hits = [e for e in repo.by_id.values()
                    if q in (e.get("name_zh") or "")]
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
                "name_zh": e.get("name_zh"),
                "target": mus.get("target"),
                "sets": sug.get("sets"), "reps": sug.get("reps"),
                "rest_sec": sug.get("rest_sec"),
                "equipment": e.get("normalized_equipment"),
                "pattern": e.get("movement_pattern"),
                "cue": f"保持目标肌群发力，按建议 {sug.get('sets')}组×{sug.get('reps')}次，"
                       f"组间休息 {sug.get('rest_sec')}s（通用要领，详见后续 RAG）",
            })
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "reason": "动作库未收录，尝试其他说法"},
                               provenance=["teach#exercise_repo.search"])
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"teach:{it['name_zh']}" for it in items])