# -*- coding: utf-8 -*-
"""执行模式动态变换 + 意图三层编排（规则→语义→LLM→arbitrate 单点）。"""
from __future__ import annotations
import functools
import json
from enum import Enum
from pathlib import Path
from app.core.intent import Intent
from app.core.llm import Classification, LLMProvider, StubProvider
from app.core.arbitrate import arbitrate
from app.core.semantic import ExemplarStore, _params_for, map_conf


class Mode(Enum):
    DIRECT = "direct"
    REACT = "react"
    PLAN_EXEC = "plan_exec"
    REWOO = "rewoo"


CONFIG = Path(__file__).resolve().parent.parent / "config" / "router_config.json"

_MODE_MAP = {"direct": Mode.DIRECT, "react": Mode.REACT,
             "plan_exec": Mode.PLAN_EXEC, "rewoo": Mode.REWOO}


@functools.lru_cache(maxsize=1)
def load_router_config() -> dict:
    """路由配置（进程内缓存：此前 route() 每轮对话都 open+parse 一次）。"""
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def route(complexity: str, task_type: str = "") -> Mode:
    cfg = load_router_config()
    ov = cfg.get("overrides", {}).get(task_type)
    if ov:
        return _MODE_MAP[ov]
    return _MODE_MAP[cfg["default"].get(complexity, "direct")]


# ------------------------------------------------------------ 记忆查询句式直通
# 缺陷（CLI 实测 2026-09-10）：kind=memory 只能由 L0 的**字面词表**产出
# （训练记录/练过什么/打卡记录/训练日志/最近练/我的偏好…），而 L2 的 tool schema
# 里根本没有 kind 字段——表外问法**结构性**到不了 qa._memory，直接落动作库检索：
#   "我啥时练的核心" → qa#exercise_repo.search → 空 → "没有找到相关内容"
# 而同一句只要带上 kind=memory 就能列出真实记录。修复：把「自指 + 时间/次数疑问
# + 记录域 + 已然体」句式提升为确定性 L0 级直通（不经 L1/L2，防被 LLM 判 unknown
# 转成反问）。判定从严：建议型/动作型/通识型问法一律不碰（宁缺毋滥）。
_MEM_SELF_KW = ("我", "俺", "自己")
_MEM_WHEN_KW = ("啥时", "啥时候", "什么时候", "哪天", "几号", "多久", "几次",
                "上次", "上回", "最近", "以前", "之前")
_MEM_ASPECT_KW = ("了", "过", "的")          # 已然体标记
_MEM_PAST_KW = ("上次", "上回", "以前", "之前")   # 显式过去锚（"什么时候"式问句无体标记）
_MEM_DOMAIN_KW = ("练", "训练", "打卡", "记录", "日志", "健身", "运动", "伤")
_MEM_ADVICE_KW = ("比较好", "合适", "建议", "应该", "推荐", "最好", "怎样",
                  "怎么", "如何", "要不要", "能不能", "可以吗", "好吗", "有用")


def is_memory_query(text: str) -> bool:
    """用户**自己的记录**查询（何时/几次练过什么/何时受过伤）——从严判定。"""
    t = text or ""
    if not any(k in t for k in _MEM_SELF_KW):
        return False
    if not any(k in t for k in _MEM_WHEN_KW):
        return False
    if not (any(k in t for k in _MEM_ASPECT_KW)
            or any(k in t for k in _MEM_PAST_KW)):
        return False
    if not any(k in t for k in _MEM_DOMAIN_KW):
        return False
    return not any(k in t for k in _MEM_ADVICE_KW)


class RouteClassifier:
    """三层分类器：L0 规则(DIRECT，纯规则可复现) → L1 语义例句库 → L2 LLM
    → arbitrate 单点。语义层加载失败静默跳过；stub => 与现状一致。"""

    _SEM_DEFAULTS = {"enabled": True, "adopt_sim": 0.62,
                     "guard_adopt_sim": 0.55, "sim_floor": 0.40, "sim_ceil": 0.75}

    def __init__(self, llm: LLMProvider):
        self._llm = llm
        self._rules = StubProvider()              # L0 纯规则（与 classify 兜底同源）
        sem = dict(self._SEM_DEFAULTS)
        sem.update(load_router_config().get("semantic") or {})
        self._sem_on = bool(sem.get("enabled", True))
        self._adopt = float(sem.get("adopt_sim", 0.62))
        self._guard_adopt = float(sem.get("guard_adopt_sim", 0.55))
        # 线上紧急开关：semantic.enabled=false 时强制置 None（跳过 L1）
        self._store = ExemplarStore.get() if self._sem_on else None

    def intent_for(self, text: str, profile: dict | None = None) -> Intent:
        c = self._classify(text, profile)         # 三层决策
        # 规则复杂度信号：更复杂任务类型给更重模式
        complexity = {"plan": "complex", "progress": "medium",
                      "teach": "medium"}.get(c.task_type, "simple")
        if "请" in text and "计划" in text and "一周" in text:
            complexity = "batch"
        return Intent(task_type=c.task_type, params=c.params, raw_text=text,
                      complexity=complexity, confidence=c.confidence,
                      needs_clarify=c.needs_clarify)

    def _classify(self, text, profile) -> Classification:
        rule = self._rules.classify(text, profile)        # L0 纯规则
        if rule.confidence >= 0.7:                        # 强信号直接定（现状）
            return rule
        # 记忆查询句式：L0 字面表外无法表达 kind（L2 tool schema 无该字段）→
        # 确定性直通 qa(kind=memory)，不赌 LLM 分类（避免 unknown→反问死路）
        if is_memory_query(text):
            return Classification("qa", {"query": text, "kind": "memory"},
                                  confidence=0.9)
        # W2：规则级指代澄清（fallback+needs_clarify）不经 L1/L2——
        # 否则 0.3 低置信会被语义层/LLM 转判（#83/#99 实证：clarify 仍变 direct）
        if rule.needs_clarify:
            return rule
        c = self._sem_or_llm(text, rule)
        # W4 兜底：最终仍需 clarify/低置信 → 意图归一化改写 → 重跑 L0/L1（仅兜底触发）
        if c.needs_clarify or c.confidence < 0.4:
            upgraded = self._intent_normalize_fallback(text, rule)
            if upgraded is not None:
                return upgraded
        return c

    def _sem_or_llm(self, text, rule) -> Classification:
        """L1 语义例句库 → 未达阈值则 L2 纯 LLM → arbitrate 单点 → 安全偏置。"""
        if self._store is not None:
            sem_tt, sim = self._store.match(text)          # L1 语义例句库
            thr = (self._guard_adopt if sem_tt == "guard"
                   else self._adopt)
            if sim >= thr:                                 # 语义命中 → 采纳
                return Classification(sem_tt, _params_for(sem_tt, text),
                                      confidence=map_conf(sim))
        # L2 纯 LLM + 仲裁（store 缺失或语义未达阈值均进入）
        if self._llm and hasattr(self._llm, "classify_llm"):
            try:
                out = self._llm.classify_llm(text)
                c = arbitrate(out.task_type, out.confidence, out.params, rule)
                return self._guard_bias(text, c)
            except Exception:
                return rule                            # 幻觉/失败 → 规则兜底
        return rule                                        # stub 环境 = 现状

    def _guard_bias(self, text, c: Classification) -> Classification:
        """plan 采纳前安全偏置：LLM 判 plan 且语义例句库命中 guard（≥guard_adopt_sim）
        → 改判 guard（药物/慢病/孕产/极端行为多被 LLM 误投 plan，安全红线优先）。"""
        if c.task_type != "plan" or self._store is None:
            return c
        try:
            sem_tt, sim = self._store.match(text)
            if sem_tt == "guard" and sim >= self._guard_adopt:
                return Classification("guard", {"signal": text},
                                      confidence=max(c.confidence, 0.9))
        except Exception:
            pass
        return c

    def _intent_normalize_fallback(self, text, rule) -> Classification | None:
        """意图归一化兜底：改写原句 → 重跑 L0/L1 一次 → 命中即采用并写回图谱。
        任何失败 → None（保持原 clarify/低置信结果，行为不退化为更差）。"""
        try:
            from app.core.normalize import Normalizer
            out = Normalizer(self._llm).normalize(text, "intent")
            if not out:
                return None
            std = str(out["standard_text"])
            r2 = self._rules.classify(std, {})             # 重跑 L0
            if r2.confidence >= 0.7:
                return self._adopt_normalized(text, std, r2.task_type,
                                              out.get("confidence", 0.0), r2)
            if self._store is not None:
                sem_tt, sim = self._store.match(std)        # 重跑 L1
                thr = (self._guard_adopt if sem_tt == "guard"
                       else self._adopt)
                if sim >= thr:
                    return self._adopt_normalized(
                        text, std, sem_tt, out.get("confidence", 0.0),
                        Classification(sem_tt, _params_for(sem_tt, std),
                                       confidence=map_conf(sim)))
            return None
        except Exception:
            return None

    @staticmethod
    def _adopt_normalized(orig: str, std: str, task_type: str,
                          conf: float, adopted: Classification) -> Classification:
        """采纳归一化结果：写回图谱（Alias→StandardTerm→IMPLIES_INTENT）+ 留痕。
        采用改写词作为 query（动作/语义匹配用标准化说法更高频）。"""
        try:
            from app.graph.store import GraphStore
            g = GraphStore.get()
            if g is not None:
                g.upsert_intent_rewrite(orig, std, task_type, conf,
                                        source="llm_auto")
        except Exception:
            pass
        return adopted