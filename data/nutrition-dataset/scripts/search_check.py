# -*- coding: utf-8 -*-
"""搜索排序最终回归验证"""
import sys
from collections import Counter

sys.path.insert(0, r"e:\FitMind\lib")
from foods_repo import FoodsRepo

repo = FoodsRepo()
TARGETS = {"苹果": "生苹果", "apple": "apple, raw", "chicken breast": "chicken, breast",
           "milk": "milk, whole", "eggs": "egg, whole", "牛肉": None}
for q, expect in TARGETS.items():
    hits = repo.search(q, limit=8)
    print(f"--- {q}（期待 '{expect}' 靠前）---")
    for i, r in enumerate(hits[:8]):
        mark = " ←" if expect and expect in r["name"].lower() else ""
        print(f"  #{i+1} s{r['_score']} {r['name'][:46]}{mark}")
# 全量快速统计
t0, t1, t2, t3, t4 = Counter(), Counter(), Counter(), Counter(), Counter()
sizes = Counter()
for q in ["苹果", "apple", "milk", "chicken", "rice", "鸡蛋"]:
    h = repo.search(q, limit=10 ** 5)
    sizes[q] = len(h)
print("\n命中量:", dict(sizes))
