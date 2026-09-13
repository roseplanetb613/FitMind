# -*- coding: utf-8 -*-
"""LangGraph 节点（闭包工厂）：guard/classify/execute/plan/aggregate/think/act/
validate/render。全部复用现有领域层：skills、runtime.validator、router。"""
from __future__ import annotations
import json
import re
from datetime import date, timedelta
from app.core import progress
from app.core.render_util import training_lines
from app.core.router import Mode, RouteClassifier, route
from app.skills.base import ExecutionContext, SkillResult
from app.runtime.validator import RuleValidator

MAX_STEPS = 4
MAX_PLAN = 4
# 消歧选择框给几个动作。**3 个**（用户定）：再多就不如直接自己打字了，
# 所以下面还配了一个自定义输入口 —— 三个候选 + 一个"都不是，我自己写"。
MAX_CLARIFY_OPTIONS = 3

# 上下文消解：肯定应答精确匹配集（剥标点后整词比对，防"好吗/行动"类子串误伤）
# 2026-09-11 补选择式应答（"就这个计划"）：此前只认 确认/好的 等短应答，
# 用户用"就这个计划"承接提议时落 _is_vague_reference → clarify，承接通道失效。
_AFFIRM_WORDS = frozenset({
    "确认", "好的", "好", "可以", "行", "嗯", "嗯嗯", "要", "没问题",
    "好呀", "好啊", "好吧", "行吧", "ok", "OK", "Ok",
    "就这个", "就这个计划", "就按这个", "按这个", "按这个来", "就要这个",
    "就用这个", "就它", "这个吧"})
# 上轮提议标记：提议动作 + plan 名词 → 肯定应答承接为 plan
_OFFER_MARKS = ("我可以", "可以帮你", "要不要", "如果你想", "需要我",
                "帮你排", "帮你制定", "帮你把")
_PLAN_NOUNS = ("计划", "课表", "训练表")
# 编排提议的指代词（2026-09-11）：teach 的编排推荐语用"这套循环/这个循环"收尾
# （"如果你想按这个循环把具体动作排进去…我来帮你搭"），不含"计划"三词之一，
# 导致承接门开不了。编排语境下这三个词与 plan 名词等价。
_CYCLE_NOUNS = ("循环", "这套", "这个安排")

# E2 通识标注（W2 分级）：LLM 提示词约束不可信——实测把标注用在了有库内出处的
# 回答上（例："练三休一"命中 teach#split_kb 却标成通识）。代码级兜底：
# knowledge 分支标注必达，非 knowledge 分支一律剥除。半角/逗号变体一并覆盖。
_KNOWLEDGE_LABEL = "（通用训练知识，非库内收录）"
_KNOWLEDGE_LABEL_RE = re.compile(r"\s*[（(]通用训练知识[，,、]?\s*非库内收录[）)]")


def _effective_data_kind(structured: dict) -> str:
    """空结果分级判定（W2）：缺省/未知一律按 data——宁严勿宽，维持禁虚构约束。
    'knowledge'（编排原理等通识）须由 skill 侧显式打标，渲染层不猜。"""
    dk = (structured.get("data") or {}).get("data_kind")
    return "knowledge" if dk == "knowledge" else "data"


# O-4 对侧（2026-09-11）：无写入却自称写入 = 假确认。提示词规则 7 已明令禁止，
# 但实测 LLM 照说不误——"已记下：你要三分化、练三休一"出现时 memory_ack 为空、
# 图谱 preference 行数为 0，用户据此以为已记账。故与 E2 同法：代码级剥除。
_FAKE_ACK_RE = re.compile(r"已记下[：:][^。\n]*。?[ \t]*")


def _strip_fake_ack(reply: str) -> str:
    """acks 为空时剥除幻觉的"已记下：…"（只认冒号式断言，不误伤"没记下"类表述）。"""
    if not reply or "已记下" not in reply:
        return reply
    out = _FAKE_ACK_RE.sub("", reply)
    out = re.sub(r"[ \t]+\n", "\n", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


# 计划改动断言的依据校验（2026-09-11）：提示词不可信，凡"声称改动了计划"必须由
# provenance 支撑。CLI 实证：数据是 9/11 推日…9/14 核心日（**无 plan#edit**），回复
# 却叙述"把整周安排整体后延一天"并**编造出数据里不存在的 9月15日**——用户会照着
# 一个不存在的日程训练。与 _strip_fake_ack 同源思路：代码级兜底，不赌 LLM 自觉。
# 词形取"改变计划结构"的完成态断言；单字/疑问式不入表（"如果要去掉某个动作…"
# 是建议而非断言，不该被降级——误判代价是渲染退化，漏判代价是用户被误导，
# 故宁可略偏保守地多收完成态词形）。
_PLAN_EDIT_CLAIM_KW = ("已改", "已经改", "已调整", "已后延", "已延", "已顺延",
                       "已推迟", "已平移", "已挪", "后延", "延后", "顺延", "推迟",
                       "平移", "往后挪", "挪到", "已排到", "已去掉", "去掉了",
                       "已撤", "撤掉了", "已换", "换成了")
# 编辑**失败**的 provenance 结尾（"无法解析/未找到/无计划"等如实拒绝不算改动依据）
_PLAN_EDIT_FAIL = (".parse_fail", ".not_found", ".no_plan", ".no_base",
                   ".guard", ".pattern_mismatch", ".y_not_found")


def _edit_ok_provenance(sources) -> bool:
    """sources 里是否存在**成功**的计划编辑依据。"""
    for s in sources or []:
        s = str(s)
        if s.startswith("plan#edit") and not s.endswith(_PLAN_EDIT_FAIL):
            return True
    return False


def _fabricated_plan_edit(reply: str, sources) -> bool:
    """reply 声称改动了计划，却无成功编辑依据 → 判幻觉（调用方降级零幻觉渲染）。"""
    if not reply or not any(k in reply for k in _PLAN_EDIT_CLAIM_KW):
        return False
    return not _edit_ok_provenance(sources)


# 日期锚点断言（2026-09-11 D2 渲染侧残留）：数据锚点已按"今天"重算，但 LLM 仍按
# "今天（X月Y日）"模板输出 → 把 9月9日 称作"今天"（实测）。只认**带具体日期**的
# 今天/明天锚点，不误伤"今天就休息"这类无数值表述。
_ANCHOR_CLAIM_RE = re.compile(r"(今天|明天)[（(]\s*(\d{1,2})月(\d{1,2})日")


def _fabricated_date_anchor(reply: str, structured: dict,
                            today: date | None = None) -> bool:
    """回复把某个具体旧日期称作"今天/明天" → 判幻觉（调用方降级零幻觉渲染）。
    仅对含 training.items 的计划类数据生效；无计划数据不适用（聊天里说"今天"很正常）。"""
    if not reply:
        return False
    items = ((structured.get("data") or {}).get("training") or {}).get("items") or []
    if not items:
        return False
    t = today or date.today()
    expect = {"今天": t, "明天": t + timedelta(days=1)}
    for m in _ANCHOR_CLAIM_RE.finditer(reply):
        want = expect.get(m.group(1))
        try:
            got = (int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if want is not None and got != (want.month, want.day):
            return True
    return False


def _enforce_knowledge_label(reply: str, structured: dict) -> str:
    """E2：通识标注确定性兜底（同 O-4 思路，不依赖提示词自觉）。
    knowledge 分支 → 标注必达；其余分支 → 越界标注剥除（库内有出处的回答
    不得标成通识，否则用户会误以为系统没查到）。"""
    if _effective_data_kind(structured) == "knowledge":
        if _KNOWLEDGE_LABEL_RE.search(reply or ""):
            return reply
        return ((reply or "").rstrip() + "\n\n" + _KNOWLEDGE_LABEL).strip()
    return _KNOWLEDGE_LABEL_RE.sub("", reply or "").rstrip()


def _affirm_plan_intent(msg: str, history: list) -> dict | None:
    """肯定应答 + 上轮 plan 提议 → 承接提议意图（CLI 实证："确认"承接
    "我可以帮你把这套循环排成一周的具体计划"，曾丢上下文落 qa 空检索）。
    窄口径宁缺毋滥：肯定词须整词命中；上轮助手消息须同时含提议标记与
    plan 名词（或编排指代名词 _CYCLE_NOUNS）。其余情形返回 None 走正常分类。"""
    m = (msg or "").strip().strip("。，！!？?、~ ")
    if m not in _AFFIRM_WORDS:
        return None
    last_a = next((h.get("text", "") for h in reversed(history or [])
                   if h.get("role") == "assistant"), "")
    if not last_a:
        return None
    if not (any(k in last_a for k in _OFFER_MARKS)
            and any(k in last_a for k in _PLAN_NOUNS + _CYCLE_NOUNS)):
        return None
    import split_cycle
    params = split_cycle.extract_plan_params(last_a)   # 继承提议中的方案/天数
    # 指代场景回溯：提议用"这套循环"指代、不含方案名（"按练三休一排计划"→
    # "把这套循环排成一周"→"好"）→ split 从最近含方案别名的用户历史消息回补。
    if "split" not in params:
        try:
            for h in reversed(history or []):
                if isinstance(h, dict) and h.get("role") == "user":
                    alias = split_cycle.find_alias(h.get("text") or "")
                    if alias:
                        params["split"] = alias
                        break
        except Exception:
            pass    # 历史缺失/畸形 → 跳过回补，仅继承提议参数
    return {"task_type": "plan", "params": params, "raw_text": msg,
            "complexity": "complex", "confidence": 1.0,
            "needs_clarify": False}


def build_nodes(registry, llm, classifier=None, validator=None) -> dict:
    classifier = classifier or RouteClassifier(llm)
    validator = validator or RuleValidator()

    def _ctx(state: dict, mode: str) -> ExecutionContext:
        # F4：ExecutionContext 需携带会话 user_id（guard/qa 记忆归属消费）；
        # 此前 session=None 导致 ctx.session.user_id 恒空、记忆回落 local。
        from app.core.session import Session as _Session
        return ExecutionContext(
            session=_Session(id=state.get("session_id", ""),
                             user_id=state.get("user_id", "local")),
            profile=state.get("profile") or {}, mode=mode, skill_log=[])

    # ---------------- guard（无条件前置） ----------------
    def guard(state: dict) -> dict:
        guard_skill = registry.get("guard")
        out = {"blocked": False, "guard_data": None}
        if guard_skill is None:
            return out
        r = guard_skill.execute(_ctx(state, "guard"),
                                {"signal": state.get("message", "")})
        if r.ok and r.data.get("blocked"):
            d = r.data
            out = {"blocked": True, "guard_data": d, "mode_used": "guard",
                   "reply": f"风险提示：{d.get('level_label', '')}。{d.get('advice', '')}",
                   "provenance": r.provenance}
        return out

    def guard_route(state: dict) -> str:
        return "blocked" if state.get("blocked") else "pass"

    # ---------------- classify（意图 + 模式，尊重调用方预置） ----------------
    def classify(state: dict) -> dict:
        # 阶段进度（见 progress.py）：理解意图是这一轮最耗时的一段（L1 语义召回 +
        # L2 LLM 兜底 + 低置信时再来一次意图归一），前端就靠它填住这段死屏。
        progress.emit(progress.STAGE_UNDERSTAND)
        if state.get("intent") and state.get("mode_used"):
            return {}
        # 上下文消解优先：肯定应答承接上轮提议（audit 留痕 raw_text 保原词）
        affirm = _affirm_plan_intent(state.get("message", ""),
                                     state.get("history") or [])
        if affirm is not None:
            return {"intent": affirm, "mode_used": "plan_exec"}
        intent = classifier.intent_for(state.get("message", ""),
                                       state.get("profile") or {})
        try:
            mode = route(intent.complexity, intent.task_type)
        except Exception:
            mode = Mode.DIRECT
        # 低置信（needs_clarify）→ 不硬猜：模式定向 clarify，触发反问
        if intent.needs_clarify:
            mode = Mode.DIRECT  # 占位，mode_used 由下方显式置 "clarify"
        out = {"intent": {**intent.__dict__, "needs_clarify": intent.needs_clarify}}
        # 打卡消歧优先于低置信反问：用户已经明确说了练了什么，只是我们没对上
        # 库里的动作 —— 这时候问"你想做哪件事"是答非所问。
        if state.get("exercise_clarify"):
            out["mode_used"] = "clarify_exercise"
        else:
            out["mode_used"] = "clarify" if intent.needs_clarify else mode.value
        return out

    def mode_route(state: dict) -> str:
        # 已经知道要干什么了，接下来是查数据/编排计划——具体走哪个分支用户不关心
        progress.emit(progress.STAGE_WORK)
        m = state.get("mode_used", "direct")
        # 反问答疑类直接收束，不经过下面对 mode 的一一列举
        return m if m in ("clarify", "clarify_exercise") else m

    # ---------------- clarify（低置信反问；直接 END，不耗 render） ----------------
    def clarify(state: dict) -> dict:
        # O-3：有记忆写入时确认优先于反问（"我喜欢清淡"不再被问"想做哪件事"）
        ack = state.get("memory_ack") or []
        if ack:
            return {"reply": "已记下：" + "、".join(ack), "mode_used": "clarify"}
        it = state.get("intent") or {}
        tt = it.get("task_type", "fallback")
        opts = {"teach": "动作怎么做", "plan": "制定训练/饮食计划",
                "progress": "根据记录给下一组建议", "qa": "查询食物或动作",
                "smalltalk": "随便聊聊"}.get(tt, "查询食物或动作")
        # 数据飞轮：澄清输入落 intent_miss 表（try/except 静默，失败不影响反问）
        _log_intent_miss(state.get("message") or "", tt, it.get("confidence", 0.0))
        return {"reply": (f"我有点不确定你想做哪件事——你是想{opts}，"
                          "还是有别的问题？直接告诉我，我马上帮你。"),
                "mode_used": "clarify"}

    # -------- clarify_exercise（打卡动作消歧；直接 END，不耗 render） --------
    def clarify_exercise(state: dict) -> dict:
        """打卡里有没对上库内动作的片段 → 让用户从**库内真实动作**里挑一个。

        为什么不让 LLM 猜：库里拼作"史密斯深蹲"而用户写"斯密斯深蹲"，没有拼音/
        模糊匹配，猜不出来只会编；而编错的训练记录会直接污染恢复度计算。
        让用户点一下，既准确又顺手把偏好攒下来。
        """
        from app.graph.memory import MemoryStore
        from lib.parts import PART_CHARS, PART_WORDS
        from lib.parts import muscle_of as _muscle_of
        from app.runtime.repos import exercise_repo

        items = state.get("exercise_clarify") or []
        raw = next((str(i.get("raw")) for i in items if i.get("raw")), "")
        uid = state.get("user_id") or "local"

        # 从片段里**扫出部位词**："完腿" → 含"腿" → quadriceps。
        # 长词优先，免得"大腿"被"腿"抢先匹配掉。扫不到就不过滤部位（见下）。
        muscle = None
        for w in sorted(PART_WORDS, key=len, reverse=True):
            if w and w in raw:
                muscle = _muscle_of(w)
                if muscle:
                    break
        if not muscle:
            for c in PART_CHARS:
                if c and c in raw:
                    muscle = _muscle_of(c)
                    if muscle:
                        break

        ex = exercise_repo()
        cands: list[dict] = []
        try:
            if muscle:
                # **用 recommend 而不是 filter。** filter 会把该肌群的**全部**
                # 205 条按文件顺序倒出来，头几条是"分臂 绕环 触趾"这类热身/拉伸；
                # recommend 是既有的推荐口径（主目标优先、去变体重复、难度递进），
                # 3D 视图的"点肌肉看推荐"用的也是它 —— 两处同源，口径不会分家。
                # 多取一倍，留给下面的偏好重排。
                cands = ex.recommend(muscle, count=MAX_CLARIFY_OPTIONS * 2)["recommendations"]
            else:
                # 没有部位线索（多半是拼错的动作名）→ 直接拿原词去检索。
                # 搜不到就**不给选项**，只问 —— 编一组不相干的动作比空着更糟。
                cands = ex.search_zh(raw, limit=MAX_CLARIFY_OPTIONS * 2)
        except Exception:
            cands = []

        # 常练的排前面：用户第二次遇到就不用翻了。
        # 排序只在这一处做（前端不再排），避免两边口径不一致。
        recent: dict = {}
        try:
            store = MemoryStore.get()
            if store is not None:
                recent = store.recent_exercises(uid, days=30)
        except Exception:
            recent = {}
        cands.sort(key=lambda c: -recent.get(str(c.get("id")), 0))

        options = [{"id": c.get("id"), "name_zh": c.get("name_zh"),
                    "equipment": c.get("normalized_equipment"),
                    "difficulty": c.get("difficulty"),
                    "recent_count": recent.get(str(c.get("id")), 0)}
                   for c in cands[:MAX_CLARIFY_OPTIONS]]

        where = f"「{raw}」" if raw else "这个动作"
        if options:
            reply = f"你说的{where}我没对上库里的动作——是下面哪个？点一下我就记上。"
        else:
            reply = (f"你说的{where}我在动作库里没找到。"
                     "换个说法，或者直接说动作的完整名称？")
        return {"event": None, "outcome": {"ok": True, "data": {
                    "options": options, "raw": raw, "verb": state.get("verb")}},
                "reply": reply, "mode_used": "clarify_exercise"}

    # ---------------- 技能执行 ----------------
    def _call_skill(state: dict, name: str, params: dict, mode: str):
        # 多步编排（plan_exec / rewoo / react）里的**每一次**技能调用都经过这里，
        # 所以进度上报只需要这一个点 —— 四个调用点各写一遍必然漂移。
        progress.emit(progress.STAGE_TOOL, skill=name, mode=mode)
        s = registry.get(name)
        if s is None:
            return SkillResult(ok=False, data={}, provenance=[], error=f"无 {name}")
        return s.execute(_ctx(state, mode), params)

    def execute(state: dict) -> dict:
        task_type = (state.get("intent") or {}).get("task_type", "fallback")
        params = (state.get("intent") or {}).get("params", {})
        cands = registry.by_task(task_type)
        if not cands:
            return {"outcome": {"ok": False, "data": {}, "provenance": [],
                                "error": f"无技能承接 {task_type}"}}
        # 顶层承接技能（direct 模式走这里，不经过 _call_skill）
        progress.emit(progress.STAGE_SKILL, skill=cands[0].name, mode="direct")
        r = cands[0].execute(_ctx(state, "direct"), params)
        # skill：实际承接的技能名。此前 state 里没有它，CLI 只能把 provenance 当
        # 技能名打印（"工具→pipeline#screening.plan_check"），排查时误导。
        return {"outcome": {"ok": r.ok, "data": r.data,
                            "provenance": r.provenance, "error": r.error},
                "provenance": r.provenance,
                "skill": cands[0].name}

    # ---------------- plan（PlanExec/ReWOO 共用） ----------------
    def plan_node(state: dict) -> dict:
        task = (state.get("intent") or {}).get("task_type", "fallback")
        calls = llm.plan(task, registry.names)
        plan_calls = [{"step_id": c.step_id, "skill": c.skill,
                       "params": c.params} for c in calls]
        # intent 参数透传：split/days/read 注入 plan 技能调用（LLM 显式给的不覆盖）。
        # read（计划**查询**标记，2026-09-11）也必须透传——漏传时技能看不到标记，
        # 会静默重跑引擎覆盖用户已编辑的计划（实测编辑后"今天练啥"读不回来）。
        ip = (state.get("intent") or {}).get("params") or {}
        for c in plan_calls:
            if c["skill"] == "plan":
                for k in ("split", "days", "read"):
                    if k in ip:
                        c["params"].setdefault(k, ip[k])
        out: dict = {"plan_calls": plan_calls}
        # plan_exec（seq）：图边直连 aggregate，无 fan-out 槽位 → 在本节点顺序执行计划
        if state.get("mode_used") == "plan_exec":
            observations = []
            for c in plan_calls:
                r = _call_skill(state, c["skill"], c["params"], "plan_exec")
                observations.append({"step": c["step_id"], "skill": c["skill"],
                                     "ok": r.ok, "data": r.data,
                                     "error": r.error,
                                     "provenance": r.provenance})
            out["observations"] = observations
        return out

    def plan_route(state: dict) -> str:
        return "parallel" if state.get("mode_used") == "rewoo" else "seq"

    def _make_execute_step(i: int):
        def step(state: dict) -> dict:
            calls = state.get("plan_calls") or []
            if i >= len(calls):
                return {}
            c = calls[i]
            r = _call_skill(state, c["skill"], c["params"], "rewoo")
            return {"observations": [{"step": c["step_id"], "skill": c["skill"],
                                      "ok": r.ok, "data": r.data,
                                      "error": r.error,
                                      "provenance": r.provenance}]}
        return step

    # ---------------- aggregate（PlanExec/ReWOO 汇合 + ReAct 结算） ----------------
    def aggregate(state: dict) -> dict:
        obs = state.get("observations") or []
        steps = state.get("react_steps") or []
        if not obs and steps:
            act_steps = [s for s in steps
                         if s.get("skill") and s["skill"] != "_done"]
            ok = all(s.get("ok", False) for s in act_steps) if act_steps else False
            last = act_steps[-1].get("data", {}) if act_steps else {}
            prov = [f"react:{s['skill']}" for s in act_steps]
            err = next((s.get("error") for s in act_steps if not s.get("ok")),
                       None)
            return {"outcome": {"ok": ok, "data": last, "provenance": prov,
                                "error": err, "_react_steps": steps},
                    "provenance": prov}
        if not obs:
            return {"outcome": {"ok": False, "data": {}, "provenance": [],
                                "error": "无执行观测"}}
        ok = all(o["ok"] for o in obs)
        last = obs[-1]["data"] if obs else {}
        # 失败：透传首个失败观测的具体原因（如 plan 缺档案字段），不泛化
        err = next((o["error"] for o in obs if not o["ok"]), None)
        prov = [f"{o['skill']}:{o['step']}" for o in obs]
        # 技能级 provenance 透传合并（如 memory#preference），不丢留痕
        for o in obs:
            prov.extend(p for p in (o.get("provenance") or []) if p not in prov)
        return {"outcome": {"ok": ok, "data": last, "provenance": prov,
                            "error": err, "_observations": obs},
                "provenance": prov}

    # ---------------- ReAct 循环 ----------------
    def think(state: dict) -> dict:
        steps = state.get("react_steps") or []
        call = llm.think(steps, registry.names)
        if call.skill == "_done":
            return {"react_done": True, "react_next": {}}
        return {"react_done": False,
                "react_next": {"skill": call.skill, "params": call.params}}

    def act(state: dict) -> dict:
        nxt = state.get("react_next") or {}
        done = state.get("react_done", False)
        if done or not nxt.get("skill"):
            return {"react_steps": [{"skill": "_done"}]}
        r = _call_skill(state, nxt["skill"], nxt["params"], "react")
        return {"react_steps": [{"skill": nxt["skill"], "params": nxt["params"],
                                 "ok": r.ok, "data": r.data, "error": r.error,
                                 "result": {"ok": r.ok, "data": r.data,
                                            "error": r.error}}]}

    def react_route(state: dict) -> str:
        done = state.get("react_done", False)
        steps = state.get("react_steps") or []
        if done or len(steps) >= MAX_STEPS:
            return "done"
        return "loop"

    # ---------------- validate + render ----------------
    def validate(state: dict) -> dict:
        from app.core.graph import state_to_intent
        outcome = dict(state.get("outcome") or {})
        intent = state_to_intent(state.get("intent") or {})
        return {"outcome": validator.check(outcome, intent)}

    def render(state: dict) -> dict:
        # 最后一段：LLM 生成（+ 后置护栏可能整条改写）。**只报阶段，不流字**——
        # 理由见 progress.py 的模块注释（护栏会在渲染后整条丢弃 reply）。
        progress.emit(progress.STAGE_RENDER)
        from app.core.graph import build_structured
        # structured 单源组装（标题中文映射+失败原因透传+无 items 死键）；
        # W1：用户原话与最近对话一并注入，渲染才能承接态度/追问
        structured = build_structured(state.get("intent"), state.get("outcome"),
                                      message=state.get("message"),
                                      history=state.get("history"))
        # W2：空结果分级显式化（skill 打标，缺省归 data）——渲染侧不猜缺省语义；
        # 只补空结果，正常命中数据不被改动
        _d = structured.get("data")
        if isinstance(_d, dict) and (_d.get("empty") or _d.get("reason")):
            _d["data_kind"] = _effective_data_kind(structured)
        if state.get("memory_ack"):                     # W1：记忆确认话术透传
            structured["memory_ack"] = list(state["memory_ack"])
        try:
            reply = llm.render(structured)
        except Exception:
            reply = _render_fallback(structured)
        # 渲染后置护栏（单点注册，见模块底部 _POST_RENDER_GUARDS）：
        # 数字幻觉 / ack 双向必达 / 通识标注 / 假计划改动 / 假日程锚点。
        # 顺序敏感，由列表定义；新增护栏只加注册项，不动此处。
        return {"reply": _apply_post_render_guards(reply, structured, state)}

    nodes = {"guard": guard, "guard_route": guard_route,
             "classify": classify, "mode_route": mode_route,
             "clarify": clarify, "clarify_exercise": clarify_exercise,
             "execute": execute,
             "plan_node": plan_node, "plan_route": plan_route,
             "aggregate": aggregate,
             "think": think, "act": act, "react_route": react_route,
             "validate": validate, "render": render}
    for i in range(MAX_PLAN):
        nodes[f"execute_step{i}"] = _make_execute_step(i)
    return nodes


def _log_intent_miss(message: str, task_type: str, confidence: float) -> None:
    """澄清/低置信输入落 intent_miss 表（数据飞轮）。任何异常静默，绝不影响反问。"""
    try:
        from app.storage.db import LogStore
        LogStore().log_miss(message, guessed=task_type, confidence=confidence)
    except Exception:
        pass


# ---- N-11 身体数字幻觉校验：reply 中带身体量纲的数字必须有出处（profile/structured） ----
_UNIT_CLASS = {"公斤": "kg", "千克": "kg", "kg": "kg", "斤": "kg",
               "厘米": "cm", "cm": "cm", "岁": "yr"}
_NUM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(公斤|千克|kg|斤|厘米|cm|岁)", re.I)

# W1：message/history 是用户原话（可能含"如果我80kg""我以前90公斤"这类假设值
# 与过期值），只作渲染上下文，**绝不进授权集**——否则用户提过的任何数字都被洗成
# "有出处"，N-11 身体数字幻觉守卫形同虚设。
_AUTH_EXCLUDED_KEYS = ("message", "history")


def _auth_numbers(structured: dict, profile: dict) -> set[tuple[str, float]]:
    """授权数字集（单位类, 值）：profile 三围 + structured 文本内同量纲数字。
    structured 里的 message/history 键被排除（见 _AUTH_EXCLUDED_KEYS）。"""
    out: set[tuple[str, float]] = set()

    def add(cls, v):
        try:
            out.add((cls, round(float(v), 1)))
        except (TypeError, ValueError):
            pass

    p = profile or {}
    add("kg", p.get("weight_kg"))
    add("cm", p.get("height_cm"))
    add("yr", p.get("age"))
    # progression.weight_kg 在 JSON 里是裸数字（单位在键名），regex 扫不到 →
    # 显式遍历授权：LLM 按 rule 8 如实念出建议重量时不被幻觉守卫击落。
    for day in (((structured.get("data") or {}).get("training") or {})
                .get("items") or []):
        for e in day.get("exercises") or []:
            pr = e.get("progression") or {}
            if pr.get("weight_kg") is not None:
                add("kg", pr["weight_kg"])
    try:
        scan = ({k: v for k, v in structured.items()
                 if k not in _AUTH_EXCLUDED_KEYS}
                if isinstance(structured, dict) else structured)
        for m in _NUM_RE.finditer(json.dumps(scan, ensure_ascii=False)):
            cls = _UNIT_CLASS[m.group(2).lower()]
            v = float(m.group(1)) / (2 if m.group(2) == "斤" else 1)
            out.add((cls, round(v, 1)))
    except TypeError:
        pass    # 非 JSON structured（set/datetime 等）→ 静默回落仅 profile 授权
    return out


def _reply_fake_numbers(reply: str, auth: set[tuple[str, float]]) -> list[str]:
    """reply 中带身体量纲且无出处的数字（幻觉证据）；空=干净。"""
    fake = []
    for m in _NUM_RE.finditer(reply or ""):
        cls = _UNIT_CLASS[m.group(2).lower()]
        v = round(float(m.group(1)) / (2 if m.group(2) == "斤" else 1), 1)
        if (cls, v) not in auth:
            fake.append(m.group(0))
    return fake


def _render_fallback(structured: dict) -> str:
    d = structured.get("data") or {}
    lines = [str(structured.get("title", "回答"))]
    acks = structured.get("memory_ack") or []
    if acks:
        lines.append("已记下：" + "、".join(acks))
    if structured.get("error"):
        lines.append("提示: " + str(structured["error"]))   # 失败原因如实透出
    for it in d.get("items", []):
        nm = it.get("name") or it.get("name_zh")
        if nm:
            lines.append(f"· {nm}")
    lines.extend(training_lines(d))   # 天级行（日期/休息/封堵/建议）单源：render_util
    if "macros" in d:
        m = d["macros"]
        lines.append(f"目标热量 {m.get('target_kcal')} kcal，"
                     f"蛋白 {m.get('protein_g')}g")
    if structured.get("sources"):
        lines.append("来源: " + ", ".join(structured["sources"][:3]))
    return "\n".join(lines) if lines else "（渲染服务暂不可用）"

# ---------------------------------------------------------------- 渲染后置护栏
# 单点注册（2026-09-11 收口）：此前 5 道护栏平铺在 render 尾部，每新增一类幻觉就要
# 再插一段 if（假确认 → 假编辑 → 假日程，一个月内三处）。改为注册式列表——
# **新增护栏 = 追加一个 (name, fn) + 一条测试**，不再动主流程。
# 统一签名 fn(reply, structured, state) -> str；顺序即执行顺序，不可随意调换
# （如数字幻觉降级到 _render_fallback 后，ack 已由 fallback 写入，ack 护栏随即成为空操作）。
def _g_number_hallucination(reply: str, structured: dict, state: dict) -> str:
    """N-11：reply 里带身体量纲却无出处的数字 → 降级确定性渲染。
    无条件校验（不按 is_stub 门控）：stub 输出逐字来自 structured，必在授权集内；
    而固定文本测试替身继承 is_stub=True 却在模拟真实 LLM 幻觉，门控会放行幻觉。"""
    if _reply_fake_numbers(reply, _auth_numbers(structured, state.get("profile"))):
        return _render_fallback(structured)
    return reply


def _g_ack_truth(reply: str, structured: dict, state: dict) -> str:
    """O-4 双向必达：有写入必须念出"已记下"；**无写入不得出现**。"""
    acks = structured.get("memory_ack") or []
    if acks and "已记下" not in reply:
        return "已记下：" + "、".join(acks) + "\n\n" + reply
    if not acks:
        return _strip_fake_ack(reply)
    return reply


def _g_knowledge_label(reply: str, structured: dict, state: dict) -> str:
    """E2：通识标注确定性兜底（knowledge 必达 / 非 knowledge 剥除）。"""
    return _enforce_knowledge_label(reply, structured)


def _g_plan_edit_claim(reply: str, structured: dict, state: dict) -> str:
    """声称改了计划但 provenance 无成功 plan#edit* → 判幻觉，降级零幻觉渲染。"""
    if _fabricated_plan_edit(reply, structured.get("sources")):
        return _render_fallback(structured)
    return reply


def _g_date_anchor(reply: str, structured: dict, state: dict) -> str:
    """把具体旧日期称作"今天/明天" → 判幻觉，同上。"""
    if _fabricated_date_anchor(reply, structured):
        return _render_fallback(structured)
    return reply


_POST_RENDER_GUARDS = (
    ("number_hallucination", _g_number_hallucination),
    ("ack_truth", _g_ack_truth),
    ("knowledge_label", _g_knowledge_label),
    ("plan_edit_claim", _g_plan_edit_claim),
    ("date_anchor", _g_date_anchor),
)


def _apply_post_render_guards(reply: str, structured: dict, state: dict) -> str:
    """按注册顺序施加全部后置护栏。**渲染出口只有一个，护栏才守得住**。"""
    for _name, fn in _POST_RENDER_GUARDS:
        reply = fn(reply, structured, state)
    return reply
