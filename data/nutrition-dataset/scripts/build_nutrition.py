# -*- coding: utf-8 -*-
"""
build_nutrition.py — 营养数据归一化管道
========================================
读取 data/*.csv（Kaggle: Global Food & Nutrition Database 2026, CC BY-SA 4.0），
输出统一核心集 data/foods_core.json。

统一口径：
  - 计量统一为「每 100g」
  - 主键：USDA 条目 fdc_<fdc_id>；Open Food Facts 条目 off_<md5(name|brands)8位>（源数据无稳定 id）
  - 来源标注 source: usda | openfoodfacts | healthy_subset
  - health_score 为作者自定义评分（0-100），此处原样保留并标记 reference=作者自拟
  - OFF 品牌食品的过敏原/饮食限制标志合并自三个 OFF 分文件
  - 保留幂等：重复运行输出一致

用法：python scripts/build_nutrition.py
"""
import csv
import hashlib
import json
import os
from collections import Counter

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NULL = {"", "nan", "N/A", "None", "null"}


def read_csv(name):
    with open(os.path.join(DATA, "data", name), encoding="utf-8-sig", errors="replace") as f:
        return list(csv.DictReader(f))


def fnum(v):
    """容错转 float；空/非法返回 None。"""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s or s in NULL or s.startswith("nan"):
        return None
    try:
        return round(float(s), 3)
    except ValueError:
        return None


# 每 100g 合理性上限（纯脂肪 ≈900 kcal，营养素分量 ≤100g）
LIMITS = {"calories_kcal": (0.0, 2000.0), "protein_g": (0.0, 100.0),
          "fat_g": (0.0, 100.0), "saturated_fat_g": (0.0, 100.0),
          "carbs_g": (0.0, 100.0), "fiber_g": (0.0, 100.0), "sugar_g": (0.0, 100.0),
          "sodium_mg": (0.0, 20000.0), "cholesterol_mg": (0.0, 2000.0),
          "vitamin_c_mg": (0.0, 5000.0), "calcium_mg": (0.0, 5000.0), "iron_mg": (0.0, 500.0)}
QC = Counter()


def clamper():
    """返回 (值, 是否异常)：越界返回 None 并计数。"""
    def c(key, v):
        if v is None:
            return None, False
        lo, hi = LIMITS[key]
        if v < lo or v > hi:
            QC[key] += 1
            return None, True
        return v, False
    return c


def flag(v):
    s = str(v).strip().lower()
    return s in ("true", "1", "yes")


# 统一 per_100g：数值转 float + 合理性钳制（越界置 None 计入 QC）
CORE_NUTRIENTS = ["calories_kcal", "protein_g", "fat_g", "saturated_fat_g", "carbs_g",
                  "fiber_g", "sugar_g", "sodium_mg", "cholesterol_mg",
                  "vitamin_c_mg", "calcium_mg", "iron_mg"]


def normalize_100g(raw):
    """raw: {core_key: 原csv列值}；返回每 100g 统一字典。"""
    c = clamper()
    return {k: c(k, fnum(v))[0] for k, v in raw.items()}

# 过敏原键（与三个 OFF 文件一致的布尔列）
ALLERGEN_KEYS = ["contains_gluten", "contains_dairy", "contains_nuts",
                 "contains_soy", "contains_eggs", "contains_fish"]


def main():
    usda = read_csv("comprehensive_foods_usda.csv")
    off_health = read_csv("foods_health_scores_allergens.csv")
    off_allergens = read_csv("foods_allergens.csv")
    off_diet = read_csv("foods_dietary_restrictions.csv")
    healthy = read_csv("healthy_foods_database.csv")

    # ---------- 1. OFF 侧：三个文件按 (product_name, brands) 合并 ----------
    off = {}  # key -> merged row
    for r in off_health + off_allergens + off_diet:
        name = (r.get("product_name") or "").strip()
        brands = (r.get("brands") or "").strip()
        key = (name.lower(), brands.lower())
        if not name:
            continue
        tgt = off.setdefault(key, dict(r))
        if tgt is r:   # 首次加入时初始化布尔为空字典的已有行复用
            tgt = dict(r)
            off[key] = tgt
        else:
            for k in r:
                if k not in tgt or not str(tgt[k] or "").strip():
                    tgt[k] = r[k]
        # 布尔标志取并集
        for k in r:
            if flag(r.get(k)) and k in ALLERGEN_KEYS:
                tgt[k] = "True"
    off_merged = [r for r in off.values() if (r.get("product_name") or "").strip()]

    # ---------- 1.5 能量单位校验（calories 列存在 kJ/kcal 混用） ----------
    # 判定：kcal 与宏量估算(4P+9F+4C)严重不符，但 kcal/4.184 与估算吻合 → 实为 kJ
    def energy_kcal(r):
        kcal = fnum(r.get("calories"))
        est_req = ("protein_g", "fat_g", "carbs_g")
        est = None
        vals = [fnum(r.get(k)) for k in est_req]
        if all(v is not None for v in vals):
            est = 4 * vals[0] + 9 * vals[1] + 4 * vals[2]
        if kcal is not None and est and abs(kcal - est) / est > 0.2 \
                and abs(kcal / 4.184 - est) / est <= 0.25:
            stats["kj_energy_fixed"] += 1
            return round(kcal / 4.184, 3)
        return kcal

    # ---------- 2. 主键与统一记录 ----------
    def off_id(name, brands):
        h = hashlib.md5(f"{name.strip().lower()}|{brands.strip().lower()}".encode()).hexdigest()[:8]
        return f"off_{h}"

    core, stats = [], Counter()
    for r in usda:
        name = (r.get("food_name") or "").strip()
        if not name:
            continue
        rec = {
            "food_id": f"fdc_{r['fdc_id'].strip()}",
            "name": name, "source": "usda",
            "category": r.get("food_category"), "food_type": r.get("food_type"),
            "data_type": r.get("data_type"),
            "per_100g": normalize_100g({
                "calories_kcal": energy_kcal(r), "protein_g": r.get("protein_g"),
                "fat_g": r.get("fat_g"), "saturated_fat_g": r.get("saturated_fat_g"),
                "carbs_g": r.get("carbs_g"), "fiber_g": r.get("fiber_g"),
                "sugar_g": r.get("sugar_g"), "sodium_mg": r.get("sodium_mg"),
                "cholesterol_mg": r.get("cholesterol_mg"),
                "vitamin_c_mg": r.get("vitamin_c_mg"),
                "calcium_mg": r.get("calcium_mg"), "iron_mg": r.get("iron_mg"),
            }),
            "serving": {"size": fnum(r.get("serving_size")),
                        "unit": r.get("serving_unit"), "household": r.get("household_serving")},
            "health_score": fnum(r.get("health_score")),
            "health_score_note": "作者自定义评分(0-100)，仅作参考",
            "brand": None, "ingredients": None,
            "allergens": None,
            "labels": {},
            "flags": {},
        }
        for k in ALLERGEN_KEYS:
            rec["flags"][k] = flag(r.get(k))
        core.append(rec)
        stats["usda"] += 1

    off_flags_by_key = {}
    for r in off_merged:
        key = (r.get("product_name", "").strip().lower(), r.get("brands", "").strip().lower())
        off_flags_by_key[key] = r

    for r in off_merged:
        name = (r.get("product_name") or "").strip()
        brands = (r.get("brands") or "").strip()
        rec = {
            "food_id": off_id(name, brands),
            "name": name, "source": "openfoodfacts",
            "category": r.get("categories"), "food_type": r.get("food_type"),
            "data_type": "branded",
            "per_100g": normalize_100g({
                "calories_kcal": r.get("energy_kcal"), "protein_g": r.get("proteins_100g"),
                "fat_g": r.get("fat_100g"),
                "saturated_fat_g": r.get("saturated_fat_100g"),
                "carbs_g": r.get("carbs_100g"), "fiber_g": r.get("fiber_100g"),
                "sugar_g": r.get("sugars_100g"), "sodium_mg": r.get("sodium_100g"),
                "cholesterol_mg": None, "vitamin_c_mg": None, "calcium_mg": None, "iron_mg": None,
            }),
            "serving": {"size": None, "unit": "100g", "household": None},
            "health_score": None, "health_score_note": None,
            "brand": brands or None, "ingredients": r.get("ingredients") or None,
            "allergens": (r.get("allergens") or None),
            "labels": {"nutriscore": (r.get("nutriscore_grade") or None),
                       "nova": (r.get("nova_group") or None),
                       "ecoscore": (r.get("ecoscore_grade") or None)},
            "flags": {k: flag(r.get(k)) for k in ALLERGEN_KEYS},
        }
        core.append(rec)
        stats["openfoodfacts"] += 1

    # ---------- 3. healthy 子集：按 food_name 挂到 USDA 条目，孤儿单列 ----------
    usda_by_name = {r["name"].strip().lower(): r for r in core if r["source"] == "usda"}
    orphan = 0
    for r in healthy:
        nm = (r.get("food_name") or "").strip()
        if not nm:
            continue
        tgt = usda_by_name.get(nm.lower())
        if tgt is not None:
            if tgt.get("health_score") is None:
                tgt["health_score"] = fnum(r.get("health_score"))
            continue
        orphan += 1
        core.append({
            "food_id": off_id(nm, "healthy"), "name": nm, "source": "healthy_subset",
            "category": None, "food_type": r.get("food_type"), "data_type": "sr legacy",
            "per_100g": normalize_100g({
                "calories_kcal": energy_kcal(r), "protein_g": r.get("protein_g"),
                "fat_g": r.get("fat_g"), "saturated_fat_g": None,
                "carbs_g": r.get("carbs_g"), "fiber_g": r.get("fiber_g"),
                "sugar_g": r.get("sugar_g"), "sodium_mg": r.get("sodium_mg"),
                "cholesterol_mg": None, "vitamin_c_mg": None, "calcium_mg": None, "iron_mg": None,
            }),
            "serving": {"size": None, "unit": "100g", "household": None},
            "health_score": fnum(r.get("health_score")),
            "health_score_note": "作者自定义评分(0-100)，仅作参考",
            "brand": None, "ingredients": None, "allergens": None,
            "labels": {}, "flags": {},
        })
        stats["healthy_subset"] += 1
    stats["healthy_orphan"] = orphan

    # ---------- 4. 去重与统计 ----------
    seen, dup = set(), 0
    final = []
    for r in core:
        if r["food_id"] in seen:
            dup += 1
            continue
        seen.add(r["food_id"])
        final.append(r)
    stats["total"] = len(final)
    stats["dup_removed"] = dup

    with open(os.path.join(DATA, "data", "foods_core.json"), "w", encoding="utf-8") as f:
        json.dump({"version": "1.0",
                   "source": "Kaggle: Global Food & Nutrition Database 2026 (CC BY-SA 4.0)",
                   "unit": "per 100g",
                   "note": "health_score 为作者自定义评分，仅作参考；ID 规则 fdc_*/off_*；"
                           "OFF 条目无稳定 id，off_* 由 name|brands 哈希生成",
                   "foods": final}, f, ensure_ascii=False, indent=1)

    print("== 构建统计 ==")
    for k in ("usda", "openfoodfacts", "healthy_subset", "healthy_orphan",
              "total", "dup_removed", "kj_energy_fixed"):
        print(f"  {k}: {stats[k]}")
    # 质量抽查
    hs = [r["health_score"] for r in final if r["health_score"] is not None]
    print(f"  health_score 覆盖: {len(hs)} 条, 范围 {min(hs)}-{max(hs)}")
    kcal = [r["per_100g"]["calories_kcal"] for r in final if r["per_100g"]["calories_kcal"]]
    print(f"  热量有效: {len(kcal)}/{len(final)}, max(钳制后) {max(kcal)}")
    if QC:
        print(f"  QC 钳制掉异常值: {dict(QC)}")


if __name__ == "__main__":
    main()