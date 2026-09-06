# -*- coding: utf-8 -*-
"""build_enrichment.py 输出抽检评审：打印难度与模式命中的样本，供人工核验调优。"""
import json, re, sys

with open(r"e:\FitMind\exercises-dataset\data\exercise_metadata.json", encoding="utf-8") as f:
    meta = {m["id"]: m for m in json.load(f)["exercises"]}
with open(r"e:\FitMind\exercises-dataset\data\exercises.json", encoding="utf-8") as f:
    raw = {e["id"]: e for e in json.load(f)}

def show(title, cond, limit=200, order_by_name=False):
    rows = [m for m in meta.values() if cond(m)]
    if order_by_name:
        rows = sorted(rows, key=lambda m: m["name"].lower())
    print(f"\n=== {title} (共 {len(rows)} 条, 显示前 {min(limit, len(rows))}) ===")
    for m in rows[:limit]:
        e = raw[m["id"]]
        print(f"  d{m['difficulty']} {m['difficulty_label']:12s} | {m['movement_pattern']:6s} | "
              f"{m['exercise_type']:19s} | {m['name']} [{e['equipment']}]")

mode = sys.argv[1] if len(sys.argv) > 1 else "all"

if mode in ("all", "other"):
    show("other 模式（可能需细分）", lambda m: m["movement_pattern"] == "other", 120, True)
if mode in ("all", "d1"):
    show("难度=1 抽查（按名排序）", lambda m: m["difficulty"] == 1, 60, True)
if mode in ("all", "d2"):
    show("难度=2 抽查（按名排序）", lambda m: m["difficulty"] == 2, 60, True)
if mode in ("all", "d3"):
    show("难度=3（高级）全部", lambda m: m["difficulty"] == 3, 200, True)
if mode in ("all", "cardio"):
    show("cardio 模式全部", lambda m: m["movement_pattern"] == "cardio", 60, True)