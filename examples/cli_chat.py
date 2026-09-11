# -*- coding: utf-8 -*-
"""临时 CLI 对话测试（用后即删，不入 git）

用法： python examples/cli_chat.py
命令： exit / quit 退出； /setup 以默认档案建档（供 plan 使用）
      /user <uid> 切换用户； /trace on|off 开关过程观测（默认开）
其他输入直接对话（当前 provider 由 llm_config auto 决定：有 key→DeepSeek）

每轮除最终回复外，贴出全过程观测（仅本 CLI 进程生效，业务文件零改动）：
  事件流    LangGraph 节点流转 guard→classify→…→render
  思考内容  意图/置信度/复杂度、模式决策、计划分解、ReAct 下一步决策
  调用工具  技能名+参数+结果（成功/失败原因/数据预览）
实现：把 agent.graph.invoke 换成 stream 旁路（updates=逐节点输出，
values=累积状态，末帧即 invoke 返回值）→ agent.run 编排/记忆/历史不变。
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):          # "" = 项目根（app 包解析需要）
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient   # noqa: E402
from app.server import create_app           # noqa: E402

# Windows 控制台兜底：不可编码字符替换而非崩溃（事件流从服务线程打印）
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

# CLI 记忆注入计划 §3.1：uid 解析优先级 /user 命令 > 环境变量 FITMIND_UID > 默认 local
UID = os.environ.get("FITMIND_UID", "local")

# 过程观测开关（/trace on|off）
_TRACE = {"on": True}


def _sid() -> str:
    """session_id 随 uid 派生（换用户换 session，防旧 profile 缓存串味）。"""
    return f"cli-{UID}"

# 中文口语输入 → API 英文枚举（仅 CLI 边界归一化，业务层契约不变）
SEX_MAP = {"男": "male", "男生": "male", "m": "male", "male": "male",
           "女": "female", "女生": "female", "f": "female", "female": "female"}
GOAL_MAP = {"减脂": "lose_fat", "减重": "lose_fat", "减肥": "lose_fat", "lose_fat": "lose_fat",
            "维持": "maintain", "保持": "maintain", "maintain": "maintain",
            "增肌": "build_muscle", "增重": "build_muscle", "长肌肉": "build_muscle",
            "build_muscle": "build_muscle"}


# ================================================================ 过程观测
def _trunc(x, limit: int = 160) -> str:
    """任意对象 → 单行 JSON 预览（json 转义换行；超长截断）。"""
    try:
        s = json.dumps(x, ensure_ascii=False)
    except Exception:
        s = str(x)
    return s if len(s) <= limit else s[: limit - 1] + "..."


def _tool_line(skill, params, ok, error, data=None) -> str:
    """技能调用一行：名称+参数+结果+数据预览。"""
    p = f"({_trunc(params)})" if params else ""
    st = "成功" if ok else f"失败({error})"
    d = f" 数据={_trunc(data, 120)}" if data else ""
    return f"工具→{skill}{p} {st}{d}"


def _fmt_node(node: str, out: dict) -> list[str]:
    """节点输出 dict → 人类可读事件行（空输出也占一行，保持流转完整）。"""
    if not out:
        return ["(无状态变更)"]
    lines: list[str] = []
    if node == "guard":
        if out.get("blocked"):
            d = out.get("guard_data") or {}
            lines.append(f"拦截→级别={d.get('level_label', '?')} "
                         f"建议={_trunc(d.get('advice'))}")
        else:
            lines.append("放行→无风险信号")
    elif node == "classify":
        it = out.get("intent") or {}
        if it:
            lines.append(f"思考→意图={it.get('task_type')} "
                         f"参数={_trunc(it.get('params'))} "
                         f"置信度={it.get('confidence')} "
                         f"复杂度={it.get('complexity')}")
        if out.get("mode_used"):
            lines.append(f"决策→模式={out['mode_used']}")
    elif node == "clarify":
        lines.append(f"反问→{_trunc(out.get('reply'))}")
    elif node == "execute":
        oc = out.get("outcome") or {}
        prov = out.get("provenance") or oc.get("provenance") or []
        st = "成功" if oc.get("ok") else f"失败({oc.get('error')})"
        # 技能名与来源分开打印：此前把 provenance 当技能名（"工具→pipeline#…"），
        # 排查时误以为调了那个"技能"。skill 缺省回落旧行为（兼容旧图）。
        skill = out.get("skill") or "skill"
        lines.append(f"工具→{skill} {st}")
        if prov:
            lines.append(f"来源→{','.join(map(str, prov))}")
        if oc.get("data"):
            lines.append(f"数据→{_trunc(oc.get('data'))}")
    elif node == "plan_node":
        for c_ in out.get("plan_calls") or []:
            lines.append(f"计划→步骤{c_.get('step_id')} 调用 "
                         f"{c_.get('skill')}({_trunc(c_.get('params'))})")
        for o in out.get("observations") or []:     # plan_exec 顺序执行结果
            lines.append(_tool_line(o.get("skill"), None, o.get("ok"),
                                    o.get("error"), o.get("data")))
    elif node.startswith("execute_step"):           # ReWOO 并行槽位
        for o in out.get("observations") or []:
            lines.append(_tool_line(o.get("skill"), None, o.get("ok"),
                                    o.get("error"), o.get("data")))
    elif node == "think":
        if out.get("react_done"):
            lines.append("思考→ReAct 判定完成，收束")
        else:
            nxt = out.get("react_next") or {}
            lines.append(f"思考→ReAct 下一步 {nxt.get('skill')}"
                         f"({_trunc(nxt.get('params'))})")
    elif node == "act":
        for s_ in out.get("react_steps") or []:
            if s_.get("skill") and s_.get("skill") != "_done":
                lines.append(_tool_line(s_.get("skill"), s_.get("params"),
                                        s_.get("ok"), s_.get("error"),
                                        s_.get("data")))
    elif node == "aggregate":
        oc = out.get("outcome") or {}
        st = "成功" if oc.get("ok") else f"失败({oc.get('error')})"
        lines.append(f"汇合→{st} 来源={_trunc(out.get('provenance'))}")
    elif node == "validate":
        oc = out.get("outcome") or {}
        st = "通过" if oc.get("ok") else f"未过({oc.get('error')})"
        extra = (f" 警告={_trunc(oc.get('_warnings'))}"
                 if oc.get("_warnings") else "")
        lines.append(f"校验→{st}{extra}")
    elif node == "render":
        lines.append(f"渲染→{_trunc(out.get('reply'))}")
    else:
        lines.append(_trunc(out))
    return lines


def _install_trace(c) -> None:
    """给 agent.graph.invoke 装事件流旁路：改用 stream 逐节点打印，
    末帧 values 快照作为 invoke 返回值 → agent.run 编排逻辑零改动。"""
    g = c.app.state.agent.graph

    def traced_invoke(inp, config=None, **kwargs):
        final = None
        for mode, chunk in g.stream(inp, config=config,
                                    stream_mode=["updates", "values"],
                                    **kwargs):
            if mode == "updates":
                if _TRACE["on"]:
                    for node, out in (chunk or {}).items():
                        if node.startswith("__"):
                            continue
                        for ln in _fmt_node(node, out or {}):
                            print(f"  · {node:<13} {ln}")
            elif chunk is not None:
                final = chunk
        return final if final is not None else dict(inp)

    g.invoke = traced_invoke


def _setup(c) -> None:
    """交互式建档：逐项询问，回车为空时用演示默认值。"""
    print("  建档（回车使用演示默认值）:")
    sex = SEX_MAP.get(input("  性别 male/female> ").strip().lower(), "male")
    age = input("  年龄> ").strip() or "28"
    height = input("  身高cm> ").strip() or "175"
    weight = input("  体重kg> ").strip() or "75"
    goal = GOAL_MAP.get(input("  目标 lose_fat/maintain/build_muscle> ").strip(),
                       "maintain")
    profile = {"sex": sex, "age": int(age), "height_cm": float(height),
               "weight_kg": float(weight), "goal": goal, "activity": 1.55}
    r = c.post("/v1/profile", json={"session_id": _sid(), "user_id": UID,
                                    "profile": profile})
    if r.status_code == 200:
        p = r.json()["profile"]
        print(f"  [建档 200] uid={UID} {p.get('sex')}/{p.get('age')}岁/"
              f"{p.get('height_cm')}cm/{p.get('weight_kg')}kg goal={p.get('goal')}")
    else:
        print(f"  [建档 {r.status_code}] {r.json().get('error')} "
              f"{r.json().get('details', '')}")


def main() -> None:
    global UID
    with TestClient(create_app()) as c:
        _install_trace(c)     # 事件流旁路（仅本进程，业务文件零改动）
        print(f"FitMind CLI（user={UID}；输入 exit 退出；/setup 建档；"
              "/user <uid> 切换用户；/trace on|off 观测开关）")
        while True:
            try:
                msg = input(f"you[{UID}]> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not msg:
                continue
            low = msg.lower()
            if low in ("exit", "quit"):
                break
            if low == "/setup":
                _setup(c)
                continue
            if low.startswith("/user "):
                new_uid = msg.split(None, 1)[1].strip()
                if new_uid:
                    UID = new_uid
                    print(f"  [switch] uid -> {UID}，session -> {_sid()}")
                else:
                    print("  用法：/user <uid>")
                continue
            if low.startswith("/trace"):
                _TRACE["on"] = not low.endswith("off")
                print(f"  [trace] 过程观测 -> {'开' if _TRACE['on'] else '关'}")
                continue
            if low == "/whoami":
                print(f"  [whoami] uid={UID} session={_sid()}")
                continue
            r = c.post("/v1/chat", json={"message": msg, "session_id": _sid(),
                                        "user_id": UID})
            b = r.json()
            st = b.get("structured") or {}
            if st.get("memory_ack"):
                print(f"  [记忆] 已记下：{'、'.join(st['memory_ack'])}")
            print(f"agent[{b['mode_used']}]> {b['reply']}")
            if st.get("sources"):
                print(f"  来源: {', '.join(map(str, st['sources'][:5]))}")
            print("-" * 50)


if __name__ == "__main__":
    main()
