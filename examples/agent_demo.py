# -*- coding: utf-8 -*-
"""agent_demo.py — 核心 Agent 五会话演示（stub LLM，全规则决策）。
用法：python examples/agent_demo.py"""
from __future__ import annotations
import sys
from pathlib import Path

for p in ("", "app", "lib"):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / p))

from fastapi.testclient import TestClient
from app.server import create_app

PROFILE = {"sex": "male", "age": 28, "height_cm": 175, "weight_kg": 75,
           "activity": 1.55, "goal": "lose_fat", "deficit_kcal": 300,
           "conditions": [], "patterns": []}

CASES = [
    ("我有腰椎间盘突出，能硬拉吗", None),
    ("鸡胸肉蛋白质多少", None),
    ("帮我制定一日减脂计划", "demo-plan"),
    ("深蹲怎么做", None),
    ("卧推下一组做多重", "demo-pg"),
]

if __name__ == "__main__":
    app = create_app()
    app.state.agent.sessions.create("demo-plan").profile = PROFILE   # plan 建档
    client = TestClient(app)
    for msg, sid in CASES:
        r = client.post("/v1/chat", json={"message": msg, "session_id": sid})
        b = r.json()
        print("=" * 60)
        print(f"问: {msg}   [mode={b['mode_used']}]")
        print(b["reply"][:300])
    print("=" * 60)
    print("注：决策全部走规则链；LLM 仅意图解析与渲染（当前为 stub）。")