# -*- coding: utf-8 -*-
"""中国食品数据覆盖盘点：OFF 中文产品 / USDA 中式类目"""
import json, re, sys
sys.path.insert(0, r"e:\FitMind\lib")
from foods_repo import FoodsRepo

repo = FoodsRepo()
foods = list(repo.by_id.values())
off = [f for f in foods if f["source"] == "openfoodfacts"]
usda = [f for f in foods if f["source"] == "usda"]

def has_cjk(s):
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))

off_zh_name = [f for f in off if has_cjk(f["name"])]
off_zh_brand = [f for f in off if has_cjk(f.get("brand") or "")]
off_cn_cat = [f for f in off if re.search(r"cn:|china|chinese|mandarin", (f.get("category") or "").lower())]
usda_chinese = [f for f in usda if re.search(r"chinese|china|asian|mandarin|soy sauce|tofu|dim sum",
                                             f["name"].lower())]

print(f"OFF 总数 {len(off)}")
print(f"  OFF 名含中文: {len(off_zh_name)}")
print(f"  OFF 品牌中文: {len(off_zh_brand)}")
print(f"  OFF 类目含 cn/china/chinese: {len(off_cn_cat)}")
print(f"USDA 总数 {len(usda)}")
print(f"  USDA 中式/亚洲相关: {len(usda_chinese)}")

print("\n=== OFF 中文产品样例（前10） ===")
for f in off_zh_name[:10]:
    print(f"  {f['name'][:46]} | brand={f.get('brand')}")
print("\n=== OFF 中国类目标签样例 ===")
for f in off_cn_cat[:8]:
    print(f"  {f['name'][:40]} | cat={f.get('category')[:50]}")
print("\n=== USDA 中式/亚洲条目样例 ===")
for f in usda_chinese[:10]:
    print(f"  {f['name'][:56]}")