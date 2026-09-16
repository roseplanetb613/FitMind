# -*- coding: utf-8 -*-
"""
exercise_repo.py — 健身动作检索/推荐层
======================================
把原始数据集 exercises.json 与增强映射文件（muscle_ontology / muscle_mapping /
exercise_metadata / variant_families）统一装配成单一访问入口，供健身智能体调用。

核心能力：
  - get(id)                按 ID 取完整增强记录
  - search(text)           动作名关键字搜索
  - filter(...)            组合过滤：肌肉/器械/难度/模式/类型/变体族
  - recommend(...)         简单推荐：肌肉为主 + 渐进放宽 + 变体族去重
  - 肌肉归一化：'delts'/'shoulders'/'三角肌' 均能定位到三角肌本体

设计约束：只读、无第三方依赖、可复用。错误输入做宽容处理（找不到返回空）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional

try:                                    # lib/ 平铺导入（runtime 的 sys.path 注入）
    from parts import PARTS, muscle_of
except ImportError:                     # 包形式导入（lib.parts）
    from lib.parts import PARTS, muscle_of

# 数据包 = data/exercises-dataset（lib/ 顶层，数据集集中在 data/ 下）
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "exercises-dataset" / "data"

DIFF_LABEL = {1: "beginner", 2: "intermediate", 3: "advanced"}
LABEL_DIFF = {v: k for k, v in DIFF_LABEL.items()}

# 口语别名 → 库内可检索名称片段（压测补全：库收录"杠铃 弓步"但用户说"箭步蹲"等）
# 使用方：teach_skill/qa_skill 的中文检索在匹配前先经 expand_aliases 归一。
NAME_ALIASES = {
    "箭步蹲": "弓步",
    "箭步": "弓步",
    "保加利亚分腿蹲": "分腿深蹲",
    "保加利亚蹲": "分腿深蹲",
    "臀推": "臀冲",
    "双杠臂屈伸": "臂屈伸",
    "后蹲": "完全深蹲",
    # W1：口语"肩推举"库内无整词（有"肩推"/"推举"拆分），归一为"肩推"
    #（压测 #76 '练肩推举和飞鸟哪个先' 空结果根因）
    "肩推举": "肩推",
    # 2026-09-12 健身房口语（用户实测 "夹腿" 整句被丢）：库内用解剖学名
    #（内收/外展/飞鸟/下拉/推举），用户说器械俗称。每条都已核过目标词在库内存在：
    #   夹腿 6 个（'杠杆机 坐姿 髋内收' 等）· 开腿 5 个 · 飞鸟 38 个
    #   下拉 27 个 · 胸部推举（'器械 内侧 胸部推举'）
    "夹腿": "内收",      # 髋内收器械
    "开腿": "外展",      # 髋外展器械
    "夹胸": "飞鸟",      # 蝴蝶机/绳索夹胸 ≈ 飞鸟
    "拉背": "下拉",      # 高位下拉俗称
    "推胸": "胸部推举",   # 器械推胸
    # 2026-09-16 器械俗称（用户实测「倒蹬机」补记被业务 404 挡在门外）：
    # 库的 leg press 全翻译成「腿举」，「倒蹬」在库内无任何字面命中 ——
    # checkin_resolve 的自定义输入分支 search_zh 捞候选全空，直接落到
    # 「动作库里没有」的 404。归一到「腿举」后走既有的 need_pick 追问
    # （"是下面哪个？"）再记，不回业务 404。
    # 目标词「腿举」已核在库内存在（史密斯 腿举 / 雪橇机 45° 腿举 等）；
    # 顺序敏感：expand_aliases 按插入序首命即返，「倒蹬机」必须写在「倒蹬」前，
    # 否则「倒蹬机」会被「倒蹬」先替换成「腿举机」，字面依旧不中（同样白 404）。
    "倒蹬机": "腿举",
    "倒蹬": "腿举",
    "腿举机": "腿举",
    # 注：**腿屈伸（leg extension）库内无对应动作**（只有'椅子 腿 伸展 拉伸'这个拉伸）
    # ——属数据集缺口，不是别名缺口，不能硬造映射。已记于 spec。
}


def expand_aliases(text: str) -> str:
    """把常见口语动作名替换为库内可检索名片段（首个命中即返回）。"""
    for alias, target in NAME_ALIASES.items():
        if alias in text:
            return text.replace(alias, target)
    return text


def norm_zh(s: str) -> str:
    """中文检索归一：去空格+小写（'杠铃 卧推'→'杠铃卧推'）。装配与查询两侧共用。"""
    return "".join((s or "").split()).lower()

def same_name(a: str, b: str) -> bool:
    """中文名等价判定（归一后**双向包含**）——跨层名字比对的唯一入口。

    2026-09-11 收口：`norm_zh` 早已是单源，但**没有机制强制消费方调用它**——
    同一天出现过两个独立同类缺陷（plan_skill._edit 的动作名匹配、
    memory.muscles_of_exercises 的图谱匹配），根因都是裸子串比对遇上带空格复合名。
    新代码做名字比对请用本函数；裸 `a in b` 式比对在 review 中视为缺陷。"""
    x, y = norm_zh(a), norm_zh(b)
    return bool(x) and bool(y) and (x in y or y in x)


# 中文问句修饰词表：剥离后提取核心动作名（qa/teach 统一，单源）
# W1 扩展：复合句尾"怎么做才X/怎么做比较X"（#61）、"哪个先/哪个后"（#76）、
# "标准动作/标准/正确/规范"（#157）；"练习"先于"练"命中避免"练习X"被拆残。
_ZH_PREFIX = ("你知道", "我想查", "我想问", "请问", "查一下", "帮我查",
              "帮我找", "我想了解", "了解一下", "能不能", "可以吗", "你能告诉我",
              "在家", "家里",  # 场景前缀（2026-09-16）：场景词已转成器械约束，留着只会污染匹配
              "练习", "练")
# 长的在前，保证 '应该怎么做' 先于 '怎么做' 命中；'的区别/哪个好' 等比较问法也剥
_ZH_SUFFIX = ("应该怎么做", "应该怎么练", "是什么意思",
              "怎么做才标准", "怎么做才正确", "怎么做才规范", "怎么做才到位",
              "怎么做比较标准", "怎么做比较正确", "怎么做比较规范",
              "怎么练才标准", "怎么练比较标准",
              "怎么做才", "怎么做比较", "怎么练才", "怎么练比较",
              "怎么做", "怎么练", "如何做", "如何练", "的做法", "的区别",
              "是什么", "怎么样",
              "标准动作", "正确动作", "做几组", "做几次", "做多少组",
              "哪个练腿", "哪个练臀", "哪个练胸", "哪个练背", "哪个练肩",
              "哪个先", "哪个后", "先做", "后做",
              "哪个好", "哪个多", "哪个难", "怎么选", "怎么用", "怎么", "如何",
              "标准", "正确", "规范",
              "训练", "动作", "哪个", "吗", "呢", "啊", "呀", "嘛", "吧")

# W1 逐级回退的尾部实词表（非问句修饰，而是动作名的尾词部分）：
# 整词无命中时依次砍掉尾词再试（至多 2 轮），如 '壶铃摇摆' → '壶铃'。
_TAIL_MOD = ("标准动作", "正确动作", "摇摆", "标准", "正确", "规范", "到位",
             "练习", "训练", "姿势", "技巧", "视频", "教程", "要领", "要点",
             "方法", "入门", "动作")

# 尾部虚词（2026-09-16）：剥掉问句修饰后残留的结构虚词。
# 「练胸的动作」剥「动作」剩「胸的」——「的」不是独立修饰词、不在 _ZH_SUFFIX，
# 留在尾巴上让整词检索必空。剥离后逐字清理（虚词可能连着出现）。
_ZH_PARTICLE = "的了呢吧啊呀嘛"

# 场景词 → 器械约束（2026-09-16）：「在家能做的腿部训练」这类句子里的场景修饰
# 其实是**器械筛选条件**，当普通检索词只会污染匹配。只收约束明确的一组；
# 「健身房」刻意不加——健身房什么器械都有，过滤反而错杀。
_SCENE_EQUIP = (("在家", "body weight"), ("家里", "body weight"),
                ("徒手", "body weight"), ("无器械", "body weight"),
                ("不用器械", "body weight"), ("自重", "body weight"))


def split_zh_query(query: str) -> list[str]:
    """复合查询切分 + 修饰词剥离 → 核心词列表。
    例：'深蹲和硬拉的区别' → ['深蹲', '硬拉']；'你知道深蹲吗' → ['深蹲']。
    W1：后缀轮循剥（至多 2 轮）——'壶铃摇摆标准动作' 经 '标准动作'→'壶铃摇摆'；
     '深蹲怎么做才标准' 经 '怎么做才标准'→'深蹲'，一次到位。"""
    q = (query or "").strip().lower()
    parts = re.split(r"[和与、/]|还是|对比|vs|跟|以及", q)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        for pre in _ZH_PREFIX:
            if p.startswith(pre) and len(p) > len(pre):
                p = p[len(pre):]
                break
        for _ in range(2):                   # 后缀剥至多 2 轮（长的在前先命中）
            hit = None
            for suf in _ZH_SUFFIX:
                if p.endswith(suf) and len(p) > len(suf):
                    hit = suf
                    break
            if hit is None:
                break
            p = p[: -len(hit)].strip()
            if not p:
                break
        p = p.strip()
        # 尾部虚词清理（见 _ZH_PARTICLE）：「练胸的动作」→「胸的」→「胸」。
        # 不清的话「胸的」整词检索必空，而「胸」能走部位旁路命中整组胸动作。
        while p and p[-1] in _ZH_PARTICLE:
            p = p[:-1].strip()
        if p:
            out.append(p)
    return out or ([q] if q else [])


def _tail_cut(s: str) -> str:
    """W1：逐级回退砍尾——优先砍 _TAIL_MOD 实词（'壶铃摇摆'→'壶铃'），
    未命中修饰表则砍 1 字；len<=2 不再砍（防破坏 2 字动作名）。"""
    if len(s) <= 2:
        return s
    for k in _TAIL_MOD:
        if s.endswith(k) and len(s) > len(k):
            return s[: -len(k)]
    return s[:-1]


class ExerciseRepo:
    """统一数据集访问入口。实例化时一次性加载 5 个 JSON 并建立索引。"""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = Path(data_dir)

        def _read(name: str, key: Optional[str] = None) -> Any:
            path = self.data_dir / name
            if not path.exists():
                raise FileNotFoundError(f"缺少数据文件: {path}")
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            return obj.get(key) if key else obj

        self.exercises = _read("exercises.json")
        self.meta = {
            m["id"]: m for m in _read("exercise_metadata.json", "exercises")
        }
        self.ontology = _read("muscle_ontology.json", "muscles")
        self.mapping = _read("muscle_mapping.json", "mapping")
        self.families = {f["family_id"]: f for f in _read("variant_families.json", "families")}
        # 中文动作名映射（name_zh.json 缺失时降级为空表）
        zh_path = self.data_dir / "name_zh.json"
        self.name_zh = {}
        if zh_path.exists():
            with open(zh_path, encoding="utf-8") as f:
                self.name_zh = {k.lower(): v for k, v in json.load(f).items()}

        # 索引与别名表（必须先于 by_id 装配，_enrich 依赖两者）
        self.muscle_by_alias = {k: v for k, v in self.mapping.items()}
        self.muscle_by_alias.update({m["id"]: m["id"] for m in self.ontology})
        self.muscle_meta = {m["id"]: m for m in self.ontology}

        self.by_id = {e["id"]: self._enrich(e) for e in self.exercises}

    # ------------------------------------------------------------------ 装配
    def _enrich(self, e: dict) -> dict:
        """合并元数据与肌肉本体解析，返回完整增强记录（不动原始数据）。"""
        m = self.meta.get(e["id"], {})
        record = dict(e)
        record.update({k: m[k] for k in ("canonical_name", "difficulty", "difficulty_label",
                                         "movement_pattern", "exercise_type",
                                         "normalized_equipment", "suggested",
                                         "met_range", "met_typical") if k in m})
        record["family"] = m.get("family")
        record["family_name"] = (
            self.families[m["family"]]["stem"] if record["family"] else None
        )
        # 中文动作名（缺翻译时回退英文原名）
        record["name_zh"] = self.name_zh.get(
            e["name"].strip().lower(), e["name"])
        # 装配期预计算归一名：search_zh 每次查询不必对全库现算（去空格+小写）
        record["norm_name_zh"] = norm_zh(record["name_zh"])
        # 肌肉归一化：把原始术语映射成本体 id
        record["muscles_canonical"] = {
            "target": self.muscle_by_alias.get(e["target"].strip().lower()),
            "muscle_group": self.muscle_by_alias.get(e["muscle_group"].strip().lower()),
            "secondary": [self.muscle_by_alias.get(x.strip().lower())
                          for x in e["secondary_muscles"]],
        }
        return record

    # ---------------------------------------------------------------- 查询
    def get(self, exercise_id: str) -> Optional[dict]:
        return self.by_id.get(exercise_id)

    def search(self, text: str, limit: Optional[int] = None) -> list[dict]:
        t = text.strip().lower()
        if not t:
            return []
        hits = [r for r in self.by_id.values() if t in r["name"].lower()]
        if limit:
            hits = hits[:limit]
        return hits

    def search_zh(self, query: str, limit: int = 5,
                  part_fallback: bool = False) -> list[dict]:
        """中文动作检索统一入口（qa/teach 共用，防双写漂移）：
        复合查询切分 → 修饰词剥离 → 口语别名归一 → 双向子串匹配
        （正向：动作名含核心词；反向：核心词含动作名，动作名 ≥2 字防单字误命中），
        场景约束过滤 → 相关性排序（精确同名＞字面命中＞部位旁路；字面内按
        名字长短＝变体少者先，难度次级）；整词无命中时逐级回退（W1），
        仍空再乱序 AND（W1）。非中文输入回退 search()。

        `part_fallback`（默认 False）：字面/回退/AND 全部落空后，允许按**部位词
        旁路**（`_part_scan`）兜底。**只有对话检索（qa/teach）开启**——判定类
        检索（memory_extract/plan）依赖「空 = 没有这个动作」的语义，开了它
        search_zh 会几乎永远非空（详见 `_part_scan` 的说明，实测鸡胸误判）。
        场景约束滤空时退回无约束结果（约束是偏好，不该把结果清零）。"""
        if not re.search(r"[一-鿿]", query or ""):
            return self.search(query, limit=limit)
        core = split_zh_query(query)
        hits = self._match_zh(core)
        if not hits and part_fallback:
            hits = self._part_scan(core)         # 部位词旁路（对话检索专属）
        if not hits:
            hits = self._fallback_search(core)   # 整词无命中 → 砍尾词重试（≤2 轮）
        if not hits:
            hits = self._and_search(core)        # 仍空 → token 集合 AND（词序无关）
        # 场景词 → 器械约束（「在家能做的腿部训练」→ body weight）。
        # 在相关性排序**之前**过滤：约束不满足的再贴题也不能给；
        # 但约束把结果滤**空**时退回无约束版本 —— 约束是偏好不是硬条件，
        # 「在家练深蹲」库里没有自重深蹲时，给杠铃深蹲好过给空。
        equip = self._scene_equip(query)
        if equip:
            filtered = [h for h in hits if h.get("normalized_equipment") == equip]
            if filtered:
                hits = filtered
        # 相关性排序（2026-09-16）：此前只有难度升序——「深蹲」命中 72 条
        #（杠铃 26 条最大族），返回的却是 5 条弹力带变体，杠铃深蹲一条看不到。
        #
        # 复合查询用**按词分桶轮转**：每个核心词的字面命中各占一路，按
        # 「词1第1、词2第1、词1第2…」穿插。不能用统一键排序——「练肩推举和
        # 飞鸟哪个先」里飞鸟有 38 条且难度更低，统一排会把肩推整批挤出前 5，
        # 用户问的第一个动作反而消失（实测）。部位旁路命中排所有字面之后。
        buckets = [[] for _ in core] + [[]]
        for h in hits:
            keys = [self._relevance(h, w) for w in core]
            lit = [i for i, k in enumerate(keys) if k[1] == 0]
            if lit:
                i = min(lit, key=lambda j: keys[j])
                buckets[i].append((keys[i], h))
            else:
                buckets[-1].append((min(keys), h))
        merged: list = []
        for bi, b in enumerate(buckets[:-1]):
            b.sort(key=lambda kh: kh[0])
            for rank, (_, h) in enumerate(b):
                merged.append((rank, bi, h))
        offset = max((len(b) for b in buckets[:-1]), default=0)
        b = buckets[-1]
        b.sort(key=lambda kh: kh[0])
        for rank, (_, h) in enumerate(b):
            merged.append((rank + offset, len(buckets) - 1, h))
        merged.sort(key=lambda t: (t[0], t[1]))
        hits = [h for _, _, h in merged]
        return hits[:limit]

    @staticmethod
    def _scene_equip(query: str) -> str | None:
        for k, v in _SCENE_EQUIP:
            if k in query:
                return v
        return None

    @classmethod
    def _relevance(cls, r: dict, core: str) -> tuple:
        """相关性键（越小越靠前）：精确同名 > 名字越短 > 难度。

        名字长度是「贴题度」的代理：库内动作名 = 器械 + 变体修饰 + 动作根，
        「深蹲」的命中里「杠铃 深蹲」（4 字）是基础动作、「弹力带 单臂 单腿
        分腿深蹲」（11 字）是叠满修饰的变体 —— 越短越接近用户问的那个动作。
        难度只作同长度时的次级键（「杠铃 深蹲」与「哑铃 深蹲」同为 4 字时
        先给简单的，与全库渐进口径一致）。
        """
        nz = norm_zh(r.get("name_zh") or "")
        # ⚠ 与 _match_zh 同一入口：必须先过 expand_aliases——否则「肩推举」
        # 不归一成「肩推」，字面匹配全部失效、真命中全被挤进旁路桶（实测）。
        nq = norm_zh(expand_aliases(core))
        d = r.get("difficulty")
        # 字面命中（名字含核心词）**永远排在部位旁路命中之前**：用户自己的词
        # 优先于"按部位扫出来的"（test_clarify_prefers_the_users_own_word...
        # 钉过的原则）。旁路条目的名字与 query 无字面关系，len 对它们无意义
        # ——不压住的话，3 字的「侧平举」会挤出 4 字的「杠铃 肩推」。
        literal = 0 if (nq and nq in nz) else 1
        return (0 if nz and nz == nq else 1,
                literal,
                len(nz or "000") if literal == 0 else 0,
                d if isinstance(d, int) else 9)

    def _match_zh(self, words: list[str]) -> list[dict]:
        """逐核心词双向子串匹配（去重，排序由调用方做）。**纯字面**——
        部位词旁路拆到 `_part_scan`（可选开关），原因见其 docstring。"""
        seen: set = set()
        hits: list[dict] = []
        for q in words:
            nq = norm_zh(expand_aliases(q))
            if not nq:
                continue
            for r in self.by_id.values():
                nz = r.get("norm_name_zh") or ""
                if not nz:
                    continue
                if (nq in nz) or (len(nz) >= 2 and nz in nq):
                    if r["id"] not in seen:
                        seen.add(r["id"])
                        hits.append(r)
        return hits

    def _part_scan(self, words: list[str]) -> list[dict]:
        """部位词旁路：核心词本身就是身体部位（如「核心」「胸」）时，库内动作名
        通常**不含**这个词（没有叫「核心」的动作），纯名字检索必空；经
        `parts.PARTS` 映射到肌群后按肌肉字段取，排除拉伸/柔韧（同 _pool 口径）。

        ⚠ **独立旁路、默认关闭**（`search_zh(..., part_fallback=True)` 才启用）：
        它会把 search_zh 变成"几乎永远非空"——任何含部位字的词（「鸡胸」含「胸」）
        都能命中肌肉动作，而 memory_extract 的 `_has_exercise_candidates` 与
        plan 的动作名匹配都依赖「**空 = 没有这个动作**」的语义（实测：鸡胸的
        食物偏好因此被误判成动作）。只有**对话检索**（qa/teach）开启——那里
        空结果会走 LLM 兜底，多一层猜测可接受；判定类检索保持严格。

        部位词在核心词里**查找**（长键优先防「大腿」被「腿」截糊）——
        「家里没有器械怎么练胸」剥完修饰剩「没有器械练胸」，两头都不等于
        「胸」，但里面的「胸」就是主题。
        """
        seen: set = set()
        hits: list[dict] = []
        for q in words:
            part_word = next((k for k in sorted(PARTS, key=len, reverse=True)
                              if k in q), None)
            muscle = muscle_of(part_word) if part_word else None
            if not muscle:
                continue
            for r in self.filter(muscle=muscle):
                # 排除拉伸/柔韧（同 _pool 口径）：部位问的是训练动作，
                # 「在家能做的腿部训练」混进「股四头肌拉伸」就是答非所问
                if r.get("exercise_type") == "stretch_mobility":
                    continue
                if r["id"] not in seen:
                    seen.add(r["id"])
                    hits.append(r)
        return hits

    def _fallback_search(self, words: list[str]) -> list[dict]:
        """W1 逐级回退：核心词整词无命中 → 砍尾部修饰实词重试（至多 2 轮）。
        回退命中浅拷贝注入 `uncovered_query`（原核心词），供渲染侧注明"未收录"。"""
        for q in words:
            if not q:
                continue
            cur = norm_zh(expand_aliases(q))
            cuts = 0
            while cuts < 2:
                nxt = _tail_cut(cur)
                if nxt == cur:
                    break
                cur, cuts = nxt, cuts + 1
                found = [r for r in self.by_id.values()
                         if (cur in (r.get("norm_name_zh") or "")
                             or (len(r.get("norm_name_zh") or "") >= 2
                                 and (r.get("norm_name_zh") or "") in cur))]
                if found:
                    return [dict(r, uncovered_query=q) for r in found]
        return []

    def _and_search(self, words: list[str]) -> list[dict]:
        """W1 乱序容忍：所有核心 token 须同时出现于同一动作名（词序无关）。
        仅多核心词场景生效（单核心词直接返回空，避免全库 O(n) 白跑）。"""
        toks = [norm_zh(expand_aliases(q)) for q in words if q]
        if len(toks) < 2:
            return []
        out = []
        for r in self.by_id.values():
            nz = r.get("norm_name_zh") or ""
            if nz and all(t in nz for t in toks):
                out.append(r)
        return out

    def _norm_muscle(self, muscle: str) -> Optional[str]:
        """把用户输入的肌肉（规范 id / 原始术语 / 中文名）归一成本体 id。"""
        key = muscle.strip().lower()
        key = {"三角肌": "deltoids", "肩": "deltoids", "肩部": "deltoids",
               "胸": "pectorals", "胸肌": "pectorals", "背阔肌": "latissimus_dorsi",
               "核心": "core", "腹肌": "rectus_abdominis", "臀": "glutes",
               "肱二": "biceps", "bicep": "biceps", "tricep": "triceps",
               "肱三": "triceps", "腘绳": "hamstrings", "股四": "quadriceps",
               "小腿": "calves", "前臂": "forearms", "竖脊": "lower_back",
               "竖脊肌": "lower_back", "下背": "lower_back"}.get(key, key)
        return None if key not in self.muscle_by_alias else self.muscle_by_alias[key]

    def filter(self, *,
               muscle: Optional[str] = None,
               equipment: Optional[str] = None,
               difficulty: Optional[int | str] = None,
               pattern: Optional[str] = None,
               exercise_type: Optional[str] = None,
               family: Optional[str] = None,
               limit: Optional[int] = None,
               sort_by_difficulty: bool = False) -> list[dict]:
        """组合过滤。任一条件可缺省；条件之间是 AND，返回匹配列表。"""
        if isinstance(difficulty, str) and difficulty.lower() in LABEL_DIFF:
            difficulty = LABEL_DIFF[difficulty.lower()]
        if isinstance(difficulty, str):
            difficulty = int(difficulty)
        if equipment is not None:
            equipment = {"bodyweight": "body weight", "徒手": "body weight",
                         "自重": "body weight"}.get(equipment.strip().lower(),
                                                    equipment.strip().lower())
        canonical = self._norm_muscle(muscle) if muscle else None
        # **归一失败时必须返回空，不能"静默地不筛"。**
        # 旧写法只在 `canonical is not None` 时才过滤，于是拼错的肌肉名（或压根不存在的）
        # 会被当成"不过滤" —— `filter(muscle="no_such_muscle_xyz")` 返回**全部 1324 个**，
        # 而不是空。这会让任何按肌肉取数的地方把"查不到"误当成"全都要"。
        if muscle and canonical is None:
            return []

        out = []
        for r in self.by_id.values():
            if canonical is not None:
                c = r["muscles_canonical"]
                if canonical not in {c["target"], c["muscle_group"], *c["secondary"]}:
                    continue
            if equipment is not None and r["equipment"].strip().lower() != equipment:
                continue
            if difficulty is not None and r["difficulty"] != difficulty:
                continue
            if pattern is not None and r["movement_pattern"] != pattern:
                continue
            if exercise_type is not None and r["exercise_type"] != exercise_type:
                continue
            if family is not None and r["family"] != family:
                continue
            out.append(r)

        if sort_by_difficulty:
            out.sort(key=lambda r: r["difficulty"])
        return out[:limit] if limit else out

    # -------------------------------------------------------------- 推荐
    def recommend(self, muscle: str, *,
                  equipment: Optional[str] = None,
                  difficulty: Optional[int | str] = None,
                  count: int = 5,
                  exclude: Optional[Iterable[str]] = None,
                  include_stretch: bool = False,
                  prefer: Optional[Iterable[str]] = None) -> dict:
        """
        简单推荐：以目标肌肉为主键，逐级放宽条件直到凑够 count。
        默认排除拉伸/柔韧类动作（适合力量/训练建议），include_stretch=True 可放开。
        返回 {recommendations: [...], fallback: bool}，fallback=True 表示放宽过条件。

        `prefer`：用户**最近练过**的动作 id 集合，排在前面。默认 None → 顺序完全不变
        （既有调用方与测试不受影响）。这是"按你的习惯排序"的唯一注入点。

        排序总键：(是否最近练过, 是否该肌肉的主目标, 难度)。
        **最近练过排在最前** —— 它比"主目标"更能代表这个人实际在做什么。
        """
        blacklist = set(exclude or [])
        # **每一步都必须带 `muscle`。** 这里曾漏传，于是候选集是**全部 1324 个动作**，
        # 只在排序里把命中该肌肉的排前面 —— 命中数 ≥ count 时看着正常，**不足时就用
        # 不相干的动作凑数**（实测 tibialis_anterior 只有 1 个命中，却返回
        # 「3/4 仰卧起坐 / 45° 体侧屈 / 空中蹬车」），而且因为第一步就凑够了数量，
        # 直接返回并报 `fallback=False`（谎报没放宽过条件）。
        # 下面每行的注释本来写的就是"只按肌肉"——意图是对的，只是漏了参数。
        # 每一步是 (过滤条件, 是否允许拉伸类)，按"从严到宽"排列。
        steps = [
            (dict(muscle=muscle, equipment=equipment, difficulty=difficulty), include_stretch),
            (dict(muscle=muscle, difficulty=difficulty), include_stretch),      # 放宽器械
            (dict(muscle=muscle, equipment=None, difficulty=None), include_stretch),  # 只按肌肉
            (dict(muscle=muscle), include_stretch),                             # 任意难度
            # 最后**连拉伸也放开**：有些肌肉全库就只有拉伸类动作
            # （实测 levator_scapulae / sternocleidomastoid 各 2 条，全是 stretch_mobility），
            # 不放宽的话它们一条推荐都没有 —— 而"这块肌肉该拉伸"本身是有用的建议。
            (dict(muscle=muscle), True),
        ]
        best: list[dict] = []
        target_id = self._norm_muscle(muscle)
        for idx, (kw, allow_stretch) in enumerate(steps):
            cands = [r for r in self.filter(**kw)
                     if r["id"] not in blacklist
                     and (allow_stretch or r["exercise_type"] != "stretch_mobility")]
            if not cands:
                continue
            # 变体去重（family 优先，无 family 用去括号后的动作名）+ 主目标优先 + 难度升序
            seen_fam: set[str] = set()
            picked = []
            _pref = set(prefer or ())
            # 第一项：最近练过的排最前。没有 prefer 时恒为 0，等价于原行为。
            # ⚠ 改了排序会连带改变"哪几个被 picked"——下面凑够 count 就 break，
            # 且后半段靠 blacklist 在步骤间排除，所以步骤 2-5 的候选集也会变。
            for r in sorted(cands, key=lambda x: (0 if x["id"] in _pref else 1,
                                                  0 if x["muscles_canonical"]["target"] ==
                                                  target_id else 1, x["difficulty"])):
                key = r["family"] or self._strip_variant(r["name"])
                if key in seen_fam:
                    continue
                seen_fam.add(key)
                picked.append(r)
                if len(picked) >= count:
                    break
            if len(picked) >= count:
                return {"recommendations": picked,
                        "fallback": idx > 0}   # 只有放宽过条件才标记 fallback
            # 这一步凑不够 count —— 记下它，继续放宽。
            # **不要在这里 `continue` 掉**：旧写法要求每一步都必须凑够 count，
            # 于是"全库只有 1~2 个动作"的肌肉（tibialis_anterior / levator_scapulae）
            # 所有 step 都过不了这道门，最后返回**空**——一块肌肉一个推荐都没有。
            if len(picked) > len(best):
                best = picked
            blacklist.update(r["id"] for r in picked)
        # 放宽到底仍不足 count —— **有多少给多少**。不掺假（不拿不相干的凑数），
        # 也不清空（那不是"宁缺毋滥"，是"什么都不给"）。
        return {"recommendations": best, "fallback": True}

    @staticmethod
    def _strip_variant(name: str) -> str:
        """去括号/版本标签，用于推荐去重：'x (with towel)' → 'x'。"""
        t = name.lower().strip()
        t = re.sub(r"\s*\([^)]*\)", "", t)
        t = re.sub(r"\s*v\.?\s*\d+.*$", "", t)
        return t.strip()

    # ------------------------------------------------------------ 其他工具
    def muscle(self, muscle: str) -> Optional[dict]:
        """返回肌肉本体信息（en/zh/region）。"""
        mid = self._norm_muscle(muscle)
        return self.muscle_meta.get(mid)

    def family(self, family_id: str) -> Optional[dict]:
        return self.families.get(family_id)

    def siblings(self, exercise_id: str) -> list[dict]:
        """同一变体族内的其他动作（用于替换建议）。"""
        r = self.by_id.get(exercise_id)
        if not r or not r["family"]:
            return []
        return [self.by_id[i] for i in self.families[r["family"]]["member_ids"]
                if i != exercise_id]


def build() -> ExerciseRepo:
    """便捷工厂：单例由调用方自行持有。"""
    return ExerciseRepo()


if __name__ == "__main__":
    repo = build()
    print(f"已加载 {len(repo.by_id)} 个动作、{len(repo.ontology)} 个肌肉本体、"
          f"{len(repo.families)} 个变体族")