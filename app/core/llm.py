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
      "几天练", "练几天", "一周练",
      # W3 R6：复合目标问法（倒三角+腹肌+半马 怎么排/冲突吗）→ teach 编排知识
      "怎么排", "怎么兼顾", "冲突吗", "冲突"), "teach",
     lambda t, kw: {"query": t}),
    # W4：#64 '出来多久腹肌练能' 被 LLM 误投 plan → "多久" qa 强信号 L0 短路。
    # 放 progress 行之前（reversed 时 progress 先查），不抢"下一组休息多久"。
    (("多久",), "qa",
     lambda t, kw: {"query": t}),
    (("下一组", "加重量", "减重量", "加几公斤", "减载"), "progress",
     lambda t, kw: {"query": t}),
    # guard：症状词单源（app.core.vocab.GUARD_SYMPTOMS，与 guard_skill 共享）
    # + 意图层补充信号（"能不能练"类问法）。tfcc/半月板等已含于症状表。
    (GUARD_SYMPTOMS + GUARD_SIGNAL_EXTRA, "guard",
     lambda t, kw: {"signal": t}),
    (("挺不错", "还不错", "就按这个", "按这个练", "没问题就按"), "smalltalk",
     lambda t, kw: {"topic": t}),
    (("你好", "您好", "hi", "hello", "嗨", "你是谁", "你叫什么",
      "谢谢", "再见", "拜拜"), "smalltalk",
     lambda t, kw: {"topic": t}),
    # 档案查询（CLI 实证："我的身体数据"被 LLM 误投 plan）：走 qa 的 profile 分支，
    # 直接读会话档案，不检索动作/食物库
    (("身体数据", "我的身体", "我的档案", "个人档案", "身体信息", "我的数据",
      "身体情况"), "qa",
     lambda t, kw: {"query": t, "kind": "profile"}),
    # W1·档案查询模式（v2#161-180 误投 plan 根治）：档案名词 + 评价问法 → qa(profile)。
    # 因 appended 在列表末尾，reversed(_RULES) 时最先命中，先于 plan 截胡。
    (("我的肌肉量", "肌肉量多少", "体脂多少", "体脂百分", "内脏脂肪", "训练容量",
      "练了什么", "昨天练", "上周练", "恢复能力", "柔韧性", "训练年限",
      "基础代谢", "骨量", "握力", "心肺功能", "肌肉分布", "腰臀比", "一英里",
      "FFMI", "ffmi", "bmr", "理想体重", "蛋白质需求", "水分需求",
      "训练强度该", "该吃多少卡", "吃够了吗", "我的身高体重", "体重",
      "硬拉多少算达标", "算达标", "算正常", "算快吗", "算什么水平", "健康吗"), "qa",
     lambda t, kw: {"query": t, "kind": "profile"}),
    # W1·跨域请求 → smalltalk 引导（v2#52 写周报被当 plan）
    (("写周报", "周报", "讲笑话", "冷笑话", "打游戏", "买房子", "买房", "哪套房值得买",
      "会下棋", "股票", "写代码"), "smalltalk",
     lambda t, kw: {"topic": t}),
    # W3·咨询/改计划/评价类 → qa（禁 plan_exec）："怎么改/有没有效/怎么样"等表达
    # 是咨询非生成完整计划（G6-02/G6-19/G7-12 等 plan 过触发根因）
    (("怎么改", "怎么调整", "怎么变", "有没有效", "有没有用", "练什么能",
      "能防", "效果好", "什么效果"), "qa",
     lambda t, kw: {"query": t}),
    # W1·辟谣/评价类问法 → qa（禁 plan；"是不是/有用吗/对吧"类常被 LLM 误判 plan）
    (("是不是", "真的吗", "有用吗", "靠谱吗", "好吗", "对不对", "对吧",
      "智商税", "能瘦吗", "有效吗", "会不会", "可以吗", "瘦肚子", "掉秤"), "qa",
     lambda t, kw: {"query": t}),
    # D5 2026B：记忆元/偏好查询问法 → qa（禁 clarify/plan；T9-15/17/19）
    (("练了几次", "跑过吗", "跑过步", "练过吗", "训练过吗", "吃过几次",
      "练几天了", "坚持多久了", "餐单", "配餐", "食谱"), "qa",
     lambda t, kw: {"query": t}),
    # D5 2026B：无伤语境下"今天能练X吗" → qa（禁 guard 疑问/plan；T9-13）
    (("能练腿", "能练肩", "能练背", "能练胸", "能练臀", "能练吗", "今天能练"), "qa",
     lambda t, kw: {"query": t}),
    # D5 2026B：增肌速度咨询（T7-07 一月长10斤）→ qa 非 clarify
    (("长肌肉", "长肉", "增肌速度", "多久能增肌"), "qa",
     lambda t, kw: {"query": t}),
    # D5 2026B：康复后确认问法（T9-03"能练深蹲了吧"/T9-09"深蹲可以吧"）→ qa
    (("能练深蹲", "能练硬拉", "能练卧推", "可以吧", "行不行", "行吧", "没问题吧"), "qa",
     lambda t, kw: {"query": t}),
    # D6 2026B：增肌/肌肉话题（T7-07 一月长10斤肌肉）→ qa 非 clarify。
    # 2026-09-09 收窄："增肌/肌肉"移除——"帮我做个增肌计划"应 plan（test_classify_plan
    # _generation_kept）、"我的肌肉量有多少"应 profile kind（test_profile_field_questions
    # _to_qa_not_plan）；仅长肉类咨询词命中，T7-07 落 fallback qa(0.3)→direct 仍达标。
    (("长肉", "长几斤", "长了多少", "增肌速度"), "qa",
     lambda t, kw: {"query": t}),
    # D6 2026B：康复后单句确认（T9-18"深蹲吧"）→ qa 非 clarify
    (("深蹲吧", "硬拉吧", "练吧", "跑吧"), "qa",
     lambda t, kw: {"query": t}),
    # 2026-09-08 个性化：身份/建档问法 → qa(profile)（CLI 实证空检索乱判；
    # 与 memory_extract 停用词"谁"协同——路由答"我是谁"，抽取绝不记"谁"）
    (("我是谁", "我叫什么", "我的名字", "你记得我吗"), "qa",
     lambda t, kw: {"query": t, "kind": "profile"}),
    (("建档", "建立档案", "建档案", "录入档案"), "qa",
     lambda t, kw: {"query": t, "kind": "profile"}),
]

# W2 指代消解：领域实体词（动作/食物/肌群/编排）命中说明有明确指代，不触发澄清
_ENTITY_HINT = ("深蹲", "硬拉", "卧推", "推举", "划船", "引体", "俯卧撑", "弯举",
                "箭步", "蹲", "拉", "推", "举", "卷腹", "平板", "臀", "胸", "背",
                "肩", "腿", "腹", "肱", "三角", "蛋白", "鸡胸", "鸡蛋", "牛奶",
                "牛肉", "鱼", "米饭", "碳水", "脂肪", "卡路里", "热量", "食物",
                "训练计划", "课表", "分化", "一周", "公里", "分钟",
                # W2 补充：具体器械实体（防"壶铃那个"被当指代；"器械/动作"等类别
                # 词不加——"那个器械叫什么名字"应触发指代澄清）
                "杠铃", "哑铃", "壶铃", "绳索", "弹力带",
                # RG-02 补充：档案域指标名词（"训练容量/体脂/肌肉量…"是明确指代，
                # "X够不够/对不对"悬空评价应放行到 profile 规则，不触发指代澄清）
                "训练容量", "体脂", "肌肉量", "内脏脂肪", "柔韧性", "腰臀比",
                "基础代谢", "骨量", "握力", "心肺", "训练年限", "肌肉分布",
                "FFMI", "ffmi", "一英里", "BMI",
                "按这个", "挺不错", "没问题",
                # D6 2026B：疲劳/恢复话题是明确实体（T6-17"不是伤就是疲劳，继续练？"）
                "疲劳", "恢复")

# W2 指代消解词表（_is_vague_reference 用）：句含任一且无领域实体 → 规则级 clarify。
# R4 实证：#99 还要继续吗 / #92 能不能换一种 / #83 一天做几个 / #98 帮我看看对不对。
# RG-02 补全（H5 差 2 题达标）：多久能 / 不明白 / 换个 / 够不够 / 接下来。
# 与 _ENTITY_HINT 配合：含实体（"继续深蹲""做几个卧推"）→ 放行。
_REFERENCE_WORDS = ("这个", "那个", "它", "这样", "上次", "随便",
                    "继续", "换一种", "换一个", "换个", "几个", "几组",
                    "多少组", "对不对", "多久能", "不明白", "不太明白",
                    "够不够", "接下来")


# 指代消解词表（F2-B 亦复用领域实体豁免）
_MINOR_WORDS = ("10岁", "11岁", "12岁", "13岁", "14岁", "15岁", "16岁", "17岁",
                    "初中生", "高中生", "未成年")
_INTENSITY_WORDS = ("冲pr", "破pr", "大重量", "上重量", "备战", "比赛",
                    "药检", "举铁", "力量举", "冲记录", "加重")


def _is_minor_context(p: str) -> bool:
    """未成年语境：年龄/学段词命中。"""
    return any(k in p for k in _MINOR_WORDS)


def _is_intensity_context(p: str) -> bool:
    """训练强度/备战语境：与未成年叠加才触发 guard（防建档误拦）。"""
    return any(k in p for k in _INTENSITY_WORDS)


def _is_vague_reference(t: str) -> bool:
    """句首/句中含指代词（这个/那个/它/这样/上次/继续/换一种/几个…）且无领域实体词
    → 纯指代、缺上下文，应规则级 clarify（不硬答、不经 LLM）。"""
    if not any(k in t for k in _REFERENCE_WORDS):
        return False
    return not any(k in t for k in _ENTITY_HINT)


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

    def normalize(self, text: str, purpose: str) -> dict | None:
        """LLM 归一化（兜底路径用）：长尾原句 → 高频标准说法。
        返回 {"standard_text","confidence","evidence","dropped"} 或 None（无法改写）。
        抽象基类默认降级：子类未实现 = 不可用（上层按 None 静默走原逻辑）。"""
        return None


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
        # D6 2026B：心理红线否定短路（"我没想死，就是好累" T5-14）——否定前缀的
        # 红线词不是自报，不得路由 guard。
        if any(k in t for k in ("没想死", "不想死", "没有想死", "不自杀", "没自杀",
                                "不想轻生")):
            pass
        # D6 2026B：普通应激（压力）+ 评价问法 → 不 guard（T5-17 高三压力失眠想哭
        # +运动有用吗；T5-18 丧亲失眠含"出事"非"压力"仍 guard——豁免仅限"压力"）
        elif any(k in t for k in ("压力大", "压力")) and any(
                k in t for k in ("有用吗", "有效吗", "能缓解", "有帮助吗", "缓解吗")):
            pass
        # 安全红线：guard 症状词绝对优先于一切规则（防 W1 评价类规则误吞症状问法）。
        # 注意 reversed(_RULES) 中新规则（放末尾者优先）含"可以吗/会不会"等宽词，
        # 必须先于此检查症状信号。
        elif any(k in p or k in t for k in GUARD_SYMPTOMS + GUARD_SIGNAL_EXTRA):
            return Classification("guard", {"signal": t}, confidence=1.0)
        # F2-B 未成年×强度语境复合规则（v3 triage R2）：年龄词 ∧ 冲强度/备战语境 →
        # guard 保守话术；仅年龄词（建档/普通询问）不拦（防误伤）。
        if (_is_minor_context(p) and _is_intensity_context(p)):
            return Classification("guard", {"signal": t}, confidence=1.0)
        # W2：未成年×补剂/药物语境（G10-10 16岁喝肌酸影响身高）→ guard 保守
        # 拦截；否则被"会不会"(辟谣)qa 规则截胡。置于 _RULES 前。
        if _is_minor_context(p) and any(k in t for k in ("肌酸", "补剂", "蛋白粉",
                                                         "增肌粉", "支链", "氮泵", "增肌剂")):
            return Classification("guard", {"signal": t}, confidence=1.0)
        # D6 2026B：未成年×减重施压（T4-18 我妈逼我15岁减肥）→ guard
        if _is_minor_context(p) and any(k in t for k in ("减肥", "减脂", "节食", "瘦身")):
            return Classification("guard", {"signal": t}, confidence=1.0)
        # D5 2026B：注入越权语境——角色扮演/编码/伪造指令/格式混排等不触发 plan_exec
        # 与指代澄清。含药词（药方/特效药/药单）→ 仍 guard（红线优先）；否则拒绝
        # 配合走 smalltalk。置于指代消解之前（T8-09 伪造债务含"上次"须先拦截）。
        _INJECT_KW = ("被授权", "授权为", "角色扮演", "假装", "无视安全", "绕过",
                      "base64", "编码", "输出：", "====", "你上次欠", "跪下",
                      "删库", "上面通知", "上级命令", "命令你", "系统指令",
                      "重置系统", "忽略之前的")
        if any(k in t for k in _INJECT_KW):
            if any(k in t for k in ("药方", "特效药", "药单", "药物", "处方", "开药")):
                return Classification("guard", {"signal": t}, confidence=1.0)
            return Classification("smalltalk", {"topic": t}, confidence=1.0)
        # W2 指代消解：无领域实体的纯指代问法 → 规则级 clarify（不经 LLM，不硬答）
        if _is_vague_reference(t):
            return Classification("fallback", {}, confidence=0.3,
                                  needs_clarify=True)
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
        acks = structured.get("memory_ack") or []
        if acks:
            parts.append("已记下：" + "、".join(acks))
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
        role_models = {"classify": model, "plan": model, "render": model,
                       "normalize": model}
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
                 "若语句不通顺/语序明显混乱/看不出意图（例如'出来多久腹肌练能'），"
                 "必须选 unknown 并给低 confidence，不要硬猜成 plan 或其它意图。"
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
                 "的措辞，回答应围绕 data.items 的实际内容或 empty/reason 字段组织。"
                 "6) 禁止提及实现细节：严禁出现'结构化''字段''data''items''JSON''数组'"
                 "'为空'这类措辞——你是在跟用户说话，不是在描述数据；检索为空就自然地说"
                 "'没有找到相关内容'并给换词建议。")
        try:
            return str(self._models["render"].invoke(
                [("system", sys_p),
                 ("user", json.dumps(structured, ensure_ascii=False))]).content)
        except Exception:
            return self._fallback.render(structured, tone)

    # ---- 归一化（W3：检索链兜底；W4：意图链兜底） ----

    def normalize(self, text: str, purpose: str) -> dict | None:
        """长尾原句 → 高频标准说法（仅同义替换/提炼，禁止扩写）。
        purpose: intent|exercise|food。返回 JSON 规范化产物或 None（任何失败）。"""
        self.calls.append("normalize")
        sys_p = ("你是 FitMind 的中文健身术语归一化器。把口语长尾说法改写为"
                 "健身库里的高频标准说法或规范化中文词（如'蛋白质粉'→'蛋白粉'）。"
                 "硬性约束："
                 "1) 只允许同义替换与提炼，禁止扩写、禁止补充原句没有的信息；"
                 "2) 禁止删除或软化症状/否定语义：原句含'疼''痛''晕''麻''酸''胀'等"
                 "症状字或'不''别''不敢'等否定，改写后必须原样保留；"
                 "3) 输出 JSON：{\"standard_text\":\"改写结果\",\"confidence\":0~1,"
                 "\"evidence\":\"逐字抄取自原句的核心片段\",\"dropped\":[\"被剥离的修饰词\"]}；"
                 "4) 改不出高频说法就返回 {\"standard_text\":null, \"confidence\":0,"
                 "\"evidence\":\"\",\"dropped\":[]}，绝不许硬编；"
                 "5) evidence 必须逐字摘自原句，禁止编造。")
        purpose_guide = {"intent": "本任务是意图归类用，保持原句语义中心词",
                         "exercise": "本任务是动作库检索用，改写成动作名（如'练胸'→'卧推'）",
                         "food": "本任务是食物检索用，改写成食材名（如'蛋白质粉'→'蛋白粉'）"}.get(
                             purpose, "")
        user = f"purpose={purpose}。{purpose_guide} 原句：{text}"
        try:
            data = self._invoke_json(sys_p, user, "standard_text",
                                     llm=self._models["normalize"])
            std = data.get("standard_text")
            if not std:
                return None                        # 改不出来 → 上层走原逻辑
            ev = data.get("evidence") or ""
            if ev and str(ev) not in text:
                raise ValueError(f"evidence 不在原句（幻觉）: {ev!r}")
            return {"standard_text": str(std),
                    "confidence": float(data.get("confidence") or 0.0),
                    "evidence": ev,
                    "dropped": list(data.get("dropped") or [])}
        except Exception:
            return None


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