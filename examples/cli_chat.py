# -*- coding: utf-8 -*-
"""临时 CLI 对话测试（用后即删，不入 git）

用法： python examples/cli_chat.py
命令： exit / quit 退出； /setup 以默认档案建档（供 plan 使用）
其他输入直接对话（当前 provider 由 llm_config auto 决定：有 key→DeepSeek）
"""
from __future__ import annotations
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

# CLI 记忆注入计划 §3.1：uid 解析优先级 /user 命令 > 环境变量 FITMIND_UID > 默认 local
UID = os.environ.get("FITMIND_UID", "local")


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
        print(f"FitMind CLI（user={UID}；输入 exit 退出；/setup 建档；"
              "/user <uid> 切换用户）")
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
            if low == "/whoami":
                print(f"  [whoami] uid={UID} session={_sid()}")
                continue
            r = c.post("/v1/chat", json={"message": msg, "session_id": _sid(),
                                        "user_id": UID})
            b = r.json()
            print(f"agent[{b['mode_used']}]> {b['reply']}")
            print("-" * 50)


if __name__ == "__main__":
    main()
