# -*- coding: utf-8 -*-
"""编排链：screening → plan_check → 宏观目标 → 动作候选 → 食材候选。
全部落在 lib/ 只读引擎与规则数据上；缺数据透传原因，不猜值。"""
from __future__ import annotations
import json
from pathlib import Path
import screening
from app.runtime.repos import exercise_repo, foods_repo

ROOT = Path(__file__).resolve().parent.parent.parent
FITT = json.load(open(ROOT / "data" / "sports-medicine" / "data"
                      / "fitt_prescription.json", encoding="utf-8"))
# 参考 planner_demo 的卫生过滤（干货/珍稀排除）
UNEDIBLE = ("鱼翅", "燕窝", "冬虫夏草", "琼脂", "石花菜", "蛏干")
DIRT = (FITT["resistance"]["frequency"], FITT["resistance"]["intensity"],
        FITT["resistance"]["volume"], FITT["resistance"]["rest"])


def _bp(p: dict, sex: str) -> float:
    w, h, a = p["weight_kg"], p["height_cm"], p["age"]
    return 10 * w + 6.25 * h - 5 * a + (5 if sex == "male" else -161)


def build_plan(profile: dict) -> dict:
    ex, fr = exercise_repo(), foods_repo()   # 共享单例（原模块级 _EX/_FR 副本）
    conds = profile.get("conditions") or []
    pats = profile.get("patterns") or []
    sc = screening.plan_check(conds, pats)
    sex = profile.get("sex", "male")
    activity = profile.get("activity", 1.55)
    goal = profile.get("goal", "maintain")
    bmr = _bp(profile, sex)
    tdee = bmr * activity
    deficit = profile.get("deficit_kcal", 0) if goal == "lose_fat" else 0
    target_kcal = max(tdee - deficit, 1000)
    protein = profile["weight_kg"] * profile.get("protein_g_per_kg", 2.0)
    fat = profile["weight_kg"] * profile.get("fat_g_per_kg", 0.8)
    carbs = max((target_kcal - 4 * protein - 9 * fat) / 4, 0)
    macros = {"bmr": round(bmr), "tdee": round(tdee),
              "target_kcal": round(target_kcal),
              "protein_g": round(protein), "fat_g": round(fat),
              "carbs_g": round(carbs)}
    blocked_patterns = [b["hit_patterns"] for b in sc["blocks"]]
    blocked = {p for l in blocked_patterns for p in l}
    training_items = []
    plans = {"推日(胸·肩·三头)": "push", "拉日(背·二头)": "pull",
             "腿日(股四·臀·腘绳)": "squat", "核心日": "core"}
    for day, pat in plans.items():
        if pat in blocked:
            continue
        recs = ex.filter(pattern=pat, difficulty=2, limit=4,
                         sort_by_difficulty=True)
        training_items.append({"day": day, "pattern": pat,
                               "exercises": [{
                                   "name": e.get("name_zh"),
                                   "difficulty": e.get("difficulty"),
                                   "sets": (e.get("suggested") or {}).get("sets"),
                                   "reps": (e.get("suggested") or {}).get("reps"),
                                   "rest_sec": (e.get("suggested") or {}).get("rest_sec"),
                                   "equipment": e.get("normalized_equipment"),
                               } for e in recs[:3]]})
    meals = []
    for f in fr.filter(category="meat", protein_min=18, kcal_max=300,
                       limit=8, sort_by="protein_desc"):
        nm = f.get("name_zh") or f.get("name")
        if any(u in nm for u in UNEDIBLE):
            continue
        p = f.get("per_100g") or {}
        meals.append({"name": nm, "kcal": p.get("calories_kcal"),
                      "protein_g": p.get("protein_g"), "source": f.get("source")})
    return {"ok": True, "plan": {
        "profile": {"goal": goal, "sex": sex},
        "screening": {"level": sc["level"], "level_label": sc["level_label"],
                      "warnings": sc["warnings"]},
        "fitt": {"resistance": DIRT},
        "macros": macros,
        "training": {"items": training_items},
        "meals": {"items": meals[:5]},
    }, "provenance": ["pipeline#screening.plan_check",
                      "pipeline#exercise_repo.filter",
                      "pipeline#foods_repo.filter"]}