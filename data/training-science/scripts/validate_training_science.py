# -*- coding: utf-8 -*-
"""validate_training_science.py — training-science 数据校验（失败退出码 1）"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main():
    rpe = json.load(open(DATA / "rpe_rir.json", encoding="utf-8"))
    std = json.load(open(DATA / "strength_standards.json", encoding="utf-8"))
    ci = json.load(open(DATA / "contraindications.json", encoding="utf-8"))
    parq = json.load(open(DATA / "parq_plus.json", encoding="utf-8"))

    check(len(rpe["anchors"]) >= 8, "rpe 锚点过少")
    for a in rpe["anchors"]:
        check(40 <= a["pct_1rm"] <= 100, f"rpe 锚点越界 {a}")

    for lift, by_sex in std["lift"].items():
        for sex, lv in by_sex.items():
            check(set(lv) == set(std["levels"]), f"{lift}/{sex} 水平键不全")

    for c in ci["conditions"]:
        check(c["risk_level"] in {"red", "yellow", "green"}, f"{c['id']} 级别非法")
        check(c["danger_patterns"] and c["safe_prescriptions"], f"{c['id']} 缺危险/替代")
        check("待审" in c["source"], f"{c['id']} 未标注待审来源")

    check(len(parq["questions"]) == 7, "PAR-Q 主问卷应为 7 题")
    return finish()


def finish():
    if errors:
        print(f"校验未通过，共 {len(errors)} 项：")
        for e in errors[:20]:
            print("  ✗", e)
        return 1
    print("校验通过：training-science 4 表结构与口径一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())