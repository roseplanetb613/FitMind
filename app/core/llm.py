# -*- coding: utf-8 -*-
"""LLM 抽象层：供应商可插拔；S1–S5 用 StubProvider（确定可复现）。"""
from __future__ import annotations
import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from app.core.intent import Intent, PlannedCall
from app.core.vocab import GUARD_SIGNAL_EXTRA, GUARD_SYMPTOMS


@dataclass
class Classification:
    task_type: str
    params: dict
    complexity: str = "simple"
    confidence: float = 1.0
    needs_clarify: bool = False


# 规则意图词典（stub 分类用；真实 provider 接管后可删）
_RULES = [
    # plan：收窄强信号词（去掉单独的"怎么安排"/"一周"，避免吞编排模式问法）
    (("计划", "练什么", "帮我安排", "帮我规划", "帮我排",
      "制定方案", "制定计划", "给我计划", "安排课表", "安排训练", "制定", "方案"), "plan",
     lambda t, kw: {"days": 1}),
    (("怎么做", "要领", "动作教学", "怎么练"), "teach",
     lambda t, kw: {"query": t}),
    # 训练编排模式问法（询问"如何分化"而非"生成完整计划"）→ teach
    # 压测补全：五分化/双分化/单分化/三分化/全身训练/怎么分/如何分/几天练
    (("练三休一", "练二休一", "练四休一", "练一休一", "推拉腿",
      "上下肢", "上下分化", "全身分化", "分化训练", "分化方案",
      "怎么分化", "如何分化", "五分化", "双分化", "单分化", "三分化",
      "推拉", "推拉腿蹲", "全身训练", "怎么分", "如何分",
      "几天练", "练几天", "一周练"), "teach",
     lambda t, kw: {"query": t}),
    (("下一组", "加重量", "减重量", "加几公斤", "减载"), "progress",
     lambda t, kw: {"query": t}),
    # guard：症状词单源（app.core.vocab.GUARD_SYMPTOMS，与 guard_skill 共享）
    # + 意图层补充信号（"能不能练"类问法）。tfcc/半月板等已含于症状表。
    (GUARD_SYMPTOMS + GUARD_SIGNAL_EXTRA, "guard",
     lambda t, kw: {"signal": t}),
    (("你好", "您好", "hi", "hello", "嗨", "你是谁", "你叫什么",
      "谢谢", "再见", "拜拜"), "smalltalk",
     lambda t, kw: {"topic": t}),
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
        t = (text or "").strip()
        # 纯语气词/单字/无意义输入 → smalltalk（不落 qa 检索；"卧推""深蹲"等
        # 两字实义词不受影响，继续走词表规则）
        if len(t) <= 1 or all(ch in "啊嗯哦哈诶嘿呀嘛吧呢？?。！! " for ch in t):
            return Classification("smalltalk", {"topic": t}, confidence=1.0)
        p = t.lower()
        for kws, tt, build in reversed(_RULES):
            if any(k in p or k in t for k in kws):
                # 规则强信号：命中即高置信（零 token、可复现；供真实 provider 跳过 LLM）
                return Classification(tt, build(t, kws), confidence=1.0)
        return Classification("qa", {"query": t}, confidence=0.3)  # 非强命中信号

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
        if structured.get("error"):
            parts.append("提示: " + str(structured["error"]))   # 失败原因如实透出
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
# 意图白名单（LLM 分类输出受限集合，外部不可越界）
# unknown：LLM 拿不准的真正退路（消解"被迫硬选"），arbitrate 定向 clarify。
INTENT_WHITELIST = frozenset(
    {"qa", "teach", "plan", "progress", "guard", "smalltalk", "fallback",
     "unknown"})

# function-calling schema：强制 LLM 按受限选择题+置信度+evidence 返回
CLASSIFY_TOOL = {
    "type": "function",
    "function": {
        "name": "classify_intent",
        "description": "将用户健身消息归类为以下意图之一，并给出置信度与简要参数",
        "parameters": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "enum": sorted(INTENT_WHITELIST),
                    "description": "意图类型",
                },
                "params": {
                    "type": "object",
                    "description": "简要参数，如 {'query': '<原句>' } 或 {'signal': '<原句>'}",
                },
                "confidence": {
                    "type": "number", "minimum": 0, "maximum": 1,
                    "description": "本分类的确信度",
                },
                "evidence": {
                    "type": "string",
                    "description": "触发该分类的原句片段，必须从用户原句中逐字截取（禁止编造）",
                },
            },
            "required": ["task_type", "params", "confidence", "evidence"],
        },
    },
}


def load_dotenv(path: str | None = None) -> bool:
    """零依赖 .env 加载器：把 `KEY=VALUE` 行写入 os.environ（已存在则跳过）。
    默认读项目根 .env（gitignored）。返回是否加载到文件。"""
    import os as _os
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parent.parent.parent
    env_file = _Path(path) if path else root / ".env"
    if not env_file.exists():
        return False
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in _os.environ:
            _os.environ[k] = v
    return True


class DeepSeekProvider(LLMProvider):
    """真实 LLM（DeepSeek，OpenAI 兼容）实现。
    决策路径原则不变：guard/plan/progress 数值决策锁定规则链；本类只做
    意图/计划分类与渲染。调用或解析失败由调用方回落（见 build_provider 与图 render）。
    Key 来源（优先级）：构造参数 > 环境变量 > 项目根 .env。"""

    is_stub: bool = False
    BASE_URL = "https://api.deepseek.com"

    def __init__(self, key: str | None = None, model: str = "deepseek-chat",
                 temperature: float = 0.2, models: dict | None = None):
        load_dotenv()                            # .env 兜底（幂等）
        self.key = key or os.environ.get("DEEPSEEK_API_KEY") or ""
        if not self.key:
            raise ValueError("DEEPSEEK_API_KEY 未设置：无法构造 DeepSeekProvider")
        from langchain_openai import ChatOpenAI
        self.model_name = model
        # llm_config.models 分角色指定模型（classify/plan/render），缺省统一回退 model。
        # 显式收紧 timeout/重试：分类/渲染任何失败本就有规则兜底，不吃 SDK 默认长重试。
        role_models = {"classify": model, "plan": model, "render": model}
        role_models.update({k: v for k, v in (models or {}).items() if v})
        self._models = {
            role: ChatOpenAI(model=name, api_key=self.key, base_url=self.BASE_URL,
                             temperature=temperature,
                             request_timeout=30, max_retries=1)
            for role, name in role_models.items()}
        self._llm = self._models["render"]       # 兼容既有引用
        self._fallback = StubProvider()          # 规则回落（分类兜底）
        self.calls: list[str] = []

    # ---- 工具：单次带 JSON 约束的调用 ----
    def _invoke_json(self, system: str, user: str, reply_key: str, llm=None):
        client = (llm or self._llm).bind(response_format={"type": "json_object"})
        resp = client.invoke([("system", system), ("user", user)])
        text = (resp.content or "") if hasattr(resp, "content") else str(resp)
        data = self._parse_json(text)
        if reply_key not in data:
            raise ValueError(f"LLM 返回缺少键 {reply_key}: {text[:200]}")
        return data

    @staticmethod
    def _parse_json(text: str) -> dict:
        s = text.strip()
        s = s[s.find("{"):s.rfind("}") + 1] if "{" in s else s
        return json.loads(s)

    # ---- LLMProvider 接口 ----
    def classify(self, text, profile=None) -> Classification:
        """兼容包装：L0 规则强信号 → classify_llm（L2 纯 LLM）→ arbitrate（单点）。
        行为与改造前一致（供既有调用方/测试使用）。"""
        rule = self._fallback.classify(text, profile)      # Stub 规则（强命中=1.0）
        if rule.confidence >= 0.7:                          # ① 强信号直接采用
            return rule
        try:
            out = self.classify_llm(text)                   # ② 纯 LLM 层
        except Exception:
            return rule                                     # 幻觉/失败 → 规则兜底
        return arbitrate(out.task_type, out.confidence, out.params, rule)

    def classify_llm(self, text, profile=None) -> Classification:
        """纯 LLM 层（跳过规则）：function-calling 受限选择题+置信度+evidence。
        evidence 必须逐字截取自原句（否则视为幻觉抛 ValueError）；unknown →
        体面退路（fallback+clarify）。返回**未仲裁**结果；调用方再套 arbitrate。"""
        self.calls.append("classify")
        sys_p = ("你是 FitMind 健身助手的意图路由器。只能从给定 task_type 枚举中选一个，"
                 "并给出确信度。规则参考：疾病/疼痛/伤/晕/骨折/断/扭伤/TFCC/ACL/半月板/"
                 "韧带等健康信号→guard；问候/寒暄/语气词→smalltalk；怎么做/要领→teach；"
                 "计划/安排/一周→plan；下一组/加重量/减载→progress；食物/动作知识检索→qa。"
                 "特别注意：'练三休一/练二休一/推拉腿/上下肢/分化/怎么分化'等训练编排模式"
                 "问法属于 teach（询问如何安排训练），不要误判为 plan（生成完整计划）。"
                 "plan 仅在用户明确要'生成/制定/给我一份训练计划'时采用。"
                 "few-shot 锚点：'练三休一怎么分'→teach；'帮我制定一周计划'→plan；"
                 "'胸口有点闷'→guard；'下一组加几公斤'→progress；'你是谁'→smalltalk；"
                 "'鸡胸肉蛋白质多少'→qa。"
                 "若实在无法判断，选 unknown 并给低 confidence，不要乱猜。"
                 "evidence 必须是你从用户原句中看到并逐字截取的片段，禁止编造。")
        try:
            bound = self._models["classify"].bind_tools([CLASSIFY_TOOL])
            resp = bound.invoke([("system", sys_p), ("user", text)])
            calls = getattr(resp, "tool_calls", None) or []
            if not calls:
                raise ValueError("LLM 未返回工具调用")
            args = dict(calls[0].get("args", {}))
        except Exception:
            return self._fallback.classify(text)   # 网络/解析失败 → 规则兜底
        tt = str(args.get("task_type") or "fallback")
        params = dict(args.get("params") or {})
        conf = float(args.get("confidence") or 0.0)
        ev = args.get("evidence")
        if ev and str(ev) not in text:
            raise ValueError(f"evidence 不在原句（幻觉）: {ev!r}")
        if tt == "unknown":                        # 体面退路 → 反问
            return Classification("fallback", {}, confidence=0.3,
                                  needs_clarify=True)
        return Classification(tt, params, confidence=conf)

    def plan(self, task, available) -> list[PlannedCall]:
        self.calls.append("plan")
        sys_p = ("你是 FitMind 计划的技能调用规划器。输出 JSON 数组，元素形如 "
                 '{"step_id":"1","skill":"<技能名>","params":{},"depends_on":[]}。'
                 f"可用技能: {available}。一次规划全部调用（≤3 步）。仅 JSON。")
        try:
            data = self._invoke_json(sys_p, task, "steps",
                                     llm=self._models["plan"])
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
            data = self._invoke_json(sys_p, str(context[-1:] or context), "skill",
                                     llm=self._models["classify"])
            skill = str(data.get("skill") or "_done")
            if skill == "_done":
                return PlannedCall("end", "_done", {})
            return PlannedCall("1", skill, data.get("params") or {})
        except Exception:
            return self._fallback.think(context, available)

    def render(self, structured, tone="coach") -> str:
        self.calls.append("render")
        sys_p = ("你是 FitMind 的中文健身教练，语气亲切专业（Apple 风格：简洁、正向、不说教）。"
                 "非诊断、风险提示明确。依据结构化结果组织 3-6 句回答，可含分项。"
                 "硬性约束："
                 "1) 若 data.items 为空或 data 含 empty/reason（检索未命中），务必如实说"
                 "'没有找到相关内容'并给出换关键词建议，绝不虚构数据或健康结论；"
                 "2) 绝不输出'绿灯/健康无风险/一切正常'这类健康评估结论，除非该结论明确来自"
                 "结构化结果中的 screening 字段；"
                 "3) 若 data 含 message 字段（闲聊/固定应答），直接以同样友好的口气回应它；"
                 "4) 若结构化结果含 error 字段（如计划缺少档案字段），如实转述失败原因并给出"
                 "下一步指引（如'请先在设置中完善身体信息后再试'），不要臆造成'没找到相关内容'；"
                 "5) structured.title 是任务类别标签（如'动作教学''知识问答''训练计划'），"
                 "不是用户查询的关键词；严禁在回答里出现'与XXX相关'这类把 title 当查询词"
                 "的措辞，回答应围绕 data.items 的实际内容或 empty/reason 字段组织。")
        try:
            return str(self._models["render"].invoke(
                [("system", sys_p),
                 ("user", json.dumps(structured, ensure_ascii=False))]).content)
        except Exception:
            return self._fallback.render(structured, tone)


# ---------------------------------------------------------------- 工厂
LLM_CONFIG = Path(__file__).resolve().parent.parent / "config" / "llm_config.json"


def load_llm_config(path: str | None = None) -> dict:
    """读取 llm_config.json（缺文件/解析失败 → {}，等价 provider=auto）。"""
    p = Path(path) if path else LLM_CONFIG
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_provider(config: dict | None = None) -> LLMProvider:
    """选择 provider：config=None 时读 llm_config.json（此前文件从未被接线，
    是死配置）。auto=有 key 则 DeepSeek 否则 Stub；显式 "deepseek" 无 key
    也回落 Stub；"stub" 强制 Stub。models 字段分角色指定模型。"""
    cfg = load_llm_config() if config is None else (config or {})
    mode = str(cfg.get("provider") or "auto").lower()
    models = dict(cfg.get("models") or {})
    if mode == "stub":
        return StubProvider()
    if mode in ("auto", "deepseek"):
        try:
            return DeepSeekProvider(models=models or None)
        except ValueError:
            return StubProvider()
    return StubProvider()


# arbitrate 单点化：本体移入 app/core/arbitrate.py，此处保留同名导出兼容
# 既有 `from app.core.llm import arbitrate`（test_intent_router 等）。
from app.core.arbitrate import arbitrate  # noqa: E402,F401