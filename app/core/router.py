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
from app.core.vocab import (                       # 记忆/档案查询词表单源（见 vocab.py）
    _MEM_ADVICE_KW, _MEM_ASPECT_KW, _MEM_DOMAIN_KW, _MEM_PAST_KW,
    _MEM_ASK_KW, _MEM_SELF_KW, _MEM_WHEN_KW, _MEM_WHERE_FRAME_KW,
    _MEM_WHERE_SELF_KW, is_profile_query)


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
# 修复：把「自指 + 时间/次数疑问 + 记录域 + 已然体」句式提升为确定性 L0 级直通
# （不经 L1/L2，防被 LLM 判 unknown 转成反问）。判定从严：建议型/动作型/通识型
# 问法一律不碰（宁缺毋滥）。
# 词表已集中到 app/core/vocab.py（单源，2026-09-11 收口）——此前与 llm._RULES 的
# 记忆查询行平行维护，修"疲劳依据追问"时必须同时改两处才生效。**加词只改 vocab**。


def is_memory_query(text: str) -> bool:
    """用户**自己的记录**查询（何时/几次练过什么/何时受过伤/练的哪里）——从严判定。"""
    t = text or ""
    if any(k in t for k in _MEM_ADVICE_KW):
        return False
    if any(k in t for k in _MEM_WHERE_SELF_KW):
        return True
    if (any(k in t for k in _MEM_WHERE_FRAME_KW)
            and any(k in t for k in ("什么", "哪些", "哪里"))
            and any(k in t for k in _MEM_DOMAIN_KW)):
        return True
    if not any(k in t for k in _MEM_SELF_KW):
        return False
    # 时间疑问（啥时/哪天/几次）**或**内容疑问（啥/什么/哪些）—— 二者居其一即可。
    # 前者问"什么时候练的"，后者问"练了什么"，都是对**自己记录**的回读。
    if not (any(k in t for k in _MEM_WHEN_KW)
            or any(k in t for k in _MEM_ASK_KW)):
        return False
    if not (any(k in t for k in _MEM_ASPECT_KW)
            or any(k in t for k in _MEM_PAST_KW)):
        return False
    if not any(k in t for k in _MEM_DOMAIN_KW):
        return False
    return not any(k in t for k in _MEM_ADVICE_KW)


def is_profile_update(text: str) -> bool:
    """档案**写入**陈述（改目标/体重/身高…），而非查询。

    与 `is_preference_statement` 同法：判据**复用抽取器**，不另起词表——只有真抽出
    `op=profile` 指令才算数（"我不想受伤"含"想"却抽不出，就不该当档案写入）。

    为什么需要这条门：写入本身由 `memory_extract` 在 agent 入口完成，但**答复**
    来自当时恰好被选中的技能——实测「我想减脂」落 qa 动作库检索答"没有找到相关
    内容"、「把目标改成减脂」更被 plan_edit 接走答"当前计划里没有「目标」这个动作"。
    用户按回复原话说的，系统却接不住自己提的话。

    ⚠ 只认 `op == "profile"`（写一个具体字段值）。不带 `profile_delta`
    （相对量"又瘦了一斤"——无现值锚定时 apply 会跳过不写，那就没有 ack，
    答成空卡片）也不带别的 op（混了偏好/打卡的句子留给原先的路由）。
    ⚠ 建议型否决沿用查询门那一套：-"我体重怎么减"是在问，不是在改。
    """
    t = text or ""
    if any(k in t for k in _MEM_ADVICE_KW):
        return False
    try:
        from app.graph.memory_extract import extract
        cmds = extract(t)
    except Exception:
        return False
    return bool(cmds) and all(c.get("op") == "profile" for c in cmds)


def is_preference_statement(text: str) -> bool:
    """是否"偏好陈述句"（写入），而非知识问答/记忆查询。

    ⚠ **判据必须复用抽取器**，不能自己再列一份"喜欢/讨厌/不想"词表：
      · 只有抽取器真正产出偏好指令时才算数——否则会造出"没有写入却有回执"的
        假确认（"我不想受伤"含"不想"但抽不出任何偏好，不该答"已记下"）；
      · 词表已有单源（memory_extract._LIKE/_DISLIKE，含「不喜欢」⊂「喜欢」
        的最长优先与否定窗口），另起一份必然与其漂移。
    问句由抽取器自身挡掉（"我喜欢的动作有什么"→ _is_question）。
    """
    try:
        from app.graph.memory_extract import extract
        cmds = extract(text)
    except Exception:
        return False
    return bool(cmds) and all(
        str(c.get("op", "")).startswith("preference") for c in cmds)


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
        # L1 状态显式化（2026-09-11）：enabled=true 但 store 为 None（Ollama 不可达）
        # 时 L1 整层空转，此前全静默 → 排查时误以为语义层在参与决策。供 /health 查。
        self.semantic_status = ("disabled" if not self._sem_on
                                else "on" if self._store is not None
                                else "off(unreachable)")

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
        # 档案**写入**优先于**宽词表**的强信号（2026-09-13）：
        # `_RULES` 的 W1 档案行把裸「体重」写成 conf=1.0，于是"把体重改成70kg"
        # 在下面那道 `>= 0.7` 就返回了，标签落成"知识问答"——同一类操作
        # （改目标/改体重/改身高）却出现两种卡片标题。写入能力本身没问题，
        # 只是**答话的技能与标签不对**。
        #
        # ⚠ **guard 例外，不可省**：安全红线不能被档案写入抢走——
        # "我体重70kg，膝盖疼"必须走 guard（实测仍是 guard，见 test_router）。
        # 其余强信号（plan/teach/progress…）与本门天然互斥：那些句式含"计划/
        # 怎么做"等词，会被 `memory_extract._SKIP`/`_is_question` 整句挡在抽取之外
        # → 本门判 False（"我身高175，帮我排个计划"即为该情形）。
        if rule.task_type != "guard" and is_profile_update(text):
            return Classification("profile_edit",
                                  {"query": text, "kind": "profile"},
                                  confidence=0.9)
        if rule.confidence >= 0.7:                        # 强信号直接定（现状）
            return rule
        # 记忆查询句式：L0 字面表外无法表达 kind（L2 tool schema 无该字段）→
        # 确定性直通 qa(kind=memory)，不赌 LLM 分类（避免 unknown→反问死路）
        if is_memory_query(text):
            return Classification("qa", {"query": text, "kind": "memory"},
                                  confidence=0.9)
        # 档案查询句式：与上一门同因——L2 表达不了 kind（tool schema 无该字段），
        # 表外说法会落动作库检索答"没找到"。确定性直通 qa(kind=profile)。
        # 排在记忆查询**之后**：记录型否决门已让两者互斥，但顺序上让更专的先行。
        if is_profile_query(text):
            return Classification("qa", {"query": text, "kind": "profile"},
                                  confidence=0.9)
        # W2：规则级指代澄清（fallback+needs_clarify）不经 L1/L2——
        # 否则 0.3 低置信会被语义层/LLM 转判（#83/#99 实证：clarify 仍变 direct）
        if rule.needs_clarify:
            return rule
        # 偏好陈述句：是记忆**写入**，不是知识问答。走 qa 会拿这句去动作库检索，
        # 而句子里的动作正是用户要避开的 → 命中必然是反答案（实测"我今天不想练
        # 杠铃卧推"被答成"知识问答 · 杠铃 卧推"）。判据复用抽取器，不另起词表。
        #
        # ⚠ **必须排在 needs_clarify 之后**：指代澄清优先。"我喜欢清淡这个"含悬空
        # 指代"这个"，走 clarify 路径才能把"已记下"排在反问之前（O-3，见
        # test_agent_http.test_memory_ack_overrides_clarify）；抢到 preference 会
        # 把标题顶到 ack 前面，破坏那条契约。
        if is_preference_statement(text):
            return Classification("preference", {"query": text}, confidence=0.9)
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