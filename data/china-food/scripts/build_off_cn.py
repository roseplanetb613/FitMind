# -*- coding: utf-8 -*-
"""
build_off_cn.py — Open Food Facts 中国区产品（source='off_cn'）
==============================================================
从 OFF 官方 API 拉取 countries_tags_en=china 的全量产品并归一：
  - 许可：ODbL（开放数据，商用需署名+共享衍生，见 docs）
  - 每 100g；sodium 单位 g→mg；过敏原 tags 推 6 项布尔
  - 原始响应保留在 raw/off_cn_raw/ 便于审计
用法：python scripts/build_off_cn.py （需联网）
"""
import json
import re
import subprocess
import time
import urllib.parse
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
RAW_DIR = PKG / "raw" / "off_cn_raw"
OUT = PKG / "data" / "off_cn.json"
API = "https://world.openfoodfacts.org/api/v2/search"
UA = "FitMind-Research/0.1 (academic prototype; contact: research@fitmind.local)"
PAGE_SIZE = 100
MAX_RETRY = 5

FIELDS = "code,product_name,brands,categories_tags,nutriments,allergens_tags," \
         "nutriscore_grade,nova_group,ingredients_text,countries_tags,product_quantity"

ALLERGEN_MAP = [  # (en 标签关键词, flags 键)
    ("gluten", "contains_gluten"), ("milk", "contains_dairy"),
    ("nut", "contains_nuts"), ("soy", "contains_soy"),
    ("egg", "contains_eggs"), ("fish", "contains_fish"),
]


def fetch_page(page: int) -> list:
    qs = urllib.parse.urlencode({
        "countries_tags_en": "china", "page_size": PAGE_SIZE, "page": page,
        "fields": FIELDS,
    })
    url = f"{API}?{qs}"
    for attempt in range(1, MAX_RETRY + 1):
        try:
            r = subprocess.run(
                ["curl.exe", "-sL", "--max-time", "120", "-A", UA, url],
                capture_output=True, text=True, encoding="utf-8",
                timeout=150, check=True)
            data = json.loads(r.stdout)
            return data.get("products", []), data.get("count", 0)
        except Exception as exc:
            if attempt == MAX_RETRY:
                raise RuntimeError(f"page {page} 拉取失败: {exc}") from exc
            wait = 3 * attempt
            print(f"  retry page {page} ({attempt}/{MAX_RETRY}) 等待 {wait}s: {type(exc).__name__}")
            time.sleep(wait)
    return [], 0


def fnum(v):
    if v is None or v == "":
        return None
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return None


def load_raw_pages():
    """读取已存在的原始页文件（断点续传）。"""
    pages = {}
    for fp in sorted(RAW_DIR.glob("page_*.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            continue
        if items:
            pages[int(fp.stem.split("_")[1])] = items
    return pages


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    pages = load_raw_pages()
    total = None
    page = max(pages) + 1 if pages else 1
    while True:
        if total is None:
            # 先取一次条数（复用现有页或拉第 1 页）
            try:
                _, total = fetch_page(1)
            except Exception as exc:
                print(f"获取总数失败: {exc}")
                total = len(pages) * PAGE_SIZE
        try:
            items, count = fetch_page(page)
            total = count
        except Exception as exc:
            print(f"⚠️ page {page} 拉取失败（{exc}），使用已有 {len(pages)} 页数据继续")
            break
        with open(RAW_DIR / f"page_{page:03d}.json", "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
        pages[page] = items
        print(f"page {page}: +{len(items)} (累计 {sum(len(v) for v in pages.values())}/{total})")
        if len(pages) >= (total + PAGE_SIZE - 1) // PAGE_SIZE or not items:
            break
        page += 1
        time.sleep(0.8)

    products = [p for v in pages.values() for p in v]

    foods = []
    for p in products:
        code = str(p.get("code") or p.get("_id") or "").strip()
        name = (p.get("product_name") or "").strip()
        if not name:
            continue
        n = p.get("nutriments") or {}
        sat = n.get("saturated-fat_100g")
        sodium_g = n.get("sodium_100g")
        if sodium_g is None:
            salt_g = n.get("salt_100g")
            sodium_g = salt_g * 0.393 if salt_g is not None else None
        allergens = [t for t in (p.get("allergens_tags") or [])]
        flags = {k: False for _, k in ALLERGEN_MAP}
        for tag in allergens:
            for kw, key in ALLERGEN_MAP:
                if kw in tag:
                    flags[key] = True
        foods.append({
            "food_id": f"offcn_{code}",
            "name": name,
            "name_zh": name,
            "source": "off_cn",
            "category": None,
            "food_type": next((c for c in (p.get("categories_tags") or []) if c), None),
            "data_type": "branded",
            "per_100g": {
                "calories_kcal": (lambda v: v if v is not None and 0 < v <= 2000 else None)(
                    fnum(n.get("energy-kcal_100g") or n.get("energy_100g"))),
                "protein_g": fnum(n.get("proteins_100g")),
                "fat_g": fnum(n.get("fat_100g")),
                "saturated_fat_g": fnum(sat),
                "carbs_g": fnum(n.get("carbohydrates_100g")),
                "fiber_g": fnum(n.get("fiber_100g")),
                "sugar_g": fnum(n.get("sugars_100g")),
                "sodium_mg": fnum(sodium_g * 1000) if sodium_g is not None else None,
                "cholesterol_mg": None, "vitamin_c_mg": None,
                "calcium_mg": None, "iron_mg": None,
            },
            "serving": {"size": None, "unit": "100g", "household": None},
            "health_score": None, "health_score_note": None,
            "brand": p.get("brands") or None,
            "ingredients": (p.get("ingredients_text") or None),
            "allergens": ", ".join(allergens) if allergens else None,
            "labels": {"nutriscore": p.get("nutriscore_grade"),
                       "nova": str(p.get("nova_group")) if p.get("nova_group") else None,
                       "ecoscore": None},
            "flags": flags,
        })

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"version": "1.0", "source": "off_cn",
                   "origin": "Open Food Facts 官方 API (countries_tags_en=china), ODbL 许可",
                   "unit": "per 100g", "foods": foods}, f, ensure_ascii=False, indent=1)
    print(f"归一完成: {len(foods)} 条 -> {OUT}")


if __name__ == "__main__":
    main()