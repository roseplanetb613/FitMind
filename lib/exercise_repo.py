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


# 中文问句修饰词表：剥离后提取核心动作名（qa/teach 统一，单源）
# W1 扩展：复合句尾"怎么做才X/怎么做比较X"（#61）、"哪个先/哪个后"（#76）、
# "标准动作/标准/正确/规范"（#157）；"练习"先于"练"命中避免"练习X"被拆残。
_ZH_PREFIX = ("你知道", "我想查", "我想问", "请问", "查一下", "帮我查",
              "帮我找", "我想了解", "了解一下", "能不能", "可以吗", "你能告诉我",
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
              "动作", "哪个", "吗", "呢", "啊", "呀", "嘛", "吧")

# W1 逐级回退的尾部实词表（非问句修饰，而是动作名的尾词部分）：
# 整词无命中时依次砍掉尾词再试（至多 2 轮），如 '壶铃摇摆' → '壶铃'。
_TAIL_MOD = ("标准动作", "正确动作", "摇摆", "标准", "正确", "规范", "到位",
             "练习", "训练", "姿势", "技巧", "视频", "教程", "要领", "要点",
             "方法", "入门", "动作")


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

    def search_zh(self, query: str, limit: int = 5) -> list[dict]:
        """中文动作检索统一入口（qa/teach 共用，防双写漂移）：
        复合查询切分 → 修饰词剥离 → 口语别名归一 → 双向子串匹配
        （正向：动作名含核心词；反向：核心词含动作名，动作名 ≥2 字防单字误命中），
        按难度升序；整词无命中时逐级回退（W1），仍空再乱序 AND（W1）。
        非中文输入回退 search()。"""
        if not re.search(r"[一-鿿]", query or ""):
            return self.search(query, limit=limit)
        core = split_zh_query(query)
        hits = self._match_zh(core)
        if not hits:
            hits = self._fallback_search(core)   # 整词无命中 → 砍尾词重试（≤2 轮）
        if not hits:
            hits = self._and_search(core)        # 仍空 → token 集合 AND（词序无关）
        hits.sort(key=lambda e: e.get("difficulty") or 9)
        return hits[:limit]

    def _match_zh(self, words: list[str]) -> list[dict]:
        """逐核心词双向子串匹配（去重，难度排序由调用方做）。"""
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
                  include_stretch: bool = False) -> dict:
        """
        简单推荐：以目标肌肉为主键，逐级放宽条件直到凑够 count。
        默认排除拉伸/柔韧类动作（适合力量/训练建议），include_stretch=True 可放开。
        返回 {recommendations: [...], fallback: bool}，fallback=True 表示放宽过条件。
        """
        blacklist = set(exclude or [])
        steps = [
            dict(equipment=equipment, difficulty=difficulty),   # 全条件
            dict(difficulty=difficulty),                        # 放宽器械
            dict(equipment=None, difficulty=None),              # 只按肌肉
            {},                                                 # 最后：任意难度空手兜底
        ]
        for idx, kw in enumerate(steps):
            cands = [r for r in self.filter(**kw)
                     if r["id"] not in blacklist
                     and (include_stretch or r["exercise_type"] != "stretch_mobility")]
            if len(cands) < count:
                continue
            # 变体去重（family 优先，无 family 用去括号后的动作名）+ 主目标优先 + 难度升序
            seen_fam: set[str] = set()
            picked = []
            target_id = self._norm_muscle(muscle)
            for r in sorted(cands, key=lambda x: (0 if x["muscles_canonical"]["target"] ==
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
            blacklist.update(r["id"] for r in picked)
        # 理论不可达：数据量充足
        return {"recommendations": [], "fallback": True}

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