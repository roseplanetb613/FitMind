# -*- coding: utf-8 -*-
"""动作教学：检索动作+处方组次+器械要求（要领文本 RAG 补强，全静默降级）。"""
from __future__ import annotations
import re
from app.skills.base import Skill, SkillResult

# W3 练休节奏正则泛化：枚举只留分化类，"练X休Y"（练四休一/练五休二…）统一参数化——
# 节奏问题是编排问题，落动作库检索属路由对象错误（必然空结果，P2 根因）。
_SPLIT_CYCLE_RE = re.compile(r"练([一二三四五六日1-7])休([一二三四五六日1-7])")
_CN2NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7,
           "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7}
_NUM2CN = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "日"}

# W2/F2 编排问法（通识型）：编排原理/频率类问题数据包不收录，命中即走通识作答，
# 不谎报"动作库未收录"。刻意不含泛化的"怎么练"——动作类问法（"卡卡罗特怎么练"）
# 落空必须继续如实说没找到（data 型禁虚构不回退）。
_SPLIT_ASK_KW = ("怎么排", "怎么分", "怎么安排", "如何排", "如何分", "如何安排",
                 "几天练", "练几天", "一周练", "每天练", "训练频率", "怎么分配")


def _split_cycle_kb(train: int, rest: int) -> dict:
    """练X休Y → 参数化知识块。频率定位：练/休≥3 偏高，1.5~3 均衡，<1.5 偏恢复。"""
    cycle = train + rest
    ratio = train / rest
    if ratio >= 3:
        pos = "频率偏高，适合时间充裕、恢复能力好的训练者"
    elif ratio >= 1.5:
        pos = "训练频率与恢复较平衡，适合大多数初中级训练者"
    else:
        pos = "频率偏低、恢复充分，适合大重量日较多或恢复偏慢的训练者"
    return {"name_zh": f"练{_NUM2CN[train]}休{_NUM2CN[rest]}（{train} 练 {rest} 休循环）",
            "summary": f"连续训练 {train} 天、休息 {rest} 天，{cycle} 天一个循环；{pos}。",
            "example": f"按分化把训练日排入 {train} 个训练日（如推拉腿/上下肢），"
                       f"之后休息 {rest} 天，循环往复。",
            "note": "循环天数越长单肌群刺激间隔越大，按自己的恢复能力选择。"}


def _repo():
    """共享单例（app.runtime.repos）：避免与 pipeline/qa 各自装配。"""
    from app.runtime.repos import exercise_repo
    return exercise_repo()


def _cue(it: dict) -> str:
    """动作要领文本（本地拼接，零 RAG 往返）。

    2026-09-14：此前这里写占位符"（通用要领，详见后续 RAG）"，指望 _rag_enrich 用
    向量召回把 ingest._exercise_cue() 生成的文本取回来。但那段文本本就只是对动作记录
    的**确定性拼接**，而本函数手里的字段已经够用——绕一圈 Ollama + PG 只是把已知信息
    取回来，代价是每条 query 多一次 embed（60s 超时 ×3 重试），且 Ollama 一挂就永远
    停在占位符上。真正需要图才能得到的"同族替代"仍走 _rag_enrich（Neo4j）。"""
    return (f"{it.get('name_zh')}：{it.get('pattern') or '综合'}模式，"
            f"建议{it.get('sets')}组×{it.get('reps')}次，"
            f"组间休息{it.get('rest_sec')}s，器械{it.get('equipment') or '自重'}。"
            f"保持{it.get('target') or '目标肌群'}发力，避免借力代偿。")


def _out_item(e: dict) -> dict:
    """动作记录 → 对外 item（主路径与归一化兜底路径共用，保证两处字段一致）。"""
    mus = e.get("muscles_canonical") or {}
    sug = e.get("suggested") or {}
    it = {
        "id": e["id"],
        "name_zh": e.get("name_zh"),
        "target": mus.get("target"),
        "sets": sug.get("sets"), "reps": sug.get("reps"),
        "rest_sec": sug.get("rest_sec"),
        "equipment": e.get("normalized_equipment"),
        "pattern": e.get("movement_pattern"),
        "rag": False,
    }
    it["cue"] = _cue(it)
    return it


class TeachSkill(Skill):
    name = "teach"
    description = "动作要领：目标肌群/组次/休息/器械（RAG 扩展位）"
    task_types = ("teach", "fallback")

    # 概念兜底（2026-09-17 收敛后只剩这两条）。
    #
    # 原先这里有 6 条 `_SPLIT_KB`，但其中 4 条（练三休一 / 练二休一 / 推拉腿 /
    # 上下肢）讲的就是 data/split-schemes 里**已经有的方案**——同一个编排两套描述，
    # 而且会漂：代码里"上下肢"写「一周循环含双休」，数据包里是「3 天一个循环」。
    # 更别扭的是 `lib/split_cycle.py` 自称"数据单源"、`scheme_intent` 也早已用那份
    # 数据包判"这是不是编排问法"，于是同一条链路一半读数据包、一半读这里的副本。
    #
    # 现在编排方案的教学文案（summary/example）归数据包，`_split_hit` 只读不写；
    # 留在代码里的只有**数据包没有的概念条目**——它们是解释而非可执行方案，
    # 塞进方案包会污染"哪些方案能排进计划"这件事。
    _CONCEPT_KB = (
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
        """编排命中：**先查数据包（单源），未中再回落概念兜底**。

        顺序刻意：数据包方案在前（用户问"练三休一"必须拿到与计划一致的说法），
        `_CONCEPT_KB` 在后 —— 其中 `分化` 是泛化兜底，放前面会吞掉所有含"分化"的问法。

        数据包侧复用 `lib/split_cycle.find_alias`（同一匹配原语，不另写一份）：
        它按 aliases 做**子串**匹配、且要求别名出现在 query 里，所以单问"分化"
        不会命中"上下肢分化"那条方案，会正确落到下面的概念兜底。
        """
        try:
            import split_cycle
            alias = split_cycle.find_alias(query)
            if alias:
                s = split_cycle.resolve_scheme(alias)
                # summary 缺省（数据包未补字段/内置兜底）→ 视为未命中，继续往下找
                if s.get("summary"):
                    return s
        except Exception:
            pass                     # lib 不可用 → 回落概念兜底（全降级不阻断主链路）
        for kb in self._CONCEPT_KB:
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
        return _repo().search_zh(q, limit=limit,
                                  part_fallback=True)  # 对话检索开部位旁路

    def execute(self, ctx, params) -> SkillResult:
        query = str(params.get("query", ""))
        kb = self._split_hit(query)          # 编排枚举优先（精细文案，回归不变）
        if kb:
            item = {"kind": "编排知识", "name_zh": kb["name_zh"],
                    "summary": kb["summary"], "example": kb["example"],
                    "note": kb["note"]}
            return SkillResult(ok=True, data={"items": [item]},
                               provenance=["teach#split_kb"])
        # W3：练X休Y 参数化兜底（枚举未收录的练休节奏，如练四休一/练五休二）
        m = _SPLIT_CYCLE_RE.search(query)
        if m:
            try:
                item = {"kind": "编排知识",
                        **_split_cycle_kb(_CN2NUM[m.group(1)], _CN2NUM[m.group(2)])}
                return SkillResult(ok=True, data={"items": [item]},
                                   provenance=["teach#split_kb"])
            except Exception:
                # F2：参数生成失败 → 通识兜底（编排问题不谎报"动作库未收录"）
                return SkillResult(ok=True,
                                   data={"items": [], "empty": True,
                                         "data_kind": "knowledge",
                                         "reason": "练休节奏类编排问题，库内无对应条目"},
                                   provenance=["teach#parametric"])
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
        items = [_out_item(e) for e in self._search(query)]
        if not items:
            # 词表没接住的省略式追问 → LLM 结合历史判断（qa/teach 共用第二层）。
            # 「家里没器械还能怎么练腿」这类问法意图常落 teach，但它同样是
            # "接着上文要动作"——词表与字面检索都够不着，交给 LLM 判定。
            # 只在字面检索为空后调（正常路径零 LLM 成本）；失败静默走原逻辑。
            from app.skills.qa_skill import QaSkill
            fu = QaSkill()._followup_llm(ctx, _repo(), query)
            if fu is not None:
                return fu
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
                items.extend(_out_item(e) for e in hits)
        if not items:
            # W4：#40 担忧型问法（"斜方肌越练越大"）意图常落 teach（贴近"怎么练"例句）
            # 但动作库无肌群名词 → 兜底查 qa 辟谣块（"越练越大"类担忧），避免空结果
            from app.skills.qa_skill import QaSkill
            myth = QaSkill._myth_hit(query)
            if myth:
                return SkillResult(ok=True, data={"items": [myth], "myth": True},
                                   provenance=["teach#myth_kb"])
            # F2：编排问法未命中知识块 → 通识型空结果（渲染侧按 knowledge 分级作答，
            # 结尾标注"通用训练知识，非库内收录"），避免把编排原理谎报成"未收录"
            if any(k in query for k in _SPLIT_ASK_KW):
                return SkillResult(ok=True,
                                   data={"items": [], "empty": True,
                                         "data_kind": "knowledge",
                                         "reason": "编排原理类问题，库内无对应条目"},
                                   provenance=["teach#parametric"])
            return SkillResult(ok=True, data={"items": [], "empty": True,
                                              "data_kind": "data",
                                              "reason": "动作库未收录，尝试其他说法"},
                               provenance=["teach#exercise_repo.search"])
        self._rag_enrich(ctx, query, items)      # RAG 补强（异常静默，不影响规则结果）
        return SkillResult(ok=True, data={"items": items},
                           provenance=[f"teach:{it['name_zh']}" for it in items])

    def _rag_enrich(self, ctx, query: str, items: list) -> None:
        """对前 2 条动作追加图证据：同族替代（Neo4j）。全 try/except 静默降级。

        2026-09-14 两处变更：
        1. 去掉了 exercise_cue 的向量召回。它取回的文本是对动作记录的确定性拼接
           （见 _cue），而这里手里已有记录——是零信息量的往返。只保留真正需要图
           才能得到的同族替代。
        2. 同族替代现在有上限、按同主目标肌排序、并带可读中文名（见
           GraphStore.family_alternatives）。

        ⚠ **`rag_evidence` 目前仍无任何读取方**（`grep -rn rag_evidence` 全仓只有
        本文件这一处写入）。它当初是作为"RAG 扩展位"预留的，消费它的渲染器一直没写。
        因此在接上渲染之前，这里的产出**对用户不可见**——别误以为它在生效。
        要与用户可见的出处标注区分：那个走的是 items[].source / source_ref
        （见 qa._rag_science 与 render_util.item_lines）。"""
        try:
            from app.rag import retriever
            from app.graph.store import GraphStore
            graph = GraphStore.get()
            if graph is None:
                return
            for it in items[:2]:
                eid = it.get("id")
                if not eid:
                    continue
                try:
                    alts = retriever.graph_family_alternatives(graph, eid)
                except Exception:
                    continue
                if alts:
                    it["rag_evidence"] = {"alternatives": alts}
                    it["rag"] = True
        except Exception:
            pass