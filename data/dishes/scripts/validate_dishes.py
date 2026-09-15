# -*- coding: utf-8 -*-
"""菜品库结构性校验。八项检查，ERROR 拦提交、WARNING 只提示。

**为什么要有这个脚本**：菜品库是手写的，而手写数据集的失败模式是"沉默的错"——
引一个不存在的 food_id 会静默变成 missing，引一个 calories_kcal 为 None 的油
（cn_1920xx 整个类目都是）会静默少算 130kcal。这两种都不会抛异常，只会给错数字。

跑法：`python data/dishes/scripts/validate_dishes.py`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

DISH_DIR = ROOT / "data" / "dishes" / "data"
MACROS = ("calories_kcal", "protein_g", "fat_g", "carbs_g")
# 每 100g 热量合理区间：蔬菜 ~20，纯油脂 ~900
KCAL_MIN, KCAL_MAX = 20.0, 900.0
# 配方总重与 serving_g 的最大允许偏差
SERVING_TOLERANCE = 0.20


def validate(foods_repo, dish_dir: Path = DISH_DIR):
    """返回 `(errors, warnings)`。errors 非空 = 数据不可用。"""
    errors, warnings = [], []
    with open(Path(dish_dir) / "dishes_core.json", encoding="utf-8") as f:
        dishes = json.load(f)["dishes"]
    with open(Path(dish_dir) / "ingredient_aliases.json", encoding="utf-8") as f:
        aliases = json.load(f)["aliases"]

    seen_ids, seen_names = set(), {}
    for d in dishes:
        did = d.get("dish_id", "<无 id>")

        # ① dish_id 唯一
        if did in seen_ids:
            errors.append(f"[①] dish_id 重复：{did}")
        seen_ids.add(did)

        # ② 名称/别名不撞车
        for n in [d.get("name_zh"), *(d.get("aliases") or [])]:
            if not n:
                continue
            if n in seen_names and seen_names[n] != did:
                errors.append(f"[②] 名称/别名冲突：{n!r} 同时属于 "
                              f"{seen_names[n]} 与 {did}")
            seen_names[n] = did

        recipe = d.get("recipe") or []
        if not recipe:
            errors.append(f"[①] {did} 配方为空")
            continue

        total_g = 0.0
        total_kcal = 0.0
        kcal_known = False
        for it in recipe:
            fid, grams = it.get("food_id"), it.get("grams")

            rec = foods_repo.get(fid) if fid else None
            if rec is None:
                # ③ 引用完整性
                errors.append(f"[③] {did} 引用了不存在的 food_id：{fid}")
                continue

            # ④ 克数区间
            if not isinstance(grams, (int, float)) or grams <= 0:
                errors.append(f"[④] {did} 的 {fid} 克数非法：{grams!r}")
                continue

            total_g += grams

            # ⑤ 宏量完整（**错误级**，理由见模块 docstring）

            # 配方是**我们**写的，没有任何理由去引一条算不出热量的记录：
            # cn_1920xx 植物油 19 条的 calories_kcal 全是 None，引它不会抛异常，
            # 只会让这道菜静默少算 ~130kcal。运行时 nutrition_from_recipe 的
            # None 容忍是留给 VLM 兜底路径的——那条路的数据不是我们挑的，
            # 算不出就只能显示"—"；这里的数据是我们挑的，就该换一条。
            per100 = rec.get("per_100g") or {}
            for m in MACROS:
                if per100.get(m) is None:
                    errors.append(f"[⑤] {did} 的 {fid}（{rec.get('name')}）"
                                  f"缺 {m} —— 会静默低估，换一条完整的")
            if per100.get("calories_kcal") is not None:
                total_kcal += per100["calories_kcal"] * grams / 100.0
                kcal_known = True

        # ⑥ serving_g 与配方总重相符
        #
        # 这里**曾经**还有一条「单个食材不超过总重」的检查，已删除：它不可达。
        # total_g 就是各项 grams 之和，而 ④ 已保证每项 grams > 0，故 g > total_g
        # 恒为 False。死分支比没有分支更坏——它会让人以为这条被守住了，
        # 还会长出一个"验证它"的测试（该测试同样永不成立，见 test_flags_* 那组）。
        # 真正的数量级手滑（把 500 写成 5000）由 ⑥ 的偏差检查与 ⑦ 的密度检查兜住。
        serving = d.get("serving_g")
        if not isinstance(serving, (int, float)) or serving <= 0:
            errors.append(f"[⑥] {did} 的 serving_g 非法：{serving!r}")
        elif total_g > 0 and abs(serving - total_g) / total_g > SERVING_TOLERANCE:
            errors.append(f"[⑥] {did} 的 serving_g={serving} 与配方总重 {total_g:.0f} "
                          f"偏差超过 {SERVING_TOLERANCE:.0%} —— 是不是写错数量级了？")

        # ⑦ 每 100g 热量数量级
        #
        # 密度是"这道菜整体"的量级指纹：生菜本身 12kcal/100g 完全正常，
        # 但一道"菜"折合 12kcal/100g 就说明配方里几乎只有水/菜叶——即写错了。
        # 上界 900 贴着纯油脂，下界 20 贴着最清淡的蔬菜汤。
        if kcal_known and total_g > 0:
            density = total_kcal * 100.0 / total_g
            if not (KCAL_MIN <= density <= KCAL_MAX):
                errors.append(f"[⑦] {did} 折合 {density:.0f} kcal/100g，"
                              f"超出 {KCAL_MIN:.0f}~{KCAL_MAX:.0f} 合理区间")
            elif density < 60 or density > 450:
                warnings.append(f"[⑦] {did} 折合 {density:.0f} kcal/100g，偏高/偏低，确认一下")

    # 别名表：引用的 food_id 必须存在**且宏量完整**（⑧）
    # 宏量完整性在这里同样是**错误级**，理由与 ⑤ 相同，且更隐蔽：别名表把
    # 口语名直接指到 food_id，绕过了 search。若指向 cn_1920xx，运行时会静默
    # 按 0 算热量。Task 1 评审就是靠这条发现的（「豆油」曾无别名）。
    for name, fid in aliases.items():
        rec = foods_repo.get(fid)
        if rec is None:
            errors.append(f"[⑧] 别名表：{name!r} 引用了不存在的 food_id：{fid}")
            continue
        for m in MACROS:
            if (rec.get("per_100g") or {}).get(m) is None:
                errors.append(f"[⑧] 别名表：{name!r} → {fid}"
                              f"（{rec.get('name')}）缺 {m} —— 会静默低估")

    return errors, warnings


def main() -> int:
    from foods_repo import FoodsRepo
    errors, warnings = validate(FoodsRepo())
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"\n{len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
