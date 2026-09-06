# -*- coding: utf-8 -*-
"""foods_repo 组合查询演示：python examples/quickstart_nutrition.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from foods_repo import FoodsRepo  # noqa: E402

repo = FoodsRepo()


def show(records, label, n=6):
    print(f"\n{label}（{len(records)} 条，前 {min(n, len(records))}）")
    for r in records[:n]:
        p = r["per_100g"]
        fam = f" | 族:{r['family_name']}" if r.get("family_name") else ""
        print(f"  [{r['food_id']}] {r['name_zh']} | {p['calories_kcal']}kcal 蛋{p['protein_g']}g "
              f"脂{p['fat_g']}g 碳{p['carbs_g']}g{r['source']}{fam}")


if __name__ == "__main__":
    # 1) 中文搜索
    show(repo.search("苹果"), "① 中文搜索「苹果」")
    show(repo.search("chicken"), "② 英文搜索「chicken」")

    # 2) 高蛋白低脂低钠（增肌食物候选）
    show(repo.filter(protein_min=25, kcal_max=260, fat_max=10, sodium_max=400,
                     source="usda", sort_by="protein_desc"), "③ 高蛋白低脂低钠：蛋白≥25g/100g")

    # 3) 减脂友好：低脂低糖高纤维
    show(repo.filter(kcal_max=60, fat_max=1.5, sugar_max=5, fiber_min=2.5,
                     sort_by="health_desc"), "④ 减脂蔬菜候选：热量≤60 脂肪≤1.5 纤维≥2.5")

    # 4) 过敏排除
    show(repo.filter(source="openfoodfacts", protein_min=10, kcal_max=250,
                     allergen_free=["gluten", "dairy"], nutriscore=["A"]),
         "⑤ 无麸质无乳制品 + Nutri-Score A")

    # 5) 族替换（鸡胸肉族的制备态成员）
    chk = repo.search("chicken breast", limit=50)
    base = next((r for r in chk if r["family"]), None)
    if base:
        print(f"\n⑥ 替换参考（{base['name_zh']} 的同源族，{len(repo.siblings(base['food_id']))} 条）:")
        for m in repo.siblings(base["food_id"])[:6]:
            print(f"  [{m['food_id']}] {m['name']} （{m['prep_state']}）")

    # 7) 食用份换算：选有官方 serving 的条目（基础生鲜多半无官方份量，返回 None 是诚实行为）
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
    from serving_units import serving_grams as _sg
    ok = [r for r in repo.by_id.values()
          if r["source"] == "usda" and _sg(r.get("serving"), r.get("food_type"))["grams"]]
    print(f"\n⑦ USDA 条目可换算份量比例：{len(ok)}/{sum(1 for r in repo.by_id.values() if r['source']=='usda')}")
    for r in ok[:3]:
        info = repo.nutrition_for_serving(r["food_id"], 1.0)
        p = info["per_serving"] or {}
        print(f"    {info['name_zh']}：一份 {info['grams']}g（{info['serving_note']}）"
              f" → {p.get('calories_kcal')} kcal / 蛋白 {p.get('protein_g')}g")
    # 演示"无法换算"的诚实返回（基础生鲜无官方份量）
    egg = next(r for r in repo.search("egg, whole, raw") if r["source"] == "usda")
    info = repo.nutrition_for_serving(egg["food_id"])
    print(f"    基础生鲜（{info['name_zh']}）→ {info['error']}")