# -*- coding: utf-8 -*-
"""
validate_nutrition.py — 营养核心集一致性校验
用法：python scripts/validate_nutrition.py （失败退出码 1）
检查：结构 / 条数 / id 唯一性与格式 / 字段白名单 / 数值范围 / flags 完整性
"""
import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SOURCES = {"usda", "openfoodfacts", "healthy_subset"}
NUTRI = {"calories_kcal", "protein_g", "fat_g", "saturated_fat_g", "carbs_g",
         "fiber_g", "sugar_g", "sodium_mg", "cholesterol_mg",
         "vitamin_c_mg", "calcium_mg", "iron_mg"}
ALLERGEN_KEYS = {"contains_gluten", "contains_dairy", "contains_nuts",
                 "contains_soy", "contains_eggs", "contains_fish"}
RANGE = {"calories_kcal": (0, 2000), "protein_g": (0, 110), "fat_g": (0, 110),
         "saturated_fat_g": (0, 110), "carbs_g": (0, 110), "fiber_g": (0, 110),
         "sugar_g": (0, 110), "sodium_mg": (0, 20000), "cholesterol_mg": (0, 2000),
         "vitamin_c_mg": (0, 5000), "calcium_mg": (0, 5000), "iron_mg": (0, 500)}
errors, must_keys = [], {"food_id", "name", "source", "category", "food_type",
                         "data_type", "per_100g", "serving", "health_score",
                         "health_score_note", "brand", "ingredients", "allergens",
                         "labels", "flags"}


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main():
    path = DATA / "foods_core.json"
    check(path.exists(), f"缺失 foods_core.json")
    if not path.exists():
        return finish()
    obj = json.load(open(path, encoding="utf-8"))
    foods = obj["foods"]

    check(isinstance(foods, list) and len(foods) == 44613, f"条数应为 44613，实际 {len(foods)}")
    ids = [f["food_id"] for f in foods]
    check(len(set(ids)) == len(ids), "存在重复 food_id")
    for i in ids:
        check(bool(re.fullmatch(r"(fdc_\d+|off_[0-9a-f]{8})", i)), f"id 格式异常: {i}")

    for f in foods:
        missing = must_keys - set(f)
        check(not missing, f"{f['food_id']} 缺字段 {missing}")
        check(f["source"] in SOURCES, f"{f['food_id']} 来源非法: {f['source']}")
        p = f["per_100g"]
        check(set(p) == NUTRI, f"{f['food_id']} per_100g 键不齐")
        for k, (lo, hi) in RANGE.items():
            v = p.get(k)
            if v is not None:
                check(lo <= v <= hi, f"{f['food_id']} {k}={v} 越界")
        check(set(f["flags"]) == ALLERGEN_KEYS, f"{f['food_id']} flags 键不齐")
        if f["source"] == "usda":
            check(f["health_score"] is not None, f"{f['food_id']} USDA 应带 health_score")
        check(f["name"].strip() != "", f"{f['food_id']} name 为空")
    return finish()


def finish():
    if errors:
        print(f"校验未通过，共 {len(errors)} 项，前 50：")
        for e in errors[:50]:
            print("  ✗", e)
        return 1
    print("校验通过：结构/条数/id/数值范围/flags 全部一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())