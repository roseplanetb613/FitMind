# -*- coding: utf-8 -*-
"""LangGraph 编排图：guard→classify→(execute|plan|react)→validate→render。
执行模式动态变换 = 条件边；checkpointer 可注入（默认无持久化）。"""
from __future__ import annotations
import operator
from typing import Annotated, Any, TypedDict
from langgraph.graph import END, START, StateGraph


class FitMindState(TypedDict, total=False):
    session_id: str
    user_id: str                          # F4：记忆归属（agent.run 注入，nodes._ctx 消费）
    message: str
    profile: dict
    intent: dict                      # task_type/params/raw_text/complexity
    mode_used: str                    # guard/direct/react/plan_exec/rewoo
    blocked: bool                     # guard 短路标记
    guard_data: dict
    plan_calls: list                  # llm.plan 产出（PlannedCall → dict）
    observations: Annotated[list, operator.add]   # fan-out 执行结果（reducer 追加）
    react_steps: Annotated[list, operator.add]    # react 步骤历史
    react_done: bool
    react_next: dict                  # think 决策的下一步 {"skill","params"}
    outcome: dict                     # {"ok","data","provenance","error",...}
    reply: str
    memory_ack: list[str]             # 记忆写入确认话术（agent.run 注入，render/clarify 消费）
    provenance: Annotated[list, operator.add]   # 顶层来源标注（guard/execute/aggregate 写入）


def build_graph(registry, llm, classifier=None, validator=None,
                checkpointer=None):
    """装配并编译整张图。registry/llm 为必选；classifier/validator/checkpointer 可注入。"""
    from app.core.nodes import build_nodes

    nodes = build_nodes(registry=registry, llm=llm, classifier=classifier,
                        validator=validator)
    g = StateGraph(FitMindState)

    g.add_node("guard", nodes["guard"])
    g.add_node("classify", nodes["classify"])
    g.add_node("clarify", nodes["clarify"])
    g.add_node("execute", nodes["execute"])
    g.add_node("plan_node", nodes["plan_node"])
    g.add_node("aggregate", nodes["aggregate"])
    for i in range(4):                       # ReWOO fan-out 槽位
        g.add_node(f"execute_step{i}", nodes[f"execute_step{i}"])
    g.add_node("think", nodes["think"])
    g.add_node("act", nodes["act"])
    g.add_node("validate", nodes["validate"])
    g.add_node("render", nodes["render"])

    # guard 无条件前置：blocked → END（reply 已在 guard 写就）；否则进 classify
    g.add_conditional_edges("guard", nodes["guard_route"],
                            {"blocked": END, "pass": "classify"})
    # classify：按 mode_used 条件路由（direct/react/plan_exec/rewoo/clarify）
    g.add_conditional_edges("classify", nodes["mode_route"], {
        "direct": "execute",
        "react": "think",
        "plan_exec": "plan_node",
        "rewoo": "plan_node",
        "clarify": "clarify",
    })
    g.add_edge("clarify", END)   # 低置信反问：直接收束，不耗 render token
    # plan_node：plan_exec 走顺序 aggregate；rewoo 走 fan-out
    g.add_conditional_edges("plan_node", nodes["plan_route"], {
        "seq": "aggregate", "parallel": "execute_step0",
    })
    # ReWOO fan-out：4 槽位并行执行 → 全部汇入 aggregate（不可重复 add 同起终点边）
    for i in range(4):
        g.add_edge(f"execute_step{i}", "aggregate")
    # ReAct 循环：think→act→（条件边）→ 继续/收束
    g.add_edge(START, "guard")
    g.add_edge("execute", "validate")
    g.add_edge("think", "act")
    g.add_conditional_edges("act", nodes["react_route"],
                            {"loop": "think", "done": "aggregate"})
    g.add_edge("aggregate", "validate")
    g.add_edge("validate", "render")
    g.add_edge("render", END)

    return g.compile(checkpointer=checkpointer)


def state_to_intent(intent_dict: dict):
    """把 state.intent（dict）转回 Intent 对象供 validator 使用。"""
    from app.core.intent import Intent
    return Intent(task_type=intent_dict.get("task_type", "fallback"),
                  params=intent_dict.get("params", {}),
                  raw_text=intent_dict.get("raw_text", ""),
                  complexity=intent_dict.get("complexity", "simple"),
                  confidence=intent_dict.get("confidence", 1.0))


# task_type 英文枚举 → 中文语义标题（render 节点与 API 响应共用，防双写漂移；
# 避免 LLM 把 "teach" 当 query 脑补"与teach相关"）
TITLE_ZH = {"qa": "知识问答", "teach": "动作教学", "plan": "训练计划",
            "plan_edit": "计划调整", "progress": "进度建议",
            "guard": "安全提示",
            "smalltalk": "闲聊", "fallback": "回答"}


def build_structured(intent: dict | None, outcome: dict | None,
                     sources: list | None = None) -> dict:
    """组装 render/API 共用的结构化结果（单源）。失败原因透传到 error 字段，
    由渲染侧如实转述；不再携带恒空的 items 死键。
    sources 缺省取 outcome.provenance；API 侧可传图顶层 provenance（guard 短路
    时 outcome 为空，顶层仍保留守卫来源标注）。"""
    intent = intent or {}
    outcome = outcome or {}
    structured = {"title": TITLE_ZH.get(intent.get("task_type", "fallback"),
                                        "回答"),
                  "sources": (sources if sources is not None
                              else outcome.get("provenance", []))}
    if outcome.get("ok"):
        structured["data"] = outcome.get("data", {})
    else:
        err = outcome.get("_error") or outcome.get("error")
        if err:
            structured["error"] = err
    return structured
