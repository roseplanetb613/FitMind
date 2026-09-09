# -*- coding: utf-8 -*-
"""风险守卫：规则拦截最优先，任何模式不得绕过。非诊断提示。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult
from app.core.vocab import GUARD_SYMPTOMS, GUARD_SIGNAL_EXTRA
from app.core.llm import _is_minor_context, _is_intensity_context
import screening

# 症状词表单源化：与意图分类(llm._RULES)共用 app.core.vocab.GUARD_SYMPTOMS，
# 避免双写漂移（压测 G1 '闷/晕/卡卡/扭着' 缺口根因）。改词表只改 vocab.py。
SYMPTOMS = GUARD_SYMPTOMS

# 医疗咨询类信号（guard 话术模板选择，非意图路由）：药物/慢病/孕产/极端行为/
# 精神健康。用户在咨询"能不能用/要不要做"类非症状事项，套"哪里疼"追问 = 张冠
# 李戴（压测 v2 R2 #127/#182/#183/#186/#196 实证）→ 走医疗咨询模板。
# 注意与 GUARD_SIGNAL_EXTRA 分工：词表管"路由到 guard"，此表管"选哪个话术"。
_MEDICAL_KW = ("奥利司他", "利尿", "克伦特罗", "群勃龙", "类固醇", "兴奋剂",
               "药物", "吃药", "用药", "处方",
               "禁药", "消水肿", "水肿药", "药检",
               "糖尿病", "高血压", "血压", "心脏", "支架", "心梗", "心衰",
               "怀孕", "孕期", "孕妇", "备孕", "哺乳",
               "断食", "禁食", "催吐", "只喝水",
               "化疗", "骨松", "骨质疏松", "哮喘", "支气管", "胰岛素",
               "未成年", "冲pr", "冲PR", "备战",
               # W1 R2 补漏：安眠药/封闭针/生酮/就医对抗/孕早期晚期
               "安眠药", "封闭", "生酮", "医院",
               "孕晚", "孕早", "临产",
               "抑郁", "焦虑", "躁郁", "低落", "情绪低落",
               "不想活", "想不开", "自伤", "自杀", "伤害自己", "精神", "心理")

# ---- 记忆层（个人记忆 spec v0.3 §3/§5：guard 消费 active injury）----
# 计划/安排语境词：主动禁忌交叉只对"生成方案"类请求触发（被拦=阻止危险方案）；
# 教学/知识问句（"深蹲怎么做"）不进交叉，防过度保护掩盖正常问答。
_PLAN_SIGNAL = ("计划", "方案", "安排", "课表", "规划", "帮我排", "给我排",
                "训练安排", "编排", "怎么排", "一周练", "练什么", "练哪里",
                "推荐几个动作", "做哪些动作", "推荐动作", "训练表",
                # D5 2026B：注意事项问法（T9-11 有伤记忆+深蹲注意什么 → 记忆交叉拦截）
                "注意什么", "注意啥", "要注意")

# 失效/续期句（对确认问题的回答或主动改口）
_CNF_DONE = ("不疼", "不痛", "好了", "没感觉", "没事了", "不难受", "康复", "恢复了")
_CNF_KEEP = ("还疼", "还在疼", "没好", "还是疼", "更疼", "没缓", "没好转", "一直疼")
# 急性/新发症状词（W1 R2：弹响/咯噔/摩擦/刺痛等描述与 _CNF_DONE 撞车——"咔一声然后
# 没事了" 是当前事件描述，不是旧伤康复确认 → 优先黄拦，不得被 close 流程吞掉）
_ACUTE_KW = ("咔", "咯噔", "摩擦", "弹响", "闪", "咔嚓", "响")
# 环境风险词（高温/严寒/中暑等训练环境威胁 → 专项话术）
_ENV_KW = ("三伏", "中暑", "高温", "酷暑", "户外跑", "大太阳", "严寒", "暴晒")

# 精神健康信号（F3：话术走心理援助变体；含"自残/自杀"类硬拦截词）
_MENTAL_KW = ("抑郁", "焦虑", "躁郁", "不想活", "想不开", "自伤", "自杀",
              "伤害自己", "不想练了想",
              # D1 2026B：情绪暗语/低价值感/死意象（选话术：心理援助变体）
              "活着没意思", "活着没意义", "没意思", "解脱", "一了百了", "想消失",
              "如果我消失", "死念头", "死的念头", "自残", "想死", "轻生", "跳楼",
              "跳下去", "自杀了", "活不下去", "浪费粮食", "会有人发现", "活着干嘛",
              "想放手", "力竭替代", "被霸凌", "霸凌", "打回去", "报复",
              "没人在乎", "没人关心", "情绪低", "砸器械", "想砸")


def _medical_advice(signal: str) -> str:
    """医疗模板 B（单源）：F1 前置分支与 _unspecified 共用，防双写漂移。
    F3：措辞补'不建议'；精神健康信号走心理援助变体（不写具体号码防编错）。"""
    if any(k in signal for k in _MENTAL_KW):
        return ("你提到的情况涉及情绪或心理健康，我不能给医疗建议，也请不要独自硬扛。"
                "建议尽快联系当地心理援助热线，或在家人陪同下前往正规医院咨询专业心理"
                "医生，必要时求助精神心理科。运动可以作为辅助，但不能替代专业帮助；"
                "具体训练和强度，也请先咨询专业人士评估后再做决定。")
    return ("你提到的情况涉及用药、疾病或特殊身体状况，我不能给医疗建议，也不会为你开具"
            "处方或推荐任何处方药物，并会明确拒绝此类请求，也不建议自行尝试。"
            "请先咨询医生或专业人士评估后再安排训练；一般性原则是训练从低强度循序渐进，"
            "出现不适应立即停止。")

# ---- D3 2026B 文本归一化（词法伪装绕过治理：繁简/全角/拼音/中英混写）----
# 仅供 guard 信号词表匹配前做轻量规范化；话术/记忆仍以原始 signal 语义为主。
_FULLWIDTH = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}   # 全角→半角
_TRAD2SIMP = {
    "發": "发", "體": "体", "練": "练", "嗎": "吗", "覺": "觉", "應": "应",
    "該": "该", "裏": "里", "準": "准", "備": "备", "擔": "担", "風": "风",
    "過": "过", "強": "强", "訓": "训", "讀": "读", "聽": "听", "嚐": "尝",
    "關": "关", "節": "节", "關節": "关节", "語": "语", "處": "处", "適": "适",
}
# 拼音/英文症状词音译（先长后短，防 partial 命中）
_SOUND_MAP = (
    ("xuanyun", "xuan yun", "XuanYun", "XUANYUN", "眩晕"),
    ("suan", "酸"), ("teng", "疼"), ("tong", "痛"), ("yan", "炎"),
    ("click", "弹响"), ("shoulder", "肩"), ("knee", "膝"), ("waist", "腰"),
    ("ankle", "踝"), ("elbow", "肘"), ("back", "背"), ("leg", "腿"),
    ("yo6", "有点"), ("去吐", "想吐"), ("geli", "隔离"), ("xuan", "晕"),
)


def _normalize_signal(text: str) -> str:
    """归一化：全角→半角 → 繁→简 → 音译替换。返回规范文本。"""
    s = "".join(_FULLWIDTH.get(ch, ch) for ch in (text or ""))
    for k, v in _TRAD2SIMP.items():
        s = s.replace(k, v)
    low = s.lower()
    for item in _SOUND_MAP:
        keys, val = item[:-1], item[-1]
        for k in keys:
            if k in low or k in s:
                s = s.replace(k, val)
    return s


# ---- D4 2026B 数值红线（心率/睡眠/脱水阈值）----
import re as _re

_HR_RE = _re.compile(r"(?:心率|心跳|心跳率)\s*[:：]?\s*(\d{2,3})")
_SLEEP_RE = _re.compile(r"睡\s*(\d{1,2})\s*(?:个)?小时")
_RUN_HOUR_RE = _re.compile(r"(?:连续|一直|连着)\s*(?:跑|练|训练)\s*(\d{1,2})\s*小时")
_DEHYDRATE_KW = ("一口水没喝", "没喝一口水", "没喝水", "水都没喝", "没喝过水")


def _extract_hr(signal: str) -> int | None:
    m = _HR_RE.search(signal)
    return int(m.group(1)) if m else None


def _extract_sleep_hours(signal: str) -> int | None:
    m = _SLEEP_RE.search(signal)
    return int(m.group(1)) if m else None


def _extract_run_hours(signal: str) -> int | None:
    m = _RUN_HOUR_RE.search(signal)
    return int(m.group(1)) if m else None


# 请求文本 → 动作模式映射（GD-04 pattern 级放行用）：
# "帮我安排卧推训练" → push；injury(膝) 危险模式不含 push → 放行
_REQ_PATTERN = (
    (("深蹲", "蹲腿", "保加利亚", "半蹲"), ("squat",)),
    (("箭步", "弓步", "分腿"), ("lunge",)),
    (("硬拉", "弯腰"), ("hinge",)),
    (("卧推", "推举", "俯卧撑", "推胸", "臂屈伸"), ("push",)),
    (("划船", "引体", "下拉", "练背"), ("pull",)),
    (("核心", "腹肌", "平板", "卷腹"), ("core",)),
    (("跑", "走", "跳", "深蹲跳"), ("carry",)),
    (("拉伸", "柔韧"), ("stretch",)),
)


class GuardSkill(Skill):
    name = "guard"
    description = "风险信号拦截：按已报疾病/损伤关键词命中禁忌表 → 拦截+转诊提示"
    task_types = ("guard", "fallback")
    # 记忆层 user_id（默认单用户 local；测试可注入独立 uid 隔离，防跨用例污染）
    memory_user_id = None

    def execute(self, ctx, params) -> SkillResult:
        signal = _normalize_signal(str(params.get("signal", "")))   # D3：词法伪装归一化
        uid = self._uid_for(ctx)                   # F4：记忆归属 = 会话 user_id
        # ---- 记忆：症状自报 → injury 写入（静默，不回读，不影响规则链）----
        self._memory_record(signal, uid)
        # ---- 记忆：失效/续期句与过期确认流程（先于症状模板，防"我好了"被黄拦）----
        m = self._memory_or_none()
        if m is not None:
            flow = self._injury_flow(signal, m, uid)
            if flow is not None:
                return SkillResult(ok=True, data=flow,
                                   provenance=["guard#memory.flow"])
        # C5 修复：guard 被调用即说明用户涉及健康信号，但词表/禁忌表都未命中时，
        # 不得回"一切正常"；改为引导补充不适部位，避免误导性绿灯。
        outcome = {"blocked": False, "level_label": "green", "advice": "",
                   "blocks": []}
        # W6：精神健康硬红线（自伤/自杀明示）最高优先，先于禁忌/症状/未成年
        # ——G10-12 "想伤害自己+难受" 须走心理援助话术，不得被 SYMPTOMS 截胡
        # D6 2026B：心理红线否定短路（"我没想死，就是好累" T5-14）——否定前缀的
        # 红线词不是自报，不硬拦；普通应激（压力）+评价问法（T5-17）也不硬拦。
        if any(k in signal for k in ("没想死", "不想死", "没有想死", "不自杀",
                                     "没自杀", "不想轻生")):
            return SkillResult(ok=True,
                               data={"blocked": False, "level_label": "green",
                                     "advice": "", "blocks": []},
                               provenance=["guard#mental.neg"])
        elif any(k in signal for k in ("压力大", "压力")) and any(
                k in signal for k in ("有用吗", "有效吗", "能缓解", "有帮助吗",
                                      "缓解吗")):
            return SkillResult(ok=True,
                               data={"blocked": False, "level_label": "green",
                                     "advice": "", "blocks": []},
                               provenance=["guard#distress.eval"])
        else:
            _MENTAL_HARD = ("不想活", "想不开", "自伤", "自杀", "伤害自己",
                            # D1 2026B：隐喻表达硬拦（漏则模式全错+话术缺热线）
                            "活着没意思", "活着没意义", "解脱", "一了百了", "想消失",
                            "如果我消失", "死念头", "死的念头", "自残", "想死", "轻生",
                            "跳楼", "跳下去", "自杀了", "活不下去", "浪费粮食",
                            "会有人发现", "活着干嘛", "力竭替代", "打回去", "报复",
                            "没人在乎", "没人关心", "想放手", "情绪低", "砸器械", "想砸",
                            "霸凌", "被霸凌", "报复心理", "以暴制暴")
            if any(k in signal for k in _MENTAL_HARD):
                return SkillResult(ok=True,
                                   data={"blocked": True, "level_label": "黄色",
                                         "advice": _medical_advice(signal),
                                         "blocks": []},
                                   provenance=["guard#mental.hard"])
        # D6 2026B：他人转述放行——"我自己没事，同事膝盖疼问深蹲"(T4-08)、
        # "作为他的主治医生，我建议他跑步"(T4-05) 是转述他人非本人症状；
        # 红线语境（_MEDICAL_KW 全套药词/慢病/精神 + 补剂词）仍拦：
        # T4-01 朋友肌酸、test_guard "替朋友问吃克伦特罗" 均不豁免。置于症状前。
        if any(k in signal for k in ("同事", "朋友", "别人", "同学", "他",
                                     "她")) and not any(
                k in signal.lower() for k in _MEDICAL_KW) and not any(
                k in signal for k in ("肌酸", "补剂", "蛋白粉", "氮泵",
                                      "增肌粉")):
            return SkillResult(ok=True,
                               data={"blocked": False, "level_label": "green",
                                     "advice": "", "blocks": []},
                               provenance=["guard#proxy.other"])
        # D6 2026B 症状疑问句（"我膝盖疼吗/我之前膝盖不是疼吗"）：是查询/回忆
        # 不是症状自报 → 不硬拦，交 direct/qa（T9-10/12；本户无 active injury 前提）
        if (_re.search(r"(?:疼|痛|酸|胀|麻|晕|难受|紧)\s*吗", signal)
                or "疼不疼" in signal or "痛不痛" in signal):
            return SkillResult(ok=True,
                               data={"blocked": False, "level_label": "yellow",
                                     "advice": "", "blocks": []},
                               provenance=["guard#query.self"])
        # D4 2026B 数值红线（先于禁忌/症状；心率/睡眠/脱水极值 → 硬拦）
        _hr = _extract_hr(signal)
        if _hr is not None and (_hr >= 190 or _hr <= 40):
            return SkillResult(ok=True,
                               data={"blocked": True, "level_label": "黄色",
                                     "advice": (f"你提到的运动心率（{_hr} 次/分）已属"
                                                "危险区间，建议立即停止训练、充分休息，"
                                                "并尽快咨询医生评估心肺状态，切勿继续"
                                                "高强度运动。"),
                                     "blocks": []},
                               provenance=["guard#hr.extreme"])
        if _hr is not None and _hr >= 120:
            return SkillResult(ok=True,
                               data={"blocked": True, "level_label": "黄色",
                                     "advice": (f"你提到的运动心率（{_hr} 次/分）偏高，"
                                                "高强度训练下存在心血管风险，建议先"
                                                "降低强度、监测心率，并咨询专业人士评估"
                                                "后再继续。"),
                                     "blocks": []},
                               provenance=["guard#hr.high"])
        _sl = _extract_sleep_hours(signal)
        if _sl is not None and _sl <= 3:
            return SkillResult(ok=True,
                               data={"blocked": True, "level_label": "黄色",
                                     "advice": (f"你提到只睡了 {_sl} 小时，睡眠严重不足"
                                                "状态下高强度训练易引发运动损伤或心脏"
                                                "风险，建议今天优先休息补觉，训练顺延"
                                                "到状态恢复后。"),
                                     "blocks": []},
                               provenance=["guard#sleep.short"])
        if any(k in signal for k in _DEHYDRATE_KW):
            return SkillResult(ok=True,
                               data={"blocked": True, "level_label": "黄色",
                                     "advice": "长时间运动且没有补充水分极易脱水，严重时"
                                               "可能引发热射病或肾脏损伤。建议立即补水、"
                                               "暂停训练，出现头晕心慌请及时就医。",
                                     "blocks": []},
                               provenance=["guard#dehydrate"])
        # condition_zh 是长描述（如"腰椎间盘突出/坐骨神经痛（活动期或明显症状）"），
        # 取首个"/"前核心病名词应对信号做子串匹配
        hits = []
        for cond in self._conditions():
            head = cond.split("（")[0].split("/")[0]
            if head and head in signal:
                hits.append(cond)
        if hits:
            out = screening.plan_check(hits, ["push", "pull", "squat", "hinge",
                                              "lunge", "core", "carry", "stretch"])
            # plan_check 返回 level/level_label/blocks/warnings（无 advice 键，需合成）
            advice = "；".join(out["warnings"])
            if out["level"] != "green":
                advice = (advice + "；" if advice else "") + (
                    "建议先咨询医生或专业康复师评估后再安排训练")
            outcome = {"blocked": out["level"] != "green",
                       "level_label": out["level_label"], "advice": advice,
                       "blocks": out["blocks"]}
        elif _is_minor_context(signal) and _is_intensity_context(signal):
            # W1 R2：未成年×高强度语境（12/16岁上大重量/举铁）→ 未成年专项话术
            # （独立于 EXTRA：'16岁/大重量' 不在 EXTRA 词表，L0 由复合规则路由）
            outcome = {"blocked": True, "level_label": "黄色",
                       "advice": ("未成年人进行高强度力量训练需谨慎：建议在专业教练或"
                                  "老师指导下循序渐进、小重量起步，并先咨询医生评估"
                                  "身体发育情况，不建议盲目上大重量或冲刺记录。"),
                       "blocks": []}
        elif _is_minor_context(signal) and any(k in signal for k in ("减肥", "减脂",
                                                                     "节食", "瘦身",
                                                                     "过度训练")):
            # D6 2026B：未成年×减重施压（T4-18 我妈逼我15岁减肥）→ 保守拦截
            outcome = {"blocked": True, "level_label": "黄色",
                       "advice": ("未成年人被施压减重需谨慎：身体处于发育期，盲目减脂"
                                  "可能影响生长发育和营养摄入。建议与家长或学校老师"
                                  "沟通，并在医生或专业营养师评估下进行，不建议节食"
                                  "或极端减重。"),
                       "blocks": []}
        elif _is_minor_context(signal) and any(k in signal for k in ("肌酸", "补剂",
                                                                     "蛋白粉", "增肌粉",
                                                                     "支链", "氮泵",
                                                                     "增肌剂")):
            # W2：未成年×补剂/增肌剂（G10-10 16岁喝肌酸）→ 保守拦截，交医生评估。
            # 提前于 SYMPTOMS：'肌酸'含单字'酸'，否则会被良性软症状误放行（回归）
            outcome = {"blocked": True, "level_label": "黄色",
                       "advice": ("未成年人使用补剂（肌酸/蛋白粉等）需谨慎："
                                  "身体仍在发育，盲目补充可能影响生长发育。建议先咨询"
                                  "医生或专业营养师评估，不建议自行服用或跟风使用。"),
                       "blocks": []}
        elif any(k in signal.lower() for k in SYMPTOMS):
            if self._benign_dominant(signal):
                outcome = {"blocked": False, "level_label": "yellow", "advice": "",
                           "blocks": []}
            else:
                outcome = {"blocked": True, "level_label": "黄色",
                           "advice": "出现症状建议暂停训练、充分休息并咨询医生，切勿硬撑", "blocks": []}
        elif any(k in signal.lower() for k in GUARD_SIGNAL_EXTRA):
            if any(k in signal for k in ("姨妈", "经期", "月经", "量多", "量少")):
                # 经期是正常生理阶段非伤病；"能练腰腹吗"属训练安排咨询 → 交 direct
                outcome = {"blocked": False, "level_label": "yellow", "advice": "",
                           "blocks": []}
                return SkillResult(ok=True, data=outcome,
                                   provenance=["guard#menstrual.benign"])
            # W1 R2：路由层 GUARD_SIGNAL_EXTRA 命中（腰/膝/药名/未成年/孕产/就医对抗/
            # 高温等风险信号）→ 必须真实拦截，不得因 skill 内部词表未命中而 blocked=False
            # 降级 direct（压测 G1-09 等 '路由 guard 却直答' 根因）。
            # 优先级：医疗/药物 → 医疗模板 B；未成年×强度 → 未成年专项；
            # 环境风险 → 环境专项；其余部位/泛风险词 → 风险话术（C5 永不绿灯）。
            if any(k in signal.lower() for k in _MEDICAL_KW):
                outcome = {"blocked": True, "level_label": "黄色",
                           "advice": _medical_advice(signal), "blocks": []}
            elif any(k in signal.lower() for k in _ENV_KW):
                outcome = {"blocked": True, "level_label": "黄色",
                           "advice": ("高温/恶劣环境不建议高强度户外训练：建议避开高温时间"
                                      "（清晨或傍晚）、及时补水，出现头晕、心慌或不适立即"
                                      "停止并休息，切勿硬撑。"),
                           "blocks": []}
            else:
                outcome = {"blocked": True, "level_label": "黄色",
                           "advice": GuardSkill._risk_advice(signal), "blocks": []}
        elif any(k in signal.lower() for k in _MEDICAL_KW):
            # F1 红线：医疗词命中时**永不 benign 逃逸**——独立前置分支。
            # 此前"克伦特罗吃多少"命中 _unblocked_fallback(多少) → 跳过医疗模板落绿灯。
            outcome = {"blocked": True, "level_label": "黄色",
                       "advice": _medical_advice(signal), "blocks": []}
        elif not self._unblocked_fallback(signal):
            # 意图 guard 但词表/禁忌表都未命中（语义层/LLM 触发）：
            # 双模板选择，绝不说"一切正常"（C5）
            outcome = self._unspecified(signal)
        # ---- 记忆：主动禁忌交叉（规则链未拦 + 训练信号 + 已记录 injury）----
        if not outcome.get("blocked") and m is not None:
            intercept = self._memory_intercept(signal, m, uid)
            if intercept is not None:
                outcome = intercept
                return SkillResult(ok=True, data=outcome,
                                   provenance=["guard#memory.about"])
        src = ["guard#screening.plan_check"] if hits else ["guard#keyword"]
        return SkillResult(ok=True, data=outcome, provenance=src)

    # ------------------------------------------------------------ 记忆层
    @staticmethod
    def _memory_or_none():
        """MemoryStore（可空）；任何异常 → None（降级链不变）。"""
        try:
            from app.graph.memory import MemoryStore
            return MemoryStore.get()
        except Exception:
            return None

    def _uid_for(self, ctx) -> str:
        """F4：记忆归属优先级——测试注入 memory_user_id > 会话 user_id > local。
        （memory_user_id 优先保证记忆专项测试隔离契约不因会话默认 local 破坏）"""
        from app.graph.memory import MEMORY_USER_ID
        if self.memory_user_id:
            return self.memory_user_id
        if ctx is not None and getattr(ctx, "session", None) is not None:
            su = getattr(ctx.session, "user_id", None)
            if su:
                return su
        return MEMORY_USER_ID

    def _memory_record(self, signal: str, uid: str) -> None:
        """症状自报 → injury 记忆（部位抽取；静默，抽不中宁缺毋滥）。"""
        try:
            from app.graph.memory import extract_injury_site
            m = self._memory_or_none()
            if m is None:
                return
            if not any(k in signal for k in SYMPTOMS):
                return
            site = extract_injury_site(signal)
            if site is None:
                return
            zh, _key = site
            m.upsert_state(uid, "injury", "不适", about=zh)
        except Exception:
            pass

    @staticmethod
    def _find_injury(m, uid: str, site_zh: str) -> dict | None:
        """找该部位 active 或过期的 injury fact（确认/续期用）。"""
        for r in m.current(uid, "injury", include_expired=True):
            if r.get("about") == site_zh:
                return r
        return None

    def _injury_flow(self, signal: str, m, uid: str) -> dict | None:
        """记忆 injury 的确认/续期/过期流程：
        心理红线词（想放手/解脱等）优先于康复确认——"对训练没感觉了，想放手"含
        done 词"没感觉"但实为心理信号，不得被 green 吞掉（T5-05）。
        - '我X不疼了/好了' → close，green 放行+确认话术（防被症状模板误黄拦）
        - '我X还疼/没好' → renew(30d)，黄色提醒
        - 有过期 injury + 训练语境 → 确认话术（不拦，yellow）
        仅训练语境或确认/续期词才进入，防打扰普通闲聊。"""
        if any(k in signal for k in _MENTAL_KW):
            return None
        done = any(k in signal for k in _CNF_DONE)
        keep = any(k in signal for k in _CNF_KEEP)
        plan = any(k in signal for k in _PLAN_SIGNAL)
        if not (plan or done or keep):
            return None
        # W1 R2：句含急性/新发症状词（弹响/咯噔/摩擦/闪/咔嚓/响）且带 done 词（"没事了/
        # 不疼"）→ 是"刚发生的异常事件+无痛补充"，不是旧伤康复确认 → 不走 close，
        # 交回常规症状拦截（G1-04/14/18 曾因 close 误放行 green；D2：T2-02
        # "膝盖不疼了但一蹲就响"——done 词消除后仍有其它症状词 → 非康复）
        if done:
            if any(k in signal for k in _ACUTE_KW):
                return None
            # 把 done 词本身（"不疼/不痛/不难受"）剔除后，若仍命中症状词（"响"/
            # "肿"等），说明还有未愈症状残留，不得 close
            residue = signal
            for k in _CNF_DONE:
                residue = residue.replace(k, "")
            if any(k in residue for k in SYMPTOMS):
                return None
        try:
            from app.graph.memory import extract_injury_site
            site = extract_injury_site(signal)
            if done or keep:
                if site is None:
                    # D2 2026B：重度康复回应无部位（"好了好了不疼了，别啰嗦" T6-19）
                    # ——剔除 done 词后已无残留症状（上层 ACUTE/residue 均放过），
                    # 是无具体部位的泛康复确认，不得再被 SYMPTOMS 误黄拦 → 放行。
                    # 有部位走下方 fact close 流程；T2-02 类有残留症状在上层已 return None。
                    # 收紧：含医嘱对抗/转折确认词（"医生让我休息可我觉得好了" T2-16、
                    # "不疼就是抬不起胳膊" T2-11）不是纯康复回应，交回常规拦截。
                    if done and not any(k in signal for k in (
                            "医生", "但", "可是", "就是", "但是", "然而", "不过")):
                        return {"blocked": False, "level_label": "green",
                                "advice": "收到。恢复训练请从低强度开始，循序渐进，"
                                          "给身体足够适应时间。",
                                "blocks": []}
                    return None
                zh, _key = site
                fact = self._find_injury(m, uid, zh)
                if fact:
                    if keep:
                        m.renew(uid, fact["fact_id"])
                        return {"blocked": False, "level_label": "黄色",
                                "advice": f"了解，{zh}部仍有不适。建议暂停相关训练并"
                                          "咨询医生，切勿硬撑；已为你记录持续状态。",
                                "blocks": []}
                    if done:
                        m.close(uid, fact["fact_id"])
                        return {"blocked": False, "level_label": "green",
                                "advice": f"收到，{zh}部不适已记为康复。恢复训练请从低"
                                          "强度开始，循序渐进。",
                                "blocks": []}
            # 无回答句但有过期 injury + 计划语境 → 确认话术
            prompts = m.expire_prompts(uid)
            if prompts and plan:
                p = prompts[0]
                zh = str(p.get("about") or "身体")
                days = int(p.get("days_since") or 0)
                # D5 2026B：过期伤+训练请求 → 硬拦（T9-05 45天前腰伤+帮我排训练），
                # 先确认状态再放行，不得直接进 plan_exec
                return {"blocked": True, "level_label": "黄色",
                        "advice": f"你 {days} 天前提过{zh}部不适，现在还有不适吗？"
                                  "如果已经好了告诉我，我会帮你更新记录；"
                                  "如果仍不适，建议先暂停相关训练并咨询医生。",
                        "blocks": []}
        except Exception:
            pass
        return None

    def _memory_intercept(self, signal: str, m, uid: str) -> dict | None:
        """主动禁忌交叉：未自报但记忆有 active injury + 计划/安排语境 →
        按请求动作模式 × 禁忌危险模式交叉（GD-04：膝不阻 push 卧推）；
        泛计划请求（未指明动作）→ 有禁忌部位即保守拦截。
        记忆部位词（膝/腰/肩…）经 contraindication 子串命中禁忌表疾病。"""
        try:
            if not any(k in signal for k in _PLAN_SIGNAL):
                return None
            inj = m.current_about(uid, "injury")      # 未过期 active
            if not inj:
                return None
            req_pats = set()
            for kws, pats in _REQ_PATTERN:
                if any(k in signal for k in kws):
                    req_pats.update(pats)
            hits = []                                          # (condition_zh, risk, danger∩req)
            generic = not req_pats
            for part in inj:
                entry = screening.contraindication(part)
                if entry is None:
                    continue
                danger = set(entry["danger_patterns"])
                if generic:
                    hits.append((entry["condition_zh"],
                                 entry["risk_level"], None))   # 泛请求保守拦
                elif danger & req_pats:
                    hits.append((entry["condition_zh"],
                                 entry["risk_level"],
                                 sorted(danger & req_pats)))
            if not hits:
                return None
            conds = "、".join(part for part in inj)
            detail = "；".join(
                f"{c}（危险模式 {','.join(p)}）" if p is not None else c
                for c, _r, p in hits)
            return {"blocked": True, "level_label": "黄色",
                    "advice": (f"根据你的历史记录，之前曾提到{conds}不适（记忆来源），"
                               f"本次训练安排命中禁忌：{detail}。"
                               "建议先咨询医生或专业康复师评估，再决定训练安排。"),
                    "blocks": [
                        {"condition": c, "risk_level": r, "hit_patterns": p or []}
                        for c, r, p in hits]}
        except Exception:
            return None

    @staticmethod
    def _unspecified_advice(signal: str) -> str:
        """疑似症状/风险语境但具体词表未命中 → 引导补充话术（C5 永不绿灯）。"""
        return ("你提到了身体状况相关的问题，但我没识别到具体症状。"
                "请描述不适的部位和感觉（如哪里疼、什么情况下出现），"
                "我会更安全地分析。")

    @staticmethod
    def _risk_advice(signal: str) -> str:
        """部位/泛风险信号（腰/膝/睡眠不足等）→ 风险话术：含暂停/休息/不建议/医生。"""
        return ("这涉及身体状况或训练风险，建议先暂停相关训练、充分休息，不建议硬撑；"
                "如持续不适或症状加重，请及时咨询医生。")

    @staticmethod
    def _unspecified(signal: str) -> dict:
        """意图 guard 但词表/禁忌表未命中时的双模板（压测 v2 W0）：
        - 医疗咨询词命中（药物/慢病/孕产/极端行为/精神健康）→ 模板 B（复用
          _medical_advice 单源：不做医疗建议+咨询医生+一般安全原则+不建议）；
        - 否则（疑似症状但词表漏）→ 引导补充模板，blocked=True（C5：guard 被调用
          即涉及健康信号，不得降级直答；#82/#88 实证有效）。"""
        if any(k in signal.lower() for k in _MEDICAL_KW):
            return {"blocked": True, "level_label": "黄色",
                    "advice": _medical_advice(signal), "blocks": []}
        # C5 修正（W1 回归）：fallback 分支不得硬拦普通句——guard 节点无条件
        # 前置执行，blocked=True 会吞掉所有词表未命中的正常请求（"帮我安排训练
        # 计划"/"你好"/纯标点等，压测全量 59 OK 大面积过拦根因）。真风险句均由
        # SYMPTOMS/EXTRA/MEDICAL/未成年专项/禁忌表硬拦；此处仅软提示 yellow。
        return {"blocked": False, "level_label": "yellow",
                "advice": GuardSkill._unspecified_advice(signal), "blocks": []}

    @staticmethod
    def _benign_dominant(signal: str) -> bool:
        """W2 规划主导/良性观察覆盖：句含明确规划意图（重新规划/安排训练计划等）
        或良性自评（没练到位/不酸），且仅软症状（不舒服/酸/胀）→ 不硬拦，交 direct
        （G8-14 超长规划句含'膝盖有点不舒服'过拦根因；G1-06 等）。硬症状/医疗/伤仍拦。"""
        # 规划主导：强烈训练规划意图，即使附带软不适也应回答而非拦截
        _PLAN_DOM = ("重新规划", "重新安排", "安排训练计划", "规划一下", "怎么开始",
                     "系统的", "系统", "重新")
        _SOFT_SYM = ("不舒服", "有点", "酸", "胀")
        if any(k in signal for k in _PLAN_DOM):
            # 仅当不适是软性描述（非疼/痛/断/麻等硬症）才放行
            hard = any(k in signal for k in ("疼", "痛", "骨折", "断", "脱臼", "麻木",
                                             "麻", "刺痛", "发炎", "晕", "想吐", "呕吐"))
            return not hard
        # D2 2026B：转折/承认词——否定豁免的作用域限制。句含"但/就是/其实/还有"
        # 等转折确认词时，说明前否定后被症状回摆（"不酸，就是胀得难受""不发烧但
        # 喉咙有痰"）→ 不得整句豁免，交常规拦截（T2-05/T6-10/T6-16 根因）。
        _TURN = ("但", "可是", "就是", "不过", "反而", "其实", "除了", "还有",
                 "然后", "但是", "然而", "可 ", "还好吗", "有点")
        # 良性自评：否定的症状词
        if ("没练到位" in signal or "没到位" in signal or "不酸" in signal):
            if any(k in signal for k in _TURN):
                return False
            return True
        # 补剂/药物词含"酸/粉"字但非症状（肌酸/蛋白粉/氮泵）→ 永不走软症状豁免
        # （T4-01"我朋友想喝肌酸"被"酸"软症状放行根因）
        _SUPP = ("肌酸", "蛋白粉", "氮泵", "增肌粉", "支链", "粉剂")
        if any(k in signal for k in _SUPP):
            return False
        # 无部位单字软症状（酸/胀/软/紧）+ 无硬症状 → 泛疲劳观察（G6-04 练完还酸）。
        # 语义规避：承认类转折（其实/好吧/还是有点/就是）+ 软症状 = 承认有不适，
        # 不得当泛疲劳放行（T6-12"好吧其实有点酸"→拦；G6-04"练完还酸"→放行）
        _SOFT = ("酸", "胀", "软", "紧", "无力")
        _PART = ("肩", "膝", "腰", "腕", "肘", "踝", "颈", "背", "髋", "腿",
                 "脚", "手", "手肘", "小腿", "大腿", "胸", "下背", "关节", "骨头",
                 "跟腱", "肩袖", "手指", "胳膊", "上臂", "手臂",
                 # D3 2026B：肌肉发酸属有部位症状（T1-19 繁体混写）
                 "肌肉")
        _HARD = ("疼", "痛", "骨折", "断", "脱臼", "麻木", "麻", "刺痛", "发炎",
                 "晕", "想吐", "呕吐", "撕裂", "扭", "耳鸣", "嗡嗡", "疲惫")
        if any(k in signal for k in _SOFT) and not any(k in signal for k in _PART):
            if not any(k in signal for k in _HARD):
                if any(k in signal for k in _TURN):
                    return False
                return True
        # 否定的发热词（G6-05 感冒流鼻涕没发烧）：无发烧属轻症,可中低强度；
        # 同样受转折词限制（T6-10"不发烧但喉咙有痰"须拦截）
        if ("没发烧" in signal or "不发烧" in signal or "没发热" in signal
                or "不发热" in signal):
            if any(k in signal for k in _TURN):
                return False
            return True
        return False

    @staticmethod
    def _conditions() -> list[str]:
        return [c["condition_zh"] for c in screening.CONDITIONS]

    @staticmethod
    def _unblocked_fallback(signal: str) -> bool:
        """非安全语境直接放行（信号不含任何健康/训练风险特征词）。
        供词表未命中分支判断：纯普通问句（如"深蹲怎么做"）不经语义 guard 应走原逻辑。
        保守起见：只要不是明显普通句就引导补充，宁可多问不误导。"""
        benign = ("怎么做", "怎么练", "要领", "区别", "哪个好", "啥是",
                  "怎么入门", "多久见效", "多少", "热量", "蛋白质")
        return any(k in signal for k in benign)
