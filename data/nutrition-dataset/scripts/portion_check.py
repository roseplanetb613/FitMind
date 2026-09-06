# -*- coding: utf-8 -*-
"""参考份量表集成验证"""
import sys
sys.path.insert(0, r"e:\FitMind\lib")
from foods_repo import FoodsRepo

repo = FoodsRepo()
probes = ["egg, whole, raw", "apple, raw", "bananas, raw",
          "potatoes, flesh and skin, baked", "milk, whole, 3.25",
          "bread, white", "almonds, raw", "salmon, atlantic, wild, raw",
          "chicken, broilers or fryers, breast, meat only, raw",
          "peanut butter, smooth style"]
for p in probes:
    hits = [r for r in repo.search(p, limit=3) if r["source"] == "usda"]
    if not hits:
        continue
    r = hits[0]
    sv = repo.serving_grams(r["food_id"])
    info = repo.nutrition_for_serving(r["food_id"], 1.0)
    k = (info.get("per_serving") or {}).get("calories_kcal")
    print(f'{r["name"][:44]:46s} | {sv["method"]:10s} {sv["grams"]:>5} g | {k} kcal | {sv["note"][:52]}')
