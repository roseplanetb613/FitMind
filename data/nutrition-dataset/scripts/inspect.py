# -*- coding: utf-8 -*-
"""nutrition 数据体检：行数/列/空值/示例/重复"""
import csv, os
from collections import Counter

DATA = r"e:\FitMind\nutrition-dataset\data"
for f in os.listdir(DATA):
    if not f.endswith(".csv"):
        continue
    path = os.path.join(DATA, f)
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print(f"{f}: 空")
        continue
    n = len(rows)
    fields = list(rows[0].keys())
    # 空值率
    miss = {col: sum(1 for r in rows if not (r.get(col) or "").strip()) for col in fields}
    miss = {k: v for k, v in sorted(miss.items(), key=lambda x: -x[1]) if v > 0}
    print(f"\n=== {f} | 行数 {n} | 列 {len(fields)} ===")
    print("列:", ", ".join(fields))
    print(f"空值Top5: {list(miss.items())[:5]}")
    # 首行示例
    print("示例:", {k: v[:32] for k, v in list(rows[0].items())[:8]})