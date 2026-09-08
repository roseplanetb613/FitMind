# -*- coding: utf-8 -*-
"""W2 种子迁移：静态词表 → Neo4j 别名子图（一次幂等执行，可重复跑）。

来源（source=seed, status=approved, confidence=1.0）：
  - lib/exercise_repo.py NAME_ALIASES → Alias→ALIAS_OF(domain=exercise)→StandardTerm
  - app/skills/qa_skill.py _FOOD_CONCRETE + _FOOD_ALIASES → Alias→ALIAS_OF(domain=food)→StandardTerm
  - app/config/intent_exemplars.json → Alias→ALIAS_OF→StandardTerm(domain=intent)
    →IMPLIES_INTENT→Intent

迁移后静态词表冻结为降级兜底（Neo4j 不可用时仍可用），不再人工新增词条。

用法：
  python scripts/migrate_aliases_to_graph.py [--dry-run]
  --dry-run 仅打印将迁移的条目数，不写库。
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in ("", "lib"):
    if str(ROOT / p) not in sys.path:
        sys.path.insert(0, str(ROOT / p))

from app.graph.store import GraphStore  # noqa: E402


def load_sources() -> dict:
    """聚合三类种子源 → {domain: {alias: term}}。"""
    from exercise_repo import NAME_ALIASES          # exercise 别名
    from app.skills.qa_skill import QaSkill         # food 别名/具体词

    sources = {"exercise": dict(NAME_ALIASES),
               "food": {}}
    for a in QaSkill._FOOD_CONCRETE:
        sources["food"].setdefault(a, a)
    for a, t in QaSkill._FOOD_ALIASES.items():
        sources["food"][a] = t

    exemplars = json.loads(
        (ROOT / "app" / "config" / "intent_exemplars.json").read_text(encoding="utf-8"))
    sources["intent"] = {ex["text"]: ex["task_type"] for ex in exemplars["exemplars"]}
    return sources


def migrate(graph: GraphStore, sources: dict,
            dry_run: bool = False) -> dict:
    total = {"exercise": 0, "food": 0, "intent": 0}
    for domain, m in sources.items():
        if domain in ("exercise", "food"):
            for alias, term in m.items():
                total[domain] += 1
                if not dry_run:
                    graph.upsert_alias(alias, term, domain,
                                       confidence=1.0, source="seed")
        else:  # intent：例句 → 意图映射
            for text, tt in m.items():
                total["intent"] += 1
                if not dry_run:
                    graph.upsert_alias_intent(text, tt, confidence=1.0)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="仅打印将迁移条目数，不写库")
    args = ap.parse_args()

    sources = load_sources()
    if args.dry_run:
        print("dry-run 迁移计划：")
        for k, v in sources.items():
            print(f"  {k}: {len(v)} 条")
        return 0

    graph = GraphStore.get()
    if graph is None:
        print("[降级] Neo4j 不可用（enabled=false / 连接失败），跳过迁移。"
              "在线行为保持现状（静态词表）无影响。")
        return 1
    total = migrate(graph, sources)
    print("迁移完成:", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())