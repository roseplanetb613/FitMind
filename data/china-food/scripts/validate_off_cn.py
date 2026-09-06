# -*- coding: utf-8 -*-
"""validate_off_cn.py — off_cn.json 校验（失败退出码 1）"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
NUTRI = {"calories_kcal", "protein_g", "fat_g", "saturated_fat_g", "carbs_g",
         "fiber_g", "sugar_g", "sodium_mg", "cholesterol_mg",
         "vitamin_c_mg", "calcium_mg", "iron_mg"}
KEYS = {"food_id", "name", "source", "per_100g", "flags", "labels",
        "brand", "ingredients", "allergens", "serving", "labels"}
errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main():
    path = DATA / "off_cn.json"
    if not path.exists():
        errors.append("缺失 off_cn.json")
        return finish()
    obj = json.load(open(path, encoding="utf-8"))
    foods = obj["foods"]
    check(len(foods) >= 800, f"期望 ≥800 条（当前 {len(foods)}，OFF 限流致部分缺页，见 docs）")
    ids = [f["food_id"] for f in foods]
    check(len(set(ids)) == len(ids), "food_id 重复")
    for f in foods:
        check(f["source"] == "off_cn", f"{f['food_id']} source 非法")
        check(set(f["per_100g"]) == NUTRI, f"{f['food_id']} per_100g 键不齐")
        check(set(f["flags"]) == {"contains_gluten", "contains_dairy",
                                   "contains_nuts", "contains_soy",
                                   "contains_eggs", "contains_fish"},
              f"{f['food_id']} flags 键不齐")
        for k in ("calories_kcal", "protein_g", "fat_g", "carbs_g"):
            v = f["per_100g"][k]
            if v is not None:
                check(0 <= v <= 2000, f"{f['food_id']} {k}={v} 异常")
    return finish()


def finish():
    if errors:
        print(f"校验未通过，共 {len(errors)} 项，前 20：")
        for e in errors[:20]:
            print("  ✗", e)
        return 1
    print("校验通过：off_cn 结构/条数/id/flags 一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())