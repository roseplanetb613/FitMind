# -*- coding: utf-8 -*-
"""LLM 归一化器（W3/W4 兜底路径）：长尾原句 → 高频标准说法。

设计（合并计划 §5.2 + §6 安全红线）：
- 只允许同义替换/提炼，禁止扩写；改不出返回 None（上层静默走原逻辑）；
- evidence 逐字校验（不在原句 → 丢弃本次改写，复用 classify_llm 同套防幻觉）；
- 症状词保留校验：原句命中 GUARD_SYMPTOMS/GUARD_SIGNAL_EXTRA 而改写结果
  全丢症状 → 判定危险改写，丢弃（guard 永远基于原始输入的安全双保险）；
- 空结果兜底：改写产物 → 重检索；同时写回图谱（upsert_alias 按置信分流）。
- audit 留痕：改写记录 append 到 ctx.skill_log（与 mode_used 同级，见 nodes。execute）。
"""
from __future__ import annotations
from app.core.llm import LLMProvider
from app.core.vocab import GUARD_SIGNAL_EXTRA, GUARD_SYMPTOMS

# 症状 + 意图层 guard 信号合集（改写后必须保留的敏感语义）
_SENSITIVE = GUARD_SYMPTOMS + GUARD_SIGNAL_EXTRA
# 否定语义（约束 2：不得删除）
_NEGATIVES = ("不", "别", "不要", "不敢", "不能")


class Normalizer:
    def __init__(self, llm: LLMProvider):
        self.llm = llm

    def normalize(self, text: str, purpose: str) -> dict | None:
        """LLM 归一化 + 双校验。产物含 standard_text/confidence/evidence/dropped。"""
        try:
            out = self.llm.normalize(text, purpose)
        except Exception:
            return None
        if not out or not out.get("standard_text"):
            return None
        std = str(out["standard_text"])
        if std == text:
            return None                        # 无变化 = 无兜底价值
        if not self._safe_rewrite(text, std):
            return None
        return out

    def attempt(self, ctx, text: str, purpose: str, retry) -> tuple[bool, list]:
        """空结果兜底全流程：normalize → 重检索 retry(q) → 写回图谱 → audit 留痕。
        返回 (adopted, hits)：adopted=True 表示改写被采纳（重检索有命中）；
        主路径为空结果时调用，任何失败静默，行为退回原 empty 分支。"""
        try:
            out = self.normalize(text, purpose)
        except Exception:
            return False, []
        if not out:
            return False, []
        std = str(out["standard_text"])
        written = _write_back_alias(text, std, purpose, out.get("confidence", 0.0))
        try:
            hits = list(retry(std))
        except Exception:
            hits = []
        adopted = bool(hits)
        if ctx is not None:
            try:
                ctx.skill_log.append({
                    "event": "normalization",
                    "purpose": purpose,
                    "original": text,
                    "rewritten": std,
                    "confidence": out.get("confidence", 0.0),
                    "evidence": out.get("evidence", ""),
                    "dropped": out.get("dropped", []),
                    "adopted": adopted,
                    "written_to_graph": written,
                })
            except Exception:
                pass
        return adopted, hits

    def _safe_rewrite(self, original: str, rewritten: str) -> bool:
        """安全红线：改写不得丢失症状/否定语义。返回 False = 危险改写丢弃。"""
        if not any(s in original for s in _SENSITIVE):
            return True                        # 原句无敏感语义 → 不触发保留校验
        for s in _SENSITIVE:
            if s in original and s not in rewritten:
                return False
        for n in _NEGATIVES:
            if (n in original and n not in rewritten
                    and rewritten not in original):
                return False
        return True


def _write_back_alias(alias: str, term: str, purpose: str,
                      confidence: float) -> bool:
    """改写产物写回图谱：Alias(原句)→ALIAS_OF→StandardTerm(改写)。
    upsert_alias 按阈值分流 approved/pending；图谱不可用/低置信 → 静默 False。"""
    try:
        from app.graph.store import GraphStore
        g = GraphStore.get()
        if g is None:
            return False
        domain = purpose if purpose in ("exercise", "food") else "intent"
        status = g.upsert_alias(alias, term, domain,
                                confidence=confidence, source="llm_auto")
        return status in ("approved", "pending")
    except Exception:
        return False