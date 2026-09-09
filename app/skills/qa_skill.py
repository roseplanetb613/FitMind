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
    task_types = ("qa", "fallback")

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
        if res.ok and res.data.get("items"):
            self._rag_science(res.data["items"], query)
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
            return SkillResult(ok=True, data={"items": [], "empty": True,
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
                               "empty": True},
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

    def _exercises(self, ctx, ex, query: str) -> SkillResult:
        # 中文检索已下沉 exercise_repo.search_zh（qa/teach 单源）：
        # 复合切分+修饰剥离+别名归一+双向匹配，装配期预计算归一名。
        # W5：图谱 approved 别名优先（静态 NAME_ALIASES 退居降级位）
        from app.core.graphalias import graph_alias_for
        q = graph_alias_for(query, "exercise") or query
        matched = ex.search_zh(q, limit=5)
        items = []
        for e in matched:
            sug = e.get("suggested") or {}
            items.append({"id": e["id"], "name_zh": e.get("name_zh"),
                          "difficulty": e.get("difficulty"),
                          "pattern": e.get("movement_pattern"),
                          "equipment": e.get("normalized_equipment"),
                          "sets": sug.get("sets"), "reps": sug.get("reps"),
                          "rest_sec": sug.get("rest_sec")})
        if not items:
            # W3 兜底：空结果 → LLM 归一化改写 → 重检索 → 写回图谱
            adopted, hits = self._normalize_fallback(
                ctx, query, "exercise",
                lambda q: ex.search_zh(q, limit=5))
            if adopted:
                for e in hits:
                    sug = e.get("suggested") or {}
                    items.append({"id": e["id"], "name_zh": e.get("name_zh"),
                                  "difficulty": e.get("difficulty"),
                                  "pattern": e.get("movement_pattern"),
                                  "equipment": e.get("normalized_equipment"),
                                  "sets": sug.get("sets"), "reps": sug.get("reps"),
                                  "rest_sec": sug.get("rest_sec")})
        if not items:
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "reason": "未检索到动作"},
                               provenance=["qa#exercise_repo.search"])
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"ex:{it['id']}" for it in items])

    def _rag_science(self, items: list, query: str) -> None:
        """科学/医学语境 → 追加 top1 science_doc 知识块。全 try/except 静默降级。"""
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
                top_k=1, chunk_types=("science_doc",))
        except Exception:
            return
        if not hits:
            return
        items.append({"name": "知识块(RAG)", "content": hits[0]["content"],
                      "pending_review": True, "rag": True})

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