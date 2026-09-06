# -*- coding: utf-8 -*-
"""
validate_enrichment.py — 数据层一致性校验
=========================================
校验 5 个 JSON 的完整性、口径一致性、字段合法性与交叉引用，供 CI / 数据变更后回归。
用法：python scripts/validate_enrichment.py    （失败时退出码 1）

检查项：
  1. 文件齐全且可解析
  2. metadata 与 exercises 的 id 双向一致（1324 条）
  3. 肌肉映射覆盖全部原始术语
  4. movement_pattern / exercise_type / difficulty / normalized_equipment 取值白名单
  5. met 区间合法（0 < lo <= hi 且 typical 在区间内）
  6. suggested 处方结构完整
  7. canonical_name 非空
  8. family 交叉引用有效且成员数一致
"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

PATTERNS = {"push", "pull", "squat", "hinge", "lunge", "core", "carry", "cardio", "stretch", "other"}
TYPES = {"strength_compound", "strength_isolation", "cardio", "stretch_mobility"}
EQUIP = {"body weight", "dumbbell", "barbell", "kettlebell", "cable", "machine", "band",
         "weighted", "other"}
DIFF_LABEL = {1: "beginner", 2: "intermediate", 3: "advanced"}
SUGGESTED_KEYS = {"sets", "reps", "rest_sec", "duration_min"}

errors = []


def check(cond: bool, msg: str):
    (errors.append if not cond else lambda m: None)(f"  ✗ {msg}")


def main() -> int:
    files = {"exercises.json": None, "exercise_metadata.json": "exercises",
             "muscle_ontology.json": "muscles", "muscle_mapping.json": "mapping",
             "variant_families.json": "families"}
    objs = {}
    for name, key in files.items():
        path = DATA / name
        if not path.exists():
            errors.append(f"  缺失文件 {name}")
            continue
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        objs[name] = obj.get(key) if key else obj

    if any(n not in objs for n in ("exercises.json", "exercise_metadata.json")):
        return _finish()

    ex = {e["id"]: e for e in objs["exercises.json"]}
    metas = {m["id"]: m for m in objs["exercise_metadata.json"]}

    # 2) id 双向一致
    check(set(ex) == set(metas) and len(metas) == 1324,
          f"id 不一致或条数异常（exercises={len(ex)} / metadata={len(metas)}）")

    # 3) 肌肉术语覆盖
    raw = set()
    for e in ex.values():
        raw.add(e["target"].strip().lower())
        raw.add(e["muscle_group"].strip().lower())
        raw.update(x.strip().lower() for x in e["secondary_muscles"])
    mapping = objs["muscle_mapping.json"]
    missing = raw - set(mapping)
    check(not missing, f"未映射肌肉术语 {sorted(missing)}")
    check(all(mapping[t] in {m["id"] for m in objs["muscle_ontology.json"]}
              for t in mapping), "映射目标不在肌肉本体库中")

    # 4/5/6/7) 字段合法性与处方结构
    for mid, m in metas.items():
        check(m["difficulty"] in DIFF_LABEL,
              f"{mid} 难度非法: {m.get('difficulty')}")
        check(m["difficulty_label"] == DIFF_LABEL.get(m["difficulty"]),
              f"{mid} 难度标签不匹配")
        check(m["movement_pattern"] in PATTERNS,
              f"{mid} 动作模式非法: {m['movement_pattern']}")
        check(m["exercise_type"] in TYPES, f"{mid} 类型非法: {m['exercise_type']}")
        check(m["exercise_type"] != "stretch_mobility" or m["difficulty"] == 1,
              f"{mid} 拉伸类难度应为 1")
        check(m["normalized_equipment"] in EQUIP,
              f"{mid} 器械归一非法: {m['normalized_equipment']} <- {m['equipment']}")
        lo, hi, typ = m["met_range"][0], m["met_range"][1], m["met_typical"]
        check(0 < lo <= hi, f"{mid} MET 区间非法: {m['met_range']}")
        check(lo <= typ <= hi, f"{mid} MET typical 不在区间: {typ} vs {m['met_range']}")
        check(set(m["suggested"]) == SUGGESTED_KEYS, f"{mid} 处方字段缺失: {m['suggested']}")
        check(bool(m["canonical_name"]), f"{mid} canonical_name 为空")

    # 8) family 交叉引用
    fam_by_id = {}
    for f_ in objs["variant_families.json"]:
        for i in f_["member_ids"]:
            check(i not in fam_by_id, f"id {i} 出现在多个变体族（{fam_by_id.get(i)} 与 {f_['family_id']}）")
            fam_by_id[i] = f_["family_id"]
        check(f_["member_count"] == len(f_["member_ids"]),
              f"{f_['family_id']} member_count 与列表长度不符")
    for mid, m in metas.items():
        if m["family"] is not None:
            check(m["family"] in {f["family_id"] for f in objs["variant_families.json"]},
                  f"{mid} 引用了不存在的变体族 {m['family']}")
        else:
            check(mid not in fam_by_id, f"{mid} 在变体族中却 family 为空")

    return _finish()


def _finish() -> int:
    if errors:
        print(f"校验未通过，共 {len(errors)} 项：")
        for e in errors[:60]:
            print(e)
        if len(errors) > 60:
            print(f"  …… 其余 {len(errors) - 60} 项已省略")
        return 1
    print("校验通过：5 个文件结构、字段口径、交叉引用全部一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())