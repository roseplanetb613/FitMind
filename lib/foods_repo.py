# -*- coding: utf-8 -*-
"""
foods_repo.py — 食物检索/过滤层
================================
统一加载 foods_core.json + name_zh.json + food_families.json，提供：
  - get(id)           完整记录（含中文名、族信息）
  - search(text)      中/英文名子串搜索
  - filter(...)       组合过滤：营养素区间 / 过敏原排除 / 健康评分 / 标签
  - family / siblings 同源族与替换建议
  - serving_grams / nutrition_for_serving  食用份换算（lib/serving_units.py）
设计对齐 lib/exercise_repo.py；纯只读、无第三方依赖。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from serving_units import serving_grams
from portion_reference import lookup_reference

# 项目根 = e:\FitMind（lib/ 顶层）；所有数据集集中在 data/ 下
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "nutrition-dataset" / "data"

ALLERGEN_KEYS = ["contains_gluten", "contains_dairy", "contains_nuts",
                 "contains_soy", "contains_eggs", "contains_fish"]


class FoodsRepo:
    def __init__(self, data_dir: Path = DATA_DIR,
                 china_dir: Optional[Path] = None):
        """
        data_dir   营养核心集目录（USDA+OFF）
        china_dir  中国食物成分表核心集目录（source='china'），默认自动探测
        """
        self.data_dir = Path(data_dir)
        if china_dir is None:
            china_dir = Path(__file__).resolve().parent.parent / "data" \
                / "china-food" / "data"
        self.china_dir = Path(china_dir)

        def _read(name):
            with open(Path(name), encoding="utf-8") as f:
                return json.load(f)

        core = _read(self.data_dir / "foods_core.json")
        self.by_id = {f["food_id"]: f for f in core["foods"]}
        # 中国数据源（存在即合并）：食物成分表 + OFF 中国区
        for src_file in ("foods_china.json", "off_cn.json"):
            src = self.china_dir / src_file
            if src.exists():
                for f in _read(src)["foods"]:
                    self.by_id[f["food_id"]] = f
        self.name_zh = _read(self.data_dir / "name_zh.json")
        fam = _read(self.data_dir / "food_families.json")
        self.families = {f["family_id"]: f for f in fam.get("families", [])}
        # 统一大类映射（跨源类目对齐）
        self.categories = []
        cat_path = self.data_dir / "food_categories.json"
        if cat_path.exists():
            self.categories = _read(cat_path)["categories"]
        # 成员索引: food_id -> family_id
        self.member_family = {}
        for fid, famobj in self.families.items():
            for m in famobj["members"]:
                self.member_family[m["food_id"]] = fid
        # 注入记录级扩展字段
        for rid, r in self.by_id.items():
            r["name_zh"] = r.get("name_zh") or self.name_zh.get(
                r["name"].strip().lower(), r["name"])
            r["family"] = self.member_family.get(rid)
            r["family_name"] = (self.families[r["family"]]["stem"]
                                if r["family"] else None)

    # ------------------------------------------------------------ 基础查询
    def get(self, food_id: str):
        return self.by_id.get(food_id)

    def category_unified(self, food: dict | str) -> str:
        """把任意源食物的类目归一到统一大类 id（fruits/meat/…）。"""
        if isinstance(food, str):
            food = self.by_id.get(food)
            if not food:
                return "other"
        text = " ".join(filter(None, [
            (food.get("name") or ""),
            (food.get("food_type") or ""),
            (food.get("category") or "")])).lower()
        for c in self.categories:
            if any(k in text for k in c["match"]):
                return c["id"]
        return "other"

    @staticmethod
    def _score(text: str, query: str) -> int:
        """搜索打分（0-5）：
        中文: 5相等 4前缀 3后缀 2子串
        英文: 5相等 4前缀 3全token命中 2子串
        """
        t = text.strip().lower()
        q = query.strip().lower()
        if not q:
            return 0
        if t == q:
            return 5
        if re.search(r"[\u4e00-\u9fff]", q):
            if t.startswith(q):
                return 4
            if t.endswith(q):
                return 3
            return 2 if q in t else 0
        if t.startswith(q):
            return 4
        # 逗号归一后前缀（"chicken, breast, raw" 对查询 "chicken breast"）
        if t.startswith(q) or re.sub(r"[,\s]+", " ", t).startswith(q):
            return 4
        qtoks = [x for x in re.split(r"[^a-z0-9]+", q) if x]
        if qtoks:
            ntoks = set(re.findall(r"[a-z0-9]+", t))
            if all(x in ntoks for x in qtoks):
                return 3
        return 2 if q in t else 0

    def search(self, text: str, limit: Optional[int] = None,
               zh_only: bool = False, score_floor: int = 1) -> list[dict]:
        """
        中/英文名搜索（评分排序，不返回浅拷贝污染共享记录）。
        中文：精确名 > 前缀（苹果汁） > 后缀（生苹果） > 子串（苹果味）
        英文：精确名 > 全 token 命中（chicken breast→两词均在） > 前缀 > 子串
        score_floor=1 只保留有匹配的；置 0 返回全部评分。
        """
        t = text.strip().lower()
        if not t:
            return []
        scored = []
        for r in self.by_id.values():
            s_en = self._score(r["name"], t) if not zh_only else 0
            s_zh = self._score(r["name_zh"], t)
            s = max(s_en, s_zh)
            if s >= score_floor:
                rec = dict(r)
                rec["_score"] = s
                rec["_score_en"], rec["_score_zh"] = s_en, s_zh
                scored.append(rec)
        # 同分按 名长短升序（泛指核心名更短靠前）+ food_id 稳定
        scored.sort(key=lambda x: (-x["_score"], len(x["name_zh"]), x["food_id"]))
        if limit:
            scored = scored[:limit]
        return scored

    # ------------------------------------------------------------ 组合过滤
    def filter(self, *,
               source: Optional[str] = None,
               food_type: Optional[str] = None,
               category: Optional[str] = None,
               food_id_in: Optional[Iterable[str]] = None,
               kcal_min: Optional[float] = None, kcal_max: Optional[float] = None,
               protein_min: Optional[float] = None, protein_max: Optional[float] = None,
               fat_max: Optional[float] = None, carbs_max: Optional[float] = None,
               sugar_max: Optional[float] = None, sodium_max: Optional[float] = None,
               fiber_min: Optional[float] = None,
               health_min: Optional[float] = None,
               nutriscore: Optional[Iterable[str]] = None,
               nova: Optional[Iterable[str]] = None,
               allergen_free: Optional[Iterable[str]] = None,
               limit: Optional[int] = None,
               sort_by: Optional[str] = None) -> list[dict]:
        """组合过滤，条件间 AND。allergen_free 传 'contains_gluten' 等键，要求为 False。"""
        ft = food_type.lower() if food_type else None
        id_ok = set(food_id_in) if food_id_in else None
        ns = set(nutriscore) if nutriscore else None
        nv = set(nova) if nova else None
        af = {k.removeprefix("contains_"): True for k in (allergen_free or [])}

        out = []
        for r in self.by_id.values():
            if source is not None and r["source"] != source:
                continue
            if category is not None and self.category_unified(r) != category:
                continue
            if ft and r.get("food_type") and ft not in r["food_type"].lower():
                continue
            if id_ok is not None and r["food_id"] not in id_ok:
                continue
            p = r["per_100g"]
            v = p.get("calories_kcal")
            if kcal_min is not None and (v is None or v < kcal_min):
                continue
            if kcal_max is not None and (v is None or v > kcal_max):
                continue
            if protein_min is not None and (p.get("protein_g") is None or p["protein_g"] < protein_min):
                continue
            if protein_max is not None and (p.get("protein_g") is None or p["protein_g"] > protein_max):
                continue
            for key, cap in (("fat_g", fat_max), ("carbs_g", carbs_max),
                             ("sugar_g", sugar_max), ("sodium_mg", sodium_max)):
                if cap is not None and (p.get(key) is None or p[key] > cap):
                    break
            else:
                if fiber_min is not None and (p.get("fiber_g") is None or p["fiber_g"] < fiber_min):
                    continue
                if health_min is not None and (r.get("health_score") is None or r["health_score"] < health_min):
                    continue
                if ns is not None and (r.get("labels") or {}).get("nutriscore") not in ns:
                    continue
                if nv is not None and (r.get("labels") or {}).get("nova") not in nv:
                    continue
                if af and any(r["flags"].get("contains_" + k) for k in af):
                    continue
                out.append(r)

        if sort_by:
            def keyof(r):
                if sort_by in ("protein_desc", "-protein"):
                    return -(r["per_100g"]["protein_g"] or 0)
                if sort_by in ("kcal_asc", "calories"):
                    return r["per_100g"]["calories_kcal"] or 0
                if sort_by == "health_desc":
                    return -(r.get("health_score") or 0)
                return 0
            out.sort(key=keyof)
        return out[:limit] if limit else out

    # ------------------------------------------------------------ 族/替换
    def family(self, family_id: str):
        return self.families.get(family_id)

    def siblings(self, food_id: str) -> list[dict]:
        """同源族内其他条目（含制备态，营养不作等价，仅供替换参考）。"""
        fid = self.member_family.get(food_id)
        if not fid:
            return []
        return [m for m in self.families[fid]["members"] if m["food_id"] != food_id]

    def by_prep(self, food_id: str) -> Optional[str]:
        """返回某条目在其族内的制备态标签。"""
        fid = self.member_family.get(food_id)
        if not fid:
            return None
        for m in self.families[fid]["members"]:
            if m["food_id"] == food_id:
                return m["prep_state"]
        return None

    # --------------------------------------------------------- 食用份换算
    def serving_grams(self, food_id: str) -> dict:
        """
        返回该食物的「一份」克数。
        优先级：官方 serving（direct/density/household）→ 参考份量表（reference）→ None。
        """
        r = self.by_id.get(food_id)
        if not r:
            return {"grams": None, "method": "unknown", "note": "food_id 不存在"}
        res = serving_grams(r.get("serving"), r.get("food_type"))
        if res["grams"] is not None:
            return res
        ref = lookup_reference(r["name"], r.get("food_type"), r.get("family_name"))
        if ref:
            return {"grams": ref["portion_g"], "method": "reference",
                    "note": f"{ref['note']}（参考份量，{ref['matched_by']}）"}
        return res

    def nutrition_for_serving(self, food_id: str, servings: float = 1.0) -> dict:
        """
        按份计算营养：100g 值 × 份数 × 单份克数。
        返回 {food_id, name, name_zh, grams, per_serving:{...}, serving_note}。
        """
        r = self.by_id.get(food_id)
        if not r:
            return {"error": "food_id 不存在"}
        sv = self.serving_grams(food_id)
        g = sv["grams"]
        if g is None:
            return {"food_id": food_id, "name": r["name"], "name_zh": r["name_zh"],
                    "grams": None, "error": f"无法换算食用份：{sv['note']}",
                    "per_serving": None, "serving_note": sv["note"]}
        total_g = round(g * servings, 2)
        p = r["per_100g"]
        scaled = {k: (round(v * total_g / 100, 2) if v is not None else None)
                  for k, v in p.items()}
        return {"food_id": food_id, "name": r["name"], "name_zh": r["name_zh"],
                "grams": total_g, "per_serving": scaled,
                "serving_note": sv["note"], "servings": servings}


def build() -> FoodsRepo:
    return FoodsRepo()


if __name__ == "__main__":
    repo = build()
    print(f"加载 {len(repo.by_id)} 食物、{len(repo.families)} 族、"
          f"{repo.name_zh.__len__()} 条中文名")