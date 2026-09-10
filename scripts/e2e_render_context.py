# -*- coding: utf-8 -*-
"""W1-W4（渲染上下文注入 + 通识解禁）E2E 验证脚本。

真实 provider（.env 有 key 时走 DeepSeek）+ 真实 Neo4j；独立 uid，验后 forget 清理。
计划书 T5 Step 2 六例逐条断言，结果如实打印（不做"通过率美化"）。

用法： python scripts/_e2e_rctx.py
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

from fastapi.testclient import TestClient   # noqa: E402
from app.server import create_app           # noqa: E402

UID = "e2e-rctx-" + time.strftime("%H%M%S")
SID = "e2e-sess-" + UID


def _ask(c, msg):
    b = c.post("/v1/chat", json={"message": msg, "session_id": SID,
                                 "user_id": UID}).json()
    st = b.get("structured") or {}
    return b, st


def _ck(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    results = []
    with TestClient(create_app()) as c:
        print(f"== uid={UID} session={SID} ==")

        # --- 例 1：体验型负向偏好写入 + 渲染承接 ---
        b, st = _ask(c, "我不习惯练三休一")
        reply, acks = b["reply"], st.get("memory_ack") or []
        print(f"\n[1] 我不习惯练三休一\n  mode={b['mode_used']} src={st.get('sources')}\n"
              f"  ack={acks}\n  reply={reply}")
        results.append(_ck("1a 负向偏好落库(ack 含 不喜欢)", any("不喜欢" in a for a in acks)))
        results.append(_ck("1b 正文仍讲清练三休一", "练三休一" in reply or "3 练 1 休" in reply))
        results.append(_ck("1c 渲染承接原话(非机械复述)",
                           any(k in reply[:60] for k in ("不习惯", "习惯", "那", "理解", "明白"))))

        # --- 例 2/3：练X休Y 参数化（不再错查动作库） ---
        for idx, q, cycle in ((2, "练四休一呢", "5 天"), (3, "练五休二怎么样", "7 天")):
            b, st = _ask(c, q)
            reply = b["reply"]
            src = " ".join(map(str, st.get("sources") or []))
            print(f"\n[{idx}] {q}\n  mode={b['mode_used']} src={src}\n  reply={reply}")
            results.append(_ck(f"{idx}a 参数化命中(含 {cycle} 一个循环)", cycle in reply))
            results.append(_ck(f"{idx}b 未错报动作库未收录", "动作库未收录" not in reply))
            results.append(_ck(f"{idx}c provenance=teach#split_kb", "teach#split_kb" in src))

        # --- 例 4：数据型禁虚构不回退（计划书原问法 + 实证走 teach 的空结果问法） ---
        for idx, q in ((4, "卡卡罗特深蹲怎么做"), (4, "不存在的动作xyzzy怎么做")):
            b, st = _ask(c, q)
            reply = b["reply"]
            print(f"\n[{idx}] {q}\n  mode={b['mode_used']} src={st.get('sources')}\n"
                  f"  reply={reply}")
            ok = ("没有找到" in reply) or (b["mode_used"] == "guard")
            results.append(_ck(f"{idx} 数据型如实（未虚构动作/数值）", ok,
                               "" if ok else "既未说没找到也未走 guard"))

        # --- 例 5：祈使句否决不回退 ---
        b, st = _ask(c, "别给我排推拉腿")
        reply, acks = b["reply"], st.get("memory_ack") or []
        print(f"\n[5] 别给我排推拉腿\n  mode={b['mode_used']} ack={acks}\n  reply={reply}")
        results.append(_ck("5 祈使句无记忆写入(无'已记下')",
                           not acks and "已记下" not in reply))
        # E1 反渗透：上一轮问过的"卡卡罗特深蹲"不得复述进本轮回答
        results.append(_ck("5b 无跨轮话题渗透(不复述上轮)", "卡卡罗特" not in reply))

        # --- 例 6：N-11 数字校验不回退 ---
        _ask(c, "我只有60kg")
        b, st = _ask(c, "我的身体数据")
        reply = b["reply"]
        print(f"\n[6] 我只有60kg → 我的身体数据\n  mode={b['mode_used']}\n  reply={reply}")
        results.append(_ck("6 档案数字如实(60kg 在答)", "60" in reply))
        results.append(_ck("6b 无未授权身体数字", "80公斤" not in reply))
        results.append(_ck("6c 无跨轮话题渗透(不复述上轮)", "卡卡罗特" not in reply))

    # --- 清理：独立 uid 验后 forget ---
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is not None:
            m.forget(UID)
            print(f"\n[cleanup] forget({UID}) 完成")
    except Exception as e:      # noqa: BLE001
        print(f"\n[cleanup] 失败（需手工清理 {UID}）: {e}")

    print(f"\n== 结果：{sum(1 for r in results if r)}/{len(results)} 通过 ==")
    for i, r in enumerate(results):
        if not r:
            print(f"  失败项 #{i + 1}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
