# -*- coding: utf-8 -*-
"""风险守卫：规则拦截最优先，任何模式不得绕过。非诊断提示。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult
from app.core.vocab import GUARD_SYMPTOMS
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
               "糖尿病", "高血压", "血压", "心脏", "支架", "心梗", "心衰",
               "怀孕", "孕期", "孕妇", "备孕", "哺乳",
               "断食", "禁食", "催吐", "只喝水",
               "抑郁", "焦虑", "躁郁", "低落", "情绪低落", "精神", "心理")

# ---- 记忆层（个人记忆 spec v0.3 §3/§5：guard 消费 active injury）----
# 计划/安排语境词：主动禁忌交叉只对"生成方案"类请求触发（被拦=阻止危险方案）；
# 教学/知识问句（"深蹲怎么做"）不进交叉，防过度保护掩盖正常问答。
_PLAN_SIGNAL = ("计划", "方案", "安排", "课表", "规划", "帮我排", "给我排",
                "训练安排", "编排", "怎么排", "一周练", "练什么", "练哪里",
                "推荐几个动作", "做哪些动作", "推荐动作", "训练表")

# 失效/续期句（对确认问题的回答或主动改口）
_CNF_DONE = ("不疼", "不痛", "好了", "没感觉", "没事了", "不难受", "康复", "恢复了")
_CNF_KEEP = ("还疼", "还在疼", "没好", "还是疼", "更疼", "没缓", "没好转", "一直疼")

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
        signal = str(params.get("signal", ""))
        # ---- 记忆：症状自报 → injury 写入（静默，不回读，不影响规则链）----
        self._memory_record(signal)
        # ---- 记忆：失效/续期句与过期确认流程（先于症状模板，防"我好了"被黄拦）----
        m = self._memory_or_none()
        if m is not None:
            flow = self._injury_flow(signal, m)
            if flow is not None:
                return SkillResult(ok=True, data=flow,
                                   provenance=["guard#memory.flow"])
        # C5 修复：guard 被调用即说明用户涉及健康信号，但词表/禁忌表都未命中时，
        # 不得回"一切正常"；改为引导补充不适部位，避免误导性绿灯。
        outcome = {"blocked": False, "level_label": "green", "advice": "",
                   "blocks": []}
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
            advice = "；".join(out["warnings"]) or (
                "已报禁忌疾病：建议在医生/康复师评估后再安排训练"
                if out["level"] != "green" else "")
            outcome = {"blocked": out["level"] != "green",
                       "level_label": out["level_label"], "advice": advice,
                       "blocks": out["blocks"]}
        elif any(k in signal.lower() for k in SYMPTOMS):
            outcome = {"blocked": True, "level_label": "黄色",
                       "advice": "出现症状建议暂停训练并咨询医生，切勿硬撑", "blocks": []}
        elif not self._unblocked_fallback(signal):
            # 意图 guard 但词表/禁忌表都未命中（语义层/LLM 触发）：
            # 双模板选择，绝不说"一切正常"（C5）
            outcome = self._unspecified(signal)
        # ---- 记忆：主动禁忌交叉（规则链未拦 + 训练信号 + 已记录 injury）----
        if not outcome.get("blocked") and m is not None:
            intercept = self._memory_intercept(signal, m)
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

    def _uid(self) -> str:
        from app.graph.memory import MEMORY_USER_ID
        return self.memory_user_id or MEMORY_USER_ID

    def _memory_record(self, signal: str) -> None:
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
            m.upsert_state(self._uid(), "injury", "不适", about=zh)
        except Exception:
            pass

    @staticmethod
    def _find_injury(m, uid: str, site_zh: str) -> dict | None:
        """找该部位 active 或过期的 injury fact（确认/续期用）。"""
        for r in m.current(uid, "injury", include_expired=True):
            if r.get("about") == site_zh:
                return r
        return None

    def _injury_flow(self, signal: str, m) -> dict | None:
        """记忆 injury 的确认/续期/过期流程：
        - '我X不疼了/好了' → close，green 放行+确认话术（防被症状模板误黄拦）
        - '我X还疼/没好' → renew(30d)，黄色提醒
        - 有过期 injury + 训练语境 → 确认话术（不拦，yellow）
        仅训练语境或确认/续期词才进入，防打扰普通闲聊。"""
        done = any(k in signal for k in _CNF_DONE)
        keep = any(k in signal for k in _CNF_KEEP)
        plan = any(k in signal for k in _PLAN_SIGNAL)
        if not (plan or done or keep):
            return None
        try:
            from app.graph.memory import extract_injury_site
            site = extract_injury_site(signal)
            if done or keep:
                if site is None:
                    return None
                zh, _key = site
                fact = self._find_injury(m, self._uid(), zh)
                if fact:
                    if keep:
                        m.renew(self._uid(), fact["fact_id"])
                        return {"blocked": False, "level_label": "黄色",
                                "advice": f"了解，{zh}部仍有不适。建议暂停相关训练并"
                                          "咨询医生，切勿硬撑；已为你记录持续状态。",
                                "blocks": []}
                    if done:
                        m.close(self._uid(), fact["fact_id"])
                        return {"blocked": False, "level_label": "green",
                                "advice": f"收到，{zh}部不适已记为康复。恢复训练请从低"
                                          "强度开始，循序渐进。",
                                "blocks": []}
            # 无回答句但有过期 injury + 计划语境 → 确认话术
            prompts = m.expire_prompts(self._uid())
            if prompts and plan:
                p = prompts[0]
                zh = str(p.get("about") or "身体")
                days = int(p.get("days_since") or 0)
                return {"blocked": False, "level_label": "yellow",
                        "advice": f"你 {days} 天前提过{zh}部不适，现在还有不适吗？"
                                  "如果已经好了告诉我，我会帮你更新记录；"
                                  "如果仍不适，建议先暂停相关训练并咨询医生。",
                        "blocks": []}
        except Exception:
            pass
        return None

    def _memory_intercept(self, signal: str, m) -> dict | None:
        """主动禁忌交叉：未自报但记忆有 active injury + 计划/安排语境 →
        按请求动作模式 × 禁忌危险模式交叉（GD-04：膝不阻 push 卧推）；
        泛计划请求（未指明动作）→ 有禁忌部位即保守拦截。
        记忆部位词（膝/腰/肩…）经 contraindication 子串命中禁忌表疾病。"""
        try:
            if not any(k in signal for k in _PLAN_SIGNAL):
                return None
            inj = m.current_about(self._uid(), "injury")      # 未过期 active
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
    def _unspecified(signal: str) -> dict:
        """意图 guard 但词表/禁忌表未命中时的双模板（压测 v2 W0）：
        - 医疗咨询词命中（药物/慢病/孕产/极端行为/精神健康）→ 模板 B：不做医疗
          建议 + 咨询医生/专业人士 + 一般安全原则（禁再套"哪里疼"）；
        - 否则（疑似症状但词表漏）→ 原引导补充模板（#82/#88 实证有效）。"""
        if any(k in signal.lower() for k in _MEDICAL_KW):
            return {"blocked": True, "level_label": "黄色",
                    "advice": ("你提到的情况涉及用药、疾病或特殊身体状况，我不能给医疗建议。"
                               "请先咨询医生或专业人士评估后再安排训练；"
                               "一般性原则是训练循序渐进，出现不适立即停止。"),
                    "blocks": []}
        return {"blocked": False, "level_label": "yellow",
                "advice": ("你提到了身体状况相关的问题，但我没识别到具体症状。"
                           "请描述不适的部位和感觉（如哪里疼、什么情况下出现），"
                           "我会更安全地分析。"),
                "blocks": []}

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
