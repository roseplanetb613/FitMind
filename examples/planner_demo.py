# -*- coding: utf-8 -*-
"""
planner_demo.py — 数据驱动的一日 训练+食谱 计划（v0 基座版）
=============================================================
演示：给定身体数据与目标，用现有数据层产出一日计划。
【诚实边界】这是「计算 + 候选集」而不是个性化营养师：
  - 热量/宏观用 Mifflin-St Jeor + 活动系数（公认公式）
  - 食材来自 foods_repo（USDA/中国成分表/OFF 中国），按份换算
  - 动作来自 exercise_repo（1324 动作增强元数据：难度/模式/处方）
  - 无饮食历史/训练历史/禁忌输入

用法：python examples/planner_demo.py  （示例身体数据可改）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from foods_repo import FoodsRepo   # noqa: E402
from exercise_repo import ExerciseRepo  # noqa: E402

foods = FoodsRepo()
ex = ExerciseRepo()

# ---------------- 输入（示例；换成你的真实数据） ----------------
PROFILE = {
    "sex": "male", "age": 28, "height_cm": 175, "weight_kg": 75,
    "activity": 1.55,        # 久坐1.2 轻1.375 中1.55 高1.725
    "goal": "lose_fat",      # lose_fat / maintain / build_muscle
    "deficit_kcal": 300,     # 减脂缺口
    "protein_g_per_kg": 2.0, "fat_g_per_kg": 0.8,
}

# ---------------- 1. 热量与宏观目标 ----------------
def macro_target(p: dict) -> dict:
    bmr = (10 * p["weight_kg"] + 6.25 * p["height_cm"] - 5 * p["age"]
           + (5 if p["sex"] == "male" else -161))
    tdee = bmr * p["activity"]
    target_kcal = tdee - p.get("deficit_kcal", 0)
    protein = p["weight_kg"] * p["protein_g_per_kg"]
    fat = p["weight_kg"] * p["fat_g_per_kg"]
    carbs = (target_kcal - 4 * protein - 9 * fat) / 4
    return {"bmr": bmr, "tdee": tdee, "target_kcal": target_kcal,
            "protein_g": protein, "fat_g": fat, "carbs_g": max(carbs, 0)}


m = macro_target(PROFILE)
print("========== 一、热量与宏观目标 ==========")
print(f"BMR={m['bmr']:.0f} kcal | TDEE={m['tdee']:.0f} | 目标 {m['target_kcal']:.0f} kcal/日")
print(f"蛋白 {m['protein_g']:.0f}g | 脂肪 {m['fat_g']:.0f}g | 碳水 {m['carbs_g']:.0f}g")

# ---------------- 2. 食谱候选（高蛋白，偏好中国源） ----------------
import re

# 实用过滤：干制品/珍稀干货排除（成分表蛋白浓度虚高但日常不食用）
UNEDIBLE = re.compile(r"[（(]干|干[［\[〔]|鱼翅|鱼肚|鱼唇|裙边|驼掌|驼峰|"
                      r"燕窝|琼脂|石花菜|冬虫夏草|乌梢蛇|蛏干|墨鱼（|鱿鱼（")


def pick(protein_min, kcal_max, fat_max, source=None, n=4):
    r = [x for x in foods.filter(source=source, protein_min=protein_min,
                                 kcal_max=kcal_max, fat_max=fat_max, limit=999)
         if not UNEDIBLE.search(x["name"])]
    r.sort(key=lambda x: -(x["per_100g"]["protein_g"] or 0))
    out, seen = [], set()
    for x in r:
        key = x.get("family_name") or x["name"]
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) >= n:
            break
    return out


def line(food):
    p = food["per_100g"]
    sv = foods.serving_grams(food["food_id"])
    return (f"  {food['name_zh'][:26]:28s} | {p['calories_kcal']}kcal/100g "
            f"蛋{p['protein_g']}g | 一份≈{sv['grams']}g({sv['method']})")


print("\n========== 二、高蛋白食材候选（每 100g） ==========")
print("▲ 中国源（成分表）：")
for f in pick(15, 350, 12, source="china", n=5)[:5]:
    print(line(f))
print("▲ 中国源（OFF 品牌）备选：")
for f in pick(20, 300, 15, source="off_cn", n=3)[:3]:
    print(line(f))
print("▲ 全球基线（USDA）补充：")
for f in pick(20, 300, 15, source="usda", n=4)[:4]:
    print(line(f))

# ---------------- 3. 训练计划（按动作模式分化：推/拉/腿/核心） ----------------
print("\n========== 三、训练计划（示例全身分化） ==========")
# 腿日按 squat+hinge 模式（部位词不能当肌肉本体用，模式驱动语义更准）
PLAN = {"推日(胸·肩·三头)": ("push", 2), "拉日(背·二头)": ("pull", 2),
        "腿日(股四·臀·腘绳)": ("squat", 2), "核心日": ("core", 1)}
for day, (pattern, diff) in PLAN.items():
    rec = ex.filter(pattern=pattern, difficulty=diff, limit=30)
    rec.sort(key=lambda r: r["difficulty"])
    print(f"\n▸ {day}（模式:{pattern} 难度≤{diff}）")
    for r in rec[:3]:
        sug = r["suggested"]
        print(f"  {r['name_zh']} | {r['difficulty']}档/{r['movement_pattern']} "
              f"| 建议{sug['sets']}组×{sug['reps']}次 休息{sug['rest_sec']}s | 需{r['normalized_equipment']}")

# ---------------- 4. 边界声明 ----------------
print("\n========== 边界（务必知悉） ==========")
print("""① 食谱是「食材候选」不是成品菜组合；烹饪搭配、口味偏好需人工/后续编排层介入
② 训练是「骨架分化」，无渐进超负荷与周期；真实进阶需记录历史
③ 无伤病史/过敏/禁忌输入，不构成医学建议
④ 本演示基于示例身体数据——把你的真实 性别/年龄/身高/体重/活动水平/目标 发我，我重跑一遍""")