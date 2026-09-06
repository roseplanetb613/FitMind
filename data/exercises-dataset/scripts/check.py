# -*- coding: utf-8 -*-
"""定向核验：特定动作的分类 + 变体族 + 处方/MET 抽查"""
import json, sys
from collections import Counter

with open(r"e:\FitMind\exercises-dataset\data\exercise_metadata.json", encoding="utf-8") as f:
    meta = {m["id"]: m for m in json.load(f)["exercises"]}
with open(r"e:\FitMind\exercises-dataset\data\exercises.json", encoding="utf-8") as f:
    raw = {e["id"]: e for e in json.load(f)}
with open(r"e:\FitMind\exercises-dataset\data\variant_families.json", encoding="utf-8") as f:
    fam = {x["family_id"]: x for x in json.load(f)["families"]}

# 1. 定向核验
targets = ["narrow stance squat", "sitted alternate", "butterfly", "twisting seated row",
           "squatting row", "hyperextension", "clean and press", "clean & press", "skull crusher",
           "tricep pushdown", "palms-down wrist curl", "body-up", "front lever", "straight bar dip",
           "smith reverse calf", "deadlift", "good morning", "burpee", "jump rope", "wall ball",
           "tire flip", "carry", "hip thrust"]
print("=== 定向核验 ===")
for m in meta.values():
    n = m["name"].lower()
    if any(t in n for t in targets):
        e = raw[m["id"]]
        print(f"  d{m['difficulty']} {m['difficulty_label']:12s} | {m['movement_pattern']:6s} | "
              f"{m['exercise_type']:19s} | {m['name']} [{e['equipment']}] | 处方{m['suggested']} | MET{m['met_range']}")

# 2. 变体族质量
print("\n=== 变体族中 前5和后5 ===")
for fid in sorted(fam, key=lambda x: -len(fam[x]["member_ids"]))[:5] + sorted(fam, key=lambda x: len(fam[x]["member_ids"]))[:5]:
    f_ = fam[fid]
    names = [m["name"] for m in meta.values() if m["id"] in f_["member_ids"]]
    print(f"  {fid} {f_['stem']} x{len(names)}: {names[:4]}" )

# 3. 处方与 MET 分布
print("\n=== 处方种类 ===")
for k, v in Counter(str(m["suggested"]) for m in meta.values()).most_common():
    print(f"  {v:4d}  {k}")
print("\n=== MET 典型值分布 ===")
for k, v in Counter(m["met_typical"] for m in meta.values()).most_common():
    print(f"  {v:4d}  MET {k}")