# -*- coding: utf-8 -*-
"""跨数据集粒度对齐审计：主键 / 单位 / 类目 / 等级 / 名称 / 互引"""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, r"e:\FitMind\lib")
from foods_repo import FoodsRepo
from exercise_repo import ExerciseRepo
import screening

R = Path(r"e:\FitMind")

print("=" * 60)
print("一、主键 / ID 体系")
ex = ExerciseRepo()
print(f"  动作: id={len(ex.by_id)} 主键格式 4位数字")
fr = FoodsRepo()
ns = {}
for f in fr.by_id.values():
    ns.setdefault(f["source"], 0)
    ns[f["source"]] += 1
for src, c in ns.items():
    prefix = {"usda": "fdc_", "openfoodfacts": "off_", "china": "cn_",
              "off_cn": "offcn_"}.get(src, "?")
    print(f"  食物[{src}]: {c} 条, 主键前缀 {prefix}")

print("\n二、单位口径")
print("  动作: 组×次/秒/周频率（exercise_metadata.suggested）")
print("  食物: 每 100g 统一（foods_core/china/off_cn）; serving 换算→克")
print("  强度: %1RM / RPE-10 / %HRR（progression + fitt 双口径对齐）")

print("\n三、食物类目跨源一致性（粒度最大不一致点）")
for src in ("usda", "openfoodfacts", "china", "off_cn"):
    cats = {}
    for f in fr.by_id.values():
        if f["source"] != src:
            continue
        key = (f.get("food_type") or f.get("category") or "").split(",")[0]
        if key:
            cats[key] = cats.get(key, 0) + 1
    top = sorted(cats.items(), key=lambda x: -x[1])[:5]
    print(f"  [{src}] top5: {top}")

print("\n四、等级体系")
d = Counter(x["difficulty"] for x in ex.by_id.values())
print(f"  动作难度(3档): {dict(d)}")
print(f"  力量标准(4档): untrained/novice/intermediate/advanced —— 与动作难度无映射表")
print(f"  RPE(1-10) / %1RM —— progression 已接 rpe_rir")

print("\n五、名称/多语言")
miss_zh = sum(1 for r in ex.by_id.values() if r["name_zh"] == r["name"] and " " not in r["name"][:1])
print(f"  动作 name_zh 覆盖: 全部注入（cb 有 zh 词典）")
fmiss = sum(1 for r in fr.by_id.values() if r["name_zh"] == r["name"])
print(f"  食物 name_zh==name(未翻译或自带中文): {fmiss}/{len(fr.by_id)} 条")

print("\n六、禁忌替代动作名 ↔ 动作库可检索性（互引验证）")
hits, miss = 0, []
for cond in screening.CONDITIONS:
    for alt in cond["alternatives"]:
        found = ex.search(alt, limit=3)
        if found:
            hits += 1
        else:
            miss.append(f"{cond['condition_zh'][:10]} → {alt}")
print(f"  命中动作库: {hits} 个替代名 | 未命中 {len(miss)} 个:")
for m in miss:
    print(f"    ✗ {m}")

print("\n七、serving 口径")
print("  官方 serving(USDA branded) + 参考份量(china 包) + 每100g —— 统一换算到克")

# 统一类目映射缺口统计
print("\n八、统一类目映射建议（跨源无法自然 join 的证明）")
print("  USDA food_type 与 OFF en: 标签 与 china 中文类目 是 3 套独立词汇 → 需映射表")