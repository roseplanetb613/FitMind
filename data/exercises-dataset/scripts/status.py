# -*- coding: utf-8 -*-
"""数据层现状盘点"""
import json, os
from collections import Counter

D = "e:/FitMind/exercises-dataset"
data = json.load(open(D + "/data/exercises.json", encoding="utf-8"))
meta = {m["id"]: m for m in json.load(open(D + "/data/exercise_metadata.json", encoding="utf-8"))["exercises"]}
onto = json.load(open(D + "/data/muscle_ontology.json", encoding="utf-8"))["muscles"]
mapping = json.load(open(D + "/data/muscle_mapping.json", encoding="utf-8"))["mapping"]
fam = json.load(open(D + "/data/variant_families.json", encoding="utf-8"))["families"]

print("== 原始数据 ==")
print("动作:", len(data))
langs = sorted({k for e in data for k in e["instructions"]})
print("语言:", str(len(langs)) + " 种:", " ".join(langs))
print("图片/GIF:", len(os.listdir(D + "/images")), "/", len(os.listdir(D + "/videos")))

print("\n== 增强文件 ==")
print("肌肉本体:", len(onto), "| 归一映射:", len(mapping), "| 变体族:", len(fam))
for f in sorted(os.listdir(D + "/data")):
    if f.endswith(".json"):
        print(f"  {f}  {os.path.getsize(D + '/data/' + f) // 1024} KB")

print("\n== 增强字段分布 ==")
print("难度:", dict(Counter(m["difficulty"] for m in meta.values())))
print("模式:", dict(Counter(m["movement_pattern"] for m in meta.values())))
print("类型:", dict(Counter(m["exercise_type"] for m in meta.values())))
print("器械归一:", dict(Counter(m["normalized_equipment"] for m in meta.values())))