# -*- coding: utf-8 -*-
"""split_schemes.json 校验：结构/唯一性/pattern 白名单。
手动：python data/split-schemes/scripts/validate.py"""
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "split_schemes.json"
PATTERNS = {"push", "pull", "squat", "hinge", "core"}


def validate(data: dict) -> list[str]:
    errors: list[str] = []
    schemes = data.get("schemes")
    if not isinstance(schemes, list) or not schemes:
        return ["schemes 缺失或为空"]
    ids: set = set()
    aliases: dict = {}
    defaults = 0
    for s in schemes:
        sid = s.get("id")
        if not sid or sid in ids:
            errors.append(f"id 缺失或重复: {sid}")
        ids.add(sid)
        if s.get("default"):
            defaults += 1
        if not s.get("name_zh"):
            errors.append(f"{sid}: name_zh 缺失")
        for a in s.get("aliases") or []:
            key = str(a).lower()
            if key in aliases:
                errors.append(f"alias 重复: {a}（{aliases[key]} / {sid}）")
            aliases[key] = sid
        cycle = s.get("cycle")
        if not cycle:
            errors.append(f"{sid}: cycle 为空")
            continue
        for i, slot in enumerate(cycle):
            t = slot.get("type")
            if t not in ("train", "rest"):
                errors.append(f"{sid}[{i}]: type 非法 {t!r}")
                continue
            if not slot.get("label"):
                errors.append(f"{sid}[{i}]: label 缺失")
            if t == "train":
                if slot.get("pattern") not in PATTERNS:
                    errors.append(f"{sid}[{i}]: pattern 非法 {slot.get('pattern')!r}")
                for ep in slot.get("extra_patterns") or []:
                    if ep not in PATTERNS:
                        errors.append(f"{sid}[{i}]: extra_pattern 非法 {ep!r}")
            elif slot.get("pattern"):
                errors.append(f"{sid}[{i}]: rest 日不得有 pattern")
    if defaults != 1:
        errors.append(f"default 方案必须恰好一个，实际 {defaults}")
    return errors


def main() -> int:
    errors = validate(json.loads(DATA.read_text(encoding="utf-8")))
    for e in errors:
        print("ERROR:", e)
    print(f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
