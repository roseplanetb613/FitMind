# -*- coding: utf-8 -*-
"""抽样食物名结构：逗号/空格 token 高频词"""
import json, re
from collections import Counter

foods = json.load(open(r"e:\FitMind\nutrition-dataset\data\foods_core.json", encoding="utf-8"))["foods"]
names = [f["name"] for f in foods]
print("总条数:", len(names), "| unique 名:", len(set(names)))

comma = sum(1 for n in names if "," in n)
print(f"含逗号: {comma} ({comma/len(names):.1%})")

tokens = Counter()
for n in names:
    # 逗号、括号、斜杠分割，去标点
    parts = re.split(r"[,()/]", n)
    for p in parts:
        p = p.strip().lower()
        if p:
            tokens[p] += 1
print("\n分段后 unique 段:", len(tokens))
print("\n=== 频率 Top 60 段 ===")
for t, c in tokens.most_common(60):
    print(f"{c:6d}  {t}")
print("\n=== 出现次数=1 的段数:", sum(1 for c in tokens.values() if c == 1))