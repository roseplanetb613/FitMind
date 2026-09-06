# -*- coding: utf-8 -*-
"""exercises 数据包 —— 检索/推荐层组合查询示例
运行：python examples/quickstart_exercise.py（顶层 lib/ 为消费层）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from exercise_repo import ExerciseRepo  # noqa: E402

repo = ExerciseRepo()


def show(records, label):
    print(f"\n{label}（{len(records)} 条）")
    for r in records:
        fam = f" | 变体族:{r['family_name']}" if r.get("family_name") else ""
        print(f"  [{r['id']}] {r['name_zh']} | 难度{r['difficulty']}/{'新手进阶高级'[r['difficulty']]}"
              f" | {r['movement_pattern']} | {r['exercise_type']} | {r['equipment']}{fam}")


if __name__ == "__main__":
    # 1) 组合过滤：胸 + 杠铃 + 进阶（注：杠铃动作在难度模型里没有"新手"档，属数据事实）
    show(repo.filter(muscle="chest", equipment="barbell", difficulty=2,
                     limit=6, sort_by_difficulty=True), "① 胸 + 杠铃 + 进阶（组合过滤）")

    # 2) 别名归一化：delts / 三角肌 命中同一批动作
    a = {r["id"] for r in repo.filter(muscle="delts", limit=200)}
    b = {r["id"] for r in repo.filter(muscle="三角肌", limit=200)}
    print(f"\n② 肌肉归一化：muscle='delts' 与 muscle='三角肌' 命中集合一致 = {a == b} ({len(a)} 条)")

    # 3) 推荐：徒手新手练背
    rec = repo.recommend("back", equipment="body weight", difficulty=1, count=5)
    show(rec["recommendations"], f"③ 推荐：徒手新手练背（fallback={rec['fallback']}）")

    # 4) 推荐：进阶练肩
    rec2 = repo.recommend("shoulders", difficulty=2, count=6)
    show(rec2["recommendations"], f"④ 推荐：进阶练肩（fallback={rec2['fallback']}）")

    # 5) 动作替换：杠铃卧推的变体
    base = next(r for r in repo.search("bench press")
                if r["equipment"] == "barbell" and r["name"].startswith("barbell"))
    show(repo.siblings(base["id"]), f"⑤ 动作替换（{base['name']} 的变体族成员）")

    # 6) 中文指令与肌肉中文名
    ex = repo.get("0025")
    mus = repo.muscle("delts")
    print(f"\n⑥ 中文能力：{ex['name']} 中文名 '{ex['name_zh']}' | 中文分步 → {ex['instruction_steps']['zh'][0][:24]}…")
    print(f"   肌肉中文名：delts → {mus['name_zh']}（{mus['name_en']}，区域 {mus['region']}）")

    # 7) 数据层新字段：规范名 / 器械归一 / 柔韧类
    st = repo.filter(pattern="stretch", limit=3)
    show(st, "⑦ 柔韧类动作（stretch_mobility，独立于力量推荐）")
    ex2 = repo.get("0352")
    print(f"   规范名示例：原 '{ex2['name']}' → canonical '{ex2['canonical_name']}'")
    ex3 = repo.search("smith").pop()
    print(f"   器械归一示例：'{ex3['equipment']}' → '{ex3['normalized_equipment']}'")