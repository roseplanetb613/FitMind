# -*- coding: utf-8 -*-
"""
serving_units.py — 食物食用份 → 克 换算层
=========================================
USDA serving 字段：{size, unit, household}
  - unit 为 g/grm/gm 时 size 即克数（直接可用，占 78.8%）
  - unit 为 ml/mlt 时按食物大类密度估算（g = ml × 密度）
  - unit 为 mg 时 /1000；iu/mc 无法换算返回 None
  - size 缺失/不可用时，尝试解析 household 文本（"1 cup"/"2 tbsp"...）

确定性优先：算不出来的返回 None + reason，不瞎猜。
"""
from __future__ import annotations

import re
import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "nutrition-dataset" / "data"

# 食物大类 → 密度(g/ml)，估算值用于体积类单位（注释为典型值）
DENSITY_RULES = [
    (("beverage", "juice", "drink", "water", "milk", "tea", "coffee", "soda",
      "energy drink"), 1.03),
    (("fruit",), 0.62),                       # 切块水果 ~150g/cup
    (("vegetable", "salad", "greens"), 0.55),  # 叶菜/切块蔬菜
    (("grain", "bread", "cereal", "rice", "pasta", "flour", "bakery", "muffin",
      "roll", "biscuit"), 0.55),              # 面粉类 ~125g/cup
    (("nut", "seed", "dried", "dehydrated"), 0.50),
    (("oil", "fat", "butter", "margarine", "shortening"), 0.92),
    (("syrup", "honey", "molasses", "maple"), 1.35),
    (("sugar", "sweetener"), 0.85),           # 白砂糖 ~200g/cup
    (("meat", "poultry", "chicken", "beef", "pork", "lamb", "fish", "seafood",
      "turkey", "ham", "sausage", "bacon"), 1.03),
]
DEFAULT_DENSITY = 1.0

# 体积类单位 → 标称 ml（×密度 = 克）
VOLUME_ML = {"cup": 240.0, "tbsp": 15.0, "tsp": 5.0, "floz": 29.57, "pint": 473.2,
             "quart": 946.4, "gallon": 3785.4, "ml": 1.0}
# 重量类单位 → 克
WEIGHT_G = {"g": 1.0, "grm": 1.0, "gm": 1.0, "kg": 1000.0, "oz": 28.35, "onz": 28.35,
            "oza": 28.35, "lb": 453.6, "stick": 113.4, "mg": 1e-3}
# 无法换算的单位
NON_WEIGHT = {"iu", "mc", "icl"}


def density_of(food_type: str | None) -> float:
    ft = (food_type or "").lower()
    for kws, d in DENSITY_RULES:
        if any(k in ft for k in kws):
            return d
    return DEFAULT_DENSITY


def _norm_unit(u: str) -> str:
    s = u.strip().lower().rstrip("s")
    aliases = {"gr": "g", "grms": "grm", "grams": "grm", "gram": "grm",
               "mlt": "ml", "milliliters": "ml", "milliliter": "ml",
               "fluid": "floz", "fluid ounce": "floz", "fluid ounces": "floz",
               "ounces": "oz", "ounce": "oz", "pounds": "lb", "pound": "lb",
               "whole": "piece", "container": "package", "bottle": "package"}
    return aliases.get(s, s)


def household_grams(text: str | None, density: float) -> tuple[float | None, str]:
    """解析 household 文本（"1 cup" / "1/2 cup" / "2 oz"...），返回 (克, 说明)。"""
    if not text:
        return None, "no household"
    t = text.strip().lower()
    m = re.match(r"([\d./]+)\s*([a-z\- ]+)", t)
    if not m:
        return None, f"unparsed household: {t[:40]}"
    qty = 1.0
    num = m.group(1)
    if "/" in num:
        a, b = num.split("/")
        try:
            qty = float(a) / float(b)
        except (ValueError, ZeroDivisionError):
            return None, f"bad fraction: {num}"
    else:
        try:
            qty = float(num)
        except ValueError:
            return None, f"bad number: {num}"
    unit = _norm_unit(m.group(2).split(",")[0].strip())
    if unit in WEIGHT_G:
        return qty * WEIGHT_G[unit], f"household:{num} {unit}"
    if unit in VOLUME_ML:
        return qty * VOLUME_ML[unit] * density, f"household:{num} {unit}(密度{density:.2f})"
    return None, f"unknown unit: {unit}"


def serving_grams(serving: dict | None, food_type: str | None = None) -> dict:
    """由 serving 记录计算克数。返回 {grams, method, note}；无法换算 grams=None。"""
    if not serving:
        return {"grams": None, "method": "none", "note": "无 serving 信息"}
    size = serving.get("size")
    unit = _norm_unit(serving.get("unit") or "")
    density = density_of(food_type)

    if unit in WEIGHT_G:
        if size is None:
            return {"grams": None, "method": "size_only", "note": "unit 但 size 为空"}
        return {"grams": round(size * WEIGHT_G[unit], 2), "method": "direct",
                "note": f"{size} {unit}"}
    if unit in VOLUME_ML:
        if size is None:
            return {"grams": None, "method": "size_only", "note": "unit 但 size 为空"}
        return {"grams": round(size * VOLUME_ML[unit] * density, 2), "method": "density",
                "note": f"{size} {unit} × 密度{density:.2f}"}
    if unit in NON_WEIGHT:
        # 兜底：尝试 household 文本
        g, note = household_grams(serving.get("household"), density)
        return {"grams": g, "method": "household" if g else "unsupported",
                "note": note if g is None else f"{serving.get('household')} → {g}g"}

    # 无有效 unit（如 'piece'/'serving'）或空：household 兜底
    g, note = household_grams(serving.get("household"), density)
    return {"grams": g, "method": "household" if g else "unsupported", "note": note}


def export_table(path: Path = DATA_DIR / "serving_units.json") -> None:
    """导出静态换算表（供审查/文档）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "version": "1.0",
            "weight_g": WEIGHT_G, "volume_ml": VOLUME_ML,
            "density_rules": [{"match": kws, "g_per_ml": d} for kws, d in DENSITY_RULES],
            "default_density": DEFAULT_DENSITY,
            "non_weight_units": sorted(NON_WEIGHT),
        }, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    export_table()
    print("serving_units.json 已导出")