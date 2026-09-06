# -*- coding: utf-8 -*-
"""
build_china.py — 中国食物成分表数据标准化（源='china'）
========================================================
读 raw/repo/json_data_v3_20260825_qwen38max_kimi_k3_fixed/merged_*.json
（来源：《中国食物成分表》第6版 识别修正版，1718 span 来源见 data_dictionary.md 版权注释）
输出 data/foods_china.json，字段对齐 foods_core（每 100g），source='china'。

处理要点：
  - '—'（无此数据）及空值 → None
  - kcal/kJ 双单位自带，直接使用 energyKCal
  - 保留成分表独有字段：可食部分(edible)、水分、灰分、维生素A、B1/B2/烟酸、
    胡萝卜素/视黄醇/维E、矿物质 K/P/Mg/Zn/Se/Cu/Mn 到 extra
  - 无糖细分、无过敏原（flags 全 false）、无官方份量
用法：python scripts/build_china.py
"""
import glob
import json
import os
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "raw" / "repo" \
      / "json_data_v3_20260825_qwen38max_kimi_k3_fixed"
OUT = Path(__file__).resolve().parent.parent / "data" / "foods_china.json"

MISSING = {"—", "-", "", None, "nan"}

CORE_MAP = {"calories_kcal": "energyKCal", "protein_g": "protein", "fat_g": "fat",
            "carbs_g": "CHO", "fiber_g": "dietaryFiber", "sugar_g": None,
            "sodium_mg": "Na", "cholesterol_mg": "cholesterol",
            "vitamin_c_mg": "vitaminC", "calcium_mg": "Ca", "iron_mg": "Fe"}
EXTRA_MAP = {"edible": "edible", "water": "water", "ash": "ash", "energy_kj": "energyKJ",
             "vitamin_a": "vitaminA", "carotene": "carotene", "retinol": "retinol",
             "thiamin": "thiamin", "riboflavin": "riboflavin", "niacin": "niacin",
             "vitamin_e": "vitaminETotal", "potassium_mg": "K", "phosphorus_mg": "P",
             "magnesium_mg": "Mg", "zinc_mg": "Zn", "selenium_ug": "Se",
             "copper_mg": "Cu", "manganese_mg": "Mn"}


def fnum(v):
    s = str(v).strip().replace(",", "") if v is not None else ""
    if not s or s in MISSING:
        return None
    try:
        return round(float(s), 3)
    except ValueError:
        return None


def main():
    files = sorted(glob.glob(str(RAW / "merged_*.json")))
    foods, seen = [], set()
    stats = {"category_files": len(files)}
    for fp in files:
        cat = os.path.basename(fp)[len("merged_"):-5]
        cat, _, sub = cat.partition("-")
        with open(fp, encoding="utf-8") as f:
            items = json.load(f)
        for r in items:
            code = str(r.get("foodCode") or "").strip()
            name = str(r.get("foodName") or "").strip()
            if not name or code in seen:
                continue
            seen.add(code)
            per = {}
            for k, src in CORE_MAP.items():
                per[k] = fnum(r.get(src)) if src else None
            # 能量钳制（每100g 合理上限 2000 kcal）
            if per["calories_kcal"] is not None and per["calories_kcal"] > 2000:
                stats.setdefault("energy_clamped", 0)
                stats["energy_clamped"] += 1
                per["calories_kcal"] = None
            extra = {k: fnum(r.get(src)) for k, src in EXTRA_MAP.items()}
            foods.append({
                "food_id": f"cn_{code}",
                "name": name,
                "name_zh": name,
                "source": "china",
                "category": cat or None,
                "food_type": sub or None,
                "data_type": "china-food-composition-6th",
                "per_100g": per,
                "serving": {"size": None, "unit": None, "household": None},
                "health_score": None, "health_score_note": None,
                "brand": None, "ingredients": None, "allergens": None,
                "labels": {},
                "flags": {"contains_gluten": False, "contains_dairy": False,
                          "contains_nuts": False, "contains_soy": False,
                          "contains_eggs": False, "contains_fish": False},
                "extra": extra,
            })
    stats["total"] = len(foods)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0", "source": "china",
                   "note": "《中国食物成分表》第6版识别修正版(来源见 docs/data_dictionary.md)；"
                           "本次转录自第三方解析数据，商用需另行确认授权；每100g；'—'=无数据",
                   "unit": "per 100g", "foods": foods}, f, ensure_ascii=False, indent=1)
    print(f"china 条目: {stats['total']} | 类别文件 {stats['category_files']} | "
          f"能量钳制 {stats.get('energy_clamped', 0)}")
    ok = sum(1 for f in foods if f["per_100g"]["calories_kcal"] is not None)
    print(f"热量有效 {ok}/{len(foods)}")
    print("样例:", [(f["name"], f["per_100g"]["calories_kcal"],
                     f["per_100g"]["protein_g"]) for f in foods[:3]])


if __name__ == "__main__":
    main()