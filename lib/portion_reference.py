# -*- coding: utf-8 -*-
"""
portion_reference.py — 参考份量表（业务层兜底）
==============================================
基础生鲜（鸡蛋/牛奶/面包等 Foundation 条目）无官方 serving，
用本表按常识给参考份量。返回的 method='reference'，与官方值严格区分。

匹配顺序：
  ① 条目家族 stem 精确匹配（food_families 的 stem）
  ② 名称首段核心词（逗号前，去复数/去制备态修饰）精确匹配
  ③ food_type 类目兜底（估算）
未命中返回 None（调用方决定如何处理，不猜）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "nutrition-dataset" / "data"

_REF = json.load(open(DATA_DIR / "portion_reference.json", encoding="utf-8"))
ENTRIES = {e["key"]: e for e in _REF["entries"]}
CATEGORY_DEFAULTS = _REF["category_defaults"]

# 名称首段可能携带的制备词/修饰（"egg, whole, raw" → 首段取 "egg"）
_HEAD_STRIP = ["raw", "cooked", "fresh", "whole", "large", "medium", "small",
               "frozen", "canned", "dried", "raw fresh", "whole raw"]


def _plural_candidates(w: str) -> list[str]:
    """给定单词，生成可能的单数候选（ies→y, oes→o, 常规 s）。"""
    out = [w]
    if len(w) > 4:
        if w.endswith("ies"):
            out.append(w[:-3] + "y")
        elif w.endswith("oes"):
            out.append(w[:-2])
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        out.append(w[:-1])
    return out


def _head_candidates(name: str) -> list[str]:
    """名称首段候选：逗号前 / 首个单词，各附复数规约。"""
    head = re.split(r"[,()/]", name)[0].strip().lower()
    cands = []
    for tok in {head, head.split()[0] if " " in head else head}:
        tok = re.sub(r"\s*\b(raw|cooked|fresh|whole|large|medium|small|frozen|"
                     r"dried|canned|skinless|boneless)\b\s*$", " ", tok).strip()
        cands += _plural_candidates(tok)
    return list(dict.fromkeys(c for c in cands if c))


def lookup_reference(name: str, food_type: str | None, family_stem: str | None = None
                     ) -> dict | None:
    """按匹配顺序查参考份量。返回 {portion_g, note, matched_by} 或 None。"""
    # ① 族 stem 精确
    if family_stem and family_stem in ENTRIES:
        e = ENTRIES[family_stem]
        return {"portion_g": e["portion_g"], "note": e["note"], "matched_by": "family_stem"}
    # ② 名称首段候选（含复数规约/制备词剥离）
    for c in _head_candidates(name):
        if c in ENTRIES:
            e = ENTRIES[c]
            return {"portion_g": e["portion_g"], "note": e["note"], "matched_by": f"head:{c}"}
    # ③ food_type 类目
    if food_type:
        ft = food_type.lower()
        for d in CATEGORY_DEFAULTS:
            if any(k in ft for k in d["match"]):
                return {"portion_g": d["portion_g"], "note": d["note"],
                        "matched_by": f"category:{ft}"}
    return None


if __name__ == "__main__":
    for n, ft in [("Egg, whole, raw, fresh", "Dairy and Egg Products"),
                  ("Apples, raw, with skin", "Fruits and Fruit Juices"),
                  ("Bananas, raw", "Fruits and Fruit Juices"),
                  ("Potatoes, flesh and skin, baked", "Vegetables and Vegetable Products"),
                  ("Milk, whole, 3.25% milkfat, with added vitamin D", "Dairy and Egg Products"),
                  ("Bread, white, commercially prepared", "Baked Products"),
                  ("Almonds, raw", "Nut and Seed Products")]:
        r = lookup_reference(n, ft)
        print(f"{n[:42]:44s} -> {r['portion_g']}g ({r['matched_by']})" if r else f"{n[:42]:44s} -> None")