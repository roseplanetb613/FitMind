# -*- coding: utf-8 -*-
"""阶段进度发射器：让一次同步的 agent.run 能对外报"我现在在哪一步"。

**为什么需要它：** `/v1/chat` 实测 2.4~5.1s（复杂意图更久），这期间前端只有一个
死屏。原生办法是逐字流，但那与后置护栏直接冲突——`nodes.py` 的
`_apply_post_render_guards` 会在 LLM 渲染**之后**整条丢弃并替换 reply
（`_g_number_hallucination`：出现无出处的身体数字就换成确定性渲染），
按 token 流出去等于先把"编造的数字"给用户看一遍。所以这里只报**阶段**，
文字仍然一次性给出，护栏的"渲染出口只有一个"不变式原样保留。

**为什么用 contextvars：** 节点函数的签名都是 `(state: dict) -> dict`，
要透传一个回调就得逐层改签名（`agent.run` → `graph.invoke` → 每个 node）。
contextvar 对整条同步调用链可见，于是 `agent.py` **一行都不用改**：
调用方在 worker 线程里 `set_emitter` 之后再调 `agent.run` 即可。

⚠ 发射失败**绝不能影响主链路**——进度是装饰，把主流程带挂是本末倒置。
"""

from __future__ import annotations

import contextvars

# 阶段取值（前端按这个映射文案）。**窄集合**：只有用户看得懂的语义才值得报，
# 把内部节点名（mode_route / aggregate / validate）暴露出去只是噪音。
#
# STAGE_RECEIVED 由**端点**在起线程前立刻发出，不由图内节点发。实测识别意图之前
# 还有 ~1.4s 的死区（agent.run 开头的记忆抽取 + guard 节点），不先在 50ms 内给点
# 反馈的话，用户面对的就是一个 1.4 秒毫无反应的输入框。
STAGE_RECEIVED = "received"       # 已收到，正在分析
STAGE_UNDERSTAND = "understand"   # 正在理解你的问题
STAGE_WORK = "work"               # 正在查询动作库与训练记录
STAGE_RENDER = "render"           # 正在生成回答

_emitter: contextvars.ContextVar = contextvars.ContextVar("stage_emitter", default=None)


def set_emitter(fn) -> None:
    """在当前上下文（线程）内注册发射器。传 None 即关闭。"""
    _emitter.set(fn)


def clear() -> None:
    _emitter.set(None)


def emit(stage: str) -> None:
    """报一个阶段。无人订阅时是空操作。

    **吞掉发射器的异常**：订阅方是自己人（SSE 生成器往 queue 里塞），
    但它一旦出错也不该让用户的回答失败。
    """
    fn = _emitter.get()
    if fn is None:
        return
    try:
        fn(stage)
    except Exception:
        pass
