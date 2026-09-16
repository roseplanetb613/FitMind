# -*- coding: utf-8 -*-
"""知识问答：动作/食物检索，结果带来源标注（不发 LLM，纯规则；科学语境补 RAG 块）。"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone
from app.skills.base import Skill, SkillResult


def _as_of_hint(query: str) -> str | None:
    """档案时间问法 → as-of 时刻（ISO；E2E-01：上个月/昨天/N天前）。"""
    now = datetime.now(timezone.utc)
    if "上个月" in query or "上月" in query:
        return (now - timedelta(days=30)).isoformat()
    if "上周" in query:
        return (now - timedelta(days=7)).isoformat()
    m = re.search(r"(\d+)\s*天前", query)
    if m:
        return (now - timedelta(days=int(m.group(1)))).isoformat()
    if "昨天" in query:
        return (now - timedelta(days=1)).isoformat()
    return None


def _detect_profile_field(query: str) -> str | None:
    """从档案类问句中识别用户想问但档案可能缺的指标名（W1 反幻觉）。"""
    for key, hints in _PROFILE_FIELD_HINTS.items():
        if any(h in query for h in hints):
            return key
    return None


# 档案未记录指标 → 如实引导话术（绝不硬编数值）
_PROFILE_MISSING_HINT = {
    "bodyfat": "档案未记录体脂数据，需体脂秤/体测仪数据才能估算",
    "ffmi": "档案未记录肌肉与骨骼数据，FFMI 需体测仪比对",
    "training_capacity": "档案未记录训练容量/记录，需在会话中保持训练打卡",
    "heart": "档案未记录心率数据，建议先测量静息心率再评估",
    "strength": "档案未记录各动作参考标准，可查动作库对应力量标准",
    "flexibility": "档案未记录柔韧性评估，常规体测可补",
    "sleep": "档案未记录睡眠数据，可自行记录一周观察",
    "body_shape": "档案未记录腰臀比/围度数据，需皮尺测量",
}
_PROFILE_FIELD_HINTS = {
    "bodyfat": ("体脂", "内脏脂肪"),
    "ffmi": ("FFMI", "ffmi", "肌肉量", "骨量", "BMI"),
    "training_capacity": ("训练容量", "训练年限", "练了什么", "昨天练", "上周练"),
    "heart": ("心肺", "心率", "握力", "一英里"),
    "strength": ("硬拉多少算达标", "算达标", "算什么水平"),
    "flexibility": ("柔韧性", "肌肉分布"),
    "sleep": ("睡眠", "蛋白质需求", "水分需求"),
    "body_shape": ("腰臀比", "理想体重", "该吃多少卡"),
}


def repos():
    """共享单例（app.runtime.repos）：避免与 pipeline/teach 各自装配大表。"""
    from app.runtime.repos import exercise_repo, foods_repo
    return exercise_repo(), foods_repo()


class QaSkill(Skill):
    name = "qa"
    description = "动作/食物/知识检索问答"
    # profile_edit：档案**写入**的回读确认（"把目标改成减脂"）。写入本身由
    # memory_extract 在 agent 入口完成，这里只负责组织答复——回读档案时新值
    # 已经在里面，等于"已记下 + 给你看现状"。故复用 qa 的 profile 分支，
    # 不另起技能（同样的读法、同样的字段表，另写一份必然漂）。
    task_types = ("qa", "fallback", "profile_edit")

    # 科学/医学语境触发词 → 追加 RAG 知识块（training-science / sports-medicine）
    _SCIENCE_KW = ("强度", "RPE", "心率", "渐进", "免疫", "肌肉生长",
                   "营养", "增肌", "减脂", "代谢", "激素", "运动科学",
                   "科学", "原理", "机制", "恢复")

    # W3 辟谣知识块（业务规则知识，不进数据包）：结论 + 原理一句话，禁医疗建议口吻。
    # 与 guard 分工：谣言→qa 知识；症状→guard 安全。具体条目在前，泛化兜底在后。
    _MYTH_KB = (
        {"keys": ("暴汗服",), "name_zh": "暴汗服不是瘦得快",
         "summary": "暴汗服只是多出汗（水分流失），不减脂；脂肪靠热量缺口消耗，"
                    "穿它运动反而易脱水，注意补水。"},
        {"keys": ("束腰带", "束腰"), "name_zh": "束腰带不瘦腰",
         "summary": "束腰只是物理收紧外观，不改变皮下脂肪；长期勒紧还可能影响呼吸与核心发力。"},
        {"keys": ("左旋肉碱", "左旋"), "name_zh": "左旋肉碱不是减肥神器",
         "summary": "左旋肉碱帮脂肪转运，但运动中能否提质增量证据不足；不运动时吃它不会瘦。"},
        {"keys": ("局部减脂", "瘦肚子", "瘦哪"), "name_zh": "局部减脂不存在",
         "summary": "脂肪消耗是全身性的，卷腹练不瘦肚子；要靠总热量缺口+全身训练降低体脂。"},
        {"keys": ("排毒", "出汗"), "name_zh": "出汗不等于排毒",
         "summary": "汗液中绝大部分是水，代谢废物主要由肝肾处理；出汗多少和训练效果没有直接关系。"},
        {"keys": ("脂肪变肌肉", "肌肉变脂肪", "练成肌肉", "脂肪变", "变成脂肪", "掉肌肉"),
         "name_zh": "脂肪与肌肉不可互变",
         "summary": "这是两种不同组织，不存在转化；停训掉的是肌肉，胖是额外热量堆积。"},
        {"keys": ("emo", "一周没练", "一周不想", "情绪化"),
         "name_zh": "emo/短暂情绪低谷不会掉肌肉",
         "summary": "几天心情不好停练消耗的是训练状态，不是肌肉本身；恢复规律训练"
                    "后会很快找回。情绪自我照顾优先，别用'掉肌肉'吓自己。"},
        {"keys": ("摆烂", "半个月", "停练", "很久没练", "一个月没练"),
         "name_zh": "停练/摆烂不会让肌肉变脂肪",
         "summary": "停练掉的肌肉是缺刺激，不会'变成脂肪'；恢复时别急着追重量，"
                    "按原强度的 60-70% 起步、逐周加量（肌肉记忆会帮你较快恢复）。"},
        {"keys": ("斜方", "溜肩"),
         "name_zh": "正常训练不会把斜方练得过大",
         "summary": "斜方参与推举/划船等复合动作，但只有大重量孤立刺激叠加增肌"
                    "负荷才会明显变大；'越练越大'多为耸肩/体态错觉，先检查发力姿势。"},
        {"keys": ("晚上", "八点", "夜宵"), "name_zh": "晚上吃不是直接长膘",
         "summary": "总摄入＞消耗才会胖，和几点吃关系不大；睡前进食主要影响睡眠和消化。"},
        {"keys": ("30分钟", "黄金窗口"), "name_zh": "不存在苛刻的30分钟黄金窗口",
         "summary": "练后及时吃够蛋白质有益恢复，但并非超过30分钟就白练；全天总蛋白更关键。"},
        {"keys": ("空腹有氧",), "name_zh": "空腹有氧并不减脂翻倍",
         "summary": "空腹运动时脂肪供能比例略升，但全天总热量消耗相近；易低血糖者不建议空腹大强度。"},
        {"keys": ("女生练胸", "越练越小"), "name_zh": "女生练胸不会越练越小",
         "summary": "胸部主要是脂肪组织，练胸强化的是下方胸肌，视觉更挺；掉胸通常是因为减脂。"},
        {"keys": ("膝盖", "脚尖"), "name_zh": "膝盖可超过脚尖",
         "summary": "深蹲膝盖过不过脚尖取决于躯干与腿长比例，不是铁律；关键是重心稳定、无痛。"},
        {"keys": ("筋膜枪"), "name_zh": "筋膜枪不瘦双下巴",
         "summary": "筋膜枪放松肌肉，脂肪堆积不会因震动减少；颈部使用务必避开气管与动脉。"},
        {"keys": ("激素鸡", "激素"), "name_zh": "正规渠道鸡肉没有激素问题",
         "summary": "国家明令禁止养殖添加激素，正规超市/检疫过的鸡肉可正常吃。"},
        {"keys": ("蛋白粉", "肾"), "name_zh": "正常摄入蛋白粉不伤肾",
         "summary": "健康成年人按体重适量补蛋白（约1.6-2.2g/kg）无需担心伤肾；"
                    "已有肾脏疾病者请遵循医嘱。"},
        {"keys": ("拉伸", "长高"), "name_zh": "拉伸不改变骨骼长度",
         "summary": "成年后骨骼定型，拉伸改善的是柔韧与体态，长高基本无望（22岁更无）。"},
        {"keys": ("姨妈期", "例假", "月经"), "name_zh": "经期不必完全停练",
         "summary": "多数人经期可做中低强度训练（如散步/瑜伽），尽量避开腹部大强度与冰冷大重量；"
                    "有明显不适则休息，听身体。"},
        {"keys": ("酸痛", "酸胀", "有点酸", "发酸", "肌肉酸"),
         "name_zh": "运动后酸胀多为延迟性酸痛（DOMS）",
         "summary": "运动后24-72小时的酸胀多为延迟性酸痛，是肌肉适应的正常过程；"
                    "可轻量活动、补水、保证睡眠促恢复。"
                    "若疼痛加重、超72小时不缓解或伴肿胀无力，请停训并咨询医生。"},
    )

    def execute(self, ctx, params) -> SkillResult:
        query = str(params.get("query", "")).strip()
        kind = params.get("kind")
        if kind == "profile":
            return self._profile(ctx, query)          # 档案查询：读会话档案，不检索
        if kind == "memory":
            return self._memory(ctx, query)           # 记忆查询：伤/训练/偏好（只读）
        if kind == "muscle":
            return self._muscle(ctx, query, str(params.get("part", "")))
        # W3 辟谣知识块：谣言/智商税类问法优先命中，直接给结论（不落空检索）
        myth = self._myth_hit(query)
        if myth:
            return SkillResult(ok=True, data={"items": [myth], "myth": True},
                               provenance=["qa#myth_kb"])
        # W3 R6 兜底：复合目标句（"倒三角+腹肌+半马"）意图可能落 qa（无强信号）→
        # 委托 teach 的复合目标拆解块，避免空检索
        from app.skills.teach_skill import TeachSkill
        goal = TeachSkill._goal_hit(query)
        if goal:
            return SkillResult(ok=True,
                               data={"items": [{"kind": "复合目标编排", **goal}]},
                               provenance=["qa#goal_kb"])
        if any(k in query for k in self._MEAL_KW):
            return self._meal_boundary(ctx, query)
        ex, fr = repos()
        if kind == "food" or (kind != "exercise" and self._sounds_food(query)):
            res = self._foods(ctx, fr, query)
        else:
            res = self._exercises(ctx, ex, query)
        # ⚠ 实测（2026-09-14，data/rag_eval 的 37 条标注）：
        #   **知识块召回 0/37**，这条路径实际上从未触发。
        # 原因是两个门互斥：含科学关键词（_SCIENCE_KW）的 10 条 query **全部**
        # `items` 为空（问的是知识，命不中动作库）；而 `items` 非空的 7 条
        # **全部**不含科学关键词。于是 `_rag_science` 一次都没被调到。
        # 另有 MIN_SCORE=0.55 一道：像 "RPE 是什么意思" 这种问法 top1 只有 0.50，
        # 即使调到了也会被门槛挡回（该块其实完全对题）。
        #
        # **暂不放开**：门槛不放宽时实测证据准只有 0.68（32% 是错证），而放宽
        # 词法/语义门会让这些错证直接出现在用户面前——按本项目对 RAG 的定位
        # （错证据比没有证据更糟），沉默优于答错。要放开请先提升检索质量
        # （rerank / hybrid），不是改这里的条件。
        # 详见 docs/SDD/2026-09-14-variant-family-audit.md 同批的 RAG 评估结论。
        if res.ok and res.data.get("items"):
            self._rag_science(res.data["items"], query)
        # 概念/术语题：库内**未命中**才走通识分支（data_kind=knowledge，渲染侧按
        # 通识作答并标注"通用训练知识，非库内收录"）。命中即照常返库内数据。
        if (kind == "concept" and res.ok
                and not (res.data or {}).get("items")):
            return SkillResult(
                ok=True,
                data={"items": [], "empty": True, "data_kind": "knowledge",
                      "reason": "概念/术语类问题，库内无对应条目"},
                provenance=["qa#concept"])
        return res

    @classmethod
    def _myth_hit(cls, query: str) -> dict | None:
        """谣言/智商税知识块命中（具体条目在前，泛化兜底判断在后）。"""
        for kb in cls._MYTH_KB:
            if any(k in query for k in kb["keys"]):
                return {"name": "健身辟谣", "name_zh": kb["name_zh"],
                        "summary": kb["summary"]}
        return None

    def _profile(self, ctx, query: str = "") -> SkillResult:
        """档案查询（"我的身体数据/肌肉量多少"）：直接回答会话档案，未建档如实提示；
        W1 增强：问到档案未记录的身体指标 → 如实说明未记录 + 列出已记录项，绝不硬编。
        E2E-01 增强：时间问法（"上个月体重多少"）→ 记忆图谱 as_of 历史（静默回落）。"""
        when = _as_of_hint(query)
        if when:
            hist = self._profile_as_of(
                when, getattr(getattr(ctx, "session", None), "user_id", "local"))
            if hist:
                return SkillResult(ok=True, data={"items": hist},
                                   provenance=["qa#memory.as_of"])
        p = dict(getattr(ctx, "profile", None) or {})
        if not p:
            # `need_profile`：给前端的**结构化**信号 —— 收到就把建档窗口弹出来。
            # 不靠匹配下面那句中文：文案一改前端就静默失效，而且那种失败
            # 看起来只是"自动弹窗不好使了"，没人会想到是措辞变了。
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "data_kind": "data",
                                              "need_profile": True,
                                              "reason": "尚未建立身体档案，请先建档"},
                               provenance=["qa#session.profile"])
        label = {"name": "称呼", "sex": "性别", "age": "年龄",
                 "height_cm": "身高cm", "weight_kg": "体重kg",
                 "goal": "目标", "activity": "活动系数"}
        items = [{"name": zh, "value": p[k]} for k, zh in label.items() if k in p]
        # W1：档案未记录指标 → 如实说明（如 FFMI/体脂/训练年限需体测仪数据）
        missing = _PROFILE_MISSING_HINT.get(_detect_profile_field(query))
        if missing:
            items.append({"name": "档案未记录", "value": missing})
        return SkillResult(ok=True, data={"items": items, "profile": p},
                           provenance=["qa#session.profile"])

    def _muscle(self, ctx, query: str, part: str) -> SkillResult:
        """肌肉状态面板：训练频率/伤情/偏好 聚合如实列示（只读）。"""
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        try:
            from app.graph.memory import MemoryStore, _PART2MUSCLE
            m = MemoryStore.get()
        except Exception:
            m = None
        if m is None:
            return SkillResult(ok=True, data={"items": [{
                "name": "记忆", "value": "记忆功能暂不可用（图谱离线）"}],
                "empty": True, "data_kind": "data"},
                provenance=["qa#memory.offline"])
        muscle = _PART2MUSCLE.get(part, part)
        s = m.muscle_summary(uid, muscle, part=part)
        # 恢复度（2026-09-11）：肌肉状态图的数据源。**编排参考，不是生理测量**——
        # 措辞由 recovery.describe 单源控制（含"约"与非精确口吻、低置信标注）。
        rec_txt = ""
        try:
            import recovery as _rec
            from datetime import datetime, timezone
            st = _rec.recovery_map(m.muscle_load_map(uid),
                                   datetime.now(timezone.utc)).get(muscle)
            rec_txt = _rec.describe(muscle, st)
        except Exception:
            rec_txt = ""
        if not s["trained_count"] and not s["active_injury"] and not s["preference"]:
            val = "这块肌肉还没有记录——打卡训练或聊聊偏好后再问我"
        else:
            last = str(s["last_trained"] or "无")[:10]
            val = (f"近30天训练 {s['trained_count']} 次；最近：{last}；"
                   f"伤情：{'、'.join(s['active_injury']) or '无'}；"
                   f"偏好：{s['preference'] or '无'}")
        if rec_txt:
            val += f"；恢复：{rec_txt}"
        return SkillResult(ok=True, data={"items": [
            {"name": f"{part}（{muscle}）面板", "value": val}],
            "recovery": rec_txt},
            provenance=["qa#muscle_panel"])

    def _memory(self, ctx, query: str) -> SkillResult:
        """记忆查询（伤/训练/偏好）：读图谱如实列示；空态/离线诚实声明。"""
        uid = getattr(getattr(ctx, "session", None), "user_id", "local")
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
        except Exception:
            m = None
        if m is None:
            return SkillResult(
                ok=True, data={"items": [{"name": "记忆",
                                          "value": "记忆功能暂不可用（图谱离线）"}],
                               "empty": True, "data_kind": "data"},
                provenance=["qa#memory.offline"])
        items = []
        try:
            if any(k in query for k in ("伤", "病")):
                rows = m.current(uid, "injury")
                if rows:
                    items += [{"name": "受伤记录",
                               "value": f"{r.get('about') or r['value']}"
                                        f"（{str(r.get('valid_from', ''))[:10]}起）"}
                              for r in rows]
                else:
                    items.append({"name": "受伤记录", "value": "暂无"})
            if any(k in query for k in ("训练", "练", "打卡", "日志")):
                evs = m.events(uid, "checkin")
                if evs:
                    for e in evs[:5]:
                        its = e.get("payload", {}).get("items", [])
                        seg = "、".join(
                            (i.get("name") or i.get("raw") or "")
                            + (f"{i['sets']}x{i['reps']}"
                               if i.get("sets") and i.get("reps") else "")
                            for i in its) or e.get("payload", {}).get("about", "")
                        items.append({"name": "训练记录",
                                      "value": f"{str(e.get('occurred_at', ''))[:10]}"
                                               f" {seg}"})
                else:
                    items.append({"name": "训练记录", "value": "暂无"})
            if any(k in query for k in ("偏好", "喜欢", "喜好")):
                prefs = m.current(uid, "preference")
                if prefs:
                    items += [{"name": "偏好",
                               "value": f"{r['value']}{r.get('about') or ''}"}
                              for r in prefs]
                else:
                    items.append({"name": "偏好", "value": "暂无"})
            if not items:
                items = [{"name": "记忆",
                          "value": "可问我：受伤记录 / 训练记录 / 我的偏好"}]
        except Exception:
            items = [{"name": "记忆", "value": "记忆读取异常，请稍后再试"}]
        return SkillResult(ok=True, data={"items": items},
                           provenance=["qa#memory"])

    @staticmethod
    def _diet_prefs(ctx) -> str:
        """记忆图谱通用/饮食偏好（about 为空）→ '、'拼接；无图/无偏好/异常 → ''。"""
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                return ""
            uid = getattr(getattr(ctx, "session", None), "user_id", "local")
            vals = [str(r["value"]) for r in m.current(uid, "preference")
                    if not r.get("about")]
            return "、".join(vals)
        except Exception:
            return ""

    def _meal_boundary(self, ctx, query: str) -> SkillResult:
        """配餐/食谱组合请求：诚实能力边界 + 偏好引用 + 替代引导（不检索）。"""
        items = []
        pref = self._diet_prefs(ctx)
        if pref:
            items.append({"name": "你的饮食偏好", "value": pref})
        items.append({"name": "能力边界",
                      "value": "我暂时没有按天配餐/出食谱的能力"})
        items.append({"name": "替代",
                      "value": "可查单个食材每100g营养（如'鸡胸肉蛋白质多少'），"
                               "或说'帮我制定一日减脂计划'出训练方案"})
        return SkillResult(ok=True, data={"items": items, "boundary": True},
                           provenance=["qa#meal_boundary"])

    @staticmethod
    def _profile_as_of(when: str, user_id: str = "local") -> list:
        """记忆图谱 as-of 档案历史（profile.*）；无图谱/异常 → []（回落现状）。"""
        try:
            from app.graph.memory import MemoryStore
            m = MemoryStore.get()
            if m is None:
                return []
            out = []
            for r in m.as_of(user_id, when):
                t = str(r["type"])
                if not t.startswith("profile."):
                    continue
                key = t.split(".", 1)[1]
                try:
                    v = json.loads(r["value"])
                except Exception:
                    v = r["value"]
                out.append({"name": key, "value": v})
            return out
        except Exception:
            return []

    def _foods(self, ctx, fr, query: str) -> SkillResult:
        items = []
        # 整句无命中时，回退到抽取具体食物词（"鸡胸肉蛋白质多少"→"鸡胸"），
        # 优于泛化的营养素词"蛋白质"
        search = query
        for _k, _v in self._FOOD_ALIASES.items():   # 口语词归一（子串替换）
            if _k in search:
                search = search.replace(_k, _v)
                break
        if not fr.search(search, limit=1):
            kw = sorted((k for k in self._FOOD_CONCRETE if k in search),
                        key=len, reverse=True)
            if kw:
                search = kw[0]
        # W5：图谱 approved 别名优先（"蛋白质粉"→"蛋白粉"）
        from app.core.graphalias import graph_alias_for
        search = graph_alias_for(search, "food") or search
        for f in fr.search(search, limit=5):
            p = f.get("per_100g") or {}
            items.append({
                "name": f.get("name_zh") or f.get("name"),
                "per_100g": {"calories_kcal": p.get("calories_kcal"),
                             "protein_g": p.get("protein_g"),
                             "fat_g": p.get("fat_g"),
                             "carbs_g": p.get("carbs_g")},
                "source": f.get("source"),
            })
        if not items:
            # W3 兜底：空结果 → LLM 归一化改写 → 重检索 → 写回图谱
            adopted, hits = self._normalize_fallback(ctx, query, "food",
                                                     lambda q: fr.search(q, limit=5))
            if adopted:
                for f in hits:
                    p = f.get("per_100g") or {}
                    items.append({
                        "name": f.get("name_zh") or f.get("name"),
                        "per_100g": {"calories_kcal": p.get("calories_kcal"),
                                     "protein_g": p.get("protein_g"),
                                     "fat_g": p.get("fat_g"),
                                     "carbs_g": p.get("carbs_g")},
                        "source": f.get("source"),
                    })
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "data_kind": "data",
                                              "reason": "未检索到食物"},
                               provenance=["qa#foods_repo.search"])
        pref = self._diet_prefs(ctx)
        if pref:
            items.insert(0, {"name": "你的饮食偏好", "value": pref})
        src = [f"{it['source']}:{i}{it['name']}" for i, it in enumerate(items)]
        return SkillResult(ok=True, data={"items": items},
                           provenance=src[:3])

    @staticmethod
    def _normalize_fallback(ctx, query: str, purpose: str, retry):
        """空结果兜底：LLM 归一化 → 重检索 → 写回图谱 + audit 留痕。
        无 LLM/校验失败 → (False, [])，调用方走原 empty 分支（行为不变）。"""
        try:
            from app.core.llm import build_provider
            from app.core.normalize import Normalizer
            adopted, hits = Normalizer(build_provider()).attempt(
                ctx, query, purpose, retry)
            return adopted, hits
        except Exception:
            return False, []

    # 省略式追问：这几个字单独拿去检索**必然为空**（实测回"没有找到相关内容。
    # 可以换个关键词试试，比如[进阶 深蹲]"），可它的意思再清楚不过 ——
    # 接着刚才那批往下要。触发口径**窄**（与 `nodes._affirm_plan_intent` 同一取向）：
    # 只认明确表达"还要/更难"的说法，且只在这句**自己不带走题**时才补全。
    _FOLLOWUP_KW = ("更进阶", "进阶的", "进阶一点", "更难", "难一点", "难度更高",
                    "挑战一点", "高级一点", "高阶", "还有吗", "还有别的",
                    "还有没有", "还有其它", "还有其他", "再来几个", "再来点",
                    "再来一些", "多来几个", "换几个", "其他的呢", "别的呢")

    def _followup(self, ctx, ex, query: str) -> SkillResult | None:
        """省略式追问 → 用上一轮的主题补全再检索。返回 None = 不适用，交回原逻辑。

        复现（2026-09-15 用户报）：上一轮「推荐几个练腿动作」→ 这一轮
        「有没有更进阶的」→ 拿这七个字去检索必然为空 → 回「没有找到相关内容。
        可以换个关键词试试」。用户想说的是"在刚才那批的基础上要更难的"，
        系统却把两轮当成两件不相干的事。

        **两个前提缺一不可**，否则宁可不接：
          · 这句**自己不带主题**。「推荐更进阶的练腿动作」自带部位，直接检索就行；
            走这条路反而会把它改写成"上一轮的主题"，那是错的。
          · 历史里**找得到主题**。找不到就交回原逻辑 —— 那时回"没有找到"是诚实的。
        """
        q = (query or "").strip()
        if not any(k in q for k in self._FOLLOWUP_KW):
            return None
        from lib.parts import PART_CHARS, PART_WORDS
        if any(c in q for c in PART_CHARS) or any(w in q for w in PART_WORDS):
            return None                       # 自带主题 → 不是省略式追问
        muscle = self._last_topic(getattr(getattr(ctx, "session", None), "history", None))
        if muscle is None:
            return None
        try:
            rows = ex.filter(muscle=muscle)
        except Exception:
            return None                       # 降级：交回原逻辑，不抛
        pool = self._pool(rows)
        if not pool:
            return None
        if level is not None:
            return self._pick_level(ctx, pool, level, muscle)
        return self._pick_harder(ctx, pool,
                                 hardest=any(k in q for k in self._HARDEST_KW))

    def _followup_llm(self, ctx, ex, query: str) -> SkillResult | None:
        """词表没接住的省略式追问 → LLM 结合历史判断（**第二层兜底**）。

        分层路由（用户口径）：词表只维护常用词（零成本、确定性高），词表 miss
        时交给 LLM 覆盖长尾——「有没有适合女生的」「家里没器械练什么」这类说法
        词表永远追不全，而它们与「有没有更进阶的」是同一件事。
        三层护栏：
          1. 只在**字面检索为空后**调用 —— 正常路径零额外 LLM 成本；
          2. LLM 不可用 / 判定非追问 / 部位无效 → 返回 None 交回原逻辑，
             那时回「没有找到」是诚实的；
          3. 部位词经 `muscle_of` 收敛到 canonical 肌群，选取复用词表层的
             `_pick_harder` / `_pick_level` —— **LLM 只出意图，动作仍由代码出**
             （与 `guard` 的记忆交叉同一分工）。
        """
        try:
            from app.core.llm import build_provider
            verdict = build_provider().resolve_followup(
                query, getattr(getattr(ctx, "session", None), "history", None))
        except Exception:
            return None
        if not verdict or not verdict.get("is_followup"):
            return None
        part = verdict.get("part")
        from lib.parts import muscle_of
        muscle = muscle_of(part) if part else None
        if muscle is None:
            return None                        # 部位词不在表内 → 不猜
        try:
            pool = self._pool(ex.filter(muscle=muscle))
        except Exception:
            return None
        if not pool:
            return None
        kind = verdict.get("kind") or "advanced"
        # 判定留痕（学 Normalizer.attempt）：哪句话走了 LLM 兜底、判成什么，
        # 出问题可回溯 —— 词表层命中天然可见（走哪条路），LLM 层不然。
        if ctx is not None:
            try:
                ctx.skill_log.append({"event": "followup_llm",
                                      "query": query, "part": part, "kind": kind})
            except Exception:
                pass
        # 「再来几个/别的」与「更进阶」的检索口径一致：都是"该部位再来一批"，
        # 难度降序保证优先给质量高的 —— 所以 more 与 advanced 同一条选取路。
        if kind == "easier":
            return self._pick_level(ctx, pool, 1, muscle)
        if kind == "mid":
            return self._pick_level(ctx, pool, 2, muscle)
        return self._pick_harder(ctx, pool, hardest=(kind == "hardest"))

    @classmethod
    def _level_of(cls, q: str) -> int | None:
        """难度档位词 → difficulty（1/2）；没提档位返回 None。

        口径同样窄：只有**明确说出一档**才算。3（高级）不在此列 —— "高级/更进阶"
        本来就走 `_FOLLOWUP_KW` 那条"按难度降序"的路，同一件事不开第二条实现。
        """
        for lv, words in cls._LEVEL_KW:
            if any(w in q for w in words):
                return lv
        return None

    @staticmethod
    def _diff(r: dict) -> int:
        try:
            return int(r.get("difficulty") or 0)
        except Exception:
            return 0

    @classmethod
    def _pool(cls, rows: list[dict]) -> list[dict]:
        """某肌群的候选池：去拉伸/柔韧 + 变体去重 + 难度降序。

        **直接 filter 全量，不能用 recommend(count=N) 再排序** —— recommend 内部
        按"难度升序"凑数（先给简单的），count=8 时高难度动作根本进不了候选，
        "更进阶"就变成"又来一遍最简单的"（首轮实测正中此坑）。

        变体去重（family 优先，无 family 用小写名）—— 否则同一动作的 5 个变体
        会把名额占满，用户看到的是"同一个动作的复制粘贴"。
        """
        # 排除拉伸/柔韧（同 recommend 的默认口径）：问"更进阶/最难"问的是训练动作
        rows = [r for r in rows if r.get("exercise_type") != "stretch_mobility"]
        seen, uniq = set(), []
        for r in rows:
            key = r.get("family") or str(r.get("name")).lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(r)

        def _diff(r: dict) -> int:
            try:
                return int(r.get("difficulty") or 0)
            except Exception:
                return 0

        rows = sorted(uniq, key=_diff, reverse=True)[:5]
        items = [self._exercise_item(r) for r in rows]
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"ex:{it['id']}" for it in items])

    @staticmethod
    def _last_topic(history) -> str | None:
        """最近一条**用户**消息里的部位词 → 肌群 id；找不到返回 None。

        **倒着回溯而不是只看最后一条**：连着追问两轮时（"有没有更进阶的" →
        "还有吗"），最后一条用户消息里同样没有主题 —— 一路回溯到真正说了
        部位的那轮才对。
        """
        from lib.parts import PART_CHARS, PART_WORDS, muscle_of
        for h in reversed(history or []):
            if not isinstance(h, dict) or h.get("role") != "user":
                continue
            text = str(h.get("text") or "")
            for w in sorted(PART_WORDS, key=len, reverse=True):
                if w in text:
                    m = muscle_of(w)
                    if m:
                        return m
            for c in PART_CHARS:
                if c in text:
                    m = muscle_of(c)
                    if m:
                        return m
        return None

    @staticmethod
    def _exercise_item(e: dict) -> dict:
        """检索命中 → 卡片条目。

        **正常检索与归一化兜底两条路共用这一个。** 原先各自拼一份、字段集靠人
        维护一致 —— 那正是 `clarify_exercise` 与 `/v1/checkin/resolve` 两处组装
        漂移过的同一个坑（漏字段不报错，只是界面上少个东西）。新增字段只改这里。

        `image` / `gif_url` 是**相对 `data/exercises-dataset/` 的路径**，前端拼
        `/media` 前缀。库内 1324 条 100% 有这两个字段；不带的话卡片只剩光秃秃的
        名字，而动作名是逐词直译的（"摆臂 悬垂 直腿s"），光看名字挑不出是哪一个。
        ⚠ 素材 © Gym visual：授权只到 180×180，且**每次使用都要带署名**。
        """
        sug = e.get("suggested") or {}
        return {"id": e["id"], "name_zh": e.get("name_zh"),
                "difficulty": e.get("difficulty"),
                "pattern": e.get("movement_pattern"),
                "equipment": e.get("normalized_equipment"),
                "sets": sug.get("sets"), "reps": sug.get("reps"),
                "rest_sec": sug.get("rest_sec"),
                "image": e.get("image"), "gif_url": e.get("gif_url")}

    def _exercises(self, ctx, ex, query: str) -> SkillResult:
        # 中文检索已下沉 exercise_repo.search_zh（qa/teach 单源）：
        # 复合切分+修饰剥离+别名归一+双向匹配，装配期预计算归一名。
        # W5：图谱 approved 别名优先（静态 NAME_ALIASES 退居降级位）
        from app.core.graphalias import graph_alias_for
        q = graph_alias_for(query, "exercise") or query
        # 省略式追问先补全（"有没有更进阶的"）—— 补不上就原样检索，
        # 那时的空结果是诚实的。见 _followup
        fu = self._followup(ctx, ex, q)
        if fu is not None:
            return fu
        matched = ex.search_zh(q, limit=5, part_fallback=True)
        items = [self._exercise_item(e) for e in matched]
        if not items:
            # 词表没接住的省略式追问 → LLM 结合历史判断（第二层）。
            # **只在字面检索为空后调**：正常路径零 LLM 成本，且"按字面查不到"
            # 正是省略式追问的典型形态 —— 有主题的句子走不到这里。
            fu = self._followup_llm(ctx, ex, query)
            if fu is not None:
                return fu
            # W3 兜底：空结果 → LLM 归一化改写 → 重检索 → 写回图谱
            adopted, hits = self._normalize_fallback(
                ctx, query, "exercise",
                lambda q: ex.search_zh(q, limit=5, part_fallback=True))
            if adopted:
                items = [self._exercise_item(e) for e in hits]
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "data_kind": "data",
                                              "reason": "未检索到动作"},
                               provenance=["qa#exercise_repo.search"])
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"ex:{it['id']}" for it in items])

    def _rag_science(self, items: list, query: str) -> None:
        """科学/医学语境 → 追加 top1 science_doc 知识块。全 try/except 静默降级。

        2026-09-14 修正：
        1. 传 min_score —— 此前只有 LIMIT 1，无论像不像都必返回一条，等于把随机
           知识块当证据。
        2. pending_review 如实透传 —— 此前硬编码 True，永远不反映实际审核状态。
        3. 正文键用 `value` 而非 `content`，并补 `source` / `source_ref`。
           此前是 `{"name": "知识块(RAG)", "content": ...}`，而
           `render_util.item_lines` 只认 name/name_zh/value —— **正文整段被丢掉**，
           离线渲染出来只有一行 `· 知识块(RAG)`；出处（source_ref）也在这一层丢。"""
        if not any(k in query for k in QaSkill._SCIENCE_KW):
            return
        try:
            from app.rag import retriever
            from app.rag.store import PgStore
            from app.rag.embedder import OllamaEmbedder
            store, embedder = PgStore(), OllamaEmbedder()
            qv = retriever.embed_query(embedder, query)
            hits = retriever.vector_search(
                store, qv, getattr(embedder, "model", "bge-m3"),
                top_k=1, chunk_types=("science_doc",),
                min_score=retriever.MIN_SCORE)
        except Exception:
            return
        if not hits:
            return
        sr = hits[0].get("source_ref") or {}
        # 出处：优先语料写入时的可读名（source_ref.title），无则退回 id ——
        # 老库未重建时 title 缺失，不能因此渲染成空
        items.append({"name": "知识块(RAG)", "value": hits[0]["content"],
                      "source": sr.get("title") or sr.get("id") or None,
                      "source_ref": sr,
                      "pending_review": hits[0]["pending_review"],
                      "rag": True})

    @staticmethod
    def _sounds_food(q: str) -> bool:
        return any(k in q for k in QaSkill._FOOD_KW)

    # 可识别为"食物提问"的全部触发词；其中 _FOOD_CONCRETE 为具体食材词，
    # 用于无命中时的搜索回退（优于"蛋白质"这类泛化营养素词）
    _FOOD_KW = ("鸡胸", "鸡蛋", "牛奶", "牛肉", "鱼", "米饭", "蛋白质",
                "碳水", "脂肪", "卡路里", "热量", "食物",
                # 2026-09-08 个性化：饮食组合/餐次词落 foods 分支（不再误落动作）
                "饮食", "早餐", "午餐", "晚餐", "加餐", "吃什么", "吃啥", "吃点")
    _MEAL_KW = ("食谱", "餐单", "配餐", "三餐")   # 组合配餐请求 → 能力边界
    _FOOD_CONCRETE = ("鸡胸", "鸡蛋", "牛奶", "牛肉", "鱼", "米饭",
                      "虾仁", "虾", "豆腐", "红薯", "燕麦", "香蕉", "苹果",
                      "牛油果", "三文鱼", "豆浆", "面条", "鸡腿", "酸奶",
                      "蛋白粉",
                      "乳清")
    _FOOD_ALIASES = {"蛋白质粉": "蛋白粉"}   # 口语词↔库内词