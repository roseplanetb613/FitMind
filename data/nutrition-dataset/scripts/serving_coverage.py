# -*- coding: utf-8 -*-
"""食用份换算覆盖率统计（按 source 分开）"""
import json, sys
sys.path.insert(0, r"e:\FitMind\lib")
from serving_units import serving_grams
from foods_repo import FoodsRepo

repo = FoodsRepo()
for src in ("usda", "openfoodfacts"):
    stats = {"direct": 0, "density": 0, "household": 0, "unsupported": 0, "no_serving": 0}
    for r in repo.by_id.values():
        if r["source"] != src:
            continue
        res = serving_grams(r.get("serving"), r.get("food_type"))
        m = res["method"]
        if m in ("direct", "density", "household", "unsupported"):
            stats[m] += 1
        else:
            stats["no_serving"] += 1
    total = sum(stats.values())
    ok = stats["direct"] + stats["density"] + stats["household"]
    print(f"{src:13s} 共 {total:6d} | 可换算 {ok:6d} ({ok/total:5.1%}) | "
          + " ".join(f"{k}={v}" for k, v in stats.items() if k != "unsupported")
          + f" | 不可换算 unsupported={stats['unsupported']}")
