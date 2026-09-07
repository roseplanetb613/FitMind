# -*- coding: utf-8 -*-
"""临时 CLI 对话测试（用后即删，不入 git）

用法： python examples/cli_chat.py
命令： exit / quit 退出； /setup 以默认档案建档（供 plan 使用）
其他输入直接对话（当前 provider 由 llm_config auto 决定：有 key→DeepSeek）
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "app", "lib"):          # "" = 项目根（app 包解析需要）
    p = str(ROOT / p)
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient   # noqa: E402
from app.server import create_app           # noqa: E402

SID = "cli-1"

# 中文口语输入 → API 英文枚举（仅 CLI 边界归一化，业务层契约不变）
SEX_MAP = {"男": "male", "男生": "male", "m": "male", "male": "male",
           "女": "female", "女生": "female", "f": "female", "female": "female"}
GOAL_MAP = {"减脂": "lose_fat", "减重": "lose_fat", "减肥": "lose_fat", "lose_fat": "lose_fat",
            "维持": "maintain", "保持": "maintain", "maintain": "maintain",
            "增肌": "build_muscle", "增重": "build_muscle", "长肌肉": "build_muscle",
            "build_muscle": "build_muscle"}


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
    r = c.post("/v1/profile", json={"session_id": SID, "profile": profile})
    if r.status_code == 200:
        p = r.json()["profile"]
        print(f"  [建档 200] {p.get('sex')}/{p.get('age')}岁/"
              f"{p.get('height_cm')}cm/{p.get('weight_kg')}kg goal={p.get('goal')}")
    else:
        print(f"  [建档 {r.status_code}] {r.json().get('error')} "
              f"{r.json().get('details', '')}")


def main() -> None:
    with TestClient(create_app()) as c:
        print("FitMind CLI（输入 exit 退出；/setup 建档后试'帮我制定一日减脂计划'）")
        while True:
            try:
                msg = input("you> ").strip()
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
            r = c.post("/v1/chat", json={"message": msg, "session_id": SID})
            b = r.json()
            print(f"agent[{b['mode_used']}]> {b['reply']}")
            print("-" * 50)


if __name__ == "__main__":
    main()
