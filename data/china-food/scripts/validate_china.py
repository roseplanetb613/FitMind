# -*- coding: utf-8 -*-
"""validate_china.py — foods_china.json 一致性校验（失败退出码 1）"""
import json
import re
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
NUTRI = {"calories_kcal", "protein_g", "fat_g", "carbs_g", "fiber_g", "sugar_g",
         "sodium_mg", "cholesterol_mg", "vitamin_c_mg", "calcium_mg", "iron_mg"}
RANGE = {"calories_kcal": (0, 2000), "protein_g": (0, 110), "fat_g": (0, 110),
         "carbs_g": (0, 110), "fiber_g": (0, 110), "sodium_mg": (0, 20000),
         "cholesterol_mg": (0, 4000), "vitamin_c_mg": (0, 5000),
         "calcium_mg": (0, 12000), "iron_mg": (0, 500)}
errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main():
    path = DATA / "foods_china.json"
    check(path.exists(), "缺失 foods_china.json")
    if not path.exists():
        return finish()
    obj = json.load(open(path, encoding="utf-8"))
    foods = obj["foods"]
    check(len(foods) == 1677, f"条数应为 1677，实际 {len(foods)}")
    ids = [f["food_id"] for f in foods]
    check(len(set(ids)) == len(ids), "food_id 重复")
    for i in ids:
        # 少量 code 带识别标记字母（如 102101x），放行 数字+小写字母
        check(bool(re.fullmatch(r"cn_[a-z0-9]+", i)), f"id 格式异常: {i}")
    for f in foods:
        check(f["source"] == "china", f"{f['food_id']} source 非法")
        if not str(f["name"]).strip():
            errors.append(f"{f['food_id']} name 为空")
        p = f["per_100g"]
        check(set(p) == NUTRI, f"{f['food_id']} per_100g 键不齐: {sorted(NUTRI - set(p))}")
        for k, (lo, hi) in RANGE.items():
            v = p.get(k)
            if v is not None:
                check(lo <= v <= hi, f"{f['food_id']} {k}={v} 越界")
        check(f["name_zh"] == f["name"], f"{f['food_id']} name_zh 应与中文名一致")
        check(isinstance(f.get("extra"), dict), f"{f['food_id']} extra 缺失")
    return finish()


def finish():
    if errors:
        print(f"校验未通过，共 {len(errors)} 项，前 30：")
        for e in errors[:30]:
            print("  ✗", e)
        return 1
    print("校验通过：结构/条数/id/数值范围/extra 全部一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())