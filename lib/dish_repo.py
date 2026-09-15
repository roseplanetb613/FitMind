# -*- coding: utf-8 -*-
"""菜品库：菜品 = 引用现有食材的配方。营养由求和算出，不存数字。

**核心不变式：营养数字只有一个来源——配方求和。** 菜品库命中的路径与 VLM 现场
拆食材的兜底路径走的是**同一个** `nutrition_from_recipe()`：一个菜品是一份已知
配方，VLM 拆食材是现场生成一份配方，二者同构。

对齐 `lib/portion_reference.py` 的先例：业务层算出来的值用 `method` 与库中实测值
**严格区分**，不冒充实测。

**舍入策略：一律 `round(x, 2)`（Python 内置，IEEE754 就近舍入），不做十进制进位。**
这条必须写明是因为它踩过一次：测试里手算 `99.9 × 15 / 100 = 14.985` 写成期望值
`14.99`，而 `round` 给出 `14.98`（该乘积的 float64 值略低于 14.985）。当时没人
规定过按哪种舍入，于是"实现错了"和"测试写错了"看起来一模一样。卡片只显示到整数
或一位小数，两位精度远超需要，故取内置 `round`，不为它引 Decimal。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DISH_DIR = Path(__file__).resolve().parent.parent / "data" / "dishes" / "data"

MACROS = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
ALLERGEN_KEYS = ["contains_gluten", "contains_dairy", "contains_nuts",
                 "contains_soy", "contains_eggs", "contains_fish"]


def _present(values):
    """求和：全缺返回 None；否则跳过缺值相加。

    **缺值不能当 0**（spec §2 实测三）：`cn_1920xx` 植物油 19 条的
    `calories_kcal` 全是 None，当 0 会让一道红烧肉静默少算 ~130kcal ——
    这比"算不出"更坏，因为它安静地给出一个偏低的数字。

    ⚠ **判据是"列表非空"，不是"和是否为真"**。`_present([0.0, None])` 必须返回
    `0.0` 而不是 `None` —— 食盐的热量是**真 0**，不是缺值，两者在卡片上显示
    不同（`0` vs `—`）。写成 `sum(got) or None` 就会把这个真 0 吞成缺值。
    """
    got = [v for v in values if v is not None]
    return sum(got) if got else None


def _scale(value, factor):
    """按比例缩放并舍入。舍入策略见模块 docstring（一律 round(x, 2)）。"""
    return None if value is None else round(value * factor, 2)


def nutrition_from_recipe(recipe, foods_repo) -> dict:
    """配方 → 营养。**唯一实现**，菜品库与 VLM 兜底共用。

    recipe 每项：`{"food_id": str|None, "name_zh": str, "grams": float}`。
    `food_id` 为 None 表示未命中的食材，进 `missing` 且**不计入总重**
    （计入会稀释 per_100g）。
    """
    breakdown, missing, incomplete = [], [], []
    totals = {m: [] for m in MACROS}
    allergens: set[str] = set()
    total_grams = 0.0

    for it in recipe:
        grams = float(it.get("grams") or 0)
        fid = it.get("food_id")
        rec = foods_repo.get(fid) if fid else None
        if rec is None:
            missing.append({"name": it.get("name_zh") or fid or "未知食材",
                            "grams": grams})
            continue

        total_grams += grams
        per100 = rec.get("per_100g") or {}
        row = {"food_id": fid,
               "name_zh": it.get("name_zh") or rec.get("name_zh") or fid,
               "grams": grams}
        lack = []
        for m in MACROS:
            v = per100.get(m)
            if v is None:
                lack.append(m)
                totals[m].append(None)
                row[m] = None
            else:
                val = v * grams / 100.0
                totals[m].append(val)
                row[m] = round(val, 2)
        row["incomplete"] = lack
        if lack:
            incomplete.append({"food_id": fid, "name_zh": row["name_zh"],
                               "missing_fields": lack})
        for k in ALLERGEN_KEYS:
            if (rec.get("flags") or {}).get(k):
                allergens.add(k)
        breakdown.append(row)

    per_serving = {m: _scale(_present(v), 1.0) for m, v in totals.items()}
    # per_100g 按**配方总重**算，不按 serving_g —— validate 允许二者 ≤20% 偏差，
    # 用 serving_g 会引入一个说不清来源的误差（spec §4.2）。
    factor = 100.0 / total_grams if total_grams > 0 else None
    per_100g = {m: (_scale(_present(v), factor) if factor else None)
                for m, v in totals.items()}

    return {"per_serving": per_serving, "per_100g": per_100g,
            "breakdown": breakdown, "missing": missing,
            "incomplete": incomplete, "allergens": sorted(allergens),
            "total_grams": round(total_grams, 2),
            "method": "recipe_estimated"}


def _norm_dish(s) -> str:
    """菜品名归一：去括号内容、去空白、小写。

    去括号是因为库里名字带方括号补充（"猪肉（奶面）［硬五花］"），而用户/VLM
    说的是"五花肉"；不归一就永远匹配不上。"""
    s = re.sub(r"[（(\[【][^）)\]】]*[）)\]】]", "", str(s or ""))
    return re.sub(r"\s+", "", s).strip().lower()
