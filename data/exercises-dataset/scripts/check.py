# -*- coding: utf-8 -*-
"""定向核验：特定动作的分类 + 变体族 + 处方/MET 抽查"""
import json, os, sys
from collections import Counter

# 相对脚本定位 —— 原先写死了开发者本机的绝对路径（e:\FitMind\exercises-dataset\），
# 数据包挪进 data/ 之后这个脚本就一直 FileNotFoundError，等于废了。
DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
_load = lambda name: json.load(open(os.path.join(DATA_DIR, name), encoding="utf-8"))

meta = {m["id"]: m for m in _load("exercise_metadata.json")["exercises"]}
raw = {e["id"]: e for e in _load("exercises.json")}
fam = {x["family_id"]: x for x in _load("variant_families.json")["families"]}

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