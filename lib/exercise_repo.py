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