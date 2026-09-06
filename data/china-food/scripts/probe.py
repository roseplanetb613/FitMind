# -*- coding: utf-8 -*-
"""探测 china-food JSON 结构"""
import json, glob, os

d = r"e:\FitMind\data\china-food\raw\repo\json_data_v3_20260825_qwen38max_kimi_k3_fixed"
files = [f for f in glob.glob(os.path.join(d, "merged_*.json"))]
print("merged 文件数:", len(files))

if files:
    with open(files[0], encoding="utf-8") as f:
        data = json.load(f)
    print("样例文件:", os.path.basename(files[0]))
    print("类型:", type(data))
    if isinstance(data, list):
        print("条数:", len(data))
        print("首条键:", list(data[0].keys()))
        print("首条:", json.dumps(data[0], ensure_ascii=False, indent=1)[:800])
    elif isinstance(data, dict):
        print("顶层键:", list(data.keys())[:20])
        for k in list(data.keys())[:1]:
            v = data[k]
            if isinstance(v, list) and v:
                print(f"  {k}: list[{len(v)}] 首条:", json.dumps(v[0], ensure_ascii=False)[:500])
            else:
                print(f"  {k} = {str(v)[:200]}")

# 统计全部条数与唯一字段集合
fields = set()
total = 0
for fp in files:
    with open(fp, encoding="utf-8") as f:
        obj = json.load(f)
    items = obj if isinstance(obj, list) else list(obj.values())[0]
    if isinstance(items, list):
        total += len(items)
        for it in items:
            fields.update(it.keys())
print("\n全量条数:", total, "| 字段全集:", sorted(fields))