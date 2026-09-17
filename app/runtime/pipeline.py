# -*- coding: utf-8 -*-
"""编排链：screening → plan_check → 宏观目标 → 动作候选 → 食材候选。
全部落在 lib/ 只读引擎与规则数据上；缺数据透传原因，不猜值。"""
from __future__ import annotations
import json
import re
from datetime import date
from pathlib import Path
import screening
import split_cycle
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


# 休息日提示语单源在 lib/split_cycle（计划编辑也要写同一句，不能各写一份漂移）
_REST_NOTE = split_cycle.REST_NOTE


def _reps_mid(reps) -> int:
    """'5-12'/'8' → 目标次数中值（progression.evaluate 入参）。"""
    nums = [int(x) for x in re.findall(r"\d+", str(reps or ""))]
    if not nums:
        return 8
    return max(1, round(sum(nums[:2]) / min(len(nums), 2)))


def _annotate_progression(exercises: list[dict], history) -> None:
    """逐动作回哺（spec §6）：记录原词 search_zh 归一 → 与计划动作标准名匹配
    → progression.evaluate 挂建议。无记录/归一失败/任何异常 → 不挂字段
    （宁缺毋滥，主计划不受影响）。

    `history` 是 `app/runtime/history.WorkoutHistory`（读**图谱**）。此前收
    `LogStore` 读 SQLite `workout_set`，而那张表没有生产写入方 → 本函数
    **永远**走"不挂字段"分支（见 docs/SDD/user-data-domain.md §6-F1）。"""
    import progression as prog
    try:
        recorded = history.exercise_names()
    except Exception:
        return
    norm_map: dict = {}
    for raw in recorded:
        try:
            hits = exercise_repo().search_zh(raw, limit=1)
            norm_map[raw] = (hits[0].get("name_zh") if hits else None) or raw
        except Exception:
            norm_map[raw] = raw
    for e in exercises:
        try:
            name = e.get("name")
            src = next((r for r, std in norm_map.items()
                        if std == name or r == name), None)
            if src is None:
                continue
            rows = history.history(src, top=3)
            if not rows:
                continue
            hist = prog.ExerciseHistory(name, [prog.SetRecord(
                float(r.get("weight_kg") or 0), int(r.get("reps") or 0),
                float(r.get("rir") or 0), str(r.get("date") or ""))
                for r in rows])
            rep = prog.evaluate(hist, _reps_mid(e.get("reps")), 2)
            adv = rep.get("advice") or {}
            if adv.get("reason") == "no history":
                continue
            e["progression"] = {"status": rep.get("status"),
                                "weight_kg": adv.get("weight_kg"),
                                "reason": adv.get("reason")}
        except Exception:
            continue


def build_plan(profile: dict, prefs: dict | None = None,
               fatigue: set | None = None,
               extra_blocked: set | None = None,
               scheme: dict | None = None, days: int | None = None,
               history=None, fatigue_sources: list | None = None) -> dict:
    """fatigue_sources：疲劳信号的出处 [{date,term,pattern}]（plan_skill 单源产生）。
    缺省 None → 不落键，输出与注入前逐字一致。

    history：训练记录来源（`app/runtime/history.WorkoutHistory`，需有
    `exercise_names()` / `history()` 两个方法）。**2026-09-17 由 `log_store` 改名** ——
    它原先收 `LogStore`，而 `LogStore` 的 `workout_set` 没有生产写入方，
    进阶回哺恒拿空历史（见 docs/SDD/user-data-domain.md §6-F1）。"""
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
    if extra_blocked:
        blocked |= set(extra_blocked)      # 伤痛联动：记忆 injury → 禁忌模式封堵
    scheme = scheme or split_cycle.default_scheme()
    _today = date.today()
    today_iso = _today.isoformat()          # 日期基准：落 plan["start_date"]（见下）
    try:
        skeleton = split_cycle.expand(scheme, days, _today)
    except ValueError:
        scheme = split_cycle.default_scheme()
        skeleton = split_cycle.expand(scheme, None, _today)
    training_items = []
    applied = []
    blocked_notes = []
    train_cnt = 0
    for slot in skeleton:
        if slot["type"] == "rest":
            training_items.append({"day": slot["label"], "date": slot["date"],
                                   "type": "rest", "exercises": [],
                                   "note": slot.get("note") or _REST_NOTE})
            continue
        pats = [slot["pattern"], *(slot.get("extra_patterns") or [])]
        if any(p in blocked for p in pats):
            # 封堵日 → 休息日：循环相位/日期不动（spec §5.2）
            training_items.append({"day": f"休息日（原：{slot['label']}）",
                                   "date": slot["date"], "type": "rest",
                                   "exercises": [], "note": _REST_NOTE,
                                   "blocked_from": slot["label"]})
            blocked_notes.append(f"{slot['label']}因身体筛查改为休息日")
            continue
        train_cnt += 1
        pat = slot["pattern"]
        recs = ex.filter(pattern=pat, difficulty=2, limit=4,
                         sort_by_difficulty=True)
        if prefs:
            # 不喜欢动作剔除；全剔光 → 保留原候选（安全回落，不出空计划）
            kept = [e for e in recs
                    if e.get("name_zh") not in prefs.get("dislike_ex", ())]
            if kept:
                recs = kept
        focused = bool(prefs and pat in prefs.get("pats_like", ()))
        deload = bool(fatigue and pat in fatigue)
        n = 4 if focused else (2 if deload else 3)
        item = {"day": slot["label"], "date": slot["date"], "type": "train",
                "pattern": pat,
                "exercises": [{
                    "name": e.get("name_zh"),
                    "difficulty": e.get("difficulty"),
                    "sets": (e.get("suggested") or {}).get("sets"),
                    "reps": (e.get("suggested") or {}).get("reps"),
                    "rest_sec": (e.get("suggested") or {}).get("rest_sec"),
                    "equipment": e.get("normalized_equipment"),
                } for e in recs[:n]]}
        if focused:
            item["focused"] = True
            applied.append(f"{slot['label']}加量（偏好）")
        if deload:
            item["deload"] = True
        if history is not None:
            _annotate_progression(item["exercises"], history)
        training_items.append(item)
    if train_cnt == 0:
        return {"ok": False,
                "error": "当前身体状况不建议安排训练：全部训练日因筛查封堵改为休息",
                "provenance": ["pipeline#screening.plan_check"]}
    meals = []
    for f in fr.filter(category="meat", protein_min=18, kcal_max=300,
                       limit=8, sort_by="protein_desc"):
        nm = f.get("name_zh") or f.get("name")
        if any(u in nm for u in UNEDIBLE):
            continue
        p = f.get("per_100g") or {}
        meals.append({"name": nm, "kcal": p.get("calories_kcal"),
                      "protein_g": p.get("protein_g"), "source": f.get("source")})
    if prefs:
        # 食物联动：不喜欢剔除（剔空保留原候选）；喜欢置顶（稳定排序）
        dl = prefs.get("dislike_foods", ())
        if dl:
            kept_meals = [f for f in meals if f["name"] not in dl]
            if kept_meals:
                meals = kept_meals
        lf = prefs.get("like_foods", ())
        if lf:
            meals.sort(key=lambda f: f["name"] not in lf)
    plan = {
        "profile": {"goal": goal, "sex": sex},
        "screening": {"level": sc["level"], "level_label": sc["level_label"],
                      "warnings": sc["warnings"]},
        "fitt": {"resistance": DIRT},
        "macros": macros,
        "training": {"scheme": scheme.get("name_zh"), "items": training_items},
        "meals": {"items": meals[:5]},
    }
    # 日期基准（2026-09-11）：items[].date 是"今天/明天/具体日期"的**生成时快照**，
    # 读回时不重算就会把昨天说成今天（实测隔日读回锚点整体倒退一天）。落 start_date
    # 作为唯一基准，供 split_cycle.reanchor 在读路径与平移时重算。
    plan["start_date"] = today_iso
    if applied:
        plan["preferences_applied"] = applied   # 偏好应用痕迹（render 可念出）
    if blocked_notes:
        plan["blocked_note"] = "；".join(blocked_notes)
    hit_days = [i["day"] for i in training_items if i.get("deload")]
    if hit_days:
        # 归因可追溯（2026-09-11）：断言必须带出处——此前 fatigue_note 是脱离证据的
        # 概括句，用户追问"练的哪里"时系统答不上来（落动作库检索 → 没找到）。
        note = f"近48小时练过{'、'.join(hit_days)}对应部位，已自动减量"
        if fatigue_sources:
            plan["fatigue_sources"] = list(fatigue_sources)
            seg = "、".join(f"{s.get('date', '')}「{s.get('term', '')}」"
                            for s in fatigue_sources[:3])
            note += f"（依据：{seg}）"
        plan["fatigue_note"] = note
    return {"ok": True, "plan": plan,
            "provenance": ["pipeline#screening.plan_check",
                           "pipeline#exercise_repo.filter",
                           "pipeline#foods_repo.filter"]}
