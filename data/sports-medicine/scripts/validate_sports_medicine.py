# -*- coding: utf-8 -*-
"""validate_sports_medicine.py — sports-medicine 三表校验（失败退出码 1）"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
errors = []


def check(cond, msg):
    if not cond:
        errors.append(msg)


def main():
    fitt = json.load(open(DATA / "fitt_prescription.json", encoding="utf-8"))
    epi = json.load(open(DATA / "injury_epidemiology.json", encoding="utf-8"))
    rtp = json.load(open(DATA / "recovery_rtp.json", encoding="utf-8"))

    for k in ("cardiorespiratory", "resistance", "flexibility"):
        check(k in fitt and fitt[k].get("frequency"), f"fitt 缺 {k}")
    check("intensity_scale" in fitt, "fitt 缺 intensity_scale")

    check(len(epi["conditions"]) >= 5, "流行病学条目过少")
    for c in epi["conditions"]:
        for f in ("injury_zh", "body_part", "risk_factors", "prevention", "source"):
            check(c.get(f), f"{c['id']} 缺 {f}")
        check("待审" in c["source"] or "综述" in c["source"], f"{c['id']} 来源未标注")

    check(len(rtp["conditions"]) >= 4, "恢复条目过少")
    for c in rtp["conditions"]:
        check(c.get("stages") and c.get("rtp_note"), f"{c['id']} 缺阶段/回归说明")
        check("待审" in c["source"], f"{c['id']} 未标注待审")
        for s in c["stages"]:
            for f in ("phase", "goals", "allowed", "avoid"):
                check(s.get(f), f"{c['id']}/{s['phase']} 缺 {f}")
    return finish()


def finish():
    if errors:
        print(f"校验未通过，共 {len(errors)} 项：")
        for e in errors[:20]:
            print("  ✗", e)
        return 1
    print("校验通过：sports-medicine 三表结构/来源标注一致 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())