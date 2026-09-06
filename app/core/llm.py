# -*- coding: utf-8 -*-
"""LLM 抽象层：供应商可插拔；S1–S5 用 StubProvider（确定可复现）。"""
from __future__ import annotations
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from app.core.intent import Intent, PlannedCall


@dataclass
class Classification:
    task_type: str
    params: dict
    complexity: str = "simple"


# 规则意图词典（stub 分类用；真实 provider 接管后可删）
_RULES = [
    (("计划", "练什么", "怎么安排", "一周"), "plan",
     lambda t, kw: {"days": 1}),
    (("怎么做", "要领", "动作教学", "怎么练"), "teach",
     lambda t, kw: {"query": t}),
    (("下一组", "加重量", "减重量", "加几公斤", "减载"), "progress",
     lambda t, kw: {"query": t}),
    (("腰", "膝", "伤", "痛", "禁忌", "能不能练"), "guard",
     lambda t, kw: {"signal": t}),
]


class LLMProvider(ABC):
    is_stub: bool = False

    @abstractmethod
    def classify(self, text: str, profile: dict | None = None) -> Classification:
        """意图+复杂度分类（低成本）。"""

    @abstractmethod
    def plan(self, task: str, available: list[str]) -> list[PlannedCall]:
        """PlanExec/ReWOO：一次产出全部技能调用。"""

    @abstractmethod
    def think(self, context: list[dict], available: list[str]) -> PlannedCall:
        """ReAct：基于上下文决定下一步技能调用。"""

    @abstractmethod
    def render(self, structured: dict, tone: str = "coach") -> str:
        """把结构化结果渲染成自然语言。"""


class StubProvider(LLMProvider):
    """规则实现：分类/计划/渲染全确定，供测试与无 LLM 环境。"""
    is_stub: bool = True

    def __init__(self, lang: str = "zh"):
        self.lang = lang
        self.calls: list[str] = []

    def classify(self, text, profile=None) -> Classification:
        self.calls.append("classify")
        for kws, tt, build in reversed(_RULES):
            if any(k in text for k in kws):
                return Classification(tt, build(text, kws))
        return Classification("qa", {"query": text})   # 默认问答

    def plan(self, task, available) -> list[PlannedCall]:
        self.calls.append("plan")
        # task 为 task_type（"plan"）或含计划关键词 → 调 plan 技能
        if "plan" in available and (task == "plan" or any(
                k in task for k in ("计划", "训练", "减脂", "维持"))):
            return [PlannedCall("1", "plan", {"goal": task})]
        return [PlannedCall("1", "qa", {"query": task})]

    def think(self, context, available) -> PlannedCall:
        self.calls.append("think")
        last = context[-1] if context else {}
        if "result" in last and last["result"].get("ok"):
            return PlannedCall("end", "_done", {})   # 结束标记
        first = context[0] if context else {}
        tt = first.get("task_type")
        p0 = first.get("params") or {}
        if tt in ("plan", "teach", "progress"):
            return PlannedCall("1", tt, p0)
        return PlannedCall("1", "qa", {"query": p0.get("query", "追问")})

    def render(self, structured, tone="coach") -> str:
        self.calls.append("render")
        parts = [str(structured.get("title", "回答"))]
        data = structured.get("data") or {}
        if data:
            for k in ("macros", "training", "meals"):
                if k in data:
                    parts.append(f"[{k}]")
        for it in structured.get("items", []):
            parts.append(f"· {it}")
        for s in structured.get("sources", []):
            parts.append(f"来源: {s}")
        return "\n".join(parts)


# ---------------------------------------------------------------- DeepSeek
class DeepSeekProvider(LLMProvider):
    """真实 LLM（DeepSeek，OpenAI 兼容）实现。
    决策路径原则不变：guard/plan/progress 数值决策锁定规则链；本类只做
    意图/计划分类与渲染。调用或解析失败由调用方回落（见 build_provider 与图 render）。"""

    is_stub: bool = False
    BASE_URL = "https://api.deepseek.com"

    def __init__(self, key: str | None = None, model: str = "deepseek-chat",
                 temperature: float = 0.2):
        self.key = key or os.environ.get("DEEPSEEK_API_KEY") or ""
        if not self.key:
            raise ValueError("DEEPSEEK_API_KEY 未设置：无法构造 DeepSeekProvider")
        from langchain_openai import ChatOpenAI
        self.model_name = model
        self._llm = ChatOpenAI(model=model, api_key=self.key,
                               base_url=self.BASE_URL, temperature=temperature)
        self._fallback = StubProvider()          # 规则回落（分类兜底）
        self.calls: list[str] = []

    # ---- 工具：单次带 JSON 约束的调用 ----
    def _invoke_json(self, system: str, user: str, reply_key: str):
        llm = self._llm.bind(response_format={"type": "json_object"})
        resp = llm.invoke([("system", system), ("user", user)])
        text = (resp.content or "") if hasattr(resp, "content") else str(resp)
        data = self._parse_json(text)
        if reply_key not in data:
            raise ValueError(f"LLM 返回缺少键 {reply_key}: {text[:200]}")
        return data

    @staticmethod
    def _parse_json(text: str) -> dict:
        import json
        s = text.strip()
        s = s[s.find("{"):s.rfind("}") + 1] if "{" in s else s
        return json.loads(s)

    # ---- LLMProvider 接口 ----
    def classify(self, text, profile=None) -> Classification:
        self.calls.append("classify")
        sys_p = ("你是 FitMind 健身助手的意图路由器。仅输出一个 JSON 对象，不要任何其他内容。"
                 "task_type ∈ {qa, teach, plan, progress, guard, fallback}；"
                 "params 是简要参数字典（如 {\"query\": \"<原句>\"} 或 {\"signal\": \"<原句>\"}）。"
                 "规则：含 疾病/疼痛/损伤/禁忌 信号→guard；含 怎么做/要领/怎么练→teach；"
                 "含 计划/安排/怎么练一周→plan；含 下一组/加重量/减载→progress；否则 qa。")
        try:
            data = self._invoke_json(sys_p, text, "task_type")
            return Classification(str(data.get("task_type") or "qa"),
                                  data.get("params") or {"query": text})
        except Exception:
            return self._fallback.classify(text, profile)   # 规则回落

    def plan(self, task, available) -> list[PlannedCall]:
        self.calls.append("plan")
        sys_p = ("你是 FitMind 计划的技能调用规划器。输出 JSON 数组，元素形如 "
                 '{"step_id":"1","skill":"<技能名>","params":{},"depends_on":[]}。'
                 f"可用技能: {available}。一次规划全部调用（≤3 步）。仅 JSON。")
        try:
            data = self._invoke_json(sys_p, task, "steps")
            steps = data.get("steps") or data.get("plan") or []
            return [PlannedCall(step_id=str(s.get("step_id", str(i))),
                                skill=str(s.get("skill", "qa")),
                                params=dict(s.get("params") or {}),
                                depends_on=[str(x) for x in (s.get("depends_on") or [])])
                    for i, s in enumerate(steps)]
        except Exception:
            return self._fallback.plan(task, available)      # 规则回落

    def think(self, context, available) -> PlannedCall:
        self.calls.append("think")
        sys_p = ("你是 FitMind 的 ReAct 步骤决策器。看上下文最后一步结果，决定下一步。"
                 "输出 JSON：{\"skill\":\"<技能名|_done>\",\"params\":{}}。"
                 "已完成或无需再调用→ skill 为 _done。仅 JSON。")
        try:
            data = self._invoke_json(sys_p, str(context[-1:] or context), "skill")
            skill = str(data.get("skill") or "_done")
            if skill == "_done":
                return PlannedCall("end", "_done", {})
            return PlannedCall("1", skill, data.get("params") or {})
        except Exception:
            return self._fallback.think(context, available)

    def render(self, structured, tone="coach") -> str:
        self.calls.append("render")
        sys_p = ("你是 FitMind 的中文健身教练，语气亲切专业（Apple 风格：简洁、正向、不说教）。"
                 "非诊断、风险提示明确。依据结构化结果组织 3-6 句回答，可含分项。")
        try:
            import json as _json
            return str(self._llm.invoke(
                [("system", sys_p),
                 ("user", _json.dumps(structured, ensure_ascii=False))]).content)
        except Exception:
            return self._fallback.render(structured, tone)


# ---------------------------------------------------------------- 工厂
def build_provider(config: dict | None = None) -> LLMProvider:
    """按 llm_config.json 选择 provider：auto=有 key 则 DeepSeek 否则 Stub；
    显式 "deepseek" 无 key 也回落 Stub；"stub" 强制 Stub。"""
    cfg = config or {}
    mode = str(cfg.get("provider") or "auto").lower()
    if mode == "stub":
        return StubProvider()
    if mode in ("auto", "deepseek"):
        try:
            return DeepSeekProvider()
        except ValueError:
            return StubProvider()
    return StubProvider()