# -*- coding: utf-8 -*-
"""LangGraph 编排图：guard→classify→(execute|plan|react)→validate→render。
执行模式动态变换 = 条件边；checkpointer：InMemorySaver（开发）。"""
from __future__ import annotations
import operator
from typing import Annotated, Any, TypedDict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


class FitMindState(TypedDict, total=False):
    session_id: str
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
    # classify：按 mode_used 条件路由（direct/react/plan_exec/rewoo）
    g.add_conditional_edges("classify", nodes["mode_route"], {
        "direct": "execute",
        "react": "think",
        "plan_exec": "plan_node",
        "rewoo": "plan_node",
    })
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

    return g.compile(checkpointer=checkpointer or InMemorySaver())


def state_to_intent(intent_dict: dict):
    """把 state.intent（dict）转回 Intent 对象供 validator 使用。"""
    from app.core.intent import Intent
    return Intent(task_type=intent_dict.get("task_type", "fallback"),
                  params=intent_dict.get("params", {}),
                  raw_text=intent_dict.get("raw_text", ""),
                  complexity=intent_dict.get("complexity", "simple"),
                  confidence=intent_dict.get("confidence", 1.0))