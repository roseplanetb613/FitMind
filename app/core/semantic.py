# -*- coding: utf-8 -*-
"""L1 语义意图层：例句库 + bge-m3 最近邻（零生成 = 零幻觉）。
任何一步失败都静默降级（get()→None / match→("",0.0)），由上层落到 LLM/规则。"""
from __future__ import annotations
import json
import math
from pathlib import Path

EXEMPLARS = Path(__file__).resolve().parent.parent / "config" / "intent_exemplars.json"
SIM_FLOOR, SIM_CEIL = 0.40, 0.75      # 待标定（calibrate_intent_sim.py 产出终值）


def map_conf(sim: float) -> float:
    """相似度 → confidence（接 arbitrate 三档阈值）。"""
    return max(0.0, min(1.0, (sim - SIM_FLOOR) / (SIM_CEIL - SIM_FLOOR)))


def _params_for(task_type: str, text: str) -> dict:
    if task_type == "guard":
        return {"signal": text}
    if task_type == "smalltalk":
        return {"topic": text}
    if task_type == "plan":
        import split_cycle
        return split_cycle.extract_plan_params(text)
    return {"query": text}


class ExemplarStore:
    _instance = None

    def __init__(self, items, matrix, embedder):
        self._items, self._matrix, self._embedder = items, matrix, embedder

    @classmethod
    def get(cls):
        if cls._instance is not None:
            return cls._instance
        try:
            data = json.loads(EXEMPLARS.read_text(encoding="utf-8"))
            items = [(e["text"], e["task_type"]) for e in data["exemplars"]]
            from app.rag.embedder import OllamaEmbedder
            emb = OllamaEmbedder()
            if not emb.healthy():
                return None
            matrix = emb.embed([t for t, _ in items])
            cls._instance = cls(items, matrix, emb)
        except Exception:
            return None
        return cls._instance

    def match(self, text: str) -> tuple[str, float]:
        try:
            q = self._embedder.embed_one(text)
            best, bi = -1.0, -1
            for i, v in enumerate(self._matrix):
                s = _cos(q, v)
                if s > best:
                    best, bi = s, i
            return (self._items[bi][1], best) if bi >= 0 else ("", 0.0)
        except Exception:
            return ("", 0.0)


def _cos(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)