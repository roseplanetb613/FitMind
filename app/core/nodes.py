# -*- coding: utf-8 -*-
"""LangGraph 节点（闭包工厂）：guard/classify/execute/plan/aggregate/think/act/
validate/render。全部复用现有领域层：skills、runtime.validator、router。"""
from __future__ import annotations
import json
import re
from app.core.router import Mode, RouteClassifier, route
from app.skills.base import ExecutionContext, SkillResult
from app.runtime.validator import RuleValidator

MAX_STEPS = 4
MAX_PLAN = 4

# 上下文消解：肯定应答精确匹配集（剥标点后整词比对，防"好吗/行动"类子串误伤）
_AFFIRM_WORDS = frozenset({
    "确认", "好的", "好", "可以", "行", "嗯", "嗯嗯", "要", "没问题",
    "好呀", "好啊", "好吧", "行吧", "ok", "OK", "Ok"})
# 上轮提议标记：提议动作 + plan 名词 → 肯定应答承接为 plan
_OFFER_MARKS = ("我可以", "可以帮你", "要不要", "如果你想", "需要我",
                "帮你排", "帮你制定", "帮你把")
_PLAN_NOUNS = ("计划", "课表", "训练表")


def _affirm_plan_intent(msg: str, history: list) -> dict | None:
    """肯定应答 + 上轮 plan 提议 → 承接提议意图（CLI 实证："确认"承接
    "我可以帮你把这套循环排成一周的具体计划"，曾丢上下文落 qa 空检索）。
    窄口径宁缺毋滥：肯定词须整词命中；上轮助手消息须同时含提议标记与
    plan 名词。其余情形返回 None 走正常分类。"""
    m = (msg or "").strip().strip("。，！!？?、~ ")
    if m not in _AFFIRM_WORDS:
        return None
    last_a = next((h.get("text", "") for h in reversed(history or [])
                   if h.get("role") == "assistant"), "")
    if not last_a:
        return None
    if not (any(k in last_a for k in _OFFER_MARKS)
            and any(k in last_a for k in _PLAN_NOUNS)):
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
        out["mode_used"] = "clarify" if intent.needs_clarify else mode.value
        return out

    def mode_route(state: dict) -> str:
        m = state.get("mode_used", "direct")
        return "clarify" if m == "clarify" else m

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

    # ---------------- 技能执行 ----------------
    def _call_skill(state: dict, name: str, params: dict, mode: str):
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
        r = cands[0].execute(_ctx(state, "direct"), params)
        return {"outcome": {"ok": r.ok, "data": r.data,
                            "provenance": r.provenance, "error": r.error},
                "provenance": r.provenance}

    # ---------------- plan（PlanExec/ReWOO 共用） ----------------
    def plan_node(state: dict) -> dict:
        task = (state.get("intent") or {}).get("task_type", "fallback")
        calls = llm.plan(task, registry.names)
        plan_calls = [{"step_id": c.step_id, "skill": c.skill,
                       "params": c.params} for c in calls]
        # intent 参数透传：split/days 注入 plan 技能调用（LLM 显式给的不覆盖）
        ip = (state.get("intent") or {}).get("params") or {}
        for c in plan_calls:
            if c["skill"] == "plan":
                for k in ("split", "days"):
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
        from app.core.graph import build_structured
        # structured 单源组装（标题中文映射+失败原因透传+无 items 死键）
        structured = build_structured(state.get("intent"), state.get("outcome"))
        if state.get("memory_ack"):                     # W1：记忆确认话术透传
            structured["memory_ack"] = list(state["memory_ack"])
        try:
            reply = llm.render(structured)
        except Exception:
            reply = _render_fallback(structured)
        # N-11：LLM 幻觉身体数字（与档案/structured 矛盾）→ 降级确定性渲染。
        # 无条件校验（不按 is_stub 门控）：stub 渲染输出逐字来自 structured，
        # 同量纲数字必在授权集内，校验为空操作；而固定文本测试替身继承
        # is_stub=True 却在模拟真实 LLM 幻觉，门控会放行幻觉。
        if _reply_fake_numbers(
                reply, _auth_numbers(structured, state.get("profile"))):
            reply = _render_fallback(structured)
        # O-4：ack 必达——LLM 吞掉时代码级补前缀（不依赖提示词自觉）
        acks = structured.get("memory_ack") or []
        if acks and "已记下" not in reply:
            reply = "已记下：" + "、".join(acks) + "\n\n" + reply
        return {"reply": reply}

    nodes = {"guard": guard, "guard_route": guard_route,
             "classify": classify, "mode_route": mode_route,
             "clarify": clarify,
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


def _auth_numbers(structured: dict, profile: dict) -> set[tuple[str, float]]:
    """授权数字集（单位类, 值）：profile 三围 + structured 文本内同量纲数字。"""
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
    try:
        for m in _NUM_RE.finditer(json.dumps(structured, ensure_ascii=False)):
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
    if "macros" in d:
        m = d["macros"]
        lines.append(f"目标热量 {m.get('target_kcal')} kcal，"
                     f"蛋白 {m.get('protein_g')}g")
    if structured.get("sources"):
        lines.append("来源: " + ", ".join(structured["sources"][:3]))
    return "\n".join(lines) if lines else "（渲染服务暂不可用）"