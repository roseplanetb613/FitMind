# -*- coding: utf-8 -*-
"""计划生成：调用 runtime.pipeline 编排链。
产出计划即登记记忆图谱 PlanVersion（采纳闭环前置——个人记忆 spec v0.3 §2）。"""
from __future__ import annotations
import copy
import hashlib
import json
import re
import uuid
from datetime import date, timedelta

import split_cycle
from exercise_repo import norm_zh as _norm_zh   # 中文检索归一单源（去空格+小写）
from app.core import diag                     # 降级可观测（静默失败可查）
import parts                                   # 部位词单表（lib/parts.py）
from app.skills.base import Skill, SkillResult
from app.runtime.pipeline import build_plan

# T5 计划指令控制："把X换成Y" / "去掉X"
# 2026-09-11 补否定式删除（不练/别练/取消）：CLI 实证"今天不练三头"被判为 plan_edit
# 却匹不中任何编辑句式 → 静默回落整份重新生成（零改动），而渲染层照常叙述了一次
# 并未发生的编辑（"推日里跟三头相关的动作就撤掉"）——声称做了没做的事。
_EDIT_REPLACE_RE = re.compile(r"(?:把)?(.+?)(?:换成|换掉|改成做?)(.+)")
_EDIT_REMOVE_RE = re.compile(r"(?:去掉|删掉|不要练?|不练|别练|取消)(.+)")
# 整计划删除（2026-09-16）：「删掉所有训练计划」此前被 _EDIT_REMOVE_RE 当成
# "删掉（名为'所有训练计划'的**动作**）"→ 找不到动作 → 回"没有找到"——
# 用户想删的是**整份计划**，不是某个动作。窄口径：动词与"计划"之间不留太多字。
_PLAN_CLEAR_RE = re.compile(r"(?:删掉|删除|清空|作废)[^，。]{0,6}计划")

# 支持的编辑句式单源：**技能自己会说出口**（parse_fail/not_found 的提示），渲染层
# 也只会复述这里的写法。2026-09-13 的坑正是两边不一致——助手自创「今天改成休息日」
# 而技能层没有这条语法，用户照说必然失败。
_EDIT_HINT = ("换动作（'把卧推换成哑铃卧推'）、去掉动作（'去掉窄距卧推'）、"
              "按部位撤（'今天不练三头'）、整天休息（'今天改成休息日'）")

_DAY_OFFSET_ZH = ("今天", "明天", "后天")


def _day_name(target: dict) -> str:
    """目标日 spec → 给用户看的说法（"今天"/"后天"/"9月30日"）。"""
    off = target.get("offset")
    if isinstance(off, int) and 0 <= off < len(_DAY_OFFSET_ZH):
        return _DAY_OFFSET_ZH[off]
    if "month" in target:
        return f"{target['month']}月{target['day']}日"
    return "这一天"


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


# N-8 偏好消费：部位→pattern 来自 lib/parts.py 单表（仅在 screening 放行空间内加权，
# 绝不解禁）。无 pattern 的部位词（腰/前臂等）→ None → 不加权，与收口前一致。


def _load_prefs(ctx) -> dict | None:
    """读记忆偏好 → {pats_like, dislike_ex, like_foods, dislike_foods,
    split_pref}；无图/无偏好/异常 → None。"""
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return None
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        pats_like, dislike_ex = set(), set()
        like_foods, dislike_foods = set(), set()
        split_pref = None
        for r in m.current(uid, "preference"):
            about = str(r.get("about") or "")
            val = str(r.get("value") or "")
            if about.startswith("部位:"):
                if val == "喜欢":
                    pat = parts.pattern_of(about[3:])
                    if pat:
                        pats_like.add(pat)
            elif about.startswith("编排:"):           # 周期编排偏好（spec §7.4）
                if val == "喜欢":
                    split_pref = about[3:]
            elif about.startswith("食物:"):
                fname = about[3:]
                if val == "喜欢":
                    like_foods.add(fname)
                elif val == "不喜欢":
                    dislike_foods.add(fname)
            elif about:
                if val == "不喜欢":
                    dislike_ex.add(about)
        if not (pats_like or dislike_ex or like_foods or dislike_foods
                or split_pref):
            return None
        return {"pats_like": pats_like, "dislike_ex": dislike_ex,
                "like_foods": like_foods, "dislike_foods": dislike_foods,
                "split_pref": split_pref}
    except Exception:
        return None


def _resolve_scheme(ctx, split_req, prefs) -> tuple[dict, str]:
    """编排方案三级优先（spec §7.4）：话语参数 > 记忆偏好（编排:X）>
    profile.split > 数据包默认。方案名非空却解析不到时不再静默回落：来源标记
    `unresolved:<名>`（provenance 出 split#unresolved:<名>），如实留痕（2026-09-11）。"""
    import split_cycle
    if split_req:
        sch, ok = split_cycle.resolve_scheme_checked(str(split_req))
        return sch, "utterance" if ok else f"unresolved:{split_req}"
    if prefs is None:
        prefs = _load_prefs(ctx)          # prefs 未注入 → 自查记忆（独立调用形态）
    if prefs and prefs.get("split_pref"):
        sch, ok = split_cycle.resolve_scheme_checked(prefs["split_pref"])
        return sch, "memory" if ok else f"unresolved:{prefs['split_pref']}"
    prof = (getattr(ctx, "profile", None) or {}).get("split")
    if prof:
        sch, ok = split_cycle.resolve_scheme_checked(str(prof))
        return sch, "profile" if ok else f"unresolved:{prof}"
    return split_cycle.default_scheme(), "default"


def _load_linkage(ctx) -> tuple[set, set, list]:
    """联动信号：疲劳（48h 内练过的模式，**带出处**）+ 伤痛禁忌模式集。

    返回 (fatigue, blocked, sources)：sources=[{"date","term","pattern"}] 与 fatigue
    **同一次遍历**产生——两套逻辑各算一遍必然漂移。出处用于两处：计划数据落
    `fatigue_sources`（用户追问"练的哪里"时可回读），以及 fatigue_note 逐条生成。
    异常/无图 → (set(), set(), [])，计划与现状逐字一致。"""
    fatigue: set = set()
    blocked: set = set()
    sources: list = []
    try:
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        if m is None:
            return fatigue, blocked, sources
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        import screening                                   # lib 单源
        for part in m.current_about(uid, "injury"):
            entry = screening.contraindication(part)
            if entry:
                blocked.update(entry.get("danger_patterns") or [])
        from app.runtime.repos import exercise_repo
        ex = exercise_repo()
        for e in m.events(uid, "checkin", days=2):
            date = str(e.get("occurred_at") or "")[:10]
            for it in (e.get("payload") or {}).get("items", []):
                nm = it.get("name") or it.get("raw")     # raw 段也参与（E2E 形态）
                if not nm:
                    continue
                pats = _trained_patterns(ex, nm)
                fatigue |= pats
                if pats:                # 判不准（空）不产出出处——没信号就没依据
                    sources.append({"date": date, "term": str(nm),
                                    "pattern": "/".join(sorted(pats))})
    except Exception:
        diag.bump("plan.linkage")   # 静默失败 → 计划会漏减量且无迹可查
    return fatigue, blocked, sources


# ---------------------------------------------------------------- 疲劳归属
# 缺陷（CLI 实测 2026-09-10）：checkin 里的**部位词**曾被当动作名做 top1 模糊检索，
# 再直接采信该动作的 movement_pattern——"腿" 命中「摆臂 悬垂 屈膝 腿」（腹直肌，
# pattern=core）→ 错标核心日减量，并对用户陈述"近48小时练过核心日对应的部位"。
# 修复：部位词走「部位→肌群→该肌群 primary 动作的主导模式」；判不准则不产生信号
# （宁可不减量，也不错误减量 + 说错话）。
#
# 部位词表：数据包肌群别名（肱二/股四/小腿/下背…）优先；口语词补两张小表。
# 非训练模式不参与疲劳判定（拉伸/有氧/搬运/其他）
_FATIGUE_SKIP_PATTERNS = {"stretch", "other", "cardio", "carry"}
_FATIGUE_DOMINANT_SHARE = 0.5    # 主导模式阈值：占该部位 primary 动作最大票数的比例
_PART_KW_MAXLEN = 6              # 含部位词的兜底解析只认短词（防"悬垂举腿4x8"被当腿）


def _dominant_patterns(ex, muscles: set) -> set:
    """肌群集合 → 其 primary(target) 动作的主导模式（占比达阈值；空=判不准）。"""
    from collections import Counter
    c = Counter(r["movement_pattern"] for r in ex.by_id.values()
                if r["muscles_canonical"]["target"] in muscles
                and r["movement_pattern"] not in _FATIGUE_SKIP_PATTERNS)
    if not c:
        return set()
    top = max(c.values())
    return {p for p, n in c.items() if n >= top * _FATIGUE_DOMINANT_SHARE}


def _bare_part_patterns(ex, term: str) -> set | None:
    """裸部位词 → 模式集合；不是部位词 → None。"""
    mus = ex._norm_muscle(term)
    if mus:
        if any(r["muscles_canonical"]["target"] == mus for r in ex.by_id.values()):
            return _dominant_patterns(ex, {mus})
        # 退化节点（如本体的 'core'：无 primary 动作）→ 回退其 region 肌群
        region = (ex.muscle_meta.get(mus) or {}).get("region")
        if region:
            return _dominant_patterns(
                ex, {m["id"] for m in ex.ontology if m.get("region") == region})
        return None
    if parts.kind_of(term) == "muscle" and parts.muscle_of(term):
        return _dominant_patterns(ex, {parts.muscle_of(term)})
    if parts.region_of(term):
        region = parts.region_of(term)
        return _dominant_patterns(
            ex, {m["id"] for m in ex.ontology if m.get("region") == region})
    return None


def _segment_patterns(ex, seg: str) -> set:
    """单段词 → 模式集合：裸部位词 → 动作检索 top1 → 短词含部位兜底 → 空。"""
    seg = (seg or "").strip()
    if not seg:
        return set()
    part = _bare_part_patterns(ex, seg)
    if part is not None:
        return part
    hits = ex.search_zh(seg, limit=1)
    if hits and hits[0].get("movement_pattern"):
        return {hits[0]["movement_pattern"]}
    # 兜底：脏 raw（如 N-10 产生的"过肩背"）检索无命中但含部位词
    if len(seg) <= _PART_KW_MAXLEN:
        for k in sorted(parts.region_words(), key=len, reverse=True):
            if k in seg:
                region = parts.region_of(k)
                return _dominant_patterns(
                    ex, {m["id"] for m in ex.ontology
                         if m.get("region") == region})
    return set()


def _trained_patterns(ex, nm: str) -> set:
    """一次 checkin 词（可能含"腿、悬垂举腿4x8"式复合）→ 训练到的模式集合。"""
    import re as _re
    out: set = set()
    for seg in _re.split(r"[、,，/;；和\s]+", str(nm or "")):
        out |= _segment_patterns(ex, seg)
    return out


# ---------------------------------------------------------------- 肌群级编辑
# CLI 实证（2026-09-11）："今天不练三头" 说的是**部位**不是动作名，而计划条目只有
# name（无肌群字段）→ 按名字包含匹配必然落空。改为：部位词 → 肌群集合 → 反查计划内
# 每个动作名的肌群（exercise_repo 单源）。仅用于删除（替换仍须点名动作）。
def _tag_muscles(ex, tag: str) -> set:
    """部位词 → muscle id 集合（单源 lib/parts.py）。

    策略（与收口前逐条等价）：本体精确肌群名优先 → 细分工位词（二头/三头/腹肌）直接
    取该肌群 → 区域词**展开整个区域**（"腿"→ upper_legs 全部，而非只 quadriceps——
    腿日含股四/臀/腘绳，只撤股四会漏）。"""
    m = ex._norm_muscle(tag)
    if m:
        return {m}
    if parts.kind_of(tag) == "muscle" and parts.muscle_of(tag):
        return {parts.muscle_of(tag)}
    if parts.region_of(tag):
        region = parts.region_of(tag)
        return {x["id"] for x in ex.ontology if x.get("region") == region}
    return set()


def _muscle_targets(items, tag: str) -> list[tuple[dict, dict]]:
    """计划中肌群命中 tag 的 (日, 动作) 列表；词/仓不可用 → 空（调用方如实拒绝）。"""
    try:
        from app.runtime.repos import exercise_repo
        ex = exercise_repo()
        want = _tag_muscles(ex, tag)
        if not want:
            return []
    except Exception:
        return []
    out: list[tuple[dict, dict]] = []
    for d in items:
        for e in d.get("exercises") or []:
            nm = str(e.get("name") or "")
            if not nm:
                continue
            try:
                hits = ex.search_zh(nm, limit=1)
            except Exception:
                continue
            tgt = (hits[0].get("muscles_canonical") or {}).get("target") if hits else None
            if tgt in want:
                out.append((d, e))
    return out


class PlanSkill(Skill):
    name = "plan"
    description = "一日训练+饮食计划（筛查→FITT→宏观→动作→食材）；计划指令调整"
    task_types = ("plan", "plan_edit")

    REQUIRED = ("weight_kg", "height_cm", "age")

    def execute(self, ctx, params) -> SkillResult:
        # T5 计划指令：query + 换/删句式 → _edit（不依赖 task_type 透传）
        q = str(params.get("query", "") or "")
        # W4 整体平移优先（shift_days 由分类层下发）：与换/删句式互斥，先判
        if params.get("shift_days") is not None:
            return self._edit(ctx, q, shift_days=int(params["shift_days"]))
        if q and (_EDIT_REPLACE_RE.search(q) or _EDIT_REMOVE_RE.search(q)):
            return self._edit(ctx, q)
        # plan_edit 意图但句式未识别（"edit" 标记由分类层下发）→ 交 _edit 如实拒绝。
        # 此前静默落入正常生成路径：用户以为改了计划，实际重跑了一份一模一样的。
        if q and params.get("edit"):
            return self._edit(ctx, q)
        # 计划查询（read 标记）→ 读回既有计划（含编辑后的版本），不重跑引擎。
        # 无既有计划 → 落回下面的正常生成（首次建档体验不变）。
        if params.get("read") and not params.get("split") and not params.get("days"):
            try:
                from app.graph.memory import MemoryStore
                _m = MemoryStore.get()
                _uid = getattr(getattr(ctx, "session", None), "user_id", "local")
                _latest = _m.latest_plan(_uid) if _m else None
            except Exception:
                _latest = None
            if _latest and _latest.get("content"):
                # 重锚定（2026-09-11 D2）：读回的是生成时快照，隔日不重算会把昨天
                # 说成"今天"（实测锚点整体倒退一天）；隔日同时剔除过期疲劳断言。
                import datetime as _dt
                import split_cycle
                content = split_cycle.reanchor(_latest["content"],
                                               _dt.date.today())
                return SkillResult(ok=True, data=content,
                                   provenance=["plan#read",
                                               "memory#plan_version"])
        profile = dict(ctx.profile or {})
        missing = [k for k in self.REQUIRED if k not in profile]
        if missing:
            return SkillResult(ok=False, data={}, provenance=[],
                               error=f"缺少计划必需档案字段: {', '.join(missing)}")
        profile.setdefault("goal", str(params.get("goal", "maintain")))
        profile.setdefault("activity", 1.55)
        prefs = _load_prefs(ctx)
        fatigue, extra_blocked, fatigue_sources = _load_linkage(ctx)
        scheme, split_src = _resolve_scheme(ctx, params.get("split"), prefs)
        try:
            days = int(params["days"]) if params.get("days") else None
        except (TypeError, ValueError):
            days = None
        if days is not None and days < 1:
            days = None   # <1 归一为未指定：保方案走单周期，防 pipeline 静默重置
        elif days is not None and days > 90:
            days = 90     # 超大天数封顶 90（防 expand 平铺挂起；>3 个月的计划无训练学意义）
        try:
            from app.runtime.history import WorkoutHistory
            # 进阶回哺：读**图谱** —— 训练记录的写入侧只写图谱（`log_event`），
            # SQLite `workout_set` 没有生产写入方，读它必空。
            # 见 docs/SDD/user-data-domain.md §6-F1
            hist = WorkoutHistory(user_id=getattr(
                getattr(ctx, "session", None), "user_id", None))
        except Exception:
            hist = None
        out = build_plan(profile, prefs=prefs, fatigue=fatigue,
                         extra_blocked=extra_blocked, scheme=scheme,
                         days=days, history=hist,
                         fatigue_sources=fatigue_sources)
        if not out.get("ok", True):
            return SkillResult(ok=False, data={},
                               provenance=out.get("provenance", []),
                               error=out.get("error"))
        _register_plan(out["plan"],
                      getattr(getattr(ctx, "session", None), "user_id", "local"))  # F4
        prov = list(out.get("provenance", []))
        prov.append(f"split#{split_src}")        # 方案来源留痕
        if prefs and out["plan"].get("preferences_applied"):
            prov.append("memory#preference")     # 偏好应用留痕
        return SkillResult(ok=True, data=out["plan"], provenance=prov)

    def _shift(self, m, uid: str, content: dict, days: int) -> SkillResult:
        """整体平移：start_date += days，日期锚点按"今天"重算（split_cycle 单源）。

        无 start_date（旧版计划）→ **如实拒绝**，不静默重新生成——这是 D1 的根治：
        此前无此能力，静默重跑 + 渲染层编造平移日程（含数据里不存在的日期）。"""
        base = copy.deepcopy(content)
        sd = base.get("start_date")
        if not sd:
            return SkillResult(ok=True, data={"items": [{
                "name": "无法平移",
                "value": "这版计划没有日期基准，没法整体挪动——要我重排一版吗"}]},
                provenance=["plan#edit.shift.no_base"])
        try:
            start = date.fromisoformat(str(sd))
        except (TypeError, ValueError):
            return SkillResult(ok=True, data={"items": [{
                "name": "无法平移",
                "value": "这版计划的日期基准不合法，没法整体挪动——要我重排一版吗"}]},
                provenance=["plan#edit.shift.no_base"])
        base["start_date"] = (start + timedelta(days=days)).isoformat()
        moved = split_cycle.reanchor(base, date.today())
        m.register_plan(uid, f"plan-{uuid.uuid4().hex[:8]}", "edit", content=moved)
        return SkillResult(ok=True, data={
            "items": [{"name": "计划已平移",
                       "value": f"已把整份计划后延 {days} 天"}],
            "plan": moved},
            provenance=["plan#edit.shift", "memory#plan_version"])

    def _rest_day(self, m, uid: str, content: dict, target: dict,
                  query: str) -> SkillResult:
        """整天改休息日：定位条目 → 翻转成 rest（capability 补课，2026-09-13）。

        先 `reanchor` 再定位：`_edit` 读回的是**生成时快照**，隔日锚点整体偏移
        （`day_label` 把第 1 天写成"今天"），按快照定位「今天」会撤错天——与 read
        路径 D2 同一个坑。reanchor 同时剔掉过期的 fatigue 断言（隔日即失效）。"""
        today = date.today()
        if not content.get("start_date"):
            return SkillResult(ok=True, data={"items": [{
                "name": "无法定位",
                "value": "这版计划没有日期基准，定位不到「今天」——要我重排一版吗"}]},
                provenance=["plan#edit.rest.no_base"])
        content = split_cycle.reanchor(content, today)
        items = (content.get("training") or {}).get("items") or []
        idx = split_cycle.day_index(content, target, today)
        if idx is None or idx >= len(items):
            return SkillResult(ok=True, data={"items": [{
                "name": "未找到",
                "value": f"这份计划里没有「{_day_name(target)}」那一天"
                         f"（共 {len(items)} 天，超出跨度了）"}]},
                provenance=["plan#edit.rest.not_found"])
        day = items[idx]
        if day.get("type") == "rest":
            return SkillResult(ok=True, data={"items": [{
                "name": "无需改动",
                "value": f"{day.get('date') or ''}本来就是休息日，计划没有变动"}]},
                provenance=["plan#edit.rest.noop"])
        if sum(1 for d in items if d.get("type") != "rest") <= 1:
            return SkillResult(ok=True, data={"items": [{
                "name": "拒绝",
                "value": "这是计划里唯一一个训练日，整天撤掉就没得练了——"
                         "真要休息的话我按新周期给你重排一版"}]},
                provenance=["plan#edit.rest.guard"])
        label = str(day.get("day") or "")
        for k in ("pattern", "deload", "focused", "blocked_from", "rest_from"):
            day.pop(k, None)                 # 降级日标记/模式必须清干净
        day.update({"day": f"休息日（原：{label}）", "type": "rest",
                    "exercises": [], "note": split_cycle.REST_NOTE,
                    # rest_from = **用户要求**改休；渲染层据此与"因身体筛查"
                    # （blocked_from）分开措辞——混用等于替筛查背锅
                    "rest_from": label})
        m.register_plan(uid, f"plan-{uuid.uuid4().hex[:8]}", "edit", content=content)
        return SkillResult(ok=True, data={
            "items": [{"name": "计划已更新",
                       "value": f"已把{day.get('date')}整天空出来，"
                                f"原定的{label}改为休息日"}],
            "plan": content},
            provenance=["plan#edit.rest", "memory#plan_version"])

    def _edit(self, ctx, query: str, shift_days: int | None = None) -> SkillResult:
        """计划指令：把X换成Y / 去掉X / 整天休息 / 整体平移（如实拒绝，不硬改）。"""
        from app.graph.memory import MemoryStore
        m = MemoryStore.get()
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        latest = m.latest_plan(uid) if m else None
        if not latest or not latest.get("content"):
            return SkillResult(ok=True, data={"items": [{
                "name": "无计划", "value": "你还没有训练计划——先让我帮你制定一个吧"}]},
                provenance=["plan#edit.no_plan"])
        content = latest["content"]
        # 整计划删除优先于换/删句式（「删掉所有训练计划」的"删掉(所有训练计划)"
        # 会先被 _EDIT_REMOVE_RE 当成动作名——动作 miss 的"没有找到"答非所问）。
        # **软删**：supersede_plan 打 deleted_at 标，打卡历史（log_checkin 引用
        # plan_id）与版本链都保留；latest_plan 过滤后为空 → 后续请求走 no_plan。
        if _PLAN_CLEAR_RE.search(query):
            n = m.supersede_plans(uid)
            if n < 0:
                return SkillResult(ok=True, data={"items": [
                    {"name": "删除失败", "value": "记忆图谱不可用，稍后再试"}]},
                    provenance=["plan#edit.supersede_failed"])
            return SkillResult(ok=True, data={"items": [
                {"name": "计划已删除",
                 "value": "当前训练计划已删除。你的打卡历史保留，"
                          "想重新开始随时说一声。"}]},
                provenance=["plan#edit.plan_cleared"])
        if shift_days is not None:
            return self._shift(m, uid, content, shift_days)
        items = (content.get("training") or {}).get("items") or []
        mrep = _EDIT_REPLACE_RE.search(query)
        # day 级必须先于换/删句式（2026-09-13）：「今天改成休息日」的"改成"会被
        # _EDIT_REPLACE_RE 切成 X=今天 / Y=休息日，进而报"没有「今天」这个动作"。
        target = split_cycle.extract_rest_day(query)
        if target is not None:
            # "把推日改成休息日" 句中没有日词 → 缺省今天；但 X 侧若**恰好**是某个
            # day 名，它比缺省确定得多。定位错就是把**别的**一天撤掉（破坏性），
            # 所以宁可多这一步。注意 "今天" 不是任何 day 名，不会被这里劫走。
            if mrep:
                _di = split_cycle.day_index_of_label(
                    items, mrep.group(1).strip(" ，。我的"))
                if _di is not None:
                    target = {"index": _di}
            return self._rest_day(m, uid, content, target, query)
        mdel = None if mrep else _EDIT_REMOVE_RE.search(query)
        if not mrep and not mdel:
            return SkillResult(ok=True, data={"items": [{
                "name": "无法解析",
                "value": f"告诉我要改哪一处就行：{_EDIT_HINT}"}]},
                provenance=["plan#edit.parse_fail"])
        # 尾缀语气词一并剥掉："不练三头了" 的目标词是"三头"而不是"三头了"——
        # 带着"了"去比对肌群词/日名必然 miss（同"了"在 _EDIT_REMOVE_RE 里也吃得下）。
        x_term = (mrep.group(1) if mrep else mdel.group(1)).strip(" ，。我了吧啦呀")
        # 剥离训练日前缀（"把拉日引体向上换成X"→"引体向上"），防包含匹配 miss
        x_raw = re.sub(r"(?:推|拉|腿|核心|胸|背|肩|臀|腹|臂)日", "",
                       x_term).strip(" ，。的")
        y_raw = mrep.group(2).strip(" ，。") if mrep else None
        # 空格归一后比对（2026-09-11）：计划内动作名来自 search_zh，多为带空格复合名
        # （'杠铃 窄距 卧推'），而用户说的是 '窄距卧推' → 朴素子串包含**永不命中**
        # （实测 plan#edit.not_found）。复用检索层同一归一口径 norm_zh（单源）。
        _x = _norm_zh(x_raw)
        hit_day = hit_ex = None
        for d in items:
            for e in d.get("exercises", []):
                _n = _norm_zh(str(e.get("name", "")))
                if _x and (_x in _n or _n in _x):
                    hit_day, hit_ex = d, e
                    break
            if hit_ex:
                break
        if hit_ex is None and mdel and x_term:
            # day 级删除（"今天不练推日"）：词面**恰好**是某天的 day 名 → 整天改休息日。
            # 必须用**未剥离**的 x_term——日前缀剥离会把「推日」剥成空串（实测报出
            # 「当前计划里没有「」这个动作」，用户看到一对空引号）。
            _di = split_cycle.day_index_of_label(items, x_term)
            if _di is not None:
                return self._rest_day(m, uid, content, {"index": _di}, query)
        if hit_ex is None and mdel:
            # 肌群级删除："不练三头" 命中 triceps → 撤掉计划里所有 target=triceps 的动作
            pairs = _muscle_targets(items, x_raw)
            if pairs:
                dropped, kept_days = [], 0
                for d in items:
                    drop = [e for dd, e in pairs if dd is d]
                    if not drop or len(d["exercises"]) - len(drop) < 1:
                        continue            # 每日至少留一个动作（同单动作删除护栏）
                    for e in drop:
                        d["exercises"].remove(e)
                        dropped.append(str(e.get("name")))
                    kept_days += 1
                if kept_days:
                    m.register_plan(uid, f"plan-{uuid.uuid4().hex[:8]}",
                                    "edit", content=content)
                    return SkillResult(ok=True, data={
                        "items": [{"name": "计划已更新",
                                   "value": f"已把{x_raw}相关动作"
                                            f"（{'、'.join(dropped)}）从计划里去掉"}],
                        "plan": content},
                        provenance=["plan#edit.muscle", "memory#plan_version"])
        if hit_ex is None:
            # 目标词被剥空（如"今天不练推日"且"推日"非本计划日名）→ 报未找到时
            # 会印出一对空引号，用户无从判断；按未能解析处理，退回句式提示。
            if not _x:
                return SkillResult(ok=True, data={"items": [{
                    "name": "无法解析",
                    "value": f"没看出要改哪一处。可以说：{_EDIT_HINT}"}]},
                    provenance=["plan#edit.parse_fail"])
            return SkillResult(ok=True, data={"items": [{
                "name": "未找到",
                "value": f"当前计划里没有「{x_raw}」这个动作。{_EDIT_HINT}。"
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