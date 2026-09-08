# -*- coding: utf-8 -*-
"""动作教学：检索动作+处方组次+器械要求（要领文本 RAG 补强，全静默降级）。"""
from __future__ import annotations
from app.skills.base import Skill, SkillResult


def _repo():
    """共享单例（app.runtime.repos）：避免与 pipeline/qa 各自装配。"""
    from app.runtime.repos import exercise_repo
    return exercise_repo()


class TeachSkill(Skill):
    name = "teach"
    description = "动作要领：目标肌群/组次/休息/器械（RAG 扩展位）"
    task_types = ("teach", "fallback")

    # 编排模式知识块（CLI+压测 G3 实证：teach 路由正确但动作库无编排内容）。
    # 属业务层规则知识，不进数据包；具体条目在前，"分化"兜底在后。
    _SPLIT_KB = (
        {"keys": ("练三休一",),
         "name_zh": "练三休一（3 练 1 休循环）",
         "summary": "连续训练 3 天、休息 1 天，4 天一个循环；每个肌群约每 4 天"
                    "刺激一次，训练频率与恢复较平衡。",
         "example": "Day1 推（胸·肩·三头）→ Day2 拉（背·二头）→ "
                    "Day3 腿+核心 → Day4 休息，循环往复。",
         "note": "适合初中级；第 3 个训练日避免极限重量，注意疲劳管理。"},
        {"keys": ("练二休一",),
         "name_zh": "练二休一（2 练 1 休循环）",
         "summary": "连续训练 2 天、休息 1 天，3 天一个循环；频率更高，"
                    "适合时间充裕、恢复能力好的训练者。",
         "example": "Day1 上肢 → Day2 下肢 → Day3 休息，循环往复。",
         "note": "单肌群约每 3 天刺激一次；注意睡眠与蛋白质摄入跟上了再加量。"},
        {"keys": ("推拉腿", "推拉"),
         "name_zh": "推拉腿分化（PPL）",
         "summary": "把训练分为推日（胸·肩·三头）、拉日（背·二头）、腿日"
                    "三天一轮，部位分化均衡，是中级最常用的编排之一。",
         "example": "练三休一：推→拉→腿→休；或练六休一：推拉腿×2→休。",
         "note": "腿日负荷大，前后注意给膝关节充分热身与恢复。"},
        {"keys": ("上下肢", "上下分化"),
         "name_zh": "上下肢分化",
         "summary": "上肢日与下肢日交替，每个部位每周约练 2 次，"
                    "兼顾训练频率与恢复，适合初/中级力量增长。",
         "example": "上肢→下肢→休→上肢→下肢→休→休，一周循环。",
         "note": "上肢日可同时覆盖推与拉；下肢日注意深蹲/硬拉二选一为主项。"},
        {"keys": ("全身分化", "全身训练"),
         "name_zh": "全身分化（每次练全身）",
         "summary": "每次训练覆盖全身主要肌群，每周 2~3 次、隔天进行；"
                    "单肌群频率高、单次量小，是新手入门首选编排。",
         "example": "每次：深蹲/腿举 + 卧推 + 划船 + 核心，隔天一次。",
         "note": "每次选 4~6 个复合动作即可，避免单堂训练过长。"},
        {"keys": ("分化",),
         "name_zh": "分化训练（通用编排）",
         "summary": "分化 = 把不同肌群分到不同训练日，让单肌群获得足够刺激"
                    "与恢复。常见有全身分化、上下肢、推拉腿、五分化等。",
         "example": "新手从全身分化起步；中级常用推拉腿或上下肢；"
                    "高阶再用五分化（胸/背/腿/肩/手臂）。",
         "note": "分化越细，单肌群训练频率越低，需匹配自己的恢复能力。"},
    )

    # W3 场景知识块（办公室/徒手/哑铃/酒店等限定器械与场景的编排建议）。
    # 属业务层规则知识，不进数据包；命中返回编排建议，不依赖动作库收录。
    _SCENE_KB = (
        {"keys": ("办公室", "工位", "久坐", "上班", "办公"),
         "name_zh": "办公室/工位锻炼",
         "summary": "久坐每 40-60 分钟起身活动 2-3 分钟；工位可做靠墙静蹲、"
                    "桌面俯卧撑、坐姿抬腿、肩颈环绕。强度低，主要作用是打断久坐。",
         "example": "靠墙静蹲 30-60s ×3、桌面俯卧撑 10-15 ×3、坐姿腿举抬腿 15 ×3。",
         "note": "工位空间有限以自重/等长动作为主；别影响同事与工作。"},
        {"keys": ("徒手", "自重", "没器械", "无器械", "空手"),
         "name_zh": "纯徒手练背/全身",
         "summary": "徒手练背用引体向上或澳式引体（凳下反手划船）；全身可做"
                    "俯卧撑、深蹲、弓步、臀桥、平板支撑组合，每周 3 次隔天练。",
         "example": "引体/澳式引体 3-4 组 + 俯卧撑 3 组 + 深蹲 3 组 + 平板 3 组。",
         "note": "徒手进阶靠增加次数/组数/单腿动作提高难度。"},
        {"keys": ("一对哑铃", "就一对哑铃", "哑铃", "只有哑铃"),
         "name_zh": "一对哑铃练全身",
         "summary": "一对哑铃可覆盖全身：哑铃卧推/划船/肩推/深蹲/箭步/硬拉/"
                    "弯举，按推拉腿安排，胸背腿各每天 4-5 个动作。",
         "example": "推日：哑铃卧推+肩推+飞鸟；拉日：哑铃划船+硬拉+弯举；"
                    "腿日：哑铃深蹲+箭步+单腿硬拉。",
         "note": "重量有限时优先高次数（10-15RM）和渐进加组数。"},
        {"keys": ("酒店", "出差", "商务"),
         "name_zh": "酒店健身房（仅跑步机/哑铃）训练",
         "summary": "出差期以维持为目标的全身编排：跑步机快走/坡度走热身，"
                    "哑铃推举/划船/深蹲/硬拉各 3 组，控制强度防止疲劳累积。",
         "example": "坡度走 10 分钟 + 哑铃肩推/划船/深蹲/单腿硬拉 各 3 组。",
         "note": "出差不用冲量，保持习惯与恢复优先。"},
        {"keys": ("恢复周", "减载", "休息周"),
         "name_zh": "恢复周/减载周",
         "summary": "每 4-6 周安排 1 周减载：训练量降 40-50%，强度维持或略降，"
                    "让关节与神经系统恢复，为下一周期蓄力。",
         "example": "平时 10 组变 5-6 组；大重量日改为中等重量高次数或技术日。",
         "note": "减载不是完全停练；继续轻负荷保持动作熟练度。"},
        {"keys": ("停训", "半年", "一个月", "很久没练", "重新开始",
                  "摆烂", "半个月", "回来练", "复训"),
         "name_zh": "停训恢复（重回训练）",
         "summary": "停训 2 周以上恢复训练：前 1-2 周用约 60-70% 原强度、"
                    "减量热身，先找回动作与关节适应，再逐步加回重量。",
         "example": "第一周：每个主项 60% 强度 3 组；第二周 75% 4 组；之后逐步恢复。",
         "note": "停训练回力量和肌肉比新手快（肌肉记忆），但别急着追重量。"},
    )

    # W3 多目标编排块（R6：#108 倒三角+腹肌+半马 复合目标）。命中 ≥2 目标词才
    # 触发（防"腹肌怎么练"单目标被吞）；属业务层规则知识，不进数据包。
    _GOAL_KW = ("倒三角", "六块腹肌", "腹肌", "人鱼线", "半马", "马拉松",
                "增肌", "减脂", "耐力")
    _GOAL_KB = (
        {"name_zh": "复合目标拆解（外形+耐力+力量）",
         "summary": "同时练线条（倒三角/腹肌）与耐力（半马）不冲突，但同一天同时"
                    "追大重量与长距离会互相干扰。原则：①目标分层——力量日与有氧日"
                    "错开安排（如周 1/3/5 力量、周 2/4/6 慢跑）；②阶段化——按主目标"
                    "练 4-8 周再切换，别每天全都要；③营养优先保证蛋白质与睡眠。",
         "example": "周1/3/5：推拉腿大重量复合动作；周2/4/6：慢跑 5-8km；周日休息。",
         "note": "复合目标别一天内又冲大重量又跑长距离，恢复优先。"},
    )

    def _split_hit(self, query: str) -> dict | None:
        """编排关键词子串命中（具体条目在前，'分化'通用兜底在最后）。"""
        for kb in self._SPLIT_KB:
            if any(k in query for k in kb["keys"]):
                return kb
        return None

    @classmethod
    def _goal_hit(cls, query: str) -> dict | None:
        """复合目标块：query 命中 ≥2 个目标词 → 返回拆解原则块（qa/teach 共用）。"""
        if sum(1 for k in cls._GOAL_KW if k in query) >= 2:
            return dict(cls._GOAL_KB[0])
        return None

    def _scene_hit(self, query: str) -> dict | None:
        """场景知识块命中（办公室/哑铃/酒店/恢复等限定场景）。"""
        for kb in self._SCENE_KB:
            if any(k in query for k in kb["keys"]):
                return kb
        return None

    def _search(self, query: str, limit: int = 3) -> list:
        # 中文检索统一走 exercise_repo.search_zh（qa/teach 单源，防双写漂移）：
        # 复合切分+修饰剥离+别名归一（"箭步蹲"→"弓步"）+双向匹配+难度排序
        # W5：图谱 approved 别名优先（静态 NAME_ALIASES 退居降级位）
        from app.core.graphalias import graph_alias_for
        q = graph_alias_for(query, "exercise") or query
        return _repo().search_zh(q, limit=limit)

    def execute(self, ctx, params) -> SkillResult:
        query = str(params.get("query", ""))
        kb = self._split_hit(query)          # 编排知识优先于场景/动作库检索
        if kb:
            item = {"kind": "编排知识", "name_zh": kb["name_zh"],
                    "summary": kb["summary"], "example": kb["example"],
                    "note": kb["note"]}
            return SkillResult(ok=True, data={"items": [item]},
                               provenance=["teach#split_kb"])
        if kb is None:
            kb = self._scene_hit(query)      # W3 场景知识（办公室/哑铃/酒店等）
        if kb is None:
            kb = self._goal_hit(query)       # W3 复合目标拆解（R6 #108）
        if kb:
            item = {"kind": "场景编排", "name_zh": kb["name_zh"],
                    "summary": kb["summary"], "example": kb["example"],
                    "note": kb["note"]}
            return SkillResult(ok=True, data={"items": [item]},
                               provenance=["teach#scene_kb"])
        items = []
        for e in self._search(query):
            mus = e.get("muscles_canonical") or {}
            sug = e.get("suggested") or {}
            items.append({
                "id": e["id"],
                "name_zh": e.get("name_zh"),
                "target": mus.get("target"),
                "sets": sug.get("sets"), "reps": sug.get("reps"),
                "rest_sec": sug.get("rest_sec"),
                "equipment": e.get("normalized_equipment"),
                "pattern": e.get("movement_pattern"),
                "cue": f"保持目标肌群发力，按建议 {sug.get('sets')}组×{sug.get('reps')}次，"
                       f"组间休息 {sug.get('rest_sec')}s（通用要领，详见后续 RAG）",
                "rag": False,
            })
        if not items:
            # W3 兜底：空结果 → LLM 归一化改写 → 重检索 → 写回图谱 + audit 留痕
            try:
                from app.core.llm import build_provider
                from app.core.normalize import Normalizer
                adopted, hits = Normalizer(build_provider()).attempt(
                    ctx, query, "exercise",
                    lambda q: self._search(q))
            except Exception:
                adopted, hits = False, []
            if adopted:
                for e in hits:
                    mus = e.get("muscles_canonical") or {}
                    sug = e.get("suggested") or {}
                    items.append({
                        "id": e["id"],
                        "name_zh": e.get("name_zh"),
                        "target": mus.get("target"),
                        "sets": sug.get("sets"), "reps": sug.get("reps"),
                        "rest_sec": sug.get("rest_sec"),
                        "equipment": e.get("normalized_equipment"),
                        "pattern": e.get("movement_pattern"),
                        "cue": f"保持目标肌群发力，按建议 {sug.get('sets')}组×{sug.get('reps')}次，"
                               f"组间休息 {sug.get('rest_sec')}s（通用要领，详见后续 RAG）",
                        "rag": False,
                    })
        if not items:
            # W4：#40 担忧型问法（"斜方肌越练越大"）意图常落 teach（贴近"怎么练"例句）
            # 但动作库无肌群名词 → 兜底查 qa 辟谣块（"越练越大"类担忧），避免空结果
            from app.skills.qa_skill import QaSkill
            myth = QaSkill._myth_hit(query)
            if myth:
                return SkillResult(ok=True, data={"items": [myth], "myth": True},
                                   provenance=["teach#myth_kb"])
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "reason": "动作库未收录，尝试其他说法"},
                               provenance=["teach#exercise_repo.search"])
        self._rag_enrich(ctx, query, items)      # RAG 补强（异常静默，不影响规则结果）
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"teach:{it['name_zh']}" for it in items])

    def _rag_enrich(self, ctx, query: str, items: list) -> None:
        """对前 2 条动作追加 RAG 证据：同族替代（Neo4j）+ 要领向量块（PG）。全 try/except 静默降级。"""
        try:
            from app.rag import retriever
            from app.rag.store import PgStore
            from app.rag.embedder import OllamaEmbedder
            from app.graph.store import GraphStore
            store, embedder = PgStore(), OllamaEmbedder()
            graph = GraphStore.get()
            for it in items[:2]:
                eid = it.get("id")
                if not eid:
                    continue
                ev = {"alternatives": [], "cue": None}
                if graph is not None:
                    try:
                        ev["alternatives"] = retriever.graph_family_alternatives(
                            graph, eid)
                    except Exception:
                        pass
                try:
                    qv = retriever.embed_query(embedder, it.get("name_zh") or query)
                    hits = retriever.vector_search(
                        store, qv, getattr(embedder, "model", "bge-m3"),
                        top_k=1, chunk_types=("exercise_cue",))
                    if hits:
                        ev["cue"] = hits[0]["content"]
                except Exception:
                    pass
                if ev["alternatives"] or ev["cue"]:
                    it["rag_evidence"] = ev
                    it["rag"] = True
        except Exception:
            pass