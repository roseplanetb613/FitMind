# -*- coding: utf-8 -*-
"""计划生成：调用 runtime.pipeline 编排链。
产出计划即登记记忆图谱 PlanVersion（采纳闭环前置——个人记忆 spec v0.3 §2）。"""
from __future__ import annotations
import hashlib
import json
import re
import uuid
from app.skills.base import Skill, SkillResult
from app.runtime.pipeline import build_plan

# T5 计划指令控制："把X换成Y" / "去掉X"
_EDIT_REPLACE_RE = re.compile(r"(?:把)?(.+?)(?:换成|换掉|改成做?)(.+)")
_EDIT_REMOVE_RE = re.compile(r"(?:去掉|删掉|不要练?)(.+)")


def _register_plan(plan: dict, user_id: str) -> None:
    """计划产出 → 图谱 PlanVersion（静默：图谱不可用/失败不影响计划主链路）。"""
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return
        content_hash = hashlib.sha1(
            json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:16]
        m.register_plan(user_id, f"plan-{uuid.uuid4().hex[:8]}",
                        content_hash, content=plan)
    except Exception:
        pass


# N-8 偏好消费：部位→pattern 映射（仅在 screening 放行空间内加权，绝不解禁）
_PART2PAT = {"胸": "push", "肩": "push", "背": "pull", "臂": "pull",
             "腿": "squat", "臀": "squat", "腹": "core", "核心": "core"}


def _load_prefs(ctx) -> dict | None:
    """读记忆偏好 → {pats_like, dislike_ex, like_foods, dislike_foods}；
    无图/无偏好/异常 → None。"""
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return None
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        pats_like, dislike_ex = set(), set()
        like_foods, dislike_foods = set(), set()
        for r in m.current(uid, "preference"):
            about = str(r.get("about") or "")
            val = str(r.get("value") or "")
            if about.startswith("部位:"):
                if val == "喜欢":
                    pat = _PART2PAT.get(about[3:])
                    if pat:
                        pats_like.add(pat)
            elif about.startswith("食物:"):
                fname = about[3:]
                if val == "喜欢":
                    like_foods.add(fname)
                elif val == "不喜欢":
                    dislike_foods.add(fname)
            elif about:
                if val == "不喜欢":
                    dislike_ex.add(about)
        if not (pats_like or dislike_ex or like_foods or dislike_foods):
            return None
        return {"pats_like": pats_like, "dislike_ex": dislike_ex,
                "like_foods": like_foods, "dislike_foods": dislike_foods}
    except Exception:
        return None


def _load_linkage(ctx) -> tuple[set, set]:
    """联动信号：疲劳（48h 内练过的 pattern）+ 伤痛禁忌模式集。异常/无图 → 空集。"""
    fatigue: set = set()
    blocked: set = set()
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return fatigue, blocked
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        import screening                                   # lib 单源
        for part in m.current_about(uid, "injury"):
            entry = screening.contraindication(part)
            if entry:
                blocked.update(entry.get("danger_patterns") or [])
        from app.runtime.repos import exercise_repo
        ex = exercise_repo()
        for e in m.events(uid, "checkin", days=2):
            for it in (e.get("payload") or {}).get("items", []):
                nm = it.get("name") or it.get("raw")     # raw 段也参与（E2E 形态）
                if not nm:
                    continue
                hit = ex.search_zh(nm, limit=1)
                if hit and hit[0].get("movement_pattern"):
                    fatigue.add(hit[0]["movement_pattern"])
    except Exception:
        pass
    return fatigue, blocked


class PlanSkill(Skill):
    name = "plan"
    description = "一日训练+饮食计划（筛查→FITT→宏观→动作→食材）；计划指令调整"
    task_types = ("plan", "plan_edit")

    REQUIRED = ("weight_kg", "height_cm", "age")

    def execute(self, ctx, params) -> SkillResult:
        # T5 计划指令：query + 换/删句式 → _edit（不依赖 task_type 透传）
        q = str(params.get("query", "") or "")
        if q and (_EDIT_REPLACE_RE.search(q) or _EDIT_REMOVE_RE.search(q)):
            return self._edit(ctx, q)
        profile = dict(ctx.profile or {})
        missing = [k for k in self.REQUIRED if k not in profile]
        if missing:
            return SkillResult(ok=False, data={}, provenance=[],
                               error=f"缺少计划必需档案字段: {', '.join(missing)}")
        profile.setdefault("goal", str(params.get("goal", "maintain")))
        profile.setdefault("activity", 1.55)
        prefs = _load_prefs(ctx)
        fatigue, extra_blocked = _load_linkage(ctx)
        out = build_plan(profile, prefs=prefs, fatigue=fatigue,
                         extra_blocked=extra_blocked)
        if not out.get("ok", True):
            return SkillResult(ok=False, data={},
                               provenance=out.get("provenance", []),
                               error=out.get("error"))
        _register_plan(out["plan"],
                      getattr(getattr(ctx, "session", None), "user_id", "local"))  # F4
        prov = list(out.get("provenance", []))
        if prefs and out["plan"].get("preferences_applied"):
            prov.append("memory#preference")     # 偏好应用留痕
        return SkillResult(ok=True, data=out["plan"], provenance=prov)

    def _edit(self, ctx, query: str) -> SkillResult:
        """计划指令：把X换成Y / 去掉X（pattern 一致校验；如实拒绝，不硬改）。"""
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        latest = m.latest_plan(uid) if m else None
        if not latest or not latest.get("content"):
            return SkillResult(ok=True, data={"items": [{
                "name": "无计划", "value": "你还没有训练计划——先让我帮你制定一个吧"}]},
                provenance=["plan#edit.no_plan"])
        content = latest["content"]
        items = (content.get("training") or {}).get("items") or []
        mrep = _EDIT_REPLACE_RE.search(query)
        mdel = None if mrep else _EDIT_REMOVE_RE.search(query)
        if not mrep and not mdel:
            return SkillResult(ok=True, data={"items": [{
                "name": "无法解析",
                "value": "可以说'把某动作换成某动作'或'去掉某动作'"}]},
                provenance=["plan#edit.parse_fail"])
        x_raw = (mrep.group(1) if mrep else mdel.group(1)).strip(" ，。我")
        y_raw = mrep.group(2).strip(" ，。") if mrep else None
        hit_day = hit_ex = None
        for d in items:
            for e in d.get("exercises", []):
                if x_raw and (x_raw in str(e.get("name", ""))
                              or str(e.get("name", "")) in x_raw):
                    hit_day, hit_ex = d, e
                    break
            if hit_ex:
                break
        if hit_ex is None:
            return SkillResult(ok=True, data={"items": [{
                "name": "未找到",
                "value": f"当前计划里没有「{x_raw}」这个动作。"
                         "如果是饮食偏好，直接说'我不喜欢吃X'我会记住"}]},
                provenance=["plan#edit.not_found"])
        if mdel:
            if len(hit_day["exercises"]) <= 1:
                return SkillResult(ok=True, data={"items": [{
                    "name": "拒绝", "value": "每个训练日至少保留一个动作"}]},
                    provenance=["plan#edit.guard"])
            hit_day["exercises"].remove(hit_ex)
            done = f"已把{hit_day['day']}的{hit_ex.get('name')}去掉"
        else:
            from app.runtime.repos import exercise_repo
            hits = exercise_repo().search_zh(y_raw, limit=1)
            if not hits:
                return SkillResult(ok=True, data={"items": [{
                    "name": "未找到", "value": f"没找到「{y_raw}」这个动作"}]},
                    provenance=["plan#edit.y_not_found"])
            y = hits[0]
            if y.get("movement_pattern") != hit_day.get("pattern"):
                return SkillResult(ok=True, data={"items": [{
                    "name": "拒绝",
                    "value": f"「{y.get('name_zh')}」不属于{hit_day['day']}的"
                             f"动作模式，不换。可换同模式动作"}]},
                    provenance=["plan#edit.pattern_mismatch"])
            sug = y.get("suggested") or {}
            hit_ex.update({"name": y.get("name_zh"),
                           "difficulty": y.get("difficulty"),
                           "sets": sug.get("sets"), "reps": sug.get("reps"),
                           "rest_sec": sug.get("rest_sec"),
                           "equipment": y.get("normalized_equipment")})
            done = f"已把{hit_day['day']}的{x_raw}换成{y.get('name_zh')}"
        m.register_plan(uid, f"plan-{uuid.uuid4().hex[:8]}",
                        "edit", content=content)
        return SkillResult(ok=True, data={
            "items": [{"name": "计划已更新", "value": done}],
            "plan": content}, provenance=["plan#edit", "memory#plan_version"])