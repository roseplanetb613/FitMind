# -*- coding: utf-8 -*-
"""
build_food_families.py — 食物同源族构建
========================================
把 USDA 条目按「去制备态 + 词形归一」归为同源族（family）：
  - 同一食物多个条目（raw/cooked/大小写/变体）归一组，供检索召回与替换建议
  - **不合并营养数值**：族内每条保留自己的制备态与营养（raw≠cooked）
  - Open Food Facts 品牌条目不进族（单点品牌，语义独立）

输出 data/food_families.json；幂等可复现。
用法：python scripts/build_food_families.py
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# 制备态词（名末段；一族内成员以制备态区分，营养不可互换）
PREP_WORDS = ["raw", "cooked", "roasted", "boiled", "broiled", "grilled", "fried",
              "baked", "steamed", "braised", "smoked", "cured", "dried", "frozen",
              "canned", "fresh", "pickled", "fermented", "dehydrated", "salted",
              "unsalted", "instant", "prepared", "processed", "pasteurized",
              "whole", "ground", "minced", "chopped", "sliced", "diced"]


def norm_key(name: str) -> str:
    """归一化族键：小写、分段、去尾制备态、去复数。低风险变换，宁漏勿误。"""
    s = name.lower().strip()
    segs = [x.strip() for x in re.split(r"[,()/]", s) if x.strip()]
    if segs and segs[-1] in PREP_WORDS:
        segs = segs[:-1]
    core = " ".join(segs)
    core = core.replace("%", "百分")          # 保数字语义（2%→2百分）
    core = re.sub(r"[^a-z0-9 ]", " ", core)
    tokens = [t for t in re.sub(r"\s+", " ", core).strip().split(" ") if t]
    if tokens and len(tokens[-1]) > 3 and tokens[-1].endswith("s") \
            and not tokens[-1].endswith(("ss", "us", "is", "as", "osis")):
        tokens[-1] = tokens[-1][:-1]
    # 尾词与首词复读（"apple juice, apple"）——去掉冗余尾词
    if len(tokens) > 1 and tokens[-1] == tokens[0]:
        tokens = tokens[:-1]
    return " ".join(tokens)


def prep_state(name: str) -> str:
    """提取制备态标签（最后一段命中制备词），否则 plain。"""
    segs = [x.strip().lower() for x in re.split(r"[,(/]", name) if x.strip()]
    for seg in reversed(segs):
        if seg in PREP_WORDS:
            return seg
    return "plain"


def main():
    foods = json.load(open(DATA / "foods_core.json", encoding="utf-8"))["foods"]
    usda = [f for f in foods if f["source"] == "usda"]

    groups = defaultdict(list)
    for f in usda:
        groups[norm_key(f["name"])].append(f)

    families, singles = [], 0
    for key, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(members) < 2:
            singles += 1
            continue
        if len(members) > 40:          # 过大的族（语义稀释）拆出尾部
            members = members[:40]
        families.append({
            "family_id": f"fam{len(families) + 1:04d}",
            "stem": key,
            "member_count": len(members),
            "members": [{
                "food_id": m["food_id"],
                "name": m["name"],
                "name_zh": None,       # 由检索层按 name 查 name_zh
                "prep_state": prep_state(m["name"]),
            } for m in sorted(members, key=lambda x: x["food_id"])],
        })

    with open(DATA / "food_families.json", "w", encoding="utf-8") as f:
        json.dump({"version": "1.0",
                   "scope": "usda only; prep-state members keep own nutrition",
                   "family_count": len(families),
                   "singleton_usda": singles,
                   "families": families}, f, ensure_ascii=False, indent=1)

    sizes = Counter(f["member_count"] for f in families)
    print(f"USDA 条目: {len(usda)} | 族: {len(families)} | 单条不入族: {singles}")
    print(f"族大小分布: {dict(sorted(sizes.items()))}")
    print("示例族:")
    for f in families[:5]:
        names = sorted({m["name"] for m in f["members"]})
        print(f"  {f['family_id']} {f['stem']} x{f['member_count']}: {names[:5]}")


if __name__ == "__main__":
    main()